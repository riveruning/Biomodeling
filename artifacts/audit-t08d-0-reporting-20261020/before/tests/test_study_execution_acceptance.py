"""Codex acceptance for T04: shared facts, total budgets and bounded prediction."""
from copy import deepcopy
import json
import math
import multiprocessing as mp
import time

import pytest

from biomodel_agent import agent, cli, fitting, hypotheses, modeling, studies, recovery, simulation, reporting
from biomodel_agent.domain.contracts import ExperimentSpec, SimulationResult
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.deadlines import Deadline
from test_agent_studies import Scripted, act, config


def prepared(tmp_path, **options):
    store = ArtifactStore(tmp_path / 'store')
    settings = {**config(), **options}
    plan = studies.prepare_study(store, settings)
    source = store.put('source', {'pages': [{'number': 1, 'text': 'Frozen candidate study.'}]})
    exp = from_dict(ExperimentSpec, settings['training'][0]['experiment'])
    return store, settings, plan, source, exp


def fail_fits(*a, **k):
    return {'status': 'timed_out', 'best': None, 'starts': [], 'diagnostics': ['injected timeout']}


def test_core_all_fit_timeouts_cannot_claim_completed(tmp_path, monkeypatch):
    store, settings, _, _, _ = prepared(tmp_path)
    monkeypatch.setattr(fitting, 'fit_parameters', fail_fits)
    candidates = {'cand_baseline': hypotheses.adaptation_candidates()['cand_baseline']}
    result = store.get(hypotheses.run_hypothesis_study(store, candidates, settings['training'],
        settings['holdout'], starts=1))['payload']
    assert result['status'] != 'completed'
    assert result['completion_eligible'] is False and result['comparison']['winner'] is None


def test_legacy_cli_all_fit_timeouts_must_fail(tmp_path, monkeypatch, capsys):
    store, settings, _, _, _ = prepared(tmp_path)
    training = tmp_path / 'training.json'; training.write_text(json.dumps(settings['training']))
    holdout = tmp_path / 'holdout.json'; holdout.write_text(json.dumps(settings['holdout']))
    monkeypatch.setattr(fitting, 'fit_parameters', fail_fits)
    code = cli.main(['hypothesis', '--training', str(training), '--holdout', str(holdout),
                     '--starts', '1', '--store', str(store.root)])
    assert code != 0


def test_heldout_prediction_failure_is_not_completed_research(tmp_path, monkeypatch):
    store, _, plan, _, _ = prepared(tmp_path)
    monkeypatch.setattr(fitting, 'predict', lambda *a, **k: (_ for _ in ()).throw(ValueError('prediction unavailable')))
    result = store.get(studies.execute_study(store, plan))['payload']
    assert result['completion_eligible'] is False
    assert result['status'] != 'completed' and result['comparison']['winner'] is None


def test_fit_preflight_never_runs_parent_ode_before_timeout(monkeypatch):
    from test_research_workflows import decay, training_data
    monkeypatch.setattr(fitting, 'solve_ivp', lambda *a, **k: pytest.fail('parent ODE escaped deadline'))
    result = fitting.fit_parameters(decay(), training_data(), [{'id': 'signal', 'expression': 'x'}],
                                    {'d': (.1, 1.5)}, starts=1, timeout_s=.001)
    assert result['status'] == 'timed_out'


def test_new_plan_freezes_total_study_budget(tmp_path):
    store, _, plan, _, _ = prepared(tmp_path, total_timeout_s=7.)
    payload = studies.load_plan(store, plan)
    assert payload['schema_version'] == 2
    assert payload['config']['total_timeout_s'] == 7.
    assert studies.plan_summary(store, plan)['total_timeout_s'] == 7.


@pytest.mark.parametrize('value', [True, '5', 0., -1., float('nan'), float('inf'), 601., 10**400])
def test_bad_total_timeout_rejected_before_artifact_writes(tmp_path, value):
    store = ArtifactStore(tmp_path / 'store')
    before = store.list()
    with pytest.raises(ValueError):
        studies.prepare_study(store, {**config(), 'total_timeout_s': value})
    assert store.list() == before


