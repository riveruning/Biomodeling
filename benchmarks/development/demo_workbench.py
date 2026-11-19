"""Real loopback HTTP/GUI development controls with scripted model responses.

No provider HTTP calls. Only seen synthetic sources and prepared public examples.
"""
import argparse
from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4
import zipfile

from biomodel_agent import recovery,reporting,research_tasks as tasks,typed_models
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench,OPERATION
from biomodel_agent.workbench_server import Server

ROOT=Path(__file__).resolve().parents[2]
MODEL='scripted-workbench-development-control'
BASE='https://invalid.example/v1'
BOOLEAN_TEXT='合成开发材料，非真实生物学结论。\nInitial A = False.\nInitial B = False.\nRule A = not B.\nRule B = A.\nUpdate = synchronous.\nTime unit = sweep.\nClamps = none.\n'


def action(name,**args):return dict(action=name,args=args)


class Scripted:
    model=MODEL
    base_url=BASE
    timeout_s=60
    max_tokens=4000
    json_mode=False
    last_usage={}
    def __init__(self,actions):self.actions=iter(actions);self.calls=0
    def respond(self,messages):self.calls+=1;return deepcopy(next(self.actions))


class Factory:
    def __init__(self):self.pending=[];self.providers=[]
    def __call__(self,identity):
        if not self.pending:raise ValueError('No scripted response queued; never use a live service.')
        provider=Scripted(self.pending.pop(0));self.providers.append(provider);return provider
    @property
    def calls(self):return sum(p.calls for p in self.providers)


class Client:
    def __init__(self,app,server,output):self.app=app;self.server=server;self.output=output;self.commands=[]
    def request(self,path,body=None,*,binary=False,expected=200):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15.)
        headers={'X-Workbench-Token':self.app.token,'Origin':self.server.origin}
        if body is not None:headers['Content-Type']='application/json'
        try:
            conn.request('POST' if body is not None else 'GET',path,body=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers=headers)
            r=conn.getresponse();raw=r.read()
            assert r.status==expected,(path,r.status,raw[:1000])
            result=dict(bytes=len(raw)) if binary else json.loads(raw)
            self.commands.append(dict(path=path,method='POST' if body is not None else 'GET',body=body,status=r.status,response=result))
            (self.output/'http-commands.json').write_text(json.dumps(self.commands,ensure_ascii=False,indent=2))
            return raw if binary else result
        finally:conn.close()
    def submit(self,operation,params,*,key=None,expected='returned'):
        extra={}
        if operation in ('run_task','propose_task','resume_task','recover_task'):
            preview=self.request('/api/preview',dict(operation=operation,params=params))
            if preview['requires_provider']:extra=dict(provider_consent=True,scope_sha256=preview['scope_sha256'])
        job=self.request('/api/jobs',dict(key=key or uuid4().hex,operation=operation,params=params,**extra),expected=202)
        deadline=time.monotonic()+90
        while job['state'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.05);job=self.request('/api/job/'+job['job_ref'])
        assert job['state']==expected,job
        return job


def boolean_proposal(source_ref):
    spec=json.loads((ROOT/'examples/task-proposals/boolean-proposal.template.json').read_text())
    for anchor in spec['evidence']:anchor['source_ref']=source_ref
    spec['summary']='合成演示：依据明确声明检查两节点布尔网络。'
    candidate=spec['candidates'][0];candidate['rationale']='资料逐项提供了节点初值、逻辑规则、更新方式、时间单位与无钳制声明。'
    candidate['unknowns']=['此为合成演示，生物学适用性尚未核验。']
    return spec


def seed(client,factory):
    source=client.submit('import_source',dict(filename='synthetic-boolean.txt',content=BOOLEAN_TEXT))['result']['source_ref']
    config=dict(schema_version=1,goal='合成演示 · 来源布尔网络的检查与追问',source_refs=[source],proposal_max_calls=1,max_calls=6,
        execution={'settings':{'steps':4,'seed':123,'timeout_s':15.}})
    request=client.submit('create_task',{'config':config})['result']['request_ref']
    factory.pending.extend([[action('propose_task',proposal=boolean_proposal(source))],
        [action('ask_researcher',question='请确认保留这组冻结规则，并沿用原任务继续。')],
        [action('run_typed'),action('finish')]])
    return request,source


def open_app(output,port=0):
    output=Path(output).absolute();output.mkdir(exist_ok=False)
    factory=Factory()
    app=Workbench(output/'store',allow_provider=True,base_url=BASE,model=MODEL,provider_factory=factory)
    server=Server(app,port)
    return output,factory,app,server


