"""Explicit 90-second diagnostic, sharing the existing 18-attempt pilot cap.

Only the three previously authorized synthetic sources and USTC model are used.
Old receipts remain untouched. Each diagnostic is a new task, without retries.
"""
from copy import deepcopy
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

import native_plan_pilot as pilot
from biomodel_agent import agent, recovery, reporting, timeline
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.evidence import ingest_source
from biomodel_agent.llm import ProviderError
from biomodel_agent.storage import ArtifactStore

MAXIMUM_ATTEMPTS = 18


def attempts(root):
    # Reserved calls count too, including the old sandbox connection failure.
    return sorted(p for p in Path(root).glob('*/**/provider-calls/*.json') if p.stem.isdigit())


class BudgetedProvider(pilot.AuditedProvider):
    def __init__(self, directory, manifest, budget_root, transport=None):
        super().__init__(directory, manifest, transport)
        self.budget_root = Path(budget_root)
        self.system_prompt = manifest.get('experimental_system_prompt')

    def respond(self, messages, **kwargs):
        if self.system_prompt is not None:
            if not messages or messages[0].get('role')!='system':
                raise ProviderError('experimental prompt needs an explicit system message',kind='pilot_config')
            messages=deepcopy(messages)
            messages[0]['content']=self.system_prompt
        with (self.budget_root/'request-budget.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if len(attempts(self.budget_root)) >= MAXIMUM_ATTEMPTS:
                raise ProviderError('shared 18-request pilot budget exhausted', kind='pilot_budget')
            return super().respond(messages, **kwargs)


def prepare(output, parent, cases, system_prompt=None):
    output, parent = Path(output).absolute(), Path(parent).absolute()
    original = pilot.checked(parent)
    if not cases or len(set(cases))!=len(cases) or any(c not in pilot.CASES for c in cases):
        raise ValueError('select distinct authorized cases')
    if output.parent != parent.parent:
        raise ValueError('diagnostic must share the original pilot budget directory')
    output.mkdir(exist_ok=False)
    m = deepcopy(original)
    m.update(request_timeout_s=90, cases=[c for c in m['cases'] if c['id'] in cases],
        diagnostic='new task with timeout changed from 45 to 90 seconds; other model/agent settings unchanged',
        parent=str(parent), parent_manifest_sha256=pilot.digest(parent/'manifest.json'),
        controller_sha256=pilot.digest(Path(__file__)), attempts_before=len(attempts(output.parent)),
        shared_maximum_attempts=MAXIMUM_ATTEMPTS)
    for c in m['cases']:
        (output/c['source']).write_bytes((parent/c['source']).read_bytes())
    if system_prompt is not None:
        text=Path(system_prompt).read_text(encoding='utf-8')
        if not 1<=len(text)<=60000:
            raise ValueError('experimental system prompt is empty or exceeds context limit')
        (output/'experimental-system.txt').write_text(text,encoding='utf-8')
        m.update(experimental_system_prompt=text,experimental_prompt_sha256=pilot.digest(output/'experimental-system.txt'),
                 diagnostic='experimental compact system prompt plus 90-second timeout; new task, same materials and model')
    pilot.save(output/'manifest.json',m)
    (output/'manifest.sha256').write_text(pilot.digest(output/'manifest.json'))
    return m


def checked(output):
    output=Path(output).absolute()
    if pilot.digest(output/'manifest.json')!=(output/'manifest.sha256').read_text():
        raise ValueError('diagnostic manifest changed')
    m=json.loads((output/'manifest.json').read_text())
    parent=Path(m['parent'])
    original=pilot.checked(parent)
    if (output.parent!=parent.parent or pilot.digest(parent/'manifest.json')!=m['parent_manifest_sha256']
            or pilot.digest(Path(__file__))!=m['controller_sha256'] or m['request_timeout_s']!=90
            or m['shared_maximum_attempts']!=MAXIMUM_ATTEMPTS):
        raise ValueError('diagnostic identity changed')
    for key in original:
        if key not in ('request_timeout_s','cases') and m[key]!=original[key]:
            raise ValueError('diagnostic changed more than timeout/case selection')
    if not m['cases'] or any(c not in original['cases'] for c in m['cases']):
        raise ValueError('unauthorized diagnostic case')
    for c in m['cases']:
        if pilot.digest(output/c['source'])!=c['sha256']:
            raise ValueError('source changed')
    if m.get('experimental_system_prompt') is not None:
        if (m['experimental_system_prompt']!=(output/'experimental-system.txt').read_text(encoding='utf-8')
                or pilot.digest(output/'experimental-system.txt')!=m['experimental_prompt_sha256']):
            raise ValueError('experimental system prompt changed')
    return m


def execute(output):
    output=Path(output).absolute();m=checked(output)
    with (output/'execution-started.json').open('x') as f:
        json.dump({'attempts_before':len(attempts(output.parent))},f)
    if len(attempts(output.parent))>=MAXIMUM_ATTEMPTS:
        raise ValueError('shared request budget already exhausted')
    owned_key=pilot.KEY_ENV not in os.environ
    if owned_key:
        sys.path.append('/usr/lib/python3/dist-packages')
        import yaml
        credentials=yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())
        os.environ[pilot.KEY_ENV]=credentials['refs']['USTC_API_KEY'];del credentials
    receipts,results=[],[]
    try:
        for c in m['cases']:
            available=MAXIMUM_ATTEMPTS-len(attempts(output.parent))
            if available<=0:break
            folder=output/c['id'];folder.mkdir()
            store=ArtifactStore(folder/'store');source=ingest_source(store,output/c['source'])
            provider=BudgetedProvider(folder/'provider-calls',m,output.parent)
            ref=agent.run_agent(store,provider,[source],ExperimentSpec(tuple(m['times']),time_unit=m['time_unit']),
                modeling_decisions=True,max_calls=min(m['max_calls_per_case'],available),
                max_repairs=m['max_repairs_per_case'],max_retries=0,context_max_chars=m['context_max_chars'])
            result=pilot.outcome(store,ref,c['id'],m['analytic_absolute_tolerance'])
            result.update(case=c['id'],usage=pilot.usage_summary(provider.receipts))
            receipts.extend(provider.receipts);results.append(result)
            pilot.save(folder/'result.json',result)
            (folder/'report.md').write_text(reporting.render_report(store,ref))
            (folder/'timeline.md').write_text(timeline.render_markdown(timeline.from_run(store,ref)))
            summary=dict(results=results,request_attempts=len(receipts),shared_attempts=len(attempts(output.parent)),
                shared_maximum=MAXIMUM_ATTEMPTS,usage=pilot.usage_summary(receipts),scientific_support='unknown',
                cases_not_attempted=[x['id'] for x in m['cases'][len(results):]])
            summary['prompt_variant']='experimental_compact' if m.get('experimental_system_prompt') else 'product_default'
            pilot.save(output/'summary.json',summary)
            print(json.dumps({k:result.get(k) for k in ('case','state','control_pass','calls','max_absolute_error')}),flush=True)
            if any(r.get('error_kind') in ('transport','http','response_envelope','pilot_budget') for r in provider.receipts):break
    finally:
        if owned_key:os.environ.pop(pilot.KEY_ENV,None)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','check','execute']);p.add_argument('--output',required=True)
    p.add_argument('--parent');p.add_argument('--cases',nargs='+',choices=pilot.CASES,default=['production-loss'])
    p.add_argument('--system-prompt',help='optional experimental system prompt, saved in the new manifest')
    args=p.parse_args()
    result=prepare(args.output,args.parent,args.cases,args.system_prompt) if args.mode=='prepare' else {'check':checked,'execute':execute}[args.mode](args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
