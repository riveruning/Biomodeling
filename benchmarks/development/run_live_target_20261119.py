"""Bounded live research-project run with a pre-frozen budget.

Every invocation:
  * refuses to touch a non-empty output directory (old failures are preserved),
  * writes ``run-config.json`` (model, reasoning, token caps, call budget,
    timeout, source SHA256) *before* any provider request,
  * writes an exclusive ``live-started.json`` marker so an interrupted POST is
    never silently replayed,
  * stores provider usage exactly as reported (missing stays missing).

This is a development diagnostic: a solver/planner success is not a paper
reproduction claim.
"""
import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from biomodel_agent import evidence, research_projects as projects
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore

ALLOWED_FAMILIES = ("deepseek", "glm")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, nargs="+", type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-planner-calls", type=int, default=10)
    parser.add_argument("--max-native-calls", type=int, default=8)
    parser.add_argument("--max-iterations", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--timeout-s", type=float, default=2400)
    parser.add_argument("--context-max-chars", type=int, default=60000)
    parser.add_argument("--retained", nargs="*", type=Path, default=[])
    parser.add_argument("--expect-sha256", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--base-url", default="https://api.llm.ustc.edu.cn/v1")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in args.output.iterdir()}
    # A frozen configuration written by an earlier dry run is idempotent; any
    # real evidence (source, marker, result) makes this a different attempt.
    if existing - {"run-config.json"}:
        raise SystemExit("output directory already holds evidence; refusing to overwrite")
    previous = None
    if (args.output / "run-config.json").is_file():
        previous = json.loads((args.output / "run-config.json").read_text())
    source_shas = {p.name: _sha256(p) for p in args.source}
    if args.expect_sha256 and args.expect_sha256 not in source_shas.values():
        raise SystemExit("unexpected source bytes: %s" % source_shas)

    runtime = json.loads(Path("config/development-runtime.json").read_text())
    model = args.model or runtime["model"]
    if not model.startswith(ALLOWED_FAMILIES):
        raise SystemExit("disallowed model family: %s" % model)

    scenario = args.scenario.read_text(encoding="utf-8").strip()
    config = dict(
        schema_version=1,
        model=model,
        provider_api=runtime["provider_api"],
        reasoning_effort="max",
        stream=args.stream,
        max_tokens=args.max_tokens,
        timeout_s=args.timeout_s,
        budget=dict(planner_max_calls=args.max_planner_calls,
                    context_max_chars=args.context_max_chars,
                    max_native_calls=args.max_native_calls,
                    max_iterations=args.max_iterations),
        sources=[dict(name=p.name, sha256=source_shas[p.name]) for p in args.source],
        retained=[dict(name=p.name, sha256=_sha256(p)) for p in args.retained],
        scenario_sha256=hashlib.sha256(scenario.encode()).hexdigest(),
        frozen_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        note="Call counts are never reset and unknown POSTs are never replayed.",
    )
    if previous is not None:
        keys = ("model", "reasoning_effort", "max_tokens", "timeout_s", "budget",
                "sources", "retained", "scenario_sha256")
        if any(previous.get(key) != config.get(key) for key in keys):
            raise SystemExit("frozen configuration differs from the earlier plan; "
                             "start a new output directory instead of reusing it")
    (args.output / "run-config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if not args.live:
        print(json.dumps(dict(status="config_frozen_no_model_calls", **config["budget"])))
        return

    if not os.environ.get("BIOMODEL_API_KEY"):
        raise SystemExit("BIOMODEL_API_KEY is not configured; no calls made")

    store = ArtifactStore(args.output / "store")
    sources, summaries = [], []
    for path in args.source:
        ref = evidence.ingest_source(store, path)
        sources.append(ref)
        payload = store.get(ref)["payload"]
        summaries.append(dict(
            source_ref=ref, filename=path.name, parser=payload["parser"],
            parser_version=payload["parser_version"], sha256=source_shas[path.name],
            warnings=payload.get("warnings", [])[:20],
            pages=len(payload["pages"]),
            characters=sum(len(p["text"]) for p in payload["pages"]),
            formula_source_count=payload.get("formula_source_count"),
            projected_formulas=sum(1 for f in payload.get("formula_sources", [])
                                   if f["source_spans"]),
        ))
    (args.output / "source.json").write_text(json.dumps(
        dict(sources=summaries,
             retained=[dict(name=p.name, sha256=_sha256(p),
                            ingested=False, note="original material retained, not parsed")
                       for p in args.retained]), indent=2) + "\n", encoding="utf-8")

    project = projects.create(store, dict(
        mode="reproduction", source_refs=sources, scenario=scenario,
        policy=dict(planner_max_calls=args.max_planner_calls,
                    max_literature_requests=0, max_transcription_calls=0,
                    max_iterations=args.max_iterations,
                    max_native_calls=args.max_native_calls,
                    native_calls_per_iteration=args.max_native_calls,
                    context_max_chars=args.context_max_chars,
                    total_timeout_s=args.timeout_s)))
    (args.output / "project.json").write_text(
        json.dumps(dict(project_ref=project), indent=2) + "\n", encoding="utf-8")

    marker = args.output / "live-started.json"
    with marker.open("x") as handle:
        json.dump(dict(model=model, reasoning_effort="max",
                       max_tokens=args.max_tokens, project_ref=project,
                       started_at=time.time(), sources=source_shas), handle)

    provider = ChatProvider(args.base_url, model, timeout_s=args.timeout_s,
                            max_tokens=args.max_tokens, json_mode=True,
                            reasoning_effort="max", stream=args.stream)
    error = None
    started = time.time()
    try:
        snapshot = supervisor.run(store, provider, project)
    except Exception as exc:  # preserve the failure, never retry automatically
        error = type(exc).__name__ + ": " + str(exc)[:500]
        snapshot = projects.snapshot(store, project)
    attempts, returns, unknown = projects.history(store, project)
    result = dict(
        status="development_diagnostic_not_reproduction_success",
        error=error,
        elapsed_s=round(time.time() - started, 1),
        project_ref=project, snapshot_ref=snapshot,
        view=projects.inspect(store, project),
        usage=supervisor.usage(store, project),
        unknown_attempts=unknown,
        operations=[dict(operation=a.get("operation"),
                         state=(returns[r][1]["state"] if r in returns else "unknown"))
                    for r, a in attempts],
    )
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status=result["status"],
                          state=result["view"]["state"],
                          planner_calls=result["view"]["budget"]["planner_calls"],
                          error=error, usage=result["usage"])))


if __name__ == "__main__":
    main()
