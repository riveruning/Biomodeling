"""Frozen study packages, research-run records, caching, and honest LLM-free replay.

Nothing here calls a model or a network.  A run record is either a *real*
execution, a *cache reuse* of an identical input object (explicitly marked), or
a *replay* of a frozen input (also explicitly marked).  Execution success is
never scientific success: ``scientific_status`` stays ``not_verified`` unless
the structured evidence genuinely supports a reference comparison, and
unchecked sources or unknown units can never yield a pass.
"""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import math

from . import evidence, modeling, simulation, validation, time_units
from .domain.contracts import (
    Check,
    ExperimentSpec,
    FrozenMap,
    ModelArtifact,
    ObservationSpec,
    SimulationResult,
    SolverConfig,
    ValidationReport,
)
from .domain import serialization

SIMULATION_INPUT_KIND = "simulation_input"
RUN_KIND = "run"

_MODULE_DIR = Path(__file__).resolve().parent

#: Dependency closure that defines the numerical protocol.
_PACKAGES = (
    "libroadrunner", "roadrunner", "python-libsbml", "libsbml",
    "numpy", "scipy", "pypdf", "biomodel-agent",
)


def _to_dict(store, ref):
    """Envelope payload of an object ref, or None when the ref is absent."""
    if ref is None:
        return None
    return store.get(ref)["payload"]


def environment_fingerprint() -> dict:
    """Python/platform/package versions plus a content hash of every module.

    This is not a git checkout, so no git commit is ever fabricated: the module
    hash below *is* the provenance of the running source.
    """
    import hashlib

    packages: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "absent"
        except Exception as exc:  # pragma: no cover - defensive
            packages[name] = f"unknown: {type(exc).__name__}"
    files: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in sorted(_MODULE_DIR.rglob("*.py")):
        raw = path.read_bytes()
        rel = path.relative_to(_MODULE_DIR).as_posix()
        files[rel] = hashlib.sha256(raw).hexdigest()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
    return {
        "python": sys.version.split()[0],
        "python_full": sys.version.replace("\n", " "),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "modules": files,
        "modules_sha256": digest.hexdigest(),
        "provenance": "content_hash_of_src_biomodel_agent_py (no git metadata available)",
    }


def _result_from_payload(payload: dict | None) -> SimulationResult | None:
    """Rebuild a frozen SimulationResult from an input payload, or None."""
    if payload is None:
        return None
    return serialization.from_dict(SimulationResult, payload)


def _obj_to_dict(obj) -> dict:
    if obj is None:
        return None
    return serialization.to_dict(obj)


def _input_payload(artifact, experiment, observations, solver, reference, scales,
                   threshold, strict, nonnegative) -> dict:
    return {
        "artifact": serialization.to_dict(artifact),
        "experiment": serialization.to_dict(experiment),
        "observations": [serialization.to_dict(o) for o in observations],
        "solver": serialization.to_dict(solver),
        "reference": _obj_to_dict(reference),
        "scales": dict(scales) if scales is not None else None,
        "threshold": float(threshold),
        "strict": bool(strict),
        "nonnegative": [str(n) for n in nonnegative],
        "environment": environment_fingerprint(),
    }


def _claim_refs(artifact) -> list[str]:
    """Claim refs cited by the artifact element map (never fabricated)."""
    from .storage import REF_RE

    found: list[str] = []
    for value in artifact.element_map.values():
        candidates = value if isinstance(value, (tuple, list)) else (value,)
        for item in candidates:
            if isinstance(item, str) and REF_RE.fullmatch(item) and item not in found:
                found.append(item)
    return found


