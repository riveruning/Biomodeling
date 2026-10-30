"""Bounded ODE task plans: immutable revisions and tool-derived completion.

Plans organize existing tools; they cannot change scientific inputs, execution
limits or authority. Descriptive goals never serve as completion assertions.
"""
from copy import deepcopy
import re

from .storage import canonical_bytes

VERSION = 1
MAX_EDITS = 4
CORE = ('sources', 'draft', 'validate', 'execute', 'compare', 'report')
KINDS = set(CORE) | {'inspect', 'investigate', 'assess'}
TOOLS = {'draft': 'submit_draft', 'validate': 'submit_draft', 'execute': 'run',
         'compare': 'run', 'report': 'finish', 'inspect': 'inspect_source',
         'investigate': 'investigate_parameter', 'assess': 'assess_parameter'}
GOALS = ('确认任务资料范围', '提交有引用的模型草稿', '核验当前草稿与执行时钟',
         '执行当前核验模型', '检查冻结参考比较或明确未请求比较', '完成交付检查并保存可渲染报告')


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError('plan reason must contain 1..400 characters')
    return value


def _steps(value, max_calls, source_refs, parameter_targets):
    from .parameter_applicability import TARGET
    if not isinstance(value, list) or not 6 <= len(value) <= 12:
        raise ValueError('plan must have 6..12 typed steps')
    result, ids = [], set()
    for step in value:
        if not isinstance(step, dict) or set(step) != {'id', 'kind', 'goal', 'depends_on', 'target', 'attempt_limit'}:
            raise ValueError('step fields must be id/kind/goal/depends_on/target/attempt_limit; no claimed status or evidence')
        sid, kind = step['id'], step['kind']
        if not isinstance(sid, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', sid) or sid in ids:
            raise ValueError('step ids must be unique bounded identifiers')
        if not isinstance(kind, str) or kind not in KINDS or (sid in CORE) != (kind in CORE) or (sid in CORE and sid != kind):
            raise ValueError('core ids/kinds are fixed; extra steps must inspect, investigate or assess')
        if not isinstance(step['goal'], str) or not step['goal'].strip() or len(step['goal']) > 160:
            raise ValueError('step goal must contain 1..160 characters')
        deps = step['depends_on']
        if not isinstance(deps, list) or len(deps) > 12 or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps):
            raise ValueError('invalid step dependencies')
        limit = step['attempt_limit']
        if type(limit) is not int or not 1 <= limit <= max_calls:
            raise ValueError('step attempt limit must be within the frozen call budget')
        target = step['target']
        if kind in CORE and target is not None:
            raise ValueError('core steps cannot redefine scientific targets')
        if kind == 'inspect' and target not in source_refs:
            raise ValueError('inspection target must be an authorized source')
        if kind in ('investigate', 'assess') and (not isinstance(target, str) or not TARGET.fullmatch(target)):
            raise ValueError('parameter step requires a supported parameter target')
        if kind == 'assess' and target not in parameter_targets:
            raise ValueError('assessment target must have frozen requirements')
        result.append(deepcopy(step)); ids.add(sid)
    by_id = {s['id']: s for s in result}
    if not set(CORE) <= ids or by_id['sources']['depends_on']:
        raise ValueError('all core steps are required; sources has no dependencies')
    for previous, sid in zip(CORE, CORE[1:]):
        if previous not in by_id[sid]['depends_on']:
            raise ValueError('mandatory core dependency cannot be removed: '+sid+' -> '+previous)
    ordered, visiting, visited = [], set(), set()
    def visit(sid):
        if sid not in ids or sid in visiting:
            raise ValueError('plan dependencies must form a closed acyclic graph')
        if sid in visited: return
        visiting.add(sid)
        for dep in by_id[sid]['depends_on']: visit(dep)
        visiting.remove(sid); visited.add(sid); ordered.append(by_id[sid])
    for sid in by_id: visit(sid)
    return ordered


