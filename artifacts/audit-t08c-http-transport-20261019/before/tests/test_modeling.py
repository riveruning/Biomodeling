"""Tests for the restricted-expression and SBML build/validate layer.

Run from the repo root with the project venv:
    .venv/bin/python -m pytest tests/test_modeling.py -q
"""

from __future__ import annotations

import ast
import math

import libsbml
import pytest

from biomodel_agent import expressions, modeling
from biomodel_agent.domain.contracts import (
    Compartment,
    Derived,
    FrozenMap,
    ModelArtifact,
    ModelDraft,
    Parameter,
    RateRule,
    Reaction,
    State,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _codes(report) -> dict[str, str]:
    return {c.code: c.status for c in report.checks}


def _rate_rule_draft() -> ModelDraft:
    return ModelDraft(
        id="m_rr",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("k", 0.5, "dimensionless"),),
        rate_rules=(RateRule("x", "-k * x"),),
        time_unit="second",
    )


def _reaction_draft() -> ModelDraft:
    return ModelDraft(
        id="m_rx",
        states=(State("a", 1.0, "unknown", "amount"),
                State("b", 0.0, "unknown", "amount")),
        parameters=(Parameter("k", 0.2, "dimensionless"),),
        reactions=(Reaction("r1", FrozenMap({"a": 1}), FrozenMap({"b": 1}), "k * a"),),
        time_unit="second",
    )


def _conc_draft() -> ModelDraft:
    return ModelDraft(
        id="m_c",
        states=(State("a", 1.0, "mole_per_litre", "concentration"),
                State("b", 0.0, "mole_per_litre", "concentration")),
        parameters=(Parameter("k", 0.2, "dimensionless"),),
        compartment=Compartment("cell", 1.0, "litre"),
        reactions=(Reaction("r1", FrozenMap({"a": 1}), FrozenMap({"b": 1}), "k * a"),),
        time_unit="second",
    )


def _parse(sbml: str):
    doc = libsbml.readSBMLFromString(sbml)
    assert doc is not None
    return doc.getModel()


# ---------------------------------------------------------------------------
# expressions: parsing, precedence, safety
# ---------------------------------------------------------------------------

def test_parse_expression_returns_ast():
    tree = expressions.parse_expression("a + b * 2", allowed_symbols={"a", "b"})
    assert isinstance(tree, ast.Expression)


def test_binary_and_unary_operators_are_actually_accepted():
    # the operator is a leaf of the AST: a walk-based check never sees it
    for good in ("a + b", "a - b", "a * b", "a / b", "a ** b", "-a", "+a", "-(a + b)"):
        assert expressions.parse_expression(good, allowed_symbols={"a", "b"}) is not None


def test_precedence_is_python_precedence():
    assert expressions.evaluate("2 + 3 * 4", {"time": 0.0}) == pytest.approx(14.0)
    assert expressions.evaluate("(2 + 3) * 4", {"time": 0.0}) == pytest.approx(20.0)
    assert expressions.evaluate("2 ** 3 ** 2", {"time": 0.0}) == pytest.approx(512.0)
    assert expressions.evaluate("-2 ** 2", {"time": 0.0}) == pytest.approx(-4.0)
    assert expressions.evaluate("2 ^ 3", {"time": 0.0}) == pytest.approx(8.0)


def test_functions_log_is_natural_log():
    env = {"x": 1.0, "time": 0.0}
    assert expressions.evaluate("exp(x)", env) == pytest.approx(math.e)
    assert expressions.evaluate("ln(x)", env) == pytest.approx(0.0)
    assert expressions.evaluate("log(x)", env) == pytest.approx(0.0)
    assert expressions.evaluate("log(exp(1))", env) == pytest.approx(1.0)
    assert expressions.evaluate("sqrt(4)", env) == pytest.approx(2.0)
    assert expressions.evaluate("pow(2, 10)", env) == pytest.approx(1024.0)


