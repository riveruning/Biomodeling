"""Tests for :mod:`biomodel_agent.literature_scoring`.

Covers the discriminator cases: wrong equation, wrong / fabricated /
duplicate / non-anchored citations, filled-in null expected values, NaN and
bool values, equal-rewrite expressions, malformed gold (duplicate target,
unknown kind, non-finite numbers, bad tolerances, missing probes) and
extra prediction targets.
"""

from __future__ import annotations

import math

import pytest

from biomodel_agent.literature_scoring import score_extraction

ANCHOR = "The maximal growth rate mu_max was 0.65 per hour."
SOURCES = {"src1": "Abstract\n\nThe maximal growth rate mu_max was 0.65 per hour."}

BASE_GOLD = {
    "items": [
        {
            "target": "mu_max",
            "kind": "number",
            "expected": 0.65,
            "support": [{"source_id": "src1", "quote": ANCHOR}],
        },
    ],
    "rtol": 1e-7,
    "atol": 1e-9,
}


def _item(report, target):
    return {entry["target"]: entry for entry in report["items"]}[target]


def test_correct_number_with_valid_citation_is_fully_supported():
    prediction = {
        "facts": {
            "mu_max": {
                "value": 0.65,
                "evidence": [{"source_id": "src1", "quote": ANCHOR}],
            },
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max") == {
        "target": "mu_max",
        "value_status": "pass",
        "evidence_status": "pass",
        "fully_supported": True,
    }
    assert report["counts"] == {
        "total": 1,
        "expected_present": 1,
        "value_correct": 1,
        "evidence_supported": 1,
        "fully_supported": 1,
        "expected_missing": 0,
        "correct_abstentions": 0,
        "unsupported_completions": 0,
    }
    assert report["extra_targets"] == []
    assert report["semantic_review"] == "pending_human_review"
    assert report["expression_method"] == "fixed_probe_agreement_not_symbolic_proof"


def test_value_close_enough_passes():
    prediction = {"facts": {"mu_max": {"value": 0.65000000001, "evidence": []}}, "state": "ok"}
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["value_status"] == "pass"
    assert _item(report, "mu_max")["fully_supported"] is False
    assert report["counts"]["value_correct"] == 1
    assert report["counts"]["fully_supported"] == 0


def test_wrong_number_fails():
    prediction = {"facts": {"mu_max": {"value": 0.66, "evidence": []}}, "state": "ok"}
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["value_status"] == "fail"


def test_wrong_number_with_fabricated_value_still_fails_even_with_real_citation():
    # The citation is real and valid, but the value itself is fabricated.
    prediction = {
        "facts": {
            "mu_max": {"value": 0.66,
                       "evidence": [{"source_id": "src1", "quote": ANCHOR}]},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    item = _item(report, "mu_max")
    assert item["value_status"] == "fail"
    assert item["evidence_status"] == "pass"
    assert item["fully_supported"] is False
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    item = _item(report, "mu_max")
    assert item["value_status"] == "fail"
    assert report["counts"]["value_correct"] == 0


@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), "0.65", [0.65]])
def test_non_numeric_or_nonfinite_values_fail(bad):
    prediction = {"facts": {"mu_max": {"value": bad, "evidence": []}}, "state": "ok"}
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["value_status"] == "fail"
    assert report["counts"]["value_correct"] == 0


def test_error_citation_wrong_source_id_fails_evidence():
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65,
                       "evidence": [{"source_id": "srcX", "quote": ANCHOR}]},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    item = _item(report, "mu_max")
    assert item["value_status"] == "pass"
    assert item["evidence_status"] == "fail"
    assert item["fully_supported"] is False


def test_error_citation_empty_quote_fails_evidence():
    prediction = {
        "facts": {"mu_max": {"value": 0.65, "evidence": [{"source_id": "src1", "quote": ""}]}},
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["evidence_status"] == "fail"


def test_error_citation_not_in_source_fails_evidence():
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65,
                       "evidence": [{"source_id": "src1",
                                     "quote": "The maximal growth rate mu_max was 0.99 per hour."}]},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["evidence_status"] == "fail"


def test_error_citation_exists_but_does_not_cover_anchor_fails_evidence():
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65, "evidence": [{"source_id": "src1", "quote": "Abstract"}]},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    item = _item(report, "mu_max")
    assert item["evidence_status"] == "fail"
    assert item["fully_supported"] is False


