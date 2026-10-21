"""Codex-run acceptance tests for the minimal domain contracts."""

import copy
import json
import math
import pickle

import pytest

from biomodel_agent.domain import contracts as c
from biomodel_agent.domain import serialization as s
from biomodel_agent.domain.serialization import SchemaError


def make_draft() -> c.ModelDraft:
    return c.ModelDraft(
        id="m1",
        states=(c.State("x", 1.0), c.State("y", None, nonnegative=True)),
        parameters=(c.Parameter("k", 0.5), c.Parameter("v", None)),
        reactions=(c.Reaction("r1", {"x": 1.0}, {"y": 2.0}, "k*x"),),
        rate_rules=(c.RateRule("x", "-r1"), c.RateRule("y", "r1"),),
        derived=(c.Derived("ratio", "y/x"),),
        element_map={"reaction:r/rate": ("evidence_ref_one",)},
        hypothesis_refs=("h1",),
    )


def test_rejects_non_finite_values():
    with pytest.raises(ValueError):
        c.Parameter("k", math.nan)
    with pytest.raises(ValueError):
        c.Parameter("k", math.inf)
    with pytest.raises(ValueError):
        c.State("x", float("nan"))
    with pytest.raises(ValueError):
        c.ExperimentSpec(times=(0.0, 1.0, float("inf")))


def test_none_preserved_explicitly():
    d = make_draft()
    assert d.parameters[1].value is None
    assert d.states[1].initial is None
    assert d.parent_ref is None


def test_snapshot_isolation_and_pickle():
    reactants = {"x": 1.0}
    times = [0.0, 1.0]
    d = c.ModelDraft(
        id="m2",
        states=[c.State("a", 1.0)],
        parameters=[c.Parameter("k", 1.0)],
        reactions=[c.Reaction("r", dict(reactants), {"b": 1.0}, "x")],
    )
    exp = c.ExperimentSpec(times=times, initial_overrides={"a": 2.0})
    reactants["x"] = 99.0
    times.append(9.0)
    with pytest.raises(TypeError):
        exp.initial_overrides["a"] = 99.0  # FrozenMap is read-only: no __setitem__
    assert d.reactions[0].reactants["x"] == 1.0
    assert d.states[0].id == "a"
    assert exp.times == (0.0, 1.0)
    with pytest.raises(TypeError):
        exp.initial_overrides["a"] = 0.0  # __setitem__ raises TypeError
    with pytest.raises(AttributeError):
        exp.initial_overrides._pairs = ()  # attribute protocol raises AttributeError
    d2 = pickle.loads(pickle.dumps(d))
    assert d2 == d
    e2 = pickle.loads(pickle.dumps(exp))
    assert e2 == exp
    fm = c.FrozenMap({"a": 1.0})
    assert pickle.loads(pickle.dumps(fm)) == fm


def test_frozenmap_nested_isolation():
    # External nested containers survive being frozen; later mutation is isolated.
    outer = {"path": {"nested": [1.0, 2.0]}, "s": "ok"}
    fm = c.FrozenMap(outer)
    assert fm["path"]["nested"] == (1.0, 2.0)
    outer["path"]["nested"].append(99.0)
    outer["s"] = "mutated"
    assert fm["path"]["nested"] == (1.0, 2.0)
    assert fm["s"] == "ok"
    # The backing _pairs tuple cannot be reassigned or mutated.
    with pytest.raises(AttributeError):
        fm._pairs = ()
    with pytest.raises(TypeError):
        fm._pairs[0] = ("x", 1.0)


def test_frozenmap_value_rules():
    # Evidence-path keys are legal even though they are not SBML IDs.
    fm = c.FrozenMap({"reaction:r/rate": 1.0, "x": None, "b": True, "n": 3, "s": "v"})
    assert fm["n"] == 3 and fm["s"] == "v" and fm["b"] is True and fm["x"] is None
    # Empty string keys rejected.
    with pytest.raises(ValueError):
        c.FrozenMap({"" : 1.0})
    # Duplicate keys rejected.
    with pytest.raises(ValueError):
        c.FrozenMap((("a", 1.0), ("a", 2.0)))
    # Mutable / unknown value types rejected.
    with pytest.raises(ValueError):
        c.FrozenMap({"a": {1.0}})
    with pytest.raises(ValueError):
        c.FrozenMap({"a": object()})


