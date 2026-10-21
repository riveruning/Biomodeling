"""Strict recursive JSON codec for the domain contracts.

to_dict walks dataclass fields and emits JSON-safe leaves: dataclasses via
their ``to_dict`` form, FrozenMap as a plain nested dict, tuple/list as a
JSON list, and primitives as-is (floats must be finite).

from_dict is fully type-driven: for a dataclass it reads ``get_type_hints``
and rebuilds each field from its declared type; nested dataclasses are rebuilt
via their ``from_dict``, ``tuple[T, ...]`` is rebuilt per-element using
``T`` (never the tuple hint itself), and unions such as ``str | None`` or
``float | None`` (``typing.Union`` / ``types.UnionType``) select the matching
member. Primitive coercion is strict: float accepts finite int/float but not
bool, int accepts int but not bool, bool only bool, str only str.

Unknown fields are rejected; missing required fields (``dataclasses.MISSING``)
raise while missing defaulted fields fall back to the default. Computed
(init=False) fields such as ``ModelArtifact.sha256`` are dropped on the way
out and, if present on the way in, must equal the recomputed value. All
structural/coercion failures surface as :class:`SchemaError`.
"""

from __future__ import annotations

import math
import types
import typing
from dataclasses import MISSING, is_dataclass, fields
from typing import Any, get_args, get_origin, get_type_hints

from .contracts import FrozenMap, strict_float


class SchemaError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Outgoing (to_dict)
# ---------------------------------------------------------------------------

def _leaf(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isfinite(obj):
            return obj
        raise SchemaError(f"non-finite float not allowed: {obj!r}")
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_dict(obj)
    if isinstance(obj, FrozenMap):
        return {k: _leaf(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_leaf(v) for v in obj]
    if isinstance(obj, list):
        return [_leaf(v) for v in obj]
    raise SchemaError(f"not JSON-serializable: {type(obj).__name__}")


def to_dict(obj: Any) -> dict:
    if not is_dataclass(obj) or isinstance(obj, type):
        raise SchemaError(f"to_dict expects a dataclass instance, got {type(obj).__name__}")
    out: dict = {}
    for f in fields(type(obj)):
        if f.init is False:
            continue
        out[f.name] = _leaf(getattr(obj, f.name))
    return out


# ---------------------------------------------------------------------------
# Incoming (from_dict)
# ---------------------------------------------------------------------------

def _coerce_primitive(hint: type, data: Any, name: str) -> Any:
    if hint is bool:
        if isinstance(data, bool):
            return data
        raise SchemaError(f"{name}: expected bool, got {type(data).__name__}")
    if hint is int:
        if isinstance(data, int) and not isinstance(data, bool):
            return data
        raise SchemaError(f"{name}: expected int, got {type(data).__name__}")
    if hint is float:
        try:
            return strict_float(data, name)
        except ValueError as exc:
            raise SchemaError(f"{name}: {exc}") from exc
    if hint is str:
        if isinstance(data, str):
            return data
        raise SchemaError(f"{name}: expected str, got {type(data).__name__}")
    raise SchemaError(f"{name}: unsupported primitive hint {hint!r}")


def _build(hint: Any, data: Any, name: str) -> Any:
    """Rebuild ``data`` per declared type ``hint``; wrap errors as SchemaError."""
    try:
        return _build_impl(hint, data, name)
    except SchemaError:
        raise
    except Exception as exc:
        raise SchemaError(f"{name}: {exc}") from exc


def _build_impl(hint: Any, data: Any, name: str) -> Any:
    origin = get_origin(hint)

    if origin in (typing.Union, types.UnionType):
        members = list(get_args(hint))
        if data is None:
            for m in members:
                if m is type(None):
                    return None
            raise SchemaError(f"{name}: None not allowed in union {hint!r}")
        if type(None) in members:
            members = [m for m in members if m is not type(None)]
        for m in members:
            try:
                return _build(m, data, name)
            except SchemaError:
                continue
        raise SchemaError(f"{name}: {data!r} does not match any of {hint!r}")

    if origin is tuple:
        if not isinstance(data, list):
            raise SchemaError(f"{name}: expected JSON list for tuple, got {type(data).__name__}")
        args = get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            elem = args[0]  # rebuild each element against T, not the tuple hint
            return tuple(_build(elem, v, f"{name}[{i}]") for i, v in enumerate(data))
        raise SchemaError(f"{name}: unsupported tuple hint {hint!r}")

    if origin in (dict,):
        if not isinstance(data, dict):
            raise SchemaError(f"{name}: expected mapping, got {type(data).__name__}")
        return FrozenMap(data)

    # Plain (non-generic) type.
    if hint is None:
        return data
    if hint is FrozenMap:
        if not isinstance(data, dict):
            raise SchemaError(f"{name}: expected mapping, got {type(data).__name__}")
        return FrozenMap(data)
    if isinstance(hint, type) and is_dataclass(hint):
        if not isinstance(data, dict):
            raise SchemaError(f"{name}: expected object, got {type(data).__name__}")
        return _field_data(hint, data)
    if isinstance(hint, type) and hint in (str, bool, int, float):
        return _coerce_primitive(hint, data, name)
    raise SchemaError(f"{name}: unsupported hint {hint!r}")


def _field_data(cls: type, data: dict) -> Any:
    """Build a dataclass instance from a dict, strict about unknown/missing keys."""
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise SchemaError(f"{cls.__name__}: unknown field(s) {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.init is False:
            continue
        if f.name in data:
            if f.name not in hints:
                raise SchemaError(f"{cls.__name__}.{f.name}: no type hint, refusing")
            kwargs[f.name] = _build(hints[f.name], data[f.name], f"{cls.__name__}.{f.name}")
        else:
            # Use default only when one actually exists (MISSING means required).
            if f.default is not MISSING:
                continue
            if f.default_factory is not MISSING:
                continue
            raise SchemaError(f"{cls.__name__}: missing required field {f.name!r}")
    obj = cls(**kwargs)
    # Verify computed (init=False) fields against any provided values.
    for f in fields(type(obj)):
        if f.init is False and f.name in data:
            if getattr(obj, f.name) != data[f.name]:
                raise SchemaError(
                    f"{cls.__name__}.{f.name}: provided value does not match "
                    f"computed value {getattr(obj, f.name)!r}"
                )
    return obj


def from_dict(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise SchemaError(f"from_dict expects a dict, got {type(data).__name__}")
    if not isinstance(cls, type) or not is_dataclass(cls):
        raise SchemaError(f"from_dict expects a dataclass type, got {cls!r}")
    try:
        return _field_data(cls, data)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SchemaError):
            raise
        raise SchemaError(f"{cls.__name__}: {exc}") from exc