def _convergence(artifact, experiment, observations, solver, coarse, scales, threshold):
    """Refine the solver 10x and compare with *fixed* scales.

    Returns ``(fine_result_or_None, diagnostics, effective_scales, note)``.  When
    no scales are supplied, per-column ``max|value|`` of the coarse run is used
    and declared as a numerical-stability scale, never as a paper baseline.
    """
    diag: dict[str, Any] = {}
    if coarse.status != 'succeeded':
        return None, {'status': 'not_run'}, None, 'coarse simulation failed'
    fine_solver = SolverConfig(rtol=solver.rtol / 10.0, atol=solver.atol / 10.0,
                               timeout_s=solver.timeout_s, max_steps=solver.max_steps)
    diag["requested"] = {"rtol": fine_solver.rtol, "atol": fine_solver.atol}
    diag["protocol"] = "numerical_stability_not_paper_baseline"
    try:
        fine = simulation.simulate(artifact, experiment, observations, fine_solver)
    except Exception as exc:
        diag["status"] = "error"
        diag["error"] = f"{type(exc).__name__}: {exc}"
        return None, diag, None, "fine simulation raised"
    diag["fine_status"] = fine.status
    if fine.status != "succeeded":
        diag["status"] = "unsupported_or_failed"
        diag["diagnostics"] = list(fine.diagnostics)
        return None, diag, None, "fine run did not succeed; convergence not asserted"
    if coarse.status != "succeeded":
        diag["status"] = "no_coarse_result"
        return None, diag, None, "coarse run did not succeed; convergence not asserted"
    columns = sorted(coarse.values.keys())
    effective = dict(scales) if scales is not None else {
        c: max((abs(float(v)) for v in coarse.values[c]), default=0.0) or 1.0 for c in columns
    }
    diag["scales_source"] = "provided" if scales is not None else "coarse_maxabs_per_variable"
    diag["coarse_maxabs"] = {
        c: max((abs(float(v)) for v in coarse.values[c]), default=0.0) for c in columns
    }
    try:
        outcome = validation.compare_series(fine, coarse, effective, float(threshold))
    except Exception as exc:
        diag["status"] = "error"
        diag["error"] = f"{type(exc).__name__}: {exc}"
        return fine, diag, effective, "convergence comparison failed"
    diag["status"] = outcome["status"]
    diag["comparison"] = outcome
    diag["declaration"] = (
        "scales are a numerical-stability scale, not the paper's reference scale"
        if scales is None else "scales were supplied by the caller"
    )
    return fine, diag, effective, "convergence compared on a fixed scale"


def _numeric_report(result, nonnegative, convergence_diag) -> ValidationReport:
    report = validation.validate_numeric(result, tuple(nonnegative))
    checks = list(report.checks)
    status = convergence_diag.get("status")
    if status in ("pass", "fail"):
        checks.append(Check(
            code="convergence", status=status,
            message=f"solver refinement (rtol/atol / 10): {convergence_diag.get('comparison', {}).get('max_scaled_error')!r} "
                    f"vs threshold {convergence_diag.get('comparison', {}).get('threshold')!r}",
            severity="blocking",
        ))
    else:
        checks.append(Check(
            code="convergence", status="unknown",
            message=f"convergence not established: {convergence_diag.get('status')!r}",
            severity="warning",
        ))
    return ValidationReport(tuple(checks))


def _sbml_targets(sbml: str) -> tuple[list[str], list[str]]:
    """Required source targets extracted from the SBML itself, plus unit notes.

    Targets are ``<id>/initial`` for every state/species, ``<id>/value`` for
    every constant parameter, ``rule:<target>/expression`` and
    ``reaction:<id>/rate``.  Deriving them from the document (not from
    ``element_map.keys()``) is what lets missing links be detected.
    """
    import libsbml

    doc = libsbml.readSBMLFromString(sbml)
    model = doc.getModel() if doc is not None else None
    if model is None:
        return [], ["model has no readable SBML model; required targets are unknown"]

    assignment_targets = {
        model.getListOfRules().get(i).getVariable()
        for i in range(model.getNumRules()) if model.getListOfRules().get(i).isAssignment()
    }
    rate_targets = {
        model.getListOfRules().get(i).getVariable()
        for i in range(model.getNumRules()) if model.getListOfRules().get(i).isRate()
    }
    targets: list[str] = []
    unit_notes: list[str] = []
    for i in range(model.getNumSpecies()):
        sp = model.getSpecies(i)
        targets.append(f"{sp.getId()}/initial")
        if not sp.isSetSubstanceUnits() and sp.getId() not in assignment_targets:
            unit_notes.append(f"species {sp.getId()!r}")
    for i in range(model.getNumParameters()):
        p = model.getParameter(i)
        if p.getId() in assignment_targets:
            continue                      # derived value, not an independent parameter
        if not p.getConstant() or p.getId() in rate_targets:
            targets.append(f"{p.getId()}/initial")
        else:
            targets.append(f"{p.getId()}/value")
        if not p.isSetUnits():
            unit_notes.append(f"parameter {p.getId()!r}")
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        if rule.isAlgebraic():
            unit_notes.append(f"algebraic rule for {rule.getVariable()!r}")
            continue
        targets.append(f"rule:{rule.getVariable()}/expression")
    for i in range(model.getNumReactions()):
        targets.append(f"reaction:{model.getReaction(i).getId()}/rate")
    for i in range(model.getNumCompartments()):
        comp = model.getCompartment(i)
        if not comp.isSetUnits():
            unit_notes.append(f"compartment {comp.getId()!r}")
    if not model.isSetTimeUnits():
        unit_notes.append("time")
    return sorted(dict.fromkeys(targets)), sorted(dict.fromkeys(unit_notes))


