"""SBML import / build for ODE drafts.

* :func:`validate_draft` statically checks a
  :class:`~biomodel_agent.domain.contracts.ModelDraft` (expression safety,
  dynamic coverage, dependency cycles, quantity-kind consistency, unit
  provenance) and returns a :class:`ValidationReport`.
* :func:`build_model` compiles a draft into a libSBML document and returns a
  :class:`ModelArtifact` carrying the unmodified ``element_map`` / ``parent_ref``
  of the draft.  Free annotation content (``element_map``) is passed through
  untouched -- nothing is dropped or rewritten.
* :func:`validate_artifact` re-parses produced **or imported** SBML
  (L2V3/L2V4/L3V1/L3V2) and enforces the supported ODE subset that roadrunner
  interprets natively.  "The XML parsed" is never treated as "the model is
  valid": libSBML's consistency diagnostics, explicit unsupported-construct
  checks and assignment/dependency analysis all have to pass.

Units are honest.  Only genuine SBML base units are written; anything else is
reported as an ``unknown`` warning and left *outside* the document rather than
being faked as ``dimensionless``.  Unit *consistency* is reported separately
from unit *provenance*: a model may legitimately carry units that libSBML can
only partially reconcile, so a unit warning never blocks a structurally sound
model, while a real structural error always does.
"""

from __future__ import annotations

import ast
import math
from io import StringIO
import xml.etree.ElementTree

import libsbml

from . import expressions
from .domain.contracts import (
    Check,
    ModelArtifact,
    ModelDraft,
    ValidationReport,
)

# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------

# Canonical base-unit spellings used by this module (SBML's own ids).
UNIT_LITRE = "litre"
UNIT_METRE = "metre"
UNIT_SECOND = "second"
UNIT_MOLE = "mole"
UNIT_DIMENSIONLESS = "dimensionless"
UNIT_MOLE_PER_LITRE = "mole_per_litre"

# A small accepted spelling -> canonical id map.  Everything not listed here is
# "unknown": it is never silently replaced by ``dimensionless``.
_UNIT_ALIASES = {
    "": None,
    "unknown": None,
    "dimensionless": UNIT_DIMENSIONLESS,
    "second": UNIT_SECOND,
    "seconds": UNIT_SECOND,
    "sec": UNIT_SECOND,
    "minute": "minute",
    "minutes": "minute",
    "min": "minute",
    "hour": "hour",
    "hours": "hour",
    "litre": UNIT_LITRE,
    "liter": UNIT_LITRE,
    "l": UNIT_LITRE,
    "metre": UNIT_METRE,
    "meter": UNIT_METRE,
    "mole": UNIT_MOLE,
    "mol": UNIT_MOLE,
    "mole_per_litre": UNIT_MOLE_PER_LITRE,
    "mole_per_liter": UNIT_MOLE_PER_LITRE,
}

#: unit id -> (SBML base kind, multiplier, scale, exponent) for the units that
#: need an explicit UnitDefinition because Level 3 has no kind of their own.
_DERIVED_UNIT_KINDS = {
    "minute": (libsbml.UNIT_KIND_SECOND, 60.0, 0, 1),
    "hour": (libsbml.UNIT_KIND_SECOND, 3600.0, 0, 1),
    UNIT_MOLE_PER_LITRE: None,  # built from two Unit parts (mole / litre)
}

#: Units usable directly through ``setUnits`` (SBML built-in ids).
_BUILTIN_UNITS = frozenset({UNIT_DIMENSIONLESS, UNIT_SECOND, UNIT_METRE, UNIT_MOLE, UNIT_LITRE})


def canonical_unit(unit: object) -> str | None:
    """Canonical SBML unit id for ``unit`` or ``None`` when unknown/absent."""
    if not isinstance(unit, str):
        return None
    key = unit.strip().lower()
    return _UNIT_ALIASES.get(key, None)


def unit_is_unknown(unit: object) -> bool:
    return canonical_unit(unit) is None


def _check(code: str, status: str, message: str = "", severity: str = "blocking") -> Check:
    return Check(code=code, status=status, message=message, severity=severity)


def _unit_warning(code: str, where: str, unit: object) -> Check:
    return _check(
        code, "unknown",
        f"{where}: unit {unit!r} is not a supported SBML unit; it is left undeclared "
        "rather than assumed dimensionless",
        "warning",
    )


# ---------------------------------------------------------------------------
# expression helpers
# ---------------------------------------------------------------------------

def _names_of(tree: ast.Expression) -> set[str]:
    """Expression identifiers, ``time`` included, function names excluded."""
    return expressions.symbols(ast.unparse(tree))


def _parse_safe(text: object, allowed: set[str]) -> tuple[ast.Expression | None, str | None, set[str]]:
    """Return ``(tree, problem, referenced_names)``; never raises."""
    try:
        tree = expressions.parse_expression(text, allowed_symbols=allowed)
    except expressions.ExpressionError as exc:
        return None, str(exc), set()
    return tree, None, _names_of(tree)


