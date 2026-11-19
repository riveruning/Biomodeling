"""Reviewed exploration architecture; synthetic controls, not scientific scores."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import application, bundles, cli, estimation_workflow as workflow, exploration, fitting, reporting, simulation
from biomodel_agent.domain.contracts import SimulationResult
from biomodel_agent.storage import ArtifactStore
from test_estimation_architecture import setup as base_setup
from test_agent_estimation import setup as estimation_setup, compute, approve


@pytest.fixture
def setup(estimation_setup):
    store, estimate_config, result = compute(estimation_setup)
    review = approve(store, result)
    candidate = workflow.prepare_candidate(store, review)
    config = dict(experiment=dict(times=[0., .25, .5, 1., 2., 3.], time_unit='second'),
        observations=[dict(id='signal', expression='x', unit='dimensionless')],
        solver=dict(timeout_s=10., rtol=1e-7, atol=1e-10, max_steps=100000),
        context=estimate_config['context'], rationale='Explicit synthetic exploration of the fitted candidate.')
    return store, candidate, config


def test_prepare_is_readonly_until_plan_commit_and_freezes_execution_scope(setup, monkeypatch):
    store, candidate, c = setup
    before = store.list()
    workflow.load_candidate(store, candidate)
    assert store.list() == before
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('refitting'))
    monkeypatch.setattr(simulation, 'simulate', lambda *a, **k: pytest.fail('simulation during preparation'))
    ref = exploration.prepare_plan(store, candidate, c)
    c['experiment']['times'][1] = .1
    plan = exploration.load_plan(store, ref)
    assert plan['config']['experiment']['times'][1] == .25
    assert plan['execution_policy'] == dict(max_application_calls=1, max_integrations=2, timeout_scope='each_integration', cache=False, strict=False, reference=None)
    assert not store.list(exploration.EXECUTION) and not store.list('run')
    assert exploration.status(store, ref)['state'] == 'prepared'


@pytest.mark.parametrize('defect', ['fit_override','segment_fit_override','bad_initial','clock','context','no_obs',
    'collision','unknown_expression','bad_unit','bool_time','timeout','steps','threshold','reference','missing_rationale'])
def test_invalid_exploratory_protocol_never_reaches_plan_or_executor(setup, monkeypatch, defect):
    store, candidate, c = setup
    if defect == 'fit_override': c['experiment']['parameter_overrides'] = {'k': .7}
    elif defect == 'segment_fit_override': c['experiment']['segments'] = [dict(time=1., values={'k': .7})]
    elif defect == 'bad_initial': c['experiment']['initial_overrides'] = {'k': 1.}
    elif defect == 'clock': c['experiment']['time_unit'] = 'minute'
    elif defect == 'context': c['context'] = {'organism': 'different'}
    elif defect == 'no_obs': c['observations'] = []
    elif defect == 'collision': c['observations'][0]['id'] = 'x'
    elif defect == 'unknown_expression': c['observations'][0]['expression'] = 'missing_symbol'
    elif defect == 'bad_unit': c['observations'][0]['unit'] = ''
    elif defect == 'bool_time': c['experiment']['times'][1] = True
    elif defect == 'timeout': c['solver']['timeout_s'] = 61.
    elif defect == 'steps': c['solver']['max_steps'] = 1000001
    elif defect == 'threshold': c['convergence_threshold'] = 0.
    elif defect == 'reference': c['reference'] = {'values': [1.]}
    else: c['rationale'] = ''
    before = store.list()
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('executor called'))
    with pytest.raises(ValueError): exploration.prepare_plan(store, candidate, c)
    assert store.list() == before


@pytest.mark.parametrize('defect', ['scope','origin','numerical_link','model_value','review_binding'])
def test_changed_candidate_cannot_reuse_original_review(setup, defect):
    store, candidate, c = setup
    obj = store.get(candidate); p = obj['payload']
    if defect == 'scope': p['scope'] = 'strict_reproduction'
    elif defect == 'origin': p['estimated_parameters']['k']['origin'] = 'literature_reported'
    elif defect == 'review_binding': p['review_ref'] = p['parent_model_ref']
    else:
        model = store.get(p['model_ref'])
        if defect == 'numerical_link':
            model['payload']['element_map']['k/value'] = store.get(p['parent_model_ref'])['payload']['element_map']['k/value']
        else:
            model['payload']['sbml'] = model['payload']['sbml'].replace('value="0.7', 'value="0.9')
            # Ensure a changed but still parseable payload even if writer precision differs.
            model['payload']['id'] = 'changed_model_identity'
        new = store.put('model', model['payload'], refs=model['refs'])
        obj['refs'] = [new if r == p['model_ref'] else r for r in obj['refs']]; p['model_ref'] = new
    bad = store.put(workflow.CANDIDATE, p, refs=obj['refs'])
    before = store.list()
    with pytest.raises(ValueError): exploration.prepare_plan(store, bad, c)
    assert store.list() == before


def test_real_application_execution_and_refinement_keep_estimated_provenance(setup, monkeypatch):
    store, candidate, c = setup
    before_candidate = store.get(candidate); before_claims = store.list('claim')
    calls = []; real = simulation.simulate
    def simulate(artifact, experiment, observations, solver):
        assert store.list(exploration.EXECUTION)
        calls.append(solver)
        return real(artifact, experiment, observations, solver)
    monkeypatch.setattr(simulation, 'simulate', simulate)
    plan = exploration.prepare_plan(store, candidate, c)
    ref = exploration.execute(store, plan); summary = exploration.result_summary(store, ref)
    assert summary['status'] == 'succeeded' and summary['numeric_status'] == 'pass', summary
    assert summary['scientific_status'] == 'not_verified' and summary['comparison_status'] == 'not_run'
    assert len(calls) == 2 and all(s.timeout_s == 10. for s in calls)
    assert calls[1].rtol == calls[0].rtol/10 and calls[1].atol == calls[0].atol/10
    raw = store.get(summary['application_run_ref'])['payload']; values = raw['simulation']['values']['signal']
    assert values[-1] == pytest.approx(math.exp(-.7*3), abs=2e-6)
    assert raw['cached_from'] is None and raw['mode'] == 'structured_execution'
    frozen = store.get(raw['input_ref'])['payload']
    assert frozen['strict'] is False and frozen['reference'] is None
    assert 'k/value' in raw['source_validation']['missing']
    assert summary['estimated_parameters']['k']['origin'] == 'fitted_estimate'
    assert store.get(candidate) == before_candidate and store.list('claim') == before_claims
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('repeat execution'))
    assert exploration.execute(store, plan) == ref
    assert exploration.status(store, plan)['state'] == 'result_recorded'


def test_explicit_initial_setting_and_unknown_observation_unit_are_not_promoted(setup):
    store, candidate, c = setup
    c['experiment']['initial_overrides'] = {'x': 2.}
    c['observations'][0]['unit'] = 'unknown'
    ref = exploration.execute(store, exploration.prepare_plan(store, candidate, c))
    summary = exploration.result_summary(store, ref)
    assert summary['status'] == 'succeeded'
    raw = store.get(summary['application_run_ref'])['payload']
    assert raw['simulation']['values']['x'][0] == 2.
    assert 'exploratory_observation_units_unknown' in summary['unresolved']
    assert summary['scientific_support'] == 'unknown'


@pytest.mark.parametrize('outcome', ['timeout','negative','convergence','exception'])
def test_failure_facts_are_not_hidden_by_successful_coarse_execution(setup, monkeypatch, outcome):
    store, candidate, c = setup
    if outcome == 'exception':
        def fail(*a, **k): raise RuntimeError('Synthetic application failure.')
        monkeypatch.setattr(application, 'run_reproduction', fail)
    else:
        if outcome == 'negative': c['nonnegative'] = ['x']
        def simulate(artifact, experiment, observations, solver):
            if outcome == 'timeout': return SimulationResult('timed_out', diagnostics=('Synthetic timeout.',))
            value = -1. if outcome == 'negative' else 1. if solver.rtol == c['solver']['rtol'] else 0.
            return SimulationResult('succeeded', experiment.times, {'x': (value,)*len(experiment.times), 'signal': (value,)*len(experiment.times)},
                                    time_unit=experiment.time_unit, backend='synthetic outcome control')
        monkeypatch.setattr(simulation, 'simulate', simulate)
    plan = exploration.prepare_plan(store, candidate, c); ref = exploration.execute(store, plan)
    summary = exploration.result_summary(store, ref)
    assert summary['status'] != 'succeeded' and not summary['completion_eligible']
    if outcome in ('negative','convergence'):
        assert summary['execution_status'] == 'succeeded' and summary['scientific_status'] == 'failed'
    if outcome == 'timeout': assert summary['status'] == 'timed_out'
    if outcome == 'exception': assert summary['execution_status'] is None and 'Synthetic application failure' in summary['error']
    assert exploration.execute(store, plan) == ref


def test_reservation_failure_never_calls_executor_and_local_owner_blocks_concurrency(setup, monkeypatch):
    store, candidate, c = setup
    plan = exploration.prepare_plan(store, candidate, c)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('executor called'))
    from biomodel_agent import estimation
    with estimation._owner(store, plan):
        with pytest.raises(ValueError, match='active owner'): exploration.execute(store, plan)
    put = store.put
    def fail(kind, *args, **kwargs):
        if kind == exploration.EXECUTION: raise OSError('reservation failed')
        return put(kind, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(OSError): exploration.execute(store, plan)
    assert not store.list(exploration.RUN) and not store.list('run')


def test_unknown_return_blocks_repeat_even_when_application_run_exists(setup, monkeypatch):
    store, candidate, c = setup
    plan = exploration.prepare_plan(store, candidate, c); put = store.put
    def crash(kind, *args, **kwargs):
        if kind == exploration.RECEIPT: raise SystemExit('after application, before confirmed receipt')
        return put(kind, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', crash)
        with pytest.raises(SystemExit): exploration.execute(store, plan)
    assert len(store.list('run')) == 1 and not store.list(exploration.RUN)
    assert exploration.status(store, plan)['state'] == 'reserved_outcome_unknown_do_not_retry'
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('unknown effect repeated'))
    with pytest.raises(ValueError, match='interrupted'): exploration.execute(store, plan)


def test_frozen_plan_and_stored_summary_cannot_be_relabeled_as_reproduction(setup):
    store, candidate, c = setup
    plan = exploration.prepare_plan(store, candidate, c)
    obj = store.get(plan); obj['payload']['execution_policy']['strict'] = True
    bad = store.put(exploration.PLAN, obj['payload'], refs=obj['refs'])
    with pytest.raises(ValueError, match='frozen'): exploration.execute(store, bad)
    assert not store.list(exploration.EXECUTION)
    ref = exploration.execute(store, plan); obj = store.get(ref)
    obj['payload']['summary']['scientific_status'] = 'passed_structured_reference'
    bad = store.put(exploration.RUN, obj['payload'], refs=obj['refs'])
    with pytest.raises(ValueError, match='stored numerical facts'): reporting.render_report(store, bad)


def test_mismatched_actual_input_is_an_error_not_an_exploratory_success(setup, monkeypatch):
    store, candidate, c = setup
    real = application.run_reproduction
    def mismatched(*args, **kwargs):
        ref = real(*args, **kwargs); obj = store.get(ref)
        original_input = obj['payload']['input_ref']; inp = store.get(original_input)
        inp['payload']['experiment']['parameter_overrides'] = {'k': 99.}
        bad_input = store.put(application.SIMULATION_INPUT_KIND, inp['payload'], refs=inp['refs'])
        obj['payload']['input_ref'] = bad_input
        return store.put('run', obj['payload'], refs=[bad_input if r == original_input else r for r in obj['refs']])
    monkeypatch.setattr(application, 'run_reproduction', mismatched)
    ref = exploration.execute(store, exploration.prepare_plan(store, candidate, c))
    summary = exploration.result_summary(store, ref)
    assert summary['status'] == 'failed' and summary['execution_status'] is None
    assert 'does not match the frozen exploratory input' in summary['error']
    assert not summary['completion_eligible'] and summary['scientific_support'] == 'unknown'


def test_report_and_export_preserve_data_review_and_estimated_origin(setup, tmp_path, monkeypatch):
    store, candidate, c = setup
    plan = exploration.prepare_plan(store, candidate, c); ref = exploration.execute(store, plan)
    report = reporting.render_report(store, ref); p = store.get(candidate)['payload']
    assert 'fitted_estimate' in report and 'exploratory_only' in report
    assert p['result_ref'] in report and p['review_ref'] in report and p['plan_ref'] in report
    assert 'characters 0:' in report and 'not CI' in report and 'k/value' in report
    assert 'at most two integrations' in report and 'Trajectory preview' in report
    target = tmp_path/'bundle'; bundles.export_bundle(store, ref, target); reopened = ArtifactStore(target)
    assert reporting.render_report(reopened, ref) == report
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **k: pytest.fail('refitting on reuse'))
    assert exploration.execute(reopened, plan) == ref
    # A new explicit plan starts a new uncached application run, without refitting.
    new = exploration.prepare_plan(reopened, candidate, deepcopy(c))
    repeated = exploration.execute(reopened, new)
    assert repeated != ref
    raw = reopened.get(exploration.result_summary(reopened, repeated)['application_run_ref'])['payload']
    assert raw['cached_from'] is None


def test_cli_explicit_prepare_execute_and_common_report(setup, tmp_path, capsys):
    store, candidate, c = setup
    path = tmp_path/'exploration.json'; path.write_text(json.dumps(c))
    common = ['--store', str(store.root)]
    assert cli.main(['estimate-explore-prepare', *common, '--candidate-ref', candidate, '--config', str(path)]) == 0
    plan = json.loads(capsys.readouterr().out)['plan_ref']
    assert cli.main(['estimate-explore-run', plan, *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['scope'] == 'exploratory_only' and result['scientific_status'] == 'not_verified'
    assert cli.main(['estimate-explore-status', plan, *common]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'result_recorded'
    output = tmp_path/'report.md'
    assert cli.main(['report', result['run_ref'], *common, '--output', str(output)]) == 0
    assert 'fitted_estimate' in output.read_text()
