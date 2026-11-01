"""Direct computations, confirmed-return recovery, and validation boundaries."""
import json
import pytest

from biomodel_agent import bundles,reporting,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted,act
from test_research_projects import ROOT,frame,framed,opened,step,assess


@pytest.mark.parametrize('kind',['boolean','stochastic','pde','individual','fba'])
def test_complete_math_uses_no_inner_model_and_detached_project_keeps_real_results(tmp_path,kind):
    store,project,it=opened(tmp_path,kind,native_agent=False)
    result=step(store,project,'run_iteration',iteration_ref=it)
    before=p.facts(store,it)
    assert before['executed'] and before['numeric_status']=='pass' and before['native_provider_calls']==0
    assert p.inspect(store,project)['budget']['native_reserved']==0
    data=step(store,project,'inspect_iteration',iteration_ref=it)
    assert data['preview']['model'] and data['preview']['result'] and data['preview']['verification']['completion_eligible']
    snap=p.snapshot(store,project);report=reporting.render_report(store,snap)
    bundles.export_bundle(store,snap,tmp_path/'bundle');detached=ArtifactStore(tmp_path/'bundle')
    assert p.facts(detached,it)==before and reporting.render_report(detached,snap)==report
    assert step(detached,project,'run_iteration',iteration_ref=it)==result
    assert len(detached.list('fba_execution' if kind=='fba' else 'typed_execution'))==1
    assert supervisor.usage(store,project)['native_provider_calls']==0


@pytest.mark.parametrize('kind',['boolean','fba'])
def test_confirmed_direct_result_can_be_recovered_in_detached_bundle_without_recomputation(tmp_path,kind,monkeypatch):
    store,project,it=opened(tmp_path,kind,native_agent=False);original=store.put
    def fail(name,payload,refs=None):
        if name==p.RETURN and payload.get('state')=='returned' and payload.get('result',{}).get('result_ref'):
            raise KeyboardInterrupt('crash after scientific return, before project return')
        return original(name,payload,refs)
    monkeypatch.setattr(store,'put',fail)
    with pytest.raises(KeyboardInterrupt):p.operate(store,project,'run_iteration',dict(iteration_ref=it),key='interrupted')
    monkeypatch.setattr(store,'put',original)
    actual=p.facts(store,it);assert actual['executed']
    pending=p.inspect(store,project)['unresolved_operation_refs'][0];snap=p.snapshot(store,project)
    bundles.export_bundle(store,snap,tmp_path/'bundle');detached=ArtifactStore(tmp_path/'bundle')
    assert p.inspect(detached,project)['state']=='needs_recovery'
    recovered=p.recover(detached,project,pending)
    assert recovered['result_ref']==actual['application_run_ref']
    assert p.operate(detached,project,'run_iteration',dict(iteration_ref=it),key='interrupted')==recovered
    assert len(detached.list('fba_execution' if kind=='fba' else 'typed_execution'))==1


def test_bad_science_arguments_are_rejected_before_reservation_and_do_not_wedge_project(tmp_path):
    store,project,it=opened(tmp_path)
    with pytest.raises(ValueError,match='provider required'):step(store,project,'run_iteration',iteration_ref=it)
    with pytest.raises(ValueError,match='outside project'):step(store,project,'run_iteration',iteration_ref='0'*64)
    assert p.inspect(store,project)['state']=='ready'
    assert not any(x['operation']=='run_iteration' for _,x in p.rows(store,p.STEP,project))


def test_native_human_continuation_uses_attributed_reply_and_keeps_paused_snapshot(tmp_path):
    store,project,it=opened(tmp_path)
    p.operate(store,project,'run_iteration',dict(iteration_ref=it),provider=Scripted([act('ask_researcher',question='Which observable?')]))
    assert p.facts(store,it)['state']=='awaiting_input' and not p.facts(store,it)['executed']
    snap=p.snapshot(store,project);old=reporting.render_report(store,snap)
    q=step(store,project,'ask_alignment',category='objective',question='Which observable?',affected_fields=[it])['question_ref']
    reply=step(store,project,'reply_alignment',question_ref=q,reply='Population switching.',respondent='Researcher')['reply_ref']
    p.operate(store,project,'resume_iteration',dict(iteration_ref=it,reply_ref=reply),provider=Scripted([act('run_typed'),act('finish')]))
    assert p.facts(store,it)['executed'] and p.facts(store,it)['native_provider_calls']==3
    assert reporting.render_report(store,snap)==old
    bundles.export_bundle(store,snap,tmp_path/'paused');detached=ArtifactStore(tmp_path/'paused')
    assert reporting.render_report(detached,snap)==old and not p.facts(detached,it)['executed']


def test_alignment_requires_its_question_and_cannot_replace_scientific_computation(tmp_path):
    store,project,_=framed(tmp_path)
    q=step(store,project,'ask_alignment',category='granularity',question='Coarse or detailed?',affected_fields=['granularity'])['question_ref']
    step(store,project,'reply_alignment',question_ref=q,reply='Coarse.',respondent='Researcher')
    f=frame();f['criteria']=[dict(id='C1',kind='alignment',description='Explicit granularity alignment.',required=True)]
    step(store,project,'revise_frame',frame=f,reason='An unrelated answer is insufficient.')
    def open_math():return step(store,project,'open_iteration',reason='Freeze a hypothesis.',config=dict(model_type='boolean'),
        native_draft=json.loads((ROOT/'examples/model-types/boolean.json').read_text()),hypothesis_ids=['H1'])['iteration_ref']
    first=open_math();step(store,project,'run_iteration',iteration_ref=first)
    with pytest.raises(ValueError,match='required frozen criteria'):assess(store,project,first)
    f['criteria'][0]['question_ref']=q;step(store,project,'revise_frame',frame=f,reason='Bind this criterion to the actual question.')
    second=open_math()
    with pytest.raises(ValueError,match='required frozen criteria'):assess(store,project,second)
    step(store,project,'run_iteration',iteration_ref=second)
    assert p.own(store,assess(store,project,second),p.ASSESSMENT,project)['criteria_satisfied']


def test_one_planner_call_is_consumed_then_budget_state_is_explicit(tmp_path):
    store,project,_=framed(tmp_path,planner_max_calls=1)
    provider=Scripted([act('inspect_source',source_ref='0'*64,page=1,start=0,end=10)])
    supervisor.run(store,provider,project)
    assert p.inspect(store,project)['state']=='planner_budget_exhausted'
    supervisor.run(store,provider,project);assert len(provider.requests)==1
