"""Audit T01 acceptance: explicit failure verdicts beat unknown states.

Covers the shared run verdict (application.derive_scientific_status /
blocking_verdict_basis / initial_scientific_status) through the *real* chain:
strict-mode numeric failures, real reference mismatch, controlled convergence
failure, unknown-check negative controls, legitimate source-unknown controls,
old records whose stored status conflicts with their stored facts, and the
Agent / CLI consumers of the same verdict.  All real solves use the project's
own SBML/scientific tooling; no network.
"""
import math

import pytest

from biomodel_agent import agent, application, modeling
from biomodel_agent.cli import _print_run_result
from biomodel_agent.domain.contracts import (
    ExperimentSpec,
    ModelDraft,
    Parameter,
    RateRule,
    SimulationResult,
    State,
)
from biomodel_agent.storage import ArtifactStore

NEGATIVE_SBML = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m_negative" name="m_negative">
    <listOfCompartments><compartment id="c" constant="true" size="1.0"/></listOfCompartments>
    <listOfSpecies>
      <species id="x" compartment="c" initial="1.0" hasOnlySubstanceUnits="false" boundaryCondition="false" constant="false"/>
    </listOfSpecies>
    <listOfParameters><parameter id="k" value="0.4" constant="true"/></listOfParameters>
    <listOfReactions><reaction id="r" reversible="false">
      <listOfReactants><speciesReference species="x" stoichiometry="1" constant="true"/></listOfReactants>
      <kineticLaw>
        <formula>k</formula>
        <listOfLocalParameters><localParameter id="k" value="k"/></listOfLocalParameters>
      </kineticLaw>
    </reaction></listOfReactions>
  </model>
</sbml>
"""

DECAY_SBML = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m_decay" name="m_decay">
    <listOfCompartments><compartment id="c" constant="true" size="1.0"/></listOfCompartments>
    <listOfSpecies>
      <species id="x" compartment="c" initial="1.0" hasOnlySubstanceUnits="false" boundaryCondition="false" constant="false"/>
    </listOfSpecies>
    <listOfParameters><parameter id="d" value="0.5" constant="true"/></listOfParameters>
    <listOfReactions><reaction id="r" reversible="false">
      <listOfReactants><speciesReference species="x" stoichiometry="1" constant="true"/></listOfReactants>
      <kineticLaw>
        <formula>d * x</formula>
        <listOfLocalParameters><localParameter id="d" value="d"/></listOfLocalParameters>
      </kineticLaw>
    </reaction></listOfReactions>
  </model>
</sbml>
"""


def _artifact():
    return modeling.build_model(
        ModelDraft("neg_const", (State("x", 1.0),), (Parameter("k", 0.4),),
                   rate_rules=(RateRule("x", "-k"),)))


def _decay_artifact():
    return modeling.build_model(
        ModelDraft("decay", (State("x", 1.0),), (Parameter("d", 0.5),),
                   rate_rules=(RateRule("x", "-d*x"),)))


def _numeric_checks(payload):
    return {c["code"]: c for c in (payload.get("numeric_validation") or {}).get("checks", [])}


# ---------------------------------------------------------------------------
# Real-chain strict-mode numeric failure: the regression case
# ---------------------------------------------------------------------------

def test_strict_nonneg_failure_is_failed_not_masked(tmp_path):
    """x(0)=1 with constant drain k=0.4 over [0,4] reaches -0.6: the strict
    verdict must be 'failed' (with a numeric basis), never 'not_verified',
    and the stored record must agree with the derived verdict."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 4.0))
    ref = application.run_reproduction(
        store, _artifact(), exp, strict=True, nonnegative=("x",),
        use_cache=False)
    payload = store.get(ref)["payload"]
    assert payload["status"] == "succeeded"
    assert payload["mode"] == "strict_reproduction"
    numeric = _numeric_checks(payload)
    assert numeric["nonnegative"]["status"] == "fail"
    assert numeric["nonnegative"]["severity"] == "blocking"
    # The regression: strict must NOT collapse an explicit numeric failure
    # into not_verified.
    assert application.derive_scientific_status(payload) == "failed"
    assert payload["scientific_status"] == "failed"
    basis = application.blocking_verdict_basis(payload)
    assert "numeric" in basis and any("nonnegative" in item for item in basis["numeric"])
    # The same record must fail the CLI and the Agent gates.
    assert _print_run_result(ref, store, strict=True) == 1
    # Convergence check must not have silently turned the verdict unknown:
    # unknown checks exist only for convergence-style non-failure gaps.
    assert numeric["convergence"]["status"] in ("pass", "fail", "unknown")


def test_strict_reference_mismatch_is_failed(tmp_path):
    """Real solve of a first-order decay vs a reference that disagrees by a
    factor of 10: strict verdict must be 'failed' on the comparison basis."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 1.0, 2.0))
    true_values = tuple(math.exp(-0.5 * t) for t in exp.times)
    wrong_ref = SimulationResult("succeeded", exp.times, {"x": tuple(10.0 * v for v in true_values)})
    ref = application.run_reproduction(
        store, _decay_artifact(), exp, reference=wrong_ref, scales={"x": 1.0},
        strict=True, use_cache=False)
    payload = store.get(ref)["payload"]
    assert payload["status"] == "succeeded"
    assert payload["comparison"]["status"] == "fail"
    assert application.derive_scientific_status(payload) == "failed"
    assert payload["scientific_status"] == "failed"
    assert "comparison" in application.blocking_verdict_basis(payload)
    assert _print_run_result(ref, store, strict=True) == 1


