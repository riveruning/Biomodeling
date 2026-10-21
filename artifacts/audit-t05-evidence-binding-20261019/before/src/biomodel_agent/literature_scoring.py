"""Per-item scorer for literature-extraction development benchmarks.

This module is a development evaluator only.  It scores a frozen gold
extraction against an agent prediction:

* gold is trusted input and is validated once; malformed gold raises
  :class:`ValueError`.  The gold dict is never mutated and never fed to any
  agent in this repository.
* predictions are untrusted: bad per-field data degrades to per-item
  ``fail`` / ``missing`` statuses and never raises.
* a prediction ``value`` is ``pass`` when it matches the gold ``expected``
  (``math.isclose`` for numbers, exact equality for text, probe agreement for
  expressions).
* a citation is *valid* only if its ``source_id`` exists in ``sources``, its
  ``quote`` is a non-empty string that occurs **exactly once** in that source,
  and it contains the full gold support quote for the same source as a
  substring (an "anchor").  A citation that merely exists in the source but
  does not cover the gold anchor does not count.
* ``expected: null`` marks "the material does not contain this fact".  A
  ``null`` / absent value is a correct abstention; any non-null value is an
  ``unsupported_completion`` and is reported separately.
* expression values are compared only by :func:`expressions.evaluate` over
  the gold probes and are reported as ``probe_agreement``, never as symbolic
  or scientific equivalence.  ``semantic_review`` is always
  ``pending_human_review``; no overall success is computed.
"""

from __future__ import annotations

import math

from .expressions import ExpressionError, evaluate

_KINDS = ("number", "expression", "text")
_SEMANTIC_REVIEW = "pending_human_review"
_EXPRESSION_METHOD = "fixed_probe_agreement_not_symbolic_proof"

__all__ = ["score_extraction"]


# ---------------------------------------------------------------------------
# small value helpers
# ---------------------------------------------------------------------------

