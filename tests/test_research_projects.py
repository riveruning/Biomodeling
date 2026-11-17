"""Agent-owned problem details, frozen iterations and honest research outcomes."""
from copy import deepcopy
import hashlib
import json
import math
import time
from pathlib import Path

import httpx
import pytest

from biomodel_agent import bundles,cli,evidence,modeling_plan,recovery,reporting,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore,canonical_bytes
from test_fba import Scripted,act
from test_modeling_plan import setup as ode_setup

ROOT=Path(__file__).parents[1]


def frame(mode='application_modeling'):
    return dict(objective='Understand feedback in a hypothetical treatment scenario.',mode=mode,
        granularity='Two binary activities; this is a deliberately coarse development hypothesis.',
        modeling_targets=['Activities A and B'],rationale='Test a minimal explicit feedback hypothesis before adding detail.',
        evidence_needs=[dict(id='N1',question='What observations constrain this abstraction?',kind='data',queries=['feedback treatment observations'])],
        assumptions=[dict(id='H1',statement='Use the two-node synthetic feedback template.',
            rationale='A testable modeling choice without biological validation.',evidence_ids=[],decision='adopt')],
        criteria=[dict(id='C1',kind='native_checks',description='The frozen native checks must pass.',required=True)],
        evidence=[],unresolved=['Biological applicability has not been verified.'])


def setup(tmp_path,**policy):
    store=ArtifactStore(tmp_path/'store')
    project=p.create(store,dict(scenario='Study feedback under a treatment scenario.',policy=dict(
        **{**dict(planner_max_calls=20,max_native_calls=18),**policy})))
    return store,project


def step(store,project,name,**args):return p.operate(store,project,name,args,actor='operator')


def framed(tmp_path,**policy):
    store,project=setup(tmp_path,**policy)
    ref=step(store,project,'revise_frame',frame=frame(),reason='Choose the research target and granularity.')['frame_ref']
    return store,project,ref


def opened(tmp_path,kind='boolean',native_agent=True,**policy):
    store,project,ref=framed(tmp_path,**policy)
    path=ROOT/('examples/fba-v1/chain.json' if kind=='fba' else f'examples/model-types/{kind}.json')
    raw=json.loads(path.read_text());settings={} if kind=='fba' else json.loads((ROOT/f'examples/model-types/{kind}-settings.json').read_text())
    iteration=step(store,project,'open_iteration',reason='Freeze an explicit hypothesis for development.',
        config=dict(model_type=kind,execution=dict(settings=settings)),native_draft=raw,hypothesis_ids=['H1'],native_agent=native_agent)['iteration_ref']
    return store,project,iteration


def assess(store,project,iteration,interpretation='consistent'):
    return step(store,project,'assess_iteration',iteration_ref=iteration,interpretation=interpretation,
                rationale='Interpret the stored checks within the frozen scope; biological support remains unknown.',evidence_ids=[])['assessment_ref']


def test_human_only_supplies_scenario_agent_owns_details_and_assumptions(tmp_path):
    store,project=setup(tmp_path)
    assert p.inspect(store,project)['frame_ref'] is None
    f=step(store,project,'revise_frame',frame=frame(),reason='Agent proposes the scientific details.')['frame_ref']
    loaded=p.own(store,f,p.FRAME,project)
    assert loaded['spec']['granularity']==frame()['granularity']
    assert loaded['spec']['assumptions'][0]['decision']=='adopt'
    assert loaded['authority']=='agent_scientific_proposal'
    assert not store.list('research_alignment_reply') and not store.list('typed_run')


@pytest.mark.parametrize('kind',['boolean','stochastic','pde','individual','fba'])
def test_agent_hypothesis_reuses_real_backend_and_does_not_claim_source_proof(tmp_path,kind):
    store,project,it=opened(tmp_path,kind)
    result=p.operate(
        store,project,'run_iteration',dict(iteration_ref=it),provider=Scripted([act('run_fba' if kind=='fba' else 'run_typed'),act('finish')]))
    actual=p.facts(store,it)
    assert actual['numeric_status']=='pass' and actual['source_binding_status']=='unknown'
    model=store.get(p.own(store,it,p.ITERATION,project)['config']['model_ref'])['payload']
    assert model['origin']=='agent_project_hypothesis' and model['hypothesis_ids']==['H1']
    assert p.operate(store,project,'run_iteration',dict(iteration_ref=it),provider=None)['result_ref']==result['result_ref']
    ar=assess(store,project,it)
    step(store,project,'conclude',outcome='completed_with_limits',summary='Numerically consistent hypothetical model only.',
         assessment_refs=[ar],limitations=['No biological or independent validation.'])
    assert p.inspect(store,project)['state']=='completed_with_limits'