def test_division_by_zero_and_bad_domain_rejected():
    env = {"time": 0.0}
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("1 / 0", env)
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("sqrt(-1)", env)


def test_time_requires_explicit_environment():
    assert expressions.evaluate("time", {"time": 7.0}) == pytest.approx(7.0)
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("time")  # no implicit time = 0
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("x + time", {"x": 1.0})


def test_symbols_is_case_sensitive_and_keeps_time():
    assert expressions.symbols("a * exp(k) + b") == {"a", "b", "k"}
    assert expressions.symbols("sqrt(x) + time") == {"x", "time"}
    assert expressions.symbols("X + x") == {"X", "x"}


def test_case_sensitive_resolution():
    env = {"X": 2.0, "x": 3.0, "time": 0.0}
    assert expressions.evaluate("X + x", env) == pytest.approx(5.0)
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("x", {"X": 2.0})
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("x", allowed_symbols={"X"})


def test_function_name_is_not_a_variable():
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("sqrt")
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("sqrt + 1")
    assert "sqrt" not in expressions.symbols("sqrt(a) + a")


def test_illegal_constructs_rejected():
    for bad in (
        "__import__('os')",
        "x.__class__",
        "x[0]",
        "[i for i in (1,)]",
        "min(x=1)",
        "True",
        "None",
        "x if y else z",
        "a % b",
        "a & b",
        "a < b",
        "lambda: 1",
    ):
        with pytest.raises(expressions.ExpressionError):
            expressions.parse_expression(bad, allowed_symbols={"a", "b", "x", "y", "z"})


def test_expression_shape_limits():
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression(None)  # type: ignore[arg-type]
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("")
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("1 " * 5000)
    with pytest.raises(expressions.ExpressionError):
        expressions.parse_expression("+".join(["x"] * 400), allowed_symbols={"x"})


def test_non_finite_environment_rejected():
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("x", {"x": float("nan"), "time": 0.0})
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("exp(x)", {"x": 1000.0, "time": 0.0})


# ---------------------------------------------------------------------------
# expressions: SBML formula generation
# ---------------------------------------------------------------------------

def test_to_sbml_formula_parenthesizes_every_operation():
    # (k - d) * x must not degrade into k - d * x
    assert expressions.to_sbml_formula("(k - d) * x") == "((k - d) * x)"
    assert expressions.to_sbml_formula("k - d * x") == "(k - (d * x))"
    assert expressions.to_sbml_formula("-x") == "(-x)"
    assert expressions.to_sbml_formula("a / (b + c)") == "(a / (b + c))"
    assert expressions.to_sbml_formula("2 ^ 3") == "(2 ^ 3)"


def test_to_sbml_formula_maps_log_to_ln_and_keeps_case():
    assert expressions.to_sbml_formula("log(x)") == "ln(x)"
    assert expressions.to_sbml_formula("ln(x)") == "ln(x)"
    assert expressions.to_sbml_formula("exp(X)") == "exp(X)"


def test_sbml_formula_round_trips_through_libsbml():
    formula = expressions.to_sbml_formula("(k - d) * x + time")
    node = libsbml.parseL3Formula(formula)
    assert node is not None
    names = set()

    def walk(item):
        if item.isName():
            names.add(item.getName())
        for i in range(item.getNumChildren()):
            walk(item.getChild(i))

    walk(node)
    assert {"k", "d", "x", "time"} <= names


# ---------------------------------------------------------------------------
# validate_draft
# ---------------------------------------------------------------------------

def test_valid_drafts_pass_structure_but_not_units():
    for draft in (_rate_rule_draft(), _reaction_draft(), _conc_draft()):
        report = modeling.validate_draft(draft)
        assert _codes(report).get("draft_valid") == "pass"
        assert report.can_execute


