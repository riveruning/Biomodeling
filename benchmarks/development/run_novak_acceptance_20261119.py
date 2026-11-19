"""Bounded, seen-paper development diagnostic; never a reproduction score.

Live mode requires existing BIOMODEL_API_KEY credentials in the environment.
No automatic rerun, source repair or missing-parameter substitution.
Unknown/exhausted requests never replay; ordinary new planner actions remain counted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from biomodel_agent import evidence, recovery, research_projects as projects
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pdf',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    def save(name,value):
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    raw=args.pdf.read_bytes()
    expected='cbdd88e9caf1de838449679f225b313c4c2905c9672704478ae3ae155f74314f'
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Unexpected paper bytes.')
    store=ArtifactStore(args.output/'store')
    source=evidence.ingest_source(store,args.pdf)
    document=store.get(source)['payload']
    source_summary=dict(source_ref=source,pages=len(document['pages']),warnings=document['warnings'],
        characters=sum(len(page['text']) for page in document['pages']),sha256=expected,
        implementation_sha256=recovery.implementation_fingerprint())
    save('source.json',source_summary)
    if not args.live:
        print(json.dumps(dict(status='offline_ingestion_only',**source_summary)))
        return
    model=json.loads(Path('config/development-runtime.json').read_text())['model']
    if not model.startswith(('glm','deepseek')):raise ValueError('Disallowed model family.')
    if not os.environ.get('BIOMODEL_API_KEY'):raise ValueError('Provider credentials not configured.')
    # Exclusive marker preserves unknown outcomes if the process dies mid-POST.
    with (args.output/'live-started.json').open('x') as handle:
        json.dump(dict(model=model,reasoning_effort='max',max_tokens=12000,max_calls=3,
            started_at=time.time(),source_ref=source),handle)
    project=projects.create(store,dict(mode='reproduction',source_refs=[source],
        scenario=('Reproduction triage for Novak and Tyson, Design Principles of Biochemical Oscillators (2008). '
            'The first target is Figure 3c / equations (4), not MM-to-Expt literature classification. '
            'Inspect original source pages, especially the equation region and Figure 3 caption on page 20. '
            'Report whether the equations, parameters, units and initial conditions are actually available. '
            'This PDF is a review with multiple distinct models, image-only equations and unavailable supplements. '
            'Do not combine models, invent image contents, substitute guessed parameters, or fit a graph to fill missing declarations. '
            'If critical material is unreadable or missing, ask a precise alignment question and retain the gap. '
            'A read, model proposal or successful solver call alone is not a reproduced result.'),
        policy=dict(planner_max_calls=3,max_literature_requests=0,max_transcription_calls=0,
            max_iterations=1,max_native_calls=2,native_calls_per_iteration=2,total_timeout_s=600)))
    save('project.json',dict(project_ref=project))
    provider=ChatProvider('https://api.llm.ustc.edu.cn/v1',model,timeout_s=180,max_tokens=12000,
        json_mode=True,reasoning_effort='max')
    error=None
    try:snapshot=supervisor.run(store,provider,project)
    except Exception as exc:
        error=type(exc).__name__
        snapshot=projects.snapshot(store,project)
    attempts,returns,unknown=projects.history(store,project)
    save('result.json',dict(status='development_diagnostic_not_reproduction_success',
        project_ref=project,snapshot_ref=snapshot,view=projects.inspect(store,project),
        usage=supervisor.usage(store,project),error_type=error,unknown_attempts=unknown,
        operations=[dict(operation=a['operation'],state=returns[r][1]['state'] if r in returns else 'unknown')
            for r,a in attempts]))
    print(json.dumps(dict(result=str(args.output/'result.json'),state=projects.inspect(store,project)['state'])))


if __name__=='__main__':main()
