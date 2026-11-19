"""Offline audit/regression checks of three findings; never calls a remote service.

Run from the repository root with the project's Python environment. These
Default checks repaired behavior; --expect baseline checks the original bugs
when this script is run against the original source. Formal regression tests
also cover public CLI and workbench entrypoints.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from biomodel_agent import research_pipeline as batch
from biomodel_agent import research_projects as projects
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider, ModelResponseError
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def respond(self, messages):
        self.calls += 1
        return deepcopy(self.answer)


def http_provider(answer):
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(answer)}}],
            "usage": {"total_tokens": 10},
        })

    provider = ChatProvider("https://audit.invalid/v1", "offline-audit",
                            api_key_env="BIOMODEL_AUDIT_DUMMY_KEY",
                            transport=httpx.MockTransport(transport))
    return provider, requests


def fresh_project(path):
    store = ArtifactStore(path)
    ref = projects.create(store, {"scenario": "Offline protocol control.",
                                  "policy": {"planner_max_calls": 1}})
    return store, ref


def project_response(root, baseline=False):
    action = {"action": "scenario_source", "args": {}}
    store, ref = fresh_project(root / "project-http")
    provider, requests = http_provider(action)
    supervisor.run(store, provider, ref)
    view = projects.inspect(store, ref)
    invalid = [p for _, p in projects.rows(store, projects.STEP, ref)
               if p["operation"] == "invalid_response"]
    control, control_ref = fresh_project(root / "project-scripted")
    supervisor.run(control, Scripted(action), control_ref)
    control_view = projects.inspect(control, control_ref)
    assert len(requests) == 1 and len(invalid) == (1 if baseline else 0)
    assert len(view["source_refs"]) == (0 if baseline else 1) and len(control_view["source_refs"]) == 1
    return {"id": "BUG-01", "status": "reproduced" if baseline else "fixed",
            "http_requests_mocked": len(requests),
            "adapter_result_keys": sorted(provider._parse(json.dumps(action))),
            "invalid_responses": len(invalid), "sources_created": len(view["source_refs"]),
            "scripted_control_sources": len(control_view["source_refs"]),
            "final_state": view["state"]}


def lessons_response(root, baseline=False):
    store = ArtifactStore(root / "batch")
    ref = batch.create(store, {"name": "Offline audit", "lessons_max_calls": 1,
        "cases": [{"id": "control", "paper_id": "synthetic:audit",
            "family": "audit", "material_kind": "synthetic_control",
            "project": {"scenario": "No source evidence.", "mode": "reproduction",
                        "policy": {"planner_max_calls": 0}}}]})
    batch.run(store, ref, lambda case: None)
    answer = {"lessons": [{"statement": "Check source availability.", "case_ids": ["control"],
                           "conditions": "Missing sources.", "validation_plan": "Use separate development controls."}]}
    provider, requests = http_provider(answer if baseline else dict(action='propose_lessons',args=answer))
    if not baseline:
        refs=batch.lessons(store,ref,provider)
        assert len(refs)==1 and not store.get(refs[0])['payload']['active']
        assert batch.lessons(store,ref,None)==refs and len(requests)==1
        return dict(id='BUG-02',status='fixed',http_requests_mocked=len(requests),
                    saved_lesson_responses=len(store.list(batch.LESSON_RESPONSE)),candidates=len(refs))
    try:
        batch.lessons(store, ref, provider)
    except ValueError as exc:
        error = str(exc)
    else:
        raise AssertionError("Expected the current adapter/schema mismatch")
    try:
        provider._parse(json.dumps(answer))
    except ModelResponseError as exc:
        parser_error = type(exc).__name__ + ": " + str(exc)
    else:
        raise AssertionError("Expected the current lesson-envelope rejection")
    try:
        batch.lessons(store, ref, provider)
    except ValueError as exc:
        retry_error = str(exc)
    else:
        raise AssertionError("Unknown lesson outcome must not be resent")
    assert len(requests) == 1 and len(store.list(batch.LESSON_ATTEMPT)) == 1
    assert not store.list(batch.LESSON_RESPONSE) and not store.list(batch.EXPERIENCE)
    return {"id": "BUG-02", "status": "reproduced", "http_requests_mocked": len(requests),
            "parser_error": parser_error, "pipeline_error": error,
            "repeat_call_error": retry_error, "saved_lesson_responses": 0, "candidates": 0}


def render_button(view):
    # Executes the actual render function in a minimal DOM stub, not a browser.
    script = r'''
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')), elements={};
class Element {constructor(){this.children=[];} append(...x){this.children.push(...x);}
 replaceChildren(...x){this.children=x;} setAttribute(){} addEventListener(){}}
const sandbox={Node:Element, location:{host:'audit',hash:'#'+input.view.project_ref},
 localStorage:{getItem:()=>null,setItem:()=>{}},
 document:{querySelector:()=>({content:'dummy'}),getElementById:id=>elements[id]??=(new Element()),
 createElement:()=>new Element(),createTextNode:x=>x}};
vm.createContext(sandbox);
const source=fs.readFileSync(input.source,'utf8');
vm.runInContext(source.slice(0,source.indexOf('$("new-project").onclick')),sandbox);
sandbox.data={...input.view,questions:[],iteration_facts:{},assessments:[],usage:{}};
vm.runInContext('render(data)',sandbox);
process.stdout.write(JSON.stringify({disabled:elements['advance-project'].disabled,
 notice:elements['project-status-note'].textContent}));
'''
    result = subprocess.run(["node", "-e", script], input=json.dumps({"view": view,
        "source": str(ROOT / "src/biomodel_agent/workbench_assets/projects.js")}),
        text=True, capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


def pending_response(root, baseline=False):
    store, ref = fresh_project(root / "pending")
    provider = Scripted({"action": "scenario_source", "args": {}})
    original = projects._operate_owned

    def interrupt_after_return(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == "respond":
            raise KeyboardInterrupt("Injected stop after saved response, before dispatch")
        return result

    with patch.object(projects, "_operate_owned", interrupt_after_return):
        try:
            supervisor.run(store, provider, ref)
        except KeyboardInterrupt:
            pass
    before = projects.inspect(store, ref)
    assert before["state"] == "ready" and supervisor._pending_response(store, ref)
    ui = render_button(before)
    assert ui["disabled"] is baseline
    supervisor.run(store, None, ref)
    after = projects.inspect(store, ref)
    assert len(after["source_refs"]) == 1 and provider.calls == 1
    return {"id": "BUG-03", "status": "reproduced" if baseline else "fixed", "state_before": before["state"],
            "planner_calls": before["budget"]["planner_calls"],
            "planner_limit": before["budget"]["planner_max_calls"],
            "actual_render_function": ui, "backend_resume_created_source": True,
            "extra_provider_calls": 0, "validation": "Node VM DOM stub; not real browser"}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect',choices=('baseline','fixed'),default='fixed')
    args=parser.parse_args()
    baseline=args.expect=='baseline'
    with tempfile.TemporaryDirectory(prefix="biomodel-protocol-audit-") as directory:
        with patch.dict(os.environ, {"BIOMODEL_AUDIT_DUMMY_KEY": "offline-dummy-not-a-secret"}):
            root = Path(directory)
            results = [project_response(root,baseline), lessons_response(root,baseline), pending_response(root,baseline)]
    print(json.dumps({"audited_baseline_commit": "593affca82ee2b16afd798752d0623536fd4434c",
                      "expect":args.expect,"implementation_sha256":projects.recovery.implementation_fingerprint(),
                      "network_requests": 0, "diagnostic_modifies_product_code": False,
                      "findings": results}, ensure_ascii=False, indent=2))
