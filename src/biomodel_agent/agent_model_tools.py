"""Execution handlers for the agent's model submission and scientific run.

The agent loop keeps ownership of the pre-submit run budget guard, the
immediate artifact/model invalidation timing, repair accounting, failure-policy
routing, event/message persistence, checkpoints, state assignment and
termination.  Each handler here performs exactly one action through the
caller's ``invoke`` journal callable and returns the events, feedback and
references the loop must persist or adopt.

``call_count`` is a live callable, not a snapshot: the journal wrapper
synchronizes the agent counters in its ``finally`` block, so events carry the
post-invocation count.  Collaborator modules are imported inside the handlers
as module objects and resolved at call time, so existing monkeypatching keeps
working and no early function copies are bound.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .domain.serialization import to_dict

__all__ = ('ModelSubmission', 'RunExecution', 'execute_model_submission', 'execute_run')


@dataclass(frozen=True)
class ModelSubmission:
    """Result of one submit_draft/submit_plan execution.

    ``draft_ref`` already applies the original fallback semantics: submit_plan
    replaces the current draft reference with the result's own (possibly
    ``None``), while submit_draft keeps the previous draft when the result has
    none.  ``artifact``/``model_ref`` are set only for a ready state and the
    loop adopts them only there; ``awaiting_event`` is prebuilt for the loop
    to append when it reaches the awaiting_input break after failure-policy
    routing.
    """

    result: dict
    event: dict
    draft_ref: str | None
    state: str
    artifact: Any | None = None
    model_ref: str | None = None
    message: dict | None = None
    awaiting_event: dict | None = None


@dataclass(frozen=True)
class RunExecution:
    """Result of one journaled scientific execution.

    ``run_ref`` is formed as soon as ``invoke`` returns: a later failure while
    reading the stored result keeps the completed run reference and its
    consumed run budget in the outcome for the loop to adopt.
    """

    events: tuple[dict, ...]
    message: dict | None
    run_ref: str | None
    stop: bool = False
    state: str | None = None


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def execute_model_submission(action, args, *, store, source_refs, time_unit, mode,
                             allowed_mechanisms, parameter_requirements, task_ref,
                             events, draft_ref, invoke, call_count) -> ModelSubmission:
    """Run one submit_draft/submit_plan through the journal and build its feedback.

    The pre-submit run budget guard and the immediate artifact/model
    invalidation stay in the agent loop.  This handler owns the journaled
    submission, the post-return time-unit check (which can still turn a ready
    result invalid, leaving no executable artifact) and the exact tool_result
    event and per-state message construction.
    """
    from . import agent_tools, diagnostics, native_modeling, time_units
    if action == 'submit_plan':
        parent_plan_ref = native_modeling.latest_ref(events)
        result = invoke('submit_plan', lambda: native_modeling.submit(store, args, source_refs,
            task_ref=task_ref, parent_ref=parent_plan_ref),
            details=dict(args=args, source_refs=list(source_refs), parent_modeling_plan_ref=parent_plan_ref),
            summarize=lambda r: {k: v for k, v in r.items() if k != 'artifact'}, result_refs=native_modeling.refs)
    else:
        result = invoke('submit_draft', lambda: agent_tools.submit_draft(store, args, source_refs, mode, tuple(allowed_mechanisms),
                    **({'parameter_requirements': parameter_requirements} if parameter_requirements else {})),
                 details={'args': args},
                 summarize=lambda r: {k: v for k, v in r.items() if k != 'artifact'},
                 result_refs=lambda r: [ref for ref in (r.get('draft_ref'), r.get('model_ref'),
                                                      r.get('hypothesis_ref'), r.get('source_review_ref'), *r.get('assessment_refs', [])) if ref])
    if result['state'] == 'ready':
        # The journal already returned the submission; the time-unit check can
        # still reject it and must leave no artifact to execute.
        clock = time_units.check_experiment_time(result['artifact'].sbml, time_unit)
        result['checks'].append(to_dict(clock))
        if clock.status == 'fail':
            result.update(state='invalid', artifact=None, model_ref=None, problems=[clock.message])
    # submit_plan replaces the draft reference; submit_draft falls back to the
    # current draft when the result carries none.
    resolved_draft_ref = result.get('draft_ref') if action == 'submit_plan' else result.get("draft_ref") or draft_ref
    validation_feedback = diagnostics.draft_feedback(result)
    event = {"type": "tool_result", "call": call_count(), "tool": action,
             "validation_feedback": validation_feedback,
             "parameter_diagnostics": result.get('parameter_diagnostics', []),
             "state": result["state"], "problems": list(result["problems"]),
             "citation_diagnostics": result.get("citation_diagnostics", []),
             "binding_diagnostics": result.get("binding_diagnostics", []),
             "checks": result["checks"], "source_review": result.get("source_review"),
             **({k: result.get(k) for k in ('modeling_plan_ref', 'compilation_ref', 'submission_ref', 'decision_feedback')} if action == 'submit_plan' else {}),
             "artifact_refs": native_modeling.refs(result) if action == 'submit_plan' else [result.get(k) for k in ("draft_ref","model_ref","hypothesis_ref","source_review_ref")]}
    if result["state"] == "ready":
        artifact = result["artifact"]
        summary = {"id": artifact.id, "format": artifact.format, "kind": artifact.kind,
                   "sha256": artifact.sha256,
                   "element_map_targets": sorted(artifact.element_map.keys()),
                   "checks": result["checks"], "model_ref": result["model_ref"],
                   **({'decision_feedback': result['decision_feedback']} if action == 'submit_plan' else {}),
                   "note": "artifact data held internally; only this summary is shown"}
        return ModelSubmission(result=result, event=event, draft_ref=resolved_draft_ref,
                               state="ready", artifact=artifact, model_ref=result["model_ref"],
                               message=_user(_json({"tool": action, "ok": True, **summary})))
    if result["state"] == "invalid":
        return ModelSubmission(result=result, event=event, draft_ref=resolved_draft_ref,
                               state="invalid",
                               message=_user(_json({"tool": action, "ok": False, "state": "invalid",
                                                    "validation_feedback": validation_feedback,
                                                    **({'decision_feedback': result['decision_feedback']} if action == 'submit_plan' else {}),
                                                    "problems": list(result["problems"]),
                                                    "citation_diagnostics": result.get("citation_diagnostics", []),
                                                    "binding_diagnostics": result.get("binding_diagnostics", [])})))
    awaiting_event = None
    if result["state"] == "awaiting_input":
        awaiting_event = {"type":"awaiting_input","call":call_count(),
                          "origin":"validation",
                          "question":"Please resolve: " + "; ".join(result["problems"]),
                          "problems": list(result["problems"]),
                          "source_review_ref": result.get("source_review_ref")}
    # An ordinary submit_draft stays without a per-state message; submit_plan
    # always reports its decision feedback.
    message = None
    if action == 'submit_plan':
        message = _user(_json(dict(tool=action, ok=False, state=result['state'],
            decision_feedback=result['decision_feedback'], validation_feedback=validation_feedback,
            problems=list(result['problems']))))
    return ModelSubmission(result=result, event=event, draft_ref=resolved_draft_ref,
                           state=result["state"], message=message,
                           awaiting_event=awaiting_event)


def execute_run(*, store, artifact, experiment, observations, solver, reference, scales,
                mode, task_ref, decision_options, events, source_refs, model_ref,
                draft_ref, invoke, call_count, run_facts) -> RunExecution:
    """Run the single journaled scientific execution and build its feedback.

    The args and prior-run guards stay in the agent loop.  The decision gate
    runs before the journal reserves the run budget; ``nonnegative`` is read
    from the current draft; the journal guarantees the single execution.
    ``run_ref`` is kept even when reading the stored result afterwards fails,
    and the fact projection stays outside the error boundary exactly as
    before.
    """
    from . import application, native_modeling
    run_ref = None
    try:
        if decision_options:
            artifact = native_modeling.active_model(store, task_ref, events, source_refs, model_ref, draft_ref)
            from .modeling_workflow import _config as check_decision_execution
            submission = next(e for e in reversed(events) if e.get('tool') == 'submit_plan')
            check_decision_execution(store, submission['compilation_ref'], dict(
                experiment=to_dict(experiment), solver=to_dict(solver),
                observations=[to_dict(o) for o in observations],
                reference=to_dict(reference) if reference is not None else None, scales=scales))
        draft_data = store.get(draft_ref)['payload']
        nonnegative = tuple(s['id'] for s in draft_data['states'] if s.get('nonnegative'))
        # The journal's scientific-execution reservation is persisted
        # before the evaluation; let it account the run budget.
        run_ref = invoke('run', lambda: application.run_reproduction(store, artifact, experiment, observations,
                                                                     solver, reference, scales, strict=(mode == "reproduction"),
                                                                     nonnegative=nonnegative),
                         details={'model_ref': model_ref, 'draft_ref': draft_ref},
                         summarize=lambda r: {'result_ref': r},
                         result_refs=lambda r: [r])
        run = store.get(run_ref)["payload"]
    except Exception as exc:
        # A failure after the journal returned keeps the completed run
        # reference and the consumed run budget; only the feedback changes.
        return RunExecution(events=({"type": "tool_error", "call": call_count(), "tool": "run",
                                     "error": f"{type(exc).__name__}: {exc}"},),
                            message=_user(_json({"tool": "run", "ok": False,
                                                 "error": "run tool failed"})),
                            run_ref=run_ref, stop=True, state="incomplete")
    out = {"tool": "run", "ok": True, "run_ref": run_ref, **run_facts(run)}
    return RunExecution(events=({"type": "tool_result", "call": call_count(),
                                 **{k: v for k, v in out.items()}},),
                        message=_user(_json(out)), run_ref=run_ref)