def test_two_model_versions_preserve_parent_results_and_total_budget(tmp_path):
    store,project,first=opened(tmp_path,max_iterations=2)
    p.operate(store,project,'run_iteration',dict(iteration_ref=first),provider=Scripted([act('run_typed'),act('finish')]))
    old_frame=p.inspect(store,project)['frame_ref'];new=frame();new['granularity']='Compare an explicitly changed update rule.'
    step(store,project,'revise_frame',frame=new,reason='The first result motivates a second hypothesis.')
    model=json.loads((ROOT/'examples/model-types/boolean.json').read_text());model['nodes'][0]['rule']='B'
    second=step(store,project,'open_iteration',reason='A new linked mathematical hypothesis.',parent_iteration_ref=first,
        config=dict(model_type='boolean',execution=dict(settings={'steps':4})),native_draft=model,hypothesis_ids=['H1'],native_agent=True)['iteration_ref']
    p.operate(store,project,'run_iteration',dict(iteration_ref=second),provider=Scripted([act('run_typed'),act('finish')]))
    view=p.inspect(store,project);assert len(view['iteration_refs'])==2 and view['budget']['native_reserved']==12
    assert p.facts(store,first)['numeric_status']=='pass'
    assert p.own(store,view['frame_ref'],p.FRAME,project)['post_result_revision']
    assert p.own(store,first,p.ITERATION,project)['frame_ref']==old_frame
    assert p.own(store,second,p.ITERATION,project)['parent_iteration_ref']==first
    with pytest.raises(ValueError,match='iteration budget'):step(store,project,'open_iteration',reason='No extra slots.',config=dict(model_type='boolean'))
    assert len(p.inspect(store,project)['iteration_refs'])==2


@pytest.mark.parametrize('field,value',[
    ('planner_max_calls',True),('planner_max_calls',51),('max_iterations',0),('max_iterations',9),
    ('max_native_calls',True),('native_calls_per_iteration',21),('max_literature_requests',21),
    ('context_max_chars',15999),('assumptions','silently_invent')])
def test_strict_project_policy_rejects_invalid_limits(tmp_path,field,value):
    store=ArtifactStore(tmp_path/'s')
    with pytest.raises(ValueError):p.create(store,dict(scenario='A scenario.',policy={field:value}))
    assert not store.list(p.PROJECT)


@pytest.mark.parametrize('mode,forbidden', [('strict_reproduction',False),('application_modeling',True)])
def test_hypotheses_cannot_enter_strict_or_forbidden_routes(tmp_path,mode,forbidden):
    store,project,_=framed(tmp_path,**({'assumptions':'forbidden'} if forbidden else {}))
    if mode=='strict_reproduction':step(store,project,'revise_frame',frame=frame(mode),reason='Require strict reproduction.')
    model=json.loads((ROOT/'examples/model-types/boolean.json').read_text())
    with pytest.raises(ValueError,match='hypotheses cannot'):
        step(store,project,'open_iteration',reason='Forbidden hypothesis.',native_draft=model,hypothesis_ids=['H1'],config=dict(model_type='boolean'))
    assert not store.list('typed_run')


def test_strict_source_bound_ssa_is_agent_adopted_and_rechecked(tmp_path):
    store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,ROOT/'examples/source-stochastic/birth-death.txt')
    project=p.create(store,dict(scenario='Reproduce explicit synthetic SSA declarations.',mode='reproduction',source_refs=[source]))
    spec=json.loads((ROOT/'examples/source-stochastic/proposal.template.json').read_text().replace('SOURCE_REF',source))
    f=frame('strict_reproduction');f['assumptions']=[];f['evidence']=spec['evidence']
    f['criteria'].append(dict(id='C2',kind='source_coverage',description='Source declarations must bind.',required=True))
    step(store,project,'revise_frame',frame=f,reason='Agent selects a source-verified model.')
    candidate=spec['candidates'][0]
    it=step(store,project,'open_iteration',reason='Freeze the literal source model.',native_draft=candidate['stochastic_draft'],
        source_evidence_map=candidate['evidence_map'],stochastic_rate_units=candidate['stochastic_rate_units'],
        config=dict(model_type='stochastic',execution=dict(settings=dict(duration=1.,samples=3,trajectories=4))))['iteration_ref']
    assert p.facts(store,it)['source_binding_status']=='pass'
    p.operate(store,project,'run_iteration',dict(iteration_ref=it),provider=Scripted([act('run_typed'),act('finish')]))
    assert p.own(store,assess(store,project,it),p.ASSESSMENT,project)['criteria_satisfied']


def test_original_ode_decision_plan_and_independent_reference_are_preserved(tmp_path):
    store,source,plan=ode_setup(tmp_path)
    ref=p.register_reference(store,dict(reference=dict(times=[0.,1.],values={'x':[1.,math.exp(-.4)]},
        time_unit='second',backend='closed_form',status='succeeded'),scales={'x':1.},note='Synthetic analytic control.'),'development analyst')
    project=p.create(store,dict(scenario='Reproduce the explicit decay and compare with a frozen analytic reference.',mode='reproduction',source_refs=[source],reference_refs=[ref]))
    f=frame('strict_reproduction');f['assumptions']=[];f['criteria'].append(dict(id='C2',kind='reference_comparison',description='Compare the fixed reference.',required=True))
    step(store,project,'revise_frame',frame=f,reason='Agent defines reproduction criterion.')
    it=step(store,project,'open_iteration',reason='Freeze model and reference before execution.',decision_plan=plan,reference_ref=ref,
        config=dict(model_type='ode',execution=dict(experiment=dict(times=[0.,1.],time_unit='second'))))['iteration_ref']
    step(store,project,'run_iteration',iteration_ref=it)
    assert p.facts(store,it)['comparison_status']=='pass'
    assert p.own(store,assess(store,project,it),p.ASSESSMENT,project)['criteria_satisfied']


