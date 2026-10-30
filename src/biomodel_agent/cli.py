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
    if not args.source and not args.source_ref and not args.estimation_plan_ref:
        return _fail("agent requires at least one --source or --source-ref")
    try:
        base_url = args.base_url or os.environ.get("BIOMODEL_BASE_URL")
        model = args.model or os.environ.get("BIOMODEL_MODEL")
        api_key_env = os.environ.get(args.api_key_env)
        if not base_url or not model or not api_key_env:
            return _fail("missing LLM settings: set BIOMODEL_BASE_URL / BIOMODEL_MODEL and the API key env var")
        store = ArtifactStore(args.store)
        materials = [ingest_source(store,p, pdf_parser=args.pdf_parser, pdf_timeout_s=args.pdf_timeout) for p in args.source]
        materials.extend(args.source_ref)
        provider = ChatProvider(base_url=base_url, model=model, api_key_env=args.api_key_env,
                                json_mode=args.json_mode)
        if args.estimation_plan_ref:
            from .estimation import load_plan
            plan = load_plan(store, args.estimation_plan_ref)
            if not materials:
                materials = plan['config']['source_refs'][:]
            exp_data = _load_json(args.experiment, 'experiment JSON') if args.experiment else plan['config']['training'][0]['experiment']
        elif args.study_plan_ref:
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
                                  memory_context=_load_json(args.memory_context, 'memory context') if args.memory_context else None,
                                  experience_proposals=args.experience_proposals,
                                  study_plan_ref=args.study_plan_ref, context_max_chars=args.context_max_chars,
                                  estimation_requests=args.estimation_requests, estimation_plan_ref=args.estimation_plan_ref,
                                  modeling_decisions=args.modeling_decisions,
                                  modeling_context=_load_json(args.modeling_context, 'modeling context') if args.modeling_context else None,
                                  retrieval_method=args.retrieval_method,
                                  knowledge_databases=args.knowledge_database,
                                  parameter_requirements=_load_json(args.parameter_requirements, 'parameter requirements JSON') if args.parameter_requirements else None,
                                  task_planning=args.task_planning, failure_handling=args.failure_handling, scientific_skills=args.scientific_skills)

    except Exception as exc:
        return _fail(f"agent failed: {exc}")
    state = store.get(ref)["payload"]["state"]
    print(json.dumps({"ref": ref, "mode": args.mode, "state":state}))
    return 0 if state == "completed" else 2


def _cmd_knowledge(args):
    from . import knowledge
    from .storage import ArtifactStore
    try:
        store = ArtifactStore(args.store)
        if args.command == 'knowledge-search':
            result = knowledge.search(store, args.database, args.query, args.limit)
        elif args.command == 'knowledge-files':
            result = knowledge.model_files(store, args.identifier)
        else:
            result = knowledge.fetch(store, args.database, args.identifier, args.filename)
            if args.output:
                if args.database != 'biomodels':
                    raise ValueError('--output is only supported for BioModels SBML')
                source = store.get(result['source_ref'])['payload']
                with open(args.output, 'xb') as stream:
                    stream.write(store.get_blob(source['blob_ref']))
                result['output'] = args.output
    except (ValueError, OSError) as exc:
        return _fail(f'knowledge access failed: {exc}')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