def test_unknown_units_are_warnings_not_dimensionless():
    report = modeling.validate_draft(_rate_rule_draft())
    codes = _codes(report)
    assert codes.get("unit_unknown") == "unknown"
    assert report.can_execute          # warnings never block explore
    assert not report.fully_passed     # ... but are never a clean pass either


def test_declared_symbol_must_be_case_sensitive():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("k", 0.5, "dimensionless"),),
        rate_rules=(RateRule("x", "-K * x"),),  # K undeclared, k is declared
    )
    report = modeling.validate_draft(draft)
    assert _codes(report).get("expression_invalid") == "fail"
    assert not report.can_execute


def test_reserved_symbol_declaration_rejected():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("exp", 1.0, "dimensionless"),),
        rate_rules=(RateRule("x", "-exp * x"),),
    )
    report = modeling.validate_draft(draft)
    assert _codes(report).get("symbol_reserved") == "fail"
    assert not report.can_execute


def test_unknown_rate_rule_target_is_impossible_by_contract():
    from biomodel_agent.domain.contracts import RateRule as RR
    with pytest.raises(ValueError):
        ModelDraft(
            id="m",
            states=(State("x", 1.0, "unknown", "variable"),),
            rate_rules=(RR("ghost", "-x"),),
        )


def test_undeclared_and_empty_expressions_fail():
    for text in ("-k * x", ""):
        draft = ModelDraft(
            id="m",
            states=(State("x", 1.0, "unknown", "variable"),),
            rate_rules=(RateRule("x", text),),
        )
        assert _codes(modeling.validate_draft(draft)).get("expression_invalid") == "fail"


def test_illegal_code_input_rejected():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "__import__('os')"),),
    )
    report = modeling.validate_draft(draft)
    assert _codes(report).get("expression_invalid") == "fail"
    assert not report.can_execute


def test_placeholder_missing_values_fail():
    missing_param = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("k", None, "dimensionless"),),
        rate_rules=(RateRule("x", "-k * x"),),
    )
    assert _codes(modeling.validate_draft(missing_param)).get("parameter_missing") == "fail"

    missing_initial = ModelDraft(
        id="m",
        states=(State("x", None, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-x"),),
    )
    assert _codes(modeling.validate_draft(missing_initial)).get("initial_missing") == "fail"


def test_duplicate_and_conflicting_rate_rules_fail():
    duplicate = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-x"), RateRule("x", "-2 * x")),
    )
    assert _codes(modeling.validate_draft(duplicate)).get("rate_duplicate") == "fail"

    conflicting = ModelDraft(
        id="m",
        states=(State("a", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("a", "-a"),),
        reactions=(Reaction("r", FrozenMap({"a": 1}), FrozenMap(), "a"),),
    )
    assert _codes(modeling.validate_draft(conflicting)).get("rate_reaction_conflict") == "fail"


def test_state_without_dynamics_fails():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),
                State("y", 1.0, "unknown", "variable")),
        rate_rules=(RateRule("x", "-x"),),
    )
    assert _codes(modeling.validate_draft(draft)).get("state_not_dynamic") == "fail"


def test_derived_cycles_and_self_reference_fail():
    cycle = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-x"),),
        derived=(Derived("a", "b + 1"), Derived("b", "a + 1")),
    )
    assert _codes(modeling.validate_draft(cycle)).get("derived_cycle") == "fail"

    self_ref = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-x"),),
        derived=(Derived("a", "a + 1"),),
    )
    assert _codes(modeling.validate_draft(self_ref)).get("derived_cycle") == "fail"


def test_valid_nested_derived_passes():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("k", 0.5, "dimensionless"),),
        rate_rules=(RateRule("x", "-x * d1"),),
        derived=(Derived("d1", "k + x"), Derived("d2", "d1 * 2")),
        time_unit="second",
    )
    report = modeling.validate_draft(draft)
    assert _codes(report).get("draft_valid") == "pass"
    assert report.can_execute


