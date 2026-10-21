"""Frozen product child. Reads only single-stage inputs and its own earlier checkpoint."""
import argparse
import json
from pathlib import Path
import time
from biomodel_agent import agent, application, evidence
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


def dump(path, obj):
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False))


class ObservedPacedProvider(ChatProvider):
    def _post(self,url,payload,headers):
        time.sleep(5)
        status,body=super()._post(url,payload,headers)
        dest=Path('/work')/PHASE
        number=len(list(dest.glob('response-*.json')))+1
        if status==200:
            try:dump(dest/f'response-{number:02}.json',json.loads(body))
            except ValueError:pass
        return status,body


def main():
    global PHASE
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True)
    p.add_argument('--phase',choices=['incomplete','clarified','resume'],required=True)
    args=p.parse_args();PHASE=args.phase
    dest=args.work/PHASE;dest.mkdir(exist_ok=False)
    store=ArtifactStore(args.work/'store')
    provider=ObservedPacedProvider('https://api.llm.ustc.edu.cn/v1','qwen3.8-chat',
                                 timeout_s=90,max_tokens=4000,json_mode=True)
    started=time.monotonic()
    if PHASE=='resume':
        parent=json.loads((args.work/'incomplete/result.json').read_text())['agent_ref']
        ref=agent.resume_agent(store,provider,parent,(args.work/'researcher-reply.txt').read_text(),
                               respondent='Pre-written held-out researcher reply')
    else:
        source=evidence.ingest_source(store,args.work/'source.txt')
        experiment=json.loads((args.work/'experiment.json').read_text())
        ref=agent.run_agent(store,provider,[source],ExperimentSpec(tuple(experiment['times'])),
                            max_calls=8,max_repairs=2,max_retries=2)
    record=store.get(ref);payload=record['payload']
    dump(dest/'agent.json',payload)
    if payload.get('run_ref'):
        run=store.get(payload['run_ref'])['payload'];dump(dest/'run.json',run)
        if run.get('simulation'):dump(dest/'trajectory.json',run['simulation'])
    result={'agent_ref':ref,'status':payload['state'],'calls':payload['call_count'],
            'retries':payload['retry_count'],'repairs':payload['repair_count'],
            'model_ref':payload['model_ref'],'run_ref':payload['run_ref'],
            'questions':[e['question'] for e in payload['events'] if e.get('type')=='awaiting_input'],
            'tokens_cumulative':sum(e.get('usage',{}).get('total_tokens',0) for e in payload['events']),
            'elapsed_seconds':time.monotonic()-started,
            'modules_sha256':application.environment_fingerprint()['modules_sha256']}
    dump(dest/'result.json',result)
    print(json.dumps({'phase':PHASE,**result},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