def test_controlled_convergence_failure_is_failed(tmp_path):
    """A convergence check that genuinely fails (blocking) must drive the
    verdict to 'failed' even though the raw execution succeeded and the
    nonnegativity check passed.  A near-degenerate solver (very loose rtol)
    forces the 10x refinement to disagree with the coarse run."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 1.0))
    from biomodel_agent.domain.contracts import SolverConfig

    def simulate_stub(artifact, experiment, observations, solver):
        if solver.rtol < 1e-7:
            return SimulationResult(
                "succeeded", experiment.times,
                {"x": tuple(2.0 * math.exp(-0.5 * t) for t in experiment.times)})
        return SimulationResult(
            "succeeded", experiment.times,
            {"x": tuple(math.exp(-0.5 * t) for t in experiment.times)})

    monkey = pytest.MonkeyPatch()
    monkey.setattr(application.simulation, "simulate", simulate_stub)
    try:
        ref = application.run_reproduction(
            store, _decay_artifact(), exp, strict=True,
            solver=SolverConfig(rtol=1.0), use_cache=False)
    finally:
        monkey.undo()
    payload = store.get(ref)["payload"]
    assert payload["status"] == "succeeded"
    numeric = _numeric_checks(payload)
    assert numeric["convergence"]["status"] == "fail"
    assert numeric["convergence"]["severity"] == "blocking"
    assert application.derive_scientific_status(payload) == "failed"
    assert payload["scientific_status"] == "failed"
    assert "numeric" in application.blocking_verdict_basis(payload)
    assert _print_run_result(ref, store, strict=True) == 1


# ---------------------------------------------------------------------------
# Negative controls: unknowns stay unknown, never promoted, never auto-failed
# ---------------------------------------------------------------------------

def test_unknown_checks_stay_not_verified_and_cli_exit_2(tmp_path):
    """Succeeded run, no explicit failure, but convergence unknown and source
    not fully supported: verdict is 'not_verified' (not 'failed', not pass);
    strict CLI exits 2 (unverified), non-strict exits 0 (legacy behavior)."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 1.0))
    ref = application.run_reproduction(
        store, _decay_artifact(), exp, strict=True, use_cache=False)
    payload = store.get(ref)["payload"]
    assert payload["status"] == "succeeded"
    numeric = _numeric_checks(payload)
    assert numeric["convergence"]["status"] in ("unknown", "fail")
    if numeric["convergence"]["status"] == "fail":
        # Environment-dependent: solver refinement failed; still must be failed.
        assert application.derive_scientific_status(payload) == "failed"
        return
    # No explicit failure at all: the unknown convergence must NOT be a fail.
    assert application.derive_scientific_status(payload) == "not_verified"
    assert application.blocking_verdict_basis(payload) == {}
    assert payload["scientific_status"] != "passed_structured_reference"
    assert _print_run_result(ref, store, strict=True) == 2
    assert _print_run_result(ref, store, strict=False) == 0


