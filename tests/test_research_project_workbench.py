"""Project HTTP entrypoint, scoped model-service consent and attributable discussion."""
import json
from uuid import uuid4
import pytest

from biomodel_agent import research_projects as projects
from biomodel_agent.workbench import Workbench,OPERATION
from test_fba import Scripted,act
from test_research_projects import frame,step
from test_workbench import app,web,request,submit,wait


def config():return dict(scenario='A human supplies an application scenario.',policy=dict(planner_max_calls=10))


def test_projects_http_page_and_initial_state_have_no_scientific_side_effects(web):
    before=web.workbench.store.list()
    status,body,headers=request(web,'/projects')
    assert status==200 and b'__WORKBENCH_TOKEN__' not in body and 'no-store' in headers['Cache-Control']
    status,body,_=request(web,'/projects.js');assert status==200 and b'eval(' not in body
    assert web.workbench.store.list()==before
    created=submit(web.workbench,'create_project',dict(config=config()))
    ref=created['result']['project_ref'];status,raw,_=request(web,'/api/project/'+ref);view=json.loads(raw)
    assert status==200 and view['frame_ref'] is None and not view['iteration_refs']
    assert any(x['project_ref']==ref for x in web.workbench.overview()['projects'])
    assert not web.workbench.store.list('typed_run')


def test_disabled_project_model_service_cannot_be_enabled_by_a_consent_field(app):
    ref=submit(app,'create_project',dict(config=config()))['result']['project_ref']
    params=dict(project_ref=ref);preview=app.preview('run_project',params);before=app.store.list()
    assert preview['requires_provider'] and not preview['provider_enabled']
    with pytest.raises(ValueError):app.submit(uuid4().hex,'run_project',params,provider_consent=True,scope_sha256=preview['scope_sha256'])
    assert app.store.list()==before and not projects.rows(app.store,projects.RESPONSE,ref)


def test_project_creation_preserves_client_operation_key_and_can_be_found_without_reposting(app):
    key=uuid4().hex;params=dict(config=config())
    queued=app.submit(key,'create_project',params);assert queued['key']==key
    done=wait(app,queued);assert done['key']==key and done['state']=='returned'
    assert app.job_by_key(key)['job_ref']==queued['job_ref']
    assert app.submit(key,'create_project',params)['job_ref']==queued['job_ref']
    assert len(app.store.list(projects.PROJECT))==1


def test_project_model_consent_scope_tracks_frame_and_idempotency_does_not_repeat_model_call(tmp_path):
    providers=[]
    def factory(_):
        model=Scripted([act('conclude',outcome='insufficient_evidence',summary='Need observations before a model can answer this question.',
            assessment_refs=[],limitations=['Synthetic control.'])]);providers.append(model);return model
    app=Workbench(tmp_path/'s',allow_provider=True,base_url='https://invalid.example/v1',model='scripted-control',provider_factory=factory)
    try:
        ref=submit(app,'create_project',dict(config=config()))['result']['project_ref'];params=dict(project_ref=ref)
        stale=app.preview('run_project',params)
        step(app.store,ref,'revise_frame',frame=frame(),reason='A changed research frame changes disclosure scope.')
        count=len(app.store.list(OPERATION))
        with pytest.raises(ValueError):app.submit(uuid4().hex,'run_project',params,provider_consent=True,scope_sha256=stale['scope_sha256'])
        assert len(app.store.list(OPERATION))==count and not providers
        fresh=app.preview('run_project',params);key=uuid4().hex
        kwargs=dict(provider_consent=True,scope_sha256=fresh['scope_sha256'])
        job=wait(app,app.submit(key,'run_project',params,**kwargs));assert job['state']=='returned'
        assert app.submit(key,'run_project',params,**kwargs)['job_ref']==job['job_ref']
        assert len(providers)==1 and len(providers[0].requests)==1
        assert projects.inspect(app.readonly,ref)['state']=='insufficient_evidence'
    finally:app.close()


def test_project_human_reply_is_scoped_attributed_and_survives_export(app):
    ref=submit(app,'create_project',dict(config=config()))['result']['project_ref']
    question=step(app.store,ref,'ask_alignment',category='granularity',question='Population or individual behavior?',affected_fields=['granularity'])['question_ref']
    params=dict(project_ref=ref,question_ref=question,reply='Population behavior.',respondent='Researcher')
    answer=submit(app,'reply_project',params);assert answer['state']=='returned'
    saved=app.store.get(answer['result']['reply_ref'])['payload'];assert saved['respondent']=='Researcher'
    assert projects.inspect(app.readonly,ref)['state']=='ready'
    frozen=submit(app,'snapshot_project',dict(project_ref=ref));assert frozen['state']=='returned'
    exported=submit(app,'export_bundle',dict(ref=frozen['result']['snapshot_ref']));assert exported['state']=='returned'
    assert not app.store.list('typed_run')