def test_citation_must_contain_full_gold_anchor():
    # quote contains only part of the anchor -> not source support.
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65,
                       "evidence": [{"source_id": "src1", "quote": "The maximal growth rate"}]},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert _item(report, "mu_max")["evidence_status"] == "fail"


def test_duplicate_quote_in_source_fails_evidence():
    sources = {"src1": "mu_max is 0.65. Later mu_max is 0.65."}
    gold = {
        "items": [{
            "target": "mu_max", "kind": "number", "expected": 0.65,
            "support": [{"source_id": "src1", "quote": "mu_max is 0.65."}],
        }],
        "rtol": 1e-7, "atol": 1e-9,
    }
    prediction = {
        "facts": {"mu_max": {"value": 0.65,
                             "evidence": [{"source_id": "src1", "quote": "mu_max is 0.65."}]}},
        "state": "ok",
    }
    report = score_extraction(gold, prediction, sources)
    assert _item(report, "mu_max")["evidence_status"] == "fail"


def test_wider_quote_covering_anchor_is_valid():
    prediction = {
        "facts": {
            "mu_max": {
                "value": 0.65,
                "evidence": [{"source_id": "src1", "quote": "The maximal growth rate mu_max "
                                                            "was 0.65 per hour. It was measured."}],
            },
        },
        "state": "ok",
    }
    sources = {"src1": "The maximal growth rate mu_max was 0.65 per hour. It was measured."}
    report = score_extraction(BASE_GOLD, prediction, sources)
    assert _item(report, "mu_max")["evidence_status"] == "pass"
    assert _item(report, "mu_max")["fully_supported"] is True


def test_absent_fact_is_missing():
    report = score_extraction(BASE_GOLD, {"facts": {}, "state": "ok"}, SOURCES)
    item = _item(report, "mu_max")
    assert item["value_status"] == "missing"
    assert item["evidence_status"] == "fail"
    assert item["fully_supported"] is False


def test_missing_fact_in_non_dict_prediction_does_not_crash():
    report = score_extraction(BASE_GOLD, "nonsense", SOURCES)
    assert report["counts"]["total"] == 1
    assert _item(report, "mu_max")["value_status"] == "missing"
    assert _item(report, "mu_max")["fully_supported"] is False


def test_null_expected_absent_value_is_correct_abstention():
    gold = {
        "items": [{"target": "inhibitor_ki", "kind": "number", "expected": None, "support": []}],
        "rtol": 1e-7, "atol": 1e-9,
    }
    report = score_extraction(gold, {"facts": {}, "state": "ok"}, SOURCES)
    item = _item(report, "inhibitor_ki")
    assert item["value_status"] == "correct_abstention"
    assert item["evidence_status"] == "not_applicable"
    assert item["fully_supported"] is False
    assert report["counts"]["correct_abstentions"] == 1
    assert report["counts"]["unsupported_completions"] == 0
    assert report["counts"]["fully_supported"] == 0


def test_null_expected_with_null_value_is_correct_abstention():
    gold = {
        "items": [{"target": "inhibitor_ki", "kind": "number", "expected": None, "support": []}],
        "rtol": 1e-7, "atol": 1e-9,
    }
    report = score_extraction(
        gold, {"facts": {"inhibitor_ki": {"value": None, "evidence": []}}, "state": "ok"}, SOURCES
    )
    assert _item(report, "inhibitor_ki")["value_status"] == "correct_abstention"


def test_null_expected_with_value_is_unsupported_completion():
    gold = {
        "items": [{"target": "inhibitor_ki", "kind": "number", "expected": None, "support": []}],
        "rtol": 1e-7, "atol": 1e-9,
    }
    report = score_extraction(
        gold,
        {"facts": {"inhibitor_ki": {"value": 2.5,
                                    "evidence": [{"source_id": "src1", "quote": "KI = 2.5 uM."}]}},
         "state": "ok"},
        {"src1": "KI = 2.5 uM."},
    )
    item = _item(report, "inhibitor_ki")
    assert item["value_status"] == "unsupported_completion"
    assert item["evidence_status"] == "fail"
    assert item["fully_supported"] is False
    assert report["counts"]["unsupported_completions"] == 1
    assert report["counts"]["correct_abstentions"] == 0


