"""Real integration, fixed references and interruption controls for plan execution."""
from copy import deepcopy
import math

import pytest

from biomodel_agent import application, bundles, modeling_plan as plans, modeling_workflow as workflow, timeline
from biomodel_agent.domain.contracts import FrozenMap, SimulationResult
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.recovery import execution_owner
from biomodel_agent.storage import ArtifactStore
from test_modeling_plan import setup


def prepared(tmp_path, *, reference=False, wrong_reference=False, mode='reproduction'):
    store, source, spec = setup(tmp_path)
    spec['mode'] = mode
    ref = plans.prepare(store, spec, [source]); compilation = plans.compile_plan(store, ref)
    config = dict(experiment=dict(times=[0., .5, 1., 2.], time_unit='second'), solver=dict(timeout_s=10.))
    if reference:
        times = tuple(config['experiment']['times'])
        values = tuple(math.exp(-.4*t)+(1 if wrong_reference else 0) for t in times)
        config.update(reference=to_dict(SimulationResult('succeeded', times, FrozenMap({'x': values}), time_unit='second')),
                      scales={'x': 1.}, threshold=.001)
    task = workflow.prepare(store, compilation, config)
    return store, ref, compilation, task, config


def test_real_run_matches_analytic_reference_and_is_idempotent(tmp_path, monkeypatch):
    store, plan_ref, compilation, task, _ = prepared(tmp_path, reference=True)
    ref = workflow.execute(store, task)
    result = workflow.summary(store, ref)
    assert result['state'] == 'completed' and result['numeric_status'] == 'pass'
    assert result['comparison_status'] == 'pass' and result['scientific_support'] == 'unknown'
    sim = store.get(result['application_run_ref'])['payload']['simulation']
    assert sim['values']['x'][-1] == pytest.approx(math.exp(-.8), rel=1e-6)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('must not execute again'))
    assert workflow.execute(store, task) == ref
    assert len(store.list('run')) == 1 and len(store.list(workflow.EXECUTION)) == 1
    assert workflow.status(store, task)['state'] == 'result_recorded'
    assert timeline.from_run(store, ref)['call_count'] == 0


def test_failed_reference_remains_failed_and_return_does_not_claim_support(tmp_path):
    store, _, _, task, _ = prepared(tmp_path, reference=True, wrong_reference=True)
    ref = workflow.execute(store, task); result = workflow.summary(store, ref)
    assert result['state'] == 'failed' and result['comparison_status'] == 'fail'
    assert result['numeric_status'] == 'pass'  # Solver checks and reference agreement are distinct.
    assert not result['completion_eligible'] and result['scientific_support'] == 'unknown'
    assert timeline.from_run(store, ref)['rows'][-1]['status'] == 'failed'


def test_without_reference_runs_numerically_but_preserves_unknown_science(tmp_path):
    store, _, _, task, _ = prepared(tmp_path)
    result = workflow.summary(store, workflow.execute(store, task))
    assert result['state'] == 'completed'
    assert result['comparison_status'] == 'not_run' and result['scientific_status'] == 'not_verified'


@pytest.mark.parametrize('defect', ['override', 'initial_override', 'segments', 'samples', 'timeout',
    'boolean_timeout', 'steps', 'foreign_observation', 'nonnegative', 'reference_without_scales', 'reference_grid',
    'scales_without_reference', 'unknown_field'])
def test_bad_execution_config_does_not_reserve_or_simulate(tmp_path, defect):
    store, _, compilation, _, config = prepared(tmp_path, reference=True)
    if defect == 'override': config['experiment']['parameter_overrides'] = {'k': .8}
    elif defect == 'initial_override': config['experiment']['initial_overrides'] = {'x': 2.}
    elif defect == 'segments': config['experiment']['segments'] = [dict(time=0., values={'k': .8})]
    elif defect == 'samples': config['experiment']['times'] = [0.]
    elif defect == 'timeout': config['solver']['timeout_s'] = 1000.
    elif defect == 'boolean_timeout': config['solver']['timeout_s'] = True
    elif defect == 'steps': config['solver']['max_steps'] = 1000001
    elif defect == 'foreign_observation': config['observations'] = [dict(id='obs', expression='foreign', unit='unknown')]
    elif defect == 'nonnegative': config['nonnegative'] = ['foreign']
    elif defect == 'reference_without_scales': config.pop('scales')
    elif defect == 'reference_grid': config['reference']['times'][1] = .75
    elif defect == 'scales_without_reference': config.pop('reference')
    elif defect == 'unknown_field': config['ignore_source_checks'] = True
    before = store.list('agent_task')
    with pytest.raises(ValueError): workflow.prepare(store, compilation, config)
    assert store.list('agent_task') == before and not store.list('run')


