"""Known planner truncation and explicit, counted higher-cap continuation.

This is an operator SDK, not a model action. Planning never contacts a provider,
refunds a call, changes frozen policy, or recovers an unknown POST.
"""
from copy import deepcopy
import hashlib
import math

from .storage import canonical_bytes

PLAN = 'research_project_output_budget_plan'


def failure_closure(store, project_ref, attempt_args, attempt_ref, failure_ref):
    from . import research_projects as p
    failure = p.own(store, failure_ref, p.PROVIDER_FAILURE, project_ref)
    request_ref = attempt_args.get('request_ref')
    provider = attempt_args.get('provider')
    scope = attempt_args.get('scope_sha256')
    diagnostic = failure.get('response_diagnostic')
    cap = provider.get('max_tokens') if isinstance(provider, dict) else None
    if (failure.get('version') != 1 or failure.get('attempt_ref') != attempt_ref
            or failure.get('request_ref') != request_ref or failure.get('provider') != provider
            or failure.get('error_kind') != 'output_budget'
            or failure.get('output_budget_verified') is not True
            or not isinstance(diagnostic, dict) or diagnostic.get('finish_reason') != 'length'
            or type(cap) is not int or cap <= 0
            or type(diagnostic.get('requested_max_tokens')) is not int
            or diagnostic['requested_max_tokens'] != cap):
        raise ValueError('output budget failure binding mismatch')
    expected = {project_ref, attempt_ref, request_ref}
    blob = failure.get('model_content_json_blob_ref')
    if blob is not None:
        if not isinstance(blob, str):raise ValueError('output budget content binding mismatch')
        store.get_blob(blob)
        expected.add(blob)
    if set(store.get(failure_ref)['refs']) != expected:
        raise ValueError('output budget failure reference closure mismatch')
    prompt = store.get(request_ref)
    payload = prompt['payload']
    if (prompt['kind'] != 'research_project_prompt'
            or payload.get('project_ref') != project_ref or project_ref not in prompt['refs']
            or payload.get('provider') != provider or payload.get('scope_sha256') != scope
            or not isinstance(payload.get('messages'), list)
            or hashlib.sha256(canonical_bytes(payload['messages'])).hexdigest() != scope):
        raise ValueError('output budget prompt binding mismatch')
    return failure


def pending(attempts, returns):
    """Only the latest planner attempt can require a new budget plan."""
    for ref, attempt in reversed(attempts):
        if attempt['operation'] != 'respond':continue
        result = returns.get(ref)
        if (result and result[1]['state'] == 'rejected'
                and (result[1].get('diagnostic') or {}).get('code') == 'output_budget'):
            return ref, attempt, result[1]['provider_failure_ref']
        return None
    return None


def _profile(original, target):
    if not isinstance(original, dict) or not isinstance(target, dict):
        raise ValueError('output budget provider profile required')
    before, after = deepcopy(original), deepcopy(target)
    old_cap, new_cap = before.pop('max_tokens', None), after.pop('max_tokens', None)
    old_time, new_time = before.pop('timeout_s', None), after.pop('timeout_s', None)
    if (before != after or type(old_cap) is not int or type(new_cap) is not int
            or not 0 < old_cap < new_cap <= 10**9
            or type(old_time) not in (int, float) or type(new_time) not in (int, float)
            or not math.isfinite(old_time) or not math.isfinite(new_time)
            or not 0 < old_time <= new_time):
        raise ValueError('new plan requires a higher output cap on the same provider/model/reasoning profile')


def plans(store, project_ref, attempts, returns):
    """Validate every plan on reread, including plans already consumed."""
    from . import research_projects as p
    by_attempt = dict(attempts)
    found = {}
    for ref, payload in p.rows(store, PLAN, project_ref):
        attempt_ref = payload.get('attempt_ref')
        attempt = by_attempt.get(attempt_ref)
        failure_ref = payload.get('failure_ref')
        result = returns.get(attempt_ref)
        if (payload.get('version') != 1 or payload.get('project_ref') != project_ref
                or not attempt or attempt['operation'] != 'respond' or not result
                or result[1]['state'] != 'rejected'
                or result[1].get('provider_failure_ref') != failure_ref
                or attempt_ref in found):
            raise ValueError('invalid or conflicting output budget plan')
        failure = failure_closure(store, project_ref, attempt['args'], attempt_ref, failure_ref)
        if (payload.get('request_ref') != failure['request_ref']
                or payload.get('scope_sha256') != attempt['args']['scope_sha256']
                or payload.get('original_provider') != failure['provider']
                or set(store.get(ref)['refs']) != {project_ref, attempt_ref, failure_ref, failure['request_ref']}):
            raise ValueError('output budget plan reference binding mismatch')
        p.text(payload.get('actor'), 200)
        p.text(payload.get('reason'), 1000)
        _profile(failure['provider'], payload.get('provider'))
        found[attempt_ref] = (ref, payload)
    return found


def validate_provider(store, project_ref, provider_identity):
    """Called before a new prompt/attempt/network request is created."""
    from . import research_projects as p
    attempts, returns, _ = p.history(store, project_ref)
    blocked = pending(attempts, returns)
    if blocked is None:return
    available = plans(store, project_ref, attempts, returns).get(blocked[0])
    if available is None or available[1]['provider'] != provider_identity:
        raise ValueError('planner provider does not match the explicit output budget plan')


def plan(store, project_ref, attempt_ref, provider, *, actor, reason):
    """Authorize exactly the next counted attempt with an explicit higher cap.

    Same identity/attribution is idempotent. Different plans for an already
    planned attempt are rejected. No model action can create this receipt.
    """
    from . import research_projects as p, research_tasks as tasks
    from .provider_identity import identity
    project = p.load(store, project_ref)
    p.text(actor, 200);p.text(reason, 1000)
    if not actor.strip() or not reason.strip():raise ValueError('plan attribution and reason are required')
    with tasks.owner(store, project['id']):
        attempts, returns, unknown = p.history(store, project_ref)
        available = plans(store, project_ref, attempts, returns)
        target = identity(provider)
        previous = available.get(attempt_ref)
        if previous:
            if all(previous[1].get(k) == v for k, v in dict(provider=target, actor=actor, reason=reason).items()):
                return previous[0]
            raise ValueError('conflicting output budget plan')
        blocked = pending(attempts, returns)
        if unknown or blocked is None or blocked[0] != attempt_ref:
            raise ValueError('only a current known output budget failure can receive a new plan')
        view = p.inspect(store, project_ref)
        if (view['state'] != 'output_budget_exhausted'
                or view['budget']['planner_calls'] >= view['budget']['planner_max_calls']
                or len(attempts) >= 200):
            raise ValueError('project cannot continue within its remaining policy')
        _, attempt, failure_ref = blocked
        failure = failure_closure(store, project_ref, attempt['args'], attempt_ref, failure_ref)
        _profile(failure['provider'], target)
        return store.put(PLAN, dict(version=1, project_ref=project_ref, attempt_ref=attempt_ref,
            failure_ref=failure_ref, request_ref=failure['request_ref'],
            scope_sha256=attempt['args']['scope_sha256'], original_provider=failure['provider'],
            provider=target, actor=actor, reason=reason),
            refs=(project_ref, attempt_ref, failure_ref, failure['request_ref']))