def test_frozenmap_hash_deterministic():
    a = c.FrozenMap({"x": 1.0, "y": {"z": 2.0, "w": (1, 2)}})
    b = c.FrozenMap({"y": {"w": (1, 2), "z": 2.0}, "x": 1.0})
    assert a == b
    assert hash(a) == hash(b)


def test_id_rules_and_conflicts():
    with pytest.raises(ValueError):
        c.State("1bad", 0.0)
    with pytest.raises(ValueError):
        c.State("-x", 0.0)
    with pytest.raises(ValueError):
        c.ModelDraft(id="m", states=(c.State("a", 0.0),), parameters=(c.Parameter("a", 1.0),))
    with pytest.raises(ValueError):
        c.ModelDraft(id="m", states=(c.State("a", 0.0), c.State("a", 1.0)))


def test_rate_rule_no_id_and_unknown_state():
    # RateRule has no own ID; two rules for different states never "conflict".
    d = c.ModelDraft(
        id="m",
        states=(c.State("a", 0.0), c.State("b", 1.0)),
        rate_rules=(c.RateRule("a", "f1"), c.RateRule("b", "f2")),
    )
    assert d.rate_rules == (c.RateRule("a", "f1"), c.RateRule("b", "f2"))
    # A rate rule referencing a state that does not exist is rejected.
    with pytest.raises(ValueError):
        c.ModelDraft(id="m", states=(c.State("a", 0.0),), rate_rules=(c.RateRule("ghost", "f"),))


def test_state_quantity_kind_restricted():
    for bad in ("", "mass", "AMOUNT", "flux"):
        with pytest.raises(ValueError):
            c.State("x", 1.0, quantity_kind=bad)
    for good in ("variable", "amount", "concentration"):
        assert c.State("x", 1.0, quantity_kind=good).quantity_kind == good


def test_time_series_rules():
    with pytest.raises(ValueError):
        c.ExperimentSpec(times=())
    with pytest.raises(ValueError):
        c.ExperimentSpec(times=(1.0, 1.0))
    with pytest.raises(ValueError):
        c.ExperimentSpec(times=(1.0, 0.5))
    with pytest.raises(ValueError):
        c.ExperimentSpec(
            times=(0.0, 2.0),
            segments=(c.InputSegment(1.0, {"a": 1.0}), c.InputSegment(1.0, {"a": 2.0})),
        )


def test_compartment_and_stoichiometry():
    with pytest.raises(ValueError):
        c.Compartment(size=0.0)
    with pytest.raises(ValueError):
        c.Compartment(size=-1.0)
    with pytest.raises(ValueError):
        c.Reaction("r", {"x": 0.0}, {"y": 1.0}, "x")
    with pytest.raises(ValueError):
        c.Reaction("r", {"x": 1.0}, {"y": -1.0}, "x")


def test_artifact_sha_changes_with_sbml():
    a1 = c.ModelArtifact(id="art", sbml="<sbml/>")
    a2 = c.ModelArtifact(id="art", sbml="<sbml v2/>")
    assert a1.sha256 != a2.sha256
    assert len(a1.sha256) == 64
    with pytest.raises(ValueError):
        c.ModelArtifact(id="art", sbml="   ")


def test_sbml_sha_matches_content():
    import hashlib
    a = c.ModelArtifact(id="a", sbml="<sbml version=\"1\"/>")
    assert a.sha256 == hashlib.sha256(a.sbml.encode("utf-8")).hexdigest()


def test_unknown_field_rejected():
    data = s.to_dict(make_draft())
    data["not_a_field"] = 1
    with pytest.raises(SchemaError):
        s.from_dict(c.ModelDraft, data)
    # Deleting a defaulted field must be fine; deleting a required one is not.
    data = s.to_dict(c.SolverConfig())
    del data["max_steps"]
    assert s.from_dict(c.SolverConfig, data) == c.SolverConfig()
    data = s.to_dict(c.Parameter("k", 1.0))
    del data["id"]
    with pytest.raises(SchemaError):
        s.from_dict(c.Parameter, data)
    # Nested unknown fields are rejected too.
    data = s.to_dict(make_draft())
    data["states"][0]["wat"] = 1
    with pytest.raises(SchemaError):
        s.from_dict(c.ModelDraft, data)


def test_json_roundtrips():
    for obj in (make_draft(),
                c.ExperimentSpec(times=(0.0, 1.0), initial_overrides={"x": 1.0},
                                 segments=(c.InputSegment(0.5, {"x": 1.0}),)),
                c.SolverConfig(rtol=1e-8)):
        back = json.loads(json.dumps(s.to_dict(obj)))
        rebuilt = s.from_dict(type(obj), back)
        assert rebuilt == obj
        assert s.to_dict(rebuilt) == back