@pytest.mark.parametrize('criterion',['reference_comparison','external_review','source_coverage'])
def test_llm_cannot_upgrade_unperformed_validation_to_a_pass(tmp_path,criterion):
    store,project,it=opened(tmp_path)
    # A later frame cannot replace the criteria frozen into this iteration.
    f=frame();f['criteria'][0]['kind']=criterion
    step(store,project,'revise_frame',frame=f,reason='A different future criterion.')
    with pytest.raises(ValueError,match='required frozen criteria'):assess(store,project,it)
    first=p.own(store,it,p.ITERATION,project);assert first['criteria'][0]['kind']=='native_checks'
    with pytest.raises(ValueError):step(store,project,'conclude',outcome='completed_with_limits',summary='Unsupported completion.',assessment_refs=[],limitations=[])


def test_scope_and_exact_quotes_cannot_be_expanded_by_the_agent(tmp_path):
    store,project=setup(tmp_path)
    path=tmp_path/'s.txt';path.write_text('An unrelated source.');ref=evidence.ingest_source(store,path)
    f=frame();f['evidence']=[dict(id='E1',kind='source',source_ref=ref,page=1,quote='An unrelated source.')]
    with pytest.raises(ValueError,match='outside authorized scope'):
        step(store,project,'revise_frame',frame=f,reason='Attempt unauthorized source use.')
    assert not p.inspect(store,project)['frame_ref']


def test_alignment_is_attributed_and_agent_cannot_answer_for_the_human(tmp_path):
    store,project,_=framed(tmp_path)
    q=step(store,project,'ask_alignment',category='granularity',question='Do you need population or individual behavior?',
        affected_fields=['granularity'],options=['Population','Individual'])['question_ref']
    assert p.inspect(store,project)['state']=='awaiting_alignment'
    with pytest.raises(ValueError):p.operate(store,project,'reply_alignment',dict(question_ref=q,reply='Population',respondent='invented'))
    reply=step(store,project,'reply_alignment',question_ref=q,reply='Population behavior is sufficient.',respondent='researcher')['reply_ref']
    assert p.inspect(store,project)['state']=='ready'
    source=step(store,project,'reply_source',reply_ref=reply)['source_ref']
    assert source in p.inspect(store,project)['source_refs']
    assert store.get(source)['payload']['warnings']


def test_native_call_allocations_are_not_reset_by_frames_or_index_deletion(tmp_path):
    store,project,it=opened(tmp_path,max_native_calls=6)
    step(store,project,'revise_frame',frame=frame(),reason='Another framing does not refund calls.')
    (store.root/'index.sqlite').unlink()
    with pytest.raises(ValueError,match='native budget exhausted'):
        step(store,project,'open_iteration',reason='No budget.',config=dict(model_type='boolean'),native_agent=True)
    assert p.inspect(store,project)['budget']['native_reserved']==6


def test_idempotent_key_does_not_repeat_a_decision(tmp_path):
    store,project=setup(tmp_path)
    args=dict(frame=frame(),reason='One decision.')
    a=p.operate(store,project,'revise_frame',args,key='same-key')
    assert p.operate(store,project,'revise_frame',args,key='same-key')==a
    assert len(p.inspect(store,project)['frame_refs'])==1
    with pytest.raises(ValueError,match='different inputs'):
        p.operate(store,project,'revise_frame',dict(frame=frame(),reason='Changed'),key='same-key')


def test_provider_failure_is_not_retried_and_secrets_are_not_journaled(tmp_path):
    store,project=setup(tmp_path);provider=Scripted([ValueError('private-provider-credential')])
    result=supervisor.run(store,provider,project)
    assert p.inspect(store,project)['state']=='needs_recovery' and len(provider.requests)==1
    assert supervisor.run(store,provider,project)==result and len(provider.requests)==1
    assert 'private-provider-credential' not in ''.join(x.read_text() for x in store.objects_dir.glob('*.json'))
    with pytest.raises(ValueError,match='no unique confirmed'):p.recover(store,project,p.inspect(store,project)['unresolved_operation_refs'][0])


