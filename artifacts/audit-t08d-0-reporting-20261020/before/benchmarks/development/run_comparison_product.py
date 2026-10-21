"""Run the unchanged product in the frozen, explicitly developmental pilot."""
import argparse
import json
from pathlib import Path
import time

from biomodel_agent import agent, application, evidence, reporting
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.literature_scoring import score_extraction
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore
from evaluate_literature import normalize_attempt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/comparison-pilot-20261017'


def main(api_key_env):
    protocol = json.loads((OUT/'protocol.json').read_text())
    dest = OUT/'biomodel';dest.mkdir(exist_ok=True)
    for case, config in protocol['cases'].items():
        output = dest/case;output.mkdir(exist_ok=True)
        store = ArtifactStore(output/'store')
        source = ROOT/config['source'];text = source.read_text()
        source_ref = evidence.ingest_source(store,source)
        provider = ChatProvider('https://api.llm.ustc.edu.cn/v1','qwen3.8-chat',api_key_env,
                                timeout_s=90,max_tokens=4000)
        started = time.monotonic()
        ref = agent.run_agent(store,provider,[source_ref],ExperimentSpec(tuple(config['experiment']['times'])),
                              max_calls=8,max_repairs=2)
        payload = store.get(ref)['payload']
        prediction = normalize_attempt(payload,source_ref,'case',text)
        score = score_extraction(config['gold'],prediction,{'case':text})
        record = {'agent_ref':ref,'state':payload['state'],'run_ref':payload['run_ref'],
                  'calls':payload['call_count'],'repairs':payload['repair_count'],
                  'elapsed_seconds':time.monotonic()-started,
                  'usage_total_tokens':sum(e.get('usage',{}).get('total_tokens',0) for e in payload['events']),
                  'questions':[e['question'] for e in payload['events'] if e.get('type')=='awaiting_input'],
                  'environment':application.environment_fingerprint(),
                  'submission_diagnostics':[e for e in payload['events'] if e.get('tool')=='submit_draft'],
                  'score':score}
        for name,value in [('prediction.json',prediction),('result.json',record)]:
            (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        (output/'report.md').write_text(reporting.render_report(store,ref))
        print(case,json.dumps({'state':record['state'],'calls':record['calls'],'counts':score['counts']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--api-key-env',default='BIOMODEL_API_KEY')
    main(parser.parse_args().api_key_env)
