"""Finite trajectory inputs may overflow the intermediate absolute difference."""
from fractions import Fraction
import json
import sys

import pytest

from biomodel_agent import application, cli, modeling, reporting, simulation, validation
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State
from biomodel_agent.storage import ArtifactStore


def series(value): return {'times': [0.], 'values': {'x': [value]}}


@pytest.mark.parametrize('x,y,scale', [(1e308, -1e308, 1e308), (-1e308, 1e308, 1e308),
                                      (1.7e308, -1.7e308, 2.), (1.7e308, -1.7e308, 1.7e308)])
def test_representable_ratio_uses_exact_rational_oracle(x, y, scale):
    expected = float(abs(Fraction(x) - Fraction(y)) / Fraction(scale))
    result = validation.compare_series(series(x), series(y), {'x': scale}, sys.float_info.max)
    assert result['max_scaled_error'] == expected
    assert result['status'] == 'pass' and not result.get('error_overflow')
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('threshold,status', [(1.9, 'fail'), (2., 'pass'), (2.1, 'pass')])
def test_overflow_recovery_obeys_original_inclusive_threshold(threshold, status):
    result = validation.compare_series(series(1e308), series(-1e308), {'x': 1e308}, threshold)
    assert result['max_scaled_error'] == 2. and result['status'] == status


@pytest.mark.parametrize('scale', [1., 1e-308, 5e-324])
def test_true_unrepresentable_error_fails_and_remains_json_safe(scale):
    result = validation.compare_series(series(1e308), series(-1e308), {'x': scale}, sys.float_info.max)
    assert result['status'] == 'fail' and result['error_overflow'] is True
    assert result['max_scaled_error'] is None and result['worst'] == 'x[0]'
    json.dumps(result, allow_nan=False)


def test_ratio_overflow_without_subtraction_overflow_is_safe():
    result = validation.compare_series(series(1.), series(0.), {'x': 5e-324})
    assert result['status'] == 'fail' and result['error_overflow']
    json.dumps(result, allow_nan=False)


def test_convergence_reports_overflow_without_passing():
    check = validation.convergence_check(series(1e308), series(-1e308), {'x': 1.})
    assert check.status == 'fail' and 'overflow' in check.message


@pytest.mark.parametrize('scale,threshold,expected', [(1., .001, 'fail'), (1e308, 3., 'pass')])
def test_stubbed_solver_run_cache_and_replay_preserve_error_evidence(tmp_path, monkeypatch, scale, threshold, expected):
    # This exercises the storage boundary with deterministic supplied results;
    # it does not claim that this stub generated a physical solver trajectory.
    actual = SimulationResult('succeeded', (0., 1.), {'x': (1e308, 1e308)})
    reference = SimulationResult('succeeded', (0., 1.), {'x': (-1e308, -1e308)})
    monkeypatch.setattr(simulation, 'simulate', lambda *a, **kw: actual)
    artifact = modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                                              rate_rules=(RateRule('x', '-k*x'),)))
    store = ArtifactStore(tmp_path / 'store'); exp = ExperimentSpec((0., 1.))
    first = application.run_reproduction(store, artifact, exp, reference=reference, scales={'x': scale}, threshold=threshold)
    cached = application.run_reproduction(store, artifact, exp, reference=reference, scales={'x': scale}, threshold=threshold)
    replay = application.replay_run(store, first)
    for ref in [first, cached, replay]:
        run = store.get(ref)['payload']; comparison = run['comparison']
        assert comparison['status'] == expected
        assert bool(comparison.get('error_overflow')) == (expected == 'fail')
        assert run['scientific_status'] == ('failed' if expected == 'fail' else 'passed_structured_reference')
        json.dumps(run, allow_nan=False)
        if expected == 'fail':
            assert 'error_overflow' in reporting.render_report(store, ref)
        assert cli._print_run_result(ref, store, strict=False) == (1 if expected == 'fail' else 0)
    assert store.get(cached)['payload']['cached_from'] == first