def test_confirmed_planner_response_recovers_without_reissuing_request(tmp_path,monkeypatch):
    store,project=setup(tmp_path);provider=Scripted([act('conclude',outcome='insufficient_evidence',summary='No measurements.',assessment_refs=[],limitations=['Development control.'])])
    original=store.put
    def fail(kind,payload,refs=None):
        if kind==p.RETURN and payload.get('state')=='returned' and payload.get('result',{}).get('response_ref'):raise KeyboardInterrupt
        return original(kind,payload,refs)
    monkeypatch.setattr(store,'put',fail)
    with pytest.raises(KeyboardInterrupt):supervisor.run(store,provider,project)
    monkeypatch.setattr(store,'put',original)
    attempt=p.inspect(store,project)['unresolved_operation_refs'][0]
    p.recover(store,project,attempt)
    supervisor.run(store,provider,project)
    assert len(provider.requests)==1 and p.inspect(store,project)['state']=='insufficient_evidence'


def test_context_contains_actual_human_reply_and_templates_without_fabricated_evidence(tmp_path):
    store,project=setup(tmp_path)
    q=step(store,project,'ask_alignment',category='objective',question='Which observable matters?',affected_fields=['objective'])['question_ref']
    step(store,project,'reply_alignment',question_ref=q,reply='Only population-level switching.',respondent='human')
    message=supervisor._messages(store,project)
    assert isinstance(message,list)
    raw=json.dumps(message)
    assert 'Only population-level switching.' in raw and 'native_model_schema_examples' in raw and 'not task evidence' in raw


def test_supervisor_invalid_actions_consume_calls_and_stop_without_science(tmp_path):
    store,project=setup(tmp_path);provider=Scripted([act('shell',cmd='forbidden')]*3)
    supervisor.run(store,provider,project)
    assert len(provider.requests)==3 and p.inspect(store,project)['budget']['planner_calls']==3
    assert not p.inspect(store,project)['iteration_refs']


def test_source_reading_can_reach_beyond_the_old_proposal_prefix(tmp_path):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'text.txt';path.write_text('prefix '*600+'Important appendix evidence.')
    ref=evidence.ingest_source(store,path);project=p.create(store,dict(scenario='Read the appendix.',source_refs=[ref]))
    result=step(store,project,'inspect_source',source_ref=ref,page=1,start=4200,end=4228)
    assert 'Important appendix evidence.' in result['text']


def test_snapshot_report_and_export_remain_byte_identical_after_later_iterations(tmp_path):
    store,project,it=opened(tmp_path)
    p.operate(store,project,'run_iteration',dict(iteration_ref=it),provider=Scripted([act('run_typed'),act('finish')]))
    snap=p.snapshot(store,project);before=reporting.render_report(store,snap)
    step(store,project,'revise_frame',frame=frame(),reason='A later project revision.')
    assert reporting.render_report(store,snap)==before
    bundles.export_bundle(store,snap,tmp_path/'bundle')
    assert reporting.render_report(ArtifactStore(tmp_path/'bundle'),snap)==before


def test_cli_scenario_intake_and_readonly_show(tmp_path,capsys):
    config=tmp_path/'project.json';config.write_text(json.dumps(dict(scenario='A human application scenario.')))
    assert cli.main(['research-project-create','--config',str(config),'--store',str(tmp_path/'s')])==0
    result=json.loads(capsys.readouterr().out);ref=result['project_ref']
    assert cli.main(['research-project-show',ref,'--store',str(tmp_path/'s')])==0
    assert json.loads(capsys.readouterr().out)['scenario']=='A human application scenario.'


# -- Legacy received-but-unparsed planner responses: explicit recovery ----------

DECAY_TEXT='Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\nTime is measured in seconds.\n'


def dsml_block(name,params):
    body='<｜DSML｜ calls>\n<｜DSML｜ invoke name="%s">\n'%name
    for key,flag,value in params:
        body+='<｜DSML｜ parameter name="%s" string="%s">%s</｜DSML｜ parameter>\n'%(key,flag,value)
    return body+'</｜DSML｜ invoke>\n</｜DSML｜ calls>'


