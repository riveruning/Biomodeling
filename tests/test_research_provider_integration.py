"""Public research entrypoints exercised through the production chat adapter."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from biomodel_agent import cli, research_pipeline as batch, research_projects as p
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider, ProviderError, make_provider
from biomodel_agent.provider_identity import identity
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench
from test_research_projects import opened, step
from test_workbench import app, web, request, wait

FIXTURES = Path(__file__).parent / 'fixtures/provider-actions'
DSML_FLAT = (FIXTURES / 'deepseek-flat.txt').read_text(encoding='utf-8')


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


PROVIDER_ARGS=('--base-url','https://offline.invalid/v1','--model','offline-control',
    '--api-key-env','BIOMODEL_TEST_PROVIDER_KEY')


def project_run_args(extra=()):
    """Parse real research-project-run options without dispatching the command."""
    import argparse
    from biomodel_agent import research_project_cli
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    research_project_cli.register(sub,lambda p:p.add_argument('--store',required=True))
    return parser.parse_args(['research-project-run','project-ref','--store','store',*extra])


def timeout_seconds(timeout):
    """Normalize an httpx request extension or client timeout to seconds."""
    if isinstance(timeout,dict):
        values=[value for value in timeout.values() if value is not None]
        assert values
        return max(values)
    if isinstance(timeout,(int,float)):return timeout
    assert timeout is not None
    return timeout.read


def install_http_provider(monkeypatch,answers,timeouts=None):
    """Inject MockTransport at the HTTP client; the production ChatProvider stays real."""
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','offline-dummy')
    requests=[];client_timeouts=[]
    def handle(request):
        if timeouts is not None:
            timeout=request.extensions.get('timeout')
            timeouts.append(timeout_seconds(client_timeouts[-1] if timeout is None else timeout))
        requests.append(json.loads(request.content))
        assert len(requests)<=len(answers),'Unexpected additional provider request'
        answer=answers[len(requests)-1]
        if callable(answer):answer=answer()
        return httpx.Response(200,json=dict(choices=[dict(message=dict(
            content=json.dumps(answer)))],usage=dict(total_tokens=12)))
    real_client=httpx.Client
    def client(*args,**kwargs):
        if kwargs.get('transport') is None:
            kwargs['transport']=httpx.MockTransport(handle)
        client_timeouts.append(kwargs.get('timeout'))
        return real_client(*args,**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    return requests


def conclude():
    return dict(action='conclude', args=dict(outcome='insufficient_evidence',
        summary='No measurements available.', assessment_refs=[], limitations=['Offline development control.']))


def dsml_action(name, params):
    """A restricted DeepSeek DSML calls block with independent parameters."""
    body = '<｜DSML｜ calls>\n<｜DSML｜ invoke name="%s">\n' % name
    for key, value in params.items():
        if isinstance(value, str):
            body += '<｜DSML｜ parameter name="%s" string="true">%s</｜DSML｜ parameter>\n' % (key, value)
        else:
            body += '<｜DSML｜ parameter name="%s" string="false">%s</｜DSML｜ parameter>\n' % (key, json.dumps(value))
    return body + '</｜DSML｜ invoke>\n</｜DSML｜ calls>'


def raw_content_provider(monkeypatch, contents, model='deepseek-flash-2'):
    """Real ChatProvider over a mock HTTP service returning raw message content."""
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY', 'offline-dummy')
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        assert len(requests) <= len(contents), 'Unexpected additional provider request'
        body = contents[len(requests) - 1]
        if callable(body):body = body()
        return httpx.Response(200, json=dict(choices=[dict(message=dict(content=body), finish_reason='stop')],
                                            usage=dict(total_tokens=12)))
    provider = ChatProvider('https://offline.invalid/v1', model,
                            api_key_env='BIOMODEL_TEST_PROVIDER_KEY',
                            transport=httpx.MockTransport(handle))
    return provider, requests


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


def test_truncated_but_fully_received_answer_is_rejected_with_bounded_correction(monkeypatch,tmp_path):
    """A complete HTTP envelope whose content is malformed is a received-but-
    refused response: usage stays bound, the raw blob is retained, and the
    planner may correct within the original budget instead of an unknown stop."""
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
    assert view['state']=='planner_budget_exhausted' and len(requests)==2
    usage=supervisor.usage(store,root)
    assert usage['reported_total_tokens']==246 and usage['missing_usage_calls']==0
    assert usage['planner_calls']==2 and usage['planner_complete_elapsed_s'] is not None
    returns=[v for _,v in p.rows(store,p.RETURN,root)]
    assert len(returns)==2 and all(v['state']=='rejected' and v['provider_failure_ref'] for v in returns)
    diagnostic=returns[0]['diagnostic']
    assert diagnostic['code']=='model_response' and 'refused' in diagnostic['message']
    assert diagnostic['response']==dict(finish_reason='length',content_characters=10)
    failure=store.get(returns[0]['provider_failure_ref'])
    assert failure['kind']==p.PROVIDER_FAILURE
    assert json.loads(store.get_blob(failure['payload']['model_content_json_blob_ref']))=='{"action":'
    assert failure['payload']['model_content_authority']=='untrusted_model_output_not_an_action'
    feedback=[m for m in json.loads(requests[1].content)['messages'] if 'refused' in m['content']]
    assert len(feedback)==1 and feedback[0]['role']=='user'
    assert 'planner_response' in feedback[0]['content']
    assert 'inspect the saved diagnostic' not in feedback[0]['content']
    bundles.export_bundle(store,snapshot,tmp_path/'export')
    assert reporting.render_report(store,snapshot)==reporting.render_report(ArtifactStore(tmp_path/'export'),snapshot)
    assert 'private-dummy-key' not in ''.join(x.read_text(encoding='utf-8') for x in store.objects_dir.glob('*.json'))


@pytest.mark.parametrize('finish_reason', ['length', 'stop'])
def test_reasoning_only_envelope_retains_safe_diagnostic(monkeypatch, tmp_path, finish_reason):
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY', 'private-dummy-key')
    requests = []
    reasoning = 'Private reasoning text must remain outside project artifacts.'

    def reply(request):
        requests.append(request)
        return httpx.Response(200, json=dict(
            choices=[dict(finish_reason=finish_reason,
                          message=dict(content=None, reasoning_content=reasoning))],
            usage=dict(prompt_tokens=100, completion_tokens=6000, total_tokens=6100)))

    provider = ChatProvider('https://offline.invalid/v1', 'offline-control',
                            api_key_env='BIOMODEL_TEST_PROVIDER_KEY',
                            transport=httpx.MockTransport(reply))
    store = ArtifactStore(tmp_path/'store')
    project = p.create(store, dict(scenario='Envelope diagnostic control.',
                                   policy=dict(planner_max_calls=2)))
    supervisor.run(store, provider, project)
    assert len(requests) == 1 and p.inspect(store, project)['state'] == 'needs_recovery'
    failure = p.rows(store, p.PROVIDER_FAILURE, project)[0][1]
    assert failure['error_kind'] == 'response_envelope'
    assert failure['response_diagnostic'] == dict(
        finish_reason=finish_reason, content_characters=0, content_type='NoneType')
    assert failure['usage']['total_tokens'] == 6100
    assert failure['model_content_json_blob_ref'] is None
    assert reasoning not in ''.join(path.read_text() for path in store.objects_dir.glob('*.json'))


@pytest.mark.parametrize('response,kind,reported,missing', [
    ('broken-envelope','response_envelope',7,0),
    ('transport-drop','transport',0,1),
])
def test_unconfirmed_provider_failures_stay_unknown_and_need_recovery(monkeypatch,tmp_path,response,kind,reported,missing):
    """Broken envelopes and dropped transports keep the unknown outcome: no
    automatic replay, needs_recovery state, and retained provider accounting."""
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','private-dummy-key')
    requests=[]
    def broken(request):
        requests.append(request)
        if response=='transport-drop':raise httpx.ConnectError('connection dropped')
        return httpx.Response(200,json=dict(object='error',usage=dict(total_tokens=7)))
    provider=ChatProvider('https://offline.invalid/v1','offline-control',api_key_env='BIOMODEL_TEST_PROVIDER_KEY',transport=httpx.MockTransport(broken))
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=2)))
    snapshot=supervisor.run(store,provider,root);view=p.inspect(store,root)
    assert view['state']=='needs_recovery' and len(requests)==1
    usage=supervisor.usage(store,root)
    assert usage['reported_total_tokens']==reported and usage['missing_usage_calls']==missing
    assert usage['planner_calls']==1
    ret=[v for _,v in p.rows(store,p.RETURN,root) if v['state']=='unknown'][0]
    assert ret['diagnostic']['code']==kind and ret['result'] is None
    assert supervisor.run(store,provider,root)==snapshot and len(requests)==1


def test_public_cli_run_normalizes_real_dsml_and_keeps_raw_message_traceable(monkeypatch,tmp_path,capsys):
    """The reported failure chain through the default public CLI: DeepSeek DSML
    answer -> normalized action -> tool rejection -> bounded correction ->
    completion, with usage continuity and the retained raw message provenance."""
    from biomodel_agent import research_cli
    provider,requests=raw_content_provider(monkeypatch,[DSML_FLAT,
        dsml_action('conclude',dict(outcome='insufficient_evidence',
            summary='Continued after the out-of-scope read was rejected.',
            assessment_refs=[],limitations=['Offline development control.']))])
    monkeypatch.setattr(research_cli,'_provider',lambda *args:provider)
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=2)))
    assert cli.main(['research-project-run',root,'--store',str(store.root),*PROVIDER_ARGS,
        '--reasoning-effort','max'])==0
    assert json.loads(capsys.readouterr().out)['state']=='insufficient_evidence'
    assert len(requests)==2
    usage=supervisor.usage(store,root)
    assert usage['planner_calls']==2 and usage['reported_total_tokens']==24
    assert usage['missing_usage_calls']==0
    receipts=[r for _,r in p.rows(store,p.RESPONSE,root)]
    reads=[r for r in receipts if r['response']['action']=='inspect_source']
    assert len(reads)==1 and reads[0]['response']['args']==dict(
        source_ref='760cd9935f5b6f295371c96eae12ae4bee5a1f789f36c8a3c081e995bc708563',
        page=1,start=0,end=3449)
    normalized=[r for r in receipts if r.get('response_source')=='provider_dsml_normalization']
    assert len(normalized)==2
    for receipt in normalized:
        assert receipt['model_content_authority']=='untrusted_model_output_not_an_action'
        assert '<｜DSML｜ calls>' in json.loads(store.get_blob(receipt['model_content_json_blob_ref']))


def test_dsml_research_actions_beyond_the_base_agent_table_execute(monkeypatch,tmp_path):
    from test_research_projects import frame
    provider,requests=raw_content_provider(monkeypatch,[
        dsml_action('revise_frame',dict(reason='Agent proposes the frame.',frame=frame())),
        dsml_action('conclude',dict(outcome='insufficient_evidence',summary='DSML control complete.',
            assessment_refs=[],limitations=['Offline development control.']))])
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=2)))
    supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert view['frame_ref'] and view['state']=='insufficient_evidence' and len(requests)==2


def test_dsml_unknown_action_is_rejected_by_the_project_caller(monkeypatch,tmp_path):
    provider,requests=raw_content_provider(monkeypatch,[dsml_action('shell',dict(cmd='forbidden'))]*3)
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=3)))
    supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert len(requests)==3 and view['budget']['planner_calls']==3 and not view['frame_ref']
    assert len([x for _,x in p.rows(store,p.STEP,root) if x['operation']=='invalid_response'])==3


def test_frame_query_limit_diagnostic_identifies_field_and_limit(tmp_path):
    from test_research_projects import frame
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.'))
    f=frame();f['evidence_needs'][0]['queries']=['q']*5
    with pytest.raises(ValueError,match=r'frame.evidence_needs\[N1\].queries expects at most 4'):
        step(store,root,'revise_frame',frame=f,reason='Bad query count.')
    assert p.inspect(store,root)['frame_ref'] is None


def test_chat_provider_legacy_positional_arguments_are_unchanged(monkeypatch):
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','offline-dummy')
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=dict(choices=[dict(message=dict(
            content=json.dumps(conclude())))],usage=dict(total_tokens=1)))
    provider=ChatProvider('https://offline.invalid/v1','offline-control','BIOMODEL_TEST_PROVIDER_KEY',
        60,4000,httpx.MockTransport(handle),False)
    assert provider.reasoning_effort is None and provider.max_tokens==4000 and provider.json_mode is False
    provider.respond([{'role':'user','content':'probe'}])
    assert 'reasoning_effort' not in requests[0] and requests[0]['max_tokens']==4000


def test_project_run_cli_sends_explicit_max_request(monkeypatch,tmp_path,capsys):
    requests=install_http_provider(monkeypatch,[conclude()])
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
    assert cli.main(['research-project-run',root,'--store',str(store.root),*PROVIDER_ARGS,
        '--reasoning-effort','max','--max-output-tokens','8000'])==0
    assert json.loads(capsys.readouterr().out)['state']=='insufficient_evidence'
    assert len(requests)==1
    assert requests[0]['reasoning_effort']=='max' and requests[0]['max_tokens']==8000


def test_project_run_cli_sends_explicit_provider_timeout(monkeypatch,tmp_path,capsys):
    timeouts=[]
    install_http_provider(monkeypatch,[conclude()],timeouts=timeouts)
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='Synthetic.',policy=dict(planner_max_calls=1)))
    assert cli.main(['research-project-run',root,'--store',str(store.root),*PROVIDER_ARGS,
        '--provider-timeout','900'])==0
    assert json.loads(capsys.readouterr().out)['state']=='insufficient_evidence'
    assert timeouts==[900.0]


def test_project_run_default_provider_timeout_is_60(monkeypatch):
    from biomodel_agent import research_cli
    timeouts=[]
    install_http_provider(monkeypatch,[conclude()],timeouts=timeouts)
    args=project_run_args(PROVIDER_ARGS)
    assert args.provider_timeout is None
    provider=research_cli._provider(args)
    assert provider.timeout_s==60
    provider.respond([{'role':'user','content':'probe'}])
    assert timeouts==[60.0]


def test_frozen_identity_keeps_provider_timeout_and_old_identity_does_not_upgrade(monkeypatch):
    from biomodel_agent import research_cli
    timeouts=[]
    install_http_provider(monkeypatch,[conclude(),conclude()],timeouts=timeouts)
    frozen=identity(research_cli._provider(project_run_args(PROVIDER_ARGS+('--provider-timeout','900'))))
    assert frozen['timeout_s']==900
    recovery=project_run_args(PROVIDER_ARGS+('--provider-timeout','30'))
    recovered=research_cli._provider(recovery,frozen)
    assert recovered.timeout_s==900
    recovered.respond([{'role':'user','content':'probe'}])
    legacy=research_cli._provider(project_run_args(PROVIDER_ARGS+('--provider-timeout','900')),
        dict(model='offline-control',json_mode=False,max_tokens=4000))
    assert legacy.timeout_s==60
    legacy.respond([{'role':'user','content':'probe'}])
    assert timeouts==[900.0,60.0]


def test_project_deadline_caps_explicit_provider_timeout(monkeypatch,tmp_path,capsys):
    timeouts=[]
    install_http_provider(monkeypatch,[conclude()],timeouts=timeouts)
    store=ArtifactStore(tmp_path/'s')
    root=p.create(store,dict(scenario='Synthetic.',
        policy=dict(planner_max_calls=1,total_timeout_s=30)))
    stored=p.load(store,root)
    assert stored['policy']['total_timeout_s']==30
    assert cli.main(['research-project-run',root,'--store',str(store.root),*PROVIDER_ARGS,
        '--provider-timeout','900'])==0
    assert json.loads(capsys.readouterr().out)['state']=='insufficient_evidence'
    assert len(timeouts)==1 and 0<timeouts[0]<=30
    assert p.load(store,root)['policy']['total_timeout_s']==30


def test_invalid_provider_timeout_is_rejected_before_network(monkeypatch):
    from biomodel_agent import research_cli
    requests=install_http_provider(monkeypatch,[conclude()])
    for value in ('0','-1','nan','inf'):
        with pytest.raises(ProviderError,match='timeout_s'):
            research_cli._provider(project_run_args(PROVIDER_ARGS+('--provider-timeout',value)))
    assert not requests


def test_project_run_default_payload_omits_reasoning_effort(monkeypatch):
    from biomodel_agent import research_cli
    requests=install_http_provider(monkeypatch,[conclude()])
    args=project_run_args(PROVIDER_ARGS)
    assert args.reasoning_effort is None and args.max_output_tokens==4000
    provider=research_cli._provider(args)
    assert provider.reasoning_effort is None and provider.max_tokens==4000
    provider.respond([{'role':'user','content':'probe'}])
    assert set(requests[0])=={'model','messages','temperature','max_tokens'}
    assert requests[0]['max_tokens']==4000


def test_frozen_identity_keeps_reasoning_and_max_but_old_identity_does_not_upgrade(monkeypatch):
    from biomodel_agent import research_cli
    requests=install_http_provider(monkeypatch,[conclude(),conclude()])
    args=project_run_args(PROVIDER_ARGS+('--reasoning-effort','max','--max-output-tokens','8000'))
    frozen=identity(research_cli._provider(args))
    assert frozen['reasoning_effort']=='max' and frozen['max_tokens']==8000
    recovery=project_run_args(PROVIDER_ARGS+('--reasoning-effort','low','--max-output-tokens','6000'))
    recovered=research_cli._provider(recovery,frozen)
    assert recovered.reasoning_effort=='max' and recovered.max_tokens==8000
    recovered.respond([{'role':'user','content':'probe'}])
    assert requests[0]['reasoning_effort']=='max' and requests[0]['max_tokens']==8000
    old=dict(model='offline-control',json_mode=False,timeout_s=60,max_tokens=4000)
    legacy=research_cli._provider(args,old)
    assert legacy.reasoning_effort is None and legacy.max_tokens==4000
    legacy.respond([{'role':'user','content':'probe'}])
    assert 'reasoning_effort' not in requests[1] and requests[1]['max_tokens']==4000
    missing=research_cli._provider(args,dict(model='offline-control'))
    assert missing.reasoning_effort is None and missing.max_tokens==4000


def test_make_provider_carries_reasoning_effort_and_rejects_invalid_config(monkeypatch):
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','offline-dummy')
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=dict(choices=[dict(message=dict(
            content=json.dumps(conclude())))],usage=dict(total_tokens=1)))
    transport=httpx.MockTransport(handle)
    config=dict(base_url='https://offline.invalid/v1',model='offline-control',
        api_key_env='BIOMODEL_TEST_PROVIDER_KEY')
    provider=make_provider({**config,'max_tokens':8000,'reasoning_effort':'max'},transport=transport)
    provider.respond([{'role':'user','content':'probe'}])
    assert requests[0]['reasoning_effort']=='max' and requests[0]['max_tokens']==8000
    provider=make_provider(dict(config),transport=transport)
    provider.respond([{'role':'user','content':'probe'}])
    assert 'reasoning_effort' not in requests[1] and requests[1]['max_tokens']==4000
    with pytest.raises(ProviderError,match='unknown provider config'):
        make_provider({**config,'unexpected':'x'},transport=transport)
    with pytest.raises(ProviderError,match='reasoning_effort'):
        make_provider({**config,'reasoning_effort':'extreme'},transport=transport)
    assert len(requests)==2


def test_invalid_reasoning_effort_is_rejected_before_network(monkeypatch):
    from biomodel_agent import research_cli
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','offline-dummy')
    calls=[]
    transport=httpx.MockTransport(lambda request:calls.append(request))
    for value in ('extreme','','MAX',0,True):
        with pytest.raises(ProviderError,match='reasoning_effort'):
            ChatProvider('https://offline.invalid/v1','offline-control',
                api_key_env='BIOMODEL_TEST_PROVIDER_KEY',transport=transport,reasoning_effort=value)
    with pytest.raises(ProviderError,match='reasoning_effort'):
        research_cli._provider(project_run_args(PROVIDER_ARGS+('--reasoning-effort','extreme')))
    with pytest.raises(ProviderError,match='max_tokens'):
        research_cli._provider(project_run_args(PROVIDER_ARGS+('--max-output-tokens','0')))
    assert not calls


def test_provider_identity_records_reasoning_only_when_set(monkeypatch):
    from biomodel_agent import research_usage
    transport=httpx.MockTransport(lambda request:httpx.Response(500))
    default=ChatProvider('https://offline.invalid/v1','offline-control',transport=transport)
    old=identity(default)
    assert set(old)=={'endpoint_sha256','class','model','json_mode','timeout_s','max_tokens'}
    assert 'reasoning_effort' not in old
    assert old['endpoint_sha256']==hashlib.sha256(b'https://offline.invalid/v1').hexdigest()
    chosen=ChatProvider('https://offline.invalid/v1','offline-control',transport=transport,
        max_tokens=8000,reasoning_effort='max')
    new=identity(chosen)
    assert new['reasoning_effort']=='max' and new['max_tokens']==8000
    for key in ('endpoint_sha256','class','model','json_mode','timeout_s'):
        assert new[key]==old[key]
    class LegacyProvider:
        base_url='https://legacy.invalid/v1'
        model='legacy'
        json_mode=False
        timeout_s=60
        max_tokens=4000
    assert set(identity(LegacyProvider()))==set(old)
    class Wrapper:
        def __init__(self,provider):self.provider=provider
    monkeypatch.setattr(research_usage,'MeteredProvider',Wrapper)
    assert identity(Wrapper(chosen))==new


def test_legacy_args_without_new_options_keep_defaults(monkeypatch):
    from types import SimpleNamespace
    from biomodel_agent import research_cli
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY','offline-dummy')
    args=SimpleNamespace(base_url='https://offline.invalid/v1',model='offline-control',
        api_key_env='BIOMODEL_TEST_PROVIDER_KEY')
    provider=research_cli._provider(args)
    assert provider.reasoning_effort is None and provider.max_tokens==4000 and provider.timeout_s==60
