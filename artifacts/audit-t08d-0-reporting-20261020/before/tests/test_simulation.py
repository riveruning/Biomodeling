"""Spawned libRoadRunner simulation: protocol, overrides, segments, validation.

Every assertion here is against the real libRoadRunner backend and against the
analytic solution of the model under test.  Nothing is stubbed or monkeypatched.
"""

from __future__ import annotations

import math

import pytest

from biomodel_agent import modeling, validation
from biomodel_agent.domain.contracts import (
    Compartment,
    Derived,
    ExperimentSpec,
    FrozenMap,
    InputSegment,
    ModelArtifact,
    ModelDraft,
    ObservationSpec,
    Parameter,
    RateRule,
    Reaction,
    SimulationResult,
    SolverConfig,
    State,
)
from biomodel_agent.simulation import simulate, _inspect_sbml


def birth_death(volume: float = 1.0, kind: str = "amount") -> ModelArtifact:
    """Analytic dx/dt = k - d*x on the declared quantity kind."""
    draft = ModelDraft(
        id="birth_death",
        states=(State(id="X", initial=1.0, quantity_kind=kind),),
        parameters=(Parameter(id="k", value=2.0), Parameter(id="d", value=1.0)),
        compartment=Compartment(id="cell", size=volume),
        reactions=(Reaction(id="birth", reactants={}, products={"X": 1.0}, rate="k"),
                   Reaction(id="death", reactants={"X": 1.0}, products={}, rate="d*X")),
    )
    return modeling.build_model(draft)


def rate_rule_birth_death() -> ModelArtifact:
    """dx/dt = k - d*x as a genuine rate rule: X is a non-constant parameter.

    This is the rate-rule counterpart of :func:`birth_death`: the state has no
    SBML species, so ``rr['[X]']`` does not exist and the value must be read
    through ``rr['X']``.
    """
    draft = ModelDraft(
        id="rate_rule_birth_death",
        states=(State(id="X", initial=0.0, quantity_kind="variable"),),
        parameters=(Parameter(id="k", value=2.0), Parameter(id="d", value=1.0)),
        compartment=Compartment(id="cell", size=1.0),
        rate_rules=(RateRule(state="X", expression="k - d*X"),),
    )
    return modeling.build_model(draft)


def test_unknown_unit_stays_unknown():
    draft = ModelDraft(
        id="unit_model",
        states=(State(id="X", initial=1.0, unit="furlong", quantity_kind="amount"),),
        parameters=(Parameter(id="k", value=0.0, unit="smoot"),),
        reactions=(Reaction(id="R", reactants={"X": 1.0}, products={}, rate="k"),),
    )
    report = modeling.validate_draft(draft)
    warnings = [c for c in report.checks if c.code == "unit_unknown"]
    assert warnings and all(c.status == "unknown" for c in warnings)
    assert report.can_execute  # unknown unit is a warning, never faked as dimensionless
    artifact = modeling.build_model(draft)
    assert "furlong" not in artifact.sbml and "smoot" not in artifact.sbml
    _inspect_sbml(artifact.sbml)  # libSBML-only inspection, no roadrunner import


def test_irregular_times_and_overrides_analytic():
    artifact = birth_death()
    times = (0.0, 0.3, 1.7, 4.0)
    experiment = ExperimentSpec(
        times=times,
        initial_overrides=FrozenMap({"X": 0.5}),
        parameter_overrides=FrozenMap({"d": 2.0}),
    )
    result = simulate(artifact, experiment, (ObservationSpec(id="double", expression="2*X"),))
    assert result.status == "succeeded", result.diagnostics
    assert result.times == times
    assert set(result.values) == {"X", "double"}
    for t, x, dbl in zip(result.times, result.values["X"], result.values["double"]):
        expected = 1.0 + (0.5 - 1.0) * math.exp(-2.0 * t)
        assert abs(x - expected) < 1e-5
        assert abs(dbl - 2 * x) < 1e-9
    report = validation.validate_numeric(result, nonnegative=("X",))
    assert report.can_execute


