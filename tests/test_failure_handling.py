"""Failure routing acceptance: scripted decisions, real ODE tools, negative controls."""
from copy import deepcopy
import json
import math

import pytest
from biomodel_agent import agent, evidence, failure_handling as routes, recovery, reporting, task_plans, workstate
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State, SimulationResult
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}
    def __init__(self, actions): self.actions=iter(actions); self.requests=[]
    def respond(self, messages):
        self.requests.append(deepcopy(messages))
        out=next(self.actions)
        if isinstance(out, BaseException): raise out
        return out(messages) if callable(out) else out


def act(action, **args): return dict(action=action, args=args)


def setup(tmp_path, text='Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'):
    store=ArtifactStore(tmp_path/'store')
    path=tmp_path/'source.txt'; path.write_text(text)
    return store, evidence.ingest_source(store,path)


def proposal(source, value=.4):
    return dict(draft=to_dict(ModelDraft('decay',(State('x',1.),),(Parameter('k',value),),
        rate_rules=(RateRule('x','-k*x'),))),evidence=[dict(target=t,source_ref=source,page=1,quote=q)
        for t,q in [('x/initial','Initial x = 1.'),('k/value','Parameter k = 0.4.'),
                    ('rule:x/expression','Equation dx/dt = -k*x.')]])


def run(store, source, actions, **kwargs):
    ref=agent.run_agent(store,Scripted(actions),[source],ExperimentSpec((0.,1.)),failure_handling=True,**kwargs)
    p=store.get(ref)['payload']
    assert workstate.from_run(store,ref)==p['progress']
    return ref,p


def decisions(p): return p['progress']['failure_handling']['decisions']


def test_citation_repair_then_real_run(tmp_path):
    store,source=setup(tmp_path)
    bad=proposal(source); bad['evidence'][1]['quote']='Not in source.'
    ref,p=run(store,source,[act('submit_draft',**bad),act('submit_draft',**proposal(source)),act('run'),act('finish')])
    assert p['state']=='completed' and p['call_count']==4 and p['repair_count']==1
    assert decisions(p)[0]['route']=='repair'
    result=store.get(p['run_ref'])['payload']
    assert result['simulation']['values']['x'][-1]==pytest.approx(math.exp(-.4),abs=1e-6)
    assert 'Bounded failure decisions' in reporting.render_report(store,ref)
    assert decisions(p)[0]['scientific_support']=='unknown'


def test_missing_parameter_searches_locally_then_requires_cited_repair(tmp_path):
    store,source=setup(tmp_path)
    missing=proposal(source,None)
    _,p=run(store,source,[act('submit_draft',**missing),act('submit_draft',**proposal(source)),act('run'),act('finish')])
    assert p['state']=='completed',p
    assert decisions(p)[0]['route']=='retrieve'
    investigation=store.get(store.list('parameter_investigation')[0])['payload']
    assert investigation['source_refs']==[source] and investigation['selected_value'] is None
    assert p['progress']['failure_handling']['auto_investigations']==1
    assert len(store.list('run'))==1


@pytest.mark.parametrize('text', ['Initial x = 1.\nParameter k is not reported.\nEquation dx/dt = -k*x.\n',
                                 'Initial x = 1.\nEquation dx/dt = -k*x.\n'])
def test_unresolved_missing_keeps_null_and_asks(tmp_path,text):
    store,source=setup(tmp_path,text)
    missing=proposal(source,None); missing['evidence']=[e for e in missing['evidence'] if e['target']!='k/value']
    actions=[act('submit_draft',**missing)]
    # A symbol in an equation is a lead, not a numeric declaration.
    if 'not reported' not in text:
        actions.append(act('ask_researcher',question='Supply k/value and its units.'))
    _,p=run(store,source,actions)
    assert p['state']=='awaiting_input' and p['call_count']==len(actions),p
    assert p['progress']['parameters'][0]['value'] is None
    assert not store.list('run') and not store.list('model')
    assert p['events'][-1]['origin']==('failure_policy' if 'not reported' in text else 'model')
    assert 'k/value' in p['events'][-1]['question']


@pytest.mark.parametrize('extra',['Parameter k = 0.9.\n',
    'The table labels k per second, but the legend labels k per minute.\n'])