def _is_number(value: object) -> bool:
    """True for finite int/float; booleans are explicitly not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _numbers_close(a: object, b: object, rtol: float, atol: float) -> bool:
    """Compare two candidate numbers with ``math.isclose``; reject non-numbers."""
    return (
        _is_number(a)
        and _is_number(b)
        and math.isclose(a, b, rel_tol=rtol, abs_tol=atol)
    )


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and len(value) > 0


def _evidence_entries(raw: object) -> list[dict]:
    """Return the citation list of a fact (``[]`` for anything malformed)."""
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _valid_citation(entry: dict, source_text: str, gold_anchors: dict[str, list[str]]) -> bool:
    """Check one candidate citation against source text and gold anchors.

    Valid only if the ``source_id`` exists, the quote is a non-empty string
    occurring exactly once in that source, and the quote contains the full
    gold support quote (anchor) for the same source.
    """
    source_id = entry.get("source_id")
    quote = entry.get("quote")
    if not _is_nonempty_str(source_id) or not _is_nonempty_str(quote):
        return False
    if not isinstance(source_text, str):
        return False
    if not any(anchor in quote for anchor in gold_anchors.get(source_id, [])):
        return False
    return source_text.count(quote) == 1


def _probe_env(probe: dict) -> dict:
    """Build the evaluation environment for one probe (symbols + time)."""
    env: dict = {}
    for key, value in probe.items():
        if key == "time":
            continue
        if _is_number(value):
            env[key] = float(value)
    if _is_number(probe.get("time")):
        env["time"] = float(probe["time"])
    return env


def _probe_agrees(expected_expr: str, value: object, probes: list[dict],
                  rtol: float, atol: float) -> bool:
    """Every probe must give a finite value on both sides that is close.

    Any ``ExpressionError``, missing probe, or non-finite result counts as
    disagreement.  This is agreement on gold probes only, not proof that the
    expressions are symbolically equivalent.
    """
    if not _is_nonempty_str(value) or not probes:
        return False
    for probe in probes:
        if not isinstance(probe, dict):
            return False
        env = _probe_env(probe)
        try:
            expected_value = evaluate(expected_expr, env)
            actual_value = evaluate(value, env)
        except ExpressionError:
            return False
        if not _numbers_close(expected_value, actual_value, rtol, atol):
            return False
    return True


def _value_matches(item: dict, value: object, rtol: float, atol: float) -> bool:
    """Match a predicted value against the gold ``expected`` for its kind."""
    kind = item["kind"]
    if value is None:
        return False
    if kind == "number":
        return _numbers_close(item["expected"], value, rtol, atol)
    if kind == "text":
        return isinstance(value, str) and value == item["expected"]
    return _probe_agrees(item["expected"], value, item["probes"], rtol, atol)


# ---------------------------------------------------------------------------
# gold validation
# ---------------------------------------------------------------------------

def _validate_gold(gold: object) -> None:
    """Raise ``ValueError`` on any malformed gold; never mutates ``gold``."""
    if not isinstance(gold, dict):
        raise ValueError("gold must be a dict")
    items = gold.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("gold must contain a non-empty 'items' list")
    rtol = gold.get("rtol")
    atol = gold.get("atol")
    for name, tol in (("rtol", rtol), ("atol", atol)):
        if not _is_number(tol) or tol < 0:
            raise ValueError(f"gold '{name}' must be a finite non-negative number")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("every gold item must be a dict")
        target = item.get("target")
        if not _is_nonempty_str(target):
            raise ValueError("gold item 'target' must be a non-empty string")
        if target in seen:
            raise ValueError(f"duplicate gold target: {target!r}")
        seen.add(target)
        kind = item.get("kind")
        if kind not in _KINDS:
            raise ValueError(f"unknown gold kind: {kind!r}")
        if "expected" not in item:
            raise ValueError("gold item must explicitly declare expected, including null")
        expected = item["expected"]
        support = item.get("support")
        if expected is not None:
            if kind == "number":
                if not _is_number(expected):
                    raise ValueError(f"gold expected for {target!r} must be a finite number")
            elif kind == "expression":
                if not _is_nonempty_str(expected):
                    raise ValueError(f"gold expected for {target!r} must be an expression string")
                probes = item.get("probes")
                if (not isinstance(probes, list) or not probes
                        or not all(isinstance(p, dict) for p in probes)):
                    raise ValueError(
                        f"gold expression {target!r} needs a non-empty 'probes' list of dicts"
                    )
                for probe in probes:
                    if not probe or not all(_is_nonempty_str(k) and _is_number(v)
                                            for k, v in probe.items()):
                        raise ValueError("gold probes must contain finite named values")
                    try:
                        evaluated = evaluate(expected, probe)
                    except (ExpressionError, ValueError, TypeError, OverflowError) as exc:
                        raise ValueError("gold expression cannot be evaluated on probe") from exc
                    if not _is_number(evaluated):
                        raise ValueError("gold expression must be finite on every probe")
            else:  # text
                if not _is_nonempty_str(expected):
                    raise ValueError(f"gold expected for {target!r} must be a non-empty string")
            if not isinstance(support, list) or not support:
                raise ValueError(f"gold support for {target!r} must be a non-empty list")
            for entry in support:
                if not isinstance(entry, dict):
                    raise ValueError(f"gold support entries for {target!r} must be dicts")
                if not _is_nonempty_str(entry.get("source_id")):
                    raise ValueError(f"gold support source_id for {target!r} must be non-empty")
                if not _is_nonempty_str(entry.get("quote")):
                    raise ValueError(f"gold support quote for {target!r} must be non-empty")
        elif support != []:
            raise ValueError(f"missing gold value {target!r} must have empty support")


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def score_extraction(gold: dict, prediction: object, sources: dict) -> dict:
    """Score ``prediction`` against ``gold`` using ``sources``.

    ``gold`` shape: ``{"items": [{"target", "kind", "expected", "support",
    optional "probes"}], "rtol", "atol"}``.  ``prediction`` shape:
    ``{"facts": {target: {"value", "evidence"}}, "state"}``.  ``sources`` maps
    ``source_id`` to UTF-8 text.  Malformed gold raises ``ValueError``; any
    malformed prediction degrades to per-item ``fail`` / ``missing``.
    """
    _validate_gold(gold)
    rtol = gold["rtol"]
    atol = gold["atol"]

    # Keep all alternative anchors, including several from the same source.
    anchors: dict[str, dict[str, list[str]]] = {}
    for item in gold["items"]:
        by_source: dict[str, list[str]] = {}
        for entry in item.get("support") or []:
            by_source.setdefault(entry["source_id"], []).append(entry["quote"])
        anchors[item["target"]] = by_source

    if not isinstance(sources, dict):
        sources = {}
    if not isinstance(prediction, dict):
        prediction = {}
    facts = prediction.get("facts")
    if not isinstance(facts, dict):
        facts = {}

    items_out: list[dict] = []
    total = 0
    expected_present = 0
    value_correct = 0
    evidence_supported = 0
    fully_supported = 0
    expected_missing = 0
    correct_abstentions = 0
    unsupported_completions = 0

    for item in gold["items"]:
        target = item["target"]
        total += 1
        fact = facts.get(target)
        if not isinstance(fact, dict):
            fact = {}
        has_value = "value" in fact
        value = fact.get("value")
        evidence = _evidence_entries(fact.get("evidence"))

        is_null_expected = item["expected"] is None
        if is_null_expected:
            # Missing material: null / absent value = correct abstention.
            # An explicit null value is indistinguishable from an absent value
            # in both cases and counts the same way.
            if not has_value or value is None:
                value_status = "correct_abstention"
                correct_abstentions += 1
                evidence_status = "not_applicable"
            else:
                value_status = "unsupported_completion"
                unsupported_completions += 1
                evidence_status = "fail"
            expected_missing += 1
        else:
            expected_present += 1
            value_status = ("missing" if not has_value or value is None else
                            "pass" if _value_matches(item, value, rtol, atol) else "fail")
            if value_status == "pass":
                value_correct += 1
            source_texts = {sid: sources[sid]
                            for sid in anchors[target] if sid in sources}
            ev_ok = any(
                _valid_citation(entry, source_texts[entry['source_id']], anchors[target])
                for entry in evidence
                if _is_nonempty_str(entry.get("source_id"))
                and entry["source_id"] in source_texts
            )
            evidence_status = "pass" if ev_ok else "fail"
            if ev_ok:
                evidence_supported += 1
            if value_status == "pass" and ev_ok:
                fully_supported += 1

        items_out.append({
            "target": target,
            "value_status": value_status,
            "evidence_status": evidence_status,
            "fully_supported": bool(
                has_value and value_status == "pass" and evidence_status == "pass"
            ) if not is_null_expected else False,
        })

    counts = {
        "total": total,
        "expected_present": expected_present,
        "value_correct": value_correct,
        "evidence_supported": evidence_supported,
        "fully_supported": fully_supported,
        "expected_missing": expected_missing,
        "correct_abstentions": correct_abstentions,
        "unsupported_completions": unsupported_completions,
    }
    gold_targets = {item["target"] for item in gold["items"]}
    extra_targets = sorted(
        key for key in facts
        if _is_nonempty_str(key) and key not in gold_targets
    )
    return {
        "items": items_out,
        "counts": counts,
        "extra_targets": extra_targets,
        "semantic_review": _SEMANTIC_REVIEW,
        "expression_method": _EXPRESSION_METHOD,
    }