def test_source_unknown_controls_stay_not_verified(tmp_path):
    """Legitimate source-unknown behavior is preserved: a strict run with an
    incomplete source validation keeps not_verified (never pass, never fail),
    while the non-strict numerical pass label is unchanged."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 0.3, 2.0))
    ref_result = SimulationResult("succeeded", exp.times,
                                  {"x": tuple(math.exp(-0.5 * t) for t in exp.times)})
    strict_ref = application.run_reproduction(
        store, _decay_artifact(), exp, reference=ref_result, scales={"x": 1.0},
        strict=True, use_cache=False)
    sp = store.get(strict_ref)["payload"]
    assert sp["comparison"]["status"] == "pass"
    assert sp["source_validation"]["missing"]  # no source links in this artifact
    assert sp["scientific_status"] == "not_verified"
    assert application.derive_scientific_status(sp) == "not_verified"
    assert application.blocking_verdict_basis(sp) == {}
    assert _print_run_result(strict_ref, store, strict=True) == 2

    norm_ref = application.run_reproduction(
        store, _decay_artifact(), exp, reference=ref_result, scales={"x": 1.0},
        strict=False, use_cache=False)
    np_ = store.get(norm_ref)["payload"]
    assert np_["comparison"]["status"] == "pass"
    assert np_["scientific_status"] == "passed_structured_reference"
    assert _print_run_result(norm_ref, store, strict=False) == 0
    # Unknowns never promote: strict re-use of the same input cannot become pass.
    assert application.derive_scientific_status(np_) == "not_verified"


# ---------------------------------------------------------------------------
# Old records: stored status conflicts with stored facts
# ---------------------------------------------------------------------------

def test_stored_not_verified_with_blocking_numeric_failure_is_failed(tmp_path):
    """A saved record claiming 'not_verified' while its stored numeric checks
    show a blocking nonnegativity failure must be re-deriving to 'failed' --
    the stored string must not override the facts."""
    record = {
        "status": "succeeded",
        "scientific_status": "not_verified",
        "static_validation": {"checks": [], "can_execute": True},
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
            {"code": "nonnegative", "status": "fail", "severity": "blocking",
             "message": "'x' reached -0.6 < -1e-09"},
            {"code": "conservation", "status": "not_run", "severity": "informational", "message": ""},
            {"code": "convergence", "status": "unknown", "severity": "warning", "message": "not run"},
        ]},
        "comparison": {"status": "not_run"},
        "source_validation": {"status": "incomplete", "semantic_support": "unknown"},
    }
    assert application.derive_scientific_status(record) == "failed"
    basis = application.blocking_verdict_basis(record)
    assert "numeric" in basis and "not_run" not in str(list(basis))
    # Unknown convergence and not_run conservation must not appear as failures.
    joined = " ".join(item for items in basis.values() for item in items)
    assert "convergence" not in joined and "conservation" not in joined


def test_stored_failed_with_unknown_facts_is_failed(tmp_path):
    """A stored scientific_status of 'failed' with otherwise unknown/absent
    facts is authoritative: it must not be downgraded to not_verified."""
    record = {"status": "succeeded", "scientific_status": "failed",
              "numeric_validation": {"checks": []},
              "comparison": {"status": "not_run"}}
    assert application.derive_scientific_status(record) == "failed"
    assert "scientific" in application.blocking_verdict_basis(record)


def test_stored_pass_with_blocking_static_failure_is_failed(tmp_path):
    """A stored 'passed_structured_reference' whose stored static report shows
    a blocking failure is re-derived to 'failed'."""
    record = {
        "status": "succeeded",
        "scientific_status": "passed_structured_reference",
        "static_validation": {"checks": [
            {"code": "sbml", "status": "fail", "severity": "blocking", "message": "unreadable model"},
        ]},
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
        ]},
        "comparison": {"status": "pass"},
    }
    assert application.derive_scientific_status(record) == "failed"
    assert "static" in application.blocking_verdict_basis(record)


def test_execution_status_failure_is_failed(tmp_path):
    """Execution not succeeded is a failure regardless of other facts."""
    assert application.derive_scientific_status({"status": "failed"}) == "failed"
    assert application.derive_scientific_status({"status": "timed_out"}) == "failed"
    assert application.derive_scientific_status({"status": "unsupported"}) == "failed"


def test_clean_succeeded_record_stays_not_verified(tmp_path):
    """No explicit failure anywhere -> not_verified (conservative, never pass)."""
    record = {
        "status": "succeeded",
        "scientific_status": "not_verified",
        "static_validation": {"checks": []},
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
            {"code": "convergence", "status": "unknown", "severity": "warning", "message": ""},
        ]},
        "comparison": {"status": "not_run"},
    }
    assert application.derive_scientific_status(record) == "not_verified"
    assert application.blocking_verdict_basis(record) == {}


def test_cache_reuse_cannot_remask_explicit_failure(tmp_path):
    """Re-derive path: a candidate that passed the numeric pre-filter but whose
    stored static facts contain a blocking failure must not be cached as
    'not_verified' — the derived verdict is 'failed'."""
    store = ArtifactStore(tmp_path / "store")
    exp = ExperimentSpec((0.0, 1.0))
    candidate = {
        "run_id": "cand", "input_ref": None, "mode": "strict_reproduction",
        "status": "succeeded",
        "static_validation": {"checks": [
            {"code": "sbml", "status": "fail", "severity": "blocking", "message": "bad"},
        ], "can_execute": False},
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
            {"code": "convergence", "status": "pass", "severity": "blocking", "message": ""},
        ]},
        "comparison": {"status": "pass"},
        "scientific_status": "not_verified",
    }
    assert application.derive_scientific_status(candidate) == "failed"
    # The cache pre-filter on numeric checks alone passes this candidate:
    checks = candidate["numeric_validation"]["checks"]
    assert not any(c["status"] == "fail" and c["severity"] == "blocking" for c in checks)
    assert all(any(c["code"] == code and c["status"] == "pass" for c in checks)
               for code in ("finite", "convergence"))
    # ...yet the derived verdict refuses to mask the static failure:
    assert application.derive_scientific_status({
        "status": candidate["status"],
        "static_validation": candidate["static_validation"],
        "numeric_validation": candidate["numeric_validation"],
        "comparison": candidate["comparison"],
        "scientific_status": candidate["scientific_status"],
    }) == "failed"


# ---------------------------------------------------------------------------
# Agent completion gate reuses the shared verdict
# ---------------------------------------------------------------------------

class _Scripted:
    last_usage = {"total_tokens": 0}

    def __init__(self, actions):
        self.actions = iter(actions)

    def respond(self, messages):
        return next(self.actions)


def test_agent_finish_incomplete_on_stored_failed(tmp_path):
    """Agent finish: stored scientific_status='failed' (with no underlying
    facts) still blocks completion via the shared verdict."""
    store = ArtifactStore(tmp_path / "store")
    failed_ref = store.put(
        "run",
        {"status": "succeeded", "scientific_status": "failed",
         "comparison": {"status": "not_run"},
         "numeric_validation": {"checks": []},
         "static_validation": {"checks": []}})
    actions = [
        {"action": "submit_draft", "args": {"draft": {}, "evidence": []}},
        {"action": "run", "args": {}},
        {"action": "finish", "args": {}},
    ]

    def fake_run_reproduction(*a, **kw):
        return failed_ref

    monkey = pytest.MonkeyPatch()
    monkey.setattr(application, "run_reproduction", fake_run_reproduction)
    try:
        ref = agent.run_agent(store, _Scripted(actions), [], ExperimentSpec((0.0, 1.0)))
    finally:
        monkey.undo()
    payload = store.get(ref)["payload"]
    assert payload["state"] == "incomplete"


def test_agent_finish_incomplete_on_numeric_failure_facts(tmp_path):
    """Agent finish: even if the stored scientific_status string were the
    non-failure 'not_verified', the stored blocking numeric failure must keep
    the agent from completing."""
    store = ArtifactStore(tmp_path / "store")
    ref = application.run_reproduction(
        store, _artifact(), ExperimentSpec((0.0, 4.0)), strict=True,
        nonnegative=("x",), use_cache=False)
    payload = store.get(ref)["payload"]
    assert payload["scientific_status"] == "failed"
    actions = [
        {"action": "submit_draft", "args": {"draft": {}, "evidence": []}},
        {"action": "run", "args": {}},
        {"action": "finish", "args": {}},
    ]

    def fake_run_reproduction(*a, **kw):
        return ref

    monkey = pytest.MonkeyPatch()
    monkey.setattr(application, "run_reproduction", fake_run_reproduction)
    try:
        agent_ref = agent.run_agent(store, _Scripted(actions), [], ExperimentSpec((0.0, 4.0)))
    finally:
        monkey.undo()
    record = store.get(agent_ref)["payload"]
    assert record["state"] == "incomplete"


# ---------------------------------------------------------------------------
# CLI exit codes reuse the shared verdict
# ---------------------------------------------------------------------------

def test_cli_exit_codes_for_failed_and_unverified(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    # Explicit failure (comparison fail): exit 1 in both modes.
    failed = store.put(
        "run",
        {"status": "succeeded", "scientific_status": "not_verified",
         "comparison": {"status": "fail", "worst": "x[2]"},
         "numeric_validation": {"checks": [
             {"code": "finite", "status": "pass", "severity": "blocking", "message": ""}]},
         "static_validation": {"checks": []}})
    assert _print_run_result(failed, store, strict=True) == 1
    assert _print_run_result(failed, store, strict=False) == 1

    # Not succeeded: exit 1.
    crashed = store.put("run", {"status": "failed", "scientific_status": "failed"})
    assert _print_run_result(crashed, store, strict=False) == 1

    # Strict and genuinely unverified (no explicit failure): exit 2.
    unverified = store.put(
        "run",
        {"status": "succeeded", "scientific_status": "not_verified",
         "comparison": {"status": "not_run"},
         "numeric_validation": {"checks": [
             {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
             {"code": "convergence", "status": "unknown", "severity": "warning", "message": ""}]},
         "static_validation": {"checks": []}})
    assert _print_run_result(unverified, store, strict=True) == 2
    assert _print_run_result(unverified, store, strict=False) == 0

    # Legitimate ordinary unverified keeps original behavior: strict exit 2,
    # non-strict exit 0.
    ordinary = store.put(
        "run",
        {"status": "succeeded", "scientific_status": "not_verified",
         "comparison": {"status": "not_run"},
         "numeric_validation": {"checks": [
             {"code": "finite", "status": "pass", "severity": "blocking", "message": ""}]},
         "static_validation": {"checks": []}})
    assert _print_run_result(ordinary, store, strict=True) == 2
    assert _print_run_result(ordinary, store, strict=False) == 0


# ---------------------------------------------------------------------------
# Shared verdict unit checks (direct, no I/O)
# ---------------------------------------------------------------------------

def test_verdict_priority_and_basis_shape():
    from biomodel_agent.application import (
        Check,
        ValidationReport,
        blocking_verdict_basis,
        derive_scientific_status,
        initial_scientific_status,
    )

    ok_source = {"status": "complete", "semantic_support": "supported", "units_unknown": False}
    bad_source = {"status": "incomplete", "semantic_support": "unknown", "units_unknown": True}

    # 1. explicit failure beats everything, including a pass-looking comparison.
    r1 = derive_scientific_status({
        "status": "succeeded", "scientific_status": "not_verified",
        "static_validation": {"checks": []},
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""},
            {"code": "nonnegative", "status": "fail", "severity": "blocking", "message": "neg"}]},
        "comparison": {"status": "pass"},
    })
    assert r1 == "failed"

    # 2. no failure, missing provenance -> not_verified, empty basis.
    r2 = derive_scientific_status({
        "status": "succeeded", "scientific_status": "not_verified",
        "numeric_validation": {"checks": [
            {"code": "finite", "status": "pass", "severity": "blocking", "message": ""}]},
        "comparison": {"status": "not_run"},
    })
    assert r2 == "not_verified" and blocking_verdict_basis({"status": "succeeded"}) == {}

    # 3. non-blocking/warning fails are not failures.
    r3 = derive_scientific_status({
        "status": "succeeded",
        "numeric_validation": {"checks": [
            {"code": "convergence", "status": "fail", "severity": "warning", "message": ""}]},
        "comparison": {"status": "not_run"},
    })
    assert r3 == "not_verified"

    # 4. initial status: strict pass requires real comparison + required
    #    numeric pass + supported sources; unknown source -> not_verified.
    numeric_ok = ValidationReport((
        Check("finite", "pass", severity="blocking"),
        Check("convergence", "pass", severity="blocking"),
    ))
    assert initial_scientific_status("succeeded", (), numeric_ok, "pass", ok_source, True) == "passed_structured_reference"
    assert initial_scientific_status("succeeded", (), numeric_ok, "pass", bad_source, True) == "not_verified"
    assert initial_scientific_status("succeeded", (), numeric_ok, "not_run", ok_source, True) == "not_verified"
    # Normal mode: numerical pass label; reference fail is a failure.
    assert initial_scientific_status("succeeded", (), numeric_ok, "pass", bad_source, False) == "passed_structured_reference"
    assert initial_scientific_status("succeeded", (), numeric_ok, "fail", ok_source, False) == "failed"
    # Execution failure dominates.
    assert initial_scientific_status("failed", (), (), "pass", ok_source, True) == "failed"