def test_project_read_rechecks_before_publishing_and_next_request_is_fresh(web,monkeypatch):
    from biomodel_agent import read_views
    ref=submit(web.workbench,'create_project',dict(config=config()))['result']['project_ref']
    status,body,_=request(web,'/api/project/'+ref);assert status==200
    path=web.workbench.store.objects_dir/(ref+'.json');original_bytes=path.read_bytes()
    original=read_views._ReadView._revalidate
    def tamper(view):
        path.write_bytes(original_bytes.replace(b'A human',b'B human'))
        return original(view)
    with monkeypatch.context() as context:
        context.setattr(read_views._ReadView,'_revalidate',tamper)
        status,body,_=request(web,'/api/project/'+ref)
        assert status==409 and 'iteration_refs' not in json.loads(body)
    path.write_bytes(original_bytes)
    question=step(web.workbench.store,ref,'ask_alignment',category='granularity',question='Choose granularity.',affected_fields=['granularity'])['question_ref']
    status,body,_=request(web,'/api/project/'+ref)
    assert status==200 and json.loads(body)['pending_questions']==[question]


@pytest.mark.parametrize('separator',['\n','\r\n','\r\n\n'])
def test_uploaded_source_keeps_exact_utf8_and_newlines(app,separator):
    content='注释😀'+separator+'Initial A = False.'+separator
    uploaded=submit(app,'import_source',dict(filename='source.txt',content=content))
    source_ref=uploaded['result']['source_ref'];record=app.store.get(source_ref)['payload']
    assert app.store.get_blob(record['blob_ref'])==content.encode('utf-8')
    start=content.index('Initial A = False.')
    view=app.source(source_ref,start=start,end=start+len('Initial A = False.'))
    assert view['quote']=='Initial A = False.'


def test_completion_after_terminal_scan_does_not_publish_resultless_success(app,monkeypatch):
    import threading,time
    from biomodel_agent.workbench import TERMINAL
    entered=threading.Event();release=threading.Event();original_perform=app._perform
    def blocked(operation,params):
        entered.set();assert release.wait(20)
        return original_perform(operation,params)
    monkeypatch.setattr(app,'_perform',blocked)
    queued=app.submit(uuid4().hex,'create_project',dict(config=config()))
    assert entered.wait(5)
    original_rows=app._rows;triggered=False
    def rows(kind,**matches):
        nonlocal triggered
        found=original_rows(kind,**matches)
        if kind==TERMINAL and not triggered:
            triggered=True;release.set();deadline=time.monotonic()+20
            while app._states[queued['job_ref']]['state'] not in ('returned','failed') and time.monotonic()<deadline:time.sleep(.01)
            assert app._states[queued['job_ref']]['state']=='returned'
        return found
    monkeypatch.setattr(app,'_rows',rows)
    try:
        racing=app.job(queued['job_ref'])
        assert racing['state'] not in ('returned','failed') or 'terminal_ref' in racing
        final=app.job(queued['job_ref'])
        assert final['state']=='returned' and final['result']['project_ref'] and final['terminal_ref']
    finally:release.set()


def test_memory_completion_without_confirmed_terminal_remains_unknown(app,monkeypatch):
    from biomodel_agent.workbench import TERMINAL
    complete=submit(app,'create_project',dict(config=config()));original=app._rows
    monkeypatch.setattr(app,'_rows',lambda kind,**matches:[] if kind==TERMINAL else original(kind,**matches))
    unknown=app.job(complete['job_ref'])
    assert unknown['state']=='unknown_effects' and unknown['result'] is None


@pytest.mark.parametrize('size',[8192,65536])
def test_rejected_post_with_body_returns_403_without_creating_jobs(web,size):
    for _ in range(4):
        status,body,_=request(web,'/api/jobs',raw=b'x'*size,headers={'X-Workbench-Token':'wrong'})
        assert status==403
    assert not web.workbench.store.list(OPERATION)


def test_rejection_does_not_wait_for_complete_request_body(web):
    import http.client
    connection=http.client.HTTPConnection('127.0.0.1',web.server_port,timeout=2)
    try:
        connection.putrequest('POST','/api/jobs')
        connection.putheader('Origin',web.origin)
        connection.putheader('X-Workbench-Token','wrong')
        connection.putheader('Content-Length','65536')
        connection.putheader('Content-Type','application/json')
        connection.endheaders();connection.send(b'x')
        response=connection.getresponse();response.read();assert response.status==403
    finally:connection.close()
    status,_,_=request(web,'/api/overview')
    assert status==200 and not web.workbench.store.list(OPERATION)


def test_rejected_slow_body_has_a_total_time_limit(web,monkeypatch):
    import socket,threading,time
    from biomodel_agent.workbench_server import Handler
    finished=threading.Event();stop=threading.Event();original=Handler._discard_rejected_body
    def observed(handler):
        try:return original(handler)
        finally:finished.set()
    monkeypatch.setattr(Handler,'_discard_rejected_body',observed)
    connection=socket.create_connection(('127.0.0.1',web.server_port),timeout=2)
    head=('POST /api/jobs HTTP/1.1\r\nHost: '+web.authority+'\r\nX-Workbench-Token: wrong\r\nContent-Length: 65536\r\n\r\n')
    connection.sendall(head.encode()+b'x')
    def trickle():
        for _ in range(50):
            if stop.wait(.1):return
            try:connection.sendall(b'x')
            except OSError:return
    sender=threading.Thread(target=trickle,daemon=True);sender.start()
    try:
        assert b'403' in connection.recv(1024)
        assert finished.wait(2),'rejected slow input must not extend the cleanup deadline'
        assert not web.workbench.store.list(OPERATION)
    finally:stop.set();connection.close();sender.join(2)