def legacy_provider_failure(store,project,text,*,model='deepseek-flash-2',failure_model=None,
                            error_kind='model_response',failures=1,usage=None,
                            args_scope=None,prompt_scope=None,failure_request=False,
                            with_blob=True,blob_in_refs=True,return_failure_ref=True):
    """Recreate the historical shape: a respond attempt whose only saved
    outcome is an unknown return plus provider failure receipt(s) with the
    retained raw model content blob. The prompt records the real canonical
    message scope; the keyword parameters only exist to assemble tampered
    stores for refusal tests and default to the genuine production shape."""
    identity={'class':'biomodel_agent.llm.ChatProvider','endpoint_sha256':'a'*64,'model':model,
              'json_mode':False,'timeout_s':60.,'max_tokens':8000}
    messages=[dict(role='system',content='Offline legacy control prompt.'),
              dict(role='user',content=json.dumps(dict(project_ref=project)))]
    scope=hashlib.sha256(canonical_bytes(messages)).hexdigest()
    request=store.put('research_project_prompt',dict(project_ref=project,messages=messages,
        scope_sha256=prompt_scope or scope,provider=identity),refs=(project,))
    args=dict(request_ref=request,scope_sha256=args_scope or scope,provider=identity)
    signature=hashlib.sha256(canonical_bytes(dict(operation='respond',args=args,actor='agent',response_ref=None))).hexdigest()
    attempt=store.put(p.STEP,dict(version=1,project_ref=project,sequence=0,key='legacy-respond',operation='respond',
        signature=signature,args=args,actor='agent',native_allocation=0,response_ref=None),refs=(project,))
    blob=store.put_blob(json.dumps(text,ensure_ascii=True).encode('ascii'))
    reported=usage if usage is not None else dict(prompt_tokens=8000,completion_tokens=1012,total_tokens=9012)
    failure_provider=identity if failure_model is None else dict(identity,model=failure_model)
    failure_request_ref=store.put('research_project_prompt',dict(project_ref=project,messages=[*messages,dict(role='user',content='Different request.')],
        scope_sha256=scope,provider=identity),refs=(project,)) if failure_request else request
    failure_refs=[]
    for index in range(failures):
        payload=dict(version=1,project_ref=project,attempt_ref=attempt,
            request_ref=failure_request_ref,provider=failure_provider,error_kind=error_kind,usage=reported,
            elapsed_s=4.24+index,response_diagnostic=dict(finish_reason='stop',content_characters=len(text)),
            model_content_authority='untrusted_model_output_not_an_action')
        if with_blob:payload['model_content_json_blob_ref']=blob
        refs=[project,attempt,failure_request_ref]
        if with_blob and blob_in_refs:refs.append(blob)
        failure_refs.append(store.put(p.PROVIDER_FAILURE,payload,refs=tuple(refs)))
    if return_failure_ref=='other':
        return_ref=store.put(p.PROVIDER_FAILURE,dict(version=1,project_ref=project,attempt_ref='f'*64,
            request_ref=request,provider=identity,error_kind='transport',usage={},elapsed_s=0.,
            response_diagnostic=dict(finish_reason='stop',content_characters=0),
            model_content_authority='untrusted_model_output_not_an_action'),refs=(project,attempt,request))
    elif return_failure_ref:
        return_ref=failure_refs[0]
    else:
        return_ref=None
    store.put(p.RETURN,dict(version=1,project_ref=project,attempt_ref=attempt,state='unknown',result=None,
        error_type='ModelResponseError',diagnostic=dict(stage='respond',code='model_response',
        message='Provider request failed; inspect the saved diagnostic without resending.',
        response=dict(finish_reason='stop',content_characters=len(text))),
        **({'provider_failure_ref':return_ref} if return_ref else {})),
        refs=(project,attempt,*([return_ref] if return_ref else [])))
    return attempt


def test_legacy_model_response_failure_recovers_dsml_without_network_or_new_calls(tmp_path):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'paper.txt'
    path.write_text(DECAY_TEXT,encoding='utf-8',newline='\n')
    source=evidence.ingest_source(store,path)
    project=p.create(store,dict(scenario='Read the frozen paper snapshot.',source_refs=[source],
        policy=dict(planner_max_calls=1)))
    text=dsml_block('inspect_source',[('source_ref','true',source),('page','false','1'),
        ('start','false','0'),('end','false','33')])
    attempt=legacy_provider_failure(store,project,text)
    view=p.inspect(store,project)
    assert view['state']=='needs_recovery' and view['unresolved_operation_refs']==[attempt]
    assert supervisor.usage(store,project)['reported_total_tokens']==9012
    result=p.recover(store,project,attempt)
    assert result['response']['action']=='inspect_source'
    assert result['response']['args']==dict(source_ref=source,page=1,start=0,end=33)
    assert p.recover(store,project,attempt)==result  # repeated recovery is idempotent
    receipt=store.get(result['response_ref'])
    assert receipt['payload']['source']=='provider_failure_content'
    assert receipt['payload']['model_content_authority']=='untrusted_model_output_not_an_action'
    assert json.loads(store.get_blob(receipt['payload']['model_content_json_blob_ref']))==text
    class Forbidden:
        def respond(self,messages):raise AssertionError('recovery must not call the provider')
    supervisor.run(store,Forbidden(),project)
    usage=supervisor.usage(store,project)
    assert usage['planner_calls']==1 and usage['reported_total_tokens']==9012
    reads=[v for _,v in p.rows(store,p.RETURN,project) if v['state']=='returned' and (v['result'] or {}).get('text')]
    assert reads and reads[0]['result']['text']=='Initial x = 1.\nParameter k = 0.4.'
    assert p.inspect(store,project)['state']=='planner_budget_exhausted'


class ReportingScripted(Scripted):
    """Scripted planner that reports the known usage of its own call, so the
    legacy failure usage and the live call usage stay separately visible."""

    def __init__(self,actions,usage):
        super().__init__(actions);self._reported=usage

    def respond(self,messages):
        self.last_usage=dict(self._reported)
        return super().respond(messages)