def _source_validation(store, artifact) -> dict:
    targets, unit_notes = _sbml_targets(artifact.sbml)
    links = evidence.validate_links(store, dict(artifact.element_map), targets)
    claims_review: dict[str, str] = {}
    source_refs = []
    disputed = []
    for target, value in artifact.element_map.items():
        for ref in (value if isinstance(value, (tuple, list)) else (value,)):
            if not isinstance(ref, str) or ref in claims_review:
                continue
            try:
                env = store.get(ref)
            except Exception:
                claims_review[ref] = "unreadable"
                continue
            if env.get("kind") == evidence.CLAIM_KIND and isinstance(env.get("payload"), dict):
                claims_review[ref] = env["payload"].get("review_status", "unknown")
                source_refs.append(env['payload']['source_ref'])
                if claims_review[ref] == 'disputed' or env['payload'].get('relation') == 'opposes':
                    disputed.append({'target':target,
                                     'kind':'disputed_claim','question':target+': resolve disputed source claim '+ref,
                                     'evidence':[{'claim_ref':ref}]})
    from .source_review import review_sources
    review = review_sources(store, source_refs, targets)
    review["issues"].extend(disputed)
    if review["issues"]:
        review["status"] = "requires_clarification"
    units_unknown = bool(unit_notes)
    human_reviewed = any(
        status in ("source_checked", "accepted_for_analysis") for status in claims_review.values()
    )
    return {
        "status": links["status"],
        "required_targets": targets,
        "coverage": links["coverage"],
        "missing": links["missing"],
        "invalid": links["invalid"],
        "semantic_support": links["semantic_support"],
        "claims_review_status": claims_review,
        "source_review": review,
        "human_review_present": human_reviewed,
        "units_unknown": units_unknown,
        "units_unknown_for": unit_notes,
        "units_status": "unknown" if units_unknown else "declared",
        "note": (
            "source_checked means the quote is faithful to the source text; it does not "
            "establish that the quoted claim supports the equation. Unknown units are not "
            "assumed dimensionless, so no math-level pass is claimed."
        ),
    }


