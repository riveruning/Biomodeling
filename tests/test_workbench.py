"""Local UI adapter controls over real workflows, with no live providers."""
import gc
import http.client
import io
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
import zipfile

import pytest

from biomodel_agent import research_tasks as tasks, task_proposals as proposals
from biomodel_agent.workbench import MAX_BODY, OPERATION, Workbench
from biomodel_agent.workbench_server import Server
from test_fba import Scripted, act
from test_research_tasks import setup as native_setup
from test_task_proposals import setup as proposal_setup


def wait(app,job,timeout=60):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        view=app.job(job['job_ref'])
        if view['state'] not in ('queued','running'):return view
        time.sleep(.02)
    raise AssertionError('workbench job did not settle')


def submit(app,operation,params,*,service=False,key=None):
    kwargs={}
    if service:
        preview=app.preview(operation,params);kwargs=dict(provider_consent=True,scope_sha256=preview['scope_sha256'])
    return wait(app,app.submit(key or uuid4().hex,operation,params,**kwargs))


def factory(actions,requests):
    def make(identity):
        provider=Scripted(actions.pop(0));requests.append(provider);return provider
    return make


@pytest.fixture
def app(tmp_path):
    wb=Workbench(tmp_path/'store')
    try:yield wb
    finally:wb.close()


@pytest.fixture
def web(app):
    server=Server(app);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(2.)


def request(server,path,*,body=None,headers=None,raw=None):
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10.)
    args={'X-Workbench-Token':server.workbench.token}
    if body is not None or raw is not None:args['Content-Type']='application/json'
    args.update(headers or {})
    try:
        connection.request('POST' if body is not None or raw is not None else 'GET',path,
            body=raw if raw is not None else json.dumps(body).encode() if body is not None else None,headers=args)
        response=connection.getresponse();return response.status,response.read(),dict(response.getheaders())
    finally:connection.close()


def test_empty_workbench_read_paths_do_not_create_tasks_or_change_index(app):
    before=app.store.index_path.read_bytes();objects=app.store.list()
    for _ in range(3):
        assert app.overview()['requests']==[] and not app.overview()['provider']['enabled']
        gc.collect()
    assert app.store.list()==objects and app.store.index_path.read_bytes()==before


def test_same_store_cannot_have_two_workbench_owners(app):
    with pytest.raises(ValueError):Workbench(app.store.root)


@pytest.mark.parametrize('kind',tasks.TYPES)
def test_all_six_native_workflows_execute_through_journaled_workbench_once(tmp_path,kind):
    store,config,actions=native_setup(tmp_path,kind);providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',provider_factory=factory([actions],providers))
    try:
        created=submit(wb,'create_task',{'config':config});assert created['state']=='returned'
        r=created['result']['request_ref'];params={'request_ref':r};key=uuid4().hex
        before=store.list();preview=wb.preview('run_task',params)
        assert preview['requires_provider'] and not providers and store.list()==before
        job=submit(wb,'run_task',params,service=True,key=key)
        assert job['state']=='returned',job
        result=tasks.result(store,job['result']['result_ref'])
        assert result['state']=='completed' and result['run_count']==1
        again=wb.submit(key,'run_task',params)
        assert again['job_ref']==job['job_ref'] and len(providers)==1
        assert len(providers[0].requests)==len(actions)
        view=wb.task(r)
        assert view['status']['state']=='completed' and view['timeline']
        assert 'unknown' in wb.report(result['request_ref'])
        exported=submit(wb,'export_bundle',{'ref':job['result']['result_ref']})
        data=wb.bundle(exported['job_ref'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest=json.loads(z.read('bundle/bundle.json'))
            assert manifest['root_ref']==job['result']['result_ref']
            assert all(not x.startswith(('/','../')) for x in z.namelist())
    finally:wb.close()


def test_propose_select_then_native_run_keeps_separate_budgets_and_evidence(tmp_path):
    store,r,spec=proposal_setup(tmp_path);providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',
        provider_factory=factory([[act('propose_task',proposal=spec)],[act('run_typed'),act('finish')]],providers))
    try:
        proposed=submit(wb,'propose_task',{'request_ref':r},service=True)
        assert proposed['state']=='returned' and not store.list('typed_run')
        pref=proposed['result']['proposal_ref'];view=wb.task(r);candidate=view['proposals'][0]['candidates'][0]
        assert candidate['draft_adoptable'] and view['proposal_attempts'][0]['confirmed_return']
        selected=submit(wb,'select_proposal',dict(proposal_ref=pref,candidate_id='direct',reviewer='Local operator',reason='Select exact source statements.',acknowledged_unknowns=[]))
        selected_ref=selected['result']['request_ref'];assert not store.list('typed_run')
        selected_view=wb.task(selected_ref)
        assert selected_view['selection']['proposal_ref']==pref
        assert selected_view['proposals'][0]['request_ref']==r and selected_view['proposals'][0]['evidence']
        ran=submit(wb,'run_task',{'request_ref':selected_ref},service=True)
        assert ran['state']=='returned'
        status=wb.task(selected_ref)['status'];assert (status['proposal_call_count'],status['native_call_count'],status['total_call_count'])==(1,2,3)
        anchor=view['proposals'][0]['evidence'][0]
        assert wb.source(anchor['source_ref'],page=anchor['page'],start=anchor['start'],end=anchor['end'])['quote']==anchor['quote']
        assert sum(len(p.requests) for p in providers)==3
    finally:wb.close()


def test_reply_resumes_same_native_task_with_cumulative_budget(tmp_path):
    store,config,_=native_setup(tmp_path,'boolean');providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',
        provider_factory=factory([[act('ask_researcher',question='Confirm this frozen network?')],[act('run_typed'),act('finish')]],providers))
    try:
        r=submit(wb,'create_task',{'config':config})['result']['request_ref']
        submit(wb,'run_task',{'request_ref':r},service=True)
        paused=wb.task(r)['status'];assert paused['pending_question']=='Confirm this frozen network?'
        params=dict(request_ref=r,reply='Keep the supplied frozen network.',respondent='Local operator')
        preview=wb.preview('resume_task',params);changed={**params,'reply':'A different reply.'}
        with pytest.raises(ValueError):wb.submit(uuid4().hex,'resume_task',changed,provider_consent=True,scope_sha256=preview['scope_sha256'])
        result=submit(wb,'resume_task',params,service=True);assert result['state']=='returned'
        final=wb.task(r)['status']
        assert final['native_task_ref']==paused['native_task_ref'] and final['native_call_count']==3 and final['run_count']==1
    finally:wb.close()