def slow_prediction(payload, out):
    time.sleep(5)
    out.put({'status': 'succeeded', 'observables': {'response': [0., 0.]}})


def test_bounded_prediction_matches_analytic_solution():
    candidate = modeling.build_model(hypotheses.adaptation_candidates()['cand_baseline'])
    result = fitting.predict(candidate, ExperimentSpec((0., .5, 1.), time_unit='second'),
        [{'id': 'response', 'expression': 'x'}], {'k': 2., 'd': 1.}, timeout_s=5.)
    assert result['observables']['response'] == pytest.approx([0., 2*(1-math.exp(-.5)), 2*(1-math.exp(-1.))], abs=1e-7)


def test_prediction_timeout_stops_worker(tmp_path, monkeypatch):
    candidate = modeling.build_model(hypotheses.adaptation_candidates()['cand_baseline'])
    monkeypatch.setattr(fitting, '_predict_worker', slow_prediction, raising=False)
    before = {p.pid for p in mp.active_children()}
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        fitting.predict(candidate, ExperimentSpec((0., 1.), time_unit='second'),
            [{'id': 'response', 'expression': 'x'}], {'k': 2., 'd': 1.}, timeout_s=.03)
    assert time.monotonic() - start < 2.
    assert {p.pid for p in mp.active_children()} == before


@pytest.mark.parametrize('scenario', ['fit_timeout', 'prediction_error', 'valid_negative', 'success'])
def test_three_entries_share_the_exact_frozen_plan_and_verdict(tmp_path, monkeypatch, capsys, scenario):
    settings = config()
    settings.update(seed=19, total_timeout_s=30.)
    if scenario == 'valid_negative':
        settings['holdout'][0]['values']['response'] = [99.] * 5
    store, _, plan, source, exp = prepared(tmp_path, **settings)
    training = tmp_path / 'training.json'; training.write_text(json.dumps(settings['training']))
    holdout = tmp_path / 'holdout.json'; holdout.write_text(json.dumps(settings['holdout']))
    if scenario == 'fit_timeout':
        monkeypatch.setattr(fitting, 'fit_parameters', fail_fits)
    if scenario == 'prediction_error':
        monkeypatch.setattr(fitting, 'predict', lambda *a, **k: (_ for _ in ()).throw(ValueError('prediction unavailable')))
    code1 = cli.main(['hypothesis', '--training', str(training), '--holdout', str(holdout),
        '--candidate', 'cand_baseline', '--starts', '1', '--seed', '19', '--max-nfev', '60',
        '--timeout', '20', '--total-timeout', '30', '--store', str(store.root)])
    first = json.loads(capsys.readouterr().out)
    assert first['plan_ref'] == plan
    code2 = cli.main(['study-run', plan, '--store', str(store.root)])
    second = json.loads(capsys.readouterr().out)
    provider = Scripted([act('run_study'), act('finish')])
    final = store.get(agent.run_agent(store, provider, [source], exp,
        mode='hypothesis', study_plan_ref=plan))['payload']
    records = [store.get(ref)['payload'] for ref in (first['ref'], second['ref'], final['run_ref'])]
    projected = [(r['status'], r['completion_eligible'], r['comparison'],
                  r['candidates']['cand_baseline']['phase_status']) for r in records]
    assert projected[0] == projected[1] == projected[2]
    assert all(r['plan_ref'] == plan and r['protocol']['total_timeout_s'] == 30. for r in records)
    assert len(store.list('agent_execution')) == 1
    assert all(row['role'] == 'training' for row in settings['training'])
    if scenario in ('fit_timeout', 'prediction_error'):
        assert code1 == code2 == 1 and final['state'] == 'incomplete'
        assert all(r['comparison']['winner'] is None for r in records)
    else:
        assert code1 == code2 == 0 and final['state'] == 'completed'
        assert all(r['scientific_status'] == 'not_verified' for r in records)
        if scenario == 'valid_negative':
            assert all(r['comparison']['ranking'] == 'none_adequate' for r in records)


