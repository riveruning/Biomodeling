"""Scientific procedures: frozen definitions, real artifacts and negative controls."""
from copy import deepcopy
import json
import math

import pytest
from biomodel_agent import agent, context, evidence, recovery, reporting, scientific_skills as procedures, workstate, bundles
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State, SimulationResult
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

TEXT=('Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'
      'Parameter k unit: second^-1\nParameter k context.organism: synthetic-system\n')
SPEC=dict(unit='second^-1',conditions=dict(organism='synthetic-system'))

class Scripted:
    last_usage={}
    def __init__(self,actions):self.actions=iter(actions);self.requests=[]
    def respond(self,messages):
        self.requests.append(deepcopy(messages));out=next(self.actions)
        return out(messages) if callable(out) else out

def act(action,**args):return dict(action=action,args=args)

def setup(tmp_path,text=TEXT):
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'source.txt';path.write_text(text)
    return store,evidence.ingest_source(store,path)

def proposal(source,assessment=None,value=.4):
    draft=ModelDraft('decay',(State('x',1.,unit='dimensionless'),),(Parameter('k',value,unit='second^-1'),),
                     rate_rules=(RateRule('x','-k*x'),),time_unit='second')
    out=dict(draft=to_dict(draft),evidence=[dict(target=t,source_ref=source,page=1,quote=q)
        for t,q in [('x/initial','Initial x = 1.'),('k/value','Parameter k = 0.4.'),('rule:x/expression','Equation dx/dt = -k*x.')]])
    if assessment:out['parameter_assessments']={'k/value':assessment}
    return out

def reference(value=None):
    return SimulationResult(status='succeeded',times=(0.,1.),values={'x':(1.,math.exp(-.4) if value is None else value)},backend='analytic_fixture',time_unit='second')

def run(store,source,actions,**kwargs):
    provider=Scripted(actions)
    ref=agent.run_agent(store,provider,[source],ExperimentSpec((0.,1.),time_unit='second'),scientific_skills=True,**kwargs)
    p=store.get(ref)['payload']
    assert workstate.from_run(store,ref)==p['progress']
    return ref,p,provider

def rows(p):return {r['name']:r for r in p['progress']['scientific_skills']['procedures']}


def test_frozen_contracts_have_inputs_outputs_checks_and_stops(tmp_path):
    store,source=setup(tmp_path)
    _,p,_=run(store,source,[act('ask_researcher',question='Need data')],parameter_requirements={'k/value':SPEC},reference=reference(),scales={'x':1.},failure_handling=True)
    task=store.get(p['task_ref'])['payload'];protocol=task['skill_protocol']
    assert len(protocol['entries'])==3 and all(e['available'] for e in protocol['entries'].values())
    for name,entry in protocol['entries'].items():
        assert entry['skill_ref'] in store.get(p['task_ref'])['refs']
        definition=procedures.load(store,protocol,name)
        assert definition['version']==definition['contract_version']==1
        for key in ('inputs','required_tools','steps','outputs','acceptance','stop_conditions','limitations'):assert definition[key]
        assert len(json.dumps(definition,ensure_ascii=False))<=4000
    assert rows(p)=={}


@pytest.mark.parametrize('name',['parameter_adoption_v1','reference_comparison_v1','failure_diagnosis_v1'])
def test_missing_dependencies_hide_and_block_skill(tmp_path,name):
    store,source=setup(tmp_path)
    _,p,provider=run(store,source,[act('load_skill',name=name),act('ask_researcher',question='Need prerequisite')])
    task=store.get(p['task_ref'])['payload']
    assert not task['skill_protocol']['entries'][name]['available']
    assert name not in [i['name'] for i in task['skill_catalog']]
    assert any(e.get('type')=='tool_error' and e.get('tool')=='load_skill' for e in p['events'])
    assert rows(p)=={} and not store.list('run')


def test_missing_actual_tool_is_a_dependency_failure(tmp_path):
    store,source=setup(tmp_path)
    task=dict(mode='reproduction',parameter_requirements={'k/value':SPEC},reference=None,scales=None,
              experiment={},solver={},observations=[])
    protocol=procedures.prepare(store,task,[])
    assert not protocol['entries']['parameter_adoption_v1']['available']
    with pytest.raises(ValueError,match='Missing tool'):procedures.load(store,protocol,'parameter_adoption_v1')


