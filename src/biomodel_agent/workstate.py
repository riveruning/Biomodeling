"""Deterministic task progress and parameter provenance, derived from artifacts.

This projection cannot mark a step complete on an LLM's assertion, choose a
parameter, or change a frozen study. Full source text remains in source refs.
"""
from __future__ import annotations

from .diagnostics import event_failure


def parameter_ledger(store, draft_ref):
    if draft_ref is None:
        return []
    obj = store.get(draft_ref)
    if obj['kind'] != 'model_draft':
        raise ValueError('parameter ledger requires a model_draft')
    draft = obj['payload']
    rows = []
    for parameter in draft.get('parameters', []):
        target = parameter['id'] + '/value'
        citations = []
        for ref in draft.get('element_map', {}).get(target, []):
            claim = store.get(ref)
            if claim['kind'] != 'claim' or claim['payload'].get('statement') != target:
                continue
            p = claim['payload']
            citation = {'claim_ref': ref, **{k: p.get(k) for k in
                              ('source_ref', 'page', 'start', 'end', 'claim_type', 'quote')}}
            source = store.get(p['source_ref'])['payload']
            if source.get('derivation'):
                from .parameter_sources import validate
                citation['parameter_derivation'] = validate(store, p['source_ref'])
            citations.append(citation)
        rows.append({'target': target, 'value': parameter.get('value'),
                     'unit': parameter.get('unit', 'unknown'),
                     'declared_origin': parameter.get('origin', 'unknown'),
                     'value_status': 'missing' if parameter.get('value') is None else 'proposed',
                     'citations': citations, 'conditions_status': 'not_verified',
                     'units_status': 'unknown', 'semantic_support': 'unknown'})
    return rows