def test_missing_scope_or_disabled_provider_creates_no_reservation_and_no_provider(tmp_path):
    store,config,_=native_setup(tmp_path,'boolean');r=tasks.prepare(store,config);wb=Workbench(store.root)
    try:
        before=store.list();preview=wb.preview('run_task',{'request_ref':r});assert not preview['provider_enabled']
        with pytest.raises(ValueError):wb.submit(uuid4().hex,'run_task',{'request_ref':r},provider_consent=True,scope_sha256=preview['scope_sha256'])
        assert store.list()==before
    finally:wb.close()


@pytest.mark.parametrize('kwargs',[{}, {'provider_consent':True}, {'provider_consent':True,'scope_sha256':'0'*64}, {'provider_consent':'yes'}])
def test_online_action_requires_exact_preview_and_boolean_consent(tmp_path,kwargs):
    store,config,_=native_setup(tmp_path,'boolean');r=tasks.prepare(store,config);providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',provider_factory=factory([],providers))
    try:
        with pytest.raises(ValueError):wb.submit(uuid4().hex,'run_task',{'request_ref':r},**kwargs)
        assert not providers and not store.list(OPERATION)
    finally:wb.close()


def test_duplicate_key_cannot_change_contents(app):
    key=uuid4().hex;params={'config':{'schema_version':1,'goal':'Synthetic question.'}}
    job=submit(app,'create_task',params,key=key)
    assert app.submit(key,'create_task',params)['job_ref']==job['job_ref']
    with pytest.raises(ValueError):app.submit(key,'create_task',{'config':{'schema_version':1,'goal':'A different question.'}})
    assert len(app.store.list(tasks.REQUEST))==1


def test_unknown_operation_after_restart_is_never_replayed(tmp_path):
    wb=Workbench(tmp_path/'store');key=uuid4().hex;params={'config':{'schema_version':1,'goal':'Crash before returned receipt.'}}
    wb._perform=lambda *args:(_ for _ in ()).throw(KeyboardInterrupt())
    job=submit(wb,'create_task',params,key=key);assert job['state']=='unknown_effects';wb.close()
    restarted=Workbench(tmp_path/'store')
    try:
        assert restarted.job_by_key(key)['state']=='unknown_effects'
        assert restarted.submit(key,'create_task',params)['job_ref']==job['job_ref']
        assert not restarted.store.list(tasks.REQUEST) and len(restarted.store.list(OPERATION))==1
    finally:restarted.close()


