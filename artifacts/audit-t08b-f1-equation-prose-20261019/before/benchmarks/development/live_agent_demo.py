"""Optional development integration using the existing configured USTC route.

No secrets are copied to files or output. The product reads only an environment
credential; this developer fixture resolves the already configured reference.
"""
from pathlib import Path
import argparse
import json
import os
import sys
from biomodel_agent import agent,evidence,reporting
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='claude-sonnet-4-6')
    args=parser.parse_args()
    # System YAML parser is read for this developer diagnostic only, never installed
    # into conda base or required by the product package.
    sys.path.append('/usr/lib/python3/dist-packages')
    import yaml
    credentials=yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())
    os.environ['BIOMODEL_DEMO_API_KEY']=credentials['refs']['USTC_API_KEY']
    del credentials
    root=Path(__file__).resolve().parents[2]
    store=ArtifactStore(root/'artifacts/v0.1/live-agent-store')
    source=evidence.ingest_source(store,root/'benchmarks/inputs/synthetic-birth-death.txt')
    provider=ChatProvider('https://api.llm.ustc.edu.cn/v1',args.model,api_key_env='BIOMODEL_DEMO_API_KEY',timeout_s=90,max_tokens=1800)
    try:
        ref=agent.run_agent(store,provider,[source],ExperimentSpec((0.,.5,1.,2.,5.)),max_calls=8,max_repairs=2)
    finally:
        os.environ.pop('BIOMODEL_DEMO_API_KEY',None)
    p=store.get(ref)['payload']
    summary={'status':p['state'],'agent_ref':ref,'run_ref':p['run_ref'],
             'model':args.model,
             'calls':p['call_count'],'repairs':p['repair_count'],
             'usage_total_tokens':sum(e.get('usage',{}).get('total_tokens',0) for e in p['events']),
             'scope':'one live extraction/execution of a curated synthetic specification; not a paper benchmark or baseline advantage proof'}
    (root/'artifacts/v0.1/live-agent.json').write_text(json.dumps(summary,indent=2))
    (root/'artifacts/v0.1/live-agent.md').write_text(reporting.render_report(store,ref))
    if p['run_ref']:
        (root/'artifacts/v0.1/live-agent-computation.md').write_text(reporting.render_report(store,p['run_ref']))
    print(json.dumps(summary,indent=2))