def run_reproduction(store, artifact, experiment, observations=(), solver=None,
                     reference=None, scales=None, threshold=1e-3,
                     strict=False, use_cache=True, nonnegative=()) -> str:
    """Run (or reuse) a reproduction and store the run record; returns its ref."""
    if not isinstance(artifact, ModelArtifact):
        raise TypeError(f"artifact must be a ModelArtifact, got {type(artifact).__name__}")
    if not isinstance(experiment, ExperimentSpec):
        raise TypeError(f"experiment must be an ExperimentSpec, got {type(experiment).__name__}")
    observations = tuple(
        o if isinstance(o, ObservationSpec) else ObservationSpec(**o) for o in observations
    )
    solver = solver if solver is not None else SolverConfig()
    if not isinstance(solver, SolverConfig):
        raise TypeError(f"solver must be a SolverConfig, got {type(solver).__name__}")
    if reference is not None and not isinstance(reference, SimulationResult):
        raise TypeError(f"reference must be a SimulationResult or None, got {type(reference).__name__}")
    if scales is not None:
        if not isinstance(scales, dict):
            raise TypeError(f"scales must be a mapping or None, got {type(scales).__name__}")
        scales = {str(k): float(v) for k, v in scales.items()}
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError(f"threshold must be a positive number, got {threshold!r}")

    clock = time_units.check_experiment_time(artifact.sbml, experiment.time_unit)
    if clock.status == 'fail':
        raise ValueError(clock.message)
    source_preflight = _source_validation(store, artifact)
    if strict and source_preflight['source_review']['issues']:
        raise ValueError('Unresolved source conflicts: ' + '; '.join(
            item['question'] for item in source_preflight['source_review']['issues']))

    payload = _input_payload(artifact, experiment, observations, solver, reference,
                             scales, threshold, strict, nonnegative)
    input_ref = store.put(SIMULATION_INPUT_KIND, payload, refs=tuple(_claim_refs(artifact)))
    environment = payload["environment"]

    # --- cache: only a *successful, numerically passing* run of this input ---
    if use_cache:
        for candidate_ref in reversed(store.list(RUN_KIND)):
            try:
                candidate = store.get(candidate_ref)["payload"]
            except Exception:
                continue
            if not isinstance(candidate, dict) or candidate.get("input_ref") != input_ref:
                continue
            if candidate.get("mode") != ("strict_reproduction" if strict else "structured_execution"):
                continue
            if candidate.get("status") != "succeeded":
                continue
            numeric = candidate.get("numeric_validation") or {}
            checks = numeric.get("checks") if isinstance(numeric, dict) else None
            if not checks or any(c.get('status') == 'fail' and c.get('severity') == 'blocking' for c in checks):
                continue
            if not all(any(c.get('code') == code and c.get('status') == 'pass' for c in checks)
                       for code in ('finite', 'convergence')):
                continue
            cached = {
                'run_id': str(uuid4()),
                "cached_from": candidate_ref,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cache_key": input_ref,
                "note": "reused successful numeric-pass run; this is NOT an independent reproduction",
                "input_ref": input_ref,
                "status": candidate["status"],
                "simulation": candidate.get("simulation"),
                "static_validation": candidate.get("static_validation"),
                "numeric_validation": candidate.get("numeric_validation"),
                "source_validation": candidate.get("source_validation"),
                "comparison": candidate.get("comparison") or {"status": "not_run"},
                "convergence_ref": candidate.get("convergence_ref"),
                "environment": environment,
                "mode": candidate["mode"],
                "scientific_status": candidate.get("scientific_status", "not_verified"),
            }
            return store.put(RUN_KIND, cached, refs=(input_ref, candidate_ref))

    # --- real execution -----------------------------------------------------
    static = modeling.validate_artifact(artifact)
    static = ValidationReport((*static.checks, clock))
    try:
        result = simulation.simulate(artifact, experiment, observations, solver)
    except Exception as exc:
        result = SimulationResult(
            status="failed",
            checks=ValidationReport((Check(code="finite", status="fail",
                                           message=f"simulate raised {type(exc).__name__}: {exc}"),)),
            diagnostics=(f"application: {type(exc).__name__}: {exc}",),
            backend="none", elapsed_s=0.0,
        )
    fine, conv_diag, effective_scales, conv_note = _convergence(
        artifact, experiment, observations, solver, result, scales, float(threshold))
    numeric = _numeric_report(result, nonnegative, conv_diag)

    comparison: dict = {"status": "not_run"}
    if result.status == "succeeded" and reference is not None:
        scales_for_compare = dict(scales) if scales is not None else None
        if scales_for_compare is None:
            comparison = {
                "status": "not_run",
                "reason": "no fixed scales supplied; comparing without a declared scale would "
                          "imply a baseline the caller never gave",
            }
        else:
            try:
                outcome = validation.compare_series(result, reference, scales_for_compare, float(threshold))
                comparison = {"status": outcome["status"], "protocol": "fixed_scales",
                              "max_scaled_error": outcome["max_scaled_error"],
                              "threshold": outcome["threshold"], "scales": outcome["scales"],
                              "worst": outcome["worst"], "columns": outcome["columns"]}
            except Exception as exc:
                comparison = {"status": "not_run",
                              "reason": f"comparison failed: {type(exc).__name__}: {exc}"}
    elif reference is None:
        comparison = {"status": "not_run",
                      "reason": "no reference result: self-comparison is not a reproduction"}

    source = source_preflight
    convergence_ref = None
    if fine is not None:
        convergence_ref = store.put("simulation_result", {
            "role": "convergence_fine_run", "diagnostics": conv_diag,
            "declaration": conv_note, "result": serialization.to_dict(fine),
        }, refs=(input_ref,))

    scientific = "not_verified"
    if result.status != "succeeded":
        scientific = "failed"
    else:
        numeric_ok = all(c.status == 'pass' for c in numeric.checks if c.severity == 'blocking')
        sources_ok = (
            source["status"] == "complete"
            and source["semantic_support"] != "unknown"
            and not source["units_unknown"]
        )
        if strict:
            if not sources_ok or comparison.get("status") != "pass" or not numeric_ok:
                scientific = "not_verified"
            else:
                scientific = "passed_structured_reference"
        elif comparison.get("status") == "pass" and numeric_ok:
            scientific = "passed_structured_reference"  # numerical track, not paper fidelity
        elif comparison.get('status') == 'fail' or not numeric_ok:
            scientific = 'failed'

    run = {
        'run_id': str(uuid4()),
        "input_ref": input_ref,
        "status": result.status,
        "simulation": serialization.to_dict(result),
        "static_validation": {"checks": [serialization.to_dict(c) for c in static.checks],
                              "can_execute": static.can_execute},
        "numeric_validation": {"checks": [serialization.to_dict(c) for c in numeric.checks]},
        "source_validation": source,
        "comparison": comparison,
        "convergence_ref": convergence_ref,
        "convergence": conv_diag,
        "cache_key": input_ref,
        "environment": environment,
        "cached_from": None,
        "mode": "strict_reproduction" if strict else "structured_execution",
        "scientific_status": scientific,
        "replay_of": None,
    }
    refs = [input_ref] + ([convergence_ref] if convergence_ref else [])
    return store.put(RUN_KIND, run, refs=tuple(refs))