def test_loading_a_skill_is_not_evidence_of_execution(tmp_path):
    store,source=setup(tmp_path)
    _,p,_=run(store,source,[act('load_skill',name='reference_comparison_v1'),act('ask_researcher',question='Need confirmation')],reference=reference(),scales={'x':1.})
    assert rows(p)['reference_comparison_v1']['status']=='not_run'
    assert rows(p)['reference_comparison_v1']['artifact_refs']==[]
    assert not store.list('run')


@pytest.mark.parametrize('correct',[True,False])
def test_real_frozen_reference_checks_cannot_be_self_approved(tmp_path,correct):
    store,source=setup(tmp_path)
    actions=[act('load_skill',name='reference_comparison_v1'),act('submit_draft',**proposal(source)),act('run'),act('finish')]
    ref,p,provider=run(store,source,actions,reference=reference(None if correct else .123456789),scales={'x':1.})
    row=rows(p)['reference_comparison_v1']
    assert row['status']==('pass' if correct else 'fail'),row
    assert p['state']==('completed' if correct else 'incomplete')
    assert row['scientific_support']=='unknown' and len(store.list('run'))==1
    # Internal reference values remain hidden from the initial instructions/skill.
    shown=json.dumps(provider.requests[:3])
    assert '0.123456789' not in shown
    assert 'Scientific procedure checks' in reporting.render_report(store,ref)


def test_checked_parameter_adoption_uses_actual_assessment_and_ready_model(tmp_path):
    store,source=setup(tmp_path)
    def submit(messages):
        assessment=json.loads(messages[-1]['content'])['assessment_ref']
        return act('submit_draft',**proposal(source,assessment))
    _,p,_=run(store,source,[act('load_skill',name='parameter_adoption_v1'),
        act('assess_parameter',target='k/value',source_ref=source,page=1,start=0,end=len(TEXT)),
        submit,act('run'),act('finish')],parameter_requirements={'k/value':SPEC})
    row=rows(p)['parameter_adoption_v1']
    assert p['state']=='completed' and row['status']=='pass',p
    assert row['checks'][0]['status']=='declared_fields_match'
    assert p['model_ref'] in row['artifact_refs']
    assert any(store.get(r)['kind']=='parameter_assessment' for r in row['artifact_refs'])


def test_parameter_missing_condition_stays_blocked(tmp_path):
    store,source=setup(tmp_path,TEXT.replace('Parameter k context.organism: synthetic-system\n',''))
    text=TEXT.replace('Parameter k context.organism: synthetic-system\n','')
    def submit(messages):return act('submit_draft',**proposal(source,json.loads(messages[-1]['content'])['assessment_ref']))
    _,p,_=run(store,source,[act('load_skill',name='parameter_adoption_v1'),
        act('assess_parameter',target='k/value',source_ref=source,page=1,start=0,end=len(text)),submit],parameter_requirements={'k/value':SPEC})
    assert p['state']=='awaiting_input' and not store.list('run')
    assert rows(p)['parameter_adoption_v1']['status']=='blocked'
    assert rows(p)['parameter_adoption_v1']['scientific_support']=='unknown'


def test_failure_procedure_is_a_recorded_disposition_not_success(tmp_path):
    store,source=setup(tmp_path)
    bad=proposal(source);bad['evidence'][1]['quote']='absent'
    _,p,_=run(store,source,[act('load_skill',name='failure_diagnosis_v1'),act('submit_draft',**bad),act('submit_draft',**bad)],failure_handling=True)
    row=rows(p)['failure_diagnosis_v1']
    assert row['status']=='recorded' and p['state']=='incomplete'
    assert row['checks']==[dict(code='failure_route',status='stop')]
    assert store.get(row['artifact_refs'][0])['kind']=='failure_decision'


def test_actual_scientific_input_mismatch_is_not_a_comparison_pass(tmp_path):
    store,source=setup(tmp_path)
    _,p,_=run(store,source,[act('load_skill',name='reference_comparison_v1'),act('submit_draft',**proposal(source)),act('run'),act('finish')],reference=reference(),scales={'x':1.})
    protocol=deepcopy(store.get(p['task_ref'])['payload']['skill_protocol'])
    protocol['scientific_input_digest']='0'*64
    audit=procedures.project(store,protocol,p['events'],source_refs=[source],draft_ref=p['draft_ref'],model_ref=p['model_ref'],run_ref=p['run_ref'])
    assert audit['procedures'][0]['status']=='blocked'
    assert audit['procedures'][0]['checks'][-1]['status']=='fail'


