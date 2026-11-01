"""Public research entrypoints exercised through the production chat adapter."""
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from biomodel_agent import cli, research_pipeline as batch, research_projects as p
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench
from test_research_projects import opened, step
from test_workbench import app, web, request, wait


def adapter(monkeypatch, answers):
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY', 'offline-dummy')
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        assert len(requests) <= len(answers), 'Unexpected additional provider request'
        answer=answers[len(requests)-1]
        if callable(answer):answer=answer()
        return httpx.Response(200, json=dict(choices=[dict(message=dict(
            content=json.dumps(answer)))], usage=dict(total_tokens=12)))
    return ChatProvider('https://offline.invalid/v1', 'offline-control',
                        api_key_env='BIOMODEL_TEST_PROVIDER_KEY',
                        transport=httpx.MockTransport(handle)), requests


def conclude():
    return dict(action='conclude', args=dict(outcome='insufficient_evidence',
        summary='No measurements available.', assessment_refs=[], limitations=['Offline development control.']))


@pytest.mark.parametrize('reason', [None, '', 'Inspect attributed input first.'])
def test_real_adapter_executes_source_and_conclusion_with_reason(monkeypatch, tmp_path, reason):
    action=dict(action='scenario_source', args={})
    if reason is not None:action['reason']=reason
    provider, requests=adapter(monkeypatch, [action, conclude()])
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic scenario.',policy=dict(planner_max_calls=2)))
    supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert len(view['source_refs'])==1 and view['state']=='insufficient_evidence'
    assert len(requests)==2 and not [x for _,x in p.rows(store,p.STEP,root) if x['operation']=='invalid_response']
    receipt=p.rows(store,p.RESPONSE,root)[0][1]
    assert 'reason' in receipt['response']  # Preserve the actual adapter response.


def save_pending(monkeypatch, store, root, provider):
    original=p._operate_owned
    def interrupted(*args, **kwargs):
        result=original(*args,**kwargs)
        if args[2]=='respond':raise KeyboardInterrupt('after confirmed response')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(p,'_operate_owned',interrupted)
        with pytest.raises(KeyboardInterrupt):supervisor.run(store,provider,root)


def test_saved_response_binding_rejects_changed_action_or_args(monkeypatch,tmp_path):
    provider,requests=adapter(monkeypatch,[dict(action='scenario_source',args={},reason='Preserved reason')])
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
    save_pending(monkeypatch,store,root,provider)
    response=supervisor._pending_response(store,root)['response_ref']
    for operation,args in [('search_sources',dict(query='changed')),('scenario_source',dict(changed=True))]:
        with pytest.raises(ValueError,match='differs from the saved'):
            p.operate(store,root,operation,args,response_ref=response)
    supervisor.run(store,None,root)
    assert len(p.inspect(store,root)['source_refs'])==1 and len(requests)==1


def test_unknown_response_field_is_rejected_without_source_write(tmp_path):
    from biomodel_agent.action_response import normalize_action
    with pytest.raises(ValueError):normalize_action(dict(action='scenario_source',args={},extra='bad'))
    with pytest.raises(ValueError):normalize_action(dict(action='scenario_source',args={},reason=42))


def test_last_saved_response_can_advance_without_provider_consent(monkeypatch,tmp_path):
    provider,requests=adapter(monkeypatch,[dict(action='scenario_source',args={})])
    app=Workbench(tmp_path/'s')
    try:
        root=p.create(app.store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
        save_pending(monkeypatch,app.store,root,provider)
        view=p.inspect(app.readonly,root)
        assert view['continuation']['can_advance'] and view['continuation']['has_saved_response']
        assert not view['continuation']['requires_provider']
        preview=app.preview('run_project',dict(project_ref=root));assert not preview['requires_provider']
        job=wait(app,app.submit(uuid4().hex,'run_project',dict(project_ref=root)))
        assert job['state']=='returned' and len(p.inspect(app.readonly,root)['source_refs'])==1
        assert len(requests)==1
        assert not p.inspect(app.readonly,root)['continuation']['can_advance']
    finally:app.close()


def test_saved_native_action_still_requires_service_and_consent(monkeypatch,tmp_path):
    store,root,iteration=opened(tmp_path/'native',planner_max_calls=1)
    provider,_=adapter(monkeypatch,[dict(action='run_iteration',args=dict(iteration_ref=iteration))])
    save_pending(monkeypatch,store,root,provider)
    app=Workbench(store.root)
    try:
        # Legacy inner agent remains a separate paid capability after planner exhaustion.
        view=p.inspect(store,root)
        assert view['continuation']['requires_native_provider']
        assert view['continuation']['requires_provider']
        assert app.preview('run_project',dict(project_ref=root))['requires_provider']
        with pytest.raises(ValueError,match='启用模型服务'):
            app.submit(uuid4().hex,'run_project',dict(project_ref=root))
    finally:app.close()


def batch_config():
    return dict(name='Adapter integration',lessons_max_calls=1,cases=[dict(id='first',
        paper_id='synthetic:adapter',family='adapter_control',material_kind='synthetic_control',
        project=dict(scenario='No measurements.',mode='reproduction',policy=dict(planner_max_calls=1)))])


def lesson():
    return dict(statement='Inspect source availability.',case_ids=['first'],
                conditions='Development controls.',validation_plan='Test separate families.')


def test_batch_and_lessons_use_real_adapter_and_reuse_saved_response(monkeypatch,tmp_path):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,batch_config())
    provider,requests=adapter(monkeypatch,[conclude(),dict(action='propose_lessons',args=dict(lessons=[lesson()]))])
    batch.run(store,root,lambda case:provider)
    assert batch.inspect(store,root)['cases'][0]['state']=='insufficient_evidence'
    refs=batch.lessons(store,root,provider)
    assert len(refs)==1 and not store.get(refs[0])['payload']['active']
    assert batch.lessons(store,root,None)==refs and len(requests)==2


