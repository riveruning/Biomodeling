"""Scripted decisions over real source gates, solver and native recovery."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, application, bundles, context, native_modeling, recovery, reporting, workstate
from biomodel_agent.domain.contracts import ExperimentSpec, FrozenMap, SimulationResult
from biomodel_agent.storage import ArtifactStore
from test_modeling_plan import setup, TEXT
from test_shortfall_repair import Scripted, action, proposal as legacy_proposal


def plan_args(spec):
    spec = deepcopy(spec)
    for e in spec['evidence']:
        e.pop('start', None); e.pop('end', None)
    return dict(plan=spec)


def run(store, source, provider, **kwargs):
    return agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.), time_unit='second'),
                           modeling_decisions=True, **kwargs)


def test_native_complete_proposal_executes_and_preserves_closure(tmp_path):
    store, source, spec = setup(tmp_path)
    provider = Scripted([action('submit_plan', **plan_args(spec)), action('inspect_progress'), action('run'), action('finish')])
    ref = run(store, source, provider); p = store.get(ref)['payload']
    assert p['state'] == 'completed' and p['call_count'] == 4
    assert p['run_count'] == 1
    projection = workstate.from_run(store, ref)
    assert projection == p['progress'] and projection['modeling_decisions']['model_ready']
    submitted = next(e for e in p['events'] if e.get('tool') == 'submit_plan')
    assert submitted['state'] == 'ready'
    assert {submitted[k] for k in ['modeling_plan_ref', 'compilation_ref', 'submission_ref']} <= set(store.get(ref)['refs'])
    assert len(store.list('run')) == 1
    actual = store.get(p['run_ref'])['payload']['simulation']['values']['x'][-1]
    assert actual == pytest.approx(math.exp(-.4), rel=1e-6)
    assert 'Native Agent modeling decisions' in reporting.render_report(store, ref)
    manifest = bundles.export_bundle(store, ref, tmp_path/'bundle')
    detached = ArtifactStore(tmp_path/'bundle')
    assert submitted['submission_ref'] in manifest['objects_and_blobs']
    assert reporting.render_report(detached, ref) == reporting.render_report(store, ref)


def test_native_selection_gap_pauses_without_model_or_simulation(tmp_path):
    store, source, spec = setup(tmp_path); spec['selected_candidate'] = None
    ref = run(store, source, Scripted([action('submit_plan', **plan_args(spec))]))
    p = store.get(ref)['payload']
    assert p['state'] == 'awaiting_input' and p['call_count'] == 1
    assert not p['model_ref'] and not store.list('run')
    assert p['progress']['modeling_decisions']['decision_feedback']['state'] == 'awaiting_selection'


def test_clarification_revises_plan_in_original_task_and_cumulative_budget(tmp_path):
    store, source, spec = setup(tmp_path, TEXT.replace('Parameter k = 0.4.', 'Parameter k is not reported.'))
    spec['candidates'][0]['draft']['parameters'][0]['value'] = None
    initial = run(store, source, Scripted([action('submit_plan', **plan_args(spec))]), max_calls=6, task_planning=True)
    before = store.get(initial)['payload']
    assert before['state'] == 'awaiting_input'
    first_plan = before['progress']['modeling_decisions']['plan_ref']
    def clarified(messages):
        reply = next(json.loads(m['content']) for m in reversed(messages)
                     if m['role'] == 'user' and json.loads(m['content']).get('tool') == 'researcher_reply')
        updated = deepcopy(spec); updated['candidates'][0]['draft']['parameters'][0]['value'] = .4
        updated['evidence'].append(dict(id='E_k_reply', source_ref=reply['source_ref'], page=1,
            quote='Parameter k = 0.4.', interpretation='Attributed researcher assertion.', conditions={}))
        updated['candidates'][0]['decisions'][1].update(origin='reported', evidence_ids=['E_k_reply'], rationale='Use the attributed parameter reply.')
        return action('submit_plan', **plan_args(updated), reason='Cite the researcher reply for missing k.')
    provider = Scripted([clarified, action('inspect_progress'), action('run'), action('finish')])
    final = agent.resume_agent(store, provider, initial, 'Parameter k = 0.4.', respondent='Operator')
    p = store.get(final)['payload']
    assert p['state'] == 'completed' and p['call_count'] == 5 and p['task_ref'] == before['task_ref']
    latest = p['progress']['modeling_decisions']['plan_ref']
    plan = store.get(latest)['payload']
    assert plan['parent_ref'] == first_plan and plan['revision'] == 1
    assert len(p['source_refs']) == 2 and len(store.list('run')) == 1
    assert all(s['status'] in ('complete', 'not_applicable') for s in p['progress']['task_plan']['steps'])


@pytest.mark.parametrize('task_planning,failure_handling,scientific_skills', [(True,False,False),(True,True,True)])
def test_proposal_stage_integrates_existing_protocols(tmp_path, task_planning, failure_handling, scientific_skills):
    store, source, spec = setup(tmp_path)
    provider = Scripted([action('submit_plan', **plan_args(spec)), action('run'), action('finish')])
    ref = run(store, source, provider, task_planning=task_planning, failure_handling=failure_handling, scientific_skills=scientific_skills)
    assert store.get(ref)['payload']['state'] == 'completed'


@pytest.mark.parametrize('mutation', ['context', 'assumed', 'foreign_source', 'bad_quote', 'mode', 'unknown_field'])
def test_native_invalid_proposals_cannot_bypass_sources_or_frozen_conditions(tmp_path, mutation):
    store, source, spec = setup(tmp_path)
    if mutation == 'context': spec['context'] = {'organism': 'new condition'}
    elif mutation == 'assumed': spec['candidates'][0]['decisions'][1].update(origin='assumed', evidence_ids=[])
    elif mutation == 'foreign_source': spec['evidence'][0]['source_ref'] = '0'*64
    elif mutation == 'bad_quote': spec['evidence'][0]['quote'] = 'Initial x = 99.'
    elif mutation == 'mode': spec['mode'] = 'exploration'
    elif mutation == 'unknown_field': spec['verified'] = True
    ref = run(store, source, Scripted([action('submit_plan', **plan_args(spec))]), max_calls=1)
    p = store.get(ref)['payload']
    assert p['state'] in ('incomplete','awaiting_input') and p['model_ref'] is None and not store.list('run')
    assert len(store.list(native_modeling.SUBMISSION)) == 1


def test_opt_in_required_and_legacy_draft_cannot_skip_decision_gate(tmp_path):
    store, source, spec = setup(tmp_path)
    p = Scripted([action('submit_plan', **plan_args(spec))])
    ref = agent.run_agent(store, p, [source], ExperimentSpec((0., 1.)), max_calls=1)
    assert store.get(ref)['payload']['state'] == 'incomplete'
    assert not store.list(native_modeling.SUBMISSION)
    ref = run(store, source, Scripted([action('submit_draft', **legacy_proposal(source)), action('run')]))
    assert store.get(ref)['payload']['state'] == 'incomplete' and not store.list('model')


def test_invalid_plan_repair_records_revision_and_consumes_repair_budget(tmp_path):
    store, source, spec = setup(tmp_path)
    invalid = deepcopy(spec); invalid['candidates'][0]['draft']['parameters'][0]['value'] = .8
    provider = Scripted([action('submit_plan', **plan_args(invalid)),
        action('submit_plan', **plan_args(spec), reason='Correct k to the explicit source value.'), action('run'), action('finish')])
    ref = run(store, source, provider)
    p = store.get(ref)['payload']
    assert p['state'] == 'completed' and p['repair_count'] == 1 and p['call_count'] == 4
    assert store.get(p['progress']['modeling_decisions']['plan_ref'])['payload']['revision'] == 1


@pytest.mark.parametrize('tool', ['submit_plan', 'run'])
def test_returned_results_recovered_without_repeating_writes_or_science(tmp_path, monkeypatch, tool):
    store, source, spec = setup(tmp_path); original = store.put
    def interrupt(kind, payload, refs=None):
        ref = original(kind, payload, refs)
        if kind == 'agent_invocation' and payload['name'] == tool and payload['phase'] == 'returned':
            raise SystemExit('confirmed return saved')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', interrupt)
        with pytest.raises(SystemExit):
            run(store, source, Scripted([action('submit_plan', **plan_args(spec)), action('run')]))
    execution = store.list('agent_execution')[-1]
    report = recovery.diagnose_execution(store, execution)
    assert report['status'] == 'ready', report
    monkeypatch.setattr(native_modeling, 'submit', lambda *a, **k: pytest.fail('submission repeated'))
    if tool == 'run':
        monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('science repeated'))
    provider = Scripted(([action('run')] if tool == 'submit_plan' else []) + [action('finish')])
    result = store.get(agent.recover_agent(store, provider, execution))['payload']
    assert result['state'] == 'completed' and result['call_count'] == 3
    assert len(store.list('run')) == len(store.list(native_modeling.SUBMISSION)) == 1


def test_unknown_submission_effect_not_automatically_retried(tmp_path, monkeypatch):
    store, source, spec = setup(tmp_path)
    monkeypatch.setattr(native_modeling, 'submit', lambda *a, **k: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit): run(store, source, Scripted([action('submit_plan', **plan_args(spec))]))
    report = recovery.diagnose_execution(store, store.list('agent_execution')[-1])
    assert report['status'] == 'blocked' and not store.list('run')


def test_new_proposal_retained_by_context_selection():
    messages=[dict(role='system',content='policy'),dict(role='user',content='task'),
        dict(role='assistant',content=json.dumps(action('submit_plan',plan={'id':'candidate'}))),
        dict(role='user',content=json.dumps(dict(tool='submit_plan',decision_feedback={'state':'awaiting_input'}))),
        dict(role='user',content='latest')]
    assert {2,3} <= context.essential_indices(messages)


@pytest.mark.parametrize('setting', ['not_bool', 'context_without_flag', 'mode', 'initial_override',
    'parameter_override', 'estimation_request', 'conditions_type', 'too_many_sources', 'reference_without_scales'])
def test_incompatible_native_config_rejected_before_inference(tmp_path, setting):
    store, source, _ = setup(tmp_path); provider = Scripted([])
    kwargs = dict(modeling_decisions=True); experiment = ExperimentSpec((0., 1.), time_unit='second')
    sources = [source]
    if setting == 'not_bool': kwargs['modeling_decisions'] = 1
    elif setting == 'context_without_flag': kwargs.update(modeling_decisions=False, modeling_context={})
    elif setting == 'mode': kwargs['mode'] = 'hypothesis'
    elif setting == 'initial_override': experiment = ExperimentSpec((0.,1.), initial_overrides=FrozenMap({'x':2.}))
    elif setting == 'parameter_override': experiment = ExperimentSpec((0.,1.), parameter_overrides=FrozenMap({'k':.8}))
    elif setting == 'estimation_request': kwargs['estimation_requests'] = True
    elif setting == 'conditions_type': kwargs['modeling_context'] = {'temperature': 25.}
    elif setting == 'too_many_sources': sources = [source]*9
    elif setting == 'reference_without_scales': kwargs['reference'] = SimulationResult('succeeded',(0.,1.),FrozenMap({'x':(1.,.67)}),time_unit='second')
    with pytest.raises(ValueError): agent.run_agent(store,provider,sources,experiment,**kwargs)
    assert not provider.requests and not store.list('agent_execution')


def test_scientific_reference_skill_uses_actual_native_plan_result(tmp_path):
    store, source, spec = setup(tmp_path)
    provider = Scripted([dict(action='load_skill', args={'name':'reference_comparison_v1'}), action('submit_plan', **plan_args(spec)),
                         action('run'), action('inspect_progress'), action('finish')])
    reference = SimulationResult('succeeded',(0.,1.),FrozenMap({'x':(1.,math.exp(-.4))}),time_unit='second')
    ref = run(store, source, provider, scientific_skills=True, reference=reference, scales={'x':1.})
    p = store.get(ref)['payload']
    assert p['state'] == 'completed'
    procedures = p['progress']['scientific_skills']['procedures']
    assert next(r for r in procedures if r['name']=='reference_comparison_v1')['status'] == 'pass'


def test_source_reply_after_science_cannot_certify_the_old_plan(tmp_path):
    store, source, spec = setup(tmp_path)
    ref = run(store, source, Scripted([action('submit_plan', **plan_args(spec)), action('run'),
        action('ask_researcher', question='Confirm whether the parameter declaration requires correction?')]))
    assert store.get(ref)['payload']['state'] == 'awaiting_input'
    final = agent.resume_agent(store, Scripted([action('finish')]), ref, 'Parameter k = 0.8.', respondent='Operator')
    p = store.get(final)['payload']
    assert p['state'] == 'incomplete' and p['run_count'] == 1 and len(store.list('run')) == 1
    assert not p['progress']['modeling_decisions']['source_scope_current']
    assert not p['progress']['modeling_decisions']['model_ready']


def test_invalid_reference_columns_stop_before_scientific_reservation(tmp_path):
    store, source, spec = setup(tmp_path)
    provider = Scripted([action('submit_plan', **plan_args(spec)), action('run')])
    reference = SimulationResult('succeeded',(0.,1.),FrozenMap({'foreign':(1.,.67)}),time_unit='second')
    ref = run(store, source, provider, reference=reference, scales={'foreign':1.})
    p = store.get(ref)['payload']
    assert p['state'] == 'incomplete' and p['run_count'] == 0 and not store.list('run')


def test_unperformed_requested_reference_prevents_finish(tmp_path, monkeypatch):
    from biomodel_agent import validation
    store, source, spec = setup(tmp_path)
    original = validation.compare_series
    def unavailable(actual, reference, *args, **kwargs):
        if isinstance(reference, SimulationResult) and not reference.backend:
            raise ValueError('reference comparison unavailable')
        return original(actual, reference, *args, **kwargs)
    monkeypatch.setattr(validation, 'compare_series', unavailable)
    reference = SimulationResult('succeeded',(0.,1.),FrozenMap({'x':(1.,math.exp(-.4))}),time_unit='second')
    provider=Scripted([action('submit_plan',**plan_args(spec)),action('run'),action('finish')])
    ref=run(store,source,provider,reference=reference,scales={'x':1.})
    p=store.get(ref)['payload']
    assert p['state']=='incomplete' and p['run_count']==1
    assert store.get(p['run_ref'])['payload']['comparison']['status']=='not_run'
