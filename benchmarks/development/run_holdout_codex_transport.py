"""All-pilot native baseline transport diagnostic, preserving the original failed attempt."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from holdout_sandbox import clean_environment, codex_command
from run_holdout_pilot import OUT as ORIGINAL, DATA, PROMPT, verify

OUT=ORIGINAL/'codex-transport-diagnostic'


def dump(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False))


def main():
    verify()
    OUT.mkdir(exist_ok=False)
    protocol=json.loads((ORIGINAL/'pilot-protocol.json').read_text())
    manifest=json.loads((ORIGINAL/'case_manifest.json').read_text())
    env=clean_environment()
    proxy_names=[k for k in ['HTTPS_PROXY','HTTP_PROXY','NO_PROXY','https_proxy','http_proxy','no_proxy','ALL_PROXY','all_proxy'] if k in os.environ]
    env.update({k:os.environ[k] for k in proxy_names})
    dump(OUT/'diagnostic-protocol.json',{'created_utc':datetime.now(timezone.utc).isoformat(),
       'reason':'Native baseline launcher waited for inherited non-terminal stdin and lacked the host proxy configuration.',
       'changes':['stdin explicitly closed using DEVNULL','preserve existing host HTTP(S) proxy variables; values never recorded'],
       'proxy_variable_names':proxy_names,'case_order':protocol['cases'],'all_9_fresh_phases':True,
       'unchanged':['native model','reasoning effort','task prompt','source and experiment','sandbox file mounts','wall limit'],
       'not_original_results':True,'launcher_sha256':sha256(Path(__file__).read_bytes()).hexdigest()})
    for cid in protocol['cases']:
        for phase,config in manifest['cases'][cid]['phases'].items():
            verify()
            work=OUT/'runs'/cid/phase;work.mkdir(parents=True)
            source=DATA/config['source']
            for name in ['source.txt','experiment.json','TASK.md']:shutil.copyfile(source.parent/name,work/name)
            dest=work/'delivery';dest.mkdir()
            command=codex_command(work,PROMPT)
            dump(dest/'invocation.json',{'command':command,'stdin':'DEVNULL','proxy_values_logged':False})
            started=time.monotonic()
            try:
                with (dest/'events.jsonl').open('w') as log,(dest/'stderr.log').open('w') as err:
                    result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=err,timeout=240)
                record={'status':'delivered' if result.returncode==0 else 'cli_failed','exit_code':result.returncode}
            except subprocess.TimeoutExpired:record={'status':'timed_out'}
            events=[]
            for line in (dest/'events.jsonl').read_text().splitlines():
                try:events.append(json.loads(line))
                except ValueError:pass
            record.update(system='codex',case=cid,phase=phase,elapsed_seconds=time.monotonic()-started,
                          model='gpt-6.1-sol',usage=next((e.get('usage',{}) for e in reversed(events) if e.get('type')=='turn.completed'),{}))
            dump(dest/'result.json',record);print(json.dumps(record),flush=True)
            if record['status']!='delivered':raise RuntimeError('native runtime still unavailable; stop remaining diagnostic trials')
    dump(OUT/'completed.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'phases':9,'candidate_unchanged':True})


if __name__=='__main__':main()