def virtual_science(monkeypatch, settings):
    clock = {'elapsed': 0.}
    budgets = {'fit': [], 'crosscheck': [], 'predict': []}
    class VirtualDeadline(Deadline):
        def elapsed(self):
            return clock['elapsed']
    monkeypatch.setattr(hypotheses, 'Deadline', VirtualDeadline)
    def values(spec):
        return [2*(1-math.exp(-t)) for t in spec['times']]
    def fit(artifact, training, observations, bounds, **kwargs):
        budgets['fit'].append(kwargs['timeout_s']); clock['elapsed'] += 2.
        return {'status': 'succeeded', 'best': {'parameters': {'k': 2., 'd': 1.}, 'cost': 0.},
                'predictions': {r['id']: {'observables': {'response': values(r['experiment'])}} for r in training}}
    def cross(artifact, spec, observations, solver):
        budgets['crosscheck'].append(solver.timeout_s); clock['elapsed'] += 1.
        return SimulationResult('succeeded', spec.times, {'response': values({'times': spec.times})}, time_unit='second')
    def predict(artifact, spec, obs, parameters, **kwargs):
        budgets['predict'].append(kwargs['timeout_s']); clock['elapsed'] += 1.
        return {'status': 'succeeded', 'observables': {'response': values(spec)}}
    monkeypatch.setattr(fitting, 'fit_parameters', fit)
    monkeypatch.setattr(simulation, 'simulate', cross)
    monkeypatch.setattr(fitting, 'predict', predict)
    return clock, budgets


@pytest.mark.parametrize('candidates,total,expected_fit,expected_cross,expected_predict', [
    (['cand_baseline'], 3.5, [3.5], [1.5], [.5]),
    (['cand_baseline', 'cand_feedforward'], 5.5, [5.5, 2.5], [3.5, .5], []),
])
def test_global_budget_is_consumed_across_phases_and_candidates(tmp_path, monkeypatch,
        candidates, total, expected_fit, expected_cross, expected_predict):
    store, settings, plan, _, _ = prepared(tmp_path, candidates=candidates, total_timeout_s=total)
    clock, budgets = virtual_science(monkeypatch, settings)
    result = store.get(studies.execute_study(store, plan))['payload']
    assert result['status'] == 'timed_out' and not result['completion_eligible']
    assert budgets == {'fit': expected_fit, 'crosscheck': expected_cross, 'predict': expected_predict}
    assert result['deadline']['elapsed_s'] == clock['elapsed'] and result['deadline']['exhausted'] is True
    assert result['comparison']['winner'] is None
    assert result['experiment_suggestion']['max_nominal_prediction_gap'] is None


def test_legacy_v1_plan_is_preserved_and_execution_default_is_disclosed(tmp_path):
    from pathlib import Path
    original = json.loads((Path(__file__).resolve().parents[1] /
        'artifacts/audit-t04-study-execution-20261019/baseline-plan-v1.json').read_text())['payload']
    store = ArtifactStore(tmp_path / 'store')
    ref = store.put(studies.PLAN_KIND, original)
    before = deepcopy(store.get(ref))
    assert studies.load_plan(store, ref) == original
    summary = studies.plan_summary(store, ref)
    assert summary['deadline_origin'] == 'legacy_execution_default'
    result = store.get(studies.execute_study(store, ref))['payload']
    assert result['deadline']['origin'] == 'legacy_execution_default'
    assert result['protocol']['total_timeout_s'] == 300.
    assert result['completion_eligible'] and store.get(ref) == before