def test_conflict_and_units_ask_without_repair_or_lookup(tmp_path,extra):
    store,source=setup(tmp_path,'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'+extra)
    _,p=run(store,source,[act('submit_draft',**proposal(source))])
    assert p['state']=='awaiting_input' and decisions(p)[0]['route']=='ask',p
    assert not store.list('run') and not store.list('parameter_investigation')
    assert p['events'][-1]['artifact_refs']


def test_identical_failed_request_stops_before_second_tool(tmp_path):
    store,source=setup(tmp_path)
    bad=proposal(source); bad['evidence'][1]['quote']='wrong quote'
    _,p=run(store,source,[act('submit_draft',**bad),act('submit_draft',**bad)])
    assert p['state']=='incomplete' and p['call_count']==2
    assert len([e for e in p['events'] if e.get('tool')=='submit_draft' and e['type']=='tool_result'])==1
    assert decisions(p)[-1]['route']=='stop'
    assert len([e for e in p['events'] if e.get('failure_code')=='duplicate_failed_request'])==1


def test_three_rounds_changing_quote_does_not_reset_failure_window(tmp_path):
    store,source=setup(tmp_path)
    actions=[]
    for i in range(3):
        bad=proposal(source);bad['evidence'][1]['quote']='wrong quote '+str(i)
        actions.append(act('submit_draft',**bad))
    _,p=run(store,source,actions,max_repairs=5)
    assert p['state']=='incomplete' and p['call_count']==3
    assert [d['route'] for d in decisions(p)]==['repair','repair','stop']
    assert 'Blocking issue keys' in decisions(p)[-1]['reason']


def test_zero_repairs_is_respected(tmp_path):
    store,source=setup(tmp_path)
    bad=proposal(source);bad['evidence'][1]['quote']='wrong quote'
    _,p=run(store,source,[act('submit_draft',**bad)],max_repairs=0)
    assert p['state']=='incomplete' and p['repair_count']==1
    assert 'repair budget' in decisions(p)[0]['reason']


def test_missing_on_last_call_asks_without_unusable_lookup(tmp_path):
    store,source=setup(tmp_path)
    _,p=run(store,source,[act('submit_draft',**proposal(source,None))],max_calls=1)
    assert p['state']=='awaiting_input' and not store.list('parameter_investigation')
    assert decisions(p)[0]['route']=='ask'


def test_unknown_tool_error_is_not_retried(tmp_path):
    store,source=setup(tmp_path)
    _,p=run(store,source,[act('investigate_parameter',target='bad',query='k')])
    assert p['state']=='incomplete' and p['call_count']==1
    assert decisions(p)[0]['route']=='stop' and decisions(p)[0]['categories']==['unknown']


def test_failed_frozen_reference_is_diagnosed_and_never_rerun(tmp_path):
    store,source=setup(tmp_path)
    reference=SimulationResult(status='succeeded',times=(0.,1.),values={'x':(1.,.1)},backend='deliberately_wrong_fixture')
    _,p=run(store,source,[act('submit_draft',**proposal(source)),act('run')],reference=reference,scales={'x':1.})
    assert p['state']=='incomplete' and p['call_count']==2 and len(store.list('run'))==1
    d=decisions(p)[0]
    assert d['route']=='diagnose_stop'
    saved=routes.load(store,d['decision_ref'],routes.POLICY)
    assert saved['descriptor']['event']['comparison']=='fail'
    assert p['run_ref'] in store.get(d['decision_ref'])['refs']
    assert store.get(p['task_ref'])['payload']['reference']['values']['x']==[1.,.1]


def test_with_task_plan_auto_lookup_counts_as_actual_attempt(tmp_path):
    store,source=setup(tmp_path)
    def add_plan(messages):
        p=json.loads(messages[1]['content'])['task_plan']
        steps=[{k:s[k] for k in ('id','kind','goal','depends_on','target','attempt_limit')} for s in p['steps']]
        steps.append(dict(id='lookup',kind='investigate',goal='Find k',depends_on=['sources'],target='k/value',attempt_limit=1))
        return act('revise_plan',expected_plan_ref=p['plan_ref'],reason='Check parameter',steps=steps)
    _,p=run(store,source,[add_plan,act('submit_draft',**proposal(source,None)),act('submit_draft',**proposal(source)),act('run'),act('finish')],task_planning=True)
    assert p['state']=='completed',p
    step=next(s for s in p['progress']['task_plan']['steps'] if s['id']=='lookup')
    assert step['attempts']==1 and step['remaining_attempts']==0 and step['status']=='complete'