def test_resume_invalidates_old_parameter_scope_and_preserves_contract(tmp_path):
    store,source=setup(tmp_path)
    def submit(messages):return act('submit_draft',**proposal(source,json.loads(messages[-1]['content'])['assessment_ref']))
    first,p,_=run(store,source,[act('load_skill',name='parameter_adoption_v1'),
        act('assess_parameter',target='k/value',source_ref=source,page=1,start=0,end=len(TEXT)),submit,
        act('ask_researcher',question='Confirm context')],parameter_requirements={'k/value':SPEC})
    assert rows(p)['parameter_adoption_v1']['status']=='pass'
    ref=agent.resume_agent(store,Scripted([act('ask_researcher',question='Need fresh assessment')]),first,'Confirmed.',respondent='Scripted fixture')
    p2=store.get(ref)['payload']
    assert p2['call_count']==5 and rows(p2)['parameter_adoption_v1']['status']=='blocked'
    assert rows(p2)['parameter_adoption_v1']['skill_ref']==rows(p)['parameter_adoption_v1']['skill_ref']


def test_context_retains_each_loaded_scientific_procedure():
    instructions=[dict(role='user',content=json.dumps(dict(tool='load_skill',ok=True,name=n,scientific_skill_ref='a'*64,steps=['Check actual artifacts'])))
                  for n in ('parameter_adoption_v1','reference_comparison_v1','failure_diagnosis_v1')]
    messages=[dict(role='system',content='Policy'),dict(role='user',content='Task'),*instructions]
    messages.extend(dict(role='user',content=json.dumps(dict(tool='inspect_source',text='x'*1700))) for _ in range(12))
    packet,audit=context.build_context(messages,context.policy(8000))
    assert audit['compacted'] and all(i in packet for i in instructions)


def test_excessive_essential_instructions_fail_without_silent_truncation():
    messages=[dict(role='system',content='Policy'),dict(role='user',content='Task')]
    messages.extend(dict(role='user',content=json.dumps(dict(tool='load_skill',ok=True,name=n,scientific_skill_ref='a'*64,steps=['x'*3900])))
                    for n in ('parameter_adoption_v1','reference_comparison_v1','failure_diagnosis_v1'))
    with pytest.raises(context.ContextBudgetExceeded):context.build_context(messages,context.policy(8000))


