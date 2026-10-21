"""Numerical validation and fixed-error comparison of simulation output.

``validate_numeric`` never promotes a non-succeeded simulation to a pass,
never checks mass balance (conservation is reported as ``not_run`` on
purpose), and preserves the original output untouched: nothing is clipped,
rounded or repaired. ``compare_series`` / ``convergence_check`` implement the
fixed scaled-error criterion used for convergence evidence.
"""

from __future__ import annotations

import math

from .domain.contracts import Check, SimulationResult, ValidationReport

_MISSING = object()


def _is_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _series(container: object, expected: int, where: str) -> dict[str, tuple[float, ...]]:
    if not isinstance(container, dict):
        raise ValueError(f"{where} must be a mapping of column -> sequence")
    out: dict[str, tuple[float, ...]] = {}
    for key, value in container.items():
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{where}[{key!r}] must be a sequence of numbers")
        column = tuple(float(x) for x in value)
        if len(column) != expected:
            raise ValueError(f"{where}[{key!r}] has {len(column)} values, expected {expected}")
        for x in column:
            if not math.isfinite(x):
                raise ValueError(f"{where}[{key!r}] contains a non-finite value: {x!r}")
        out[str(key)] = column
    return out


def _scales(scales: object, columns: object) -> dict[str, float]:
    if not isinstance(scales, dict):
        raise ValueError("scales must be a mapping of column -> positive finite number")
    resolved: dict[str, float] = {}
    for key in columns:
        value = scales.get(key, _MISSING)
        if value is _MISSING:
            raise ValueError(f"scales is missing a fixed scale for column {key!r}")
        if not _is_finite(value) or float(value) <= 0.0:
            raise ValueError(f"scales[{key!r}] must be a positive finite number, got {value!r}")
        resolved[key] = float(value)
    return resolved


def validate_numeric(
    result: SimulationResult,
    nonnegative: tuple[str, ...] = (),
    conservation: tuple[str, ...] = (),
    atol: float = 1e-10,
) -> ValidationReport:
    """Check finiteness and declared non-negativity; never clip or repair."""
    limit = float(atol)
    if limit <= 0 or not math.isfinite(limit):
        raise ValueError(f"atol must be a positive finite number, got {atol!r}")
    checks: list[Check] = []
    if result.status == 'succeeded' and (not result.times or not result.values):
        return ValidationReport((Check('finite', 'fail', 'No samples or output columns'),))
    if result.status != "succeeded":
        checks.append(Check(code="finite", status="fail", severity="blocking",
                            message=f"simulation status is {result.status!r}: no numeric evidence"))
        return ValidationReport(tuple(checks))

    known = set(result.values.keys())
    values = _series(dict(result.values), len(result.times), "values")
    bad = [f"{k}[{i}]={v!r}" for k, column in values.items()
           for i, v in enumerate(column) if not math.isfinite(v)]
    if bad:
        checks.append(Check(code="finite", status="fail", severity="blocking",
                            message="non-finite output: " + ", ".join(bad[:8])))
        return ValidationReport(tuple(checks))
    checks.append(Check(code="finite", status="pass", message="all output values are finite"))

    measured: list[str] = []
    for name in sorted(set(nonnegative)):
        if name not in known:
            checks.append(Check(code="nonnegative", status="fail", severity="blocking",
                                message=f"nonnegative variable {name!r} is not a simulation column"))
            continue
        threshold = -10.0 * limit
        worst = min(values[name])
        if worst < threshold:
            checks.append(Check(code="nonnegative", status="fail", severity="blocking",
                                message=f"{name!r} reached {worst!r} < {threshold!r}"))
        else:
            checks.append(Check(code="nonnegative", status="pass",
                                message=f"{name!r} stayed >= {threshold!r}"))
        measured.append(name)
    if not measured and not any(c.code == "nonnegative" for c in checks):
        checks.append(Check(code="nonnegative", status="not_applicable", severity="informational",
                            message="no nonnegative variables requested"))

    # Deliberately unimplemented: no mass-balance claim is made.  The code is
    # always emitted as ``not_run`` (never a pass) so that ``fully_passed``
    # stays False; a referenced-but-unknown variable is reported separately.
    checks.append(Check(code="conservation", status="not_run", severity="informational",
                        message="conservation/mass-balance checking is not implemented; not a pass"))
    if conservation:
        missing_refs = sorted({name for name in conservation if name not in known})
        if missing_refs:
            checks.append(Check(
                code="conservation_reference", status="unknown", severity="warning",
                message="conservation references are not simulation columns: " + ", ".join(missing_refs),
            ))
        else:
            checks.append(Check(
                code="conservation_reference", status="not_applicable", severity="informational",
                message="conservation references exist as columns, but no conservation check is run",
            ))
    return ValidationReport(tuple(checks))