def test_owner_rejects_concurrent_execution(tmp_path):
    store, _, _, task, _ = prepared(tmp_path)
    with execution_owner(store, task):
        with pytest.raises(ValueError, match='active execution owner'): workflow.execute(store, task)
    assert not store.list(workflow.EXECUTION)


def test_unknown_effect_blocks_retry_and_status_is_readonly(tmp_path, monkeypatch):
    store, _, _, task, _ = prepared(tmp_path)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt): workflow.execute(store, task)
    before = store.list()
    assert workflow.status(store, task)['state'] == 'reserved_outcome_unknown_do_not_retry'
    assert store.list() == before
    with pytest.raises(ValueError, match='unknown'): workflow.execute(store, task)
    assert len(store.list(workflow.EXECUTION)) == 1


def test_confirmed_return_adopted_after_root_write_interruption(tmp_path, monkeypatch):
    store, _, _, task, _ = prepared(tmp_path)
    original = store.put
    def interrupted(kind, payload, refs=()):
        if kind == workflow.RUN:
            raise KeyboardInterrupt()
        return original(kind, payload, refs=refs)
    monkeypatch.setattr(store, 'put', interrupted)
    with pytest.raises(KeyboardInterrupt): workflow.execute(store, task)
    assert len(store.list('run')) == 1 and len(store.list(workflow.RECEIPT)) == 1
    monkeypatch.setattr(store, 'put', original)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('confirmed return must be adopted'))
    assert workflow.status(store, task)['state'] == 'confirmed_receipt_available'
    ref = workflow.execute(store, task)
    assert workflow.summary(store, ref)['state'] == 'completed' and len(store.list('run')) == 1


def test_error_receipt_does_not_trigger_automatic_retry(tmp_path, monkeypatch):
    store, _, _, task, _ = prepared(tmp_path)
    calls = []
    def fail(*a, **k):
        calls.append(1); raise RuntimeError('solver launch failed')
    monkeypatch.setattr(application, 'run_reproduction', fail)
    ref = workflow.execute(store, task)
    assert workflow.execute(store, task) == ref and len(calls) == 1
    assert workflow.summary(store, ref)['state'] == 'failed'


def test_export_read_and_explicit_replay_are_distinct_scientific_runs(tmp_path):
    store, _, _, task, _ = prepared(tmp_path, reference=True)
    ref = workflow.execute(store, task)
    manifest = bundles.export_bundle(store, ref, tmp_path / 'bundle')
    detached = ArtifactStore(tmp_path / 'bundle')
    assert workflow.summary(detached, ref) == workflow.summary(store, ref)
    assert task in manifest['objects_and_blobs']
    new = workflow.replay(detached, ref)
    assert new != ref and workflow.summary(detached, new)['state'] == 'completed'
    new_task = workflow.load_task(detached, workflow.load_run(detached, new)['task_ref'])
    assert new_task['replay_of'] == ref and len(detached.list('run')) == 2


def test_forged_completion_cannot_hide_bad_numeric_facts(tmp_path):
    store, _, _, task, _ = prepared(tmp_path, reference=True, wrong_reference=True)
    ref = workflow.execute(store, task); obj = store.get(ref)
    forged = deepcopy(obj['payload']); forged['state'] = 'completed'; forged['summary']['completion_eligible'] = True
    bad = store.put(workflow.RUN, forged, refs=obj['refs'])
    with pytest.raises(ValueError, match='differs'): workflow.summary(store, bad)