def project(store, *, source_refs, events, draft_ref=None, model_ref=None, run_ref=None,
            state='running', study_plan_ref=None, calls=0, max_calls=0, planning=None, failure_policy=None, skill_protocol=None,
            estimation_plan_ref=None):
    """Reconstruct after resume/recovery from the same immutable event lineage."""
    proposals = [(i, e) for i, e in enumerate(events)
                 if e.get('type') == 'tool_result' and e.get('tool') in ('submit_draft', 'submit_plan')]
    latest = proposals[-1][1] if proposals else {}
    feedback = latest.get('validation_feedback', {}).get('items', [])
    pending = [dict(item) for item in feedback if item.get('blocking')]
    for e in events[-1:]:
        if e.get('type') == 'awaiting_input':
            pending.append({'category': 'researcher_input', 'message': e['question'],
                            'blocking': True, 'route': 'clarify'})
    failures = [dict(event_index=i, call=e.get('call'), **d)
                for i, e in enumerate(events) if (d := event_failure(e))]
    run_events = [e for e in events if e.get('type') == 'tool_result'
                  and e.get('tool') in ('run', 'run_study') and e.get('ok') is True]
    run = run_events[-1] if run_events else {}
    attempted = any(e.get('type') == 'llm_call' and e.get('action') in ('run', 'run_study') for e in events)
    numerical_failed = run.get('scientific_status') == 'failed'
    executed = run_ref is not None and bool(run_events)
    if study_plan_ref:
        steps = [dict(id='frozen_study', depends_on=[], status='complete', artifact_refs=[study_plan_ref]),
                 dict(id='execute', depends_on=['frozen_study'],
                      status='complete' if executed else 'failed' if attempted else 'pending',
                      artifact_refs=[run_ref] if run_ref else [])]
    else:
        steps = [
            dict(id='sources', depends_on=[], status='available', artifact_refs=list(source_refs)),
            dict(id='draft', depends_on=['sources'], status='complete' if model_ref else 'blocked' if latest else 'pending',
                 artifact_refs=[draft_ref] if draft_ref else []),
            dict(id='validate', depends_on=['draft'], status='complete' if model_ref else 'blocked' if latest else 'pending',
                 artifact_refs=[model_ref] if model_ref else []),
            dict(id='execute', depends_on=['validate'], status='complete' if executed else 'failed' if attempted else 'pending',
                 artifact_refs=[run_ref] if run_ref else []),
        ]
    steps.append(dict(id='compare_and_report', depends_on=['execute'],
                      status='failed' if numerical_failed else 'complete' if state == 'completed' else 'pending',
                      artifact_refs=[run_ref] if run_ref else []))
    if state == 'awaiting_input':
        next_action = 'Resolve the recorded question and resume the same task with an attributed reply.'
    elif pending:
        next_action = pending[0].get('suggestion', pending[0]['message'])
    elif not model_ref and not study_plan_ref:
        next_action = 'Inspect task sources and investigate missing parameters, then submit a cited draft.'
    elif not executed:
        next_action = 'Use run_study once for the frozen plan.' if study_plan_ref else 'Run the validated model once.'
    elif state == 'completed':
        next_action = 'Review the numerical result and remaining scientific unknowns.'
    else:
        next_action = 'Inspect the saved execution checks; finish only if completion is eligible.'
    parameters = parameter_ledger(store, draft_ref)
    for row in parameters:
        row['declared_context'] = next((d for d in latest.get('parameter_diagnostics', []) if d['target'] == row['target']), None)
    assessments = {}
    for i, event in enumerate(events):
        if event.get('type') == 'tool_result' and event.get('tool') == 'assess_parameter' and event.get('artifact_refs'):
            ref = event['artifact_refs'][0]
            payload = store.get(ref)['payload']
            assessments[event['target']] = dict(assessment_ref=ref, event_index=i,
                status=payload['status'] if payload['source_refs'] == list(source_refs) else 'stale_source_scope')
    result = dict(version=1, state=state, steps=steps, unresolved=pending,
                **({'parameter_assessments': assessments} if assessments else {}),
                draft_ref=draft_ref, parameters=parameters, failures=failures,
                proposal_revisions=len(proposals),
                last_proposal_event=proposals[-1][0] if proposals else None,
                remaining_calls=max(0, max_calls-calls), next_action=next_action,
                scientific_support='not_verified',
                note='Derived workflow progress, not independent scientific approval. Historical errors remain archived.')
    if latest.get('tool') == 'submit_plan':
        result['modeling_decisions'] = dict(submission_ref=latest.get('submission_ref'),
            plan_ref=latest.get('modeling_plan_ref'), compilation_ref=latest.get('compilation_ref'),
            submission_state=latest.get('state'), model_ready=bool(model_ref and latest.get('state') == 'ready'),
            source_scope_current=False, scientific_support='unknown')
        if latest.get('modeling_plan_ref'):
            from . import modeling_plan
            plan = modeling_plan.load(store, latest['modeling_plan_ref'])
            result['modeling_decisions'].update(source_scope_current=plan['source_refs'] == list(source_refs),
                decision_feedback=modeling_plan.inspect(store, latest['modeling_plan_ref']))
            result['modeling_decisions']['model_ready'] = bool(result['modeling_decisions']['model_ready']
                                                               and result['modeling_decisions']['source_scope_current'])
    if estimation_plan_ref:
        outputs = [e for e in events if e.get('tool') == 'run_estimation' and e.get('ok') is True]
        last = outputs[-1] if outputs else {}
        result['steps'] = [
            dict(id='frozen_estimation', depends_on=[], status='complete', artifact_refs=[estimation_plan_ref]),
            dict(id='estimate', depends_on=['frozen_estimation'], status='complete' if last.get('execution_status') == 'succeeded' else 'failed' if outputs else 'pending',
                 artifact_refs=last.get('artifact_refs', [])),
            dict(id='operator_review', depends_on=['estimate'], status='pending', artifact_refs=[])]
        result['estimation'] = dict(plan_ref=estimation_plan_ref, result_ref=last.get('result_ref'),
            execution_status=last.get('execution_status'), adoption_status='not_adopted')
        result['next_action'] = ('Review the estimate and unresolved items explicitly; do not resume this branch with a clarification source.'
            if last.get('execution_status') == 'succeeded' else
            'Inspect the recorded estimation failure; no automatic retry or candidate adoption.' if outputs else
            'Use run_estimation once; inspect receipts rather than retrying an unknown result.')
    elif events and events[-1].get('origin') == 'estimation_request':
        result['next_action'] = 'Review proposed targets and prepare an explicit separate estimation protocol; no fit has run.'
    if planning:
        from . import task_plans
        result['task_plan'] = task_plans.project(store, planning, events, source_refs=source_refs,
            draft_ref=draft_ref, model_ref=model_ref, run_ref=run_ref, state=state, calls=calls)
        if state != 'awaiting_input' and result['task_plan']['next_step']:
            result['next_action'] = 'Resolve the next planned step: '+result['task_plan']['next_step']
    if failure_policy:
        from .failure_handling import progress
        result['failure_handling'] = progress(store, failure_policy, events)
    if skill_protocol:
        from .scientific_skills import project as procedures
        result['scientific_skills'] = procedures(store, skill_protocol, events, source_refs=source_refs,
            draft_ref=draft_ref, model_ref=model_ref, run_ref=run_ref)
    return result


def from_run(store, ref):
    record = store.get(ref)
    if record['kind'] not in ('agent_run', 'agent_checkpoint'):
        raise ValueError('progress requires an agent_run or agent_checkpoint')
    p = record['payload']
    task = store.get(p['task_ref'])['payload']
    draft_ref = p.get('draft_ref')
    if draft_ref is None and p.get('checkpoint_ref'):
        draft_ref = store.get(p['checkpoint_ref'])['payload'].get('draft_ref')
    return project(store, source_refs=p['source_refs'], events=p['events'],
                   draft_ref=draft_ref, model_ref=p.get('model_ref'), run_ref=p.get('run_ref'),
                   state=p['state'], study_plan_ref=task.get('study_plan_ref'),
                   calls=p['call_count'], max_calls=task['max_calls'], planning=task.get('planning'), failure_policy=task.get('failure_policy'), skill_protocol=task.get('skill_protocol'),
                   estimation_plan_ref=task.get('estimation_plan_ref'))