@pytest.mark.parametrize('response', [dict(action='wrong',args=dict(lessons=[])),
    dict(action='propose_lessons',args=dict(lessons=[{**lesson(),'case_ids':['absent']}]))])
def test_invalid_lessons_are_saved_and_never_reissued(monkeypatch,tmp_path,response):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,batch_config())
    provider,requests=adapter(monkeypatch,[conclude(),response])
    batch.run(store,root,lambda case:provider)
    with pytest.raises(ValueError):batch.lessons(store,root,provider)
    with pytest.raises(ValueError):batch.lessons(store,root,None)
    assert len(requests)==2 and not store.list(batch.EXPERIENCE)


def test_lesson_adapter_parse_failure_has_confirmed_diagnostic(monkeypatch,tmp_path):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,batch_config())
    provider,requests=adapter(monkeypatch,[conclude(),dict(lessons=[])])
    batch.run(store,root,lambda case:provider)
    with pytest.raises(ValueError,match='invalid provider response'):batch.lessons(store,root,provider)
    with pytest.raises(ValueError,match='invalid provider response'):batch.lessons(store,root,None)
    assert len(requests)==2 and store.list(batch.LESSON_RESPONSE)


def test_public_cli_project_and_batch_entrypoints(monkeypatch,tmp_path,capsys):
    from biomodel_agent import research_cli
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
    provider,requests=adapter(monkeypatch,[conclude(),conclude(),dict(action='propose_lessons',args=dict(lessons=[lesson()]))])
    monkeypatch.setattr(research_cli,'_provider',lambda *args:provider)
    assert cli.main(['research-project-run',root,'--store',str(store.root)])==0
    assert json.loads(capsys.readouterr().out)['state']=='insufficient_evidence'
    batch_ref=batch.create(store,batch_config())
    assert cli.main(['research-batch-run',batch_ref,'--store',str(store.root)])==0
    capsys.readouterr()
    assert cli.main(['research-batch-lessons',batch_ref,'--store',str(store.root)])==0
    assert json.loads(capsys.readouterr().out)['experience_refs'] and len(requests)==3


def test_workbench_real_adapter_alignment_reply_and_finish(monkeypatch,tmp_path):
    answers=[dict(action='ask_alignment',args=dict(category='granularity',question='Population or individual?',affected_fields=['granularity'])),conclude()]
    provider,requests=adapter(monkeypatch,answers)
    app=Workbench(tmp_path/'s',allow_provider=True,base_url='https://offline.invalid/v1',model='offline-control',provider_factory=lambda _:provider)
    try:
        root=p.create(app.store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=2)))
        def advance():
            params=dict(project_ref=root);preview=app.preview('run_project',params)
            return wait(app,app.submit(uuid4().hex,'run_project',params,provider_consent=True,scope_sha256=preview['scope_sha256']))
        assert advance()['state']=='returned'
        question=p.inspect(app.store,root)['pending_questions'][0]
        reply=wait(app,app.submit(uuid4().hex,'reply_project',dict(project_ref=root,question_ref=question,reply='Population.',respondent='Researcher')))
        assert reply['state']=='returned' and advance()['state']=='returned'
        assert p.inspect(app.store,root)['state']=='insufficient_evidence' and len(requests)==2
    finally:app.close()