def test_deleted_mutable_index_cannot_reset_consumed_budget(tmp_path, monkeypatch):
    store, _, _, task, _ = prepared(tmp_path)
    ref = workflow.execute(store, task)
    import sqlite3
    with sqlite3.connect(store.index_path) as connection:
        connection.execute('DELETE FROM artifact_index WHERE kind IN (?, ?, ?)',
                           (workflow.RUN, workflow.EXECUTION, workflow.RECEIPT))
    assert not store.list(workflow.RUN)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('index deletion must not reset a reservation'))
    assert workflow.execute(store, task) == ref
    assert workflow.status(store, task)['state'] == 'result_recorded'


def test_changed_task_environment_cannot_consume_execution(tmp_path, monkeypatch):
    store, _, _, task, _ = prepared(tmp_path)
    original = application.environment_fingerprint
    monkeypatch.setattr(application, 'environment_fingerprint', lambda: {**original(), 'platform': 'changed'})
    with pytest.raises(ValueError, match='environment changed'): workflow.execute(store, task)
    assert not store.list(workflow.EXECUTION)


def test_timeline_cannot_be_rewritten_in_a_confirmed_run(tmp_path):
    store, _, _, task, _ = prepared(tmp_path)
    ref = workflow.execute(store, task); obj = store.get(ref)
    rewritten = deepcopy(obj['payload']); rewritten['events'][1]['outcome']['scientific_support'] = 'passed'
    forged = store.put(workflow.RUN, rewritten, refs=obj['refs'])
    with pytest.raises(ValueError): workflow.load_run(store, forged)


def test_explicit_missing_input_revision_reaches_actual_simulation(tmp_path):
    from test_modeling_plan import TEXT
    from biomodel_agent import evidence
    store, source, spec = setup(tmp_path, TEXT.replace('Parameter k = 0.4.', 'Parameter k is not reported.'))
    spec['candidates'][0]['draft']['parameters'][0]['value'] = None
    original = plans.prepare(store, spec, [source])
    assert plans.inspect(store, original)['state'] == 'awaiting_input'
    path = tmp_path/'reply.txt'; path.write_text(TEXT)
    supplied = evidence.ingest_source(store, path)
    _, _, updated = setup(tmp_path/'updated')
    for anchor in updated['evidence']:
        anchor['source_ref'] = supplied
    revised = plans.prepare(store, updated, [supplied], parent_ref=original, reason='User supplied an explicit complete specification.')
    compilation = plans.compile_plan(store, revised)
    task = workflow.prepare(store, compilation, dict(experiment=dict(times=[0., 1.], time_unit='second')))
    ref = workflow.execute(store, task)
    assert workflow.summary(store, ref)['state'] == 'completed'
    assert plans.inspect(store, original)['state'] == 'awaiting_input'


def test_requested_reference_not_compared_cannot_complete(tmp_path, monkeypatch):
    from biomodel_agent import validation
    from biomodel_agent.domain.contracts import SimulationResult
    store, _, _, task, _ = prepared(tmp_path, reference=True)
    original = validation.compare_series
    def unavailable_reference(actual, reference, *args, **kwargs):
        # Coarse/fine numerical checks still use actual solver outputs.
        if isinstance(reference, SimulationResult) and not reference.backend:
            raise ValueError('reference adapter could not perform the requested comparison')
        return original(actual, reference, *args, **kwargs)
    monkeypatch.setattr(validation, 'compare_series', unavailable_reference)
    ref = workflow.execute(store, task); result = workflow.summary(store, ref)
    assert result['numeric_status'] == 'pass' and result['comparison_status'] == 'not_run'
    assert not result['completion_eligible'] and result['state'] == 'failed'
    assert result['reference_requested'] is True and result['scientific_support'] == 'unknown'


def test_reference_columns_must_match_frozen_observed_outputs(tmp_path):
    store, _, compilation, _, config = prepared(tmp_path, reference=True)
    config['reference']['values'] = {'foreign': [1.] * len(config['experiment']['times'])}
    config['scales'] = {'foreign': 1.}
    before = store.list('agent_task')
    with pytest.raises(ValueError, match='reference columns'):
        workflow.prepare(store, compilation, config)
    assert store.list('agent_task') == before and not store.list('run')