def replay_run(store, run_ref: str) -> str:
    """Re-run a frozen input with caching disabled; no LLM, no cache reuse."""
    envelope = store.get(run_ref)
    if envelope.get("kind") != RUN_KIND:
        raise ValueError(f"{run_ref} is not a run record (kind={envelope.get('kind')!r})")
    original = envelope["payload"]
    input_ref = original.get("input_ref")
    if not isinstance(input_ref, str):
        raise ValueError(f"run {run_ref} has no frozen input_ref")
    frozen = store.get(input_ref)["payload"]

    artifact = serialization.from_dict(ModelArtifact, frozen["artifact"])
    experiment = serialization.from_dict(ExperimentSpec, frozen["experiment"])
    observations = tuple(
        serialization.from_dict(ObservationSpec, o) for o in frozen.get("observations") or []
    )
    solver = serialization.from_dict(SolverConfig, frozen["solver"])
    reference = _result_from_payload(frozen.get("reference"))
    scales = dict(frozen["scales"]) if frozen.get("scales") else None
    threshold = float(frozen["threshold"])
    strict = bool(frozen.get("strict"))
    nonnegative = tuple(frozen.get("nonnegative") or ())

    new_ref = run_reproduction(
        store, artifact, experiment, observations, solver, reference, scales,
        threshold, strict, use_cache=False, nonnegative=nonnegative,
    )
    new_env = store.get(new_ref)["payload"]["environment"]
    old_env = frozen.get("environment") or {}
    differences = {
        key: {"recorded": old_env.get(key), "current": new_env.get(key)}
        for key in sorted(set(old_env) | set(new_env))
        if old_env.get(key) != new_env.get(key)
    }
    record = dict(store.get(new_ref)["payload"])
    record["replay_of"] = run_ref
    record["environment_matches_recorded"] = not differences
    record["environment_differences"] = differences
    return store.put(RUN_KIND, record, refs=(input_ref, run_ref, new_ref))
