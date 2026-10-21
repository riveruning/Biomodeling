"""Offline scripted repair of already exposed evaluation traces.

The initial responses and old pause points are unchanged. A deterministic test
actor then chooses offsets from tool diagnostics, rebinds requested clarification
citations, fixes a compartment target spelling, or applies the rate-rule state
representation explicitly requested by the validator. This measures workflow and
solver compatibility, not new model reasoning, inference performance or costs.
No credentials, HTTP requests or gold answers enter the actor.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import argparse

import numpy as np

from biomodel_agent import agent, application, bundles, evidence
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.storage import ArtifactStore


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ObservedPacedProvider:
    # Preserve the original checkpoint's adapter identity and settings while
    # replacing its implementation with an explicitly labelled offline actor.
    base_url = 'https://api.llm.ustc.edu.cn/v1'
    model = 'qwen3.8-chat'
    json_mode = True
    timeout_s = 90.
    max_tokens = 4000
    last_usage = {}

    def __init__(self, prefix):
        self.prefix = iter(prefix)
        self.latest = None
        self.reply_ref = None
        self.reply_text = None
        self.scripted_actions = []

    def respond(self, messages):
        last = json.loads(messages[-1]['content'])
        if last.get('tool') == 'researcher_reply':
            self.reply_ref, self.reply_text = last['source_ref'], last['text']
        try:
            response = deepcopy(next(self.prefix))
            if response['action'] == 'submit_draft':
                self.latest = deepcopy(response['args'])
            return response
        except StopIteration:
            pass
        if last.get('tool') == 'submit_draft' and last.get('state') == 'invalid':
            proposal = deepcopy(self.latest)
            for diagnostic in last.get('citation_diagnostics', []):
                if not diagnostic['candidates']:
                    raise RuntimeError('script has no candidate; do not invent a quote')
                citation = next(c for c in proposal['evidence'] if c['target'] == diagnostic['target'])
                chosen = diagnostic['candidates'][-1]
                citation.pop('quote', None)
                citation.update(start=chosen['start'], end=chosen['end'])
            for problem in last.get('problems', []):
                marker = ': cite the researcher clarification'
                if marker in problem:
                    target = problem.split(marker, 1)[0]
                    if not self.reply_ref:
                        raise RuntimeError('no supplied clarification available')
                    for citation in proposal['evidence']:
                        if citation['target'] == target:
                            citation.pop('start', None)
                            citation.pop('end', None)
                            citation.update(source_ref=self.reply_ref, page=1, quote=self.reply_text)
                elif 'Unknown citation target' in problem:
                    for citation in proposal['evidence']:
                        if citation['target'].startswith('compartment/'):
                            field = citation['target'].split('/', 1)[1]
                            citation['target'] = proposal['draft']['compartment']['id'] + '/' + field
                elif "rate rules require quantity_kind 'variable'" in problem:
                    governed = {r['state'] for r in proposal['draft']['rate_rules']}
                    for state in proposal['draft']['states']:
                        if state['id'] in governed:
                            state['quantity_kind'] = 'variable'
            if proposal == self.latest:
                # A harness limitation must not prevent recording the remaining
                # independent cases, or turn into a fictional model success.
                response = {'action': 'finish', 'args': {}}
                self.scripted_actions.append(deepcopy(response))
                return response
            self.latest = deepcopy(proposal)
            response = {'action': 'submit_draft', 'args': proposal}
        elif last.get('tool') == 'submit_draft' and last.get('ok'):
            response = {'action': 'run', 'args': {}}
        elif last.get('tool') == 'run' and last.get('ok'):
            response = {'action': 'finish', 'args': {}}
        else:
            raise RuntimeError('unsupported actor state; no inference fallback')
        self.scripted_actions.append(deepcopy(response))
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('/home/riveruning/biologymodel_agent'))
    parser.add_argument('--dataset', type=Path, default=Path('/home/riveruning/biomodel_agent_evalset'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    original_root = args.project / 'artifacts/holdout-evaluation-20261017/runs/biomodel'
    rows = []
    jobs = [('c13_mm_with_inhibitor_ambiguous', 'resume'),
            ('c18_two_pathway_unit_conflict', 'clarified'),
            ('c18_two_pathway_unit_conflict', 'resume')]
    dump(args.output / 'protocol.json', {
        'kind': 'offline_scripted_workflow_exercise', 'jobs': jobs,
        'new_model_requests': 0, 'independent_holdout': False,
        'actor': 'unchanged original response prefix followed by explicit scripted repairs',
        'candidate': application.environment_fingerprint(),
        'budget': 'same original total calls 8, repairs 2, retries 2; resume counts cumulative',
        'model_capability_measured': False})
    for case, phase in jobs:
        original = original_root / case / ('incomplete' if phase == 'resume' else phase)
        work = args.output / case / phase
        work.mkdir(parents=True)
        prefix, hashes = [], {}
        for path in sorted((original / phase).glob('response-*.json')):
            response = json.loads(path.read_text())
            action = json.loads(response['choices'][0]['message']['content'])
            prefix.append(action)
            hashes[str(path)] = sha(path)
            if action['action'] == 'submit_draft':
                break
        provider = ObservedPacedProvider(prefix)
        parent_calls = 0
        if phase == 'resume':
            # Do not open the original index: it is immutable evaluation data.
            scratch = work / 'copy-of-original-store'
            shutil.copytree(original / 'store', scratch)
            parent_result = json.loads((original / 'incomplete/result.json').read_text())
            bundles.export_bundle(ArtifactStore(scratch), parent_result['agent_ref'], work / 'store')
            store = ArtifactStore(work / 'store')
            parent_before = store.get(parent_result['agent_ref'])
            parent_calls = parent_result['calls']
            reply = (args.dataset / 'clarifications' / (case + '.clarification.txt')).read_text()
            ref = agent.resume_agent(store, provider, parent_result['agent_ref'], reply,
                                     respondent='Pre-written held-out researcher reply')
            assert store.get(parent_result['agent_ref']) == parent_before
        else:
            store = ArtifactStore(work / 'store')
            source = evidence.ingest_source(store, original / 'source.txt')
            experiment = json.loads((original / 'experiment.json').read_text())
            ref = agent.run_agent(store, provider, [source], ExperimentSpec(tuple(experiment['times'])))
        result = store.get(ref)['payload']
        dump(work / 'agent.json', result)
        dump(work / 'scripted-actions.json', provider.scripted_actions)
        row = {'case': case, 'phase': phase, 'status': result['state'],
               'calls_cumulative': result['call_count'], 'calls_incremental': result['call_count'] - parent_calls,
               'repair_count': result['repair_count'], 'original_response_sha256': hashes,
               'scripted_actions': len(provider.scripted_actions), 'new_model_requests': 0,
               'numeric': {'status': 'not_run'}, 'parent_unchanged': phase == 'resume'}
        if result['run_ref']:
            run = store.get(result['run_ref'])['payload']
            dump(work / 'run.json', run)
            # References are read here, after the actor has finished. They are
            # never passed to the actor, checkpoint or model message history.
            reference_path = args.dataset / 'references' / (case + '.reference.json')
            reference = json.loads(reference_path.read_text())
            actual = run['simulation']
            assert actual['status'] == 'succeeded'
            required = set(reference['values'])
            assert required <= set(actual['values'])
            grid, expected_grid = np.asarray(actual['times']), np.asarray(reference['times'])
            assert grid.shape == expected_grid.shape and np.isfinite(grid).all()
            assert np.allclose(grid, expected_grid, rtol=0, atol=1e-12)
            row['additional_outputs'] = sorted(set(actual['values']) - required)
            row['output_contract_exact'] = not row['additional_outputs']
            errors = {}
            for name, values in reference['values'].items():
                got, expected = np.asarray(actual['values'][name]), np.asarray(values)
                assert got.shape == expected.shape and np.isfinite(got).all()
                errors[name] = float(np.max(np.abs(got-expected))/max(1., float(np.max(np.abs(expected)))))
            row['numeric'] = {'status': 'pass' if max(errors.values()) <= .001 else 'fail',
                              'max_scaled_error': max(errors.values()), 'per_species': errors,
                              'reference_sha256': sha(reference_path)}
            row['scientific_status'] = run['scientific_status']
        dump(work / 'result.json', row)
        rows.append(row)
        print(json.dumps(row), flush=True)
    dump(args.output / 'results.json', {'kind': 'offline_scripted_workflow_exercise', 'rows': rows,
                                      'model_capability_measured': False, 'new_model_requests': 0})


if __name__ == '__main__':
    main()
