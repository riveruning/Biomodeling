"""Agent-owned problem details, frozen iterations and honest research outcomes."""
from copy import deepcopy
import json
import math
from pathlib import Path

import httpx
import pytest

from biomodel_agent import bundles,cli,evidence,recovery,reporting,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
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

