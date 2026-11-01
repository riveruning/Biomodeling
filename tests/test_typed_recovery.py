"""Four native types retain frozen inputs and cumulative budgets on continuation."""
from copy import deepcopy
import json

import pytest
from biomodel_agent import agent, bundles, recovery, reporting, typed_agent_runtime as runtime
from biomodel_agent import typed_models as models
from test_model_types import imported, settings, Scripted


def act(name,**args):return dict(action=name,args=args)


def start(store,provider,model,name,**kwargs):
    return agent.run_model_agent(store,provider,model_type=name,model_ref=model,settings=settings(name),recoverable=True,**kwargs)


@pytest.mark.parametrize('name',models.TYPES)
def test_pause_resume_preserves_frozen_input_and_budget(tmp_path,name):
    store,model=imported(tmp_path,name)
    paused=start(store,Scripted([act('ask_researcher',question='Confirm the supplied frozen execution?')]),model,name)
    p=store.get(paused)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==1 and p['run_count']==0
    ref=agent.resume_agent(store,Scripted(['inspect_typed','run_typed','finish']),paused,'Proceed with these fixed inputs.',respondent='Operator')
    q=store.get(ref)['payload']
    assert q['state']=='completed' and q['call_count']==4 and q['run_count']==1
    assert q['task_ref']==p['task_ref'] and q['input_ref']==p['input_ref'] and q['parent_run_ref']==paused
    assert recovery.diagnose_execution(store,q['execution_ref'])['status']=='finished'
    assert 'typed_agent_v2' in reporting.render_report(store,ref)
    bundles.export_bundle(store,ref,tmp_path/'bundle')
    from biomodel_agent.storage import ArtifactStore
    assert reporting.render_report(ArtifactStore(tmp_path/'bundle'),ref)==reporting.render_report(store,ref)


@pytest.mark.parametrize('name',models.TYPES)
def test_returned_native_result_adopted_without_second_execution(tmp_path,monkeypatch,name):
    store,model=imported(tmp_path,name)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('after returned journal')))
        with pytest.raises(SystemExit):start(store,Scripted(['run_typed']),model,name)
    execution=store.list('agent_execution')[-1]
    diagnostic=recovery.diagnose_execution(store,execution)
    assert diagnostic['status']=='ready',diagnostic
    assert diagnostic['run_count']==diagnostic['call_count']==1
    monkeypatch.setattr(models,'execute',lambda *a,**k:pytest.fail('science repeated'))
    ref=agent.recover_agent(store,Scripted(['finish']),execution)
    p=store.get(ref)['payload']
    assert p['state']=='completed' and p['call_count']==2 and p['run_count']==1
    assert len(store.list('typed_execution'))==1


@pytest.mark.parametrize('name',models.TYPES)
def test_reply_after_calculation_does_not_reset_run_allowance(tmp_path,monkeypatch,name):
    store,model=imported(tmp_path,name)
    paused=start(store,Scripted(['run_typed',act('ask_researcher',question='How to interpret this numerical result?')]),model,name)
    p=store.get(paused)['payload'];assert p['run_count']==1
    monkeypatch.setattr(models,'execute',lambda *a,**k:pytest.fail('second science'))
    ref=agent.resume_agent(store,Scripted(['run_typed']),paused,'Change seed and run again.',respondent='Operator')
    q=store.get(ref)['payload']
    assert q['state']=='incomplete' and q['run_count']==1 and q['call_count']==3
    assert q['input_ref']==p['input_ref']


@pytest.mark.parametrize('where',['before_science','after_science'])
def test_unknown_scientific_effects_block_retry(tmp_path,monkeypatch,where):
    store,model=imported(tmp_path,'boolean');original=models.execute
    def fault(*args,**kwargs):
        if where=='after_science':original(*args,**kwargs)
        raise SystemExit('unconfirmed')
    with monkeypatch.context() as patch:
        patch.setattr(models,'execute',fault)
        with pytest.raises(SystemExit):start(store,Scripted(['run_typed']),model,'boolean')
    execution=store.list('agent_execution')[-1];d=recovery.diagnose_execution(store,execution)
    assert d['status']=='blocked' and d['run_count']==1
    before=store.list();provider=Scripted([])
    with pytest.raises(ValueError):agent.recover_agent(store,provider,execution)
    assert provider.calls==0 and store.list()==before