def test_known_native_result_after_workbench_crash_can_be_adopted_without_provider(tmp_path):
    store,config,actions=native_setup(tmp_path,'boolean');providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',provider_factory=factory([actions],providers))
    r=submit(wb,'create_task',{'config':config})['result']['request_ref'];perform=wb._perform
    def crash(operation,params):
        result=perform(operation,params)
        if operation=='run_task':raise KeyboardInterrupt()
        return result
    wb._perform=crash
    job=submit(wb,'run_task',{'request_ref':r},service=True);assert job['state']=='unknown_effects';wb.close()
    restarted=Workbench(store.root)
    try:
        known=tasks.status(store,r)['result_ref']
        assert not restarted.preview('recover_task',{'request_ref':r})['requires_provider']
        recovered=submit(restarted,'recover_task',{'request_ref':r})
        assert recovered['result']['result_ref']==known and len(providers)==1 and len(providers[0].requests)==2
        with pytest.raises(ValueError):submit(restarted,'run_task',{'request_ref':r})
    finally:restarted.close()


def test_unconfirmed_proposal_return_remains_unknown_and_blocks_another_request(tmp_path):
    store,r,_=proposal_setup(tmp_path);providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',provider_factory=factory([[KeyboardInterrupt()]],providers))
    try:
        job=submit(wb,'propose_task',{'request_ref':r},service=True)
        assert job['state']=='unknown_effects' and wb.task(r)['status']['state']=='needs_proposal_recovery'
        assert not wb.task(r)['proposal_attempts'][0]['confirmed_return']
        with pytest.raises(ValueError):wb.preview('propose_task',{'request_ref':r})
        assert len(providers)==1 and len(providers[0].requests)==1
    finally:wb.close()


def test_same_family_operations_cannot_overlap(tmp_path):
    store,config,_=native_setup(tmp_path,'boolean');r=tasks.prepare(store,config)
    entered=threading.Event();finish=threading.Event();wb=Workbench(store.root);perform=wb._perform
    def slow(op,params):entered.set();finish.wait(10.);return perform(op,params)
    wb._perform=slow
    try:
        config['goal']='First explicit revision.'
        one=wb.submit(uuid4().hex,'create_task',dict(config=config,parent_ref=r,reason='Clarify goal.'));assert entered.wait(3.)
        with pytest.raises(ValueError):wb.submit(uuid4().hex,'create_task',dict(config=config,parent_ref=r,reason='Second overlapping revision.'))
        finish.set();assert wait(wb,one)['state']=='returned'
    finally:finish.set();wb.close()


@pytest.mark.parametrize('filename',['../private.txt','C:\\private.txt','/private.txt','notes.pdf','a\n.txt'])
def test_upload_never_reads_arbitrary_local_paths(app,filename):
    with pytest.raises(ValueError):app.submit(uuid4().hex,'import_source',dict(filename=filename,content='known synthetic text'))
    assert not app.store.list(OPERATION)


def test_utf8_source_import_and_unicode_offsets_are_preserved(app):
    content='注释😀 <img src=x onerror=alert(1)>\nInitial A = False.\n'
    job=submit(app,'import_source',dict(filename='network.txt',content=content))
    source_ref=job['result']['source_ref'];quote='Initial A = False.';start=content.index(quote)
    view=app.source(source_ref,start=start,end=start+len(quote))
    assert view['quote']==quote and '😀' in view['before']
    assert app.store.get_blob(app.store.get(source_ref)['payload']['blob_ref'])==content.encode('utf-8')


@pytest.mark.parametrize('kind',['fba','stochastic','pde','boolean','individual'])
def test_native_json_upload_preserves_typed_origin_and_profile(app,kind):
    root=Path(__file__).parents[1];path=root/'examples/fba-v1/chain.json' if kind=='fba' else root/f'examples/model-types/{kind}.json'
    job=submit(app,'import_model',dict(filename=path.name,content=path.read_text(),model_type=kind))
    assert job['state']=='returned',job
    model=app.store.get(job['result']['model_ref']);assert model['kind']==('fba_model' if kind=='fba' else 'typed_model')
    assert not app.store.list('typed_run') and not app.store.list('fba_run')


def test_provider_factory_exception_does_not_leak_credentials(tmp_path):
    store,config,_=native_setup(tmp_path,'boolean');r=tasks.prepare(store,config)
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',
        provider_factory=lambda _:(_ for _ in ()).throw(ValueError('sensitive-test-value')))
    try:
        job=submit(wb,'run_task',{'request_ref':r},service=True)
        assert job['state']=='failed' and 'sensitive-test-value' not in json.dumps(job)
        assert not store.list(tasks.EXECUTION)
    finally:wb.close()


