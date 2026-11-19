"""Explicit source-to-SSA controls through real loopback HTTP, scripted models."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

from biomodel_agent import recovery,reporting,research_tasks as tasks,typed_models
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench
from biomodel_agent.workbench_server import Server
from demo_workbench import BASE,MODEL,Client,Factory,action

ROOT=Path(__file__).resolve().parents[2]
INPUTS=ROOT/'examples/source-stochastic'
TEXT=(INPUTS/'birth-death.txt').read_text()
TEMPLATE=json.loads((INPUTS/'proposal.template.json').read_text())
UNKNOWN='合成随机模型的生物学适用性与分布检验尚未核验。'


def spec(source,*,missing=False):
    value=deepcopy(TEMPLATE)
    value['summary']='合成演示：明确分子数、反应、随机常数、单位及假设的 SSA 模型。'
    candidate=value['candidates'][0]
    candidate['rationale']='按完整文本声明检查化学计量和组合数倾向函数；不推断反应或进行浓度换算。'
    candidate['unknowns']=[UNKNOWN]
    for anchor in value['evidence']:anchor['source_ref']=source
    if missing:
        ids=[a['id'] for a in value['evidence'] if a['quote']=='Stochastic rate birth = 5.']
        value['evidence']=[a for a in value['evidence'] if a['id'] not in ids]
        candidate['evidence_ids']=[eid for eid in candidate['evidence_ids'] if eid not in ids]
        for key in ('stochastic_draft','stochastic_rate_units','evidence_map'):candidate.pop(key)
        candidate['unknowns']=['出生反应的随机常数缺失，不能补零或套用浓度速率。']
        value['recommendation']=None;value['questions']=['出生反应的随机常数及其反时间单位是多少？']
    return value


def setup(output,port=0):
    output=Path(output).absolute();output.mkdir(exist_ok=False)
    factory=Factory()
    app=Workbench(output/'store',allow_provider=True,base_url=BASE,model=MODEL,provider_factory=factory)
    server=Server(app,port);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);thread.start()
    return output,factory,app,server,thread,Client(app,server,output)


def intake(client,text,label):
    source=client.submit('import_source',dict(filename=label+'.txt',content=text))['result']['source_ref']
    config=json.loads((INPUTS/'request.template.json').read_text().replace('SOURCE_REF',source))
    config['goal']='合成演示 · 来源随机反应网络 · '+label
    request=client.submit('create_task',{'config':config})['result']['request_ref']
    return request,source


def seed(client,factory):
    request,source=intake(client,TEXT,'明确声明')
    factory.pending.extend([[action('propose_task',proposal=spec(source))],
        [action('ask_researcher',question='请确认保留冻结反应网络、组合数约定与时间单位，不做浓度速率换算。')],
        [action('run_typed'),action('finish')]])
    return request,source


def run(output):
    output,factory,app,server,thread,client=setup(output)
    try:
        request,source=seed(client,factory)
        proposal=client.submit('propose_task',{'request_ref':request})['result']['proposal_ref']
        view=client.request('/api/task/'+request)
        assert view['proposals'][0]['candidates'][0]['draft_adoptable'] and not app.store.list('typed_run')
        selected=client.submit('select_proposal',dict(proposal_ref=proposal,candidate_id='direct',reviewer='合成 HTTP 演练',reason='逐项核对随机反应声明，科学支持保持未知。',
            acknowledged_unknowns=[UNKNOWN]))['result']['request_ref']
        accepted=client.request('/api/task/'+selected)
        assert accepted['status']['state']=='ready_to_run' and not app.store.list('typed_run')
        modelref=accepted['config']['model_ref'];payload=app.store.get(modelref)['payload']
        assert payload['source_binding']['rate_units']=={'birth':'1/second','death':'1/second'}
        assert payload['origin']=='accepted_source_bound_stochastic_proposal'
        client.submit('run_task',{'request_ref':selected})
        paused=client.request('/api/task/'+selected)['status']
        assert paused['state']=='awaiting_input' and paused['run_count']==0
        done=client.submit('resume_task',dict(request_ref=selected,reply='保留冻结的化学计量、整数初值、随机常数和单位，继续原任务。',respondent='合成 HTTP 演练'))['result']['result_ref']
        final=client.request('/api/task/'+selected)['status']
        assert final['state']=='completed' and final['native_task_ref']==paused['native_task_ref']
        assert (final['proposal_call_count'],final['native_call_count'],final['total_call_count'],final['run_count'])==(1,3,4,1)
        result=tasks.result(app.readonly,done);native=app.readonly.get(result['native_run_ref'])['payload']
        inp,model,data=typed_models.result(app.readonly,native['run_ref'])
        (output/'numerical-data.json').write_text(json.dumps(dict(model=model,settings=inp['settings'],result=data,source_binding=payload['source_binding']),indent=2))
        report=client.request('/downloads/report/'+done+'.md',binary=True);(output/'report.md').write_bytes(report)
        assert b'1/second' in report and b'Stochastic rate death = 0.5.' in report
        exported=client.submit('export_bundle',{'ref':done})
        archive=client.request('/downloads/bundle/'+exported['job_ref']+'.zip',binary=True);(output/'bundle.zip').write_bytes(archive)
        import io,tempfile,zipfile
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                assert all(n.startswith('bundle/') and '..' not in Path(n).parts for n in z.namelist());z.extractall(tmp)
            detached=reporting.render_report(ArtifactStore(Path(tmp)/'bundle'),done).encode()
            assert detached==report
        missing_request,missing_source=intake(client,TEXT.replace('Stochastic rate birth = 5.\n',''),'缺失随机常数')
        missing=spec(missing_source,missing=True)
        factory.pending.append([action('propose_task',proposal=missing)])
        missing_proposal=client.submit('propose_task',{'request_ref':missing_request})['result']['proposal_ref']
        v=client.request('/api/proposal/'+missing_proposal);assert v['state']=='awaiting_input'
        pending=client.submit('select_proposal',dict(proposal_ref=missing_proposal,candidate_id='direct',reviewer='合成 HTTP 演练',reason='只记录类型选择，缺失常数保持待补。',
            acknowledged_unknowns=missing['candidates'][0]['unknowns']))['result']['request_ref']
        assert client.request('/api/task/'+pending)['status']['state']=='awaiting_input'
        conflict_request,conflict_source=intake(client,TEXT+'Stochastic rate unit death = 1/minute.\n','单位冲突')
        factory.pending.append([action('propose_task',proposal=spec(conflict_source))])
        conflict=client.submit('propose_task',{'request_ref':conflict_request})['result']['proposal_ref']
        v=client.request('/api/proposal/'+conflict);assert not v['candidates'][0]['draft_adoptable']
        refused=client.submit('select_proposal',dict(proposal_ref=conflict,candidate_id='direct',reviewer='合成 HTTP 演练',reason='单位冲突负控，不允许构建模型。',
            acknowledged_unknowns=[UNKNOWN]),expected='failed')
        assert len(app.store.list('typed_run'))==1 and factory.calls==6 and not factory.pending
        summary=dict(scope='seen synthetic literal source-to-SSA controls',implementation_sha256=recovery.implementation_fingerprint(),
            live_provider_requests=0,scripted_provider_responses=factory.calls,scientific_application_calls=1,
            complete=dict(request_ref=selected,result_ref=done,model_ref=modelref,state='completed',proposal_calls=1,native_calls=3,total_calls=4,scientific_runs=1,same_native_task=True,detached_report_matches=True),
            missing=dict(request_ref=pending,state='awaiting_input',model_created=False,scientific_runs=0),
            conflict=dict(proposal_ref=conflict,selection_refused=refused['state']=='failed',scientific_runs=0),
            scientific_support='unknown',independent_domain_review='not_performed')
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));return summary
    finally:server.shutdown();server.server_close();app.close();thread.join(2.)


def serve(output,port):
    output,factory,app,server,thread,client=setup(output,port)
    request,source=seed(client,factory)
    info=dict(url=server.origin+'/#'+request,request_ref=request,source_ref=source,scripted_model=MODEL,live_provider_requests=0,demo=True)
    (output/'launch.json').write_text(json.dumps(info,ensure_ascii=False,indent=2));print(json.dumps(info,ensure_ascii=False),flush=True)
    try:
        while thread.is_alive():time.sleep(.25)
    except KeyboardInterrupt:pass
    finally:
        server.shutdown();server.server_close();app.close();thread.join(2.)
        (output/'summary.json').write_text(json.dumps(dict(implementation_sha256=recovery.implementation_fingerprint(),
            scripted_provider_responses=factory.calls,scientific_application_calls=len(app.store.list('typed_run')),live_provider_requests=0,scientific_support='unknown'),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if args.serve:serve(args.output,args.port)
    else:print(json.dumps(run(args.output),ensure_ascii=False,indent=2))

