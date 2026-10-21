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
    from .domain.serialization import from_dict
    from .validation import validate_reference
    if not isinstance(ref_data, dict):
        raise ValueError("reference must be a full SimulationResult or curated {times, values}")
    # Strip only documented scoring/provenance metadata. Structured result fields
    # always pass through the strict codec, even when metadata is also present.
    metadata = {"scales", "threshold", "schema_version", "track", "model_id",
                "reference_methods", "cross_method_error", "limitations"}
    payload = {key: value for key, value in ref_data.items() if key not in metadata}
    payload.setdefault("status", "succeeded")
    result = from_dict(SimulationResult, payload)
    validate_reference(result)
    return result


def _print_run_result(ref, store, strict: bool) -> int:
    from .application import blocking_verdict_basis, derive_scientific_status
    record = store.get(ref)
    payload = record.get("payload", {}) if isinstance(record, dict) else {}
    status = payload.get("status")
    stored_scientific = payload.get("scientific_status")
    comparison = payload.get("comparison")
    comp_status = comparison.get("status") if isinstance(comparison, dict) else comparison
    # Shared verdict from the actual stored facts: an explicit failure beats
    # any stored 'not_verified' / 'unknown' string (and vice versa it can
    # never manufacture a failure that the facts do not show).
    verdict = derive_scientific_status(payload)
    basis = blocking_verdict_basis(payload)
    print(json.dumps({"ref": ref, "status": status, "scientific_status": verdict,
                      "stored_scientific_status": stored_scientific,
                      "verdict": verdict, "verdict_basis": basis, "comparison": comp_status}))
    if verdict == "failed":
        return 1
    if strict and verdict != "passed_structured_reference":
        # Strict and unverified (unknown provenance / missing comparison):
        # exit 2, keeping the original unverified behavior while no explicit
        # failure is present.
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
        materials = [ingest_source(store,p, pdf_parser=args.pdf_parser, pdf_timeout_s=args.pdf_timeout) for p in args.source]
        provider = ChatProvider(base_url=base_url, model=model, api_key_env=args.api_key_env,
                                json_mode=args.json_mode)
        if args.study_plan_ref:
            from .studies import load_plan
            plan = load_plan(store, args.study_plan_ref)
            exp_data = _load_json(args.experiment, "experiment JSON") if args.experiment else plan['config']['training'][0]['experiment']
        elif args.experiment:
            exp_data = _load_json(args.experiment, "experiment JSON")
        else:
            return _fail('agent requires --experiment or --study-plan-ref')
        experiment = from_dict(ExperimentSpec, exp_data)
        ref = agent_mod.run_agent(store,provider,materials,experiment,mode=args.mode,
                                  allowed_mechanisms=tuple(args.allowed_mechanism),max_calls=args.max_calls,
                                  memory_project=args.memory_project, memory_model_ref=args.memory_model_ref,
                                  study_plan_ref=args.study_plan_ref, context_max_chars=args.context_max_chars,
                                  retrieval_method=args.retrieval_method)

    except Exception as exc:
        return _fail(f"agent failed: {exc}")
    state = store.get(ref)["payload"]["state"]
    print(json.dumps({"ref": ref, "mode": args.mode, "state":state}))
    return 0 if state == "completed" else 2


def _cmd_source_ingest(args):
    from .storage import ArtifactStore
    from .evidence import ingest_source
    try:
        store = ArtifactStore(args.store)
        ref = ingest_source(store, args.path, pdf_parser=args.pdf_parser, pdf_timeout_s=args.pdf_timeout)
        source = store.get(ref)['payload']
    except Exception as exc:
        return _fail(f'source ingestion failed: {exc}')
    print(json.dumps({'ref': ref, 'parser': source['parser'], 'parser_version': source['parser_version'],
                      'pages': len(source['pages']), 'warnings': source['warnings'],
                      'parser_output_ref': source.get('parser_output_ref')}, ensure_ascii=False))
    return 0


