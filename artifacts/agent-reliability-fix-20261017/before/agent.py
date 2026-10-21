"""Budgeted workflow agent: fixed tools, bounded calls, evidence-only results."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from . import agent_tools, application
from .domain.contracts import (
    ExperimentSpec, FrozenMap, ModelArtifact, ObservationSpec, SolverConfig,
    SimulationResult,
)
from .domain.serialization import from_dict, to_dict
from .llm import ProviderError, build_system_prompt
from .storage import REF_RE, ArtifactError

MODES = ("reproduction", "hypothesis")
ACTIONS = ("inspect_source", "submit_draft", "run", "finish", "ask_researcher")
READ_LIMIT = 8000
LIMITATIONS = (
    "source quote existence does not prove semantic support",
    "no arbitrary paper extraction guarantee",
)


class AgentRun:
    def __init__(self, ref: str) -> None:
        self.ref = ref

    def __repr__(self) -> str:
        return f"AgentRun({self.ref!r})"


def _is_int(value: Any) -> bool:
    return type(value) is int


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _experiment_brief(experiment: ExperimentSpec) -> dict:
    return {
        "times": list(experiment.times),
        "initial_overrides": dict(experiment.initial_overrides),
        "parameter_overrides": dict(experiment.parameter_overrides),
        "segments": len(experiment.segments),
    }


def _source_text(store,source_ref,page):
    for entry in store.get(source_ref)['payload']['pages']:
        if entry['number'] == page:
            return entry['text']
    return None


def _run_facts(run: dict) -> dict:
    sim = run.get("simulation") or {}
    numeric = (run.get("numeric_validation") or {}).get("checks") or []
    source = run.get("source_validation") or {}
    return {
        "execution_status": run.get("status"),
        "numeric_checks": [{"code": c.get("code"), "status": c.get("status"),
                            "severity": c.get("severity")} for c in numeric],
        "numeric_all_pass": all(c.get("status") in ("pass", "not_applicable") for c in numeric),
        "comparison": (run.get("comparison") or {}).get("status"),
        "source_validation": source.get("status"),
        "semantic_support": source.get("semantic_support"),
        "scientific_status": run.get("scientific_status"),
        "note": "pass/fail facts come from the run tool, never from the model",
    }


def _checkpoint(store: Any, state: str, call: int, repairs: int, messages: list[dict],
                model_ref: str | None, run_ref: str | None,
                source_refs: list[str], draft_ref: str | None) -> str:
    return store.put("agent_checkpoint", {
        "state": state, "call_count": call, "repair_count": repairs,
        "model_ref": model_ref, "run_ref": run_ref,
        "refs": list(source_refs) + [r for r in (draft_ref, model_ref, run_ref) if r],
        "messages": messages,
    }, refs=tuple(r for r in source_refs + [r for r in (draft_ref, model_ref, run_ref) if r]))


def _final(store: Any, state: str, mode: str, source_refs: list[str], model_ref: str | None,
           run_ref: str | None, events: list[dict], calls: int, repairs: int,
           checkpoints: list[str], draft_ref: str | None) -> str:
    refs = [r for r in source_refs + [r for r in (draft_ref, model_ref, run_ref, *checkpoints) if r]]
    refs.extend(r for e in events for r in e.get('artifact_refs',[]) if r)
    ref = store.put("agent_run", {
        "state": state, "mode": mode, "source_refs": list(source_refs),
        "model_ref": model_ref, "run_ref": run_ref, "events": events,
        "call_count": calls, "repair_count": repairs, "limitations": list(LIMITATIONS),
    }, refs=tuple(dict.fromkeys(refs)))
    return ref


def run_agent(store: Any, provider: Any, source_refs: list[str], experiment: ExperimentSpec,
              observations: tuple = (), solver: SolverConfig | None = None,
              mode: str = "reproduction", max_calls: int = 8, max_repairs: int = 2,
              allowed_mechanisms: tuple = (), reference: SimulationResult | None = None,
              scales: dict | None = None) -> str:
    if type(max_calls) is not int or not 1 <= max_calls <= 50 or type(max_repairs) is not int or not 0 <= max_repairs <= 5:
        raise ValueError('invalid agent budgets')
    if mode not in MODES:
        return _final(store, "failed", str(mode), source_refs, None, None, [], 0, 0, [], None)
    if not all(isinstance(r, str) and REF_RE.fullmatch(r) for r in source_refs):
        raise ValueError("invalid or unavailable source refs")
    try:
        for ref in source_refs:
            if store.get(ref)["kind"] != "source":
                raise ArtifactError(f"source {ref} kind is not source")
    except Exception:
        raise ValueError("invalid or unavailable source refs")

    materials = {r: {"pages": [{"page":p["number"],"chars":len(p["text"])} for p in store.get(r)["payload"]["pages"]]} for r in source_refs}
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt()},
        _user(_json({"materials": materials, "task": {"mode": mode,
                                                       "experiment": _experiment_brief(experiment), "allowed_mechanisms":list(allowed_mechanisms)}})),
    ]
    model_ref = run_ref = draft_ref = None
    artifact = None
    events: list[dict] = []
    checkpoints: list[str] = []
    calls = repairs = run_count = 0
    state = "running"
    questions = 0
    while calls < max_calls:
        calls += 1
        try:
            response = provider.respond(messages)
        except ProviderError as exc:
            events.append({"type": "provider_error", "call": calls, 'error':str(exc),
                           'usage':getattr(provider,'last_usage',{})})
            checkpoints.append(_checkpoint(store, "failed", calls, repairs, messages,
                                           model_ref, run_ref, source_refs, draft_ref))
            return _final(store, "failed", mode, source_refs, model_ref, run_ref, events,
                          calls, repairs, checkpoints, draft_ref)
        calls_usage = {k: v for k, v in (getattr(provider, "last_usage", {}) or {}).items()
                       if _is_int(v)}
        events.append({"type": "llm_call", "call": calls, "action": response.get("action"),
                       "requests": [dict(m) for m in messages], "response": response, "usage": calls_usage})
        messages.append(_assistant(_json({"action": response.get("action"),
                                          "args": response.get("args") or {}})))
        action, args = response.get("action"), response.get("args")
        if not isinstance(action, str) or action not in ACTIONS or not isinstance(args, dict):
            events.append({"type": "tool_error", "call": calls, "tool": str(action),
                           "error": "action or args invalid"})
            messages.append(_user(_json({"tool": str(action), "ok": False,
                                         "error": "action or args invalid"})))
            checkpoints.append(_checkpoint(store, "failed", calls, repairs, messages,
                                           model_ref, run_ref, source_refs, draft_ref))
            return _final(store, "failed", mode, source_refs, model_ref, run_ref, events,
                          calls, repairs, checkpoints, draft_ref)

        if action == "inspect_source":
            if set(args) != {"source_ref", "page", "start", "end"}:
                events.append({"type": "tool_error", "call": calls, "tool": action,
                               "error": "inspect_source args must be exactly source_ref,page,start,end"})
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "error": "inspect_source args must be exactly source_ref,page,start,end"})))
                state = "failed"
                break
            ref, page, start, end = args["source_ref"], args["page"], args["start"], args["end"]
            if ref not in source_refs or not _is_int(page) or not _is_int(start) or not _is_int(end) or start < 0 or end <= start or end - start > READ_LIMIT:
                events.append({"type": "tool_error", "call": calls, "tool": action,
                               "error": "source/page/start/end outside task bounds"})
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "error": "source/page/start/end outside task bounds"})))
                state = "failed"
                break
            text = _source_text(store, ref, page)
            if text is None or page < 1 or end > len(text):
                error = {'type':'tool_error','tool':action,'error':'invalid source span',
                         'available_pages': [{'page':p['number'],'chars':len(p['text'])}
                                             for p in store.get(ref)['payload']['pages']]}
                events.append(error)
                messages.append(_user(_json(error)))
                # A bounded technical retry can correct coordinates; scope remains fixed.
                continue
            excerpt = text[start:end]
            out = {"tool": action, "ok": bool(excerpt), "source_ref": ref, "page": page,
                   "start": start, "end": end, "length": len(excerpt), "text": excerpt}
            events.append({"type": "tool_result", "call": calls, **{k: v for k, v in out.items() if k != "text"}})
            messages.append(_user(_json(out)))
            continue

        if action == "submit_draft":
            if run_count:
                state = 'incomplete'
                events.append({'type':'tool_error','error':'cannot revise a model after evaluating it in this frozen task'})
                break
            result = agent_tools.submit_draft(store, args, source_refs, mode, tuple(allowed_mechanisms))
            events.append({"type": "tool_result", "call": calls, "tool": action,
                           "state": result["state"], "problems": list(result["problems"]),
                           "checks": result["checks"], "artifact_refs":[result.get(k) for k in ("draft_ref","model_ref","hypothesis_ref")]})
            if result["state"] == "ready":
                artifact = result["artifact"]
                model_ref, draft_ref = result["model_ref"], result["draft_ref"]
                summary = {"id": artifact.id, "format": artifact.format, "kind": artifact.kind,
                           "sha256": artifact.sha256,
                           "element_map_targets": sorted(artifact.element_map.keys()),
                           "checks": result["checks"], "model_ref": model_ref,
                           "note": "artifact data held internally; only this summary is shown"}
                messages.append(_user(_json({"tool": action, "ok": True, **summary})))
                continue
            if result["state"] == "invalid":
                repairs += 1
                messages.append(_user(_json({"tool": action, "ok": False, "state": "invalid",
                                             "problems": list(result["problems"])})))
                if repairs > max_repairs:
                    state = "incomplete"
                    break
                continue
            state = result["state"]
            break

        if action == "run":
            if args:
                events.append({"type": "tool_error", "call": calls, "tool": action,
                               "error": "run takes no arguments"})
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "error": "run takes no arguments"})))
                state = "failed"
                break
            if artifact is None or run_count:
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "state": "incomplete",
                                             "error": "run requires exactly one ready draft and cannot repeat after a prior run or draft change"})))
                state = "incomplete"
                break
            run_count += 1
            try:
                draft_data = store.get(draft_ref)['payload']
                nonnegative = tuple(s['id'] for s in draft_data['states'] if s.get('nonnegative'))
                run_ref = application.run_reproduction(store, artifact, experiment, observations,
                                                       solver, reference, scales, strict=(mode == "reproduction"),
                                                       nonnegative=nonnegative)
                run = store.get(run_ref)["payload"]
            except Exception as exc:
                events.append({"type": "tool_error", "call": calls, "tool": action,
                               "error": f"{type(exc).__name__}: {exc}"})
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "error": "run tool failed"})))
                state = "incomplete"
                break
            out = {"tool": action, "ok": True, "run_ref": run_ref, **_run_facts(run)}
            events.append({"type": "tool_result", "call": calls, **{k: v for k, v in out.items()}})
            messages.append(_user(_json(out)))
            continue

        if action == "finish":
            if args:
                state = "failed"
                break
            finished_run = store.get(run_ref)["payload"] if run_ref is not None else {}
            state = "completed" if (finished_run.get("status") == "succeeded"
                                      and finished_run.get("scientific_status") != "failed") else "incomplete"
            break

        if action == "ask_researcher":
            if set(args) != {"question"} or not isinstance(args["question"], str):
                events.append({"type": "tool_error", "call": calls, "tool": action,
                               "error": "ask_researcher requires only question:str"})
                messages.append(_user(_json({"tool": action, "ok": False,
                                             "error": "ask_researcher requires only question:str"})))
                state = "failed"
                break
            questions += 1
            events.append({"type": "awaiting_input", "call": calls,
                           "question": args["question"]})
            state = "awaiting_input"
            break

        events.append({"type": "tool_error", "call": calls, "tool": action,
                       "error": "action outside allowed tool set"})
        messages.append(_user(_json({"tool": action, "ok": False,
                                     "error": "action outside allowed tool set"})))
        state = "failed"
        break
    if state == "running":
        state = "incomplete"
    checkpoints.append(_checkpoint(store, state, calls, repairs, messages,
                                   model_ref, run_ref, source_refs, draft_ref))
    return _final(store, state, mode, source_refs, model_ref, run_ref, events,
                  calls, repairs, checkpoints, draft_ref)