def test_text_exact_match_and_mismatch():
    gold = {
        "items": [{"target": "organism", "kind": "text", "expected": "E. coli",
                   "support": [{"source_id": "src1", "quote": "E. coli"}]}],
        "rtol": 1e-7, "atol": 1e-9,
    }
    ok = score_extraction(
        gold,
        {"facts": {"organism": {"value": "E. coli",
                                "evidence": [{"source_id": "src1", "quote": "E. coli"}]}},
         "state": "ok"},
        {"src1": "We used E. coli as the host."},
    )
    assert _item(ok, "organism")["value_status"] == "pass"
    bad = score_extraction(
        gold,
        {"facts": {"organism": {"value": "E.coli",
                                "evidence": [{"source_id": "src1", "quote": "E. coli"}]}},
         "state": "ok"},
        {"src1": "We used E. coli as the host."},
    )
    assert _item(bad, "organism")["value_status"] == "fail"


EXPR_GOLD = {
    "items": [{
        "target": "growth_law",
        "kind": "expression",
        "expected": "k * x",
        "support": [{"source_id": "src1", "quote": "growth = k * x"}],
        "probes": [{"k": 0.5, "x": 3.0}, {"k": 2.0, "x": 0.25}],
    }],
    "rtol": 1e-7, "atol": 1e-9,
}


def test_expression_wrong_equation_fails_even_with_valid_citation():
    prediction = {
        "facts": {
            "growth_law": {"value": "k + x",
                           "evidence": [{"source_id": "src1", "quote": "growth = k * x"}]},
        },
        "state": "ok",
    }
    report = score_extraction(EXPR_GOLD, prediction, {"src1": "growth = k * x"})
    item = _item(report, "growth_law")
    assert item["value_status"] == "fail"
    assert item["evidence_status"] == "pass"
    assert item["fully_supported"] is False


def test_equal_rewrite_expression_passes_probe_agreement():
    # "x * k" is a different surface form but agrees on every probe.
    prediction = {
        "facts": {
            "growth_law": {"value": "x * k",
                           "evidence": [{"source_id": "src1", "quote": "growth = k * x"}]},
        },
        "state": "ok",
    }
    report = score_extraction(EXPR_GOLD, prediction, {"src1": "growth = k * x"})
    item = _item(report, "growth_law")
    assert item["value_status"] == "pass"
    assert item["fully_supported"] is True
    # The report must not claim symbolic or scientific equivalence.
    assert report["expression_method"] == "fixed_probe_agreement_not_symbolic_proof"
    assert report["semantic_review"] == "pending_human_review"


def test_expression_with_fabricated_symbol_fails():
    # value uses a symbol not probed by gold -> evaluation fails -> fail.
    prediction = {
        "facts": {
            "growth_law": {"value": "k * x * q",
                           "evidence": [{"source_id": "src1", "quote": "growth = k * x"}]},
        },
        "state": "ok",
    }
    report = score_extraction(EXPR_GOLD, prediction, {"src1": "growth = k * x"})
    assert _item(report, "growth_law")["value_status"] == "fail"


def test_expression_non_finite_or_non_string_value_fails():
    for bad in (float("nan"), 7, ["k * x"]):
        prediction = {
            "facts": {"growth_law": {"value": bad,
                                     "evidence": [{"source_id": "src1", "quote": "growth = k * x"}]}},
            "state": "ok",
        }
        report = score_extraction(EXPR_GOLD, prediction, {"src1": "growth = k * x"})
        assert _item(report, "growth_law")["value_status"] == "fail"


def test_extra_targets_are_reported_sorted():
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65, "evidence": [{"source_id": "src1", "quote": ANCHOR}]},
            "zeta": {"value": 1, "evidence": []},
            "alpha": {"value": 2, "evidence": []},
        },
        "state": "ok",
    }
    report = score_extraction(BASE_GOLD, prediction, SOURCES)
    assert report["extra_targets"] == ["alpha", "zeta"]
    # extra targets are not scored as gold items
    assert report["counts"]["total"] == 1
    assert "extra_targets" in report