def _add_pdf_options(parser):
    parser.add_argument('--pdf-parser', choices=['pypdf', 'pymupdf'], default='pypdf',
                        help='local PDF backend; pymupdf supplies layout/table provenance (requires pdf extra)')
    parser.add_argument('--pdf-timeout', type=float, default=30., help='pymupdf extraction budget in seconds, at most 300')


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


def _cmd_agent_recovery_status(args) -> int:
    from .recovery import diagnose_execution, open_readonly_store
    try:
        report = diagnose_execution(open_readonly_store(args.store), args.ref)
        summary = {key: value for key, value in report.items() if key != 'operations'}
        summary['operations'] = [{
            'sequence': op['reserved']['sequence'], 'name': op['reserved']['name'],
            'phase': op['terminal']['phase'] if op['terminal'] else 'reserved',
            'replay_class': op['reserved']['replay_class'],
            'reserved_ref': op['reserved_ref'], 'terminal_ref': op['terminal_ref'],
            'retry_read_only': op.get('retry_read_only', False),
        } for op in report['operations']]
        print(json.dumps(summary, ensure_ascii=False))
        return 2 if report['status'] == 'blocked' else 0
    except Exception as exc:
        return _fail(f'agent recovery diagnosis failed: {exc}')


def _cmd_agent_recover(args) -> int:
    from .storage import ArtifactStore
    from .llm import ChatProvider
    from .agent import recover_agent
    from .recovery import diagnose_execution, open_readonly_store, RecoveryBlocked
    try:
        view = open_readonly_store(args.store)
        report = diagnose_execution(view, args.ref)
        if report['status'] != 'ready':
            raise RecoveryBlocked(', '.join(report['reasons']) or 'execution already has a final agent_run')
        config = view.get(report['task_ref'])['payload']['provider']
        base_url = args.base_url or os.environ.get('BIOMODEL_BASE_URL')
        if not base_url or not os.environ.get(args.api_key_env):
            return _fail('recovery requires the frozen provider base URL and API key environment variable')
        provider = ChatProvider(base_url, config['model'], args.api_key_env,
                                timeout_s=config['timeout_s'], max_tokens=config['max_tokens'],
                                json_mode=config['json_mode'])
        store = ArtifactStore(args.store)
        ref = recover_agent(store, provider, args.ref)
        result = store.get(ref)['payload']
    except Exception as exc:
        return _fail(f'agent recovery failed: {exc}')
    print(json.dumps({'ref': ref, 'state': result['state'], 'call_count': result['call_count'],
                      'execution_ref': args.ref, 'recovery_refs': result['recovery_refs']}))
    return 0 if result['state'] == 'completed' else 2


def _cmd_hypothesis(args) -> int:
    from .storage import ArtifactStore
    from .studies import prepare_study, execute_study, result_summary
    try:
        store = ArtifactStore(args.store)
        training = _load_json(args.training, "training JSON")
        holdout = _load_json(args.holdout, "holdout JSON") if args.holdout else ()
        config = {'training': training, 'holdout': list(holdout), 'starts': args.starts,
                  'seed': args.seed, 'max_nfev': args.max_nfev, 'timeout_s': args.timeout,
                  'total_timeout_s': args.total_timeout, 'separation_threshold': args.separation_threshold}
        if args.candidate:
            config['candidates'] = args.candidate
        plan_ref = prepare_study(store, config)
        ref = execute_study(store, plan_ref)
        summary = result_summary(store, ref)
    except Exception as exc:
        return _fail(f"hypothesis study failed: {exc}")
    print(json.dumps({'ref': ref, 'plan_ref': plan_ref, 'status': summary['execution_status'],
                      'best_candidate': summary['comparison']['winner'], **summary}, ensure_ascii=False))
    # Biological uncertainty is acceptable: a completed study with no
    # definitive answer is still a successful execution.
    return 0 if summary['completion_eligible'] else 1


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


def _cmd_memory_add(args):
    from .storage import ArtifactStore
    from .memory import remember
    try:
        ref = remember(ArtifactStore(args.store), project_id=args.project,
                       content=_read_text(args.content_file), category=args.category, author=args.author,
                       source_refs=args.source_ref, model_ref=args.model_ref,
                       supersedes=args.supersedes, reason=args.reason)
    except Exception as exc:
        return _fail(f'memory add failed: {exc}')
    print(json.dumps({'memory_ref': ref, 'authority': 'operator_assertion'}))
    return 0


