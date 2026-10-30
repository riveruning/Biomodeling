"""Candidate experience -> explicit operator review -> scoped advisory memory.

Candidates are never included in memory snapshots. Reviews are immutable;
activation references the approval and uses the existing retraction machinery.
"""
from . import memory
from .workstate import from_run


def propose(store, run_ref, *, project_id, author, applicability, conditions=None):
    memory._text(author, 'author', 200)
    memory._text(applicability, 'applicability', 1000)
    memory._scope(store, project_id, None)
    run = store.get(run_ref)
    if run['kind'] != 'agent_run' or not run['payload'].get('task_ref'):
        raise ValueError('candidate requires an attributed agent_run with a task')
    progress = from_run(store, run_ref)
    p = run['payload']
    declared = memory.conditions(conditions)
    task = store.get(p['task_ref'])['payload']
    used = sorted({hit['memory_ref'] for event in p['events'] if event.get('tool') == 'recall_memory'
                   for hit in event.get('results', []) if hit.get('memory_ref')})
    model_ref = p.get('model_ref') or (task.get('memory_scope') or {}).get('model_ref')
    observations = [*progress['failures'], *progress['unresolved']]
    for event in p['events']:
        observations.extend(i for i in event.get('validation_feedback', {}).get('items', []) if i.get('blocking'))
    categories = sorted({i['category'] for i in observations})
    content = ('Observed task state: ' + p['state'] + '. Recorded issue categories: ' +
               (', '.join(categories) or 'none recorded') + '. '
               'Inspect task sources and saved validation checks before reusing this workflow. '
               'This experience does not supply parameters or establish scientific correctness.')
    # Keep complete tool-derived advice, never parameter values or model prose.
    seen = set()
    for item in observations:
        suggestion = item.get('suggestion')
        if not suggestion or suggestion in seen:
            continue
        seen.add(suggestion)
        addition = '\nObserved ' + item.get('code', item['category']) + ': ' + suggestion
        if len(content + addition + '\nApplicable when: ' + applicability) <= 2000:
            content += addition
    memory._text(content + '\nApplicable when: ' + applicability, 'candidate content', 2000)
    payload = dict(version=2 if declared is not None or used else 1, project_id=project_id, model_ref=model_ref,
                   source_refs=p['source_refs'], run_ref=run_ref, category='experience',
                   content=content, applicability=applicability, author=author,
                   status='pending', authority='candidate_only', semantic_support='unknown')
    if declared is not None:
        payload['conditions'] = declared
    if used:
        payload['depends_on'] = used
    refs = [run_ref, *p['source_refs'], *used, *([model_ref] if model_ref else [])]
    return store.put('memory_candidate', payload, refs=tuple(dict.fromkeys(refs)))


def inspect(store, ref):
    obj = store.get(ref)
    if obj['kind'] != 'memory_candidate':
        raise ValueError('expected a memory_candidate')
    reviews = [r for r in store.list('memory_candidate_review')
               if store.get(r)['payload']['candidate_ref'] == ref]
    if len(reviews) > 1:
        raise ValueError('conflicting candidate reviews; do not activate')
    p = obj['payload']
    try:
        memory.validate_advice(store, project_id=p['project_id'], source_refs=p['source_refs'], model_ref=p['model_ref'],
                               conditions_=p.get('conditions'), depends_on=p.get('depends_on', []))
        dependency_status, dependency_error = 'current', None
    except ValueError as exc:
        dependency_status, dependency_error = 'stale_or_out_of_scope', str(exc)
    return dict(candidate_ref=ref, **p, review_refs=reviews,
                review_status=store.get(reviews[0])['payload']['decision'] if reviews else 'pending',
                dependency_status=dependency_status, dependency_error=dependency_error)