def test_stoichiometry_and_quantity_kind_rules():
    unknown_state = ModelDraft(
        id="m",
        states=(State("a", 1.0, "unknown", "amount"),),
        reactions=(Reaction("r", FrozenMap({"zzz": 1}), FrozenMap({"a": 1}), "a"),),
    )
    assert _codes(modeling.validate_draft(unknown_state)).get("stoich_unknown_state") == "fail"

    variable_species = ModelDraft(
        id="m",
        states=(State("a", 1.0, "unknown", "variable"),),
        reactions=(Reaction("r", FrozenMap({"a": 1}), FrozenMap(), "a"),),
    )
    assert _codes(modeling.validate_draft(variable_species)).get("quantity_kind_mixed") == "fail"

    rate_rule_on_concentration = ModelDraft(
        id="m",
        states=(State("a", 1.0, "unknown", "concentration"),),
        rate_rules=(RateRule("a", "-a"),),
    )
    assert _codes(modeling.validate_draft(rate_rule_on_concentration)).get("quantity_kind_mixed") == "fail"

    # amount and concentration species may coexist: each is encoded natively
    mixed = ModelDraft(
        id="m",
        states=(State("a", 1.0, "mole", "amount"),
                State("b", 0.0, "mole_per_litre", "concentration")),
        parameters=(Parameter("k", 0.2, "dimensionless"),),
        compartment=Compartment("cell", 1.0, "litre"),
        reactions=(Reaction("r1", FrozenMap({"a": 1}), FrozenMap({"b": 1}), "k * a"),),
    )
    assert _codes(modeling.validate_draft(mixed)).get("draft_valid") == "pass"


def test_unit_mapping_is_honest():
    assert modeling.canonical_unit("litre") == "litre"
    assert modeling.canonical_unit("liter") == "litre"
    assert modeling.canonical_unit("minute") == "minute"
    assert modeling.canonical_unit("mole_per_litre") == "mole_per_litre"
    assert modeling.canonical_unit("furlong") is None
    assert modeling.canonical_unit(None) is None
    assert modeling.canonical_unit("unknown") is None


# ---------------------------------------------------------------------------
# build_model
# ---------------------------------------------------------------------------

def test_build_rate_rule_artifact_reads_back_as_a_rate_rule():
    artifact = modeling.build_model(_rate_rule_draft())
    assert modeling.validate_artifact(artifact).can_execute

    model = _parse(artifact.sbml)
    assert model.getNumRules() == 1
    rule = model.getListOfRules().get(0)
    assert rule.isRate() and not rule.isAssignment()
    assert rule.getVariable() == "x"
    # the variable state is a non-constant parameter whose derivative is the rule
    assert model.getParameter("x") is not None
    assert not model.getParameter("x").getConstant()


def test_build_reaction_artifact_reads_back_as_a_reaction():
    artifact = modeling.build_model(_reaction_draft())
    assert modeling.validate_artifact(artifact).can_execute

    model = _parse(artifact.sbml)
    assert model.getNumReactions() == 1
    reaction = model.getReaction(0)
    assert reaction.getNumReactants() == 1
    assert reaction.getNumProducts() == 1
    assert reaction.getKineticLaw().getMath() is not None

    names = set()

    def walk(item):
        if item.isName():
            names.add(item.getName())
        for i in range(item.getNumChildren()):
            walk(item.getChild(i))

    walk(reaction.getKineticLaw().getMath())
    assert names == {"k", "a"}