def test_plan_dependencies_block_auto_lookup(tmp_path):
    store,source=setup(tmp_path)
    def add_plan(messages):
        p=json.loads(messages[1]['content'])['task_plan']
        steps=[{k:s[k] for k in ('id','kind','goal','depends_on','target','attempt_limit')} for s in p['steps']]
        steps.append(dict(id='lookup',kind='investigate',goal='Find k after validate',depends_on=['validate'],target='k/value',attempt_limit=1))
        return act('revise_plan',expected_plan_ref=p['plan_ref'],reason='Check after ready',steps=steps)
    _,p=run(store,source,[add_plan,act('submit_draft',**proposal(source,None))],task_planning=True)
    assert p['state']=='awaiting_input' and not store.list('parameter_investigation'),p
    assert 'plan dependencies' in decisions(p)[0]['reason']


def test_researcher_resume_inherits_calls_and_lookup_budget(tmp_path):
    store,source=setup(tmp_path,'Initial x = 1.\nParameter k is not reported.\nEquation dx/dt = -k*x.\n')
    missing=proposal(source,None);missing['evidence']=[e for e in missing['evidence'] if e['target']!='k/value']
    first,p=run(store,source,[act('submit_draft',**missing)])
    parent=deepcopy(store.get(first))
    def submit(messages):
        data=[json.loads(m['content']) for m in messages if m['role']=='user' and m['content'].startswith('{')]
        reply=next(m['source_ref'] for m in data if m.get('tool')=='researcher_reply')
        good=proposal(source);good['evidence'][1]['source_ref']=reply
        return act('submit_draft',**good)
    ref=agent.resume_agent(store,Scripted([submit,act('run'),act('finish')]),first,'Parameter k = 0.4.',respondent='Scripted researcher fixture')
    resumed=store.get(ref)['payload']
    assert resumed['state']=='completed' and resumed['call_count']==4,resumed
    assert resumed['progress']['failure_handling']['auto_investigations']==1
    assert store.get(first)==parent


