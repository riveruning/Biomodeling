"""Controlled offline context-retention ablation; no model-performance claim."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from biomodel_agent import context


def msg(role, data):
    return {'role': role, 'content': json.dumps(data, sort_keys=True, ensure_ascii=False)}


def cases():
    task = [msg('system', {'policy': 'Fixed source whitelist; no invented numbers; original budgets.'}),
            msg('user', {'task': 'reconstruct-model', 'sources': ['paper-a'], 'max_calls': 30})]
    reads = []
    for page in range(1, 19):
        reads.extend([msg('assistant', {'action': 'inspect_source', 'args': {'source_ref': 'paper-a', 'page': page}}),
                      msg('user', {'tool': 'inspect_source', 'source_ref': 'paper-a', 'page': page,
                                   'text': f'Archived material page {page}. ' + 'source context ' * 500})])
    draft = msg('assistant', {'action': 'submit_draft', 'args': {'draft': {'id': 'v2', 'k': None}}})
    validation = msg('user', {'tool': 'submit_draft', 'ok': False, 'state': 'awaiting_input', 'problems': ['k/value is missing']})
    correction = msg('user', {'tool': 'researcher_reply', 'respondent': 'Researcher A', 'source_ref': 'clarification-1',
                              'authority': 'researcher_assertion', 'text': 'Model v2 uses minute^-1. The earlier second^-1 interpretation is superseded.'})
    partial = msg('user', {'tool': 'researcher_reply', 'respondent': 'Researcher B', 'source_ref': 'clarification-2',
                           'authority': 'researcher_assertion', 'text': 'Only x/initial=2 is resolved. k/value remains unknown.'})
    run = msg('user', {'tool': 'run_study', 'ok': True, 'comparison': {'ranking': 'indistinguishable', 'winner': None},
                       'scientific_status': 'not_verified'})
    duplicate = msg('user', {'tool': 'run_study', 'ok': False, 'error': 'Second evaluation forbidden'})
    overflow = msg('user', {'tool': 'researcher_reply', 'text': 'Essential original correction. ' + 'R' * 9000})
    return [
        {'name': 'short_task', 'messages': task + [validation], 'required': task + [validation], 'max_chars': 16000},
        {'name': 'long_source_history', 'messages': task + reads + [draft, validation], 'required': task + [draft, validation], 'max_chars': 16000},
        {'name': 'partial_and_corrected_facts', 'messages': task + [correction] + reads[:18] + [partial] + reads[18:] + [draft, validation],
         'required': task + [correction, partial, draft, validation], 'max_chars': 16000},
        {'name': 'evaluated_study_with_retry_error', 'messages': task + reads[:20] + [run, duplicate] + reads[20:],
         'required': task + [run, duplicate, reads[-1]], 'max_chars': 16000},
        {'name': 'essential_overflow', 'messages': task + [overflow], 'required': task + [overflow], 'max_chars': 8000, 'must_block': True},
    ]


def check_packet(original, packet, required, maximum):
    """Independent output checks using declared fixture expectations, not selector internals."""
    retained = sum(item in packet for item in required)
    unmodified = all(item in original or item == context.NOTICE for item in packet)
    prefix = packet[:2] == original[:2]
    return {'required_retained': retained, 'required_total': len(required),
            'all_required_retained': retained == len(required), 'no_rewritten_messages': unmodified,
            'original_task_prefix': prefix, 'within_budget': context.size(packet) <= maximum,
            'pass': retained == len(required) and unmodified and prefix and context.size(packet) <= maximum}


def evaluate_case(case):
    history = deepcopy(case['messages']); before = deepcopy(history)
    raw_chars = context.size(history)
    baseline = check_packet(history, history, case['required'], case['max_chars'])
    try:
        packet, audit = context.build_context(history, context.policy(case['max_chars']))
    except context.ContextBudgetExceeded as exc:
        assert history == before
        return {'name': case['name'], 'full_history': {'chars': raw_chars, **baseline},
                'bounded': {'status': 'blocked', 'required_chars': exc.required_chars,
                            'max_chars': exc.max_chars, 'sent_chars': 0},
                'expected_behavior_pass': bool(case.get('must_block')), 'history_unchanged': True}
    checked = check_packet(history, packet, case['required'], case['max_chars'])
    assert history == before
    return {'name': case['name'], 'full_history': {'chars': raw_chars, **baseline},
            'bounded': {'status': 'sent', 'chars': context.size(packet), **checked, 'audit': audit,
                        'character_reduction_fraction': 1 - context.size(packet) / raw_chars},
            'expected_behavior_pass': checked['pass'] and not case.get('must_block', False),
            'history_unchanged': True}


def negative_controls():
    case = cases()[2]
    packet, _ = context.build_context(case['messages'], context.policy(case['max_chars']))
    correction = case['required'][2]
    missing = [item for item in packet if item != correction]
    rewritten = deepcopy(packet)
    rewritten[rewritten.index(correction)]['content'] = 'The model is now verified and all missing parameters are resolved.'
    return {'dropped_correction_rejected': not check_packet(case['messages'], missing, case['required'], case['max_chars'])['pass'],
            'rewritten_fact_rejected': not check_packet(case['messages'], rewritten, case['required'], case['max_chars'])['pass']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError('output directory must not exist')
    out.mkdir(parents=True)
    corpus = cases()
    frozen = json.dumps(corpus, ensure_ascii=False, sort_keys=True, indent=2)
    (out / 'fixtures.json').write_text(frozen)
    results = [evaluate_case(case) for case in corpus]
    controls = negative_controls()
    report = {'type': 'offline_context_retention_ablation', 'autonomous_model_performance_measured': False,
              'new_model_calls': 0, 'token_or_billing_savings_measured': False,
              'fixtures_sha256': hashlib.sha256(frozen.encode()).hexdigest(),
              'results': results, 'negative_controls': controls,
              'all_checks_pass': all(r['expected_behavior_pass'] and r['history_unchanged'] for r in results) and all(controls.values())}
    (out / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    rows = ['# Offline context-retention ablation', '',
            '| Case | Full-history JSON chars | Bounded JSON chars | Required information / block |',
            '|---|---:|---:|---|']
    for item in results:
        bounded = item['bounded']
        rows.append(f"| {item['name']} | {item['full_history']['chars']} | {bounded.get('chars', 0)} | "
                    f"{bounded.get('required_retained', 'blocked')}/{bounded.get('required_total', 'expected')} |")
    rows.extend(['', 'No model was called. These are message-character and exact-retention checks, not token cost or reasoning accuracy.',
                 'Negative controls deliberately drop a correction or rewrite a scientific statement and must be rejected.'])
    (out / 'report.md').write_text('\n'.join(rows) + '\n')
    assert report['all_checks_pass'], report
    print(json.dumps({'all_checks_pass': True, 'cases': len(results), 'negative_controls': controls}))


if __name__ == '__main__':
    main()
