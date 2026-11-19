"""Unified intake/lifecycle controls over real backends; providers are scripted."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from biomodel_agent import agent, bundles, cli, fba, recovery, reporting, research_tasks as tasks, timeline
from biomodel_agent import typed_agent_runtime, typed_models
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted, act
from test_modeling_plan import setup as ode_setup

ROOT=Path(__file__).parents[1]


def setup(tmp_path,kind):
    if kind=='ode':
        store,source,plan=ode_setup(tmp_path)
        config=dict(schema_version=1,goal='Reproduce the supplied explicit ODE.',model_type=kind,
            source_refs=[source],execution={'experiment':{'times':[0.,1.],'time_unit':'second'}})
        actions=[act('submit_plan',plan=plan),act('run'),act('finish')]
    else:
        store=ArtifactStore(tmp_path/'store')
        if kind=='fba':
            model=fba.import_model(store,ROOT/'examples/fba-v1/chain.json');settings={};run='run_fba'
        else:
            model=typed_models.import_model(store,ROOT/f'examples/model-types/{kind}.json')
            settings=json.loads((ROOT/f'examples/model-types/{kind}-settings.json').read_text());run='run_typed'
        config=dict(schema_version=1,goal='Execute the supplied bounded native model.',model_type=kind,
            model_ref=model,execution={'settings':settings})
        actions=[act(run),act('finish')]
    return store,config,actions


@pytest.mark.parametrize('kind',tasks.TYPES)
def test_one_entry_routes_six_types_and_exports_actual_native_result(tmp_path,kind):
    store,config,actions=setup(tmp_path,kind)
    request=tasks.prepare(store,config)
    before=store.list();view=tasks.preflight(store,request)
    assert view['state']=='ready_to_run' and not view['gaps'] and store.list()==before
    ref=tasks.execute(store,Scripted(actions),request)
    result=tasks.result(store,ref)
    assert result['state']=='completed' and result['model_type']==kind and result['run_count']==1
    assert tasks.status(store,request)['result_ref']==ref
    assert tasks.execute(store,None,request)==ref
    assert timeline.from_run(store,ref)['run_count']==1
    report=reporting.render_report(store,ref)
    assert 'Unified research task' in report and 'unknown' in report
    manifest=bundles.export_bundle(store,ref,tmp_path/'bundle')
    assert manifest['replay_mode']=='read_only_task_inspection'
    assert reporting.render_report(ArtifactStore(tmp_path/'bundle'),ref)==report


@pytest.mark.parametrize('kind',[None,*tasks.TYPES])
def test_missing_inputs_report_gaps_and_never_dispatch(tmp_path,kind):
    store=ArtifactStore(tmp_path/'store')
    request=tasks.prepare(store,dict(schema_version=1,goal='Study this system.',model_type=kind))
    view=tasks.status(store,request);before=store.list();provider=Scripted([])
    assert view['state']==('awaiting_type' if kind is None else 'awaiting_input') and view['gaps']
    with pytest.raises(ValueError):tasks.execute(store,provider,request)
    assert not provider.requests and store.list()==before


def test_native_model_type_mismatch_is_an_input_gap(tmp_path):
    store,config,_=setup(tmp_path,'boolean');config['model_type']='pde';config['execution']={}
    request=tasks.prepare(store,config)
    assert tasks.status(store,request)['state']=='awaiting_input'
    assert tasks.status(store,request)['gaps'][0]['code']=='invalid_native_input'


@pytest.mark.parametrize('kind',tasks.TYPES)
def test_provider_failure_remains_readable_and_cannot_restart_dispatch(tmp_path,kind):
    from biomodel_agent.llm import ProviderError
    store,config,_=setup(tmp_path,kind);request=tasks.prepare(store,config)
    provider=Scripted([ProviderError('Service unavailable.',kind='http',retryable=False)])
    ref=tasks.execute(store,provider,request)
    result=tasks.result(store,ref)
    assert result['state'] in ('failed','incomplete') and result['run_count']==0 and result['call_count']==1
    assert tasks.status(store,request)['result_ref']==ref
    assert tasks.execute(store,None,request)==ref and len(provider.requests)==1
    assert 'unknown' in reporting.render_report(store,ref)


def test_pre_dispatch_revision_then_family_budget_cannot_be_reset(tmp_path):
    store,config,actions=setup(tmp_path,'boolean')
    initial=tasks.prepare(store,dict(schema_version=1,goal=config['goal']))
    selected=tasks.prepare(store,config,parent_ref=initial,reason='Select the supplied explicit Boolean model.')
    root=tasks.execute(store,Scripted(actions),selected)
    assert tasks.status(store,initial)['state']=='another_revision_dispatched'
    with pytest.raises(ValueError):tasks.execute(store,Scripted([]),initial)
    with pytest.raises(ValueError):tasks.prepare(store,config,parent_ref=selected,reason='Try a second time.')
    assert tasks.execute(store,None,selected)==root


@pytest.mark.parametrize('kind',tasks.TYPES)
def test_unified_pause_reply_keeps_native_task_and_cumulative_calls(tmp_path,kind):
    store,config,actions=setup(tmp_path,kind)
    request=tasks.prepare(store,config)
    paused=tasks.execute(store,Scripted([act('ask_researcher',question='Confirm the selected execution scope?')]),request)
    assert tasks.result(store,paused)['state']=='awaiting_input'
    assert tasks.status(store,request)['pending_question']=='Confirm the selected execution scope?'
    final=tasks.resume(store,Scripted(actions),request,'Proceed with the current frozen protocol.',respondent='Operator')
    result=tasks.result(store,final)
    assert result['state']=='completed' and result['call_count']==len(actions)+1 and result['run_count']==1
    assert result['native_task_ref']==tasks.result(store,paused)['native_task_ref']
    assert tasks.execute(store,None,request)==final
    with pytest.raises(ValueError):tasks.resume(store,Scripted([]),request,'Proceed again.',respondent='Operator')


def test_confirmed_native_final_is_adopted_after_outer_write_interruption(tmp_path,monkeypatch):
    store,config,actions=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    with monkeypatch.context() as patch:
        patch.setattr(tasks,'_wrap',lambda *a,**k:(_ for _ in ()).throw(SystemExit('native returned')))
        with pytest.raises(SystemExit):tasks.execute(store,Scripted(actions),request)
    assert tasks.status(store,request)['state']=='completed'
    monkeypatch.setattr(typed_models,'execute',lambda *a,**k:pytest.fail('science repeated'))
    ref=tasks.recover(store,None,request)
    assert tasks.result(store,ref)['state']=='completed'


def test_native_journal_recovery_flows_through_unified_entry(tmp_path,monkeypatch):
    store,config,_=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    with monkeypatch.context() as patch:
        patch.setattr(typed_agent_runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('confirmed science return')))
        with pytest.raises(SystemExit):tasks.execute(store,Scripted([act('run_typed')]),request)
    view=tasks.status(store,request);assert view['state']=='needs_recovery'
    assert view['recovery_status']=='ready' and view['call_count']==view['run_count']==1
    monkeypatch.setattr(typed_models,'execute',lambda *a,**k:pytest.fail('science repeated'))
    ref=tasks.recover(store,Scripted([act('finish')]),request)
    assert tasks.result(store,ref)['state']=='completed' and tasks.result(store,ref)['call_count']==2


def test_unknown_native_effects_do_not_create_a_new_task_or_budget(tmp_path,monkeypatch):
    store,config,_=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    with monkeypatch.context() as patch:
        patch.setattr(typed_models,'execute',lambda *a,**k:(_ for _ in ()).throw(SystemExit('unconfirmed')))
        with pytest.raises(SystemExit):tasks.execute(store,Scripted([act('run_typed')]),request)
    before=store.list();provider=Scripted([])
    view=tasks.status(store,request)
    assert view['recovery_status']=='blocked' and view['run_count']==1
    with pytest.raises(ValueError):tasks.recover(store,provider,request)
    with pytest.raises(ValueError):tasks.execute(store,provider,request)
    assert store.list()==before and not provider.requests


def test_deleted_index_does_not_reset_family_dispatch(tmp_path):
    store,config,actions=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    ref=tasks.execute(store,Scripted(actions),request)
    store.index_path.unlink()
    assert tasks.execute(store,None,request)==ref


def test_concurrent_family_owner_blocks_second_dispatch(tmp_path):
    store,config,_=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    family=tasks.load(store,request)['family_id'];before=store.list();provider=Scripted([])
    with tasks._owner(store,family):
        with pytest.raises(ValueError,match='active owner'):tasks.execute(store,provider,request)
    assert store.list()==before and not provider.requests


def test_result_cannot_relabel_goal_or_native_task(tmp_path):
    store,config,actions=setup(tmp_path,'boolean');request=tasks.prepare(store,config)
    ref=tasks.execute(store,Scripted(actions),request);obj=store.get(ref)
    for field,value in [('goal','A different research question.'),('native_task_ref','0'*64),('version',True)]:
        payload=deepcopy(obj['payload']);payload[field]=value
        forged=store.put(tasks.RUN,payload,refs=obj['refs'])
        with pytest.raises(ValueError):tasks.result(store,forged)


def test_minimal_event_shape_cannot_masquerade_as_verified_task_timeline(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    ref=store.put(tasks.RUN,dict(state='completed',events=[]))
    with pytest.raises(ValueError):timeline.from_run(store,ref)


def test_public_cli_intake_and_readonly_status_without_provider(tmp_path,capsys):
    store=tmp_path/'store';config=tmp_path/'config.json'
    config.write_text(json.dumps(dict(schema_version=1,goal='A new research question.')))
    assert cli.main(['research-task-create','--config',str(config),'--store',str(store)])==2
    out=json.loads(capsys.readouterr().out);assert out['state']=='awaiting_type'
    assert cli.main(['research-task-show',out['request_ref'],'--store',str(store)])==0
    assert json.loads(capsys.readouterr().out)['state']=='awaiting_type'
    assert cli.main(['research-task-run',out['request_ref'],'--store',str(store)])==1
    assert 'Select one supported model type' in capsys.readouterr().err


@pytest.mark.parametrize('field,value',[('max_calls',True),('schema_version',True),('execution',[]),('model_type','unknown')])
def test_malformed_task_rejected_before_request_write(tmp_path,field,value):
    store=ArtifactStore(tmp_path/'store');config=dict(schema_version=1,goal='Study a system.');config[field]=value
    with pytest.raises(ValueError):tasks.prepare(store,config)
    assert not store.list()
