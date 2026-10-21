"""Frozen real-paper pilot: extraction scoring and exploratory numerical control.

Offline calibration is deliberately labelled, never reported as LLM extraction.
Live runs expose only the selected source, never evaluator gold or references.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import numpy as np
from scipy.integrate import solve_ivp
from scipy.signal import find_peaks

from biomodel_agent import agent, application, evidence, modeling, reporting
from biomodel_agent.domain.contracts import (
    ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State,
)
from biomodel_agent.literature_scoring import score_extraction
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'benchmarks/manifests/literature-pilot.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + '\n', encoding='utf-8')


def verify_inputs(manifest):
    """Reject stale gold, sources, and image snapshots before any paid calls."""
    for entry in manifest['files']:
        path = ROOT / entry['path']
        if sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError(f"Frozen input hash mismatch: {entry['path']}")


def calibration(gold, sources):
    """Known-answer scorer control, explicitly not an extraction baseline."""
    prediction = {'facts': {i['target']: {'value': i['expected'],
                        'evidence': deepcopy(i['support'])} for i in gold['items']},
                  'state': 'calibration_fixture_not_llm'}
    positive = score_extraction(gold, prediction, sources)
    negative = deepcopy(prediction)
    if any(i['expected'] is not None for i in gold['items']):
        negative['facts']['rule:X/expression']['value'] = 'k1 + k2*X'
        negative['facts']['k1/value']['evidence'] = [
            {'source_id': next(iter(sources)), 'quote': 'invented quote'}]
    negative['facts']['X/initial'] = {'value': 0.0, 'evidence': []}
    negative_score = score_extraction(gold, negative, sources)
    expected_present = sum(i['expected'] is not None for i in gold['items'])
    if (positive['counts']['fully_supported'] != expected_present
            or negative_score['counts']['unsupported_completions'] != 1
            or (expected_present and negative_score['counts']['fully_supported']
                != expected_present - 2)):
        raise RuntimeError('Scorer discriminator calibration failed')
    return {'kind': 'known_answer_calibration_not_extraction_result',
            'positive': positive, 'deliberately_corrupted': negative_score}


def numerical_control(manifest, gold, output):
    """Assistant-transcribed model with explicitly analyst-chosen initial values."""
    config = manifest['numeric_control']
    values = {i['target']: i['expected'] for i in gold['items']}
    draft = ModelDraft('gonze2013_exploratory',
        tuple(State(k, config['initials'][k], 'unknown', 'variable')
              for k in ('X', 'Y', 'Z')),
        parameters=tuple(Parameter(k, values[k + '/value'], 'unknown',
                                   'assistant_visual_transcription')
                         for k in ('k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'Ki', 'n')),
        rate_rules=tuple(RateRule(k, values[f'rule:{k}/expression'])
                         for k in ('X', 'Y', 'Z')), time_unit='unknown')
    times = np.linspace(config['time_start'], config['time_end'], config['samples'])

    # Independent hand transcription: no product expression parser or SBML builder.
    def rhs(t, y):
        x, protein, inhibitor = y
        return [1.0 / (1.0 + inhibitor**10) - 0.1*x,
                x - 0.1*protein, protein - 0.1*inhibitor]

    reference = solve_ivp(rhs, (times[0], times[-1]),
        [config['initials'][k] for k in ('X', 'Y', 'Z')],
        method='DOP853', t_eval=times, rtol=1e-11, atol=1e-13)
    if not reference.success or not np.isfinite(reference.y).all():
        raise RuntimeError('Independent reference integration failed')
    columns = {k: reference.y[i].tolist() for i, k in enumerate(('X', 'Y', 'Z'))}
    store = ArtifactStore(output / 'numeric-store')
    ref = application.run_reproduction(store, modeling.build_model(draft),
        ExperimentSpec(tuple(times)),
        reference=SimulationResult('succeeded', tuple(times), columns),
        scales={k: config['scale'] for k in columns},
        threshold=config['threshold'], use_cache=False, strict=True,
        nonnegative=('X', 'Y', 'Z'))
    run = store.get(ref)['payload']
    (output / 'numerical-control.md').write_text(reporting.render_report(store, ref))
    peaks, _ = find_peaks(reference.y[0])
    late_peaks = times[peaks][times[peaks] >= 120.0]
    return {'kind': config['scientific_claim'], 'initial_origin': config['initial_origin'],
            'initials': config['initials'], 'run_ref': ref, 'execution': run['status'],
            'comparison': run['comparison'],
            'scientific_status': run['scientific_status'],
            'observed_late_peak_period': float(np.mean(np.diff(late_peaks)))
                if len(late_peaks) > 1 else None,
            'figure_trajectory_reproduction': 'not_verified',
            'human_semantic_review': 'pending_human_review'}


def normalize_attempt(payload, source_ref, source_id, source_text):
    """Score the last proposal, even if tools rejected it; retain this distinction."""
    proposals = [e for e in payload['events'] if e.get('type') == 'llm_call'
                 and e.get('response', {}).get('action') == 'submit_draft']
    result = {'state': payload['state'], 'facts': {},
              'proposal_status': 'not_submitted', 'duplicate_targets': []}
    if not proposals:
        return result
    args = proposals[-1]['response'].get('args', {})
    draft = args.get('draft', {})
    if not isinstance(draft, dict):
        return result
    result['proposal_status'] = 'last_attempt_not_necessarily_accepted'
    citations = args.get('evidence', [])
    for key, field, suffix in [('parameters', 'value', '/value'),
                               ('states', 'initial', '/initial'),
                               ('rate_rules', 'expression', '/expression')]:
        entries = draft.get(key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get('state' if key == 'rate_rules' else 'id')
            if not isinstance(name, str):
                continue
            target = ('rule:' if key == 'rate_rules' else '') + name + suffix
            if target in result['facts']:
                result['duplicate_targets'].append(target)
            evidence_list = []
            for citation in citations if isinstance(citations, list) else []:
                if (not isinstance(citation, dict) or citation.get('target') != target
                        or citation.get('source_ref') != source_ref
                        or citation.get('page') != 1):
                    continue
                quote = citation.get('quote')
                if quote is None:
                    start, end = citation.get('start'), citation.get('end')
                    if type(start) is int and type(end) is int and 0 <= start < end <= len(source_text):
                        quote = source_text[start:end]
                evidence_list.append({'source_id': source_id, 'quote': quote})
            result['facts'][target] = {'value': entry.get(field), 'evidence': evidence_list}
    return result


def live_track(track, config, output, args):
    source_text = (ROOT / config['source']).read_text(encoding='utf-8')
    store = ArtifactStore(output / f'{track}-agent-store')
    source_ref = evidence.ingest_source(store, ROOT / config['source'])
    provider = ChatProvider(args.base_url, args.live_model, args.api_key_env,
                            timeout_s=90, max_tokens=4000)
    started = time.monotonic()
    # No gold, numeric reference, or exploratory initial values enter this call.
    ref = agent.run_agent(store, provider, [source_ref],
        ExperimentSpec((0., 1., 2.)), max_calls=8, max_repairs=2)
    payload = store.get(ref)['payload']
    prediction = normalize_attempt(payload, source_ref, config['source_id'], source_text)
    write_json(output / f'{track}-prediction.json', prediction)
    score = score_extraction(read_json(ROOT / config['gold']), prediction,
                              {config['source_id']: source_text})
    write_json(output / f'{track}-score.json', score)
    (output / f'{track}-agent.md').write_text(reporting.render_report(store, ref))
    return {'status': payload['state'], 'agent_ref': ref, 'run_ref': payload['run_ref'],
            'calls': payload['call_count'], 'repairs': payload['repair_count'],
            'elapsed_seconds': time.monotonic() - started,
            'usage_total_tokens': sum(e.get('usage', {}).get('total_tokens', 0)
                                      for e in payload['events']),
            'questions': [e['question'] for e in payload['events']
                          if e.get('type') == 'awaiting_input'],
            'proposal_status': prediction['proposal_status'], 'score': score}


def evaluate(output, args):
    manifest = read_json(MANIFEST)
    verify_inputs(manifest)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new or empty output directory to avoid mixing runs')
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': '1', 'case_id': manifest['id'],
              'evaluated_at': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': sha256(MANIFEST.read_bytes()).hexdigest(),
              'environment': application.environment_fingerprint(),
              'scope': 'single_real_paper_development_pilot_not_blind_or_success_rate',
              'semantic_review': manifest['review_status'], 'calibration': {},
              'live_extraction': {}, 'architecture_baselines': 'not_run'}
    for name, config in manifest['tracks'].items():
        sources = {config['source_id']: (ROOT / config['source']).read_text(encoding='utf-8')}
        result['calibration'][name] = calibration(read_json(ROOT / config['gold']), sources)
    result['numerical_control'] = numerical_control(manifest,
        read_json(ROOT / manifest['tracks']['assisted']['gold']), output)
    if result['numerical_control']['comparison']['status'] != 'pass':
        raise RuntimeError('Exploratory cross-implementation numerical control failed')
    write_json(output / 'evaluation.json', result)
    for name, config in manifest['tracks'].items():
        result['live_extraction'][name] = live_track(name, config, output, args) if args.live_model else {'status': 'not_run'}
        write_json(output / 'evaluation.json', result)
    summary = ['# Real-paper development pilot', '',
        'Scope: selected Goodwin section and Fig 2 caption; one development case.',
        'Assistant visual transcription and evaluator gold await human review.',
        'Calibration fixtures are not LLM extraction results.', '',
        f"Numerical control: {result['numerical_control']['comparison']['status']}; "
        f"max scaled error {result['numerical_control']['comparison'].get('max_scaled_error'):.6g}.",
        'Initial values were analyst-chosen. Original figure trajectory: not verified.', '']
    for name, live in result['live_extraction'].items():
        summary.append(f"- {name}: {live['status']}")
        if 'score' in live:
            counts = live['score']['counts']
            summary.append(f"  - Supported extracted facts: {counts['fully_supported']}/{counts['expected_present']}; "
                           f"correct abstentions {counts['correct_abstentions']}/{counts['expected_missing']}; "
                           f"unsupported completions {counts['unsupported_completions']}.")
    summary += ['', 'No claim of advantage over generic assistants or fixed workflows.']
    (output / 'report.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'numeric': result['numerical_control']['comparison'],
                     'live': {k: v['status'] for k, v in result['live_extraction'].items()}}, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/v0.2/literature-pilot'))
    parser.add_argument('--live-model', help='Optional paid live agent integration, no gold exposed')
    parser.add_argument('--base-url')
    parser.add_argument('--api-key-env', default='BIOMODEL_API_KEY')
    options = parser.parse_args()
    if options.live_model and not options.base_url:
        parser.error('--live-model requires --base-url')
    evaluate(options.output, options)