def create(store, source_refs, max_calls, parameter_targets=(), *, comparison_required=False):
    if type(max_calls) is not int or not 1 <= max_calls <= 50:
        raise ValueError('planning requires a frozen call budget in 1..50')
    steps = [dict(id=sid, kind=sid, goal=goal, depends_on=[CORE[i-1]] if i else [],
                  target=None, attempt_limit=1 if sid in ('execute', 'compare') else max_calls)
             for i, (sid, goal) in enumerate(zip(CORE, GOALS))]
    steps = _steps(steps, max_calls, source_refs, parameter_targets)
    payload = dict(version=VERSION, revision=0, edit_count=0, previous_ref=None,
                   reason='Initial bounded ODE reproduction plan', revision_type='initial', call=0,
                   source_refs=list(source_refs), scope_after=-1, max_calls=max_calls,
                   parameter_targets=sorted(parameter_targets), comparison_required=bool(comparison_required),
                   steps=steps, fresh_after={s['id']: -1 for s in steps},
                   attempt_after={s['id']: 0 for s in steps},
                   step_bindings={s['id']: dict(kind=s['kind'], target=s['target']) for s in steps},
                   budget_caps={s['id']: s['attempt_limit'] for s in steps}, changed_steps=[])
    ref = store.put('task_plan', payload, refs=source_refs)
    return dict(version=VERSION, initial_plan_ref=ref, max_edits=MAX_EDITS)


def _change(parent, previous_ref, steps, reason, call, *, source_refs=None):
    if type(call) is not int or not parent['call'] <= call <= parent['max_calls']:
        raise ValueError('revision call is outside the cumulative task budget')
    reason = _reason(reason)
    refresh = source_refs is not None
    sources = list(source_refs) if refresh else parent['source_refs']
    normalized = _steps(steps, parent['max_calls'], sources, parent['parameter_targets'])
    if not refresh and parent['edit_count'] >= MAX_EDITS:
        raise ValueError('plan revision budget exhausted')
    if not refresh and normalized == parent['steps']:
        raise ValueError('plan revision must change a step; no-op revisions are rejected')
    if refresh and (sources[:len(parent['source_refs'])] != parent['source_refs'] or len(sources) <= len(parent['source_refs'])):
        raise ValueError('plan refresh must append attributed reply sources')
    old = {s['id']: s for s in parent['steps']}
    changed = {s['id'] for s in normalized if old.get(s['id']) != s}
    if refresh: changed.update(s['id'] for s in normalized)
    for step in normalized:
        sid = step['id']
        if sid in parent['step_bindings'] and parent['step_bindings'][sid] != dict(kind=step['kind'], target=step['target']):
            raise ValueError('an existing step id cannot change its kind or target; add a new step')
        if sid in parent['budget_caps'] and step['attempt_limit'] > parent['budget_caps'][sid]:
            raise ValueError('a revision cannot increase a previously set step budget')
        if sid not in parent['budget_caps'] and step['attempt_limit'] > parent['max_calls']-call:
            raise ValueError('new step budget exceeds remaining task calls')
    # Changing a prerequisite invalidates its descendants, even when the new
    # prerequisite has not produced a tool result yet.
    for step in normalized:
        if any(d in changed for d in step['depends_on']): changed.add(step['id'])
    p = deepcopy(parent)
    p.update(revision=parent['revision']+1, edit_count=parent['edit_count']+(not refresh),
             previous_ref=previous_ref, reason=reason, call=call, steps=normalized,
             revision_type='source_update' if refresh else 'model_revision',
             source_refs=sources, scope_after=call if refresh else parent['scope_after'],
             changed_steps=sorted(changed))
    p['fresh_after'] = {s['id']: call if s['id'] in changed else parent['fresh_after'][s['id']] for s in normalized}
    p['budget_caps'].update({s['id']: s['attempt_limit'] for s in normalized})
    for step in normalized:
        p['attempt_after'].setdefault(step['id'], call)
        p['step_bindings'].setdefault(step['id'], dict(kind=step['kind'], target=step['target']))
    return p


def load(store, config, ref):
    if config != dict(version=VERSION, initial_plan_ref=config.get('initial_plan_ref'), max_edits=MAX_EDITS):
        raise ValueError('unsupported frozen planning policy')
    chain = []
    while True:
        env = store.get(ref)
        p = env['payload']
        if env['kind'] != 'task_plan' or p.get('version') != VERSION or len(chain) > MAX_EDITS+8:
            raise ValueError('invalid task plan lineage')
        expected_refs = set(p['source_refs']) | ({p['previous_ref']} if p['previous_ref'] else set())
        if set(env['refs']) != expected_refs:
            raise ValueError('task plan is missing its immutable source or revision links')
        chain.append((ref, p))
        if ref == config['initial_plan_ref']: break
        ref = p['previous_ref']
    root = chain[-1][1]
    if root['revision'] != 0 or root['previous_ref'] is not None:
        raise ValueError('invalid initial task plan')
    chronological = list(reversed(chain))
    for (parent_ref, parent), (_, child) in zip(chronological, chronological[1:]):
        fresh = _change(parent, parent_ref, child['steps'], child['reason'], child['call'],
                        source_refs=child['source_refs'] if child['revision_type'] == 'source_update' else None)
        if canonical_bytes(child) != canonical_bytes(fresh):
            raise ValueError('plan revision differs from recomputed transition')
    return chain[0][1]