def test_legacy_unparseable_answer_recovers_as_rejected_and_advances_with_feedback(tmp_path):
    store,project=setup(tmp_path,planner_max_calls=2)
    text='I will read the paper now.\n'+dsml_block('run',[('a','false','1')]).replace('</｜DSML｜ calls>','')
    attempt=legacy_provider_failure(store,project,text)
    assert p.inspect(store,project)['state']=='needs_recovery'
    assert p.recover(store,project,attempt) is None
    recoveries=p.rows(store,p.RECOVERY,project)
    assert len(recoveries)==1 and recoveries[0][1]['state']=='rejected'
    assert recoveries[0][1]['result'] is None and 'refused' in recoveries[0][1]['diagnostic']['message']
    assert recoveries[0][1]['provider_failure_ref']==recoveries[0][1]['confirmation_ref']
    failure=p.own(store,recoveries[0][1]['confirmation_ref'],p.PROVIDER_FAILURE,project)
    assert failure['attempt_ref']==attempt and failure['error_kind']=='model_response'
    assert p.inspect(store,project)['state']=='ready' and not p.inspect(store,project)['unresolved_operation_refs']
    # The scripted mock reports its own known usage; the retained legacy
    # failure usage and the live call usage are both counted exactly once.
    provider=ReportingScripted([act('conclude',outcome='insufficient_evidence',summary='Continued after the refused response.',
        assessment_refs=[],limitations=['Offline control.'])],dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    supervisor.run(store,provider,project)
    assert p.inspect(store,project)['state']=='insufficient_evidence'
    assert 'planner_response' in json.dumps(provider.requests[0]) and 'refused' in json.dumps(provider.requests[0])
    usage=supervisor.usage(store,project)
    assert usage['planner_calls']==2 and usage['reported_total_tokens']==9027
    assert usage['missing_usage_calls']==0


def test_recovery_resumes_after_normalized_response_is_saved(tmp_path,monkeypatch):
    store,project=setup(tmp_path)
    attempt=legacy_provider_failure(store,project,dsml_block('inspect_source',[('page','false','1')]))
    original_put=store.put
    def interrupted_put(kind,*args,**kwargs):
        if kind==p.RECOVERY:raise RuntimeError('interrupted after response persistence')
        return original_put(kind,*args,**kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(store,'put',interrupted_put)
        with pytest.raises(RuntimeError,match='after response persistence'):
            p.recover(store,project,attempt)
    assert len(p.rows(store,p.RESPONSE,project))==1
    assert not p.rows(store,p.RECOVERY,project)
    result=p.recover(store,project,attempt)
    assert result['provider_failure_ref']
    assert p.recover(store,project,attempt)==result
    assert len(p.rows(store,p.RESPONSE,project))==len(p.rows(store,p.RECOVERY,project))==1
    assert supervisor.usage(store,project)['reported_total_tokens']==9012
    assert supervisor.usage(store,project)['planner_calls']==1


def test_legacy_recovery_rejects_mismatched_ambiguous_or_unconfirmed_receipts(tmp_path):
    store,project=setup(tmp_path)
    text=dsml_block('finish',[])
    attempt=legacy_provider_failure(store,project,text,failure_model='glm-5.3-flash')
    assert p.inspect(store,project)['state']=='needs_recovery'
    with pytest.raises(ValueError,match='no unique confirmed planner response'):
        p.recover(store,project,attempt)
    assert not p.rows(store,p.RECOVERY,project)
    ambiguous_store,ambiguous=setup(tmp_path/'two')
    ambiguous_attempt=legacy_provider_failure(ambiguous_store,ambiguous,text,failures=2)
    with pytest.raises(ValueError,match='no unique confirmed planner response'):
        p.recover(ambiguous_store,ambiguous,ambiguous_attempt)
    transport_store,transport_project=setup(tmp_path/'three')
    transport_attempt=legacy_provider_failure(transport_store,transport_project,text,error_kind='transport')
    with pytest.raises(ValueError,match='no unique confirmed planner response'):
        p.recover(transport_store,transport_project,transport_attempt)
    assert p.inspect(transport_store,transport_project)['state']=='needs_recovery'


def test_legacy_recovery_refuses_tampered_prompt_return_or_closure(tmp_path):
    """A provider receipt may only be claimed under the full binding closure:
    a wrong scope/request, missing blob refs, or a foreign or absent original
    RETURN are all refused without writing any derived RESPONSE/RECOVERY."""
    text=dsml_block('finish',[])
    def refused(name,**tampering):
        store,project=setup(tmp_path/name)
        attempt=legacy_provider_failure(store,project,text,**tampering)
        assert p.inspect(store,project)['state']=='needs_recovery'
        with pytest.raises(ValueError,match='no unique confirmed planner response'):
            p.recover(store,project,attempt)
        assert not p.rows(store,p.RECOVERY,project)
        assert not p.rows(store,p.RESPONSE,project)
        assert p.inspect(store,project)['state']=='needs_recovery'
        assert p.inspect(store,project)['unresolved_operation_refs']==[attempt]
    refused('args_scope',args_scope='b'*64)
    refused('prompt_scope',prompt_scope='b'*64)
    refused('request',failure_request=True)
    refused('missing_blob',with_blob=False)
    refused('open_refs',blob_in_refs=False)
    refused('foreign_return',return_failure_ref='other')
    refused('absent_return',return_failure_ref=False)


FOREIGN_FINGERPRINT='c'*64


def foreign_version_copy(store,project):
    """Simulate a store written by an older source tree: the identical frozen
    project payload carrying that version's implementation fingerprint. No
    historical artifact is rewritten; the copy is a new immutable object."""
    payload=deepcopy(store.get(project)['payload'])
    payload['implementation_sha256']=FOREIGN_FINGERPRINT
    return store.put(p.PROJECT,payload,refs=store.get(project)['refs'])


def test_historical_provider_receipt_recovers_offline_across_source_versions(tmp_path):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'paper.txt'
    path.write_text(DECAY_TEXT,encoding='utf-8',newline='\n')
    source=evidence.ingest_source(store,path)
    project=p.create(store,dict(scenario='Read the frozen paper snapshot.',source_refs=[source],
        policy=dict(planner_max_calls=1)))
    foreign=foreign_version_copy(store,project)
    assert foreign!=project
    text=dsml_block('inspect_source',[('source_ref','true',source),('page','false','1'),
        ('start','false','0'),('end','false','33')])
    attempt=legacy_provider_failure(store,foreign,text)
    # Normal reads, inspection and execution keep the strict version gate.
    with pytest.raises(ValueError,match='project implementation changed'):p.load(store,foreign)
    with pytest.raises(ValueError,match='project implementation changed'):p.inspect(store,foreign)
    class Forbidden:
        def respond(self,messages):raise AssertionError('no provider call may follow a historical recovery')
    with pytest.raises(ValueError,match='project implementation changed'):supervisor.run(store,Forbidden(),foreign)
    result=p.recover(store,foreign,attempt)
    assert result['response']['action']=='inspect_source'
    assert result['response']['args']==dict(source_ref=source,page=1,start=0,end=33)
    failure=p.own(store,result['provider_failure_ref'],p.PROVIDER_FAILURE,foreign)
    receipt=store.get(result['response_ref'])['payload']
    assert receipt['source']=='provider_failure_content'
    assert receipt['provider_failure_ref']==result['provider_failure_ref']
    assert receipt['usage']==failure['usage']==dict(prompt_tokens=8000,completion_tokens=1012,total_tokens=9012)
    assert json.loads(store.get_blob(receipt['model_content_json_blob_ref']))==text
    record=p.rows(store,p.RECOVERY,foreign)[0][1]
    assert record['state']=='returned' and record['result']==result
    assert record['implementation_sha256']==FOREIGN_FINGERPRINT
    assert record['recovery_implementation_sha256']==recovery.implementation_fingerprint()
    assert record['compatibility']=='offline_provider_receipt_recovery_only'
    # Repeated recovery is idempotent and the frozen project payload (version,
    # deadline, budgets) is untouched by the claim.
    assert p.recover(store,foreign,attempt)==result
    assert len(p.rows(store,p.RECOVERY,foreign))==1
    frozen=store.get(foreign)['payload']
    assert frozen['version']==1 and frozen['implementation_sha256']==FOREIGN_FINGERPRINT
    assert frozen['deadline_at'] is None
    assert sum(v['operation']=='respond' for _,v in p.rows(store,p.STEP,foreign))==1
    with pytest.raises(ValueError,match='project implementation changed'):p.inspect(store,foreign)
    with pytest.raises(ValueError,match='project implementation changed'):supervisor.run(store,Forbidden(),foreign)


def test_historical_native_recovery_stays_version_gated(tmp_path):
    store,project,iteration=opened(tmp_path)
    foreign=foreign_version_copy(store,project)
    frame_ref=p.own(store,iteration,p.ITERATION,project)['frame_ref']
    foreign_frame=store.put(p.FRAME,{**deepcopy(p.own(store,frame_ref,p.FRAME,project)),'project_ref':foreign},
        refs=tuple(foreign if r==project else r for r in store.get(frame_ref)['refs']))
    iteration_payload=p.own(store,iteration,p.ITERATION,project)
    foreign_iteration=store.put(p.ITERATION,{**deepcopy(iteration_payload),'project_ref':foreign,'frame_ref':foreign_frame},
        refs=tuple({project:foreign,frame_ref:foreign_frame}.get(r,r) for r in store.get(iteration)['refs']))
    args=dict(iteration_ref=foreign_iteration)
    signature=hashlib.sha256(canonical_bytes(dict(operation='run_iteration',args=args,actor='agent',response_ref=None))).hexdigest()
    attempt=store.put(p.STEP,dict(version=1,project_ref=foreign,sequence=0,key='interrupted-native',operation='run_iteration',
        signature=signature,args=args,actor='agent',native_allocation=6,response_ref=None),refs=(foreign,))
    with pytest.raises(ValueError,match='provider respond receipts'):
        p.recover(store,foreign,attempt)
    assert not p.rows(store,p.RECOVERY,foreign)
    assert p.inspect(store,project)['iteration_refs']==[iteration]


def test_expired_deadline_still_claims_the_offline_receipt_without_extension(tmp_path):
    store,project=setup(tmp_path,planner_max_calls=2,total_timeout_s=0.05)
    deadline=p.load(store,project)['deadline_at']
    while time.time()<deadline:time.sleep(0.01)
    attempt=legacy_provider_failure(store,project,dsml_block('inspect_source',[('a','true','x')]))
    assert p.inspect(store,project)['state']=='needs_recovery'
    result=p.recover(store,project,attempt)
    assert result['response']['action']=='inspect_source'
    assert store.get(project)['payload']['deadline_at']==deadline
    assert sum(v['operation']=='respond' for _,v in p.rows(store,p.STEP,project))==1
    assert p.inspect(store,project)['state']=='deadline_exhausted'
    class Forbidden:
        def respond(self,messages):raise AssertionError('execution must stop at the frozen deadline')
    snapshot=supervisor.run(store,Forbidden(),project)
    assert store.get(snapshot)['payload']['view']['state']=='deadline_exhausted'
    assert store.get(project)['payload']['deadline_at']==deadline


# -- Plan compilation diagnosis reaches the planner context (observed ODE loss) --

def blocked_decay_plan(spec):
    bad=deepcopy(spec)
    quote='Parameter k = 0.4.'
    bad['evidence'][2].update(start=DECAY_TEXT.index(quote),end=DECAY_TEXT.index(quote)+len(quote),quote=quote)
    return bad


def reproduction_project(store,source,**policy):
    limits={**dict(planner_max_calls=20),**policy}
    project=p.create(store,dict(scenario='Reproduce the supplied decay.',mode='reproduction',
        source_refs=[source],policy=limits))
    f=frame('strict_reproduction');f['assumptions']=[]
    step(store,project,'revise_frame',frame=f,reason='Agent defines reproduction criterion.')
    return project


PLAN_CONFIG=dict(model_type='ode',execution=dict(experiment=dict(times=[0.,1.],time_unit='second')))


def test_blocked_plan_compilation_diagnosis_names_target_and_reason(tmp_path):
    store,source,spec=ode_setup(tmp_path)
    project=reproduction_project(store,source)
    with pytest.raises(ValueError,match='compilation is not ready'):
        step(store,project,'open_iteration',reason='Freeze the plan.',
            decision_plan=blocked_decay_plan(spec),config=dict(PLAN_CONFIG))
    rejected=[(r,v) for r,v in p.rows(store,p.RETURN,project) if v['state']=='rejected']
    compilation=rejected[-1][1]['diagnostic']['compilation']
    assert compilation['state']=='invalid'
    issue=next(i for i in compilation['issues'] if i['code']=='source_binding_mismatch')
    assert issue['target']=='rule:x/expression'
    assert 'declares another target' in issue['question']
    assert compilation['plan_ref'] and compilation['compilation_ref']
    assert compilation['checks'] and not compilation['issues_truncated']
    context=json.dumps(supervisor._messages(store,project))
    assert 'rule:x/expression' in context and 'source_binding_mismatch' in context
    # The verified compilation/plan refs join the rejected RETURN closure, so
    # snapshot exports keep the exact blocking evidence and its source slices.
    assert {compilation['compilation_ref'],compilation['plan_ref']}<=set(store.get(rejected[-1][0])['refs'])
    snap=p.snapshot(store,project)
    bundles.export_bundle(store,snap,tmp_path/'bundle')
    exported=ArtifactStore(tmp_path/'bundle')
    saved=exported.get(compilation['compilation_ref'])
    assert saved['kind']=='modeling_plan_compilation' and saved['payload']['plan_ref']==compilation['plan_ref']
    assert saved['payload']['state']=='invalid' and saved['payload']['issues']==compilation['issues']
    plan=modeling_plan.load(exported,compilation['plan_ref'])
    for anchor in plan['spec']['evidence']:
        page=next(x for x in exported.get(anchor['source_ref'])['payload']['pages'] if x['number']==anchor['page'])
        assert page['text'][anchor['start']:anchor['end']]==anchor['quote']


def test_planner_submits_corrected_plan_after_compilation_diagnosis(tmp_path):
    store,source,spec=ode_setup(tmp_path)
    project=reproduction_project(store,source,planner_max_calls=3)
    plan_args=lambda plan:dict(reason='Freeze the plan.',decision_plan=plan,config=dict(PLAN_CONFIG))
    provider=Scripted([act('open_iteration',**plan_args(blocked_decay_plan(spec))),
                       act('open_iteration',**plan_args(spec)),
                       act('conclude',outcome='insufficient_evidence',
                           summary='The corrected plan is frozen; no execution was required.',
                           assessment_refs=[],limitations=['Offline control.'])])
    supervisor.run(store,provider,project)
    view=p.inspect(store,project)
    assert len(view['iteration_refs'])==1 and view['budget']['planner_calls']==3
    assert view['state']=='insufficient_evidence'
    assert p.facts(store,view['iteration_refs'][0])['state']=='prepared'