def review(store, ref, *, decision, reviewer, reason):
    memory._text(reviewer, 'reviewer', 200)
    memory._text(reason, 'reason', 1000)
    if decision not in ('approved', 'rejected'):
        raise ValueError('decision must be approved or rejected')
    candidate = inspect(store, ref)
    if candidate['review_refs']:
        raise ValueError('candidate already reviewed; existing review is immutable')
    if decision == 'approved' and candidate['dependency_status'] != 'current':
        raise ValueError('candidate dependencies are stale or outside applicability scope')
    return store.put('memory_candidate_review', dict(version=1, candidate_ref=ref,
        decision=decision, reviewer=reviewer, reason=reason, authority='operator_review',
        semantic_support='unknown'), refs=(ref,))


def activate(store, review_ref):
    obj = store.get(review_ref)
    if obj['kind'] != 'memory_candidate_review' or obj['payload'].get('decision') != 'approved':
        raise ValueError('activation requires an approved operator review')
    review_data = obj['payload']
    candidate = inspect(store, review_data['candidate_ref'])
    if candidate['review_refs'] != [review_ref]:
        raise ValueError('candidate approval is ambiguous')
    if candidate['dependency_status'] != 'current':
        raise ValueError('candidate dependencies changed after review; do not activate')
    for ref in store.list(memory.KIND):
        if store.get(ref)['payload'].get('candidate_review_ref') == review_ref:
            raise ValueError('review was already activated; use normal correction/retraction instead')
    content = candidate['content'] + '\nApplicable when: ' + candidate['applicability']
    # Explicit review, not candidate generation, is the authority for this write.
    return memory.remember(store, project_id=candidate['project_id'], model_ref=candidate['model_ref'],
                           source_refs=candidate['source_refs'], content=content,
                           category='experience', author=review_data['reviewer'],
                           reason=review_data['reason'], candidate_review_ref=review_ref,
                           conditions=candidate.get('conditions'), depends_on=candidate.get('depends_on', []))


def derivations(store, run_ref):
    """Derived links live beside the immutable run, not inside its own lineage."""
    store.get(run_ref)
    return [ref for ref in store.list('experience_derivation')
            if store.get(ref)['payload']['run_ref'] == run_ref]


def derive_for_run(store, run_ref):
    """Idempotent, tool-derived post-run proposal; no calls, approval or recall."""
    run = store.get(run_ref)
    if run['kind'] != 'agent_run' or not run['payload'].get('task_ref'):
        raise ValueError('experience derivation requires an actual attributed agent_run')
    task_ref = run['payload']['task_ref']; task = store.get(task_ref)
    policy = task['payload'].get('experience_policy')
    if policy is None:
        raise ValueError('experience proposals were not enabled in this frozen task')
    previous = derivations(store, run_ref)
    if previous:
        if len(previous) != 1:
            raise ValueError('ambiguous experience derivation records')
        return previous[0]
    if task['kind'] != 'agent_task' or task_ref not in run['refs'] or task['payload'].get('mode') != 'reproduction':
        raise ValueError('experience derivation task binding changed')
    scope = task['payload'].get('memory_scope') or {}
    if (policy.get('version') != 1 or policy.get('project_id') != scope.get('project_id')
            or policy.get('conditions') != scope.get('context') or memory.conditions(policy.get('conditions')) is None):
        raise ValueError('experience proposal policy/context changed')
    progress = from_run(store, run_ref)
    observations = [*progress['failures'], *progress['unresolved']]
    for event in run['payload']['events']:
        observations.extend(i for i in event.get('validation_feedback', {}).get('items', []) if i.get('blocking'))
    candidate = None
    if observations:
        candidate = propose(store, run_ref, project_id=policy['project_id'],
            author='agent_runtime_tool_projection_v1', conditions=policy['conditions'],
            applicability='Workflow advice only under the same declared context; recheck current sources and tool diagnostics. No parameter or equation authority.')
    return store.put('experience_derivation', dict(version=1, run_ref=run_ref, task_ref=task_ref,
        candidate_ref=candidate, status='candidate_pending' if candidate else 'not_applicable_no_recorded_issue',
        authority='post_run_tool_projection', semantic_support='unknown', provider_calls=0,
        notice='Derived after the original run commit; never approved, activated or recalled into that run.'),
        refs=(run_ref, task_ref, *([candidate] if candidate else [])))
