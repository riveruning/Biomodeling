"""Execution handlers for the agent's scientific and planning actions.

The agent loop retains ownership of action guards, journal-backed invocation
budgeting, event ordering, checkpoints, state transitions, termination and
recovery.  Each handler performs exactly one action through the caller's
``invoke`` callable and returns the feedback that the loop must persist.

``call_count``/``run_count`` are live callables, not snapshots: the revise
callback must observe the pre-reservation counts, while events and plan
projections use the post-invocation counts that the agent's journal wrapper
synchronizes in its ``finally`` block.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = (
    'ScientificToolOutcome',
    'execute_assess_parameter',
    'execute_request_estimation',
    'execute_revise_plan',
    'execute_run_estimation',
    'execute_run_study',
)


@dataclass(frozen=True)
class ScientificToolOutcome:
    """Normalized result of one scientific/planning action.

    The caller owns event/message ordering, checkpoint persistence, state
    application and termination.  ``run_ref`` carries the partially
    successful study execution reference so a summary failure cannot erase
    the journaled scientific result.
    """

    events: tuple[dict, ...]
    message: dict | None = None
    checkpoint: bool = False
    stop: bool = False
    state: str | None = None
    run_ref: str | None = None


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def execute_request_estimation(args, *, store, task_ref, source_refs,
                               estimation_requests, invoke, call_count,
                               run_count) -> ScientificToolOutcome:
    action = 'request_estimation'
    try:
        if not estimation_requests or run_count():
            raise ValueError('estimation requests require the enabled ordinary task before scientific evaluation')
        from . import estimation_workflow as workflow
        request_ref = invoke(action, lambda: workflow.request(store, task_ref, source_refs, args),
                             details={'args': args, 'source_refs': source_refs},
                             summarize=lambda r: {'request_ref': r},
                             result_refs=lambda r: [r])
        call = call_count()
        return ScientificToolOutcome(events=(
            dict(type='tool_result', tool=action, call=call, ok=True,
                 request_ref=request_ref, artifact_refs=[request_ref]),
            dict(type='awaiting_input', call=call, origin='estimation_request',
                 request_ref=request_ref,
                 question='Review the proposed estimation targets and explicitly freeze a separate estimation protocol; this request does not run fitting.'),
        ), stop=True, state='awaiting_input')
    except (ValueError, TypeError) as exc:
        feedback = dict(tool=action, ok=False, error=str(exc))
        return ScientificToolOutcome(
            events=(dict(type='tool_error', call=call_count(), **feedback),),
            message=_user(_json(feedback)))


def execute_run_estimation(args, *, store, estimation_plan_ref, invoke,
                           call_count, run_count) -> ScientificToolOutcome:
    action = 'run_estimation'
    if args or estimation_plan_ref is None or run_count():
        feedback = dict(tool=action, ok=False,
                        error='run_estimation requires empty args and an operator-bound frozen estimation plan, at most once.')
        return ScientificToolOutcome(
            events=(dict(type='tool_error', call=call_count(), **feedback),),
            message=_user(_json(feedback)))
    emitted: list[dict] = []
    message = None
    try:
        from . import estimation, estimation_workflow as workflow
        result_ref = invoke(action, lambda: estimation.execute(store, estimation_plan_ref),
                            details={'plan_ref': estimation_plan_ref},
                            summarize=lambda r: {'result_ref': r},
                            result_refs=lambda r: [r])
        workflow.check_result(store, result_ref, plan_ref=estimation_plan_ref)
        facts = estimation.result_summary(store, result_ref)
        call = call_count()
        emitted.append(dict(type='tool_result', tool=action, call=call, ok=True,
                            result_ref=result_ref, **facts,
                            artifact_refs=[result_ref]))
        message = _user(_json(dict(tool=action, ok=True,
                                   result_ref=result_ref, **facts)))
        state = 'awaiting_input' if facts['execution_status'] == 'succeeded' else 'incomplete'
        if state == 'awaiting_input':
            emitted.append(dict(type='awaiting_input', call=call,
                                origin='estimation_review', result_ref=result_ref,
                                question='Review the recorded estimate candidates and unresolved items with estimate-review; no automatic adoption or strict reproduction completion.'))
        return ScientificToolOutcome(events=tuple(emitted), message=message,
                                     stop=True, state=state)
    except Exception as exc:
        emitted.append(dict(type='tool_error', tool=action, call=call_count(),
                            error=f'{type(exc).__name__}: {exc}'))
        return ScientificToolOutcome(events=tuple(emitted), message=message,
                                     stop=True, state='incomplete')


def execute_revise_plan(args, *, store, planning, events, source_refs,
                        draft_ref, model_ref, run_ref, invoke, call_count,
                        run_count) -> ScientificToolOutcome:
    action = 'revise_plan'
    emitted: list[dict] = []
    try:
        from . import task_plans
        if planning is None or set(args) != {'expected_plan_ref', 'reason', 'steps'}:
            raise ValueError('revise_plan requires enabled task planning and exactly expected_plan_ref/reason/steps')
        current_plan = task_plans.current_ref(planning, events)
        out = invoke(action, lambda: task_plans.revise(store, planning, current_plan,
                     args['expected_plan_ref'], args['steps'], args['reason'],
                     call_count(), run_count=run_count()),
                     details={'args': args},
                     result_refs=lambda r: [r['plan_ref']])
        event = dict(type='tool_result', tool=action, ok=True, call=call_count(),
                     **out, artifact_refs=[out['plan_ref']])
        emitted.append(event)
        # Project only after the revision tool_result is part of the history,
        # matching the original append-then-project ordering.
        feedback = dict(tool=action, ok=True, task_plan=task_plans.project(
            store, planning, [*events, *emitted], source_refs=source_refs,
            draft_ref=draft_ref, model_ref=model_ref, run_ref=run_ref,
            calls=call_count()))
    except (ValueError, TypeError) as exc:
        feedback = dict(tool=action, ok=False, error=str(exc))
        emitted.append(dict(type='tool_error', call=call_count(), **feedback))
    return ScientificToolOutcome(events=tuple(emitted),
                                 message=_user(_json(feedback)),
                                 checkpoint=True)


def execute_run_study(args, *, store, mode, study_plan_ref, invoke,
                      call_count, run_count) -> ScientificToolOutcome:
    action = 'run_study'
    if args or mode != 'hypothesis' or study_plan_ref is None or run_count():
        feedback = {'tool': action, 'ok': False,
                    'error': 'run_study requires empty args, an authorized hypothesis study plan, and no previous evaluation.'}
        return ScientificToolOutcome(
            events=({'type': 'tool_error', 'call': call_count(), **feedback},),
            message=_user(_json(feedback)))
    executed_ref = None
    try:
        from . import studies
        # Assign before summarizing: a summary failure is a partial success
        # and must keep the journaled study reference in the final run.
        # The journal persists the scientific-execution reservation before
        # fitting; on failure it records error/interrupted, then the original
        # exception propagates and is turned into the internal diagnostic.
        executed_ref = invoke('run_study',
                              lambda: studies.execute_study(store, study_plan_ref),
                              details={'plan_ref': study_plan_ref},
                              summarize=lambda r: {'result_ref': r},
                              result_refs=lambda r: [r])
        facts = studies.result_summary(store, executed_ref)
    except Exception as exc:
        failure_ref = store.put('study_execution_failure',
            {'plan_ref': study_plan_ref,
             'error': f'{type(exc).__name__}: {exc}'}, refs=(study_plan_ref,))
        event = {'type': 'tool_error', 'call': call_count(), 'tool': action,
                 'error': 'Frozen study execution failed; see internal diagnostic artifact.',
                 'artifact_refs': [failure_ref]}
        return ScientificToolOutcome(events=(event,), stop=True,
                                     state='incomplete', run_ref=executed_ref)
    feedback = {'tool': action, 'ok': True, 'run_ref': executed_ref, **facts}
    return ScientificToolOutcome(
        events=({'type': 'tool_result', 'call': call_count(), **feedback},),
        message=_user(_json(feedback)), checkpoint=True, run_ref=executed_ref)


def execute_assess_parameter(args, *, store, source_refs, parameter_requirements,
                             invoke, call_count) -> ScientificToolOutcome:
    action = 'assess_parameter'
    try:
        from .parameter_applicability import assess
        if set(args) != {'target', 'source_ref', 'page', 'start', 'end'} or args['target'] not in parameter_requirements:
            raise ValueError('assess_parameter requires a frozen target and source_ref/page/start/end')
        out = invoke(action, lambda: assess(store, source_refs, args['target'],
                     parameter_requirements[args['target']], args['source_ref'],
                     args['page'], args['start'], args['end']),
                     details={'args': args},
                     result_refs=lambda r: [r['assessment_ref']])
        feedback = {'tool': action, 'ok': True, **out}
        event = {'type': 'tool_result', 'call': call_count(), 'tool': action,
                 'status': out['status'], 'target': out['target'],
                 'artifact_refs': [out['assessment_ref']]}
    except (ValueError, TypeError) as exc:
        feedback = {'tool': action, 'ok': False, 'error': str(exc)}
        event = {'type': 'tool_error', 'call': call_count(), **feedback}
    return ScientificToolOutcome(events=(event,), message=_user(_json(feedback)),
                                 checkpoint=True)
