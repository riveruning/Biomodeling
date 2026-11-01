"""Bounded task plans, actual ODEs, immutable history and controlled recovery."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, context, evidence, reporting, task_plans, workstate
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State, SimulationResult
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


KEYS = ('id', 'kind', 'goal', 'depends_on', 'target', 'attempt_limit')


class Scripted:
    last_usage = {}
    def __init__(self, actions): self.actions = iter(actions); self.requests = []
    def respond(self, messages):
        self.requests.append(deepcopy(messages))
        out = next(self.actions)
        return out(messages) if callable(out) else out


def act(name, **args): return dict(action=name, args=args)


def setup(tmp_path, text='Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'):
    store = ArtifactStore(tmp_path/'store')
    path = tmp_path/'source.txt'; path.write_text(text)
    source = evidence.ingest_source(store, path)
    return store, source


def proposal(source, initial_source=None, value=.4):
    return dict(draft=to_dict(ModelDraft('decay', (State('x', 1.),), (Parameter('k', value),),
        rate_rules=(RateRule('x', '-k*x'),))), evidence=[dict(target=target, source_ref=ref, page=1, quote=quote)
        for target, ref, quote in [('x/initial', initial_source or source, 'Initial x = 1.'),
                                 ('k/value', source, f'Parameter k = {value!r}.'),
                                 ('rule:x/expression', source, 'Equation dx/dt = -k*x.')]])


def definition(plan): return [{k: s[k] for k in KEYS} for s in plan['steps']]


def revision(messages, *, target='k/value'):
    plan = json.loads(messages[1]['content'])['task_plan']
    steps = definition(plan)
    steps.append(dict(id='lookup_k', kind='investigate', goal='检查参数 k 的当前来源',
                      depends_on=['sources'], target=target, attempt_limit=2))
    next(s for s in steps if s['id'] == 'draft')['depends_on'].append('lookup_k')
    return act('revise_plan', expected_plan_ref=plan['plan_ref'], reason='增加参数查证，重新提交后再执行', steps=steps)


def views(record): return {s['id']: s for s in record['progress']['task_plan']['steps']}


def test_initial_plan_and_real_execution_reference_not_requested(tmp_path):
    store, source = setup(tmp_path)
    provider = Scripted([act('submit_draft', **proposal(source)), act('run'), act('finish')])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), task_planning=True)
    p = store.get(ref)['payload']
    assert p['state'] == 'completed', p
    plan = p['progress']['task_plan']
    assert all(s['status'] == ('not_applicable' if s['id'] == 'compare' else 'complete') for s in plan['steps']), plan
    assert plan['scientific_support'] == 'unknown' and plan['revision'] == 0
    assert views(p)['execute']['artifact_refs'] == [p['run_ref']]
    assert store.get(p['run_ref'])['payload']['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-6)
    assert workstate.from_run(store, ref) == p['progress']
    assert 'Versioned task plan' in reporting.render_report(store, ref)


def test_revision_invalidates_old_draft_and_blocks_skipping_lookup(tmp_path):
    store, source = setup(tmp_path)
    def stale_check(messages):
        plan = json.loads(messages[-1]['content'])['task_plan']
        steps = {s['id']: s for s in plan['steps']}
        assert steps['draft']['status'] == 'stale'
        assert steps['validate']['status'] == 'stale'
        assert steps['lookup_k']['status'] == 'complete'
        return act('submit_draft', **proposal(source))
    provider = Scripted([act('submit_draft', **proposal(source)), revision,
        act('run'), act('investigate_parameter', target='k/value', query='k'),
        act('inspect_progress'), stale_check, act('run'), act('finish')])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), task_planning=True, max_calls=10)
    p = store.get(ref)['payload']
    assert p['state'] == 'completed', p
    assert p['call_count'] == 8 and len(store.list('run')) == 1
    assert any(e['type'] == 'plan_blocked' and e['tool'] == 'run' for e in p['events'])
    plan = p['progress']['task_plan']
    assert plan['revision'] == 1 and plan['edit_count'] == 1 and plan['remaining_calls'] == 2
    assert views(p)['draft']['proof_call'] == 6
    assert views(p)['lookup_k']['attempts'] == 1
    task = store.get(p['task_ref'])['payload']
    initial_ref = task['planning']['initial_plan_ref']
    assert task_plans.load(store, task['planning'], initial_ref)['revision'] == 0
    assert store.get(plan['plan_ref'])['payload']['previous_ref'] == initial_ref


@pytest.mark.parametrize('mutation', ['delete_core','weaken_dependency','cycle','unknown_dependency',
    'fake_status','fake_evidence','unknown_kind','wrong_core_kind','bad_budget','bool_budget',
    'foreign_source','unfrozen_assessment','duplicate_id','long_goal','too_many_steps'])
def test_invalid_plan_does_not_create_a_revision(tmp_path, mutation):
    store, source = setup(tmp_path)
    config = task_plans.create(store, [source], 12)
    root = task_plans.load(store, config, config['initial_plan_ref'])
    steps = deepcopy(root['steps'])
    if mutation == 'delete_core': steps.pop()
    if mutation == 'weaken_dependency': steps[3]['depends_on'] = ['sources']
    if mutation == 'cycle': steps[1]['depends_on'].append('report')
    if mutation == 'unknown_dependency': steps[1]['depends_on'].append('missing')
    if mutation == 'fake_status': steps[1]['status'] = 'complete'
    if mutation == 'fake_evidence': steps[1]['artifact_refs'] = [source]
    if mutation == 'unknown_kind': steps[1]['kind'] = 'arbitrary_python'
    if mutation == 'wrong_core_kind': steps[1]['kind'] = 'execute'
    if mutation == 'bad_budget': steps[1]['attempt_limit'] = 13
    if mutation == 'bool_budget': steps[1]['attempt_limit'] = True
    if mutation == 'foreign_source': steps.append(dict(id='read_other',kind='inspect',goal='read',depends_on=['sources'],target='0'*64,attempt_limit=1))
    if mutation == 'unfrozen_assessment': steps.append(dict(id='assess_k',kind='assess',goal='assess',depends_on=['sources'],target='k/value',attempt_limit=1))
    if mutation == 'duplicate_id': steps.append(deepcopy(steps[-1]))
    if mutation == 'long_goal': steps[1]['goal'] = 'x'*161
    if mutation == 'too_many_steps': steps.extend([deepcopy(steps[-1])]*7)
    with pytest.raises(ValueError):
        task_plans.revise(store, config, config['initial_plan_ref'], config['initial_plan_ref'], steps, 'test invalid plan', 1)
    assert len(store.list('task_plan')) == 1


def test_stale_revision_and_edit_limits_preserve_history(tmp_path):
    store, source = setup(tmp_path)
    config = task_plans.create(store, [source], 12)
    ref = initial = config['initial_plan_ref']
    for i in range(4):
        old = task_plans.load(store, config, ref)
        steps = deepcopy(old['steps']); steps[-1]['goal'] += str(i)
        out = task_plans.revise(store, config, ref, ref, steps, 'bounded revision '+str(i), i+1)
        ref = out['plan_ref']
    steps[-1]['goal'] += 'next'
    with pytest.raises(ValueError, match='revision budget'):
        task_plans.revise(store, config, ref, ref, steps, 'one too many', 5)
    with pytest.raises(ValueError, match='plan changed'):
        task_plans.revise(store, config, ref, initial, steps, 'stale optimistic revision', 5)
    assert len(store.list('task_plan')) == 5


def test_cannot_increase_step_budget_or_revise_after_execution(tmp_path):
    store, source = setup(tmp_path)
    config = task_plans.create(store, [source], 12)
    ref = config['initial_plan_ref']; root = task_plans.load(store, config, ref)
    steps = deepcopy(root['steps']); steps[1]['attempt_limit'] = 2
    ref = task_plans.revise(store, config, ref, ref, steps, 'limit draft repairs', 1)['plan_ref']
    steps[1]['attempt_limit'] = 3
    with pytest.raises(ValueError, match='cannot increase'):
        task_plans.revise(store, config, ref, ref, steps, 'try to increase', 2)
    with pytest.raises(ValueError, match='after scientific execution'):
        task_plans.revise(store, config, ref, ref, steps, 'after evaluation', 2, run_count=1)


def test_reply_creates_scope_revision_and_requires_fresh_submission(tmp_path):
    store, source = setup(tmp_path)
    first = agent.run_agent(store, Scripted([act('submit_draft', **proposal(source)),
        act('ask_researcher', question='Confirm initial x?')]), [source], ExperimentSpec((0., 1.)), task_planning=True)
    parent = store.get(first)
    def inspect_refreshed(messages):
        plan = json.loads(messages[-1]['content'])['task_plan']
        assert plan['revision'] == 1 and plan['edit_count'] == 0
        assert next(s for s in plan['steps'] if s['id'] == 'draft')['status'] == 'stale'
        return act('run')  # Must be blocked until the draft is revalidated.
    def submit(messages):
        reply = [json.loads(m['content']) for m in messages if m['role']=='user' and m['content'].startswith('{')]
        new_source = next(m['source_ref'] for m in reply if m.get('tool') == 'researcher_reply')
        return act('submit_draft', **proposal(source, initial_source=new_source))
    ref = agent.resume_agent(store, Scripted([inspect_refreshed, submit, act('run'), act('finish')]),
                             first, 'Initial x = 1.', respondent='Scripted researcher fixture')
    p = store.get(ref)['payload']
    assert p['state'] == 'completed' and p['call_count'] == 6, p
    assert p['progress']['task_plan']['remaining_calls'] == 2
    assert store.get(first) == parent
    assert len(store.list('task_plan')) == 2
    assert any(e.get('tool') == 'refresh_plan' for e in p['events'])


def test_identical_ready_draft_and_repeat_execution_are_not_repeated(tmp_path):
    store, source = setup(tmp_path)
    p = store.get(agent.run_agent(store, Scripted([act('submit_draft', **proposal(source)),
        act('submit_draft', **proposal(source)), act('run'), act('run'), act('finish')]),
        [source], ExperimentSpec((0., 1.)), task_planning=True))['payload']
    assert p['state'] == 'completed' and p['call_count'] == 5
    assert len(store.list('model_draft')) == len(store.list('run')) == 1
    assert sum(e['type'] == 'plan_blocked' for e in p['events']) == 2


def test_planning_is_opt_in_and_frozen_studies_are_not_changed(tmp_path):
    store, source = setup(tmp_path)
    ref = agent.run_agent(store, Scripted([act('ask_researcher',question='Need clarification')]), [source], ExperimentSpec((0.,1.)))
    assert 'planning' not in store.get(store.get(ref)['payload']['task_ref'])['payload']
    assert 'task_plan' not in store.get(ref)['payload']['progress']
    assert not store.list('task_plan')
    for options in [dict(task_planning='yes'), dict(task_planning=True,mode='hypothesis'),
                    dict(task_planning=True,study_plan_ref='0'*64)]:
        with pytest.raises(ValueError):
            agent.run_agent(store, Scripted([]), [source], ExperimentSpec((0.,1.)), **options)


def test_latest_plan_is_kept_during_context_compaction():
    messages = [dict(role='system',content='instructions'),dict(role='user',content='{}')]
    revised = dict(role='user',content=json.dumps(dict(tool='revise_plan',ok=True,task_plan={'marker':'latest'})))
    refreshed = dict(role='user',content=json.dumps(dict(tool='refresh_plan',ok=True,task_plan={'marker':'new scope'})))
    messages.extend([revised, refreshed])
    messages.extend(dict(role='user',content=json.dumps(dict(tool='inspect_source',text='x'*1800))) for _ in range(12))
    packet, audit = context.build_context(messages, context.policy(8000))
    assert audit['compacted'] and revised in packet and refreshed in packet


def test_returned_revision_is_adopted_after_interruption(tmp_path, monkeypatch):
    from biomodel_agent import recovery
    store, source = setup(tmp_path)
    put = store.put
    def crash(kind, payload, refs=None):
        ref = put(kind, payload, refs)
        if kind == 'agent_invocation' and payload['name'] == 'revise_plan' and payload['phase'] == 'returned':
            raise SystemExit('persisted plan return')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', crash)
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([revision]), [source], ExperimentSpec((0.,1.)), task_planning=True)
    execution = store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store, execution)['status'] == 'ready'
    monkeypatch.setattr(task_plans, 'revise', lambda *a, **k: pytest.fail('revision callback repeated'))
    ref = agent.recover_agent(store, Scripted([act('ask_researcher',question='Need confirmation')]), execution)
    p = store.get(ref)['payload']
    assert p['state'] == 'awaiting_input' and p['call_count'] == 2
    assert p['progress']['task_plan']['revision'] == 1
    assert len(store.list('task_plan')) == 2


def test_unknown_plan_write_is_not_retried(tmp_path, monkeypatch):
    from biomodel_agent import recovery
    store, source = setup(tmp_path)
    put = store.put
    def crash(kind, payload, refs=None):
        ref = put(kind, payload, refs)
        if kind == 'task_plan' and payload['revision'] == 1:
            raise SystemExit('no returned revision receipt')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', crash)
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([revision]), [source], ExperimentSpec((0.,1.)), task_planning=True)
    execution = store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store, execution)['status'] == 'blocked'
    with pytest.raises(recovery.RecoveryBlocked):
        agent.recover_agent(store, Scripted([]), execution)


@pytest.mark.parametrize('correct', [True, False])
def test_frozen_reference_comparison_is_not_skipped_or_self_approved(tmp_path, correct):
    store, source = setup(tmp_path)
    reference = SimulationResult(status='succeeded', times=(0., 1.), values={'x': (1., math.exp(-.4) if correct else .1)}, backend='analytic_fixture')
    p = store.get(agent.run_agent(store, Scripted([act('submit_draft', **proposal(source)), act('run'), act('finish')]),
        [source], ExperimentSpec((0.,1.)), task_planning=True, reference=reference, scales={'x':1.}, max_calls=3))['payload']
    compare = views(p)['compare']
    assert compare['outcome'] == ('pass' if correct else 'fail')
    assert compare['status'] == ('complete' if correct else 'failed')
    assert p['state'] == ('completed' if correct else 'incomplete')
    # The reference values remain evaluator-side, not in the model task packet.
    initial = json.loads(p['events'][0]['requests'][1]['content'])
    assert 'reference' not in initial and 'values' not in initial


def test_blocked_plan_action_consumes_calls_but_not_scientific_execution(tmp_path):
    store, source = setup(tmp_path)
    p = store.get(agent.run_agent(store, Scripted([act('run'), act('run')]),
        [source], ExperimentSpec((0.,1.)), task_planning=True, max_calls=2))['payload']
    assert p['state'] == 'incomplete' and p['call_count'] == 2
    assert p['progress']['task_plan']['remaining_calls'] == 0
    assert not store.list('run')
    assert views(p)['execute']['attempts'] == 0


def test_optional_step_budget_is_not_reset_by_revisions(tmp_path):
    store, source = setup(tmp_path)
    def reduce(messages):
        plan = json.loads(messages[-1]['content'])['task_plan']
        steps = definition(plan)
        next(s for s in steps if s['id']=='lookup_k')['attempt_limit'] = 1
        return act('revise_plan', expected_plan_ref=plan['plan_ref'],reason='只允许一次参数查证',steps=steps)
    provider = Scripted([revision, act('inspect_progress'), reduce,
        act('investigate_parameter',target='k/value',query='k'),
        act('investigate_parameter',target='k/value',query='k again'),
        act('ask_researcher',question='Confirm the source?')])
    p = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0.,1.)), task_planning=True,max_calls=10))['payload']
    assert p['call_count'] == 6 and len(store.list('parameter_investigation')) == 1
    assert views(p)['lookup_k']['attempts'] == 1 and views(p)['lookup_k']['remaining_attempts'] == 0
    assert any(e['type']=='plan_blocked' and e['tool']=='investigate_parameter' for e in p['events'])


@pytest.mark.parametrize('budget', [True, 0, 51, '8'])
def test_plan_root_requires_a_real_frozen_budget(tmp_path, budget):
    store, source = setup(tmp_path)
    with pytest.raises(ValueError,match='call budget'):
        task_plans.create(store,[source],budget)
    assert not store.list('task_plan')


def test_forged_revision_does_not_gain_completion_or_omit_lineage(tmp_path):
    store, source = setup(tmp_path)
    config = task_plans.create(store,[source],8)
    root = config['initial_plan_ref']
    steps = deepcopy(task_plans.load(store,config,root)['steps'])
    steps[-1]['goal'] += ' checked'
    ref = task_plans.revise(store,config,root,root,steps,'valid revision',1)['plan_ref']
    env = store.get(ref)
    forged = deepcopy(env['payload']); forged['edit_count'] = 0
    corrupt = store.put('task_plan',forged,refs=env['refs'])
    with pytest.raises(ValueError,match='recomputed transition'):
        task_plans.load(store,config,corrupt)
    unlinked = store.put('task_plan',env['payload'],refs=[source])
    with pytest.raises(ValueError,match='immutable source or revision links'):
        task_plans.load(store,config,unlinked)


def test_no_op_revision_is_not_written(tmp_path):
    store,source = setup(tmp_path)
    config = task_plans.create(store,[source],8)
    ref=config['initial_plan_ref']
    with pytest.raises(ValueError,match='no-op'):
        task_plans.revise(store,config,ref,ref,task_plans.load(store,config,ref)['steps'],'unchanged',1)
    assert len(store.list('task_plan'))==1


def test_non_attributed_scope_extension_cannot_refresh_plan(tmp_path):
    store,source = setup(tmp_path)
    config = task_plans.create(store,[source],8)
    path=tmp_path/'external.txt';path.write_text('Unapproved scope expansion')
    other=evidence.ingest_source(store,path)
    with pytest.raises(ValueError,match='attributed replies'):
        task_plans.refresh(store,config,config['initial_plan_ref'],[source,other],1)
    assert len(store.list('task_plan'))==1


def test_requested_reference_with_missing_scales_is_unresolved_not_passed(tmp_path):
    store, source = setup(tmp_path)
    reference = SimulationResult(status='succeeded', times=(0.,1.), values={'x':(1.,math.exp(-.4))})
    ref = agent.run_agent(store,Scripted([act('submit_draft',**proposal(source)),act('run'),act('finish')]),
                          [source],ExperimentSpec((0.,1.)),task_planning=True,reference=reference,max_calls=3)
    p=store.get(ref)['payload']
    assert p['state']=='incomplete'
    assert views(p)['compare']['outcome']=='not_run'
    assert views(p)['compare']['status']=='failed'


def test_assessment_step_uses_frozen_parameter_requirements_and_real_result(tmp_path):
    from test_parameter_applicability import setup as parameter_setup, proposal as parameter_proposal, SPEC
    store,source,text=parameter_setup(tmp_path)
    def add_assessment(messages):
        plan=json.loads(messages[1]['content'])['task_plan'];steps=definition(plan)
        steps.append(dict(id='check_k',kind='assess',goal='核对参数条件',depends_on=['sources'],target='k/value',attempt_limit=2))
        next(s for s in steps if s['id']=='draft')['depends_on'].append('check_k')
        return act('revise_plan',expected_plan_ref=plan['plan_ref'],steps=steps,reason='核对后再提交')
    def submit(messages):
        assessment=json.loads(messages[-1]['content'])['assessment_ref']
        return act('submit_draft',**parameter_proposal(source,assessment))
    p=store.get(agent.run_agent(store,Scripted([add_assessment,
        act('assess_parameter',target='k/value',source_ref=source,page=1,start=0,end=len(text)),
        submit,act('run'),act('finish')]),[source],ExperimentSpec((0.,1.),time_unit='second'),
        task_planning=True,parameter_requirements={'k/value':SPEC}))['payload']
    assert p['state']=='completed',p
    assert views(p)['check_k']['status']=='complete'
    assert views(p)['check_k']['outcome']=='declared_fields_match'


def test_returned_scope_refresh_is_adopted_without_duplicate_revision(tmp_path,monkeypatch):
    from biomodel_agent import recovery
    store,source=setup(tmp_path)
    first=agent.run_agent(store,Scripted([act('ask_researcher',question='What is initial x?')]),
                          [source],ExperimentSpec((0.,1.)),task_planning=True)
    put=store.put
    def crash(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='agent_invocation' and payload['name']=='refresh_plan' and payload['phase']=='returned':
            raise SystemExit('refresh return persisted')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',crash)
        with pytest.raises(SystemExit):
            agent.resume_agent(store,Scripted([]),first,'Initial x = 1.',respondent='Scripted fixture')
    executions=[r for r in store.list('agent_execution') if store.get(r)['payload']['parent_run_ref']==first]
    assert len(executions)==1
    execution=executions[0]
    assert recovery.diagnose_execution(store,execution)['status']=='ready'
    monkeypatch.setattr(task_plans,'refresh',lambda *a,**k:pytest.fail('refresh was repeated'))
    resumed=agent.recover_agent(store,Scripted([act('ask_researcher',question='Confirm remaining conditions?')]),execution)
    p=store.get(resumed)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==2
    assert p['progress']['task_plan']['revision']==1 and len(store.list('task_plan'))==2


def test_recovery_rejects_returned_plan_that_does_not_match_request(tmp_path,monkeypatch):
    from biomodel_agent import recovery
    store,source=setup(tmp_path)
    put=store.put
    def forged_return(kind,payload,refs=None):
        if kind=='agent_invocation' and payload['name']=='revise_plan' and payload['phase']=='returned':
            payload=deepcopy(payload)
            task=store.get(store.list('agent_task')[0])['payload']
            root=task['planning']['initial_plan_ref']
            payload['details']=dict(plan_ref=root,revision=0,changed_steps=[])
            ref=put(kind,payload,refs=[*refs,root])
            raise SystemExit('forged return stored for negative control')
        return put(kind,payload,refs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',forged_return)
        with pytest.raises(SystemExit):
            agent.run_agent(store,Scripted([revision]),[source],ExperimentSpec((0.,1.)),task_planning=True)
    execution=store.list('agent_execution')[0]
    report=recovery.diagnose_execution(store,execution)
    assert report['status']=='blocked'
    assert any('task_plan_result_request_mismatch' in r for r in report['reasons'])


def test_a_new_step_does_not_spend_its_budget_on_earlier_unplanned_actions(tmp_path):
    store,source=setup(tmp_path)
    def add_read(messages):
        plan=json.loads(messages[1]['content'])['task_plan'];steps=definition(plan)
        steps.append(dict(id='read_source',kind='inspect',goal='重读来源',depends_on=['sources'],target=source,attempt_limit=1))
        return act('revise_plan',expected_plan_ref=plan['plan_ref'],reason='新增一次明确的重读步骤',steps=steps)
    provider=Scripted([act('inspect_source',source_ref=source,page=1,start=0,end=15),add_read,
        act('inspect_source',source_ref=source,page=1,start=0,end=15),act('ask_researcher',question='Confirm context?')])
    p=store.get(agent.run_agent(store,provider,[source],ExperimentSpec((0.,1.)),task_planning=True))['payload']
    assert views(p)['read_source']['attempts']==1
    assert views(p)['read_source']['status']=='complete'
    assert not any(e['type']=='plan_blocked' for e in p['events'])


def test_reusing_a_step_id_cannot_reset_its_target_binding(tmp_path):
    store,source=setup(tmp_path)
    config=task_plans.create(store,[source],10)
    ref=config['initial_plan_ref'];steps=deepcopy(task_plans.load(store,config,ref)['steps'])
    steps.append(dict(id='lookup',kind='investigate',goal='查证',depends_on=['sources'],target='k/value',attempt_limit=2))
    ref=task_plans.revise(store,config,ref,ref,steps,'add lookup',1)['plan_ref']
    changed=deepcopy(task_plans.load(store,config,ref)['steps'])
    next(s for s in changed if s['id']=='lookup')['target']='x/initial'
    with pytest.raises(ValueError,match='cannot change its kind or target'):
        task_plans.revise(store,config,ref,ref,changed,'try target reset',2)


def test_invalid_revised_parameter_does_not_reuse_old_model_validation(tmp_path):
    store,source=setup(tmp_path)
    p=store.get(agent.run_agent(store,Scripted([act('submit_draft',**proposal(source)),
        act('submit_draft',**proposal(source,value=.9)),act('run'),
        act('submit_draft',**proposal(source)),act('run'),act('finish')]),
        [source],ExperimentSpec((0.,1.)),task_planning=True))['payload']
    assert p['state']=='completed',p
    assert len(store.list('run'))==1
    assert views(p)['validate']['proof_call']==4
    assert any(e['type']=='plan_blocked' and e['tool']=='run' for e in p['events'])


def test_failed_source_span_counts_toward_planned_read_budget(tmp_path):
    store,source=setup(tmp_path)
    def add_read(messages):
        plan=json.loads(messages[1]['content'])['task_plan'];steps=definition(plan)
        steps.append(dict(id='read_source',kind='inspect',goal='读取来源',depends_on=['sources'],target=source,attempt_limit=1))
        return act('revise_plan',expected_plan_ref=plan['plan_ref'],reason='一次读取预算',steps=steps)
    p=store.get(agent.run_agent(store,Scripted([add_read,
        act('inspect_source',source_ref=source,page=1,start=0,end=1000),
        act('inspect_source',source_ref=source,page=1,start=0,end=15),
        act('ask_researcher',question='Need original page')]),[source],ExperimentSpec((0.,1.)),task_planning=True))['payload']
    assert views(p)['read_source']['attempts']==1
    assert views(p)['read_source']['status']=='budget_exhausted'
    real=[r for r in p['invocation_refs'] if store.get(r)['payload']['name']=='inspect_source' and store.get(r)['payload']['phase']=='reserved']
    assert len(real)==1


def test_shared_submission_respects_validation_step_budget(tmp_path):
    store,source=setup(tmp_path)
    def limit_validation(messages):
        plan=json.loads(messages[1]['content'])['task_plan'];steps=definition(plan)
        next(s for s in steps if s['id']=='validate')['attempt_limit']=1
        return act('revise_plan',expected_plan_ref=plan['plan_ref'],reason='限制验证尝试一次',steps=steps)
    p=store.get(agent.run_agent(store,Scripted([limit_validation,
        act('submit_draft',**proposal(source,value=.9)),act('submit_draft',**proposal(source)),
        act('ask_researcher',question='Review the error')]),[source],ExperimentSpec((0.,1.)),task_planning=True))['payload']
    assert views(p)['validate']['attempts']==1
    actual=[r for r in p['invocation_refs'] if store.get(r)['payload']['name']=='submit_draft' and store.get(r)['payload']['phase']=='reserved']
    assert len(actual)==1
