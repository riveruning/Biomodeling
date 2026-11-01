"""Seen synthetic research-loop and paper-queue controls; all model replies scripted."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

from biomodel_agent import recovery,reporting,research_projects as projects,research_pipeline as pipeline,typed_models
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench
from biomodel_agent.workbench_server import Server
from demo_workbench import Client as TaskClient,action,Scripted

ROOT=Path(__file__).resolve().parents[2]
MODEL='scripted-agent-research-development-control'
BASE='https://invalid.example/v1'


def frame(second=False):
    return dict(objective='比较两个明确假设的反馈模型，定位需要进一步证据的部分。',
        mode='application_modeling',granularity='两种二元活动；先检查最小反馈抽象，再比较另一条规则。' if not second else '沿用两节点粒度，比较新的规则；保留第一版结果。',
        modeling_targets=['活动 A 与 B 的状态变化'],rationale='这是合成开发控制，用于验证研究迭代流程。',
        evidence_needs=[dict(id='N1',question='实际场景有哪些观测量可以检验这些候选？',kind='data',queries=['feedback intervention observable'])],
        assumptions=[dict(id='H2' if second else 'H1',statement='采用第二个明确逻辑假设。' if second else '采用第一个明确逻辑假设。',
            rationale='只作为合成建模假设，尚无生物学依据。',evidence_ids=[],decision='adopt')],
        criteria=[dict(id='C1',kind='native_checks',description='现有原生数值/轨迹检查应通过。',required=True)],
        evidence=[],unresolved=['生物学适用性与真实数据验证尚未完成。'])


def model(second=False):
    result=json.loads((ROOT/'examples/model-types/boolean.json').read_text())
    if second:result['nodes'][0]['rule']='B'
    return result


class Control:
    model=MODEL
    base_url=BASE
    timeout_s=60
    max_tokens=4000
    json_mode=False
    last_usage={}
    def __init__(self):
        self.planner_calls=0;self.native_calls=0;self.messages=[];self.inspected=set()
        self.native_actions=[action('ask_researcher',question='请确认本次合成开发模型的研究粒度。'),
            action('run_typed'),action('finish'),action('run_typed'),action('finish')]
    def respond(self,messages):
        self.messages.append(deepcopy(messages))
        if not messages[0]['content'].startswith('You are the research lead'):
            self.native_calls+=1
            raise ValueError('Complete models should execute directly; no inner model response was scripted.')
        self.planner_calls+=1
        state=json.loads(messages[-1]['content']);view=state['project'];iterations=state['iterations']
        if not view['frame_ref']:return action('revise_frame',frame=frame(),reason='由 Agent 从应用场景提出研究对象、粒度和假设。')
        if not iterations and not view['reply_refs']:
            return action('ask_alignment',category='granularity',question='本次先比较群体趋势，还是需要更细的个体行为？',affected_fields=['granularity'],options=['先做最小抽象比较','需要更细的个体行为'])
        if not iterations:
            return action('open_iteration',reason='冻结第一版合成假设。',config=dict(model_type='boolean',execution=dict(settings={'steps':4,'seed':123})),native_draft=model(),hypothesis_ids=['H1'])
        first=iterations[0];actual=first['facts'];first_ref=view['iteration_refs'][0]
        if actual['state']=='awaiting_input':
            if view['reply_refs']:return action('resume_iteration',iteration_ref=first_ref,reply_ref=view['reply_refs'][-1])
            return action('ask_alignment',category='granularity',question='本次先比较群体趋势，还是需要更细的个体行为？',
                          affected_fields=[first_ref,'granularity'],options=['先做最小抽象比较','需要更细的个体行为'])
        current_ref=view['iteration_refs'][-1];current=iterations[-1]
        if current['facts']['state']=='ready_to_run':return action('run_iteration',iteration_ref=current_ref)
        if current_ref not in self.inspected:
            self.inspected.add(current_ref);return action('inspect_iteration',iteration_ref=current_ref)
        if len(view['assessment_refs'])<len(iterations):
            return action('assess_iteration',iteration_ref=current_ref,interpretation='revise' if len(iterations)==1 else 'consistent',
                rationale='原生检查已完成；继续比较一个新假设。' if len(iterations)==1 else '第二版通过冻结检查；结论仅限这两个合成假设。',
                evidence_ids=[])
        if len(iterations)==1:
            if view['frame']['revision']==1:return action('revise_frame',frame=frame(True),reason='基于第一版结果，提出可区分的新规则。')
            return action('open_iteration',reason='创建与第一版关联的新模型，保留原结果和累计预算。',parent_iteration_ref=first_ref,
                config=dict(model_type='boolean',execution=dict(settings={'steps':4,'seed':123})),native_draft=model(True),hypothesis_ids=['H2'])
        return action('conclude',outcome='completed_with_limits',summary='完成两个合成假设模型的计算比较；尚不能判断生物学适用性。',
            assessment_refs=[view['assessment_refs'][-1]],limitations=['所有模型响应为脚本。','没有真实文献/数据验证或独立领域审核。'])


class Factory:
    def __init__(self):self.control=Control()
    def __call__(self,identity):return self.control


class Client(TaskClient):
    def submit(self,operation,params,*,key=None,expected='returned'):
        if operation!='run_project':return super().submit(operation,params,key=key,expected=expected)
        from uuid import uuid4
        preview=self.request('/api/preview',dict(operation=operation,params=params))
        extra=dict(provider_consent=True,scope_sha256=preview['scope_sha256']) if preview['requires_provider'] else {}
        job=self.request('/api/jobs',dict(key=key or uuid4().hex,operation=operation,params=params,**extra),expected=202)
        deadline=time.monotonic()+180
        while job['state'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.05);job=self.request('/api/job/'+job['job_ref'])
        assert job['state']==expected,job
        return job


def setup(output,port=0):
    out=Path(output).resolve();out.mkdir(exist_ok=False);factory=Factory()
    app=Workbench(out/'store',allow_provider=True,base_url=BASE,model=MODEL,provider_factory=factory)
    server=Server(app,port);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);thread.start()
    return out,factory,app,server,thread,Client(app,server,out)


def run(output):
    out,factory,app,server,thread,client=setup(output)
    try:
        project=client.submit('create_project',dict(config=dict(scenario='合成开发场景：比较反馈干预下的两个模型假设。',
            policy=dict(planner_max_calls=20,max_iterations=3,max_native_calls=18))))['result']['project_ref']
        client.submit('run_project',dict(project_ref=project))
        paused=client.request('/api/project/'+project)
        assert paused['state']=='awaiting_alignment' and not paused['iteration_refs']
        assert not app.store.list('typed_run')
        client.submit('reply_project',dict(project_ref=project,question_ref=paused['pending_questions'][0],
            reply='先做最小抽象比较，保留第一版冻结模型作为明确假设。',respondent='合成 HTTP 演练'))
        final_job=client.submit('run_project',dict(project_ref=project))
        final=client.request('/api/project/'+project)
        assert final['state']=='completed_with_limits' and len(final['iteration_refs'])==2
        assert final['budget']['native_reserved']==0 and factory.control.native_calls==0
        assert sum(projects.facts(app.readonly,r)['executed'] for r in final['iteration_refs'])==2
        values=[]
        for ref in final['iteration_refs']:
            fact=projects.facts(app.readonly,ref);inp,math,data=typed_models.result(app.readonly,fact['application_run_ref'])
            values.append(dict(model=math,settings=inp['settings'],result=data))
        (out/'numerical-data.json').write_text(json.dumps(values,indent=2))
        snapshot=final_job['result']['snapshot_ref']
        report=client.request('/downloads/report/'+snapshot+'.md',binary=True);(out/'report.md').write_bytes(report)
        exported=client.submit('export_bundle',dict(ref=snapshot));archive=client.request('/downloads/bundle/'+exported['job_ref']+'.zip',binary=True)
        (out/'bundle.zip').write_bytes(archive)
        import tempfile,zipfile,io
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                assert all(n.startswith('bundle/') and '..' not in Path(n).parts for n in z.namelist());z.extractall(tmp)
            assert reporting.render_report(ArtifactStore(Path(tmp)/'bundle'),snapshot).encode()==report
        def case(cid,material,family):
            return dict(id=cid,paper_id='synthetic:'+material,material_kind='synthetic_control',family=family,
                project=dict(scenario='Reproduce the supplied development input; report missing material honestly.',
                    mode='reproduction',policy=dict(planner_max_calls=2,max_iterations=1,max_native_calls=6)))
        batch=pipeline.create(app.store,dict(name='Synthetic development gap queue',lessons_max_calls=1,
            cases=[case('first','missing_initial','linear'),case('repeat','missing_initial','linear'),case('different','missing_rule','logic')]))
        providers=[]
        def make(_):
            provider=Scripted([action('conclude',outcome='insufficient_evidence',summary='No complete mathematical material was supplied.',
                assessment_refs=[],limitations=['Synthetic control, no scientific execution.'])]);providers.append(provider);return provider
        pipeline.run(app.store,batch,make);pipeline.run(app.store,batch,lambda _:(_ for _ in ()).throw(ValueError('must not repeat')))
        lesson=Scripted([dict(lessons=[dict(statement='Inspect input completeness before freezing a reproduction experiment.',
            case_ids=['first','repeat','different'],conditions='Seen synthetic development controls only.',
            validation_plan='Test on additional development papers and families; do not generalize parameter values.')])])
        experiences=pipeline.lessons(app.store,batch,lesson);view=pipeline.inspect(app.readonly,batch)
        assert view['case_count']==3 and view['unique_paper_count']==0 and view['unique_material_count']==2 and view['unique_family_count']==2
        assert not app.store.get(experiences[0])['payload']['active']
        (out/'pipeline-summary.json').write_text(json.dumps(view,ensure_ascii=False,indent=2))
        summary=dict(implementation_sha256=recovery.implementation_fingerprint(),project_ref=project,snapshot_ref=snapshot,
            state=final['state'],planner_responses=factory.control.planner_calls,native_responses=factory.control.native_calls,
            actual_scientific_calls=2,linked_iterations=2,native_reserved=0,human_replies=1,detached_report_matches=True,
            pipeline=dict(cases=3,unique_materials=2,unique_papers=0,unique_families=2,scripted_responses=3,scientific_calls=0,
                          inactive_experience_candidates=len(experiences),lesson_responses=1),
            live_model_requests=0,scientific_support='unknown')
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));return summary
    finally:server.shutdown();server.server_close();app.close();thread.join(2.)


def serve(output,port):
    out,factory,app,server,thread,client=setup(output,port)
    info=dict(url=server.origin+'/projects',scripted_model=MODEL,live_model_requests=0,synthetic=True)
    (out/'launch.json').write_text(json.dumps(info,indent=2));print(json.dumps(info),flush=True)
    try:
        while thread.is_alive():time.sleep(.25)
    except KeyboardInterrupt:pass
    finally:
        server.shutdown();server.server_close();app.close();thread.join(2.)
        summary=dict(implementation_sha256=recovery.implementation_fingerprint(),planner_responses=factory.control.planner_calls,
            native_responses=factory.control.native_calls,scientific_calls=len(app.store.list('typed_run')),live_model_requests=0,scientific_support='unknown')
        (out/'summary.json').write_text(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if args.serve:serve(args.output,args.port)
    else:print(json.dumps(run(args.output),ensure_ascii=False,indent=2))