def test_simulation_result_roundtrip():
    r = c.SimulationResult(status="succeeded", times=(0.0, 1.0, 2.0),
                           values={"x": (1.0, 2.0, 3.0), "y": (0.5, 0.5, 0.5)},
                           backend="roadrunner", elapsed_s=0.25,
                           diagnostics=("info line",),
                           checks=c.ValidationReport(checks=(c.Check("b", "pass"),)))
    back = json.loads(json.dumps(s.to_dict(r)))
    rebuilt = s.from_dict(c.SimulationResult, back)
    assert rebuilt == r
    assert s.to_dict(rebuilt) == back
    assert rebuilt.values["x"] == (1.0, 2.0, 3.0)


def test_strict_json_rejects_non_finite():
    # float('nan') / inf must never survive serialization.
    with pytest.raises(SchemaError):
        s.from_dict(c.Parameter, {"id": "k", "value": float("nan")})
    with pytest.raises(SchemaError):
        s.from_dict(c.Parameter, {"id": "k", "value": float("inf")})
    # And to_dict/_leaf raises when a non-finite value reaches it.
    with pytest.raises(SchemaError):
        s._leaf(float("nan"))
    with pytest.raises(SchemaError):
        s._leaf(float("-inf"))


def test_primitive_type_strictness():
    with pytest.raises(SchemaError):
        s.from_dict(c.Parameter, {"id": "k", "value": True})   # bool is not a number
    with pytest.raises(SchemaError):
        s.from_dict(c.SolverConfig, {"rtol": 1e-9, "atol": 1e-12,
                                     "timeout_s": 1.0, "max_steps": "1000"})  # int not str
    with pytest.raises(SchemaError):
        s.from_dict(c.Parameter, {"id": 5, "value": 1.0})      # str only for str
    with pytest.raises(SchemaError):
        s.from_dict(c.ExperimentSpec, {"times": [[0.0, 1.0]]})  # list not scalar float


def test_artifact_hash_verified_and_ignored_ok():
    a = c.ModelArtifact(id="a", sbml="<sbml/>")
    data = s.to_dict(a)
    assert "sha256" not in data
    assert s.from_dict(c.ModelArtifact, data) == a
    data["sha256"] = a.sha256
    assert s.from_dict(c.ModelArtifact, data) == a
    data["sha256"] = "0" * 64
    with pytest.raises(SchemaError):
        s.from_dict(c.ModelArtifact, data)


def test_unknown_check_not_fully_passed():
    ok = c.ValidationReport(checks=(c.Check("id_shape", "pass"), c.Check("t", "not_applicable")))
    assert ok.can_execute and ok.fully_passed
    unknown = c.ValidationReport(checks=(c.Check("balance", "unknown", "could not check"),))
    assert unknown.can_execute
    assert not unknown.fully_passed
    blocking_fail = c.ValidationReport(checks=(c.Check("x", "fail"),))
    assert not blocking_fail.can_execute
    warning_fail = c.ValidationReport(checks=(c.Check("x", "fail", severity="warning"),))
    assert warning_fail.can_execute
    with pytest.raises(ValueError):
        c.Check("x", "maybe")
    with pytest.raises(ValueError):
        c.Check("x", "pass", severity="loud")


def test_solver_and_simulation_validation():
    with pytest.raises(ValueError):
        c.SolverConfig(rtol=-1.0)
    with pytest.raises(ValueError):
        c.SolverConfig(timeout_s=0.0)
    with pytest.raises(ValueError):
        c.SolverConfig(max_steps=0)
    with pytest.raises(ValueError):
        c.SimulationResult(status="ok")
    with pytest.raises(ValueError):
        c.SimulationResult(status="succeeded", times=(0.0, 1.0), values={"x": (1.0,)})
    r = c.SimulationResult(status="succeeded", times=(0.0, 1.0),
                           values={"x": (1.0, 2.0)}, backend="roadrunner")
    assert r.values["x"] == (1.0, 2.0)
    assert r.checks.can_execute and not r.checks.fully_passed  # no checks were executed


def test_deep_copy_snapshot_stable():
    d = make_draft()
    copy.copy(d)
    d2 = make_draft()
    assert d == d2
    # mutation attempts raise
    with pytest.raises(Exception):
        d.id = "other"