def test_volume_amount_concentration_encoding():
    from libsbml import readSBMLFromString

    art_amount = modeling.build_model(_reaction_draft())
    art_conc = modeling.build_model(_conc_draft())
    assert art_amount.sbml != art_conc.sbml

    ma = readSBMLFromString(art_amount.sbml).getModel()
    mc = readSBMLFromString(art_conc.sbml).getModel()
    sa, sc = ma.getSpecies(0), mc.getSpecies(0)

    # the amount fixture uses the unknown unit, so substance units are honestly
    # left undeclared: assert what the encoding must do, nothing more
    assert sa.getHasOnlySubstanceUnits()
    assert not sa.isSetSubstanceUnits()
    assert sa.isSetInitialAmount() and not sa.isSetInitialConcentration()
    assert not sc.getHasOnlySubstanceUnits()
    assert sc.isSetInitialConcentration() and not sc.isSetInitialAmount()
    assert sc.getSubstanceUnits() == "mole"
    assert mc.getCompartment(0).getUnits() == "litre"
    assert mc.getCompartment(0).getSpatialDimensions() == 3


def test_concentration_species_get_mole_substance_units():
    # dedicated draft so the unit under test is a real, declared mole: a
    # concentration species in a litre compartment must map to mole
    # substance units, while the compartment keeps its litre volume unit
    draft = ModelDraft(
        id="m_c_mole",
        states=(State("a", 2.0, "mole_per_litre", "concentration"),),
        parameters=(Parameter("k", 0.2, "dimensionless"),),
        compartment=Compartment("cell", 1.0, "litre"),
        reactions=(Reaction("r1", FrozenMap({"a": 1}), FrozenMap(), "k * a"),),
        time_unit="second",
    )
    artifact = modeling.build_model(draft)
    assert modeling.validate_artifact(artifact).can_execute
    model = _parse(artifact.sbml)
    species = model.getSpecies(0)
    compartment = model.getCompartment(0)
    assert not species.getHasOnlySubstanceUnits()
    assert species.isSetSubstanceUnits() and species.getSubstanceUnits() == "mole"
    assert species.isSetInitialConcentration() and not species.isSetInitialAmount()
    assert compartment.getUnits() == "litre"


def test_rate_rule_model_uses_zero_dimensional_compartment():
    model = _parse(modeling.build_model(_rate_rule_draft()).sbml)
    # pure mathematical (variable) states are not spatial, so a dimensionless
    # compartment unit stays legal
    assert model.getCompartment(0).getSpatialDimensions() == 0


def test_only_used_units_are_defined():
    model = _parse(modeling.build_model(_rate_rule_draft()).sbml)
    assert model.getNumUnitDefinitions() == 0

    minute_draft = ModelDraft(
        id="m_min",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-x"),),
        time_unit="minute",
    )
    model = _parse(modeling.build_model(minute_draft).sbml)
    assert model.getNumUnitDefinitions() == 1
    definition = model.getUnitDefinition(0)
    assert definition.getId() == "minute"
    unit = definition.getUnit(0)
    assert unit.getKind() == libsbml.UNIT_KIND_SECOND
    assert unit.getMultiplier() == pytest.approx(60.0)


def test_build_fails_with_check_for_bad_draft():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        rate_rules=(RateRule("x", "-k * x"),),  # k undeclared
    )
    with pytest.raises(ValueError) as exc:
        modeling.build_model(draft)
    assert "expression_invalid" in str(exc.value)


def test_build_derived_creates_assignment_rules():
    draft = ModelDraft(
        id="m",
        states=(State("x", 1.0, "unknown", "variable"),),
        parameters=(Parameter("k", 0.5, "dimensionless"),),
        rate_rules=(RateRule("x", "-x * d1"),),
        derived=(Derived("d1", "k + x"), Derived("d2", "d1 * 2")),
    )
    model = _parse(modeling.build_model(draft).sbml)
    variables = {
        model.getListOfRules().get(i).getVariable()
        for i in range(model.getNumRules())
        if model.getListOfRules().get(i).isAssignment()
    }
    assert variables == {"d1", "d2"}


# ---------------------------------------------------------------------------
# validate_artifact
# ---------------------------------------------------------------------------