def test_cached_pause_response_recovers_at_call_limit(tmp_path,monkeypatch):
    store,model=imported(tmp_path,'boolean')
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'_final',lambda *a,**k:(_ for _ in ()).throw(SystemExit('before final')))
        with pytest.raises(SystemExit):start(store,Scripted([act('ask_researcher',question='Confirm?')]),model,'boolean',max_calls=1)
    execution=store.list('agent_execution')[-1];provider=Scripted([])
    ref=agent.recover_agent(store,provider,execution);p=store.get(ref)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==1 and provider.calls==0


@pytest.mark.parametrize('defect',['identity','budget','reply_size','context'])
def test_bad_continuation_rejected_before_writes(tmp_path,defect):
    store,model=imported(tmp_path,'boolean');first=Scripted([act('ask_researcher',question='Confirm?')]);first.model='first'
    ref=start(store,first,model,'boolean',max_calls=1 if defect=='budget' else 6,context_max_chars=8000)
    provider=Scripted([]);provider.model='other' if defect=='identity' else 'first'
    reply='r'*8001 if defect=='reply_size' else 'r'*8000 if defect=='context' else 'Proceed.'
    before=store.list()
    with pytest.raises(ValueError):agent.resume_agent(store,provider,ref,reply,respondent='Operator')
    assert provider.calls==0 and store.list()==before


def test_unconfirmed_provider_response_not_retried(tmp_path,monkeypatch):
    store,model=imported(tmp_path,'boolean')
    with pytest.raises(SystemExit):start(store,Scripted([SystemExit('lost response')]),model,'boolean')
    execution=store.list('agent_execution')[-1]
    assert recovery.diagnose_execution(store,execution)['status']=='blocked'


def test_old_pause_cannot_open_a_second_continuation_branch(tmp_path):
    store,model=imported(tmp_path,'boolean')
    paused=start(store,Scripted([act('ask_researcher',question='Confirm?')]),model,'boolean')
    result=agent.resume_agent(store,Scripted(['run_typed','finish']),paused,'Proceed.',respondent='Operator')
    assert store.get(result)['payload']['state']=='completed'
    before=store.list();provider=Scripted([])
    with pytest.raises(ValueError,match='already_continued'):
        agent.resume_agent(store,provider,paused,'Proceed again.',respondent='Operator')
    assert not provider.calls and store.list()==before


def test_interrupted_continuation_must_recover_instead_of_resuming_again(tmp_path):
    store,model=imported(tmp_path,'boolean')
    paused=start(store,Scripted([act('ask_researcher',question='Confirm?')]),model,'boolean')
    with pytest.raises(SystemExit):agent.resume_agent(store,Scripted([SystemExit('unknown response')]),paused,'Proceed.',respondent='Operator')
    before=store.list();provider=Scripted([])
    with pytest.raises(ValueError,match='already_continued'):
        agent.resume_agent(store,provider,paused,'Proceed.',respondent='Operator')
    assert store.list()==before and not provider.calls


def test_context_retains_confirmed_native_result_after_later_messages():
    from biomodel_agent.context import essential_indices
    messages=[dict(role='system',content='policy'),dict(role='user',content='task'),
        dict(role='assistant',content=json.dumps(act('run_typed'))),
        dict(role='user',content=json.dumps(dict(tool='run_typed',ok=True,status='succeeded'))),
        dict(role='assistant',content=json.dumps(act('inspect_typed'))),
        dict(role='user',content=json.dumps(dict(tool='inspect_typed',ok=True))),
        dict(role='user',content='latest')]
    assert {3,5}<=essential_indices(messages)
