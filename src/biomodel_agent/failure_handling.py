"""Deterministic, opt-in ODE failure routes; no scientific authority or retries."""
from __future__ import annotations

import hashlib
import re
from . import diagnostics
from .storage import canonical_bytes

POLICY = dict(version=1, max_auto_investigations=4, max_target_investigations=1,
              max_stagnant_failures=3)
TARGET = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}/(?:value|initial)')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_policy(policy):
    if canonical_bytes(policy) != canonical_bytes(POLICY):
        raise ValueError('unsupported failure handling policy')
    return dict(policy)


def request_hash(action, args, source_refs):
    return digest(dict(action=action, args=args, source_refs=list(source_refs)))


def descriptor(events, index, source_refs, *, calls, max_calls, repairs, max_repairs,
               lookup_block=None):
    event = events[index]
    request = next((e['response'] for e in reversed(events[:index+1])
                    if e.get('type') == 'llm_call' and e.get('call') == event.get('call')), {})
    items = (event.get('validation_feedback') or {}).get('items')
    if items is None:
        classified = diagnostics.event_failure(event)
        if event.get('tool') == 'inspect_source' and event.get('error') == 'invalid source span':
            classified = diagnostics.issue('citation', 'invalid_source_span', event['error'])
        if event.get('type') == 'plan_blocked':
            classified = diagnostics.issue('model', 'plan_dependency', event['reason'])
        if event.get('failure_code') == 'duplicate_failed_request':
            classified = diagnostics.issue('unknown', 'duplicate_failed_request', event['error'])
        items = [classified or diagnostics.issue('unknown', 'unclassified_failure', event.get('error', 'Unknown failure'))]
    items = [i for i in items if i.get('blocking')]
    if not items:
        items = [diagnostics.issue('unknown', 'empty_failure', 'Failure had no blocking diagnostic.')]
    keys = sorted({digest([i['category'], i['code'], i.get('target')]) for i in items})
    scope = digest(list(source_refs))
    # A ready draft starts a new progress epoch. Clarification changes the scope,
    # but neither clarification nor readiness refunds calls or lookup attempts.
    after = max((j for j, e in enumerate(events[:index]) if e.get('tool') in ('submit_draft', 'submit_plan')
                 and e.get('state') == 'ready'), default=-1)
    prior = [e for e in events[after+1:index] if e.get('tool') == 'route_failure'
             and e.get('scope') == scope]
    stagnant = 1
    for old in reversed(prior):
        previous = set(old['issue_keys'])
        if previous - set(keys):
            break
        stagnant += 1
    lookups = [e for e in events if e.get('policy_args') and e.get('tool') == 'investigate_parameter']
    return dict(version=1, event_index=index, event=event, source_refs=list(source_refs), scope=scope,
                request_hash=request_hash(request.get('action'), request.get('args'), source_refs),
                issue_keys=keys, issues=items, stagnant_failures=stagnant,
                calls=calls, remaining_calls=max(0, max_calls-calls),
                repairs=repairs, max_repairs=max_repairs, lookup_block=lookup_block,
                auto_investigations=len(lookups),
                investigated_targets=[e['policy_args']['target'] for e in lookups])


def decide(policy, d):
    validate_policy(policy)
    categories = sorted({i['category'] for i in d['issues']})
    targets = sorted({i['target'] for i in d['issues'] if i.get('target')})
    result = dict(categories=categories, targets=targets, scientific_support='unknown')
    def route(name, reason):
        return dict(result, route=name, reason=reason)
    if 'numerical' in categories and d['event'].get('type') == 'tool_error':
        return route('diagnose_stop', 'Scientific execution raised an error without a confirmed returned result. Preserve the diagnostic; no retry or result assumption.')
    if 'numerical' in categories:
        return route('diagnose_stop', 'Inspect the saved run checks and comparison. The scientific execution budget is consumed; no rerun or threshold change.')
    if 'unknown' in categories:
        return route('stop', 'Unknown or repeated failed operation: preserve its diagnostic; no automatic tool retry.')
    if set(categories) & {'source_conflict', 'units'}:
        return route('ask', 'Clarify the conflicting declarations, units or conditions for '+(', '.join(targets) or 'the model')+'. No value is selected.')
    if d['repairs'] > d['max_repairs']:
        return route('stop', 'The inherited draft repair budget is exhausted.')
    if d['stagnant_failures'] >= policy['max_stagnant_failures']:
        return route('stop', 'Blocking issue keys have not decreased within the bounded failure window; further automatic repair is stopped.')
    if 'missing_information' in categories:
        target = next((t for t in targets if TARGET.fullmatch(t)), None)
        if (target and d['remaining_calls'] > 0 and not d['lookup_block']
                and d['auto_investigations'] < policy['max_auto_investigations']
                and d['investigated_targets'].count(target) < policy['max_target_investigations']):
            return dict(route('retrieve', 'Search existing authorized materials once for this target; retrieved matches remain untrusted leads.'),
                        target=target, query=target.split('/')[0])
        return route('ask', 'Supply the missing declaration, units and conditions for '+(', '.join(targets) or 'the unresolved model input')+
                     ('. Local lookup is blocked: '+d['lookup_block'] if d['lookup_block'] else '. Local lookup is unavailable or exhausted.'))
    if 'service' in categories:
        return route('provider_policy', 'Only the existing provider retry policy may retry; its original counters and limits remain in force.')
    if not d['remaining_calls']:
        return route('stop', 'The inherited provider call budget is exhausted; preserve the unresolved failure.')
    return route('repair', 'Correct the cited or structural issue using authorized evidence within the remaining calls and repairs.')


def record(store, policy, d):
    decision = decide(policy, d)
    payload = dict(version=1, policy=policy, descriptor=d, decision=decision)
    refs = [*d['source_refs'], *[r for r in d['event'].get('artifact_refs', []) if r]]
    if d['event'].get('run_ref'):
        refs.append(d['event']['run_ref'])
    ref = store.put('failure_decision', payload, refs=tuple(dict.fromkeys(refs)))
    return dict(decision_ref=ref, **decision)


def load(store, ref, policy):
    obj = store.get(ref)
    p = obj['payload']
    validate_policy(policy)
    if (obj['kind'] != 'failure_decision' or p['version'] != 1 or p['policy'] != policy
            or p['decision'] != decide(policy, p['descriptor'])):
        raise ValueError('invalid failure decision')
    required = [*p['descriptor']['source_refs'],
                *[r for r in p['descriptor']['event'].get('artifact_refs', []) if r]]
    if p['descriptor']['event'].get('run_ref'):
        required.append(p['descriptor']['event']['run_ref'])
    if not set(required) <= set(obj['refs']):
        raise ValueError('failure decision lost its evidence refs')
    return p


def progress(store, policy, events):
    validate_policy(policy)
    decisions = []
    for e in events:
        if e.get('tool') == 'route_failure' and e.get('decision_ref'):
            p = load(store, e['decision_ref'], policy)
            decisions.append(dict(decision_ref=e['decision_ref'], event_index=p['descriptor']['event_index'],
                                  **p['decision']))
    attempts = [e for e in events if e.get('policy_args') and e.get('tool') == 'investigate_parameter']
    return dict(version=1, decisions=decisions, auto_investigations=len(attempts),
                remaining_auto_investigations=max(0, policy['max_auto_investigations']-len(attempts)),
                scientific_support='unknown')
