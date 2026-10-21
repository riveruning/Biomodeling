"""Explicit experiment-clock checks; no inferred units or automatic conversion.

SBML Model.timeUnits defines the model clock. Inspect the definition rather
than trusting its ID: an imported unit called 'minute' need not mean 60 s.
The bounded resolver accepts built-in seconds and a single second-based unit
with exponent one; other definitions remain unresolved.
"""
from __future__ import annotations

import math

_ALIASES = {
    'unknown': 'unknown',
    's': 'second', 'sec': 'second', 'secs': 'second', 'second': 'second', 'seconds': 'second',
    'min': 'minute', 'mins': 'minute', 'minute': 'minute', 'minutes': 'minute',
    'h': 'hour', 'hr': 'hour', 'hrs': 'hour', 'hour': 'hour', 'hours': 'hour',
}
_SECONDS = {'second': 1., 'minute': 60., 'hour': 3600.}


def normalize_time_unit(value):
    if not isinstance(value, str) or value.strip().lower() not in _ALIASES:
        raise ValueError('experiment time_unit must be second, minute, hour, a supported alias, or unknown')
    return _ALIASES[value.strip().lower()]


def model_time_scale(sbml):
    """Seconds per model time unit, or None when not explicitly resolved."""
    import libsbml

    doc = libsbml.readSBMLFromString(sbml)
    model = doc.getModel() if doc else None
    if model is None or not model.isSetTimeUnits():
        return None
    name = model.getTimeUnits()
    if name == 'second':
        return 1.
    definition = model.getUnitDefinition(name)
    if definition is None or definition.getNumUnits() != 1:
        return None
    unit = definition.getUnit(0)
    if unit.getKind() != libsbml.UNIT_KIND_SECOND or unit.getExponentAsDouble() != 1.:
        return None
    if unit.getOffset() != 0.:
        return None
    try:
        scale = float(unit.getMultiplier()) * 10. ** unit.getScale()
    except (OverflowError, ValueError):
        return None
    return scale if math.isfinite(scale) and scale > 0. else None


def time_unit_check(requested, model_scale):
    from .domain.contracts import Check

    unit = normalize_time_unit(requested)
    if unit == 'unknown':
        return Check('experiment_time_unit', 'unknown',
                     'Experiment time unit is unknown; alignment with the model clock was not checked.', 'warning')
    if model_scale is None:
        return Check('experiment_time_unit', 'fail',
                     f'Experiment time_unit={unit!r}, but explicit SBML time units could not be resolved; no unit is inferred.')
    if not math.isclose(_SECONDS[unit], model_scale, rel_tol=1e-12, abs_tol=0.):
        return Check('experiment_time_unit', 'fail',
                     f'Experiment time_unit={unit!r} ({_SECONDS[unit]:g} seconds/unit) differs from the SBML model clock ({model_scale:g} seconds/unit); no automatic time conversion is applied.')
    return Check('experiment_time_unit', 'pass',
                 f'Experiment time_unit={unit!r} matches the SBML model clock ({model_scale:g} seconds/unit).')


def check_experiment_time(sbml, requested):
    # Legacy experiments remain explicitly unknown without even interpreting
    # a model's unit names or importing a numerical backend.
    unit = normalize_time_unit(requested)
    return time_unit_check(unit, None if unit == 'unknown' else model_time_scale(sbml))