def test_http_bootstrap_and_readonly_api_are_local_and_no_cache(web):
    code,body,headers=request(web,'/')
    assert code==200 and b'__WORKBENCH_TOKEN__' not in body and 'no-store' in headers['Cache-Control']
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    assert b'cdn.' not in body and b'onclick=' not in body
    before=web.workbench.store.index_path.read_bytes()
    code,raw,_=request(web,'/api/overview');assert code==200 and not json.loads(raw)['requests']
    assert web.workbench.store.index_path.read_bytes()==before


@pytest.mark.parametrize('headers',[{'Host':'evil.example'}, {'Origin':'https://evil.example'}, {'Sec-Fetch-Site':'cross-site'}, {'X-Workbench-Token':'wrong'}])
def test_http_rejects_cross_site_rebinding_or_missing_session_without_mutations(web,headers):
    code,_,_=request(web,'/api/jobs',body=dict(key=uuid4().hex,operation='create_task',params={'config':{'schema_version':1,'goal':'Do not create.'}}),headers=headers)
    assert code==403 and not web.workbench.store.list(OPERATION)


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"value":NaN}',b'{"value":1e400}',b'\xff',b'['*42+b'0'+b']'*42])
def test_http_json_rejects_ambiguous_or_nonfinite_input_before_mutation(web,raw):
    code,_,_=request(web,'/api/jobs',raw=raw);assert code==409 and not web.workbench.store.list(OPERATION)


def test_json_editor_parser_does_not_silently_override_duplicate_fields(web):
    code,_,_=request(web,'/api/parse-json',body={'text':'{"rate":1,"rate":2}'});assert code==409
    code,raw,_=request(web,'/api/parse-json',body={'text':'{"rate":0.4}'})
    assert code==200 and json.loads(raw)['value']=={'rate':.4} and not web.workbench.store.list(OPERATION)


def test_body_limit_and_transfer_encoding_are_rejected_before_reading_or_saving(web):
    code,_,_=request(web,'/api/jobs',raw=b'{}',headers={'Content-Length':str(MAX_BODY+1)});assert code==413 and not web.workbench.store.list(OPERATION)
    code,_,_=request(web,'/api/jobs',raw=b'{}',headers={'Transfer-Encoding':'chunked'});assert code==409


def test_http_post_then_lookup_by_key_survives_refresh_and_idempotent_repost(web):
    key=uuid4().hex;body=dict(key=key,operation='create_task',params={'config':{'schema_version':1,'goal':'One local synthetic task.'}})
    code,raw,_=request(web,'/api/jobs',body=body);assert code==202
    first=json.loads(raw);assert wait(web.workbench,first)['state']=='returned'
    code,raw,_=request(web,'/api/job-key/'+key);assert code==200 and json.loads(raw)['job_ref']==first['job_ref']
    code,raw,_=request(web,'/api/jobs',body=body);assert code==202 and json.loads(raw)['job_ref']==first['job_ref']
    assert len(web.workbench.store.list(tasks.REQUEST))==1


@pytest.mark.parametrize('path',['/../../etc/passwd','/api/source/../../etc/passwd','/downloads/bundle/not-a-ref.zip','/api/task/'+'0'*64])
def test_unknown_paths_and_refs_never_serve_local_files(web,path):
    code,raw,_=request(web,path);assert code in (404,409) and b'root:' not in raw and not web.workbench.store.list(OPERATION)


def test_job_is_not_published_complete_before_terminal_index_commit(app,monkeypatch):
    landed=threading.Event();finish=threading.Event();original=app.store._ensure_index_row
    def slow(r,kind,payload):
        if kind=='workbench_operation_terminal':landed.set();finish.wait(5.)
        return original(r,kind,payload)
    monkeypatch.setattr(app.store,'_ensure_index_row',slow)
    try:
        job=app.submit(uuid4().hex,'create_task',{'config':{'schema_version':1,'goal':'Index publication control.'}})
        assert landed.wait(3.)
        assert app.job(job['job_ref'])['state']=='running'
        before=app.store.index_path.read_bytes();app.overview()
        assert app.store.index_path.read_bytes()==before
        finish.set();assert wait(app,job)['state']=='returned'
    finally:finish.set()


def test_confirmed_proposal_receipt_can_be_adopted_without_another_provider(tmp_path,monkeypatch):
    store,r,spec=proposal_setup(tmp_path);providers=[]
    wb=Workbench(store.root,allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',
        provider_factory=factory([[act('propose_task',proposal=spec)]],providers))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(proposals,'_save',lambda *a,**k:(_ for _ in ()).throw(KeyboardInterrupt()))
            job=submit(wb,'propose_task',{'request_ref':r},service=True)
        assert job['state']=='unknown_effects'
        attempt=wb.task(r)['proposal_attempts'][0]
        assert attempt['confirmed_return'] and not attempt['proposal_refs']
        adopted=submit(wb,'recover_proposal',{'attempt_ref':attempt['attempt_ref']})
        assert adopted['state']=='returned'
        assert wb.task(r)['proposals'][0]['candidates'][0]['draft_adoptable']
        assert len(providers)==1 and len(providers[0].requests)==1 and not store.list('typed_run')
    finally:wb.close()