def current_ref(config, events):
    refs = [e['plan_ref'] for e in events if e.get('type') == 'tool_result'
            and e.get('tool') in ('revise_plan', 'refresh_plan') and e.get('ok') is True]
    return refs[-1] if refs else config['initial_plan_ref']


def revise(store, config, current, expected, steps, reason, call, *, run_count=0):
    if expected != current: raise ValueError('plan changed; inspect_progress and revise the current plan')
    if run_count: raise ValueError('cannot revise a plan after scientific execution')
    parent = load(store, config, current)
    payload = _change(parent, current, steps, reason, call)
    ref = store.put('task_plan', payload, refs=[current, *payload['source_refs']])
    return dict(plan_ref=ref, revision=payload['revision'], changed_steps=payload['changed_steps'])


def refresh(store, config, current, source_refs, call):
    parent = load(store, config, current)
    for ref in source_refs[len(parent['source_refs']):]:
        from .source_review import _researcher_assertion
        if _researcher_assertion(store, store.get(ref)) is None:
            raise ValueError('new plan sources must be attributed replies from the existing resume flow')
    payload = _change(parent, current, parent['steps'],
        'Attributed reply extended the source scope; revalidate dependent work.', call, source_refs=source_refs)
    ref = store.put('task_plan', payload, refs=[current, *source_refs])
    return dict(plan_ref=ref, revision=payload['revision'], changed_steps=payload['changed_steps'])