def _cmd_memory_search(args):
    from .storage import ArtifactStore
    from .memory import snapshot, recall
    try:
        store = ArtifactStore(args.store)
        scope = snapshot(store, project_id=args.project, model_ref=args.model_ref, source_refs=args.source_ref)
        result = recall(store, scope, args.query, source_refs=args.source_ref)
    except Exception as exc:
        return _fail(f'memory search failed: {exc}')
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_memory_retract(args):
    from .storage import ArtifactStore
    from .memory import retract
    try:
        ref = retract(ArtifactStore(args.store), args.ref, author=args.author, reason=args.reason)
    except Exception as exc:
        return _fail(f'memory retract failed: {exc}')
    print(json.dumps({'retraction_ref': ref}))
    return 0


def _cmd_source_search(args):
    from .storage import ArtifactStore
    from .retrieval import search_sources
    try:
        result = search_sources(ArtifactStore(args.store), args.source_ref, args.query, method=args.method)
    except Exception as exc:
        return _fail(f'source search failed: {exc}')
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_study_prepare(args):
    from .storage import ArtifactStore
    from .studies import prepare_study, plan_summary
    try:
        config = {'training': _load_json(args.training, 'training JSON'),
                  'holdout': _load_json(args.holdout, 'holdout JSON') if args.holdout else [],
                  'starts': args.starts, 'seed': args.seed, 'max_nfev': args.max_nfev,
                  'timeout_s': args.timeout, 'total_timeout_s': args.total_timeout,
                  'separation_threshold': args.separation_threshold}
        if args.candidate:
            config['candidates'] = args.candidate
        store = ArtifactStore(args.store)
        ref = prepare_study(store, config)
        result = plan_summary(store, ref)
    except Exception as exc:
        return _fail(f'study prepare failed: {exc}')
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_study_run(args):
    from .storage import ArtifactStore
    from .studies import execute_study, result_summary
    try:
        store = ArtifactStore(args.store)
        ref = execute_study(store, args.ref)
        summary = result_summary(store, ref)
    except Exception as exc:
        return _fail(f'study run failed: {exc}')
    print(json.dumps({'ref': ref, **summary}, ensure_ascii=False))
    return 0 if summary['completion_eligible'] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biomodel_agent", description="Fact-driven biology model reproduction CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser('source-ingest', help='ingest local evidence without invoking an LLM')
    p.add_argument('path')
    _add_store(p)
    _add_pdf_options(p)
    p.set_defaults(func=_cmd_source_ingest)

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
    _add_pdf_options(p)
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--experiment", help="required unless a study plan supplies the experiment")
    _add_store(p)
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--api-key-env", default="BIOMODEL_API_KEY")
    p.add_argument("--max-calls", type=int, default=8)
    p.add_argument("--context-max-chars", type=int, default=60000,
                   help="message-JSON character budget, 8000..500000; not a token limit")
    p.add_argument('--retrieval-method', choices=['lexical_v1', 'passage_v2'], default='lexical_v1',
                   help='freeze source retrieval method for this task and its resumptions')
    p.add_argument("--json-mode", action="store_true",
                   help="Request JSON object output; enable only for a compatible provider")
    p.add_argument("--mode", choices=["reproduction", "hypothesis"], default="reproduction")
    p.add_argument("--allowed-mechanism", action="append", default=[])
    p.add_argument("--memory-project", help="explicit project namespace for advisory memory")
    p.add_argument("--memory-model-ref", help="optional model scope within the memory project")
    p.add_argument("--study-plan-ref", help="operator-frozen adaptation study plan; requires hypothesis mode")
    p.set_defaults(func=_cmd_agent)

    p = sub.add_parser("agent-resume", help="continue an awaiting-input agent with a researcher reply")
    p.add_argument("ref", help="awaiting_input agent_run reference")
    _add_store(p)
    p.add_argument("--reply-file", required=True, help="UTF-8 researcher reply, at most 8000 characters")
    p.add_argument("--respondent", required=True, help="attribution supplied by the operator; not identity verification")
    p.add_argument("--base-url")
    p.add_argument("--api-key-env", default="BIOMODEL_API_KEY")
    p.set_defaults(func=_cmd_agent_resume)

    p = sub.add_parser('agent-recovery-status', help='read-only diagnosis of an interrupted agent_execution')
    p.add_argument('ref', help='agent_execution reference, not agent_run')
    _add_store(p)
    p.set_defaults(func=_cmd_agent_recovery_status)

    p = sub.add_parser('agent-recover', help='explicitly continue a verified interrupted execution')
    p.add_argument('ref', help='agent_execution reference, not agent_run')
    _add_store(p)
    p.add_argument('--base-url')
    p.add_argument('--api-key-env', default='BIOMODEL_API_KEY')
    p.set_defaults(func=_cmd_agent_recover)

    p = sub.add_parser("hypothesis", help="run the hypothesis study")
    p.add_argument("--training", required=True)
    p.add_argument("--holdout")
    _add_store(p)
    p.add_argument("--starts", type=int, default=3)
    p.add_argument('--candidate', action='append', help='supported candidate; defaults to all three')
    p.add_argument('--seed', type=int, default=17)
    p.add_argument('--max-nfev', type=int, default=100)
    p.add_argument('--timeout', type=float, default=30., help='per-candidate fitting cap in seconds')
    p.add_argument('--total-timeout', type=float, default=300., help='total scientific study budget in seconds, at most 600')
    p.add_argument('--separation-threshold', type=float, default=.05)
    p.set_defaults(func=_cmd_hypothesis)

    p = sub.add_parser('export',help='export a verified research bundle')
    p.add_argument('ref')
    _add_store(p)
    p.add_argument('--output',required=True)
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser('memory-add', help='record or supersede an attributed advisory memory')
    _add_store(p)
    p.add_argument('--project', required=True)
    p.add_argument('--content-file', required=True)
    p.add_argument('--category', choices=['preference', 'experience', 'decision'], required=True)
    p.add_argument('--author', required=True)
    p.add_argument('--source-ref', action='append', default=[])
    p.add_argument('--model-ref')
    p.add_argument('--supersedes')
    p.add_argument('--reason', default='')
    p.set_defaults(func=_cmd_memory_add)

    p = sub.add_parser('memory-search', help='search active memories in an explicit project and source scope')
    _add_store(p)
    p.add_argument('--project', required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--source-ref', action='append', default=[])
    p.add_argument('--model-ref')
    p.set_defaults(func=_cmd_memory_search)

    p = sub.add_parser('memory-retract', help='append a retraction without overwriting original memory')
    _add_store(p)
    p.add_argument('ref')
    p.add_argument('--author', required=True)
    p.add_argument('--reason', required=True)
    p.set_defaults(func=_cmd_memory_retract)

    p = sub.add_parser('source-search', help='retrieve exact spans only from explicitly supplied sources')
    _add_store(p)
    p.add_argument('--source-ref', action='append', required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--method', choices=['lexical_v1', 'passage_v2'], default='lexical_v1')
    p.set_defaults(func=_cmd_source_search)

    p = sub.add_parser('study-prepare', help='freeze an adaptation candidate study before execution')
    _add_store(p)
    p.add_argument('--training', required=True)
    p.add_argument('--holdout')
    p.add_argument('--candidate', action='append', help='supported candidate name; defaults to all three')
    p.add_argument('--starts', type=int, default=3)
    p.add_argument('--seed', type=int, default=17)
    p.add_argument('--max-nfev', type=int, default=100)
    p.add_argument('--timeout', type=float, default=30.)
    p.add_argument('--total-timeout', type=float, default=300., help='total study budget in seconds, at most 600')
    p.add_argument('--separation-threshold', type=float, default=.05)
    p.set_defaults(func=_cmd_study_prepare)

    p = sub.add_parser('study-run', help='execute a frozen study without an LLM')
    _add_store(p)
    p.add_argument('ref', help='frozen study plan reference')
    p.set_defaults(func=_cmd_study_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
