"""Invalid trajectories must not be normalized into comparison evidence."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import validation
from biomodel_agent.domain.contracts import FrozenMap, SimulationResult


def data():
    return {'times': [0., 1.], 'values': {'x': [1., .5]}}


INVALID = [
    {'times': [False, True], 'values': {'x': [1., .5]}},
    {'times': ['0', '1'], 'values': {'x': [1., .5]}},
    {'times': [1., 0.], 'values': {'x': [1., .5]}},
    {'times': [0., 0.], 'values': {'x': [1., .5]}},
    {'times': [0., float('nan')], 'values': {'x': [1., .5]}},
    {'times': [0., 1.], 'values': {'x': [True, False]}},
    {'times': [0., 1.], 'values': {'x': ['1', '.5']}},
    {'times': [0., 1.], 'values': {'x': [1., float('inf')]}},
    {'times': [0., 1.], 'values': {3: [1., .5]}},
    {'times': [0., 1.], 'values': {'': [1., .5]}},
    {'times': [0., 1.], 'values': [['x', [1., .5]]]},
    {'times': [0., 1.], 'values': {'x': [1.]}},
    {'times': [0., 1.]},
    None,
]


@pytest.mark.parametrize('invalid', INVALID)
@pytest.mark.parametrize('side', ['actual', 'reference'])
def test_invalid_trajectory_cannot_be_compared(side, invalid):
    actual, reference = data(), data()
    if side == 'actual': actual = invalid
    else: reference = invalid
    with pytest.raises(ValueError):
        validation.compare_series(actual, reference, {'x': 1.})


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_numeric_result_returns_blocking_report(value):
    result = SimulationResult('succeeded', (0., 1.), {'x': [1., value]})
    report = validation.validate_numeric(result, nonnegative=('x',))
    assert not report.can_execute and not report.fully_passed
    assert any(c.code == 'finite' and c.status == 'fail' and c.severity == 'blocking' for c in report.checks)
    # The diagnostic itself remains JSON safe; the original numeric evidence is unchanged.
    json.dumps([c.message for c in report.checks], allow_nan=False)
    assert result.values['x'][1] == value or math.isnan(result.values['x'][1])


@pytest.mark.parametrize('times', [(1., 0.), (0., 0.)])
def test_invalid_result_grid_returns_blocking_report(times):
    result = SimulationResult('succeeded', times, {'x': [1., .5]})
    report = validation.validate_numeric(result)
    assert not report.can_execute and 'strictly increasing' in report.checks[0].message


@pytest.mark.parametrize('as_result', [False, True])
def test_analytical_decay_error_and_threshold_unchanged(as_result):
    ref = {'times': [0, 1], 'values': {'x': [1, math.exp(-.4)]}}
    actual = deepcopy(ref); actual['values']['x'][1] += .002
    before = deepcopy(actual)
    if as_result:
        reference = SimulationResult('succeeded', tuple(ref['times']), ref['values'])
        observed = SimulationResult('succeeded', tuple(actual['times']), actual['values'])
    else:
        reference, observed = ref, actual
    outcome = validation.compare_series(observed, reference, {'x': 2.}, .0011)
    assert outcome['status'] == 'pass' and outcome['max_scaled_error'] == pytest.approx(.001)
    assert validation.compare_series(observed, reference, {'x': 2.}, .0009)['status'] == 'fail'
    assert actual == before


def test_immutable_column_mapping_is_supported_without_stringifying_names():
    actual = {'times': [0., 1.], 'values': FrozenMap({'x': (1., .5)})}
    assert validation.compare_series(actual, data(), {'x': 1.})['status'] == 'pass'


def test_negative_start_and_single_sample_are_valid_grids():
    one = {'times': [-1.], 'values': {'x': [1.]}}
    assert validation.compare_series(one, one, {'x': 1.})['status'] == 'pass'


def test_huge_integer_not_representable_as_float_is_rejected_cleanly():
    invalid = {'times': [0., 1.], 'values': {'x': [1., 10 ** 400]}}
    with pytest.raises(ValueError, match='finite numbers'):
        validation.compare_series(invalid, data(), {'x': 1.})
    with pytest.raises(ValueError, match='positive finite'):
        validation.validate_scales({'x': 10 ** 400})
