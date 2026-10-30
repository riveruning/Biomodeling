"""Explicit estimation protocol contracts; synthetic controls, not model evaluation."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, bundles, cli, estimation, evidence, fitting, modeling
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


@pytest.fixture
def setup(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    text = 'Synthetic fixture: signal x(t)=exp(-0.7*t); independent sigma=0.02 is an analyst assumption, not measurement uncertainty.'
    path = tmp_path/'source.txt'; path.write_text(text)
    source = evidence.ingest_source(store, path)
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .3),), rate_rules=(RateRule('x', '-k*x'),))
    model = store.put('model', to_dict(modeling.build_model(draft)))
    def row(name, role, times):
        return dict(id=name, role=role, experiment=to_dict(ExperimentSpec(tuple(times), time_unit='second')),
                    values={'signal':[math.exp(-.7*t) for t in times]}, sigma={'signal': .02},
                    units={'signal':'dimensionless'}, context={'organism':'synthetic'},
                    provenance=dict(source_ref=source, page=1, start=0, end=len(text),
                        origin='synthetic_control', sigma_origin='Analyst control assumption; not inferred from data.'))
    config = dict(model_ref=model, source_refs=[source],
        observations=[dict(id='signal', expression='x', unit='dimensionless')],
        parameters={'k':dict(bounds=[.05, 1.5], unit='second^-1', rationale='Synthetic control design bounds.')},
        training=[row('train', 'training', [0., .5, 1., 2., 3.])],
        validation=[row('check', 'validation', [0., .7, 1.7, 2.7])], context={'organism':'synthetic'},
        rationale='Separate estimation branch to test interface wiring.', starts=2, max_nfev=50)
    return store, source, config


def test_plan_freezes_roles_sources_units_bounds_and_summary_omits_validation_values(setup):
    store, source, config = setup
    ref = estimation.prepare_plan(store, config)
    before = store.get(ref)
    config['parameters']['k']['bounds'][0] = .4
    p = estimation.load_plan(store, ref)
    assert p['config']['parameters']['k']['bounds'][0] == .05
    assert p['config']['validation'][0]['provenance']['quote'].startswith('Synthetic fixture')
    summary = estimation.plan_summary(store, ref)
    assert summary['validation_conditions'] == 1
    assert not {'values', 'sigma', 'training', 'validation'} & set(summary)
    assert summary['budget']['total_timeout_s'] == 120.
    assert source in before['refs'] and store.get(ref) == before
    assert estimation.status(store, ref)['state'] == 'prepared'


@pytest.mark.parametrize('defect', ['no_data','missing_value','bool_value','bool_bound','sigma','role','unit',
    'context','outside_source','state_target','unknown_target','override','clock','duplicate_role_id','loss',
    'negative_budget','bool_starts','extra_field','too_many_parameters','observable_expression'])
def test_invalid_protocol_fails_before_plan_or_optimizer(setup, monkeypatch, defect):
    store, source, c = setup
    if defect == 'no_data': c['training'] = []
    elif defect == 'missing_value': c['training'][0]['values']['signal'][1] = None
    elif defect == 'bool_value': c['training'][0]['values']['signal'][1] = True
    elif defect == 'bool_bound': c['parameters']['k']['bounds'][0] = False
    elif defect == 'sigma': c['training'][0]['sigma']['signal'] = 0
    elif defect == 'role': c['training'][0]['role'] = 'validation'
    elif defect == 'unit': c['validation'][0]['units']['signal'] = 'mM'
    elif defect == 'context': c['validation'][0]['context']['organism'] = 'different'
    elif defect == 'outside_source': c['source_refs'] = []
    elif defect == 'state_target': c['parameters'] = {'x': c['parameters']['k']}
    elif defect == 'unknown_target': c['parameters'] = {'missing': c['parameters']['k']}
    elif defect == 'override': c['training'][0]['experiment']['parameter_overrides'] = {'k': .7}
    elif defect == 'clock': c['validation'][0]['experiment']['time_unit'] = 'minute'
    elif defect == 'duplicate_role_id': c['validation'][0]['id'] = 'train'
    elif defect == 'loss': c['loss'] = 'whatever'
    elif defect == 'negative_budget': c['total_timeout_s'] = -1
    elif defect == 'bool_starts': c['starts'] = True
    elif defect == 'extra_field': c['invented'] = 1
    elif defect == 'too_many_parameters': c['parameters'] = {f'k{i}': c['parameters']['k'] for i in range(9)}
    else: c['observations'][0]['expression'] = 'missing_symbol'
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('optimizer called'))
    before = store.list()
    with pytest.raises(ValueError): estimation.prepare_plan(store, c)
    assert store.list() == before


def test_plan_requires_current_implementation_and_revalidates_provenance(setup):
    store, _, c = setup
    ref = estimation.prepare_plan(store, c)
    obj = store.get(ref); obj['payload']['environment']['modules_sha256'] = 'changed'
    bad = store.put(estimation.PLAN, obj['payload'], refs=obj['refs'])
    with pytest.raises(ValueError, match='environment changed'): estimation.execute(store, bad)
    assert not store.list('parameter_estimation_execution')
    obj = store.get(ref); obj['payload']['config']['training'][0]['provenance']['quote'] = 'invented'
    bad = store.put(estimation.PLAN, obj['payload'], refs=obj['refs'])
    with pytest.raises(ValueError, match='frozen profile'): estimation.load_plan(store, bad)


def test_real_existing_fitter_returns_candidate_only_and_does_not_mutate_model(setup):
    store, source, c = setup
    original = store.get(c['model_ref']); original_source = store.get(source)
    plan = estimation.prepare_plan(store, c)
    result = estimation.execute(store, plan); p = store.get(result)['payload']
    assert p['execution_status'] == 'succeeded', p
    estimate = p['estimates']['k']
    assert estimate['value'] == pytest.approx(.7, abs=2e-5)
    assert estimate['origin'] == 'fitted_estimate' and estimate['adoption_status'] == 'candidate_only'
    assert p['validation_status'] == 'succeeded' and p['validation'][0]['weighted_rmse'] < .001
    assert p['diagnostics']['global_identifiability'] == 'unknown'
    assert p['diagnostics']['confidence_intervals'] == 'not_run'
    assert p['scientific_status'] == 'not_verified'
    assert store.get(c['model_ref']) == original and store.get(source) == original_source
    assert len(store.list('model')) == 1 and not store.list('claim')
    assert estimation.execute(store, plan) == result
    assert len(store.list('parameter_estimation_execution')) == 1


def test_nonunique_parameter_combination_preserves_alternatives_and_no_uniqueness_claim(setup):
    store, _, c = setup
    draft = ModelDraft('sum_decay', (State('x', 1.),), (Parameter('a', .2), Parameter('b', .2)),
                       rate_rules=(RateRule('x', '-(a+b)*x'),))
    c['model_ref'] = store.put('model', to_dict(modeling.build_model(draft)))
    spec = dict(bounds=[.05, 1.5], unit='second^-1', rationale='Synthetic combination control.')
    c['parameters'] = {name: deepcopy(spec) for name in ('a', 'b')}; c['starts'] = 3
    p = store.get(estimation.execute(store, estimation.prepare_plan(store, c)))['payload']
    assert p['execution_status'] == 'succeeded', p
    assert sum(v['value'] for v in p['estimates'].values()) == pytest.approx(.7, abs=2e-5)
    fit = store.get(p['fit_ref'])['payload']
    assert len(fit['starts']) == 3
    assert any(hi-lo > .01 for lo, hi in fit['uncertainty']['endpoint_spread'].values())
    assert 'distinct_near_equal_cost_endpoints_at_declared_heuristic' in p['diagnostics']['warnings']
    assert p['diagnostics']['global_identifiability'] == 'unknown'


def test_validation_never_enters_optimizer_and_fit_is_saved_before_prediction(setup, monkeypatch):
    store, _, c = setup
    real_fit = fitting.fit_parameters
    calls = []
    def fit(artifact, rows, observations, bounds, **kwargs):
        calls.append('fit')
        assert [r['id'] for r in rows] == ['train'] and all(r['role'] == 'training' for r in rows)
        assert not store.list('fitting')
        return real_fit(artifact, rows, observations, bounds, **kwargs)
    real_predict = fitting.predict
    def predict(artifact, experiment, observations, parameters, **kwargs):
        calls.append('validation')
        assert store.list('fitting')
        assert kwargs['timeout_s'] <= c.get('total_timeout_s', 120.)
        return real_predict(artifact, experiment, observations, parameters, **kwargs)
    monkeypatch.setattr(fitting, 'fit_parameters', fit); monkeypatch.setattr(fitting, 'predict', predict)
    result = estimation.execute(store, estimation.prepare_plan(store, c))
    assert calls == ['fit','validation']
    assert 'values' not in json.dumps(estimation.result_summary(store, result))


@pytest.mark.parametrize('failure', ['failed','timed_out','unsupported','exception','bad_endpoint','validation_failure'])
def test_failure_and_timeout_are_records_not_success_or_silent_retry(setup, monkeypatch, failure):
    store, _, c = setup
    def fit(*args, **kwargs):
        if failure == 'exception': raise RuntimeError('control failure')
        if failure in ('failed','timed_out','unsupported'):
            return dict(status=failure, best=None, starts=[], diagnostics=['explicit control'])
        return dict(status='succeeded', best=dict(parameters={'k': 99. if failure == 'bad_endpoint' else .7}, cost=0.), starts=[])
    def predict(*args, **kwargs): raise ValueError('validation control failure')
    monkeypatch.setattr(fitting, 'fit_parameters', fit); monkeypatch.setattr(fitting, 'predict', predict)
    plan = estimation.prepare_plan(store, c)
    result = estimation.execute(store, plan); p = store.get(result)['payload']
    assert p['execution_status'] != 'succeeded' and p['validation_status'] == 'incomplete'
    assert 'scientific_execution_incomplete' in p['unresolved']
    assert bool(p['estimates']) == (failure == 'validation_failure')
    assert estimation.execute(store, plan) == result


def test_interrupt_keeps_reservation_and_blocks_unknown_result_retry(setup, monkeypatch):
    store, _, c = setup
    plan = estimation.prepare_plan(store, c)
    def interrupt(*args, **kwargs): raise SystemExit('no confirmed result')
    monkeypatch.setattr(fitting, 'fit_parameters', interrupt)
    with pytest.raises(SystemExit): estimation.execute(store, plan)
    assert estimation.status(store, plan)['state'] == 'reserved_result_unknown_do_not_retry'
    with pytest.raises(ValueError, match='interrupted'): estimation.execute(store, plan)
    assert len(store.list('parameter_estimation_execution')) == 1


def test_fit_and_validation_share_remaining_budget_and_timeout_retains_committed_fit(setup, monkeypatch):
    store, _, c = setup
    c.update(total_timeout_s=5., fit_timeout_s=4.)
    elapsed = [0.]
    class Clock:
        def __init__(self, timeout_s): self.timeout = timeout_s
        def elapsed(self): return elapsed[0]
        def remaining(self, cap=None):
            remaining = self.timeout-elapsed[0]
            if remaining <= 0: raise TimeoutError('shared budget exhausted')
            return min(remaining, cap) if cap else remaining
    def fit(*args, **kwargs):
        assert kwargs['timeout_s'] == 4.
        elapsed[0] += 4.
        return dict(status='succeeded', best=dict(parameters={'k':.7}, cost=0.), starts=[])
    def predict(*args, **kwargs):
        assert kwargs['timeout_s'] == 1. and store.list('fitting')
        elapsed[0] += 2.
        return {}
    monkeypatch.setattr(estimation, 'Deadline', Clock)
    monkeypatch.setattr(fitting, 'fit_parameters', fit); monkeypatch.setattr(fitting, 'predict', predict)
    p = store.get(estimation.execute(store, estimation.prepare_plan(store, c)))['payload']
    assert p['execution_status'] == 'timed_out' and p['fit_ref']
    assert p['estimates']['k']['adoption_status'] == 'candidate_only'
    assert p['validation_status'] == 'incomplete'


def test_local_owner_and_failed_reservation_prevent_solver_call(setup, monkeypatch):
    store, _, c = setup
    plan = estimation.prepare_plan(store, c)
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('solver called'))
    with estimation._owner(store, plan):
        with pytest.raises(ValueError, match='active owner'): estimation.execute(store, plan)
    put = store.put
    def fail(kind, *args, **kwargs):
        if kind == 'parameter_estimation_execution': raise OSError('reservation could not commit')
        return put(kind, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(OSError): estimation.execute(store, plan)
    assert not store.list(estimation.RESULT)


def test_ordinary_paused_task_can_link_an_explicit_branch_without_being_completed(setup):
    store, source, c = setup
    class Ask:
        last_usage = {}
        def respond(self, messages):
            return dict(action='ask_researcher', args=dict(question='Provide k or authorize a separate estimation protocol.'))
    parent = agent.run_agent(store, Ask(), [source], ExperimentSpec((0., 1.)))
    original = store.get(parent); c['parent_run_ref'] = parent
    plan = estimation.prepare_plan(store, c)
    assert parent in store.get(plan)['refs'] and store.get(parent) == original
    assert store.get(parent)['payload']['state'] == 'awaiting_input'
    other_path = store.root.parent/'outside.txt'; other_path.write_text('Outside parent scope.')
    c['source_refs'].append(evidence.ingest_source(store, other_path))
    with pytest.raises(ValueError, match='parent run'): estimation.prepare_plan(store, c)


def test_unknown_metadata_is_exposed_and_no_validation_is_not_a_pass(setup):
    store, _, c = setup
    c['parameters']['k']['unit'] = 'unknown'; c['validation'] = []
    c['training'][0]['experiment']['time_unit'] = 'unknown'
    p = store.get(estimation.execute(store, estimation.prepare_plan(store, c)))['payload']
    assert p['execution_status'] == 'succeeded' and p['validation_status'] == 'not_run'
    assert {'declared_units_unknown','time_unit_unknown','validation_data_not_supplied'} <= set(p['unresolved'])


def test_export_and_cli_round_trip_reuse_recorded_result_without_refit(setup, tmp_path, capsys, monkeypatch):
    store, _, c = setup
    path = tmp_path/'protocol.json'; path.write_text(json.dumps(c))
    common = ['--store', str(store.root)]
    assert cli.main(['estimate-prepare', *common, '--config', str(path)]) == 0
    plan = json.loads(capsys.readouterr().out)['plan_ref']
    assert cli.main(['estimate-run', plan, *common]) == 0
    result = json.loads(capsys.readouterr().out)['result_ref']
    assert cli.main(['estimate-status', plan, *common]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'result_recorded'
    target = tmp_path/'bundle'; manifest = bundles.export_bundle(store, result, target)
    assert plan in manifest['objects_and_blobs']
    reopened = ArtifactStore(target)
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('refit after validation'))
    assert estimation.execute(reopened, plan) == result
    assert estimation.result_summary(reopened, result)['estimates']['k']['origin'] == 'fitted_estimate'
