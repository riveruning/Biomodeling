"""Development verification on the already-seen regression cases, never the holdout.

Calls the original task adapter with the updated default product. Successful
provider bodies are observed for diagnosis; no response correction is performed.
"""
import json
from pathlib import Path
import shutil
import time

from biomodel_agent import llm, application
from competitor_common import write_json
import run_reliability as runner
from prepare_reliability import ROOT

BASE=ROOT/'artifacts/reliability-20261017'
OUT=ROOT/'artifacts/agent-reliability-fix-20261017/live-regression'


def main():
    import argparse
    global OUT
    parser=argparse.ArgumentParser()
    parser.add_argument("--json-mode",action="store_true")
    parser.add_argument("--output",type=Path,default=OUT)
    args=parser.parse_args()
    OUT=args.output.resolve()
    if not OUT.is_relative_to(ROOT/"artifacts"):
        raise ValueError("Development outputs must stay in project artifacts")
    if OUT.exists():raise FileExistsError('Preserve earlier trials; use a separate dated run for another development iteration')
    OUT.mkdir()
    for name in ['freeze.json','protocol.json']:shutil.copyfile(BASE/name,OUT/name)
    shutil.copytree(BASE/'references',OUT/'references')
    runner.OUT=OUT
    runner.verify_freeze()
    write_json(OUT/'run-protocol.json',{
      'scope':'already-seen 10-case/18-input development regression, not a held-out benchmark',
      'model':'qwen3.8-chat','json_mode':args.json_mode,'temperature':0,'calls':8,'repairs':2,'max_retries':2,
      'pacing_seconds_before_every_request':5,'attempts_per_input':1,
      'changes':'updated product prompt, budgeted retries, explicit source review and execution gate',
      'holdout_accessed':False,'environment':application.environment_fingerprint()})
    original=llm.ChatProvider
    class ObservedPacedProvider(original):
        current_destination=None
        def __init__(self,*provider_args,**kwargs):
            kwargs["json_mode"]=args.json_mode
            super().__init__(*provider_args,**kwargs)
        def _post(self,url,payload,headers):
            time.sleep(5)
            status,body=super()._post(url,payload,headers)
            dest=self.current_destination
            if status==200 and dest is not None:
                try:record=json.loads(body)
                except ValueError:pass
                else:write_json(dest/f'observed-response-{len(list(dest.glob("observed-response-*.json")))+1}.json',record)
            return status,body
    llm.ChatProvider=ObservedPacedProvider
    # Explicit ordering is public case/phase names, not scores or gold answers.
    jobs=[(folder,phase) for folder in sorted((ROOT/'benchmarks/inputs/reliability').iterdir())
          for phase in ['initial','clarified'] if (folder/phase/'source.txt').exists()]
    # Diagnostic order is frozen before the run; every input still gets one attempt.
    if args.json_mode:
        jobs.sort(key=lambda pair: 0 if (pair[0].name,pair[1])==('unit_decay','clarified') else 1)
    write_json(OUT/'case-order.json',[(folder.name,phase) for folder,phase in jobs])
    for folder,phase in jobs:
        ObservedPacedProvider.current_destination=OUT/'biomodel'/folder.name/phase
        runner.trial('biomodel',folder.name,phase,'BIOMODEL_API_KEY')
        result=json.loads((ObservedPacedProvider.current_destination/'result.json').read_text())
        saved=ObservedPacedProvider.current_destination/'agent.json'
        events=json.loads(saved.read_text())['events'] if saved.exists() else []
        if any(e.get('type')=='retry_deferred' for e in events):
            raise RuntimeError('Provider requested a longer cooldown; stop remaining development calls')
        if args.json_mode and result.get('status')=='failed' and any('HTTP 400' in e.get('error','') for e in events):
            raise RuntimeError('Provider rejected JSON-mode request; preserve diagnostic and stop')
        if result.get('status')=='failed' and any('HTTP 429' in e.get('error','') for e in events):
            raise RuntimeError('Provider rate limit persists; stop remaining development calls')
        if result.get('status')=='adapter_or_provider_failed':
            raise RuntimeError('Adapter failed; stop further paid calls and inspect saved diagnostic')
    llm.ChatProvider=original


if __name__=='__main__':main()