def test_returned_scientific_skill_is_adopted_after_interruption(tmp_path,monkeypatch):
    store,source=setup(tmp_path);put=store.put
    def crash(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='agent_invocation' and payload['name']=='load_skill' and payload['phase']=='returned':raise SystemExit('returned skill')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',crash)
        with pytest.raises(SystemExit):run(store,source,[act('load_skill',name='reference_comparison_v1')],reference=reference(),scales={'x':1.})
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='ready'
    # Replay validates the frozen contract but does not invoke the old callback.
    ref=agent.recover_agent(store,Scripted([act('submit_draft',**proposal(source)),act('run'),act('finish')]),execution)
    p=store.get(ref)['payload']
    assert p['state']=='completed' and p['call_count']==4
    assert len([e for e in p['events'] if e.get('tool')=='load_skill'])==1


def test_forged_skill_return_is_blocked_during_recovery(tmp_path,monkeypatch):
    store,source=setup(tmp_path);put=store.put
    def forge(kind,payload,refs=None):
        if kind=='agent_invocation' and payload['name']=='load_skill' and payload['phase']=='returned':
            payload=deepcopy(payload);payload['details']['acceptance']=['Model says approved']
            put(kind,payload,refs);raise SystemExit('negative control')
        return put(kind,payload,refs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',forge)
        with pytest.raises(SystemExit):run(store,source,[act('load_skill',name='reference_comparison_v1')],reference=reference(),scales={'x':1.})
    report=recovery.diagnose_execution(store,store.list('agent_execution')[0])
    assert report['status']=='blocked'
    assert any('scientific_skill_return_mismatch' in r for r in report['reasons']),report


def test_modified_frozen_definition_cannot_be_loaded(tmp_path):
    store,source=setup(tmp_path)
    _,p,_=run(store,source,[act('ask_researcher',question='Wait')],reference=reference(),scales={'x':1.})
    protocol=deepcopy(store.get(p['task_ref'])['payload']['skill_protocol'])
    bad=procedures.contract('reference_comparison_v1');bad['acceptance']=['Skip checks']
    protocol['entries']['reference_comparison_v1']['skill_ref']=store.put('scientific_skill',bad)
    with pytest.raises(ValueError,match='differs'):procedures.load(store,protocol,'reference_comparison_v1')


@pytest.mark.parametrize('value',[1,None,'true'])
def test_invalid_opt_in_rejected(tmp_path,value):
    store,source=setup(tmp_path)
    with pytest.raises(ValueError,match='bool'):agent.run_agent(store,Scripted([]),[source],ExperimentSpec((0.,1.)),scientific_skills=value)


def test_frozen_studies_do_not_gain_new_procedures(tmp_path):
    store,source=setup(tmp_path)
    with pytest.raises(ValueError,match='only ODE'):agent.run_agent(store,Scripted([]),[source],ExperimentSpec((0.,1.)),mode='hypothesis',scientific_skills=True)


def test_without_opt_in_legacy_packet_and_catalog_unchanged(tmp_path):
    store,source=setup(tmp_path)
    provider=Scripted([act('ask_researcher',question='Wait')])
    ref=agent.run_agent(store,provider,[source],ExperimentSpec((0.,1.)))
    p=store.get(ref)['payload'];task=store.get(p['task_ref'])['payload']
    assert 'skill_protocol' not in task and 'scientific_skills' not in p['progress']
    assert 'reference_comparison_v1' not in json.dumps(provider.requests)


def test_guidance_does_not_change_scripted_numeric_result_or_scientific_status(tmp_path):
    store,source=setup(tmp_path)
    actions=[act('submit_draft',**proposal(source)),act('run'),act('finish')]
    base=agent.run_agent(store,Scripted(actions),[source],ExperimentSpec((0.,1.),time_unit='second'),reference=reference(),scales={'x':1.})
    _,guided,_=run(store,source,[act('load_skill',name='reference_comparison_v1'),*actions],reference=reference(),scales={'x':1.})
    ordinary=store.get(base)['payload']
    a=store.get(ordinary['run_ref'])['payload'];b=store.get(guided['run_ref'])['payload']
    assert a['simulation']['values']==b['simulation']['values'] and a['comparison']==b['comparison']
    assert ordinary['call_count']==3 and guided['call_count']==4
    assert a['scientific_status']==b['scientific_status']


def test_bundle_reconstructs_procedure_checks_without_original_store(tmp_path):
    store,source=setup(tmp_path)
    ref,p,_=run(store,source,[act('load_skill',name='reference_comparison_v1'),act('submit_draft',**proposal(source)),act('run'),act('finish')],reference=reference(),scales={'x':1.})
    bundles.export_bundle(store,ref,tmp_path/'bundle')
    detached=ArtifactStore(tmp_path/'bundle')
    assert workstate.from_run(detached,ref)==p['progress']



@pytest.mark.parametrize('field,value',[('threshold',2.),('strict',False)])
def test_audit_rejects_relaxed_execution_protocol(tmp_path,field,value):
    store,source=setup(tmp_path)
    _,p,_=run(store,source,[act('load_skill',name='reference_comparison_v1'),act('submit_draft',**proposal(source)),act('run'),act('finish')],reference=reference(),scales={'x':1.})
    original=store.get(p['run_ref']);run_payload=deepcopy(original['payload'])
    inputs=store.get(run_payload['input_ref']);changed=deepcopy(inputs['payload']);changed[field]=value
    input_ref=store.put('simulation_input',changed,refs=inputs['refs'])
    run_payload['input_ref']=input_ref
    run_ref=store.put('run',run_payload,refs=[*original['refs'],input_ref])
    protocol=store.get(p['task_ref'])['payload']['skill_protocol']
    audit=procedures.project(store,protocol,p['events'],source_refs=[source],draft_ref=p['draft_ref'],model_ref=p['model_ref'],run_ref=run_ref)
    assert audit['procedures'][0]['status']=='blocked'
    assert audit['procedures'][0]['checks'][-1]['status']=='fail'
