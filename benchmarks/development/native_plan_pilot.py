"""Bounded live native-plan development pilot; prepare before explicit execution.

New synthetic materials only. No paper/holdout scores or cost-saving claims.
Credentials are loaded only at execution, for the existing USTC destination.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

from biomodel_agent import agent, reporting, recovery, timeline
from biomodel_agent.evidence import ingest_source
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.llm import ChatProvider, ProviderError
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
CASES = ('production-loss', 'transfer', 'missing-rate')
BASE = 'https://api.llm.ustc.edu.cn/v1'
KEY_ENV = 'BIOMODEL_NATIVE_PILOT_KEY'


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output):
    output = Path(output).absolute()
    output.mkdir(exist_ok=False)
    cases = []
    for name in CASES:
        source = ROOT / 'benchmarks/inputs/native-plan-pilot-20261029' / (name + '.txt')
        target = output / (name + '.txt')
        target.write_bytes(source.read_bytes())
        cases.append(dict(id=name, source=target.name, sha256=digest(target),
            expected='clarify_without_execution' if name == 'missing-rate' else 'complete_and_match_analytic'))
    manifest = dict(version=1, scope='seen_synthetic_development_live_model_pilot', cases=cases,
        base_url=BASE, model='glm-5.3-flash', model_revision='provider_not_pinned',
        api='chat_completions', temperature=0, max_tokens=5000, request_timeout_s=45,
        max_calls_per_case=6, max_repairs_per_case=2, max_retries=0, context_max_chars=60000,
        times=[0., .5, 1., 2., 3.], time_unit='second', analytic_absolute_tolerance=1e-5,
        scientific_support='unknown', independent_domain_reviews=0,
        implementation_sha256=recovery.implementation_fingerprint(), runner_sha256=digest(Path(__file__)),
        prepared_at=datetime.now(timezone.utc).isoformat())
    save(output / 'manifest.json', manifest)
    (output / 'manifest.sha256').write_text(digest(output / 'manifest.json'))
    return manifest


def checked(output):
    output = Path(output).absolute()
    if digest(output / 'manifest.json') != (output / 'manifest.sha256').read_text():
        raise ValueError('manifest changed after preparation')
    m = json.loads((output / 'manifest.json').read_text())
    if m['base_url'] != BASE or m['implementation_sha256'] != recovery.implementation_fingerprint() or m['runner_sha256'] != digest(Path(__file__)):
        raise ValueError('destination or implementation changed')
    if tuple(c['id'] for c in m['cases']) != CASES:
        raise ValueError('case scope changed')
    for c in m['cases']:
        if c['source'] != c['id']+'.txt' or digest(output/c['source']) != c['sha256']:
            raise ValueError('source snapshot changed')
    return m


class AuditedProvider(ChatProvider):
    def __init__(self, directory, manifest, transport=None):
        super().__init__(BASE, manifest['model'], api_key_env=KEY_ENV,
            timeout_s=manifest['request_timeout_s'], max_tokens=manifest['max_tokens'], transport=transport)
        self.directory = Path(directory)
        self.directory.mkdir()
        self.receipts = []

    def _extract_content(self, body):
        choices = body.get('choices')
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError('pilot requires one completed response', kind='response_envelope')
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError('pilot requires an object response choice', kind='response_envelope')
        message = choice.get('message') or {}
        if not isinstance(message, dict):
            raise ProviderError('pilot requires an object message', kind='response_envelope')
        if (choice.get('finish_reason') != 'stop' or message.get('refusal')
                or message.get('tool_calls') or message.get('function_call')):
            raise ProviderError('pilot rejects truncation, refusal or native tool response', kind='response_envelope')
        return super()._extract_content(body)

    def respond(self, messages, **kwargs):
        number = len(self.receipts)+1
        path = self.directory / f'{number:02d}.json'
        record = dict(number=number, state='reserved', messages=deepcopy(messages),
                      started_at=datetime.now(timezone.utc).isoformat(), usage=None)
        save(path, record)
        started = time.monotonic()
        try:
            result = super().respond(messages, **kwargs)
            record.update(state='returned', response=result)
            return result
        except ProviderError as exc:
            record.update(state='failed', error_kind=exc.kind, error=str(exc))
            raise
        finally:
            record.update(elapsed_s=time.monotonic()-started, usage=self.last_usage or None)
            save(path, record)
            self.receipts.append(record)


def usage_summary(receipts):
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    return {k: dict(known_subtotal=sum((r.get('usage') or {}).get(k, 0) for r in receipts),
                    missing_calls=sum(k not in (r.get('usage') or {}) for r in receipts),
                    total=sum(r['usage'][k] for r in receipts) if receipts and all(k in (r.get('usage') or {}) for r in receipts) else None)
            for k in keys}


def outcome(store, ref, case, tolerance):
    p = store.get(ref)['payload']
    result = dict(agent_ref=ref, state=p['state'], calls=p['call_count'], repairs=p['repair_count'],
                  run_count=p.get('run_count', 0), run_ref=p.get('run_ref'),
                  action_sequence=[e['action'] for e in p['events'] if e.get('type') == 'llm_call'],
                  blockers=[e for e in p['events'] if e.get('type') in ('tool_error','provider_error','awaiting_input')])
    if case == 'missing-rate':
        result['control_pass'] = p['state']=='awaiting_input' and not p.get('run_ref') and p.get('run_count',0)==0
    else:
        max_error = None
        if p.get('run_ref'):
            sim = store.get(p['run_ref'])['payload']['simulation']
            expected = ({'p':[2-math.exp(-.3*t) for t in sim['times']]} if case=='production-loss' else
                        {'a':[2*math.exp(-.2*t) for t in sim['times']], 'b':[3-2*math.exp(-.2*t) for t in sim['times']]})
            if sim['status']=='succeeded' and set(sim['values'])==set(expected):
                max_error=max(abs(x-y) for key in expected for x,y in zip(sim['values'][key],expected[key]))
        result.update(max_absolute_error=max_error,
            control_pass=p['state']=='completed' and max_error is not None and max_error<=tolerance)
    return result


def execute(output):
    output = Path(output).absolute()
    m = checked(output)
    # A durable marker blocks rerunning this pilot, including after unknown effects.
    with (output/'execution-started.json').open('x') as f:
        json.dump(dict(started_at=datetime.now(timezone.utc).isoformat()), f)
    if KEY_ENV not in os.environ:
        sys.path.append('/usr/lib/python3/dist-packages')
        import yaml
        credentials = yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())
        os.environ[KEY_ENV] = credentials['refs']['USTC_API_KEY']
        del credentials
        owned_key = True
    else:
        owned_key = False
    all_receipts, results = [], []
    try:
        for c in m['cases']:
            folder = output / c['id']; folder.mkdir()
            store = ArtifactStore(folder/'store')
            source = ingest_source(store, output/c['source'])
            provider = AuditedProvider(folder/'provider-calls', m)
            ref = agent.run_agent(store, provider, [source], ExperimentSpec(tuple(m['times']),time_unit=m['time_unit']),
                modeling_decisions=True, max_calls=m['max_calls_per_case'],max_repairs=m['max_repairs_per_case'],
                max_retries=0,context_max_chars=m['context_max_chars'])
            result = outcome(store,ref,c['id'],m['analytic_absolute_tolerance'])
            result.update(case=c['id'], usage=usage_summary(provider.receipts))
            all_receipts.extend(provider.receipts); results.append(result)
            save(folder/'result.json',result)
            (folder/'report.md').write_text(reporting.render_report(store,ref))
            (folder/'timeline.md').write_text(timeline.render_markdown(timeline.from_run(store,ref)))
            summary = dict(scope=m['scope'],results=results,live_request_attempts=len(all_receipts),
                usage=usage_summary(all_receipts),scientific_support='unknown',domain_reviews=0,
                cases_not_attempted=[x['id'] for x in m['cases'][len(results):]])
            save(output/'summary.json',summary)
            print(json.dumps(dict(case=c['id'],state=result['state'],control_pass=result['control_pass'],calls=result['calls'])),flush=True)
            if any(r.get('error_kind') in ('transport','http','response_envelope') for r in provider.receipts):
                break
    finally:
        if owned_key:
            os.environ.pop(KEY_ENV,None)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare','check','execute'])
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result={'prepare':prepare,'check':checked,'execute':execute}[args.mode](args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