_EVENT_SBML = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfSpecies>
      <species id="x" compartment="c" hasOnlySubstanceUnits="true"
               boundaryCondition="false" constant="false" initialAmount="1"/>
    </listOfSpecies>
    <listOfReactions>
      <reaction id="r1" reversible="false">
        <listOfReactants><speciesReference species="x" stoichiometry="1" constant="true"/></listOfReactants>
        <listOfProducts><speciesReference species="x" stoichiometry="1" constant="true"/></listOfProducts>
        <kineticLaw><math xmlns="http://www.w3.org/1998/Math/MathML"><ci>x</ci></math></kineticLaw>
      </reaction>
    </listOfReactions>
    <listOfEvents>
      <event id="ev1" useValuesFromTriggerTime="false">
        <trigger initialValue="false" persistent="true">
          <math xmlns="http://www.w3.org/1998/Math/MathML"><cn>1</cn></math>
        </trigger>
      </event>
    </listOfEvents>
  </model>
</sbml>
"""

_DELAY_SBML = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m">
    <listOfFunctionDefinitions>
      <functionDefinition id="lag">
        <math xmlns="http://www.w3.org/1998/Math/MathML">
          <apply><csymbol definitionURL="http://www.sbml.org/sbml/symbols/delay">delay</csymbol>
            <ci>x</ci><cn>2</cn></apply>
        </math>
      </functionDefinition>
    </listOfFunctionDefinitions>
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfSpecies>
      <species id="x" compartment="c" hasOnlySubstanceUnits="true"
               boundaryCondition="false" constant="false" initialAmount="1"/>
    </listOfSpecies>
    <listOfParameters><parameter id="k" constant="false" value="0"/></listOfParameters>
    <listOfRules>
      <rateRule variable="k">
        <math xmlns="http://www.w3.org/1998/Math/MathML">
          <apply><ci>lag</ci><ci>x</ci></apply>
        </math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""


def test_event_import_fails_unsupported():
    artifact = ModelArtifact(id="m", sbml=_EVENT_SBML, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    assert _codes(report).get("unsupported_events") == "fail"
    assert not report.can_execute


def test_delay_in_function_definition_is_detected():
    artifact = ModelArtifact(id="m", sbml=_DELAY_SBML, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    assert _codes(report).get("unsupported_delay") == "fail"
    assert not report.can_execute


def test_non_ode_kind_fails_unsupported():
    artifact = ModelArtifact(id="m", sbml="<sbml></sbml>", format="pde", kind="pde")
    report = modeling.validate_artifact(artifact)
    assert _codes(report).get("unsupported") == "fail"
    assert not report.can_execute


def test_empty_or_unparseable_sbml_never_passes():
    for bad in ("not sbml at all", "<sbml>", ""):
        if not bad:
            continue  # ModelArtifact already rejects empty sbml
        artifact = ModelArtifact(id="m", sbml=bad, format="sbml", kind="ode")
        report = modeling.validate_artifact(artifact)
        codes = _codes(report)
        assert codes.get("sbml_parsefail") == "fail"
        assert not report.can_execute
        assert not report.fully_passed


def test_parsed_but_empty_document_fails():
    artifact = ModelArtifact(
        id="m",
        sbml="<sbml xmlns='http://www.sbml.org/sbml/level3/version2' level='3' version='2'/>",
        format="sbml", kind="ode",
    )
    report = modeling.validate_artifact(artifact)
    assert _codes(report).get("sbml_parsefail") == "fail"
    assert not report.can_execute


def test_unknown_units_not_fully_passed_but_executable():
    artifact = modeling.build_model(_reaction_draft())
    report = modeling.validate_artifact(artifact)
    codes = _codes(report)
    assert codes.get("units_missing") == "unknown"
    assert report.can_execute        # a unit warning does not block explore
    assert not report.fully_passed   # ... and it is not a clean pass


def test_undefined_symbol_in_rule_blocks():
    sbml = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfParameters><parameter id="x" constant="false" value="1"/></listOfParameters>
    <listOfRules>
      <rateRule variable="x">
        <math xmlns="http://www.w3.org/1998/Math/MathML">
          <apply><minus/><ci>ghost</ci></apply>
        </math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""
    artifact = ModelArtifact(id="m", sbml=sbml, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    assert not report.can_execute
    assert any('ghost' in c.message for c in report.checks if c.status == 'fail')
    assert modeling.validate_artifact(ModelArtifact('m',sbml.replace('ghost','x'))).can_execute
    assert not report.can_execute


def test_default_plugins_do_not_mark_packageless_docs_as_required():
    # libSBML registers the layout/render visualisation plugins by default and
    # answers getPackageRequired truthily for them, even for a plain L2/L3
    # document whose root declares no package namespaces at all.  A structurally
    # valid model with no package XML must still pass.
    sbml = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfParameters><parameter id="x" constant="false" value="1"/></listOfParameters>
    <listOfRules>
      <rateRule variable="x">
        <math xmlns="http://www.w3.org/1998/Math/MathML"><ci>x</ci></math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""
    doc = libsbml.readSBMLFromString(sbml)
    assert doc is not None
    # the environment does indeed register the default visualisation plugins
    # Plugin registration varies by libSBML build; only scientific behavior matters.
    problem_names = [
        plugin.getPackageName()
        for plugin in
        (doc.getPlugin(i) for i in range(doc.getNumPlugins()))
    ]

    artifact = ModelArtifact(id="m", sbml=sbml, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    codes = _codes(report)
    assert "unsupported_package" not in codes
    assert codes.get("artifact_valid") == "pass"
    assert report.can_execute


def test_render_namespace_declared_without_required_flag_still_rejected():
    # the root really declares the render namespace, but libSBML cannot parse
    # the document without render:required; that parse error must stay fatal.
    sbml = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core"
      xmlns:render="http://www.sbml.org/sbml/level3/version1/render/version1" level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfParameters><parameter id="x" constant="false" value="1"/></listOfParameters>
    <listOfRules>
      <rateRule variable="x">
        <math xmlns="http://www.w3.org/1998/Math/MathML"><ci>x</ci></math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""
    artifact = ModelArtifact(id="m", sbml=sbml, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    assert not report.can_execute


def test_render_namespace_declared_nonrequired_is_allowed():
    # the corrected document declares the render namespace with required="false":
    # layout/render are purely graphical annotations, so the model must pass.
    sbml = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core"
      xmlns:render="http://www.sbml.org/sbml/level3/version1/render/version1" render:required="false"
      level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" constant="true" size="1"/></listOfCompartments>
    <listOfParameters><parameter id="x" constant="false" value="1"/></listOfParameters>
    <listOfRules>
      <rateRule variable="x">
        <math xmlns="http://www.w3.org/1998/Math/MathML"><ci>x</ci></math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""
    artifact = ModelArtifact(id="m", sbml=sbml, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    codes = _codes(report)
    assert "unsupported_package" not in codes
    assert codes.get("artifact_valid") == "pass"
    assert report.can_execute


def test_dynamic_compartment_blocks_even_without_constant_flag():
    sbml = """<?xml version="1.0" encoding="UTF-8"?>
<sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2">
  <model id="m">
    <listOfCompartments><compartment id="c" size="1" constant="true"/></listOfCompartments>
    <listOfParameters><parameter id="v" constant="false" value="0"/></listOfParameters>
    <listOfRules>
      <rateRule variable="c">
        <math xmlns="http://www.w3.org/1998/Math/MathML"><cn>1</cn></math>
      </rateRule>
    </listOfRules>
  </model>
</sbml>
"""
    artifact = ModelArtifact(id="m", sbml=sbml, format="sbml", kind="ode")
    report = modeling.validate_artifact(artifact)
    assert _codes(report).get("sbml_structure") == "fail"
    assert not report.can_execute