def compare_series(
    actual: object,
    reference: object,
    scales: object,
    threshold: float = 1e-3,
) -> dict:
    """Max scaled absolute error between two series on an identical grid."""
    from .time_units import comparison_time_unit
    actual_unit = actual.time_unit if isinstance(actual, SimulationResult) else actual.get('time_unit', 'unknown')
    reference_unit = reference.time_unit if isinstance(reference, SimulationResult) else reference.get('time_unit', 'unknown')
    clock = comparison_time_unit(actual_unit, reference_unit)
    if isinstance(actual, SimulationResult):
        times, values = actual.times, actual.values
    else:
        times, values = actual["times"], actual["values"]
    if isinstance(reference, SimulationResult):
        ref_times: object = reference.times
        ref_values: object = reference.values
    else:
        ref_times, ref_values = reference["times"], reference["values"]
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise ValueError("threshold must be a number")
    threshold = float(threshold)
    if not math.isfinite(threshold) or threshold <= 0.0:
        raise ValueError(f"threshold must be positive and finite, got {threshold!r}")
    if not isinstance(times, (list, tuple)) or not isinstance(ref_times, (list, tuple)):
        raise ValueError("both series must provide a time grid")
    grid_a = tuple(float(t) for t in times)
    grid_b = tuple(float(t) for t in ref_times)
    for t in grid_a + grid_b:
        if not math.isfinite(t):
            raise ValueError(f"time grid contains a non-finite value: {t!r}")
    if grid_a != grid_b:
        raise ValueError(f"time grids differ: {len(grid_a)} vs {len(grid_b)} samples")
    if not grid_a or not values or not ref_values:
        raise ValueError('comparison requires nonempty samples and columns')
    if isinstance(actual, SimulationResult) and actual.status != 'succeeded':
        raise ValueError('actual simulation did not succeed')
    if isinstance(reference, SimulationResult) and reference.status != 'succeeded':
        raise ValueError('reference simulation did not succeed')
    a = _series(dict(values), len(grid_a), "actual.values")
    b = _series(dict(ref_values), len(grid_a), "reference.values")
    if set(a) != set(b):
        raise ValueError(f"columns differ: {sorted(set(a) ^ set(b))}")
    fixed = _scales(scales, sorted(a))
    worst = 0.0
    where = ""
    for key in sorted(a):
        for i, (x, y) in enumerate(zip(a[key], b[key])):
            error = abs(x - y) / fixed[key]
            if error > worst:
                worst, where = error, f"{key}[{i}]"
    return {
        **clock,
        "max_scaled_error": worst,
        "status": "pass" if worst <= threshold else "fail",
        "threshold": threshold,
        "scales": fixed,
        "worst": where,
        "columns": sorted(a),
    }


def convergence_check(coarse: object, fine: object, scales: object, threshold: float = 1e-3) -> Check:
    """Check that refining the solver moves the series by less than ``threshold``."""
    outcome = compare_series(fine, coarse, scales, threshold)
    detail = (f"max scaled error {outcome['max_scaled_error']!r} vs threshold {outcome['threshold']!r}"
              f" at {outcome['worst']!r}")
    return Check(
        code="convergence",
        status="pass" if outcome["status"] == "pass" else "fail",
        severity="blocking",
        message=detail,
    )