def test_real_adapter_runs_science_and_exports_identical_report(monkeypatch,tmp_path):
    from biomodel_agent import bundles,reporting
    store,root,iteration=opened(tmp_path,native_agent=False,planner_max_calls=3)
    provider,requests=adapter(monkeypatch,[
        dict(action='run_iteration',args=dict(iteration_ref=iteration)),
        dict(action='assess_iteration',args=dict(iteration_ref=iteration,interpretation='consistent',
            rationale='Actual backend checks passed; applicability remains unknown.',evidence_ids=[])),
        lambda:dict(action='conclude',args=dict(outcome='completed_with_limits',
            summary='Explicit Boolean hypothesis numerically checked.',
            assessment_refs=p.inspect(store,root)['assessment_refs'],limitations=['Synthetic model; no biological validation.']))])
    snapshot=supervisor.run(store,provider,root)
    assert p.inspect(store,root)['state']=='completed_with_limits'
    assert p.facts(store,iteration)['numeric_status']=='pass' and len(requests)==3
    assert len(store.list('typed_execution'))==1
    bundles.export_bundle(store,snapshot,tmp_path/'export')
    assert reporting.render_report(store,snapshot)==reporting.render_report(ArtifactStore(tmp_path/'export'),snapshot)


def test_confirmed_adapter_return_recovers_without_extra_request(monkeypatch,tmp_path):
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
    provider,requests=adapter(monkeypatch,[conclude()]);original=store.put
    def stop(kind,payload,refs=None):
        if kind==p.RETURN and payload.get('result',{}).get('response_ref'):raise KeyboardInterrupt('before outer return')
        return original(kind,payload,refs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',stop)
        with pytest.raises(KeyboardInterrupt):supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert not view['continuation']['can_advance'] and view['state']=='needs_recovery'
    p.recover(store,root,view['unresolved_operation_refs'][0])
    supervisor.run(store,None,root)
    assert p.inspect(store,root)['state']=='insufficient_evidence' and len(requests)==1


def test_real_http_workbench_calls_adapter_and_executes_action(web,monkeypatch):
    provider,requests=adapter(monkeypatch,[conclude()]);wb=web.workbench
    monkeypatch.setattr(wb,'allow_provider',True)
    monkeypatch.setattr(wb,'provider_info',dict(enabled=True,base_url='https://offline.invalid/v1',model='offline-control'))
    monkeypatch.setattr(wb,'_provider_factory',lambda _:provider)
    root=p.create(wb.store,dict(scenario='Synthetic HTTP control.',policy=dict(planner_max_calls=1)))
    params=dict(project_ref=root)
    status,body,_=request(web,'/api/preview',body=dict(operation='run_project',params=params))
    assert status==200;preview=json.loads(body)
    status,body,_=request(web,'/api/jobs',body=dict(key=uuid4().hex,operation='run_project',params=params,
        provider_consent=True,scope_sha256=preview['scope_sha256']))
    assert status==202
    assert wait(wb,json.loads(body))['state']=='returned'
    status,body,_=request(web,'/api/project/'+root)
    assert status==200 and json.loads(body)['state']=='insufficient_evidence' and len(requests)==1


def test_invalid_adapter_return_retains_usage_and_safe_diagnostic(monkeypatch,tmp_path):
    from biomodel_agent import bundles,reporting
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','private-dummy-key')
    requests=[]
    def truncated(request):
        requests.append(request)
        return httpx.Response(200,json=dict(choices=[dict(finish_reason='length',message=dict(content='{"action":'))],
            usage=dict(total_tokens=123)))
    provider=ChatProvider('https://offline.invalid/v1','offline-control',api_key_env='BIOMODEL_TEST_PROVIDER_KEY',transport=httpx.MockTransport(truncated))
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=2)))
    snapshot=supervisor.run(store,provider,root);view=p.inspect(store,root)
    assert view['state']=='needs_recovery' and len(requests)==1
    usage=supervisor.usage(store,root)
    assert usage['reported_total_tokens']==123 and usage['missing_usage_calls']==0
    assert usage['planner_complete_elapsed_s'] is not None
    ret=[v for _,v in p.rows(store,p.RETURN,root) if v['state']=='unknown'][0]
    assert ret['diagnostic']['response']==dict(finish_reason='length',content_characters=10)
    assert store.get(ret['provider_failure_ref'])['kind']==p.PROVIDER_FAILURE
    assert supervisor.run(store,provider,root)==snapshot and len(requests)==1
    bundles.export_bundle(store,snapshot,tmp_path/'export')
    assert reporting.render_report(store,snapshot)==reporting.render_report(ArtifactStore(tmp_path/'export'),snapshot)
    assert 'private-dummy-key' not in ''.join(x.read_text() for x in store.objects_dir.glob('*.json'))


def test_frame_query_limit_diagnostic_identifies_field_and_limit(tmp_path):
    from test_research_projects import frame
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.'))
    f=frame();f['evidence_needs'][0]['queries']=['q']*5
    with pytest.raises(ValueError,match=r'frame.evidence_needs\[N1\].queries expects at most 4'):
        step(store,root,'revise_frame',frame=f,reason='Bad query count.')
    assert p.inspect(store,root)['frame_ref'] is None