def test_observation_is_appended_exactly_once_per_sample():
    """Columns alias length one-to-one with times; observations use a real symbol."""
    artifact = birth_death()
    times = (0.0, 0.5, 1.25, 3.0)
    observations = (ObservationSpec(id="o1", expression="X + k"), ObservationSpec(id="o2", expression="d"))
    result = simulate(artifact, experiment=ExperimentSpec(times=times), observations=observations)
    assert result.status == "succeeded", result.diagnostics
    assert result.times == times
    assert set(result.values) == {"X", "o1", "o2"}
    for column in result.values.values():
        assert len(column) == len(times)
    for x, o1 in zip(result.values["X"], result.values["o1"]):
        assert abs(o1 - (x + 2.0)) < 1e-9
    assert all(v == 1.0 for v in result.values["o2"])


def test_concentration_initial_override_is_scaled_by_volume():
    artifact = birth_death(volume=2.0, kind="concentration")
    experiment = ExperimentSpec(times=(0.0, 1.0), initial_overrides=FrozenMap({"X": 0.25}))
    result = simulate(artifact, experiment)
    assert result.status == "succeeded", result.diagnostics
    assert abs(result.values["X"][0] - 0.25) < 1e-9


def test_amount_and_concentration_read_the_right_symbol():
    """amount: rr[X] in moles; concentration: rr['[X]'] = amount / volume."""
    amount = simulate(birth_death(volume=2.0, kind="amount"),
                      ExperimentSpec(times=(0.0,), initial_overrides=FrozenMap({"X": 3.0})))
    conc = simulate(birth_death(volume=2.0, kind="concentration"),
                    ExperimentSpec(times=(0.0,), initial_overrides=FrozenMap({"X": 3.0})))
    assert amount.status == "succeeded" and conc.status == "succeeded"
    assert abs(amount.values["X"][0] - 3.0) < 1e-9   # substance units: the amount itself
    assert abs(conc.values["X"][0] - 3.0) < 1e-9     # species amount == conc * volume


def test_segment_is_right_continuous_in_parameters():
    artifact = birth_death()
    experiment = ExperimentSpec(
        times=(0.0, 1.0, 2.0),
        segments=(InputSegment(time=1.0, values=FrozenMap({"k": 0.0})),),
    )
    result = simulate(artifact, experiment, (ObservationSpec(id="k_obs", expression="k"),))
    assert result.status == "succeeded", result.diagnostics
    assert abs(result.values["X"][1] - (2.0 - math.exp(-1.0))) < 1e-5
    assert abs(result.values["k_obs"][0] - 2.0) < 1e-12 and abs(result.values["k_obs"][1]) < 1e-12
    assert result.values["k_obs"][2] == 0.0
    assert abs(result.values['X'][2]-(2.0-math.exp(-1.0))*math.exp(-1.0)) < 1e-5


def test_rate_rule_parameter_state_is_simulated_and_overridable():
    """Rate-rule state: read via rr[X], initial overridable, rule preserved."""
    artifact = rate_rule_birth_death()
    times = (0.0, 0.5, 1.5, 3.0)
    experiment = ExperimentSpec(
        times=times,
        initial_overrides=FrozenMap({"X": 0.25}),
        parameter_overrides=FrozenMap({"d": 1.0}),
    )
    result = simulate(artifact, experiment)
    assert result.status == "succeeded", result.diagnostics
    assert result.times == times
    assert set(result.values) == {"X"}
    for t, x in zip(result.times, result.values["X"]):
        expected = 2.0 + (0.25 - 2.0) * math.exp(-1.0 * t)
        assert abs(x - expected) < 1e-5
    report = validation.validate_numeric(result)
    assert report.can_execute


def test_rate_rule_model_starts_at_t0_without_pre_equilibration():
    """t0=5 is the initial time: the ODE is integrated only from t0 onward."""
    artifact = rate_rule_birth_death()
    times = (5.0, 6.0)
    result = simulate(artifact, ExperimentSpec(times=times, initial_overrides=FrozenMap({"X": 0.0})))
    assert result.status == "succeeded", result.diagnostics
    assert result.times == (5.0, 6.0)
    expected = 2.0 + (0.0 - 2.0) * math.exp(-1.0 * (6.0 - 5.0))
    assert abs(result.values["X"][0]) < 1e-9         # declared initial, not equilibrated to 2.0
    assert abs(result.values["X"][1] - expected) < 1e-5


