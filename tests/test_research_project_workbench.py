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