def test_timed_out_study_recovery_adopts_result_without_refitting_or_resetting_deadline(tmp_path, monkeypatch):
    store, _, plan, source, exp = prepared(tmp_path, total_timeout_s=4.)
    monkeypatch.setattr(fitting, 'fit_parameters', fail_fits)
    summary = studies.result_summary
    with monkeypatch.context() as patch:
        patch.setattr(studies, 'result_summary', lambda *a: (_ for _ in ()).throw(SystemExit('returned timeout result')))
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([act('run_study')]), [source], exp,
                            mode='hypothesis', study_plan_ref=plan)
    execution_ref = store.list('agent_execution')[0]
    report = recovery.diagnose_execution(store, execution_ref)
    assert report['status'] == 'ready'
    result_ref = report['adoptable_result_refs'][0]
    original = deepcopy(store.get(result_ref))
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('timed-out fit repeated'))
    final = store.get(agent.recover_agent(store, Scripted([act('finish')]), execution_ref))['payload']
    assert final['state'] == 'incomplete' and final['call_count'] == 2 and final['run_ref'] == result_ref
    assert store.get(result_ref) == original and len(store.list('fitting')) == 1
    assert len([o for o in recovery.diagnose_execution(store, execution_ref)['operations']
                if o['reserved']['name'] == 'run_study']) == 1


def exit_prediction(payload, out):
    import os
    os._exit(21)


def test_prediction_worker_crash_is_failure_and_is_reaped(monkeypatch):
    candidate = modeling.build_model(hypotheses.adaptation_candidates()['cand_baseline'])
    monkeypatch.setattr(fitting, '_predict_worker', exit_prediction)
    before = {p.pid for p in mp.active_children()}
    with pytest.raises(ValueError, match='without a result'):
        fitting.predict(candidate, ExperimentSpec((0., 1.), time_unit='second'),
            [{'id': 'response', 'expression': 'x'}], {'k': 2., 'd': 1.}, timeout_s=5.)
    assert {p.pid for p in mp.active_children()} == before


def test_prediction_parent_interrupt_also_stops_worker(monkeypatch):
    candidate = modeling.build_model(hypotheses.adaptation_candidates()['cand_baseline'])
    monkeypatch.setattr(fitting, '_predict_worker', slow_prediction)
    class InterruptDeadline:
        def __init__(self, *a):
            self.calls = 0
        def remaining(self):
            self.calls += 1
            if self.calls > 1:
                raise KeyboardInterrupt('injected during worker wait')
            return 1.
    monkeypatch.setattr(fitting, 'Deadline', InterruptDeadline)
    before = {p.pid for p in mp.active_children()}
    with pytest.raises(KeyboardInterrupt):
        fitting.predict(candidate, ExperimentSpec((0., 1.), time_unit='second'),
            [{'id': 'response', 'expression': 'x'}], {'k': 2., 'd': 1.}, timeout_s=5.)
    assert {p.pid for p in mp.active_children()} == before


@pytest.mark.parametrize('failure', ['fit', 'crosscheck', 'training', 'holdout', 'suggestion', 'deadline'])
def test_actual_failure_facts_override_stored_completed_flags(tmp_path, monkeypatch, failure):
    store, settings, plan, _, _ = prepared(tmp_path)
    virtual_science(monkeypatch, settings)
    result = store.get(studies.execute_study(store, plan))['payload']
    item = result['candidates']['cand_baseline']
    assert result['completion_eligible'] is True
    if failure == 'fit': item['fit_status'] = 'failed'
    elif failure == 'crosscheck': item['crosscheck']['ok'] = False
    elif failure == 'training': item['training_weighted_rmse'] = None
    elif failure == 'holdout': item['heldout_weighted_rmse'] = None
    elif failure == 'suggestion': item['phase_status']['suggestion'] = 'failed'
    else: result['deadline']['exhausted'] = True
    result.update(status='completed', completion_eligible=True)
    ref = store.put('hypothesis_study', result)
    summary = studies.result_summary(store, ref)
    assert summary['completion_eligible'] is False and summary['execution_status'] != 'completed'
    assert summary['comparison']['winner'] is None
    report = reporting.render_report(store, ref)
    assert 'Study execution: **completed**' not in report
