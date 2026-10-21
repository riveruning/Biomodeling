"""Run frozen reliability trials. Never reads evaluation answers in agent contexts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from competitor_common import verify_source, OUT as VENDOR_OUT
from prepare_reliability import ROOT, OUT, dump


def verify_freeze():
    for relative,digest in json.loads((OUT/'freeze.json').read_text()).items():
        if sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen input changed: '+relative)


def product_trial(source, dest, times, key_env):
    from biomodel_agent import agent, evidence, application
    from biomodel_agent.storage import ArtifactStore
    from biomodel_agent.llm import ChatProvider
    from biomodel_agent.domain.contracts import ExperimentSpec
    from evaluate_literature import normalize_attempt
    store=ArtifactStore(dest/'store'); source_ref=evidence.ingest_source(store,source)
    provider=ChatProvider('https://api.llm.ustc.edu.cn/v1','qwen3.8-chat',key_env,timeout_s=90,max_tokens=4000)
    ref=agent.run_agent(store,provider,[source_ref],ExperimentSpec(tuple(times)),max_calls=8,max_repairs=2)
    payload=store.get(ref)['payload'];dump(dest/'agent.json',payload)
    prediction=normalize_attempt(payload,source_ref,'case',source.read_text())
    dump(dest/'prediction.json',prediction)
    if payload['run_ref']:
        run=store.get(payload['run_ref'])['payload'];dump(dest/'run.json',run)
        if run.get('simulation'):dump(dest/'trajectory.json',run['simulation'])
    return {'status':payload['state'],'calls':payload['call_count'],
      'tokens':sum(e.get('usage',{}).get('total_tokens',0) for e in payload['events']),
      'questions':[e['question'] for e in payload['events'] if e.get('type')=='awaiting_input'],
      'problems':[p for e in payload['events'] if e.get('tool')=='submit_draft' for p in e.get('problems',[])],
      'model_ref':payload['model_ref'],'run_ref':payload['run_ref'],'agent_ref':ref,
      'product_modules_sha256':application.environment_fingerprint()['modules_sha256']}


def kinmod_trial(source,dest,times,key_env):
    import httpx
    import tellurium as te
    verify_source('kinmodgpt')
    folder=VENDOR_OUT/'vendor/kinmodgpt/Conversion/6. KinModGPT_gpt-4'
    spec=importlib.util.spec_from_file_location('official_kinmod',folder/'KinModGPT.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    instruction=(folder/'Instruction.txt').read_text();responses=[]
    def transport(inst,description,unused):
        request={'model':'qwen3.8-chat','messages':[{'role':'system','content':inst},{'role':'user','content':description}],
                 'temperature':0,'max_tokens':6144,'top_p':1,'frequency_penalty':0,'presence_penalty':0}
        dump(dest/'request.json',request)
        with httpx.Client(trust_env=False,timeout=90) as client:
            response=client.post('https://api.llm.ustc.edu.cn/v1/chat/completions',json=request,
                                headers={'Authorization':'Bearer '+os.environ[key_env]})
        if response.status_code!=200:raise RuntimeError(f'provider_http_{response.status_code}')
        payload=response.json();responses.append(payload);dump(dest/'provider-response.json',payload)
        return payload
    module.talk_to_GPT=transport
    try:
        _,_,raw,antimony,sbml=module.KinModGPT(instruction,[source.read_text()],None,verbose=0)
        (dest/'generated-antimony.txt').write_text(antimony,encoding='utf-8')
        (dest/'generated-sbml.xml').write_text(sbml,encoding='utf-8')
        rr=te.loadSBMLModel(sbml)
        # Selection names from public source's Initial lines, never evaluator gold.
        import re
        species=re.findall(r'^Initial (\w+) ',source.read_text(),re.MULTILINE)
        data=rr.simulate(times=times,selections=['time']+species)
        dump(dest/'trajectory.json',{'times':data[:,0].tolist(),'values':{k:data[:,i+1].tolist() for i,k in enumerate(species)}})
        record={'status':'executed','initial_values':{k:float(rr[f'init({k})']) for k in species},
                'parameters':{k:float(rr[k]) for k in rr.getGlobalParameterIds()}}
    except Exception as exc:
        record={'status':'failed','error_type':type(exc).__name__,'error':str(exc)[:1800]}
        if responses:
            (dest/'raw-output.txt').write_text(responses[-1]['choices'][0]['message']['content'],encoding='utf-8')
    return {**record,'calls':len(responses),'tokens':sum(p.get('usage',{}).get('total_tokens',0) for p in responses),
            'variant':'official_original_prompt_alternative_LLM_compatible_runtime','questions':[],
            'native_clarification_capability':'not_available'}


def codex_trial(source,dest,times,key_env):
    # Fresh outside-project folder, no gold, references, product schema or other trial.
    import tempfile
    work=Path(tempfile.mkdtemp(prefix='biomodel-reliability-'))
    for name in ['source.txt','experiment.json','TASK.md']:shutil.copyfile(source.parent/name,work/name)
    prompt=('Read TASK.md and source.txt. Complete this one independent evaluation task now. '
            'Do not spawn agents or inspect outside files. Do not run generated Python; it will be independently reviewed and executed. '
            'Write answer.json and simulate.py in the current directory. '
            'The evaluator supplies Python with numpy/scipy later. No package installation is needed.')
    cmd=['/home/riveruning/.local/bin/codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
         '--sandbox','workspace-write','--model','gpt-6.1-sol','--json','-C',str(work),prompt]
    clean_env={k:v for k,v in os.environ.items() if k not in {key_env,'BIOMODEL_API_KEY','USTC_API_KEY','OPENAI_API_KEY'}}
    dump(dest/'invocation.json',{'command':cmd,'working_directory':str(work),'prompt':prompt,
                                'native_auth':True,'user_config_ignored':True})
    try:
        with (dest/'events.jsonl').open('w',encoding='utf-8') as output,(dest/'stderr.log').open('w',encoding='utf-8') as err:
            result=subprocess.run(cmd,stdout=output,stderr=err,env=clean_env,timeout=240)
        record={'status':'delivered' if result.returncode==0 else 'cli_failed','exit_code':result.returncode}
    except subprocess.TimeoutExpired:
        record={'status':'timed_out'}
    for name in ['answer.json','simulate.py']:
        if (work/name).is_file():shutil.copyfile(work/name,dest/name)
    usage={}
    for line in (dest/'events.jsonl').read_text().splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('type')=='turn.completed':usage=event.get('usage',{})
    return {**record,'model':'gpt-6.1-sol','usage':usage,'tokens':None,'calls':None,'workspace':str(work)}


def trial(system,name,phase,key_env):
    verify_freeze()
    # Read only public case source + experiment; protocol/reference not passed to systems.
    source=ROOT/'benchmarks/inputs/reliability'/name/phase/'source.txt'
    times=json.loads((source.parent/'experiment.json').read_text())['times']
    dest=OUT/system/name/phase
    dest.mkdir(parents=True,exist_ok=False)  # preserve every earlier trial; no silent retries
    started=time.monotonic()
    try:
        fn={'biomodel':product_trial,'kinmodgpt':kinmod_trial,'codex':codex_trial}[system]
        record=fn(source,dest,times,key_env)
    except Exception as exc:
        record={'status':'adapter_or_provider_failed','error_type':type(exc).__name__,'error':str(exc)[:1200]}
    record.update(system=system,case=name,phase=phase,elapsed_seconds=time.monotonic()-started)
    dump(dest/'result.json',record)
    print(json.dumps({k:record.get(k) for k in ['system','case','phase','status','calls','elapsed_seconds']},ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--system',choices=['biomodel','kinmodgpt','codex'],required=True)
    p.add_argument('--key-env',default='BIOMODEL_API_KEY');p.add_argument('--jobs',type=int,default=1)
    p.add_argument('--case');args=p.parse_args();verify_freeze()
    # Public directory enumeration only; no gold loaded by orchestration.
    jobs=[(folder.parent.name,folder.name) for folder in sorted((ROOT/'benchmarks/inputs/reliability').glob('*/*')) if (folder/'source.txt').exists()]
    if args.case:jobs=[pair for pair in jobs if pair[0]==args.case]
    if args.system=='kinmodgpt' and args.jobs!=1:raise ValueError('Tellurium global native state requires sequential trials')
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures=[pool.submit(trial,args.system,name,phase,args.key_env) for name,phase in jobs]
        for f in as_completed(futures):f.result()

if __name__=='__main__':main()
