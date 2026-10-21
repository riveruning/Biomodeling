"""Thin CLI over the frozen domain interfaces; no new computation logic."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _load_json(path: str, what: str):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what}: not finite/JSON-safe: {exc}") from exc
    return data


def _cmd_validate(args) -> int:
    from .domain.contracts import ModelArtifact
    from .domain.serialization import from_dict
    from . import modeling as validation
    try:
        artifact = ModelArtifact("cli_input", _read_text(args.sbml))
        report = validation.validate_artifact(artifact)
    except Exception as exc:
        return _fail(f"validate failed: {exc}")
    for c in report.checks:
        print(f"{c.severity}\t{c.status}\t{c.code}\t{c.message}")
    if not report.can_execute:
        print("RESULT: invalid (blocking failures)")
        return 1
    if args.strict and not report.fully_passed:
        print("RESULT: not fully passed (strict)")
        return 2
    print("RESULT: valid")
    return 0


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _cmd_run(args) -> int:
    from .storage import ArtifactStore
    from .application import replay_run, run_reproduction
    from .domain.contracts import ExperimentSpec, ModelArtifact
    from .domain.serialization import from_dict
    try:
        store = ArtifactStore(args.store)
        experiment = from_dict(ExperimentSpec, _load_json(args.experiment, "experiment JSON"))
        observations = tuple()
        if args.observations:
            from .domain.contracts import ObservationSpec
            observations = tuple(from_dict(ObservationSpec,o) for o in _load_json(args.observations, "observations JSON"))
        reference = None
        if args.reference:
            ref_data = _load_json(args.reference, "reference JSON")
            reference = _build_reference(ref_data)
        scales = _load_json(args.scales, "scales JSON") if args.scales else (ref_data.get("scales") if args.reference else None)
        ref = run_reproduction(
            store,
            ModelArtifact("cli_input", _read_text(args.sbml)),
            experiment,
            observations=observations,
            reference=reference,
            scales=scales,
            threshold=ref_data.get("threshold",1e-3) if args.reference else 1e-3,
            strict=args.strict,
            use_cache=not args.no_cache,
        )
    except Exception as exc:
        return _fail(f"run failed: {exc}")
    code = _print_run_result(ref, store, strict=args.strict)
    return code


def _build_reference(ref_data):
    from .domain.contracts import SimulationResult
    from .domain.serialization import from_dict, SchemaError
    try:
        return from_dict(SimulationResult, ref_data)
    except SchemaError:
        # curated reference: {times, values, scales, threshold, ...};
        # scales/threshold are frozen alongside the reference by the caller,
        # the constructed result carries no fabricated source/pass fields.
        times = ref_data.get("times")
        values = ref_data.get("values")
        if times is None or values is None:
            raise ValueError("reference must be a full SimulationResult or curated {times, values}")
        return SimulationResult(status="succeeded", times=tuple(times), values=dict(values),
                                time_unit=ref_data.get('time_unit', 'unknown'))


def _print_run_result(ref, store, strict: bool) -> int:
    record = store.get(ref)
    payload = record.get("payload", {}) if isinstance(record, dict) else {}
    status = payload.get("status")
    scientific = payload.get("scientific_status")
    comparison = payload.get("comparison")
    comp_status = comparison.get("status") if isinstance(comparison, dict) else comparison
    print(json.dumps({"ref": ref, "status": status, "scientific_status": scientific, "comparison": comp_status}))
    if status != "succeeded":
        return 1
    if comp_status == "fail" or scientific == "failed":
        return 1
    if strict and scientific == "not_verified":
        return 2
    return 0


def _cmd_replay(args) -> int:
    from .storage import ArtifactStore
    from .application import replay_run
    try:
        store = ArtifactStore(args.store)
        ref = replay_run(store, args.ref)
    except Exception as exc:
        return _fail(f"replay failed: {exc}")
    return _print_run_result(ref, store, strict=False)


def _cmd_report(args) -> int:
    from .storage import ArtifactStore
    from .reporting import render_report
    try:
        store = ArtifactStore(args.store)
        text = render_report(store, args.ref)
    except Exception as exc:
        return _fail(f"report failed: {exc}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"report written to {args.output}")
    else:
        print(text)
    return 0


def _cmd_agent(args) -> int:
    from .storage import ArtifactStore
    from .domain.contracts import ExperimentSpec, ModelArtifact
    from .domain.serialization import from_dict
    from .evidence import ingest_source
    from .llm import ChatProvider
    from . import agent as agent_mod
    if not args.source:
        return _fail("agent requires at least one --source")
    try:
        base_url = args.base_url or os.environ.get("BIOMODEL_BASE_URL")
        model = args.model or os.environ.get("BIOMODEL_MODEL")
        api_key_env = os.environ.get(args.api_key_env)
        if not base_url or not model or not api_key_env:
            return _fail("missing LLM settings: set BIOMODEL_BASE_URL / BIOMODEL_MODEL and the API key env var")
        store = ArtifactStore(args.store)
        materials = [ingest_source(store,p) for p in args.source]
        provider = ChatProvider(base_url=base_url, model=model, api_key_env=args.api_key_env,
                                json_mode=args.json_mode)
        experiment = from_dict(ExperimentSpec, _load_json(args.experiment, "experiment JSON"))
        ref = agent_mod.run_agent(store,provider,materials,experiment,mode=args.mode,
                                  allowed_mechanisms=tuple(args.allowed_mechanism),max_calls=args.max_calls)

    except Exception as exc:
        return _fail(f"agent failed: {exc}")
    state = store.get(ref)["payload"]["state"]
    print(json.dumps({"ref": ref, "mode": args.mode, "state":state}))
    return 0 if state == "completed" else 2


def _cmd_agent_resume(args) -> int:
    from .storage import ArtifactStore
    from .llm import ChatProvider
    from .agent import resume_agent
    try:
        store = ArtifactStore(args.store)
        previous = store.get(args.ref)
        if previous['kind'] != 'agent_run' or not previous['payload'].get('task_ref'):
            return _fail("run has no recoverable task configuration")
        task = store.get(previous['payload']['task_ref'])['payload']
        config = task['provider']
        base_url = args.base_url or os.environ.get('BIOMODEL_BASE_URL')
        if not base_url or not os.environ.get(args.api_key_env):
            return _fail("resume requires a provider base URL and API key environment variable")
        provider = ChatProvider(base_url, config['model'], args.api_key_env,
                                timeout_s=config['timeout_s'], max_tokens=config['max_tokens'],
                                json_mode=config['json_mode'])
        ref = resume_agent(store, provider, args.ref, _read_text(args.reply_file),
                           respondent=args.respondent)
        payload = store.get(ref)['payload']
    except Exception as exc:
        return _fail(f"agent resume failed: {exc}")
    print(json.dumps({'ref': ref, 'state': payload['state'], 'mode': payload['mode'],
                      'call_count': payload['call_count'], 'parent_run_ref': args.ref}))
    return 0 if payload['state'] == 'completed' else 2


def _cmd_hypothesis(args) -> int:
    from .storage import ArtifactStore
    from .hypotheses import run_hypothesis_study
    from .hypotheses import adaptation_candidates
    try:
        store = ArtifactStore(args.store)
        training = _load_json(args.training, "training JSON")
        holdout = _load_json(args.holdout, "holdout JSON") if args.holdout else ()
        ref = run_hypothesis_study(store, adaptation_candidates(), training, holdout=holdout, starts=args.starts)
    except Exception as exc:
        return _fail(f"hypothesis study failed: {exc}")
    record = store.get(ref)
    payload = record.get("payload", {}) if isinstance(record, dict) else {}
    status = payload.get("status")
    print(json.dumps({"ref": ref, "status": status,
                      "best_candidate": payload.get("best_candidate")}))
    # Biological uncertainty is acceptable: a completed study with no
    # definitive answer is still a successful execution.
    if status == "failed":
        return 1
    return 0


def _add_store(sp) -> None:
    sp.add_argument("--store", required=True, help="artifact store directory")


def _cmd_export(args):
    from .storage import ArtifactStore
    from .bundles import export_bundle
    try:
        manifest = export_bundle(ArtifactStore(args.store),args.ref,args.output)
    except Exception as exc:
        return _fail(str(exc))
    print(json.dumps({'root_ref':manifest['root_ref'],'output':args.output,'objects':len(manifest['objects_and_blobs'])}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biomodel_agent", description="Fact-driven biology model reproduction CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate an SBML artifact")
    p.add_argument("sbml")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("run", help="run a reproduction")
    p.add_argument("sbml")
    p.add_argument("--experiment", required=True)
    _add_store(p)
    p.add_argument("--observations")
    p.add_argument("--reference")
    p.add_argument("--scales")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("replay", help="replay a previous run")
    p.add_argument("ref")
    _add_store(p)
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("report", help="render a Markdown report")
    p.add_argument("ref")
    _add_store(p)
    p.add_argument("--output")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("agent", help="LLM-guided reproduction or hypothesis study")
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--experiment", required=True)
    _add_store(p)
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--api-key-env", default="BIOMODEL_API_KEY")
    p.add_argument("--max-calls", type=int, default=8)
    p.add_argument("--json-mode", action="store_true",
                   help="Request JSON object output; enable only for a compatible provider")
    p.add_argument("--mode", choices=["reproduction", "hypothesis"], default="reproduction")
    p.add_argument("--allowed-mechanism", action="append", default=[])
    p.set_defaults(func=_cmd_agent)

    p = sub.add_parser("agent-resume", help="continue an awaiting-input agent with a researcher reply")
    p.add_argument("ref", help="awaiting_input agent_run reference")
    _add_store(p)
    p.add_argument("--reply-file", required=True, help="UTF-8 researcher reply, at most 8000 characters")
    p.add_argument("--respondent", required=True, help="attribution supplied by the operator; not identity verification")
    p.add_argument("--base-url")
    p.add_argument("--api-key-env", default="BIOMODEL_API_KEY")
    p.set_defaults(func=_cmd_agent_resume)

    p = sub.add_parser("hypothesis", help="run the hypothesis study")
    p.add_argument("--training", required=True)
    p.add_argument("--holdout")
    _add_store(p)
    p.add_argument("--starts", type=int, default=3)
    p.set_defaults(func=_cmd_hypothesis)

    p = sub.add_parser('export',help='export a verified research bundle')
    p.add_argument('ref')
    _add_store(p)
    p.add_argument('--output',required=True)
    p.set_defaults(func=_cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