def test_malformed_prediction_fields_never_crash():
    gold = {
        "items": [
            {"target": "mu_max", "kind": "number", "expected": 0.65,
             "support": [{"source_id": "src1", "quote": ANCHOR}]},
            {"target": "organism", "kind": "text", "expected": "E. coli",
             "support": [{"source_id": "src1", "quote": "E. coli"}]},
        ],
        "rtol": 1e-7, "atol": 1e-9,
    }
    prediction = {
        "facts": {
            "mu_max": {"value": 0.65, "evidence": "not-a-list"},
            "organism": "not-a-dict",
        },
        "state": 42,
    }
    report = score_extraction(gold, prediction, {"src1": "E. coli. " + ANCHOR})
    assert _item(report, "mu_max")["evidence_status"] == "fail"
    assert _item(report, "organism")["value_status"] == "missing"
    assert report["extra_targets"] == []


# ---------------------------------------------------------------------------
# malformed gold must raise ValueError
# ---------------------------------------------------------------------------

def _valid_gold():
    return {
        "items": [
            {"target": "a", "kind": "number", "expected": 1.0,
             "support": [{"source_id": "s", "quote": "q"}]},
        ],
        "rtol": 1e-7, "atol": 1e-9,
    }


def test_gold_duplicate_target_raises():
    gold = _valid_gold()
    gold["items"].append(dict(gold["items"][0]))
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


def test_gold_unknown_kind_raises():
    gold = _valid_gold()
    gold["items"][0]["kind"] = "boolean"
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


@pytest.mark.parametrize("expected", [True, float("nan"), float("inf"), "1.0"])
def test_gold_nonfinite_or_non_numeric_expected_raises(expected):
    gold = _valid_gold()
    gold["items"][0]["expected"] = expected
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


@pytest.mark.parametrize("name", ["rtol", "atol"])
@pytest.mark.parametrize("bad", [None, True, float("nan"), -1.0, "1e-7"])
def test_gold_bad_tolerance_raises(name, bad):
    gold = _valid_gold()
    gold[name] = bad
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


def test_gold_expression_without_probes_raises():
    gold = _valid_gold()
    gold["items"][0] = {"target": "a", "kind": "expression", "expected": "x + 1",
                        "support": [{"source_id": "s", "quote": "q"}]}
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


def test_gold_expression_with_empty_probes_raises():
    gold = _valid_gold()
    gold["items"][0] = {"target": "a", "kind": "expression", "expected": "x + 1",
                        "support": [{"source_id": "s", "quote": "q"}], "probes": []}
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


def test_gold_empty_items_raises():
    with pytest.raises(ValueError):
        score_extraction({"items": [], "rtol": 1e-7, "atol": 1e-9},
                         {"facts": {}, "state": "ok"}, {})


def test_gold_expected_present_needs_support():
    gold = _valid_gold()
    gold["items"][0]["support"] = []
    with pytest.raises(ValueError):
        score_extraction(gold, {"facts": {}, "state": "ok"}, {"s": "q"})


def test_alternative_anchors_from_same_source_are_not_overwritten():
    gold = _valid_gold()
    gold['items'][0]['support'] = [{'source_id': 's', 'quote': 'a=1'},
                                  {'source_id': 's', 'quote': 'a equals one'}]
    result = score_extraction(gold, {'facts': {'a': {'value': 1., 'evidence': [
        {'source_id': 's', 'quote': 'a=1'}]}}}, {'s': 'a=1; a equals one'})
    assert result['counts']['fully_supported'] == 1


@pytest.mark.parametrize('probe', [{'x': True}, {'x': float('nan')}, {}, {'q': 1.}])
def test_invalid_or_unusable_gold_probe_is_rejected(probe):
    gold = _valid_gold()
    gold['items'][0].update(kind='expression', expected='x+1', probes=[probe])
    with pytest.raises(ValueError):
        score_extraction(gold, {}, {'s': 'q'})


def test_omitted_expected_does_not_silently_become_missing_material():
    gold = _valid_gold()
    del gold['items'][0]['expected']
    with pytest.raises(ValueError):
        score_extraction(gold, {}, {'s': 'q'})


def test_huge_integer_and_malformed_source_do_not_crash():
    report = score_extraction(BASE_GOLD, {'facts': {'mu_max': {
        'value': 10**1000, 'evidence': [{'source_id': 'src1', 'quote': ANCHOR}]}}},
        {'src1': 42})
    assert report['items'][0]['value_status'] == 'fail'
    assert report['items'][0]['evidence_status'] == 'fail'
