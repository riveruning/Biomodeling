"""Run the predeclared isolated pilot; first attempts are never overwritten."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from holdout_sandbox import clean_environment,codex_command,product_command

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/holdout-evaluation-20261017'
DATA=Path('/home/riveruning/biomodel_agent_evalset')
PROMPT=('Read TASK.md, source.txt and experiment.json. Complete this one task using only this workspace. '
        'Write answer.json and simulate.py even when blocked. Do not read credentials, browse, install packages or delegate. '
        'Do not run the generated simulation here; an independent evaluator will review and execute it with numpy/scipy. '
        'If blocked, record a specific clarification question in answer.json and stop. Do not ask the operator during this run.')


def dump(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False))


def verify():
    freeze=json.loads((OUT/'candidate-freeze.json').read_text())
    for name,digest in freeze['candidate_files'].items():
        assert sha256((OUT/'candidate'/name).read_bytes()).hexdigest()==digest, name
    for name,digest in json.loads((OUT/'dataset-freeze.json').read_text()).items():
        assert sha256((DATA/name).read_bytes()).hexdigest()==digest, name
    protocol=json.loads((OUT/'pilot-protocol.json').read_text())
    for name,digest in protocol['adapter_sha256'].items():
        assert sha256((Path(__file__).parent/name).read_bytes()).hexdigest()==digest, name


def product(work,phase,key):
    output=work/phase
    if output.exists():raise FileExistsError(output)
    cmd=product_command(work,phase,Path(__file__).with_name('run_holdout_product.py'))
    started=time.monotonic()
    with (work/f'{phase}-launcher.log').open('w') as log:
        result=subprocess.run(cmd,env=clean_environment(key),stdout=log,stderr=subprocess.STDOUT,timeout=1000)
    if result.returncode:
        raise RuntimeError(f'product child exit {result.returncode}; see {work.name}/{phase}-launcher.log')
    record=json.loads((output/'result.json').read_text())
    assert record['modules_sha256']==json.loads((OUT/'candidate-freeze.json').read_text())['environment']['modules_sha256']
    events=json.loads((output/'agent.json').read_text())['events']
    persistent_rate=record['status']!='completed' and any(e.get('type')=='provider_error' and '429' in e.get('error','') for e in events)
    fatal=any(e.get('type')=='provider_error' and ('401' in e.get('error','') or '403' in e.get('error','')) for e in events)
    dump(output/'orchestration.json',{'exit_code':result.returncode,'wall_seconds':time.monotonic()-started,'candidate_hash_verified':True})
    if persistent_rate or fatal:raise RuntimeError('provider failure; stop paid requests without selective reruns')
    return record


def codex(work):
    dest=work/'delivery';dest.mkdir(exist_ok=False)
    cmd=codex_command(work,PROMPT)
    dump(dest/'invocation.json',{'command':cmd,'environment':'credential-free whitelist; native auth read-only runtime mount',
        'created_utc':datetime.now(timezone.utc).isoformat(),'input_files':['source.txt','experiment.json','TASK.md']})
    started=time.monotonic()
    try:
        with (dest/'events.jsonl').open('w') as log,(dest/'stderr.log').open('w') as err:
            run=subprocess.run(cmd,env=clean_environment(),stdout=log,stderr=err,timeout=240)
        record={'status':'delivered' if run.returncode==0 else 'cli_failed','exit_code':run.returncode}
    except subprocess.TimeoutExpired:
        record={'status':'timed_out'}
    events=[]
    for line in (dest/'events.jsonl').read_text().splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    record.update(elapsed_seconds=time.monotonic()-started,model='gpt-6.1-sol',
                  usage=next((e.get('usage',{}) for e in reversed(events) if e.get('type')=='turn.completed'),{}),
                  action_events=sum(e.get('type')=='item.completed' for e in events))
    dump(dest/'result.json',record)
    return record


def main():
    p=argparse.ArgumentParser();p.add_argument('--system',choices=['biomodel','codex'],required=True);a=p.parse_args()
    verify()
    protocol=json.loads((OUT/'pilot-protocol.json').read_text())
    manifest=json.loads((OUT/'case_manifest.json').read_text())
    launcher=OUT/f'{a.system}-run-plan.json'
    if launcher.exists():raise FileExistsError('run already started; do not overwrite first attempts')
    dump(launcher,{'created_utc':datetime.now(timezone.utc).isoformat(),'launcher_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
                   'system':a.system,'case_order':protocol['cases'],'codex_prompt':PROMPT})
    key=None
    if a.system=='biomodel':
        sys.path.append('/usr/lib/python3/dist-packages')
        import yaml
        key=yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())['refs']['USTC_API_KEY']
    for cid in protocol['cases']:
        for phase in manifest['cases'][cid]['phases']:
            verify();work=OUT/'runs'/a.system/cid/phase
            record=product(work,phase,key) if a.system=='biomodel' else codex(work)
            print(json.dumps({'system':a.system,'case':cid,'phase':phase,**record},ensure_ascii=False),flush=True)
            if a.system=='biomodel' and phase=='incomplete' and manifest['cases'][cid].get('clarification'):
                if record['status']=='awaiting_input' and record['calls']<8:
                    shutil.copyfile(DATA/manifest['cases'][cid]['clarification'],work/'researcher-reply.txt')
                    resumed=product(work,'resume',key)
                    print(json.dumps({'system':a.system,'case':cid,'phase':'resume',**resumed},ensure_ascii=False),flush=True)
                else:
                    dump(work/'resume-not-attempted.json',{'reason':'initial run was not resumable within the frozen total budget',
                                                          'initial_status':record['status'],'calls':record['calls']})
            if a.system=='codex' and record['status']!='delivered':
                raise RuntimeError('native agent runtime failed; stop remaining baseline trials')
    verify()
    dump(OUT/f'{a.system}-completed.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'candidate_unchanged':True,'dataset_unchanged':True})


if __name__=='__main__':main()