def test_very_close_requested_times_are_not_merged():
    """0, 1e-12 and 0.1 stay three distinct samples on the exact grid."""
    artifact = birth_death()
    times = (0.0, 1e-12, 0.1)
    result = simulate(artifact, experiment=ExperimentSpec(times=times),
                      observations=(ObservationSpec(id="t_obs", expression="time"),),
                      solver=SolverConfig(rtol=1e-11, atol=1e-13))
    assert result.status == "succeeded", result.diagnostics
    assert result.times == times
    assert len(result.values["X"]) == 3
    assert result.values["t_obs"] == (0.0, 1e-12, 0.1)
    for t, x in zip(result.times, result.values["X"]):
        assert abs(x - (2.0 - math.exp(-t))) < 1e-9


def test_derived_initial_value_is_recomputed_by_reset():
    """Assignment-rule (derived) initial value follows a parameter override."""
    draft = ModelDraft(
        id="derived_model",
        states=(State(id="X", initial=0.0, quantity_kind="amount"),),
        parameters=(Parameter(id="k", value=2.0),),
        compartment=Compartment(id="cell", size=1.0),
        reactions=(Reaction(id="R", reactants={"X": 1.0}, products={}, rate="k"),),
        derived=(Derived(id="doubled", expression="2*k"),),
    )
    artifact = modeling.build_model(draft)
    result = simulate(artifact, ExperimentSpec(times=(0.0,), parameter_overrides=FrozenMap({"k": 5.0})),
                      (ObservationSpec(id="obs_doubled", expression="doubled"),))
    assert result.status == "succeeded", result.diagnostics
    assert abs(result.values["obs_doubled"][0] - 10.0) < 1e-9


def test_unknown_or_assignment_overrides_are_rejected():
    artifact = birth_death()
    bad_initial = ExperimentSpec(times=(0.0, 1.0), initial_overrides=FrozenMap({"nope": 1.0}))
    result = simulate(artifact, bad_initial)
    assert result.status == "failed" and not result.checks.can_execute
    bad_param = ExperimentSpec(times=(0.0, 1.0), parameter_overrides=FrozenMap({"X": 1.0}))
    assert simulate(artifact, bad_param).status == "failed"


def test_assignment_target_initial_override_is_rejected():
    """A value fixed by an assignment rule can never be initially overridden."""
    draft = ModelDraft(
        id="assign_model",
        states=(State(id="X", initial=0.0, quantity_kind="amount"),),
        parameters=(Parameter(id="k", value=2.0),),
        compartment=Compartment(id="cell", size=1.0),
        reactions=(Reaction(id="R", reactants={"X": 1.0}, products={}, rate="k"),),
        derived=(Derived(id="doubled", expression="2*k"),),
    )
    artifact = modeling.build_model(draft)
    result = simulate(artifact, ExperimentSpec(times=(0.0, 1.0),
                                               initial_overrides=FrozenMap({"doubled": 1.0})))
    assert result.status == "failed" and not result.checks.can_execute


def test_duplicate_and_colliding_observation_ids_are_rejected():
    artifact = birth_death()
    experiment = ExperimentSpec(times=(0.0, 1.0))
    dup = simulate(artifact, experiment,
                   (ObservationSpec(id="o", expression="X"), ObservationSpec(id="o", expression="X")))
    assert dup.status == "failed" and not dup.checks.can_execute
    for colliding in ("X", "k"):
        result = simulate(artifact, experiment, (ObservationSpec(id=colliding, expression="X"),))
        assert result.status == "failed", colliding


def test_observation_id_collision_is_rejected_without_spawning():
    artifact = birth_death()
    experiment = ExperimentSpec(times=(0.0, 1.0))
    result = simulate(artifact, experiment, (ObservationSpec(id="X", expression="2*X"),))
    assert result.status == "failed" and not result.checks.can_execute
    undeclared = simulate(artifact, experiment,
                          (ObservationSpec(id="X2", expression="undeclared_symbol"),))
    assert undeclared.status == "failed" and not undeclared.checks.can_execute


def test_invalid_model_does_not_spawn():
    artifact = ModelArtifact(id="bad", sbml="<not-sbml/>", kind="pde", format="sbml")
    result = simulate(artifact, ExperimentSpec(times=(0.0, 1.0)))
    assert result.status == "unsupported"
    assert not result.checks.can_execute


