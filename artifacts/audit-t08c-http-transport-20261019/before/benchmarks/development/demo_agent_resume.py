"""Two-process synthetic recovery demonstration; scripted actions, no model/API call."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from biomodel_agent import agent, evidence, reporting
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


class ScriptedProvider:
    last_usage = {}

    def __init__(self, actions):
        self.actions = iter(actions)

    def respond(self, messages):
        item = next(self.actions)
        return item(messages) if callable(item) else item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('start', 'resume'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    if args.phase == 'start':
        root.mkdir(parents=True, exist_ok=False)
        path = root / 'source.txt'
        path.write_text('Initial x is not reported.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n')
        store = ArtifactStore(root / 'store')
        source_ref = evidence.ingest_source(store, path)
        provider = ScriptedProvider([{'action': 'ask_researcher', 'args': {'question': 'What is initial x?'}}])
        parent = agent.run_agent(store, provider, [source_ref], ExperimentSpec((0., 1., 2.)))
        manifest = {'parent_run_ref': parent, 'source_ref': source_ref,
                    'evidence_type': 'synthetic scripted workflow; not an LLM performance evaluation'}
        (root / 'paused.json').write_text(json.dumps(manifest, indent=2))
        (root / 'paused-report.md').write_text(reporting.render_report(store, parent))
        print(json.dumps({'state': store.get(parent)['payload']['state'], 'parent_run_ref': parent}))
        return
    if (root / 'result.json').exists():
        raise ValueError('result already exists; use a fresh output directory')
    manifest = json.loads((root / 'paused.json').read_text())
    store = ArtifactStore(root / 'store')
    original = store.get(manifest['parent_run_ref'])
    reply = 'Initial x = 2.'
    (root / 'researcher-reply.txt').write_text(reply)

    def submit(messages):
        source = json.loads(messages[-1]['content'])['source_ref']
        draft = ModelDraft('decay', (State('x', 2.),), (Parameter('k', .4),),
                           rate_rules=(RateRule('x', '-k*x'),))
        return {'action': 'submit_draft', 'args': {'draft': to_dict(draft), 'evidence': [
            {'target': 'x/initial', 'source_ref': source, 'page': 1, 'quote': reply},
            {'target': 'k/value', 'source_ref': manifest['source_ref'], 'page': 1, 'quote': 'Parameter k = 0.4.'},
            {'target': 'rule:x/expression', 'source_ref': manifest['source_ref'], 'page': 1, 'quote': 'Equation dx/dt = -k*x.'},
        ]}}
    provider = ScriptedProvider([submit, {'action': 'run', 'args': {}}, {'action': 'finish', 'args': {}}])
    resumed = agent.resume_agent(store, provider, manifest['parent_run_ref'], reply,
                                 respondent='Synthetic fixture operator')
    p = store.get(resumed)['payload']
    run = store.get(p['run_ref'])['payload']
    values = run['simulation']['values']['x']
    reference = [2. * math.exp(-.4*t) for t in (0., 1., 2.)]
    error = max(abs(a-b) for a, b in zip(values, reference))
    result = {**manifest, 'resumed_run_ref': resumed, 'state': p['state'],
              'call_count': p['call_count'], 'retry_count': p['retry_count'],
              'parent_unchanged': store.get(manifest['parent_run_ref']) == original,
              'scientific_status': run['scientific_status'], 'semantic_support': run['source_validation']['semantic_support'],
              'values': values, 'analytic_reference': reference, 'max_absolute_error': error,
              'separate_process_phase': True, 'holdout_accessed': False}
    if p['state'] != 'completed' or p['call_count'] != 4 or not result['parent_unchanged'] or error > 1e-6:
        raise AssertionError(result)
    (root / 'result.json').write_text(json.dumps(result, indent=2))
    (root / 'resumed-report.md').write_text(reporting.render_report(store, resumed))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
