"""One-shot, bounded paired DEVELOPMENT pilot. Preparation never calls a model.

All arms use the same frozen implementation. Baseline ablates only the new
deep ODE decision-plan schema and family procedures. Advice is synthetic,
operator-curated workflow advice, not automatically activated model lessons.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
PROTOCOL=ROOT/'benchmarks/development/next-evaluation-20261101.json'


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def emit(**value):print(json.dumps(value,ensure_ascii=False),flush=True)


def reference(case):
    kind=case['model_type'];times=[i/2 for i in range(21)]
    if kind=='ode':return dict(model_type=kind,reference=dict(status='succeeded',times=times,time_unit='second',
        values={'x':[10/(1+9*math.exp(-.6*t)) for t in times]},backend='independent_logistic_formula'),
        scales={'x':10.},threshold=.001,note='Synthetic logistic analytic control, not domain review.')
    if kind=='fba':return None
    from biomodel_agent.typed_references import PROFILES
    units=dict(time='sweep') if kind=='boolean' else dict(time='second',state='molecule_count') if kind=='stochastic' else json.loads((ROOT/case['path']).read_text())['units']
    spec=dict(schema_version=1,model_type=kind,units=units,profile=PROFILES[kind])
    if kind=='boolean':spec.update(steps=[0,1,2,3,4],nodes=['A','B'],states=[[False,False],[True,False],[True,True],[False,True],[False,False]])
    elif kind=='stochastic':
        t=[0.,.5,1.,1.5,2.];s=[math.exp(-.5*x) for x in t]
        spec.update(times=t,species=['X'],mean=[[10-6*x] for x in s],variance=[[4*x*(1-x)+10*(1-x)] for x in s],
            mean_scale=20.,variance_scale=20.,threshold=.15,min_trajectories=64)
    elif kind=='pde':spec.update(times=[0.,1.,2.],x=[(i+.5)/8 for i in range(8)],values=[[math.exp(-.1*t)]*8 for t in range(3)],scale=1.,threshold=1e-8)
    else:spec.update(steps=[0,1,2,3],population=[2]*4,scale=2.,threshold=1e-8)
    return dict(model_type=kind,reference=spec,note='Frozen independent synthetic control formula/sequence; not independent biological review.')


def prepare(output):
    manifest=json.loads(PROTOCOL.read_text())
    if manifest['approval']!='user_approved_20261101':raise ValueError('This new material/budget scope requires the recorded user authorization.')
    output.mkdir(parents=True,exist_ok=False)
    shutil.copytree(ROOT/'src/biomodel_agent',output/'source_snapshot'/'biomodel_agent',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    sys.path.insert(0,str(output/'source_snapshot'))
    from biomodel_agent.recovery import implementation_fingerprint
    manifest.update(implementation_sha256=implementation_fingerprint(),runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        arms_method='All arms share current code. Baseline removes only deep decision_plan schema and scientific_procedures. Advice arm adds synthetic operator workflow advice; no learned lesson is activated.')
    for case in manifest['cases']:
        raw=(ROOT/case['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=case['sha256']:raise ValueError('Frozen development input changed')
        dest=output/'inputs'/case['id']/Path(case['path']).name;dest.parent.mkdir(parents=True);dest.write_bytes(raw)
        case['frozen_path']=str(dest.relative_to(output));case['reference']=reference(case)
    shutil.copy2(Path(__file__),output/'runner.py')
    write(output/'manifest.json',manifest)
    emit(event='frozen',projects=18,implementation_sha256=manifest['implementation_sha256'],external_calls=0)


class GuidanceProvider:
    def __init__(self,provider,store,project_ref,arm):
        self.provider=provider;self.store=store;self.project_ref=project_ref;self.arm=arm;self.calls=0
    def __getattr__(self,key):return getattr(self.provider,key)
    def respond(self,messages):
        packet=deepcopy(messages)
        for message in packet:
            if message['role']!='user':continue
            try:value=json.loads(message['content'])
            except (ValueError,TypeError):continue
            if not isinstance(value,dict):continue
            if self.arm=='baseline_without_new_guidance':
                value.pop('scientific_procedures',None)
                plan=value.get('action_schemas',{}).get('open_iteration',{}).get('properties',{}).get('args',{}).get('properties',{})
                if 'decision_plan' in plan:plan['decision_plan']=dict(type='object')
            if self.arm!='stage_guidance_and_scoped_advice':value.pop('scoped_advice',None)
            message['content']=json.dumps(value,ensure_ascii=False)
        self.calls+=1
        self.store.put('development_transmission',dict(version=1,project_ref=self.project_ref,arm=self.arm,
            sequence=self.calls,messages=packet,authority='actual_filtered_development_messages_no_credentials'),refs=(self.project_ref,))
        return self.provider.respond(packet)


def run_case(output,manifest,case,arm):
    from biomodel_agent import evidence,fba,typed_models,memory,reporting
    from biomodel_agent import research_projects as p,research_supervisor as supervisor
    from biomodel_agent.storage import ArtifactStore
    from biomodel_agent.llm import ChatProvider
    directory=output/case['id']/arm;directory.mkdir(parents=True,exist_ok=False)
    store=ArtifactStore(directory/'store');source_refs=[];model_refs=[];reference_refs=[]
    path=output/case['frozen_path'];kind=case['model_type']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=case['sha256']:raise ValueError('Frozen source changed')
    if case['input_type']=='source':source_refs=[evidence.ingest_source(store,path)]
    else:model_refs=[fba.import_model(store,path) if kind=='fba' else typed_models.import_model(store,path)]
    if case['reference']:reference_refs=[p.register_reference(store,case['reference'],'development_trial_operator')]
    settings=dict(boolean='4 steps, seed 123',stochastic='duration 2 seconds, 5 samples, 128 trajectories, seed 123',
        pde='duration 2 seconds, 3 samples',individual='3 steps, seed 123',fba='default steady-state solver')
    scenario=('Synthetic development control, not a paper reproduction. Use only the authorized input. '
        'Read supplied source declarations before framing, or reuse the prepared model. '
        'Use direct deterministic execution, native_agent:false. Use the frozen external reference when supplied. '
        'Freeze native_checks and, when a reference is supplied, reference_comparison criteria. '
        'Run, inspect actual results, assess and conclude within the allocated 10 planning calls. '
        'Never invent missing source evidence or claim biological validation. '
        +('ODE: reproduce the explicitly reported logistic model using a full decision_plan; sample 0..10 seconds every 0.5 seconds.' if kind=='ode'
          else kind+': '+settings[kind]+'.'))
    limits=manifest['limits'];policy=dict(planner_max_calls=limits['planner_calls_per_project'],max_iterations=1,
        max_native_calls=limits['native_calls_per_project'],native_calls_per_iteration=limits['native_calls_per_project'],
        total_timeout_s=limits['total_timeout_s_per_project'],max_literature_requests=0)
    config=dict(scenario=scenario,mode='reproduction' if source_refs else 'application',source_refs=source_refs,
        model_refs=model_refs,reference_refs=reference_refs,policy=policy)
    if arm=='stage_guidance_and_scoped_advice':
        project_id='followup_'+case['id'];conditions=dict(model_type=kind)
        memory.remember(store,project_id=project_id,category='experience',author='development_trial_operator',
            source_refs=source_refs,model_ref=model_refs[0] if model_refs else None,conditions=conditions,
            content='Workflow note: inspect exact source declarations, preserve explicit units and conditions, use direct execution for a complete model, then assess and conclude before the planning budget ends. This supplies no parameter or scientific evidence.')
        config['advice']=dict(project_id=project_id,query='workflow',conditions=conditions,**(dict(model_ref=model_refs[0]) if model_refs else {}))
    root=p.create(store,config);cfg=manifest['provider']
    provider=GuidanceProvider(ChatProvider(cfg['base_url'],cfg['model'],timeout_s=cfg['timeout_s'],max_tokens=cfg['max_tokens']),store,root,arm)
    started=time.monotonic();error=None
    try:snapshot=supervisor.run(store,provider,root)
    except Exception as exc:error=type(exc).__name__;snapshot=p.snapshot(store,root)
    view=p.inspect(store,root);facts=[p.facts(store,r) for r in view['iteration_refs']]
    attempts,returns,_=p.history(store,root)
    diagnostics=[dict(operation=v['operation'],state=returns[r][1]['state'],diagnostic=returns[r][1].get('diagnostic'))
        for r,v in attempts if r in returns and returns[r][1]['state']!='returned']
    result=dict(case_id=case['id'],arm=arm,family=case['family'],project_ref=root,snapshot_ref=snapshot,state=view['state'],
        facts=facts,usage=supervisor.usage(store,root),transmissions=provider.calls,elapsed_s=time.monotonic()-started,error_type=error,
        diagnostics=diagnostics,human_questions=[p.own(store,r,p.QUESTION,root)['question'] for r in view['pending_questions']],
        independent_review='not_run',scientific_support='unknown',complete_paper_reproductions=0)
    write(directory/'result.json',result);(directory/'report.md').write_text(reporting.render_report(store,snapshot),encoding='utf-8')
    return result


def execute(output):
    manifest=json.loads((output/'manifest.json').read_text());sys.path.insert(0,str(output/'source_snapshot'))
    from biomodel_agent.recovery import implementation_fingerprint
    if implementation_fingerprint()!=manifest['implementation_sha256']:raise ValueError('Frozen product changed')
    if not os.environ.get('BIOMODEL_API_KEY'):raise ValueError('Configure the key in the child environment; never pass it in arguments')
    with (output/'started.json').open('x',encoding='utf-8') as stream:json.dump(dict(started=True,no_automatic_resume=True),stream)
    results=[];arms=manifest['arms']
    for index,case in enumerate(manifest['cases']):
        # Rotate arm order across cases to reduce one obvious time-order confound.
        for offset in range(3):
            arm=arms[(index+offset)%3];emit(event='project_started',case=case['id'],arm=arm)
            result=run_case(output,manifest,case,arm);results.append(result);write(output/'results.json',results)
            calls=sum(x['usage']['planner_calls'] for x in results)
            native=sum(x['usage']['native_provider_calls'] or 0 for x in results)
            if calls>180 or native>72 or len(results)>18:raise RuntimeError('Approved cumulative budget exceeded')
            emit(event='project_finished',case=case['id'],arm=arm,state=result['state'],planner_calls=result['usage']['planner_calls'],
                executions=sum(x['executed'] for x in result['facts']),cumulative_planner_calls=calls)
    emit(event='finished',projects=len(results),planner_calls=sum(x['usage']['planner_calls'] for x in results))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--run-live',action='store_true');args=parser.parse_args();output=args.output.resolve()
    if args.run_live:execute(output)
    else:prepare(output)


if __name__=='__main__':main()