def test_too_many_samples_is_refused_statically():
    artifact = birth_death()
    times = tuple(float(i) for i in range(10001))
    result = simulate(artifact, ExperimentSpec(times=times))
    assert result.status == "failed"
    assert any(c.code == "samples" for c in result.checks.checks)


def test_timeout_terminates_worker():
    artifact = birth_death()
    experiment = ExperimentSpec(times=(0.0, 5.0))
    result = simulate(artifact, experiment, solver=SolverConfig(timeout_s=0.001))
    assert result.status == "timed_out"
    assert result.values == FrozenMap()


def test_convergence_and_compare_series_contract():
    coarse = (0.0, 1.0, 2.0)
    fine = (0.0, 1.0, 2.0)
    scales = {"X": 1.0}
    assert validation.compare_series({"times": coarse, "values": {"X": [0.0, 1.0, 2.0]}},
                                     {"times": fine, "values": {"X": [0.0, 1.0, 2.0]}},
                                     scales)["status"] == "pass"
    check = validation.convergence_check({"times": coarse, "values": {"X": [0.0, 1.0, 2.0]}},
                                         {"times": fine, "values": {"X": [0.0, 1.0, 2.01]}},
                                         scales)
    assert check.status == "fail"
    with pytest.raises(ValueError):
        validation.compare_series({"times": coarse, "values": {"X": [0.0, 1.0, 2.0]}},
                                  {"times": coarse, "values": {"X": [0.0, 1.0, float("nan")]}},
                                  scales)
    with pytest.raises(ValueError):
        validation.compare_series({"times": (0.0, 1.0), "values": {"X": [0.0, 1.0]}},
                                  {"times": (0.0, 1.0000001), "values": {"X": [0.0, 1.0]}},
                                  scales)


def test_simulate_convergence_against_refined_grid():
    """Fixed scaled-error comparison of two real runs of the same model."""
    artifact = rate_rule_birth_death()
    coarse = simulate(artifact, ExperimentSpec(times=(0.0, 1.0, 2.0)))
    fine = simulate(artifact, ExperimentSpec(times=(0.0, 1.0, 2.0)),
                    solver=SolverConfig(rtol=1e-10, atol=1e-13))
    assert coarse.status == "succeeded" and fine.status == "succeeded"
    outcome = validation.compare_series(fine, coarse, {"X": 2.0}, 1e-6)
    assert outcome["status"] == "pass", outcome
    assert validation.convergence_check(fine, coarse, {"X": 2.0}, 1e-6).status == "pass"


def test_validate_numeric_rejects_non_success_and_conservation_not_run():
    failed = SimulationResult(status="failed", diagnostics=("boom",))
    report = validation.validate_numeric(failed)
    assert not report.can_execute and not report.fully_passed

    artifact = birth_death()
    result = simulate(artifact, ExperimentSpec(times=(0.0, 1.0)), (ObservationSpec(id="o", expression="X"),))
    assert result.status == "succeeded", result.diagnostics
    ok = validation.validate_numeric(result, nonnegative=("X",), conservation=("X",))
    assert ok.can_execute
    assert not ok.fully_passed  # conservation is not_run, never a silent pass
    assert all(c.status != "pass" for c in ok.checks if c.code in {"conservation", "conservation_reference"})

    unknown_ref = validation.validate_numeric(result, conservation=("nope",))
    assert any(c.code == "conservation_reference" and c.status == "unknown" for c in unknown_ref.checks)
    assert unknown_ref.can_execute  # warning severity: reported, not blocking


def test_validate_numeric_rejects_negative_when_nonnegative_declared():
    artifact = birth_death()
    result = simulate(artifact, ExperimentSpec(times=(0.0, 1.0), initial_overrides=FrozenMap({"X": -3.0})))
    assert result.status == "succeeded", result.diagnostics
    assert result.values["X"][0] < 0.0  # nothing is clipped or repaired
    report = validation.validate_numeric(result, nonnegative=("X",))
    assert any(c.code == "nonnegative" and c.status == "fail" for c in report.checks)
    assert not report.can_execute
