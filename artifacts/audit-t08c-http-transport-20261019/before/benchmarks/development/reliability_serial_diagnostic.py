"""Separate, predeclared all-case paced diagnostic after initial provider rate limits.

Never overwrites initial results. Only transport pacing and raw response observation
change; input, prompt, parser, tools, accepted schemas, model and budgets stay fixed.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from prepare_reliability import ROOT, OUT as INITIAL, dump
import run_reliability as runner

SERIAL=ROOT/'artifacts/reliability-20261017-serial-diagnostic'


def child(system,name,phase,key_env):
    runner.OUT=SERIAL
    if system=='biomodel':
        import biomodel_agent.llm as llm
        original=llm.ChatProvider
        class ObservedPacedProvider(original):
            def _post(self,url,payload,headers):
                # Delay before every request, including initial request. No retry.
                time.sleep(5)
                status,body=super()._post(url,payload,headers)
                dest=SERIAL/system/name/phase
                existing=list(dest.glob('observed-response-*.json'))
                # Store only successful JSON bodies; no headers/credentials/error bodies.
                if status==200:
                    try:dump(dest/f'observed-response-{len(existing)+1}.json',json.loads(body))
                    except ValueError:pass
                return status,body
        llm.ChatProvider=ObservedPacedProvider
    runner.trial(system,name,phase,key_env)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',choices=['biomodel','kinmodgpt']);parser.add_argument('--case');parser.add_argument('--phase');parser.add_argument('--key-env',default='BIOMODEL_API_KEY');args=parser.parse_args()
    if args.child:return child(args.child,args.case,args.phase,args.key_env)
    if SERIAL.exists():raise FileExistsError('Diagnostic exists; refusing to overwrite')
    SERIAL.mkdir()
    for name in ['protocol.json','freeze.json','evaluation-code-freeze.json']:shutil.copyfile(INITIAL/name,SERIAL/name)
    shutil.copytree(INITIAL/'references',SERIAL/'references')
    protocol={'reason':'Initial trial provider 429 errors obscure capability; all 18 input conditions per system rerun in separate directory',
      'planned_systems':['biomodel','kinmodgpt'],'selection':'all cases and phases, not selected successes/failures',
      'changes':['sequential system execution','five-second pacing before every product request','five-second pause before KinModGPT trial','observe successful provider response JSON for parser diagnosis'],
      'unchanged':['input freeze','original prompts','model selection','original parser and tools','call/repair budgets','no output correction or retry'],
      'not_primary_results':True,'no_conversation_resume':True,
      'launcher_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    dump(SERIAL/'diagnostic-protocol.json',protocol)
    for system in ['biomodel','kinmodgpt']:
        interp=ROOT/('.venv/bin/python' if system=='biomodel' else 'artifacts/competitor-integration-20261017/runtime/bin/python')
        with (SERIAL/f'{system}-runner.log').open('w',encoding='utf-8') as log:
            for folder in sorted((ROOT/'benchmarks/inputs/reliability').glob('*/*')):
                if not (folder/'source.txt').exists():continue
                if system=='kinmodgpt':time.sleep(5)
                result=subprocess.run([str(interp),str(Path(__file__).resolve()),'--child',system,'--case',folder.parent.name,'--phase',folder.name,'--key-env',args.key_env],stdout=log,stderr=subprocess.STDOUT)
                log.flush()
                print(system,folder.parent.name,folder.name,'process_exit',result.returncode,flush=True)
                record=SERIAL/system/folder.parent.name/folder.name/'result.json'
                if record.exists():
                    r=json.loads(record.read_text())
                    if '429' in str(r.get('error','')):raise RuntimeError('Rate limiting persists; stop remaining diagnostic calls')
                    if system=='biomodel':
                        events=json.loads((record.parent/'agent.json').read_text())['events'] if (record.parent/'agent.json').exists() else []
                        if any('HTTP 429' in e.get('error','') for e in events):raise RuntimeError('Rate limiting persists; stop remaining diagnostic calls')

if __name__=='__main__':main()