def project(store, config, events, *, source_refs, draft_ref=None, model_ref=None, run_ref=None,
            state='running', calls=0):
    ref = current_ref(config, events)
    p = load(store, config, ref)
    scope_ok = p['source_refs'] == list(source_refs)
    submissions = [e for e in events if e.get('type') == 'tool_result' and e.get('tool') in ('submit_draft', 'submit_plan')]
    latest = submissions[-1] if submissions else {}
    outputs = [e for e in events if e.get('type') == 'tool_result' and e.get('tool') == 'run' and e.get('ok') is True]
    executed = outputs[-1] if outputs else {}
    execution = store.get(run_ref)['payload'] if run_ref else None
    if execution and model_ref:
        frozen = store.get(execution['input_ref'])['payload']['artifact']
        if frozen != store.get(model_ref)['payload']: execution = None
    if not model_ref: execution = None
    from .application import derive_scientific_status
    eligible = bool(execution and execution.get('status') == 'succeeded' and derive_scientific_status(execution) != 'failed')
    replies = {e['call']: e for e in events if e.get('type') == 'llm_call'}
    step_views = {}
    settled = {'complete', 'not_applicable'}
    for step in p['steps']:
        sid, kind = step['id'], step['kind']
        matching = []
        for e in events:
            request = e.get('policy_args', replies.get(e.get('call'), {}).get('response', {}).get('args', {}))
            target = request.get('source_ref') if kind == 'inspect' else request.get('target')
            proposal_match = TOOLS.get(kind) == 'submit_draft' and e.get('tool') == 'submit_plan'
            if (e.get('type') in ('tool_result', 'tool_error') and (e.get('tool') == TOOLS.get(kind) or proposal_match)
                    and e.get('call', 0) > p['attempt_after'][sid]
                    and (kind in CORE or target == step['target'])):
                matching.append(e)
        attempts = len(matching)
        if kind == 'report':
            blocked = {e['call'] for e in events if e.get('type') == 'plan_blocked' and e.get('tool') == 'finish'}
            attempts = sum(e.get('action') == 'finish' and e['call'] not in blocked
                           for e in events if e.get('type') == 'llm_call')
        proof, proof_call, outcome, bad = [], -1, None, False
        if kind == 'sources' and scope_ok:
            proof, proof_call = list(source_refs), max(0, p['scope_after'])
        elif kind == 'draft' and draft_ref and draft_ref in latest.get('artifact_refs', []):
            proof, proof_call = [draft_ref], latest['call']
        elif kind == 'validate' and model_ref and latest.get('state') == 'ready' and model_ref in latest.get('artifact_refs', []):
            proof, proof_call = [model_ref], latest['call']
        elif kind == 'execute' and execution and executed.get('run_ref') == run_ref:
            proof, proof_call = [run_ref], executed['call']
            bad = execution.get('status') != 'succeeded'
        elif kind == 'compare' and execution and executed.get('run_ref') == run_ref:
            proof, proof_call = [run_ref], executed['call']
            outcome = execution.get('comparison', {}).get('status')
            bad = not eligible or (p['comparison_required'] and outcome != 'pass')
        elif kind == 'report' and state == 'completed' and eligible:
            finishes = [e['call'] for e in events if e.get('type') == 'llm_call' and e.get('action') == 'finish']
            if finishes: proof, proof_call = [run_ref], finishes[-1]
        elif kind in ('inspect', 'investigate', 'assess'):
            for e in reversed(matching):
                if e.get('type') != 'tool_result' or e.get('ok') is False: continue
                if kind == 'inspect':
                    if e.get('ok') is True: proof, proof_call = [step['target']], e['call']
                else:
                    refs = e.get('artifact_refs', [])
                    if refs:
                        data = store.get(refs[0])['payload']
                        if data.get('target') == step['target'] and data.get('source_refs') == list(source_refs):
                            proof, proof_call = [refs[0]], e['call']
                            outcome = data.get('status')
                            bad = kind == 'assess' and outcome != 'declared_fields_match'
                if proof: break
        deps = [step_views[d] for d in step['depends_on']]
        waiting = [d['id'] for d in deps if d['status'] not in settled]
        after = max([d['proof_call'] for d in deps] or [-1])
        fresh = scope_ok and proof_call > p['fresh_after'][sid] and proof_call >= after
        if kind == 'sources': fresh = scope_ok
        status = ('stale' if not scope_ok or (proof and not fresh) else
                  'blocked' if waiting else 'failed' if bad else
                  'not_applicable' if proof and kind == 'compare' and not p['comparison_required'] else
                  'complete' if proof else 'blocked' if kind == 'validate' and latest else
                  'budget_exhausted' if attempts >= step['attempt_limit'] else 'ready')
        step_views[sid] = dict(**step, status=status, artifact_refs=proof, proof_call=proof_call,
                              outcome=outcome, waiting_for=waiting, attempts=attempts,
                              remaining_attempts=max(0, step['attempt_limit']-attempts))
    pending = [s['id'] for s in step_views.values() if s['status'] not in settled]
    return dict(plan_ref=ref, revision=p['revision'], edit_count=p['edit_count'],
                remaining_revisions=MAX_EDITS-p['edit_count'], reason=p['reason'],
                changed_steps=p['changed_steps'], source_scope_current=scope_ok,
                steps=list(step_views.values()), remaining_calls=max(0, p['max_calls']-calls),
                next_step=pending[0] if pending else None, scientific_support='unknown',
                note='Typed tool outputs determine completion; a descriptive goal is not a scientific assertion.')


def guard(action, args, view):
    """Return an actionable block; no action, budget or authority is granted."""
    if action == 'submit_plan':
        action = 'submit_draft'
    required = {'submit_draft': 'draft', 'run': 'execute', 'finish': 'report'}.get(action)
    relevant = [s for s in view['steps'] if s['id'] == required or
                (s['kind'] in ('inspect', 'investigate', 'assess') and TOOLS[s['kind']] == action
                 and s['target'] == args.get('source_ref' if s['kind'] == 'inspect' else 'target'))]
    for step in relevant:
        if not view['source_scope_current'] or step['waiting_for']:
            return 'Unmet plan dependencies for '+step['id']+': '+', '.join(step['waiting_for'])
    # submit_draft also validates and run also compares. Their shared tools
    # must respect both steps' limits, without requiring their postconditions
    # to hold before the operation that produces them.
    budgeted = [s for s in view['steps'] if (s in relevant or
               (s['kind'] in ('validate', 'compare') and TOOLS[s['kind']] == action))]
    for step in budgeted:
        if step['remaining_attempts'] <= 0 and step['kind'] != 'report':
            return 'Plan step attempt budget exhausted: '+step['id']
    if action == 'finish':
        pending = [s['id'] for s in view['steps'] if s['id'] != 'report' and s['status'] not in ('complete', 'not_applicable')]
        if pending: return 'Complete or resolve planned steps before finishing: '+', '.join(pending)
    return None