def test_two_state_conversion_preserves_mapping_and_analytic_mass_balance(tmp_path):
    from biomodel_agent import evidence
    from biomodel_agent.domain.contracts import ModelDraft, Parameter, RateRule, State
    store = ArtifactStore(tmp_path/'store')
    declarations = ['Initial x = 1.', 'Initial y = 0.', 'Parameter k = 0.4.',
                    'Equation dx/dt = -k*x.', 'Equation dy/dt = k*x.']
    text = '\n'.join(declarations) + '\nTime is measured in seconds.\n'
    path = tmp_path/'conversion.txt'; path.write_text(text)
    source = evidence.ingest_source(store, path)
    targets = ['x/initial', 'y/initial', 'k/value', 'rule:x/expression', 'rule:y/expression']
    anchors = [dict(id=f'E{i}', source_ref=source, page=1, start=text.index(q), end=text.index(q)+len(q),
                    quote=q, interpretation='Supplied mathematical declaration.', conditions={'organism': 'synthetic'})
               for i, q in enumerate(declarations)]
    draft = ModelDraft('conversion', (State('x', 1.), State('y', 0.)), (Parameter('k', .4),),
        rate_rules=(RateRule('x', '-k*x'), RateRule('y', 'k*x')))
    spec = dict(schema_version=1, id='conversion', model_type='ode', mode='reproduction', context={'organism': 'synthetic'},
        entities=[dict(id='A', label='First model pool'), dict(id='B', label='Second model pool')], evidence=anchors,
        mechanisms=[dict(id='M', subject='A', object='B', relation='conversion', evidence_ids=['E3', 'E4'],
                         conditions={'organism': 'synthetic'}, rationale='Unverified interpretation of the paired equations.')],
        candidates=[dict(id='transfer', label='Supplied linear transfer', variable_map={'A': 'x', 'B': 'y'}, draft=to_dict(draft),
            decisions=[dict(target=t, origin='reported', evidence_ids=[f'E{i}'], mechanism_ids=['M'] if i >= 3 else [],
                            rationale='Use supplied mathematical declarations.', alternatives=[]) for i, t in enumerate(targets)])],
        selected_candidate='transfer')
    plan = plans.prepare(store, spec, [source]); compilation = plans.compile_plan(store, plan)
    times = (0., 1., 2.)
    reference = SimulationResult('succeeded', times, FrozenMap({'x': [math.exp(-.4*t) for t in times],
        'y': [1-math.exp(-.4*t) for t in times]}), time_unit='second')
    task = workflow.prepare(store, compilation, dict(experiment=dict(times=list(times), time_unit='second'),
        reference=to_dict(reference), scales={'x': 1., 'y': 1.}, nonnegative=['x', 'y']))
    result = workflow.summary(store, workflow.execute(store, task))
    assert result['comparison_status'] == 'pass' and result['state'] == 'completed'
    values = store.get(result['application_run_ref'])['payload']['simulation']['values']
    assert all(x+y == pytest.approx(1., abs=1e-8) for x, y in zip(values['x'], values['y']))


def test_exploratory_parameter_is_executed_without_old_numeric_evidence(tmp_path):
    store, source, spec = setup(tmp_path)
    spec['mode'] = 'exploration'
    candidate = spec['candidates'][0]
    candidate['draft']['parameters'][0]['value'] = .8
    candidate['decisions'][1].update(origin='assumed', evidence_ids=[],
        rationale='Explicit exploratory rate perturbation, not the original reported rate.')
    plan = plans.prepare(store, spec, [source]); compilation = plans.compile_plan(store, plan)
    _, receipt, artifact = plans.compiled_model(store, compilation)
    assert 'k/value' not in artifact.element_map
    task = workflow.prepare(store, compilation, dict(experiment=dict(times=[0., 1.], time_unit='second')))
    result = workflow.summary(store, workflow.execute(store, task))
    assert result['mode'] == 'exploration' and result['state'] == 'completed'
    assert result['scientific_support'] == 'unknown' and not result['strict_source_gate']
    actual = store.get(result['application_run_ref'])['payload']['simulation']['values']['x'][-1]
    assert actual == pytest.approx(math.exp(-.8), rel=1e-6)
    assert result['assumptions'] == receipt['assumptions'] and result['assumptions'][0]['target'] == 'k/value'