def test_returned_decision_is_adopted_after_crash(tmp_path,monkeypatch):
    store,source=setup(tmp_path)
    bad=proposal(source);bad['evidence'][1]['quote']='wrong'
    put=store.put
    def crash(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='agent_invocation' and payload['name']=='route_failure' and payload['phase']=='returned':
            raise SystemExit('persisted return')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',crash)
        with pytest.raises(SystemExit):run(store,source,[act('submit_draft',**bad)])
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='ready'
    monkeypatch.setattr(routes,'record',lambda *a,**k:pytest.fail('decision was repeated'))
    ref=agent.recover_agent(store,Scripted([act('submit_draft',**proposal(source)),act('run'),act('finish')]),execution)
    p=store.get(ref)['payload']
    assert p['state']=='completed' and len(store.list('failure_decision'))==1,p


def test_unknown_decision_write_blocks_recovery(tmp_path,monkeypatch):
    store,source=setup(tmp_path)
    bad=proposal(source);bad['evidence'][1]['quote']='wrong'
    put=store.put
    def crash(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='failure_decision': raise SystemExit('no returned receipt')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',crash)
        with pytest.raises(SystemExit):run(store,source,[act('submit_draft',**bad)])
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='blocked'
    with pytest.raises(recovery.RecoveryBlocked):agent.recover_agent(store,Scripted([]),execution)


@pytest.mark.parametrize('option',[1,'yes',None])
def test_invalid_opt_in_rejected(tmp_path,option):
    store,source=setup(tmp_path)
    with pytest.raises(ValueError,match='bool'):
        agent.run_agent(store,Scripted([]),[source],ExperimentSpec((0.,1.)),failure_handling=option)


def test_frozen_hypothesis_cannot_enable_policy(tmp_path):
    store,source=setup(tmp_path)
    with pytest.raises(ValueError,match='ODE reproduction'):
        agent.run_agent(store,Scripted([]),[source],ExperimentSpec((0.,1.)),mode='hypothesis',failure_handling=True)


def test_per_target_lookup_limit_does_not_allow_second_automatic_lookup(tmp_path):
    store,source=setup(tmp_path)
    second=proposal(source,None);second['draft']['id']='decay_v2'
    _,p=run(store,source,[act('submit_draft',**proposal(source,None)),act('submit_draft',**second)],max_repairs=5)
    assert p['state']=='awaiting_input',p
    assert [d['route'] for d in decisions(p)]==['retrieve','ask']
    assert len(store.list('parameter_investigation'))==1


def test_lookup_error_is_recorded_and_not_retried(tmp_path,monkeypatch):
    from biomodel_agent import parameter_investigation
    store,source=setup(tmp_path)
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1);raise ValueError('bounded retrieval unavailable')
    monkeypatch.setattr(parameter_investigation,'investigate',fail)
    _,p=run(store,source,[act('submit_draft',**proposal(source,None))])
    assert p['state']=='awaiting_input' and len(calls)==1
    assert p['progress']['failure_handling']['auto_investigations']==1
    assert not store.list('run')


@pytest.mark.parametrize('attempts,targets,expected',[(4,[], 'ask'),(3,['k/value'],'ask'),(3,[],'retrieve')])
def test_global_and_per_target_caps(attempts,targets,expected):
    issue=dict(category='missing_information',code='missing',target='k/value',blocking=True)
    d=dict(issues=[issue],event={},repairs=0,max_repairs=2,stagnant_failures=1,
           remaining_calls=2,lookup_block=None,auto_investigations=attempts,investigated_targets=targets)
    assert routes.decide(routes.POLICY,d)['route']==expected


def test_span_repair_is_allowed_but_only_within_failure_budget(tmp_path):
    store,source=setup(tmp_path)
    _,p=run(store,source,[act('inspect_source',source_ref=source,page=1,start=999,end=1000),
        act('inspect_source',source_ref=source,page=1,start=0,end=14),
        act('ask_researcher',question='Please confirm source.')])
    assert p['state']=='awaiting_input' and decisions(p)[0]['route']=='repair'
    assert len([e for e in p['events'] if e.get('tool')=='inspect_source' and e['type']=='tool_result'])==1


@pytest.mark.parametrize('retryable,max_retries,expected_calls',[(False,2,1),(True,0,1),(True,1,2)])
def test_existing_provider_retry_policy_remains_authoritative(tmp_path,monkeypatch,retryable,max_retries,expected_calls):
    from biomodel_agent.llm import ProviderError
    store,source=setup(tmp_path)
    monkeypatch.setattr(agent.time,'sleep',lambda _:None)
    actions=[ProviderError('synthetic HTTP failure',kind='http',retryable=retryable)]
    if expected_calls==2:actions.append(act('ask_researcher',question='Need input'))
    _,p=run(store,source,actions,max_retries=max_retries)
    assert p['call_count']==expected_calls and p['retry_count']==expected_calls-1
    assert not store.list('run')


def test_run_exception_preserves_unknown_result_without_retry(tmp_path,monkeypatch):
    store,source=setup(tmp_path)
    def fail(*args,**kwargs):raise RuntimeError('synthetic tool exception')
    monkeypatch.setattr(agent.application,'run_reproduction',fail)
    _,p=run(store,source,[act('submit_draft',**proposal(source)),act('run')])
    assert p['state']=='incomplete' and p['call_count']==2
    assert p['run_ref'] is None and decisions(p)[0]['route']=='diagnose_stop'
    assert 'without a confirmed returned result' in decisions(p)[0]['reason']


def test_returned_lookup_adopted_without_running_it_again(tmp_path,monkeypatch):
    from biomodel_agent import parameter_investigation
    store,source=setup(tmp_path)
    put=store.put
    def crash(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='agent_invocation' and payload['name']=='investigate_parameter' and payload['phase']=='returned':
            raise SystemExit('lookup return persisted')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',crash)
        with pytest.raises(SystemExit):run(store,source,[act('submit_draft',**proposal(source,None))])
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='ready'
    monkeypatch.setattr(parameter_investigation,'investigate',lambda *a,**k:pytest.fail('lookup repeated'))
    ref=agent.recover_agent(store,Scripted([act('submit_draft',**proposal(source)),act('run'),act('finish')]),execution)
    p=store.get(ref)['payload']
    assert p['state']=='completed' and p['progress']['failure_handling']['auto_investigations']==1


def test_default_run_keeps_legacy_packet_and_has_no_policy(tmp_path):
    store,source=setup(tmp_path)
    provider=Scripted([act('ask_researcher',question='Need input')])
    ref=agent.run_agent(store,provider,[source],ExperimentSpec((0.,1.)))
    p=store.get(ref)['payload']
    assert 'failure_policy' not in store.get(p['task_ref'])['payload']
    assert 'failure_handling' not in p['progress']
    assert 'failure_handling' not in json.loads(provider.requests[0][1]['content'])


def test_latest_failure_route_is_retained_in_compacted_context():
    from biomodel_agent import context
    latest=dict(role='user',content=json.dumps(dict(tool='route_failure',route='ask',reason='k is unknown')))
    messages=[dict(role='system',content='Instructions'),dict(role='user',content='Task'),latest]
    messages.extend(dict(role='user',content=json.dumps(dict(tool='inspect_source',text='x'*1800))) for _ in range(12))
    packet,audit=context.build_context(messages,context.policy(8000))
    assert audit['compacted'] and latest in packet


@pytest.mark.parametrize('tool',['route_failure','investigate_parameter'])
def test_recovery_rejects_mismatched_return_snapshot(tmp_path,monkeypatch,tool):
    store,source=setup(tmp_path)
    put=store.put
    def forge(kind,payload,refs=None):
        if kind=='agent_invocation' and payload['name']==tool and payload['phase']=='returned':
            payload=deepcopy(payload)
            payload['details']['reason' if tool=='route_failure' else 'query']='forged returned value'
            put(kind,payload,refs)
            raise SystemExit('negative control forged receipt')
        return put(kind,payload,refs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',forge)
        with pytest.raises(SystemExit):run(store,source,[act('submit_draft',**proposal(source,None))])
    execution=store.list('agent_execution')[0]
    report=recovery.diagnose_execution(store,execution)
    assert report['status']=='blocked'
    assert any('request_mismatch' in r for r in report['reasons']),report


def test_reply_does_not_refund_per_target_lookup_attempt(tmp_path):
    store,source=setup(tmp_path,'Initial x = 1.\nParameter k is not reported.\nEquation dx/dt = -k*x.\n')
    missing=proposal(source,None);missing['evidence']=[e for e in missing['evidence'] if e['target']!='k/value']
    first,p=run(store,source,[act('submit_draft',**missing)])
    ref=agent.resume_agent(store,Scripted([act('submit_draft',**missing)]),first,
                           'I still do not know the value.',respondent='Scripted researcher fixture')
    p=store.get(ref)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==2
    assert p['progress']['failure_handling']['auto_investigations']==1
    assert decisions(p)[-1]['route']=='ask'


def test_no_local_search_can_read_an_unrelated_source(tmp_path):
    store,source=setup(tmp_path,'Initial x = 1.\nParameter k is not reported.\nEquation dx/dt = -k*x.\n')
    unrelated=tmp_path/'unrelated.txt';unrelated.write_text('Parameter k = 999.')
    other=evidence.ingest_source(store,unrelated)
    missing=proposal(source,None);missing['evidence']=[e for e in missing['evidence'] if e['target']!='k/value']
    _,p=run(store,source,[act('submit_draft',**missing)])
    saved=store.get(store.list('parameter_investigation')[0])
    assert saved['payload']['source_refs']==[source] and other not in saved['refs']
    assert all(hit['source_ref']==source for hit in saved['payload']['retrieval']['results'])
    assert p['state']=='awaiting_input' and not store.list('run')



def test_plan_blocked_run_is_allowed_after_dependencies_are_satisfied(tmp_path):
    store,source=setup(tmp_path)
    _,p=run(store,source,[act('run'),act('submit_draft',**proposal(source)),act('run'),act('finish')],task_planning=True)
    assert p['state']=='completed' and p['call_count']==4,p
    assert len(store.list('run'))==1
    assert [d['route'] for d in decisions(p)]==['repair']
    assert not any(e.get('failure_code')=='duplicate_failed_request' for e in p['events'])