def _has_cycle(graph: dict[str, set[str]]) -> str | None:
    """Return a readable cycle description, or ``None`` when acyclic."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def visit(node: str, stack: list[str]) -> str | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in color:
                continue  # unknown target: reported separately
            if color[nxt] == GRAY:
                return " -> ".join(stack[stack.index(nxt):] + [nxt])
            if color[nxt] == WHITE:
                found = visit(nxt, stack)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            found = visit(node, [])
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# draft validation
# ---------------------------------------------------------------------------

def validate_draft(draft: ModelDraft) -> ValidationReport:
    """Run the static check suite over a draft and return a report."""
    if not isinstance(draft, ModelDraft):
        raise TypeError(f"validate_draft expects ModelDraft, got {type(draft).__name__}")

    checks: list[Check] = []
    compartment = draft.compartment
    state_kind = {s.id: s.quantity_kind for s in draft.states}
    parameter_ids = {p.id for p in draft.parameters}
    derived_ids = {d.id for d in draft.derived}
    rate_targets = {r.state for r in draft.rate_rules}
    in_reaction: dict[str, str] = {}
    for rx in draft.reactions:
        for sid in list(rx.reactants) + list(rx.products):
            in_reaction.setdefault(sid, rx.id)

    # ``_RESERVED`` (function names + ``time``) may never be declared symbols.
    reserved_ids = {
        s.id for s in draft.states
    } | parameter_ids | derived_ids | {compartment.id}
    for name in sorted(reserved_ids & expressions._RESERVED):
        checks.append(_check(
            "symbol_reserved", "fail",
            f"declared symbol {name!r} collides with a reserved name "
            f"(function names and the independent variable 'time')",
        ))

    declared = set(state_kind) | parameter_ids | derived_ids | {compartment.id, "time"}

    # --- expression shape / undeclared symbols -----------------------------
    named_expressions: list[tuple[str, str]] = []
    named_expressions += [(f"rate rule for {r.state!r}", r.expression) for r in draft.rate_rules]
    named_expressions += [(f"reaction {rx.id!r} rate", rx.rate) for rx in draft.reactions]
    named_expressions += [(f"derived {d.id!r}", d.expression) for d in draft.derived]
    derived_names: dict[str, set[str]] = {}
    for where, text in named_expressions:
        _tree, problem, _names = _parse_safe(text, declared)
        if problem:
            checks.append(_check("expression_invalid", "fail", f"{where}: {problem}"))
    for d in draft.derived:
        _tree, problem, names = _parse_safe(d.expression, declared)
        derived_names[d.id] = set() if problem else names

    # --- duplicate rate-rule targets ---------------------------------------
    seen_targets: set[str] = set()
    for r in draft.rate_rules:
        if r.state in seen_targets:
            checks.append(_check("rate_duplicate", "fail",
                                 f"state {r.state!r} is the target of more than one rate rule"))
        seen_targets.add(r.state)

    # --- rate rule and reaction writing the same state ---------------------
    for r in draft.rate_rules:
        rx_id = in_reaction.get(r.state)
        if rx_id is not None:
            checks.append(_check(
                "rate_reaction_conflict", "fail",
                f"state {r.state!r} has a rate rule and participates in reaction {rx_id!r}; "
                "its derivative would be defined twice",
            ))

    # --- dynamic coverage: every state needs a reaction or a rate rule -----
    for sid in state_kind:
        if sid not in rate_targets and sid not in in_reaction:
            checks.append(_check(
                "state_not_dynamic", "fail",
                f"state {sid!r} has no rate rule and appears in no reaction; "
                "it would be a constant SBML parameter, not a state",
            ))

    # --- missing parameter / initial values --------------------------------
    for p in draft.parameters:
        if p.value is None:
            checks.append(_check("parameter_missing", "fail", f"parameter {p.id!r} has no value"))
    for s in draft.states:
        if s.initial is None:
            checks.append(_check("initial_missing", "fail", f"state {s.id!r} has no initial value"))
        elif s.nonnegative and s.initial < 0:
            checks.append(_check("initial_negative", "fail",
                                 f"nonnegative state {s.id!r} has negative initial {s.initial!r}"))

    # --- derived dependencies: self reference and cycles --------------------
    graph: dict[str, set[str]] = {}
    for d in draft.derived:
        names = derived_names.get(d.id, set())
        if d.id in names:
            checks.append(_check("derived_cycle", "fail", f"derived {d.id!r} references itself"))
        graph[d.id] = {n for n in names if n in derived_ids and n != d.id}
    cycle = _has_cycle(graph)
    if cycle:
        checks.append(_check("derived_cycle", "fail",
                             f"derived assignment rules contain a dependency cycle: {cycle}"))

    # --- stoichiometry references ------------------------------------------
    for rx in draft.reactions:
        for role, part in (("reactant", rx.reactants), ("product", rx.products)):
            for sid in part:
                if sid not in state_kind:
                    checks.append(_check("stoich_unknown_state", "fail",
                                         f"reaction {rx.id!r}: {role} {sid!r} is not a state"))

    # --- quantity kinds -----------------------------------------------------
    # Amount and concentration species are both encoded natively and may be
    # mixed within one reaction, so 'variable' states are the real conflict.
    for rx in draft.reactions:
        for role, part in (("reactant", rx.reactants), ("product", rx.products)):
            for sid in part:
                if state_kind.get(sid) == "variable":
                    checks.append(_check(
                        "quantity_kind_mixed", "fail",
                        f"reaction {rx.id!r}: {role} {sid!r} has quantity_kind 'variable'; "
                        "reaction species must be 'amount' or 'concentration'",
                    ))
    for s in draft.states:
        if s.quantity_kind in ("amount", "concentration") and s.id in rate_targets:
            checks.append(_check(
                "quantity_kind_mixed", "fail",
                f"state {s.id!r} has quantity_kind {s.quantity_kind!r} but a rate rule; "
                "rate rules require quantity_kind 'variable'",
            ))

    # --- declared profile: rate rules are the only dynamic mechanism ---------
    # (reactions and assignment-rule derived values are supported; nothing is
    # silently dropped, this check only *documents* the profile)
    for s in draft.states:
        if s.id in rate_targets and s.nonnegative:
            checks.append(_check(
                "state_not_dynamic", "fail",
                f"state {s.id!r} is nonnegative but driven by a rate rule; "
                "nonnegativity is not representable in the supported profile",
            ))

    # --- compartment --------------------------------------------------------
    if not (isinstance(compartment.size, float) and math.isfinite(compartment.size) and compartment.size > 0):
        checks.append(_check("compartment_invalid", "fail",
                             f"compartment size must be positive and finite, got {compartment.size!r}"))

    # --- units: unknown is a warning, never silently dimensionless ----------
    for s in draft.states:
        if unit_is_unknown(s.unit):
            checks.append(_unit_warning("unit_unknown", f"state {s.id!r}", s.unit))
    for p in draft.parameters:
        if unit_is_unknown(p.unit):
            checks.append(_unit_warning("unit_unknown", f"parameter {p.id!r}", p.unit))
    for d in draft.derived:
        if unit_is_unknown(d.unit):
            checks.append(_unit_warning("unit_unknown", f"derived {d.id!r}", d.unit))
    if unit_is_unknown(compartment.unit):
        checks.append(_unit_warning("unit_unknown", "compartment", compartment.unit))
    if unit_is_unknown(draft.time_unit):
        checks.append(_unit_warning("unit_unknown", "time", draft.time_unit))
    if canonical_unit(compartment.unit) == UNIT_DIMENSIONLESS and any(
        s.quantity_kind == "concentration" for s in draft.states
    ):
        checks.append(_check(
            "unit_unknown", "unknown",
            "compartment is dimensionless but has concentration species; "
            "amount/concentration cannot be reconciled to a volume unit",
            "warning",
        ))

    # --- summary ------------------------------------------------------------
    failed = [c for c in checks if c.status == "fail"]
    if failed:
        checks.append(_check("draft_valid", "fail", f"{len(failed)} structural problem(s) found"))
    else:
        checks.append(_check("draft_valid", "pass", "all structural checks passed"))
    return ValidationReport(tuple(checks))


# ---------------------------------------------------------------------------
# SBML construction
# ---------------------------------------------------------------------------

def _require_ok(code: object, what: str) -> None:
    if not isinstance(code, int) or isinstance(code, bool):
        return  # an operation-name string / None status means "no error code"
    if code != libsbml.LIBSBML_OPERATION_SUCCESS:
        raise ValueError(f"sbml_build_failed: {what} failed (code={code!r})")


def _ensure_unit_definitions(model, needed: set[str]) -> None:
    """Create UnitDefinitions only for the units actually used.

    Level 3 has no ``minute``/``hour``/``mole_per_litre`` kinds, so those are
    expressed in seconds / mole / litre.  Nothing else is invented.
    """
    for name in sorted(needed):
        if name not in _DERIVED_UNIT_KINDS or model.getUnitDefinition(name) is not None:
            continue
        definition = model.createUnitDefinition()
        definition.setId(name)
        if name == UNIT_MOLE_PER_LITRE:
            mole = definition.createUnit()
            mole.setKind(libsbml.UNIT_KIND_MOLE)
            mole.setExponent(1)
            mole.setScale(0)
            mole.setMultiplier(1.0)
            litre = definition.createUnit()
            litre.setKind(libsbml.UNIT_KIND_LITRE)
            litre.setExponent(-1)
            litre.setScale(-3)
            litre.setMultiplier(1.0)
            continue
        kind, multiplier, scale, exponent = _DERIVED_UNIT_KINDS[name]
        unit = definition.createUnit()
        unit.setKind(kind)
        unit.setMultiplier(multiplier)
        unit.setScale(scale)
        unit.setExponent(exponent)


def _set_units(obj, unit: object) -> bool:
    """Apply a supported unit; return ``False`` when it is unknown."""
    canonical = canonical_unit(unit)
    if canonical is None:
        return False
    obj.setUnits(canonical)
    return True


def _topological_derived(draft: ModelDraft, declared: set[str]) -> list[str]:
    derived_ids = {d.id for d in draft.derived}
    dependencies: dict[str, set[str]] = {}
    for d in draft.derived:
        _tree, _problem, names = _parse_safe(d.expression, declared)
        dependencies[d.id] = {n for n in names if n in derived_ids}
    order: list[str] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in dependencies}

    def visit(node: str) -> None:
        color[node] = GRAY
        for nxt in sorted(dependencies.get(node, ())):
            if color.get(nxt) == WHITE:
                visit(nxt)
        color[node] = BLACK
        order.append(node)

    for key in sorted(dependencies):
        if color[key] == WHITE:
            visit(key)
    return order


def _used_units(units: list[object]) -> set[str]:
    return {c for c in (canonical_unit(u) for u in units) if c is not None}


def build_model(draft: ModelDraft) -> ModelArtifact:
    """Compile a draft into an SBML artifact.

    Raises :class:`ValueError` whose message names every failing check.
    """
    if not isinstance(draft, ModelDraft):
        raise TypeError(f"build_model expects ModelDraft, got {type(draft).__name__}")

    report = validate_draft(draft)
    failed = [c for c in report.checks if c.status == "fail"]
    if failed:
        raise ValueError(
            "build_model rejected draft: "
            + "; ".join(f"{c.code}: {c.message}" for c in failed)
        )

    compartment = draft.compartment
    state_kind = {s.id: s.quantity_kind for s in draft.states}
    declared = (
        set(state_kind)
        | {p.id for p in draft.parameters}
        | {d.id for d in draft.derived}
        | {compartment.id, "time"}
    )
    comp_unit = canonical_unit(compartment.unit)
    has_species = any(kind != "variable" for kind in state_kind.values())
    # A pure rate-rule model carries no spatial quantity: SBML allows a
    # zero-dimensional compartment, which is what makes a dimensionless
    # compartment unit legal alongside it.
    if has_species:
        spatial_dimensions = 3
        comp_unit_for_doc = comp_unit
    else:
        spatial_dimensions = 0
        comp_unit_for_doc = UNIT_DIMENSIONLESS if comp_unit == UNIT_DIMENSIONLESS else None
    if comp_unit_for_doc is not None and canonical_unit(comp_unit_for_doc) is None:
        # an explicit zero-dimensional compartment with an unmappable unit:
        # leave the unit undeclared rather than inventing one
        comp_unit_for_doc = None

    doc = libsbml.SBMLDocument(3, 2)
    model = doc.createModel()
    model.setId(draft.id)
    model.setName(draft.id)
    time_unit = canonical_unit(draft.time_unit)

    _ensure_unit_definitions(model, _used_units(
        [s.unit for s in draft.states]
        + [p.unit for p in draft.parameters]
        + [d.unit for d in draft.derived]
        + [compartment.unit, draft.time_unit]
    ))

    comp = model.createCompartment()
    comp.setId(compartment.id)
    comp.setConstant(True)
    comp.setSize(float(compartment.size))
    comp.setSpatialDimensions(spatial_dimensions)
    if comp_unit_for_doc is not None:
        comp.setUnits(comp_unit_for_doc)
    if time_unit is not None:
        model.setTimeUnits(time_unit)

    # --- parameters ---------------------------------------------------------
    derived_ids = {d.id for d in draft.derived}
    for p in draft.parameters:
        if p.id in derived_ids:  # already rejected by the contracts, defensive
            continue
        param = model.createParameter()
        param.setId(p.id)
        param.setConstant(True)
        if p.value is not None:
            param.setValue(float(p.value))
        _set_units(param, p.unit)

    # --- states -------------------------------------------------------------
    for s in draft.states:
        if s.quantity_kind == "variable":
            # A variable state is driven by a rate rule: SBML represents that as
            # a non-constant parameter; the rule gives its derivative directly.
            param = model.createParameter()
            param.setId(s.id)
            param.setConstant(False)
            if s.initial is not None:
                param.setValue(float(s.initial))
            _set_units(param, s.unit)
            continue

        species = model.createSpecies()
        species.setId(s.id)
        species.setCompartment(compartment.id)
        species.setBoundaryCondition(False)
        species.setConstant(False)
        if s.quantity_kind == "amount":
            species.setHasOnlySubstanceUnits(True)
            if s.initial is not None:
                species.setInitialAmount(float(s.initial))
            # An amount is a substance: mole is the only honest substance unit.
            if canonical_unit(s.unit) == UNIT_MOLE:
                species.setSubstanceUnits(UNIT_MOLE)
            elif canonical_unit(s.unit) == UNIT_DIMENSIONLESS:
                species.setSubstanceUnits(UNIT_DIMENSIONLESS)
        else:
            species.setHasOnlySubstanceUnits(False)
            if s.initial is not None:
                species.setInitialConcentration(float(s.initial))
            # Concentration maps to mole/litre only inside a litre volume.
            if comp_unit == UNIT_LITRE and canonical_unit(s.unit) == UNIT_MOLE_PER_LITRE:
                species.setSubstanceUnits(UNIT_MOLE)

    # --- derived: non-constant parameter + assignment rule, topologically ---
    derived_by_id = {d.id: d for d in draft.derived}
    for mid in _topological_derived(draft, declared):
        definition = derived_by_id[mid]
        param = model.createParameter()
        param.setId(mid)
        param.setConstant(False)
        _set_units(param, definition.unit)
        rule = model.createAssignmentRule()
        rule.setVariable(mid)
        _require_ok(
            rule.setMath(libsbml.parseL3Formula(expressions.to_sbml_formula(definition.expression))),
            f"assignment rule math for {mid!r}",
        )

    # --- rate rules ---------------------------------------------------------
    for r in draft.rate_rules:
        rule = model.createRateRule()
        rule.setVariable(r.state)
        _require_ok(
            rule.setMath(libsbml.parseL3Formula(expressions.to_sbml_formula(r.expression))),
            f"rate rule math for {r.state!r}",
        )

    # --- reactions ----------------------------------------------------------
    for rx in draft.reactions:
        reaction = model.createReaction()
        reaction.setId(rx.id)
        reaction.setReversible(False)
        for sid, stoich in rx.reactants.items():
            ref = reaction.createReactant()
            ref.setSpecies(sid)
            ref.setStoichiometry(float(stoich))
            ref.setConstant(True)
        for sid, stoich in rx.products.items():
            ref = reaction.createProduct()
            ref.setSpecies(sid)
            ref.setStoichiometry(float(stoich))
            ref.setConstant(True)
        law = reaction.createKineticLaw()
        law.setMath(libsbml.parseL3Formula(expressions.to_sbml_formula(rx.rate)))

    # ``checkConsistency`` returns a *diagnostic count*, not an operation code.
    # Only real structural errors are fatal here; unit diagnostics are reported
    # by ``validate_artifact`` instead of aborting a build.
    doc.checkConsistency()
    structural, _unit_diagnostics = _split_error_log(doc)
    structural = _declared_structure_errors(structural, model)
    structural = list(dict.fromkeys(structural))
    if structural:
        raise ValueError(
            "sbml_build_failed: document consistency: " + " | ".join(structural)
        )

    sbml = libsbml.writeSBMLToString(doc)
    if not sbml or not sbml.strip():
        raise ValueError("sbml_build_failed: libSBML produced an empty document")

    artifact = ModelArtifact(
        id=draft.id,
        sbml=sbml,
        element_map=draft.element_map,
        parent_ref=draft.parent_ref,
        format="sbml",
        kind="ode",
    )
    return artifact


# ---------------------------------------------------------------------------
# consistency diagnostics
# ---------------------------------------------------------------------------

#: libSBML error categories that are genuine structure errors.  Everything else
#: (unit reconciliation, package toggles, unrecognised warnings) is reported as
#: an ``unknown`` warning rather than blocking a structurally sound model.
_UNIT_CATEGORIES = frozenset({
    libsbml.LIBSBML_CAT_UNITS_CONSISTENCY,
})


def _split_error_log(doc) -> tuple[list[str], list[str]]:
    """Split the document error log into (structural errors, unit diagnostics).

    A diagnostic counts as an error when ``getSeverity()`` is at least
    ``LIBSBML_SEV_ERROR``; everything below is a warning.  Unit-category
    diagnostics (``LIBSBML_CAT_UNITS_CONSISTENCY``) are separated from
    structural ones, and libSBML unit errors surface as warnings so a structurally
    sound model is not blocked, while every non-unit error stays structural.
    """
    structural: list[str] = []
    unit_diagnostics: list[str] = []
    log = doc.getErrorLog()
    for i in range(log.getNumErrors()):
        error = log.getError(i)
        severity = error.getSeverity()
        if not isinstance(severity, int) or isinstance(severity, bool):
            continue
        message = error.getMessage()
        if error.getCategory() == libsbml.LIBSBML_CAT_UNITS_CONSISTENCY:
            unit_diagnostics.append(message)
        elif severity >= libsbml.LIBSBML_SEV_ERROR:
            structural.append(message)
    return structural, unit_diagnostics


def _declared_structure_errors(structural: list[str], model) -> list[str]:
    """Static structure facts we refuse to run, appended to libSBML diagnostics."""
    problems = list(structural)
    for p in model.getListOfParameters():
        if p.isSetValue() and not math.isfinite(p.getValue()):
            problems.append(f'nonfinite parameter value: {p.getId()}')
    for s in model.getListOfSpecies():
        if ((s.isSetInitialAmount() and not math.isfinite(s.getInitialAmount())) or
                (s.isSetInitialConcentration() and not math.isfinite(s.getInitialConcentration()))):
            problems.append(f'nonfinite initial species value: {s.getId()}')
    if model.getNumCompartments() == 0:
        problems.append("model has no compartment")
    if model.getNumCompartments() > 1:
        problems.append(
            f"model has {model.getNumCompartments()} compartments; only one fixed compartment is supported"
        )
    for i in range(model.getNumCompartments()):
        comp = model.getCompartment(i)
        if not comp.getConstant():
            problems.append(f"compartment {comp.getId()!r} has a dynamic size")
        elif not comp.isSetSize():
            # only an initial assignment can supply the missing size
            if not any(model.getInitialAssignment(j).getSymbol() == comp.getId()
                       for j in range(model.getNumInitialAssignments())):
                problems.append(f"compartment {comp.getId()!r} has no size and no initial assignment")
        elif not (math.isfinite(comp.getSize()) and comp.getSize() > 0):
            problems.append(f"compartment {comp.getId()!r} has non-positive or non-finite size")
    problems.extend(
        f"initial_value_missing: global parameter {pid!r} has no value and no initial assignment"
        for pid in _missing_parameter_inits(model)
    )
    problems.extend(
        f"initial_value_missing: species {sid!r} has no initialAmount/initialConcentration "
        "and no assignment/initial assignment defining it"
        for sid in _missing_species_inits(model)
    )
    return problems


def _missing_parameter_inits(model) -> list[str]:
    """Global constant parameters with no value and no rule/initial-assignment definition."""
    defined: set[str] = set()
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        variable = rule.getVariable()
        if variable and rule.isAssignment():
            defined.add(variable)
    for i in range(model.getNumInitialAssignments()):
        symbol = model.getInitialAssignment(i).getSymbol()
        if symbol:
            defined.add(symbol)
    missing: list[str] = []
    for i in range(model.getNumParameters()):
        param = model.getParameter(i)
        if param.isSetValue() or param.getId() in defined:
            continue
        missing.append(param.getId())
    return missing


def _missing_species_inits(model) -> list[str]:
    """Species without initialAmount/initialConcentration and no defining rule."""
    defined: set[str] = set()
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        variable = rule.getVariable()
        if variable and rule.isAssignment():
            defined.add(variable)
    for i in range(model.getNumInitialAssignments()):
        symbol = model.getInitialAssignment(i).getSymbol()
        if symbol:
            defined.add(symbol)
    missing: list[str] = []
    for i in range(model.getNumSpecies()):
        species = model.getSpecies(i)
        if species.isSetInitialAmount() or species.isSetInitialConcentration():
            continue
        if species.getId() in defined:
            continue
        missing.append(species.getId())
    return missing


def _unsupported_constructs(model) -> list[str]:
    unsupported: list[str] = []
    if model.getNumEvents() > 0:
        unsupported.append(f"unsupported_events: {model.getNumEvents()} event(s)")
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        if rule.isAlgebraic():
            unsupported.append("unsupported_algebraic_rule: algebraic rule(s)")
            break
    for i in range(model.getNumReactions()):
        reaction = model.getReaction(i)
        for refs in (reaction.getListOfReactants(), reaction.getListOfProducts()):
            if refs is None:
                continue
            for j in range(refs.size()):
                reference = refs.get(j)
                if reference is not None and reference.isSetStoichiometryMath() \
                        and reference.getStoichiometryMath() is not None:
                    unsupported.append("unsupported_stoichiometry_math: reaction "
                                       f"{reaction.getId()!r}")
        if reaction.getFast():
            unsupported.append(f"unsupported_fast_reaction: {reaction.getId()!r}")
        law = reaction.getKineticLaw()
        if law is not None and law.getMath() is not None and _has_delay(law.getMath()):
            unsupported.append(f"unsupported_delay: kinetic law of {reaction.getId()!r}")
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        if rule.getMath() is not None and _has_delay(rule.getMath()):
            unsupported.append(f"unsupported_delay: rule for {rule.getVariable()!r}")
    for i in range(model.getNumInitialAssignments()):
        initial = model.getInitialAssignment(i)
        if initial.getMath() is not None and _has_delay(initial.getMath()):
            unsupported.append(f"unsupported_delay: initial assignment for {initial.getSymbol()!r}")
    for i in range(model.getNumFunctionDefinitions()):
        definition = model.getFunctionDefinition(i)
        if definition.getMath() is not None and _has_delay(definition.getMath()):
            unsupported.append(f"unsupported_delay: function {definition.getId()!r}")
    if model.getNumConstraints() > 0:
        unsupported.append(f"unsupported_constraints: {model.getNumConstraints()} constraint(s)")
    if model.isSetConversionFactor():
        unsupported.append("unsupported_conversion_factor: model-level conversionFactor")
    for i in range(model.getNumSpecies()):
        species = model.getSpecies(i)
        if species.isSetConversionFactor():
            unsupported.append(f"unsupported_conversion_factor: species {species.getId()!r}")
    return unsupported


def _package_problems(doc, sbml: str) -> list[str]:
    # libSBML can register or inject graphical namespaces. Inspect original XML.
    declared = set()
    try:
        for event, value in xml.etree.ElementTree.iterparse(
                StringIO(sbml), events=("start-ns", "start")):
            if event == "start":
                root = value
                break
            declared.add(value[1])
    except xml.etree.ElementTree.ParseError:
        return ["unsupported_package: original XML could not be inspected"]
    problems = []
    for i in range(doc.getNumPlugins()):
        plugin = doc.getPlugin(i)
        name, uri = plugin.getPackageName(), plugin.getURI()
        if uri == root.tag.removeprefix('{').split('}')[0]:
            continue  # l3v2extendedmath is part of SBML Core, not an extension.
        if uri not in declared:
            continue
        if name not in ("layout", "render"):
            problems.append(f"unsupported_package: declared mathematical package {name!r}")
        elif doc.getLevel() == 3 and root.attrib.get(f"{{{uri}}}required") not in {"false", "0"}:
            problems.append(f"unsupported_package: graphical package {name!r} must explicitly be nonrequired")
    return problems


# ---------------------------------------------------------------------------
# artifact validation
# ---------------------------------------------------------------------------

def _walk_math(node) -> list:
    out = [node]
    for i in range(node.getNumChildren()):
        out.extend(_walk_math(node.getChild(i)))
    return out


def _math_names(node) -> set[str]:
    return {item.getName() for item in _walk_math(node) if item.isName()}


def _has_delay(node) -> bool:
    return any(item.getType() == libsbml.AST_FUNCTION_DELAY for item in _walk_math(node))


def _dependencies(rule) -> set[str]:
    """Symbol a rule reads, excluding its own target."""
    math_node = rule.getMath()
    if math_node is None:
        return set()
    return _math_names(math_node) - {rule.getVariable()}


def _assignment_analysis(model) -> tuple[list[str], list[str]]:
    """Dependency cycles and undefined symbols across rules / initial assignments."""
    defined = {model.getId(), "time"}
    for i in range(model.getNumCompartments()):
        defined.add(model.getCompartment(i).getId())
    for i in range(model.getNumSpecies()):
        defined.add(model.getSpecies(i).getId())
    for i in range(model.getNumParameters()):
        defined.add(model.getParameter(i).getId())
    for i in range(model.getNumReactions()):
        defined.add(model.getReaction(i).getId())
    for i in range(model.getNumFunctionDefinitions()):
        defined.add(model.getFunctionDefinition(i).getId())

    local_names: set[str] = set()
    for i in range(model.getNumReactions()):
        law = model.getReaction(i).getKineticLaw()
        if law is None:
            continue
        for j in range(law.getNumLocalParameters()):
            local_names.add(law.getLocalParameter(j).getId())

    graph: dict[str, set[str]] = {}
    unknown: list[str] = []

    def add(target: str, names: set[str]) -> None:
        graph.setdefault(target, set()).update(names)
        for name in sorted(names):
            if name in defined or name in local_names:
                continue
            unknown.append(f"{name} (referenced by {target!r})")

    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        if rule.isAssignment() and rule.getMath() is not None:
            add(rule.getVariable(), _math_names(rule.getMath()))
    for i in range(model.getNumInitialAssignments()):
        initial = model.getInitialAssignment(i)
        if initial.getMath() is not None:
            add(initial.getSymbol(), _math_names(initial.getMath()))

    unknown = list(dict.fromkeys(unknown))
    cycle = _has_cycle({k: {n for n in v if n in graph} for k, v in graph.items()})
    return ([cycle] if cycle else []), unknown


def validate_artifact(artifact: ModelArtifact) -> ValidationReport:
    """Parse the artifact's SBML and enforce the supported ODE subset."""
    if not isinstance(artifact, ModelArtifact):
        raise TypeError(f"validate_artifact expects ModelArtifact, got {type(artifact).__name__}")

    checks: list[Check] = []

    if artifact.kind != "ode" or artifact.format != "sbml":
        checks.append(_check(
            "unsupported", "fail",
            f"artifact kind/format {artifact.kind!r}/{artifact.format!r} is not a supported ODE/SBML model",
        ))
        checks.append(_check("artifact_valid", "fail", "unsupported model kind"))
        return ValidationReport(tuple(checks))

    doc = libsbml.readSBMLFromString(artifact.sbml)
    if doc is None:
        checks.append(_check("sbml_parsefail", "fail", "libSBML returned no document"))
        checks.append(_check("artifact_valid", "fail", "SBML parse failed"))
        return ValidationReport(tuple(checks))
    # ``checkConsistency`` returns a *diagnostic count*, never an operation
    # code, and unit diagnostics are not structural failures.
    doc.checkConsistency()
    structural_log, unit_log = _split_error_log(doc)
    if unit_log:
        checks.append(_check(
            "units_consistency", "unknown",
            "libSBML reported unit diagnostics; dimensional correctness is not claimed: "
            + " | ".join(dict.fromkeys(unit_log)),
            "warning",
        ))
    model = doc.getModel()
    if model is None:
        checks.append(_check(
            "sbml_parsefail", "fail",
            "document has no model: "
            + ("; ".join(structural_log) if structural_log else "not a valid SBML model document"),
        ))
        checks.append(_check("artifact_valid", "fail", "SBML parse failed"))
        return ValidationReport(tuple(checks))
    checks.append(_check("sbml_parse", "pass", "document parsed"))

    # --- supported levels ---------------------------------------------------
    doc_level, doc_version = doc.getLevel(), doc.getVersion()
    level, version = model.getLevel(), model.getVersion()
    if (level, version) not in {(2, 3), (2, 4), (3, 1), (3, 2)} \
            or (doc_level, doc_version) not in {(2, 3), (2, 4), (3, 1), (3, 2)}:
        level, version = doc_level, doc_version
        checks.append(_check("sbml_level", "fail", f"unsupported SBML L{level}V{version}"))

    # --- structure ----------------------------------------------------------
    structural = _declared_structure_errors(structural_log, model)
    for i in range(model.getNumCompartments()):
        comp = model.getCompartment(i)
        # A dynamic compartment hides in a rule target or an initial assignment,
        # not only in the constant flag.
        if any(model.getListOfRules().get(j).getVariable() == comp.getId()
               for j in range(model.getNumRules())):
            structural.append(f"compartment {comp.getId()!r} is the target of a rule")
        if any(model.getInitialAssignment(j).getSymbol() == comp.getId()
               for j in range(model.getNumInitialAssignments())):
            structural.append(f"compartment {comp.getId()!r} has an initial assignment")
    if structural:
        checks.append(_check("sbml_structure", "fail",
                             "structure error(s): " + " | ".join(dict.fromkeys(structural))))
    else:
        checks.append(_check("sbml_structure", "pass", "no structural errors at or above ERROR"))
    if unit_log:
        checks.append(_check(
            "units_consistency", "unknown",
            "libSBML reported unit diagnostics; dimensional correctness is not claimed: "
            + " | ".join(dict.fromkeys(unit_log)),
            "warning",
        ))

    # --- unsupported constructs --------------------------------------------
    unsupported = _unsupported_constructs(model) + _package_problems(doc, artifact.sbml)
    if unsupported:
        by_code: dict[str, list[str]] = {}
        for item in dict.fromkeys(unsupported):
            code, _, message = item.partition(": ")
            by_code.setdefault(code, []).append(message)
        for code in sorted(by_code):
            checks.append(_check(code, "fail", "; ".join(by_code[code])))

    # --- assignment / initial-assignment dependency analysis ---------------
    cycles, unknown = _assignment_analysis(model)
    if cycles:
        checks.append(_check("assignment_cycle", "fail",
                             f"assignment/initial dependency cycle: {cycles[0]}"))
    for name in unknown:
        checks.append(_check("assignment_unknown_symbol", "fail",
                             f"assignment references undefined symbol {name}"))

    # --- units: missing units are unknown, not dimensionless ---------------
    missing_units: list[str] = []
    for i in range(model.getNumSpecies()):
        species = model.getSpecies(i)
        if not species.isSetSubstanceUnits():
            missing_units.append(f"species {species.getId()!r}")
    for i in range(model.getNumParameters()):
        param = model.getParameter(i)
        if not param.isSetUnits():
            missing_units.append(f"parameter {param.getId()!r}")
    for i in range(model.getNumCompartments()):
        comp = model.getCompartment(i)
        if not comp.isSetUnits():
            missing_units.append(f"compartment {comp.getId()!r}")
    if not model.isSetTimeUnits():
        missing_units.append("time")
    if missing_units:
        checks.append(_check(
            "units_missing", "unknown",
            "units not declared for: " + ", ".join(missing_units)
            + " (unknown, not assumed dimensionless)",
            "warning",
        ))
    else:
        checks.append(_check("units_missing", "pass",
                             "units declared for every species/parameter/compartment/time"))

    # --- summary ------------------------------------------------------------
    failed = [c for c in checks if c.status == "fail"]
    if failed:
        checks.append(_check("artifact_valid", "fail",
                             f"{len(failed)} structure/unsupported problem(s) found"))
    else:
        checks.append(_check("artifact_valid", "pass", "artifact is a supported ODE/SBML model"))
    return ValidationReport(tuple(checks))
