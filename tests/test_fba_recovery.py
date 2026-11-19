"""FBA v2 checkpoints/recovery, real LPs and synthetic providers only."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import agent, bundles, cli, context, fba, fba_agent, fba_agent_runtime as runtime, recovery, reporting
from biomodel_agent.storage import ArtifactStore
from test_fba import imported, Scripted, act


def start(store, provider, model, **kwargs):
    return agent.run_model_agent(store, provider, model_type='fba', model_ref=model, recoverable=True, **kwargs)


def execution(store): return store.list('agent_execution')[-1]


def test_v2_pause_resume_preserves_lp_source_budget_and_report(tmp_path):
    store, model = imported(tmp_path); original=store.get(model)
    paused=start(store,Scripted([act('ask_researcher',question='Proceed with the frozen objective?')]),model)
    p=store.get(paused)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==1 and p['run_count']==0
    provider=Scripted([act('inspect_fba'),act('run_fba'),act('finish')])
    ref=agent.resume_agent(store,provider,paused,'Keep the frozen LP; do not change bounds.',respondent='Synthetic operator')
    result=store.get(ref)['payload']
    assert result['state']=='completed' and result['call_count']==4 and result['run_count']==1
    assert len(provider.requests)==3 and fba.summary(store,result['run_ref'])['objective']==10.
    assert store.get(model)==original and not store.list('source')
    assert len(result['clarification_refs'])==1 and result['parent_run_ref']==paused
    assert recovery.diagnose_execution(store,result['execution_ref'])['status']=='finished'
    report=reporting.render_report(store,ref)
    assert 'fba_agent_v2' in report and 'Remaining provider calls: 2' in report and 'Synthetic operator' in report
    assert 'Keep the frozen LP' in report and 'Scientific support: **unknown**' in report


def test_reply_cannot_change_bounds_or_create_another_scientific_budget(tmp_path,monkeypatch):
    store,model=imported(tmp_path)
    paused=start(store,Scripted([act('run_fba'),act('ask_researcher',question='How should the result be interpreted?')]),model)
    before=store.list('fba_execution')
    monkeypatch.setattr(fba,'run',lambda *a,**k:pytest.fail('LP repeated'))
    provider=Scripted([act('run_fba')])
    ref=agent.resume_agent(store,provider,paused,'Set uptake upper bound to 1000.',respondent='Synthetic operator')
    p=store.get(ref)['payload']
    assert p['state']=='incomplete' and p['run_count']==1 and p['call_count']==3
    assert store.list('fba_execution')==before and fba.summary(store,p['run_ref'])['objective']==10.


@pytest.mark.parametrize('defect',['budget','provider','reply_size','context'])
def test_resume_preflights_fail_before_reply_write_or_provider_call(tmp_path,defect):
    store,model=imported(tmp_path)
    first=Scripted([act('ask_researcher',question='Confirm the fixed protocol?')]);first.model='model-a'
    paused=start(store,first,model,max_calls=1 if defect=='budget' else 6,context_max_chars=8000)
    provider=Scripted([]);provider.model='model-b' if defect=='provider' else 'model-a'
    reply='r'*8001 if defect=='reply_size' else 'r'*8000 if defect=='context' else 'Proceed.'
    before=store.list()
    with pytest.raises(ValueError):agent.resume_agent(store,provider,paused,reply,respondent='Operator')
    assert store.list()==before and not provider.requests


def test_returned_lp_is_recovered_without_solving_again(tmp_path,monkeypatch):
    store,model=imported(tmp_path); original=Scripted([act('run_fba')])
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('after returned journal record')))
        with pytest.raises(SystemExit):start(store,original,model)
    ref=execution(store); report=recovery.diagnose_execution(store,ref)
    assert report['status']=='ready',report
    assert report['call_count']==report['run_count']==1
    assert report['adoptable_result_refs']==store.list('fba_run')
    monkeypatch.setattr(fba,'run',lambda *a,**k:pytest.fail('LP rerun on recovery'))
    provider=Scripted([act('finish')])
    root=agent.recover_agent(store,provider,ref); p=store.get(root)['payload']
    assert p['state']=='completed' and p['call_count']==2 and p['run_count']==1
    assert len(original.requests)==len(provider.requests)==1
    assert len(store.list('fba_execution'))==1 and len(p['recovery_refs'])==1
    assert recovery.diagnose_execution(store,ref)['status']=='finished'


def test_returned_pause_response_can_recover_at_exhausted_call_budget(tmp_path,monkeypatch):
    store,model=imported(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'_final',lambda *a,**k:(_ for _ in ()).throw(SystemExit('before final')))
        with pytest.raises(SystemExit):start(store,Scripted([act('ask_researcher',question='Confirm?')]),model,max_calls=1)
    ref=execution(store);provider=Scripted([])
    assert recovery.diagnose_execution(store,ref)['status']=='ready'
    root=agent.recover_agent(store,provider,ref); p=store.get(root)['payload']
    assert p['state']=='awaiting_input' and p['call_count']==1 and p['run_count']==0 and not provider.requests


def test_only_unreturned_read_is_repeated_and_original_counters_are_kept(tmp_path,monkeypatch):
    store,model=imported(tmp_path);put=store.put
    def fault(kind,payload,refs=None):
        ref=put(kind,payload,refs)
        if kind=='agent_invocation' and payload.get('name')=='inspect_fba' and payload.get('phase')=='reserved':
            raise SystemExit('read reserved')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',fault)
        with pytest.raises(SystemExit):start(store,Scripted([act('inspect_fba')]),model)
    ref=execution(store);report=recovery.diagnose_execution(store,ref)
    assert report['status']=='ready' and report['operations'][-1]['retry_read_only']
    provider=Scripted([act('ask_researcher',question='Continue?')])
    root=agent.recover_agent(store,provider,ref)
    assert store.get(root)['payload']['call_count']==2 and not store.list('fba_execution')
    assert len([store.get(r) for r in store.list('agent_invocation') if store.get(r)['payload'].get('name')=='inspect_fba'])==2


@pytest.mark.parametrize('where',['before_lp','after_lp'])
def test_unknown_scientific_effects_block_recovery_without_writes_or_calls(tmp_path,monkeypatch,where):
    store,model=imported(tmp_path); put=store.put; run=fba.run
    if where=='before_lp':
        def fault(kind,payload,refs=None):
            ref=put(kind,payload,refs)
            if kind=='agent_invocation' and payload.get('name')=='run_fba' and payload.get('phase')=='reserved':raise SystemExit('reserved')
            return ref
        with monkeypatch.context() as patch:
            patch.setattr(store,'put',fault)
            with pytest.raises(SystemExit):start(store,Scripted([act('run_fba')]),model)
    else:
        def fault(*a,**k):run(*a,**k);raise SystemExit('unconfirmed return')
        with monkeypatch.context() as patch:
            patch.setattr(fba,'run',fault)
            with pytest.raises(SystemExit):start(store,Scripted([act('run_fba')]),model)
    ref=execution(store);report=recovery.diagnose_execution(store,ref)
    assert report['status']=='blocked' and report['run_count']==1
    before=store.list();provider=Scripted([])
    with pytest.raises(recovery.RecoveryBlocked):agent.recover_agent(store,provider,ref)
    assert store.list()==before and not provider.requests
    assert len(store.list('fba_run'))==(0 if where=='before_lp' else 1)


def test_paused_export_resumes_and_recovery_of_resumed_run_preserves_initial_snapshot(tmp_path,monkeypatch):
    store,model=imported(tmp_path)
    parent=start(store,Scripted([act('ask_researcher',question='Proceed with this LP?')]),model)
    target=tmp_path/'bundle';bundles.export_bundle(store,parent,target);opened=ArtifactStore(target)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('saved returned LP')))
        with pytest.raises(SystemExit):agent.resume_agent(opened,Scripted([act('run_fba')]),parent,'Proceed.',respondent='Fixture')
    ref=execution(opened);report=recovery.diagnose_execution(opened,ref)
    assert report['status']=='ready',report
    assert report['call_count']==2 and report['run_count']==1
    monkeypatch.setattr(fba,'run',lambda *a,**k:pytest.fail('resumed LP repeated'))
    root=agent.recover_agent(opened,Scripted([act('finish')]),ref)
    p=opened.get(root)['payload']
    assert p['state']=='completed' and p['call_count']==3 and len(p['clarification_refs'])==1


@pytest.mark.parametrize('mutation',['model','settings','environment'])
def test_foreign_or_changed_scientific_return_cannot_be_adopted(tmp_path,monkeypatch,mutation):
    store,model=imported(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('returned LP')))
        with pytest.raises(SystemExit):start(store,Scripted([act('run_fba')]),model)
    ref=execution(store)
    term=next(r for r in store.list('agent_invocation') if store.get(r)['payload'].get('name')=='run_fba' and store.get(r)['payload'].get('phase')=='returned')
    obj=store.get(term); root=obj['payload']['details']['result_ref'];result=store.get(root)
    if mutation=='model':result['payload']['model_ref']='0'*64
    else:
        inp=store.get(result['payload']['input_ref'])
        if mutation=='settings':inp['payload']['settings']['method']='highs-ipm'
        else:inp['payload']['environment']['python']='changed'
        bad_input=store.put('fba_input',inp['payload'],refs=inp['refs'])
        old=result['payload']['input_ref'];result['payload']['input_ref']=bad_input
        result['refs']=[bad_input if r==old else r for r in result['refs']]
    bad=store.put('fba_run',result['payload'],refs=result['refs'])
    obj['payload']['details']['result_ref']=bad;obj['refs']=[bad if r==root else r for r in obj['refs']]
    # Replace the terminal inventory only in this isolated test store.
    store.put('agent_invocation',obj['payload'],refs=obj['refs'])
    inventory=recovery._inventory(store)
    inventory.pop(term)
    monkeypatch.setattr(recovery,'_inventory',lambda *_:inventory)
    report=recovery.diagnose_execution(store,ref)
    assert report['status']=='blocked' and 'ambiguous_operation_phases' not in report['reasons']


def test_v2_failure_and_legacy_records_stay_honest(tmp_path):
    store,model=imported(tmp_path)
    root=start(store,Scripted([act('run_fba')]),model,settings=dict(reference=dict(objective=99.,source='wrong synthetic control')))
    p=store.get(root)['payload']; assert p['state']=='incomplete' and p['run_count']==1
    assert fba.summary(store,p['run_ref'])['comparison']['status']=='fail'
    legacy=fba_agent.run_agent(store,Scripted([act('finish')]),model)
    report=recovery.diagnose_execution(store,store.get(legacy)['payload']['execution_ref'])
    assert report['status']=='blocked' and report['run_count'] is None
    with pytest.raises(ValueError):agent.resume_agent(store,Scripted([]),legacy,'Proceed.',respondent='Fixture')


def test_cli_status_and_readonly_diagnosis_do_not_repair_index(tmp_path,capsys):
    store,model=imported(tmp_path)
    paused=start(store,Scripted([act('ask_researcher',question='Confirm?')]),model)
    ref=store.get(paused)['payload']['execution_ref'];before=store.index_path.read_bytes()
    report=recovery.diagnose_execution(recovery.open_readonly_store(store.root),ref)
    assert report['status']=='finished' and store.index_path.read_bytes()==before
    assert cli.main(['agent-recovery-status',ref,'--store',str(store.root)])==0
    assert json.loads(capsys.readouterr().out)['run_count']==0
    with pytest.raises(SystemExit) as exc:cli.main(['agent-fba','--help'])
    assert exc.value.code==0 and '--recoverable' in capsys.readouterr().out


def test_v2_provider_failure_never_retries_or_stores_raw_exception(tmp_path):
    store,model=imported(tmp_path);provider=Scripted([RuntimeError('private provider diagnostic')])
    root=start(store,provider,model);p=store.get(root)['payload']
    assert p['state']=='incomplete' and p['call_count']==1 and p['run_count']==0
    assert len(provider.requests)==1 and 'private provider diagnostic' not in json.dumps(p)
    assert recovery.diagnose_execution(store,p['execution_ref'])['status']=='blocked'


def test_generic_cli_resume_supports_new_fba_protocol_without_real_service(tmp_path,monkeypatch,capsys):
    from biomodel_agent import llm
    store,model=imported(tmp_path)
    def wire(actions):
        provider=Scripted(actions);provider.base_url='https://synthetic.invalid/v1';provider.model='synthetic'
        provider.timeout_s=15.;provider.max_tokens=1000;provider.json_mode=False
        return provider
    parent=start(store,wire([act('ask_researcher',question='Proceed?')]),model)
    continued=wire([act('run_fba'),act('finish')])
    monkeypatch.setattr(llm,'ChatProvider',lambda *a,**k:continued)
    monkeypatch.setenv('BIOMODEL_API_KEY','synthetic-test-placeholder-only')
    path=tmp_path/'reply.txt';path.write_text('Continue the frozen LP.')
    assert cli.main(['agent-resume',parent,'--store',str(store.root),'--reply-file',str(path),
        '--respondent','Fixture','--base-url','https://synthetic.invalid/v1'])==0
    out=json.loads(capsys.readouterr().out)
    assert out['mode']=='fba' and out['state']=='completed' and len(continued.requests)==2
