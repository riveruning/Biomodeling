"""Minimal frozen domain contracts.

Only structural validation: finite numbers (None means explicitly missing),
identifier shape, uniqueness where required, strict ordering for time series,
and defensive copies so caller-mutated inputs never affect a snapshot.
"""

from __future__ import annotations

import math
import re
from collections.abc import KeysView, Mapping, ValuesView
from dataclasses import dataclass, field, fields
from hashlib import sha256
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_CHECK_STATUSES = frozenset({"pass", "fail", "unknown", "not_applicable", "not_run"})
_SEVERITIES = frozenset({"blocking", "warning", "informational"})
_SIM_STATUSES = frozenset({"succeeded", "failed", "timed_out", "cancelled", "unsupported"})


class FrozenMap(Mapping[str, Any]):
    """Generic, deeply immutable, picklable string-keyed map.

    Keys must be non-empty strings (evidence paths such as ``reaction:r/rate``
    are allowed; ID shape is validated by the owning dataclass). Values are
    recursively frozen: mappings become :class:`FrozenMap`, lists/tuples become
    tuples, and primitives are limited to str/bool/int/float/None. Mutable or
    unknown objects are rejected. The internal ``_pairs`` attribute is
    immutable and pickle round-trips via ``__reduce__``.
    """

    __slots__ = ("_pairs",)

    @staticmethod
    def _freeze_value(key: str, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, FrozenMap):
            return FrozenMap(value)
        if isinstance(value, Mapping):
            return FrozenMap(value)
        if isinstance(value, (list, tuple)):
            return tuple(FrozenMap._freeze_value(f"{key}[{i}]", v) for i, v in enumerate(value))
        raise ValueError(f"FrozenMap[{key!r}]: unsupported value type {type(value).__name__}")

    def __init__(self, pairs: Mapping[str, Any] | tuple[tuple[str, Any], ...] = ()) -> None:
        seen: set[str] = set()
        norm: list[tuple[str, Any]] = []
        if isinstance(pairs, FrozenMap):
            raw = pairs._pairs
        elif isinstance(pairs, Mapping):
            raw = list(pairs.items())
        else:
            raw = list(pairs)
        for key, value in raw:
            if not isinstance(key, str) or not key:
                raise ValueError(f"invalid FrozenMap key {key!r}: must be a non-empty string")
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
            norm.append((key, self._freeze_value(key, value)))
        object.__setattr__(self, "_pairs", tuple(norm))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"FrozenMap is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"FrozenMap is immutable; cannot delete {name!r}")

    def __reduce__(self) -> tuple:
        return (FrozenMap, (self._pairs,))

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(f"FrozenMap is immutable; cannot set {key!r}")

    def __getitem__(self, key: str) -> Any:
        for k, v in self._pairs:
            if k == key:
                return v
        raise KeyError(key)

    def __iter__(self):
        return (k for k, _ in self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def __contains__(self, key: object) -> bool:
        return any(k == key for k, _ in self._pairs)

    @staticmethod
    def _normalize(v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            return tuple(FrozenMap._normalize(x) for x in v)
        return v

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FrozenMap):
            return NotImplemented
        a = {k: self._normalize(v) for k, v in self._pairs}
        b = {k: self._normalize(v) for k, v in other._pairs}
        return a == b

    def __hash__(self) -> int:
        return hash(tuple(sorted(((k, self._normalize(v)) for k, v in self._pairs), key=lambda p: p[0])))

    def __repr__(self) -> str:
        return f"FrozenMap({dict(self._pairs)!r})"

    def keys(self) -> KeysView[str]:
        return KeysView(self)

    def values(self) -> ValuesView[Any]:
        return ValuesView(self)


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def require_id(value: object, what: str) -> str:
    if not is_valid_id(value):
        raise ValueError(f"invalid {what} identifier: {value!r}")
    return str(value)


def strict_float(value: object, what: str) -> float:
    """Return ``value`` as a float, strictly.

    Accepts int/float only (numpy.float64 etc. via isinstance); bool, str,
    Decimal and __float__-only objects are rejected without invoking any
    conversion protocol.  Converting an int that overflows the float range
    surfaces as ValueError rather than OverflowError.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be an int or float, got {type(value).__name__}")
    try:
        f = float(value)
    except OverflowError:
        raise ValueError(f"{what} must be finite, got {value!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return f


def finite(value: float | None, what: str, allow_none: bool) -> float | None:
    if value is None:
        if not allow_none:
            raise ValueError(f"{what} must not be None")
        return None
    return strict_float(value, what)


def copy_finite_map(value: Mapping[str, Any] | tuple[tuple[str, Any], ...], what: str) -> FrozenMap:
    """Deep-freeze ``value`` and require every leaf to be a finite number."""
    def walk(key: str, v: Any) -> None:
        if isinstance(v, FrozenMap):
            for k, x in v.items():
                walk(f"{key}/{k}" if key else k, x)
        elif isinstance(v, tuple):
            for i, x in enumerate(v):
                walk(f"{key}[{i}]", x)
        else:
            strict_float(v, f"{what}[{key!r}]")
    fm = FrozenMap(value)
    for k, v in fm.items():
        walk(k, v)
    return fm


@dataclass(frozen=True, slots=True)
class Parameter:
    id: str
    value: float | None
    unit: str = "unknown"
    origin: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "parameter"))
        object.__setattr__(self, "value", finite(self.value, f"parameter {self.id} value", True))


@dataclass(frozen=True, slots=True)
class State:
    id: str
    initial: float | None
    unit: str = "unknown"
    quantity_kind: str = "variable"
    nonnegative: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "state"))
        object.__setattr__(self, "initial", finite(self.initial, f"state {self.id} initial", True))
        if self.quantity_kind not in {"variable", "amount", "concentration"}:
            raise ValueError(
                f"state {self.id} quantity_kind must be one of "
                f"{'/'.join(sorted({'variable', 'amount', 'concentration'}))}, got {self.quantity_kind!r}"
            )


@dataclass(frozen=True, slots=True)
class Compartment:
    id: str = "cell"
    size: float = 1.0
    unit: str = "dimensionless"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "compartment"))
        size = finite(self.size, "compartment size", False)
        if size is None or size <= 0:
            raise ValueError(f"compartment size must be > 0, got {self.size!r}")
        object.__setattr__(self, "size", size)


@dataclass(frozen=True, slots=True)
class Reaction:
    id: str
    reactants: FrozenMap
    products: FrozenMap
    rate: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "reaction"))
        object.__setattr__(self, "reactants", copy_finite_map(self.reactants, "reactants"))
        object.__setattr__(self, "products", copy_finite_map(self.products, "products"))
        for name, fm in (("reactants", self.reactants), ("products", self.products)):
            for k, v in fm.items():
                if v <= 0:
                    raise ValueError(f"reaction {self.id}: {name} stoichiometry must be positive, got {k}={v}")


@dataclass(frozen=True, slots=True)
class RateRule:
    state: str
    expression: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", require_id(self.state, "rate rule state"))


@dataclass(frozen=True, slots=True)
class Derived:
    id: str
    expression: str
    unit: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "derived"))


@dataclass(frozen=True, slots=True)
class ModelDraft:
    id: str
    states: tuple[State, ...]
    parameters: tuple[Parameter, ...] = ()
    compartment: Compartment = field(default_factory=Compartment)
    reactions: tuple[Reaction, ...] = ()
    rate_rules: tuple[RateRule, ...] = ()
    derived: tuple[Derived, ...] = ()
    time_unit: str = "second"
    element_map: FrozenMap = FrozenMap()
    hypothesis_refs: tuple[str, ...] = ()
    parent_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "model draft"))
        states = tuple(self.states)
        if not states:
            raise ValueError("ModelDraft requires at least one state")
        ids: dict[str, str] = {}
        for st in states:
            if st.id in ids:
                raise ValueError(f"duplicate state id {st.id!r}")
            ids[st.id] = "state"
        params = tuple(self.parameters)
        if self.compartment.id in ids:
            raise ValueError(f"compartment id {self.compartment.id!r} conflicts with a state")
        for p in params:
            if p.id in ids:
                raise ValueError(f"parameter id {p.id!r} conflicts with {ids[p.id]}")
            ids[p.id] = "parameter"
        for kind, seq in (("compartment", (self.compartment,)), ("reaction", self.reactions),
                          ("derived", self.derived)):
            for obj in seq:
                if obj.id in ids:
                    raise ValueError(f"{kind} id {obj.id!r} conflicts with {ids[obj.id]}")
                ids[obj.id] = kind
        # RateRule has no own ID (its `state` field names an existing state);
        # it must not participate in ID uniqueness.
        for r in self.rate_rules:
            if r.state not in ids:
                raise ValueError(f"rate rule {r.expression!r} references unknown state {r.state!r}")
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "parameters", params)
        object.__setattr__(self, "compartment", Compartment(self.compartment.id, self.compartment.size,
                                                             self.compartment.unit))
        object.__setattr__(self, "reactions", tuple(Reaction(r.id, r.reactants, r.products, r.rate) for r in self.reactions))
        object.__setattr__(self, "rate_rules", tuple(RateRule(r.state, r.expression) for r in self.rate_rules))
        object.__setattr__(self, "derived", tuple(Derived(d.id, d.expression, d.unit) for d in self.derived))
        object.__setattr__(self, "element_map", FrozenMap(self.element_map))
        object.__setattr__(self, "hypothesis_refs", tuple(self.hypothesis_refs))


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    id: str
    sbml: str
    element_map: FrozenMap = FrozenMap()
    parent_ref: str | None = None
    format: str = "sbml"
    kind: str = "ode"
    sha256: str = field(init=False, repr=False, default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "model artifact"))
        if not isinstance(self.sbml, str) or not self.sbml.strip():
            raise ValueError("ModelArtifact.sbml must be a non-empty string")
        object.__setattr__(self, "element_map", FrozenMap(self.element_map))
        object.__setattr__(self, "sha256", sha256(self.sbml.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class InputSegment:
    time: float
    values: FrozenMap

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", finite(self.time, "segment time", False))
        object.__setattr__(self, "values", copy_finite_map(self.values, "segment values"))


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    times: tuple[float, ...]
    initial_overrides: FrozenMap = FrozenMap()
    parameter_overrides: FrozenMap = FrozenMap()
    segments: tuple[InputSegment, ...] = ()
    time_unit: str = 'unknown'

    def __post_init__(self) -> None:
        from ..time_units import normalize_time_unit
        object.__setattr__(self, 'time_unit', normalize_time_unit(self.time_unit))
        ts = tuple(self.times)
        if not ts:
            raise ValueError("ExperimentSpec.times must be non-empty")
        checked = []
        prev = float("-inf")
        for t in ts:
            f = finite(t, "experiment time", False)
            if f <= prev:
                raise ValueError("ExperimentSpec.times must be strictly increasing")
            prev = f
            checked.append(f)
        object.__setattr__(self, "times", tuple(checked))
        object.__setattr__(self, "initial_overrides", copy_finite_map(self.initial_overrides, "initial_overrides"))
        object.__setattr__(self, "parameter_overrides", copy_finite_map(self.parameter_overrides, "parameter_overrides"))
        segs = tuple(self.segments)
        prev = float("-inf")
        for seg in segs:
            if seg.time <= prev:
                raise ValueError("segment times must be strictly increasing")
            prev = seg.time
        object.__setattr__(self, "segments", segs)


@dataclass(frozen=True, slots=True)
class ObservationSpec:
    id: str
    expression: str
    unit: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_id(self.id, "observation"))


@dataclass(frozen=True, slots=True)
class SolverConfig:
    rtol: float = 1e-7
    atol: float = 1e-10
    timeout_s: float = 30.0
    max_steps: int = 100000

    def __post_init__(self) -> None:
        for name in ("rtol", "atol", "timeout_s"):
            v = finite(getattr(self, name), name, False)
            if v is not None and v <= 0:
                raise ValueError(f"{name} must be > 0")
            object.__setattr__(self, name, v)
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")


@dataclass(frozen=True, slots=True)
class Check:
    code: str
    status: str
    message: str = ""
    severity: str = "blocking"

    def __post_init__(self) -> None:
        if self.status not in _CHECK_STATUSES:
            raise ValueError(f"Check.status must be one of {sorted(_CHECK_STATUSES)}, got {self.status!r}")
        if self.severity not in _SEVERITIES:
            raise ValueError(f"Check.severity must be one of {sorted(_SEVERITIES)}, got {self.severity!r}")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    checks: tuple[Check, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))

    @property
    def can_execute(self) -> bool:
        """No blocking check with status 'fail' (unknown never counts as pass)."""
        return not any(c.status == "fail" and c.severity == "blocking" for c in self.checks)

    @property
    def fully_passed(self) -> bool:
        """No fail/unknown/not_run; not_applicable is allowed."""
        return bool(self.checks) and not any(c.status in {"fail", "unknown", "not_run"} for c in self.checks)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    status: str
    times: tuple[float, ...] = ()
    values: FrozenMap = FrozenMap()
    checks: ValidationReport = field(default_factory=ValidationReport)
    diagnostics: tuple[str, ...] = ()
    backend: str = ""
    elapsed_s: float = 0.0
    time_unit: str = 'unknown'

    def __post_init__(self) -> None:
        from ..time_units import normalize_time_unit
        object.__setattr__(self, 'time_unit', normalize_time_unit(self.time_unit))
        if self.status not in _SIM_STATUSES:
            raise ValueError(f"SimulationResult.status must be one of {sorted(_SIM_STATUSES)}, got {self.status!r}")
        checked = tuple(finite(t, "simulation time", False) for t in self.times)
        object.__setattr__(self, "times", checked)
        cols: dict[str, tuple[float, ...]] = {}
        for k, v in FrozenMap(self.values).items():
            if not isinstance(v, (list, tuple)):
                raise ValueError(f"SimulationResult.values[{k!r}] must be a sequence of floats")
            if len(v) != len(checked):
                raise ValueError(f"SimulationResult.values[{k!r}] length must match times")
            # Strict typing: refuse bool/str/__float__ objects and
            # non-representable ints; keep native float NaN/Inf as-is for
            # downstream validation diagnostics.
            col: list[float] = []
            for x in v:
                if isinstance(x, bool) or not isinstance(x, (int, float)):
                    raise ValueError(
                        f"SimulationResult.values[{k!r}] must contain int or float, "
                        f"got {type(x).__name__}"
                    )
                if isinstance(x, float):
                    col.append(x)
                else:
                    try:
                        col.append(float(x))
                    except OverflowError:
                        raise ValueError(
                            f"SimulationResult.values[{k!r}] cannot be represented as float: {x!r}"
                        ) from None
            cols[k] = tuple(col)
        object.__setattr__(self, "values", FrozenMap(cols))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "elapsed_s", finite(self.elapsed_s, "elapsed_s", False))


def fields_of(cls: type) -> dict[str, Any]:
    """Field names ordered by declaration (for serialization)."""
    return {f.name: f for f in fields(cls)}