def run(output):
    output,factory,app,server=open_app(output)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);thread.start()
    client=Client(app,server,output);results={}
    try:
        for kind in tasks.TYPES:
            config=dict(schema_version=1,goal='合成 HTTP 对照 · '+kind,model_type=kind,max_calls=6)
            if kind=='ode':
                assets=ROOT/'src/biomodel_agent/workspace_assets/examples'
                source=client.submit('import_source',dict(filename='ode-source.txt',content=(assets/'complete-source.txt').read_text()))['result']['source_ref']
                plan=json.loads((assets/'complete-plan.json').read_text().replace('SOURCE_REF',source))
                config.update(source_refs=[source],execution=json.loads((assets/'execution.json').read_text()))
                actions=[action('submit_plan',plan=plan),action('run'),action('finish')]
            else:
                path=ROOT/'examples/fba-v1/chain.json' if kind=='fba' else ROOT/f'examples/model-types/{kind}.json'
                model=client.submit('import_model',dict(filename=path.name,content=path.read_text(),model_type=kind))['result']['model_ref']
                settings={} if kind=='fba' else json.loads((ROOT/f'examples/model-types/{kind}-settings.json').read_text())
                config.update(model_ref=model,execution={'settings':settings})
                actions=[action('run_fba' if kind=='fba' else 'run_typed'),action('finish')]
            request=client.submit('create_task',{'config':config})['result']['request_ref']
            factory.pending.append(actions);key=uuid4().hex
            ran=client.submit('run_task',{'request_ref':request},key=key)
            before=factory.calls
            again=client.request('/api/jobs',dict(key=key,operation='run_task',params={'request_ref':request}),expected=202)
            assert again['job_ref']==ran['job_ref'] and factory.calls==before
            r=ran['result']['result_ref'];saved=tasks.result(app.readonly,r)
            assert saved['state']=='completed' and saved['run_count']==1
            results[kind]=dict(request_ref=request,result_ref=r,state=saved['state'],call_count=saved['call_count'],run_count=saved['run_count'])
            report=client.request('/downloads/report/'+r+'.md',binary=True);(output/(kind+'-report.md')).write_bytes(report)
            exported=client.submit('export_bundle',{'ref':r})
            archive=client.request('/downloads/bundle/'+exported['job_ref']+'.zip',binary=True);(output/(kind+'-bundle.zip')).write_bytes(archive)
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(archive)) as z:
                    assert all(n.startswith('bundle/') and '..' not in Path(n).parts for n in z.namelist());z.extractall(tmp)
                detached=reporting.render_report(ArtifactStore(Path(tmp)/'bundle'),r).encode()
                assert detached==report
            results[kind]['detached_report_matches']=True
        request,source=seed(client,factory)
        proposed=client.submit('propose_task',{'request_ref':request})['result']['proposal_ref']
        view=client.request('/api/task/'+request);assert view['proposals'][0]['candidates'][0]['draft_adoptable']
        selected=client.submit('select_proposal',dict(proposal_ref=proposed,candidate_id='direct',reviewer='合成 HTTP 演练',reason='逐项核对后采纳，未知项保持未知。',
            acknowledged_unknowns=boolean_proposal(source)['candidates'][0]['unknowns']))['result']['request_ref']
        assert len(app.store.list('typed_run'))==4
        paused=client.submit('run_task',{'request_ref':selected})['result']['result_ref']
        before=client.request('/api/task/'+selected)['status'];assert before['state']=='awaiting_input'
        done=client.submit('resume_task',dict(request_ref=selected,reply='保留已冻结的规则与设置，继续原任务。',respondent='合成 HTTP 演练'))['result']['result_ref']
        after=client.request('/api/task/'+selected)['status']
        assert after['state']=='completed' and after['native_task_ref']==before['native_task_ref']
        assert (after['proposal_call_count'],after['native_call_count'],after['total_call_count'],after['run_count'])==(1,3,4,1)
        model=typed_models.result(app.readonly,app.readonly.get(tasks.result(app.readonly,done)['native_run_ref'])['payload']['run_ref'])
        (output/'source-boolean-data.json').write_text(json.dumps(dict(model=model[1],settings=model[0]['settings'],result=model[2]),indent=2))
        results['source_boolean']=dict(request_ref=selected,result_ref=done,state='completed',call_count=3,proposal_call_count=1,total_call_count=4,run_count=1,same_native_task=True)
        assert factory.calls==17 and not factory.pending
        overview=client.request('/api/overview');assert all(j['state']=='returned' for j in overview['jobs'])
        summary=dict(scope='seen_synthetic_scripted_real_loopback_http_controls',implementation_sha256=recovery.implementation_fingerprint(),
            results=results,scientific_application_calls=7,scripted_provider_responses=factory.calls,live_provider_requests=0,
            independent_domain_reviews=0,scientific_support='unknown',provider_transport='injected scripted responses; real HTTP only on loopback')
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));return summary
    finally:
        server.shutdown();server.server_close();app.close();thread.join(2.)


def serve(output,port):
    output,factory,app,server=open_app(output,port)
    seed_thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);seed_thread.start()
    client=Client(app,server,output)
    request,source=seed(client,factory)
    info=dict(url=server.origin+'/#'+request,request_ref=request,source_ref=source,model=MODEL,live_provider_requests=0,scripted_demo=True)
    (output/'launch.json').write_text(json.dumps(info,ensure_ascii=False,indent=2));print(json.dumps(info,ensure_ascii=False),flush=True)
    try:
        while seed_thread.is_alive():time.sleep(.25)
    except KeyboardInterrupt:pass
    finally:
        server.shutdown();server.server_close();app.close();seed_thread.join(2.)
        summary=dict(implementation_sha256=recovery.implementation_fingerprint(),scripted_provider_responses=factory.calls,live_provider_requests=0,
            scientific_application_calls=len(app.store.list('typed_run')),scientific_support='unknown',demo=True)
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if args.serve:serve(args.output,args.port)
    else:print(json.dumps(run(args.output),ensure_ascii=False,indent=2))