def _cmd_parameter_source(args):
    from .parameter_sources import prepare
    from .storage import ArtifactStore
    try:
        result = prepare(ArtifactStore(args.store), args.source_ref, args.target, args.parameter_index, args.unit)
    except (ValueError, OSError) as exc:
        return _fail(f'parameter preparation failed: {exc}')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


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
        if previous['kind'] not in ('agent_run', 'fba_agent_run','typed_agent_run') or not previous['payload'].get('task_ref'):
            return _fail("run has no recoverable task configuration")
        task = store.get(previous['payload']['task_ref'])['payload']
        if previous['kind'] == 'fba_agent_run' and task.get('fba_protocol') != 'fba_agent_v2':
            return _fail('legacy FBA records have no recovery snapshot; start a new recoverable task')
        if previous['kind']=='typed_agent_run' and task.get('typed_protocol')!='typed_agent_v2':
            return _fail('legacy typed records have no recovery snapshot; start a new recoverable task')
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
    print(json.dumps({'ref': ref, 'state': payload['state'], 'mode': payload.get('mode', payload.get('model_type')),
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


def _cmd_memory_candidate(args):
    from .storage import ArtifactStore
    from . import memory_candidates
    try:
        store = ArtifactStore(args.store)
        operation = args.candidate_operation
        if operation == 'propose':
            result = {'candidate_ref': memory_candidates.propose(store, args.ref, project_id=args.project,
                       author=args.author, applicability=args.applicability,
                       conditions=_load_json(args.conditions, 'memory conditions') if args.conditions else None)}
        elif operation == 'inspect':
            result = memory_candidates.inspect(store, args.ref)
        elif operation == 'review':
            result = {'review_ref': memory_candidates.review(store, args.ref, decision=args.decision,
                      reviewer=args.reviewer, reason=args.reason)}
        else:
            result = {'memory_ref': memory_candidates.activate(store, args.ref)}
    except Exception as exc:
        return _fail(f'memory candidate {args.candidate_operation} failed: {exc}')
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_agent_progress(args):
    from .storage import ArtifactStore
    from .workstate import from_run
    try:
        result = from_run(ArtifactStore(args.store), args.ref)
    except Exception as exc:
        return _fail(f'agent progress failed: {exc}')
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_memory_add(args):
    from .storage import ArtifactStore
    from .memory import remember
    try:
        ref = remember(ArtifactStore(args.store), project_id=args.project,
                       content=_read_text(args.content_file), category=args.category, author=args.author,
                       source_refs=args.source_ref, model_ref=args.model_ref,
                       supersedes=args.supersedes, reason=args.reason,
                       conditions=_load_json(args.conditions, 'memory conditions') if args.conditions else None,
                       depends_on=args.depends_on)
    except Exception as exc:
        return _fail(f'memory add failed: {exc}')
    print(json.dumps({'memory_ref': ref, 'authority': 'operator_assertion'}))
    return 0


def _cmd_memory_architecture(args):
    from .storage import ArtifactStore
    from . import memory, memory_candidates
    try:
        store = ArtifactStore(args.store)
        if args.command == 'memory-invalidate':
            value = {'invalidation_ref': memory.invalidate_dependency(store, args.project, args.ref,
                     author=args.author, reason=args.reason)}
        elif args.command == 'experience-derive':
            value = {'derivation_ref': memory_candidates.derive_for_run(store, args.run_ref)}
        else:
            refs = memory_candidates.derivations(store, args.run_ref)
            value = {'run_ref': args.run_ref, 'derivations': [dict(ref=ref, **store.get(ref)['payload']) for ref in refs]}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return _fail(str(exc))
    print(json.dumps(value, ensure_ascii=False))
    return 0


def _cmd_memory_search(args):
    from .storage import ArtifactStore
    from .memory import snapshot, recall
    try:
        store = ArtifactStore(args.store)
        scope = snapshot(store, project_id=args.project, model_ref=args.model_ref, source_refs=args.source_ref,
                         context=_load_json(args.context, 'memory context') if args.context else None)
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


def _cmd_fba(args):
    from .storage import ArtifactStore
    from . import fba
    try:
        store=ArtifactStore(args.store)
        if args.command=='fba-import':
            ref=fba.import_model(store,args.path,format=args.format)
            print(json.dumps(dict(ref=ref,model_type='fba')));return 0
        if args.command=='fba-replay':
            ref=fba.replay(store,args.ref)
        elif args.command=='agent-fba':
            from .llm import ChatProvider
            from .agent import run_model_agent
            base=args.base_url or os.environ.get('BIOMODEL_BASE_URL')
            model=args.model or os.environ.get('BIOMODEL_MODEL')
            if not base or not model or not os.environ.get(args.api_key_env):
                return _fail('missing FBA Agent LLM settings; no service called')
            provider=ChatProvider(base_url=base,model=model,api_key_env=args.api_key_env)
            ref=run_model_agent(store,provider,model_type='fba',model_ref=args.ref,
                settings=_load_json(args.config,'FBA settings') if args.config else None,max_calls=args.max_calls,
                recoverable=args.recoverable,context_max_chars=args.context_max_chars)
            result=store.get(ref)['payload']
            print(json.dumps(dict(ref=ref,state=result['state'],model_type='fba')))
            return 0 if result['state']=='completed' else 2
        else:
            ref=fba.run(store,args.ref,_load_json(args.config,'FBA settings') if args.config else None)
        result=fba.summary(store,ref)
        print(json.dumps(dict(ref=ref,**result)))
        return 0 if result['completion_eligible'] else 2
    except Exception as exc:
        return _fail('FBA failed: '+str(exc))


def _cmd_case(args):
    from . import case_library as cases
    from .storage import ArtifactStore
    try:
        if args.command=='case-search':value=cases.search(args.query)
        elif args.command=='case-inspect':value=cases.inspect(args.id,args.version)
        else:
            store=ArtifactStore(args.store)
            if args.command=='case-load':
                ref=cases.load(store,args.id,args.version)
            elif args.command=='case-run':ref=cases.run(store,args.material_ref)
            else:
                ref=cases.propose(store,args.material_ref,args.source_ref,proposed_version=args.proposed_version,
                                  summary=args.summary,applicability=args.applicability,author=args.author)
            value={'ref':ref,**store.get(ref)['payload']}
        print(json.dumps(value,ensure_ascii=False,indent=2))
        if args.command=='case-run' and value['status']!='numeric_diagnostic_pass':return 2
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:return _fail(str(exc))


def _cmd_jats(args):
    from .storage import ArtifactStore
    from . import jats_sources
    try:
        store=ArtifactStore(args.store)
        if args.command=='source-jats':
            ref=jats_sources.ingest(store,args.path,args.section,_load_json(args.assets,'asset map') if args.assets else None)
        else:
            ref=jats_sources.transcribe(store,args.source_ref,_load_json(args.candidates,'visual candidates'),args.actor,args.notes)
        p=store.get(ref)['payload']
        print(json.dumps({'source_ref':ref,'parser':p['parser'],'warnings':p['warnings']},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return _fail(str(exc))


def _cmd_paper(args):
    from .storage import ArtifactStore
    from . import paper_reference as paper
    try:
        store=ArtifactStore(args.store)
        if args.command=='paper-reference':ref=paper.prepare_reference(store,args.image,_load_json(args.calibration,'calibration'))
        elif args.command=='paper-protocol':ref=paper.protocol(store,args.reference_ref,args.model_ref,_load_json(args.protocol,'protocol'))
        else:ref=paper.compare(store,args.protocol_ref,args.run_ref)
    except (ValueError,KeyError,TypeError,OSError) as exc:return _fail(str(exc))
    print(ref)
    return 0


def _cmd_estimation(args):
    from .storage import ArtifactStore
    from . import estimation
    try:
        store = ArtifactStore(args.store)
        if args.command == 'estimate-prepare':
            ref = estimation.prepare_plan(store, _load_json(args.config, 'estimation protocol'))
            output = estimation.plan_summary(store, ref)
        elif args.command == 'estimate-plan':
            output = estimation.plan_summary(store, args.ref)
        elif args.command == 'estimate-status':
            output = estimation.status(store, args.ref)
        elif args.command == 'estimate-request':
            from .estimation_workflow import REQUEST
            obj = store.get(args.ref)
            if obj['kind'] != REQUEST:
                raise ValueError('expected estimation request')
            output = dict(request_ref=args.ref, **obj['payload'])
        elif args.command == 'estimate-review':
            from .estimation_workflow import review
            ref = review(store, args.ref, decision=args.decision, reviewer=args.reviewer, reason=args.reason,
                         acknowledged_unresolved=args.acknowledge)
            output = dict(review_ref=ref, **store.get(ref)['payload'])
        elif args.command == 'estimate-candidate':
            from .estimation_workflow import prepare_candidate
            ref = prepare_candidate(store, args.ref)
            output = dict(candidate_ref=ref, **store.get(ref)['payload'])
        elif args.command == 'estimate-run':
            ref = estimation.execute(store, args.ref)
            output = dict(result_ref=ref, **estimation.result_summary(store, ref))
        else:
            output = estimation.result_summary(store, args.ref)
    except Exception as exc:
        return _fail(f'estimation failed: {exc}')
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0 if output.get('execution_status', 'succeeded') == 'succeeded' else 2


def _cmd_exploration(args):
    from .storage import ArtifactStore
    from . import exploration
    try:
        store = ArtifactStore(args.store)
        if args.command == 'estimate-explore-prepare':
            ref = exploration.prepare_plan(store, args.candidate_ref, _load_json(args.config, 'exploration protocol'))
            output = exploration.plan_summary(store, ref)
        elif args.command == 'estimate-explore-run':
            ref = exploration.execute(store, args.ref)
            output = exploration.result_summary(store, ref)
        elif args.command == 'estimate-explore-status':
            output = exploration.status(store, args.ref)
        else:
            output = exploration.result_summary(store, args.ref)
    except Exception as exc:
        return _fail(f'exploration failed: {exc}')
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0 if output.get('status', 'succeeded') == 'succeeded' else 2


def _cmd_typed(args):
    from . import typed_models as models
    from .storage import ArtifactStore
    try:
        if args.command=='model-types':
            print(json.dumps(models.capabilities(), ensure_ascii=False)); return 0
        store = ArtifactStore(args.store)
        if args.command=='typed-import':
            ref = models.import_model(store, args.path)
            print(json.dumps(dict(ref=ref, model_type=models.load(store,ref)['model_type']))); return 0
        if args.command=='typed-status':
            out = models.status(store, args.ref)
            print(json.dumps(out)); return 0 if out['state']!='unknown_effects' else 2
        if args.command=='typed-replay':
            ref = models.replay(store, args.ref)
        elif args.command=='typed-execute':
            ref = models.execute(store, args.ref)
        else:
            settings = _load_json(args.config, 'native model settings') if args.config else None
            if args.command=='typed-prepare':
                ref = models.prepare(store,args.ref,settings)
                print(json.dumps(dict(ref=ref,state='ready'))); return 0
            if args.command=='agent-typed':
                from .agent import run_model_agent
                from .llm import ChatProvider
                models.load(store, args.ref, model_type=args.model_type)
                base = args.base_url or os.environ.get('BIOMODEL_BASE_URL')
                model = args.model or os.environ.get('BIOMODEL_MODEL')
                if not base or not model or not os.environ.get(args.api_key_env):
                    return _fail('missing native Agent LLM settings; no service called')
                provider = ChatProvider(base_url=base,model=model,api_key_env=args.api_key_env)
                ref = run_model_agent(store,provider,model_type=args.model_type,model_ref=args.ref,settings=settings,max_calls=args.max_calls,
                    recoverable=args.recoverable,context_max_chars=args.context_max_chars)
                out = store.get(ref)['payload']
                print(json.dumps(dict(ref=ref,state=out['state'],model_type=out['model_type']))); return 0 if out['state']=='completed' else 2
            ref = models.run(store,args.ref,settings)
        out = models.summary(store,ref)
        print(json.dumps(dict(ref=ref,**out))); return 0 if out['completion_eligible'] else 2
    except Exception as exc:
        return _fail('native modeling failed: '+str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biomodel_agent", description="Fact-driven biology model reproduction CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    from .modeling_cli import register as register_modeling_commands
    register_modeling_commands(sub, _add_store)
    from .workspace_tools import register as register_workspace_commands
    register_workspace_commands(sub)
    from .research_cli import register as register_research_commands
    register_research_commands(sub,_add_store)
    from .task_proposal_cli import register as register_proposal_commands
    register_proposal_commands(sub,_add_store)
    from .workbench_server import register as register_workbench_commands
    register_workbench_commands(sub,_add_store)
    p = sub.add_parser('model-types', help='list explicit modeling profiles and limitations')
    p.set_defaults(func=_cmd_typed)
    p = sub.add_parser('typed-import', help='import strict native stochastic/PDE/Boolean/individual JSON')
    _add_store(p); p.add_argument('path'); p.set_defaults(func=_cmd_typed)
    for command in ('typed-prepare', 'typed-execute', 'typed-run', 'typed-replay', 'typed-status', 'agent-typed'):
        p = sub.add_parser(command, help='bounded native model execution; no automatic method inference')
        _add_store(p); p.add_argument('ref')
        if command in ('typed-prepare', 'typed-run', 'agent-typed'):
            p.add_argument('--config', help='JSON settings for the explicit native profile')
        if command=='agent-typed':
            from .typed_models import TYPES
            p.add_argument('--model-type', choices=TYPES, required=True)
            p.add_argument('--base-url'); p.add_argument('--model'); p.add_argument('--api-key-env', default='BIOMODEL_API_KEY')
            p.add_argument('--max-calls', type=int, default=6)
            p.add_argument('--recoverable',action='store_true',help='enable native typed v2 pause and verified recovery')
            p.add_argument('--context-max-chars',type=int,default=60000)
        p.set_defaults(func=_cmd_typed)
    p = sub.add_parser('estimate-explore-prepare', help='freeze explicit exploration of a reviewed estimated-model candidate')
    _add_store(p); p.add_argument('--candidate-ref', required=True); p.add_argument('--config', required=True)
    p.set_defaults(func=_cmd_exploration)
    for command in ('estimate-explore-run', 'estimate-explore-show', 'estimate-explore-status'):
        p = sub.add_parser(command, help='explicit exploratory execution/inspection; no strict paper reproduction claim')
        _add_store(p); p.add_argument('ref'); p.set_defaults(func=_cmd_exploration)
    p = sub.add_parser('estimate-prepare', help='freeze a separate explicit bounded ODE estimation branch')
    _add_store(p); p.add_argument('--config', required=True); p.set_defaults(func=_cmd_estimation)
    for command in ('estimate-plan', 'estimate-status', 'estimate-run', 'estimate-show', 'estimate-request', 'estimate-candidate'):
        p = sub.add_parser(command, help='inspect/execute frozen estimation; candidate values only, no automatic adoption')
        _add_store(p); p.add_argument('ref'); p.set_defaults(func=_cmd_estimation)
    p = sub.add_parser('estimate-review', help='explicit operator review for exploration; never scientific certification')
    _add_store(p); p.add_argument('ref'); p.add_argument('--decision', choices=['approved_for_exploration','rejected'], required=True)
    p.add_argument('--reviewer', required=True); p.add_argument('--reason', required=True)
    p.add_argument('--acknowledge', action='append', default=[], help='explicitly acknowledge each unresolved result item for exploratory approval')
    p.set_defaults(func=_cmd_estimation)
    p=sub.add_parser('case-search',help='search pinned seen-development reuse metadata, without loading source/reference files')
    p.add_argument('--query',default='');p.set_defaults(func=_cmd_case)
    for command in ('case-inspect','case-load'):
        p=sub.add_parser(command,help='inspect/load an explicit versioned reuse case; no model execution')
        p.add_argument('--id',required=True);p.add_argument('--version',default='1.0.0')
        if command=='case-load':_add_store(p)
        p.set_defaults(func=_cmd_case)
    p=sub.add_parser('case-run',help='explicit new deterministic run of an immutable loaded case; not task resume')
    _add_store(p);p.add_argument('--material-ref',required=True);p.set_defaults(func=_cmd_case)
    p=sub.add_parser('case-propose',help='quarantine an attributed knowledge candidate; never publish or activate it')
    _add_store(p);p.add_argument('--material-ref',required=True);p.add_argument('--source-ref',action='append',required=True)
    for name in ('proposed-version','summary','applicability','author'):p.add_argument('--'+name,required=True)
    p.set_defaults(func=_cmd_case)
    p=sub.add_parser('source-jats',help='project a single JATS section with explicit unresolved image/MathML/table anchors')
    _add_store(p)
    p.add_argument('--path',required=True);p.add_argument('--section',required=True);p.add_argument('--assets')
    p.set_defaults(func=_cmd_jats)
    p=sub.add_parser('source-transcript',help='new attributed visual-candidate source; independent review remains pending')
    _add_store(p)
    for name in ('source-ref','candidates','actor','notes'):p.add_argument('--'+name,required=True)
    p.set_defaults(func=_cmd_jats)
    for command,fields in [('paper-reference',('image','calibration')),('paper-protocol',('reference-ref','model-ref','protocol')),('paper-compare',('protocol-ref','run-ref'))]:
        p=sub.add_parser(command,help='retrospective image feature diagnostic; requires figures extra')
        _add_store(p)
        for field in fields:p.add_argument('--'+field,required=True)
        p.set_defaults(func=_cmd_paper)

    p=sub.add_parser('fba-import',help='import a bounded native FBA JSON or strict FBC v2 model')
    p.add_argument('path');p.add_argument('--format',choices=('json','fbc'),default='json');_add_store(p)
    p.set_defaults(func=_cmd_fba)
    for name in ('fba-run','fba-replay','agent-fba'):
        p=sub.add_parser(name,help='execute a frozen steady-state FBA model or replay')
        p.add_argument('ref');_add_store(p)
        if name!='fba-replay':p.add_argument('--config',help='JSON with method, timeout_s and optional frozen scalar reference')
        if name=='agent-fba':
            p.add_argument('--recoverable',action='store_true',help='new FBA v2 task with checkpoints, attributed pause/reply and verified recovery')
            p.add_argument('--context-max-chars',type=int,default=60000,help='message-JSON context budget; custom values require --recoverable')
            p.add_argument('--base-url');p.add_argument('--model');p.add_argument('--api-key-env',default='BIOMODEL_API_KEY')
            p.add_argument('--max-calls',type=int,default=6)
        p.set_defaults(func=_cmd_fba)

    p = sub.add_parser('parameter-source', help='prepare a recomputable native SABIO-RK parameter source for a new task')
    _add_store(p)
    p.add_argument('source_ref', help='previously imported SABIO-RK source')
    p.add_argument('--target', required=True, help='explicit model symbol/value mapping')
    p.add_argument('--parameter-index', type=int, required=True, help='original kineticlaw.parameter index')
    p.add_argument('--unit', required=True, help='explicit target unit; supported conversions only')
    p.set_defaults(func=_cmd_parameter_source)

    for name in ('knowledge-search', 'knowledge-files', 'knowledge-fetch'):
        p = sub.add_parser(name, help='query or import public BioModels/SABIO-RK records')
        _add_store(p)
        if name != 'knowledge-files':
            p.add_argument('--database', choices=['biomodels', 'sabiork'], required=True)
        if name == 'knowledge-search':
            p.add_argument('--query', required=True, help='public database query; sent to the selected service')
            p.add_argument('--limit', type=int, default=5)
        else:
            p.add_argument('identifier')
            if name == 'knowledge-fetch':
                p.add_argument('--filename', help='BioModels XML/SBML filename; auto-select only one primary XML')
                p.add_argument('--output', help='save BioModels SBML to a new file; never overwrite')
        p.set_defaults(func=_cmd_knowledge)

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
    p.add_argument("--source", action="append", default=[])
    p.add_argument('--source-ref', action='append', default=[], help='imported source in the same store')
    p.add_argument('--knowledge-database', action='append', default=[], choices=['biomodels', 'sabiork'],
                   help='enable advisory public database search; query text is sent externally')
    p.add_argument("--experiment", help="required unless a frozen study/estimation plan supplies the experiment")
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
    p.add_argument('--memory-context', help='JSON of declared exact-match applicability labels; no automatic scientific inference')
    p.add_argument('--experience-proposals', action='store_true', help='opt-in post-run tool-derived candidate; never auto-approve/activate')
    p.add_argument("--study-plan-ref", help="operator-frozen adaptation study plan; requires hypothesis mode")
    p.add_argument('--estimation-requests', action='store_true', help='allow ordinary Agent to propose a separate estimation protocol, without fitting')
    p.add_argument('--estimation-plan-ref', help='operator-frozen separate estimation branch; ends for explicit result review')
    p.add_argument('--parameter-requirements', help='JSON file freezing parameter units and explicit context requirements')
    p.add_argument('--scientific-skills', action='store_true', help='enable frozen scientific procedures and artifact-based checks')
    p.add_argument('--failure-handling', action='store_true', help='enable bounded ODE failure repair, local investigation and stop routes')
    p.add_argument('--task-planning', action='store_true', help='enable bounded ODE task plans, dependencies and audited revisions')
    p.add_argument('--modeling-decisions', action='store_true', help='opt-in reproduction through explicit modeling plans; submit_draft is disabled')
    p.add_argument('--modeling-context', help='JSON of operator-frozen conditions for decision-plan evidence')
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

    p = sub.add_parser('agent-progress', help='show derived steps, gaps, parameter provenance and failure routes')
    _add_store(p)
    p.add_argument('ref', help='agent_run or agent_checkpoint reference')
    p.set_defaults(func=_cmd_agent_progress)

    for operation in ('propose', 'inspect', 'review', 'activate'):
        p = sub.add_parser('memory-candidate-' + operation, help='candidate experience: ' + operation)
        _add_store(p)
        p.add_argument('ref', help='agent_run, candidate, or approved review reference as appropriate')
        if operation == 'propose':
            p.add_argument('--project', required=True)
            p.add_argument('--author', required=True)
            p.add_argument('--applicability', required=True)
            p.add_argument('--conditions', help='JSON of explicit applicability labels')
        elif operation == 'review':
            p.add_argument('--decision', required=True, choices=['approved', 'rejected'])
            p.add_argument('--reviewer', required=True)
            p.add_argument('--reason', required=True)
        p.set_defaults(func=_cmd_memory_candidate, candidate_operation=operation)

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
    p.add_argument('--conditions', help='JSON of explicit applicability labels')
    p.add_argument('--depends-on', action='append', default=[], help='active advisory memory required by this entry')
    p.set_defaults(func=_cmd_memory_add)

    p = sub.add_parser('memory-search', help='search active memories in an explicit project and source scope')
    _add_store(p)
    p.add_argument('--project', required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--source-ref', action='append', default=[])
    p.add_argument('--model-ref')
    p.add_argument('--context', help='JSON of declared memory applicability labels')
    p.set_defaults(func=_cmd_memory_search)

    p = sub.add_parser('memory-retract', help='append a retraction without overwriting original memory')
    _add_store(p)
    p.add_argument('ref')
    p.add_argument('--author', required=True)
    p.add_argument('--reason', required=True)
    p.set_defaults(func=_cmd_memory_retract)
    p = sub.add_parser('memory-invalidate', help='append a project-scoped source/model/memory invalidation')
    _add_store(p); p.add_argument('ref'); p.add_argument('--project', required=True)
    p.add_argument('--author', required=True); p.add_argument('--reason', required=True)
    p.set_defaults(func=_cmd_memory_architecture)
    for command in ('experience-derive', 'experience-list'):
        p = sub.add_parser(command, help='idempotent post-run proposal derivation/list; no new inference')
        _add_store(p); p.add_argument('--run-ref', required=True); p.set_defaults(func=_cmd_memory_architecture)

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