def test_conflicting_rule_is_inspectable_but_selection_job_fails_without_science(tmp_path):
    from test_task_proposals import TEXT
    store,r,spec=proposal_setup(tmp_path,TEXT+'Rule A = B.\n');wb=Workbench(store.root)
    try:
        saved=submit(wb,'prepare_proposal',{'request_ref':r,'proposal':spec})
        assert saved['state']=='returned'
        pref=saved['result']['proposal_ref'];assert not wb.task(r)['proposals'][0]['candidates'][0]['draft_adoptable']
        selected=submit(wb,'select_proposal',dict(proposal_ref=pref,candidate_id='direct',reviewer='Operator',reason='Negative control.',acknowledged_unknowns=[]))
        assert selected['state']=='failed' and not store.list('typed_model') and not store.list('typed_run')
    finally:wb.close()


def test_stale_selected_task_does_not_hide_job_history(tmp_path,monkeypatch):
    from biomodel_agent import recovery
    store,r,spec=proposal_setup(tmp_path);wb=Workbench(store.root)
    try:
        pref=submit(wb,'prepare_proposal',{'request_ref':r,'proposal':spec})['result']['proposal_ref']
        selected=submit(wb,'select_proposal',dict(proposal_ref=pref,candidate_id='direct',reviewer='Operator',reason='Explicit selection.',acknowledged_unknowns=[]))
        before=store.list()
        monkeypatch.setattr(recovery,'implementation_fingerprint',lambda:'1'*64)
        overview=wb.overview()
        assert len(overview['jobs'])==2
        assert any(x['state']=='unavailable' for x in overview['requests'])
        assert wb.job(selected['job_ref'])['state']=='returned' and store.list()==before
    finally:wb.close()


def test_http_report_and_bundle_downloads_use_session_header(web):
    created=submit(web.workbench,'create_task',{'config':{'schema_version':1,'goal':'Synthetic readonly download control.'}})
    r=created['result']['request_ref']
    code,raw,headers=request(web,'/downloads/report/'+r+'.md')
    assert code==200 and b'unknown' in raw and 'attachment;' in headers['Content-Disposition']
    exported=submit(web.workbench,'export_bundle',{'ref':r})
    code,raw,headers=request(web,'/downloads/bundle/'+exported['job_ref']+'.zip')
    assert code==200 and raw.startswith(b'PK') and headers['Content-Type']=='application/zip'
    code,_,_=request(web,'/downloads/bundle/'+exported['job_ref']+'.zip',headers={'X-Workbench-Token':''})
    assert code==403


def test_task_revision_reservation_pins_existing_source_and_native_model(app):
    source=submit(app,'import_source',dict(filename='source.txt',content='Explicit synthetic input.'))['result']['source_ref']
    path=Path(__file__).parents[1]/'examples/model-types/boolean.json'
    model=submit(app,'import_model',dict(filename=path.name,content=path.read_text(),model_type='boolean'))['result']['model_ref']
    config=dict(schema_version=1,goal='Pin known inputs.',source_refs=[source],model_ref=model)
    job=submit(app,'create_task',{'config':config})
    assert {source,model}<=set(app.store.get(job['job_ref'])['refs'])


def test_terminal_index_failure_does_not_create_conflicting_failed_receipt(app,monkeypatch):
    original=app.store._ensure_index_row
    def fail(r,kind,payload):
        if kind=='workbench_operation_terminal':raise OSError('Synthetic index commit failure.')
        return original(r,kind,payload)
    monkeypatch.setattr(app.store,'_ensure_index_row',fail)
    job=submit(app,'create_task',{'config':{'schema_version':1,'goal':'Completed primary object, missing index row.'}})
    assert job['state']=='returned'
    assert len(tasks._records(app.readonly,'workbench_operation_terminal',operation_ref=job['job_ref']))==1
    assert len(app.store.list(tasks.REQUEST))==1


def test_top_level_link_to_home_is_allowed_but_cross_site_api_stays_blocked(web):
    headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
    code,_,_=request(web,'/',headers=headers)
    assert code==200
    code,_,_=request(web,'/api/overview',headers=headers)
    assert code==403


