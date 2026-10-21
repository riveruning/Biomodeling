"""Offline tests for bounded lexical source retrieval (contract v1).

Synthetic sources are created directly as valid source-kind records via
``store.put`` with the same page payload shape ``ingest_source`` produces
(``{"number": int, "text": str}``).  No external data, no network.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src.biomodel_agent.retrieval import RetrievalError, search_sources
from src.biomodel_agent.storage import ArtifactStore


def _put_source(store: ArtifactStore, pages: list[tuple[int, str]], name: str) -> str:
    """Store a synthetic source object shaped like ingest_source output."""
    payload = {
        "blob_ref": None,  # not referenced by retrieval; keep payload shape valid
        "filename": name,
        "url": None,
        "parser": "utf8",
        "parser_version": "1",
        "pages": [{"number": n, "text": t} for n, t in pages],
        "warnings": [],
    }
    return store.put("source", payload)


def _ref(n: int) -> str:
    return "a" * (63 - n) + hex(n)[2:]


@pytest.fixture()
def store(tmp_path: Any) -> ArtifactStore:
    return ArtifactStore(tmp_path / "store")


# ---------------------------------------------------------------------------
# Relevance beyond the first page; basic hit shape
# ---------------------------------------------------------------------------


def test_relevance_beyond_first_page(store: ArtifactStore) -> None:
    src = _put_source(
        store,
        name="synthetic.md",
        [
            (1, "The mitochondria is the powerhouse of the cell."),
            (2, "Nothing relevant here, just filler."),
            (3, "Mitochondria produce ATP during respiration."),
        ],
    )
    out = search_sources(store, [src], "mitochondria atp")
    assert out["method"] == "lexical_v1"
    assert out["query"] == "mitochondria atp"
    hits = {h["page"] for h in out["results"]}
    assert hits == {1, 3}
    top = out["results"][0]
    # Page 3 matches both tokens -> higher score -> first.
    assert top["page"] == 3
    assert top["score"] == 2
    assert sorted(top["matched_terms"]) == ["atp", "mitochondria"]
    # Snippet is an exact substring of the page text.
    page3 = "Mitochondria produce ATP during respiration."
    assert page3[top["start"] : top["end"]] == top["text"]
    assert len(top["text"]) <= 600
    assert out["returned_chars"] == sum(len(h["text"]) for h in out["results"])
    assert out["truncated"] is False


def test_case_insensitive_matching_on_original_text(store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "the mitochondria is the powerhouse.")])
    out = search_sources(store, [src], "POWERHOUSE mitochondria")
    assert len(out["results"]) == 1
    hit = out["results"][0]
    # Offsets are exact in the ORIGINAL string (case preserved as stored).
    assert hit["text"] == "the mitochondria is the powerhouse."
    assert hit["start"] == 0
    assert hit["end"] == len(hit["text"])
    assert hit["score"] == 2


# ---------------------------------------------------------------------------
# Unicode: exact original substrings and offsets despite casefold changes
# ---------------------------------------------------------------------------


def test_unicode_casefold_length_change_keeps_original_offsets(
    store: ArtifactStore,
) -> None:
    # Original text uses an uppercase "ß" whose casefold is "ss" (1 -> 2
    # chars).  Any casefold-index-based offset would drift; matches must be
    # located in the ORIGINAL text instead.
    page = "Straße: straße meets ßstraße here"
    src = _put_source(store, name="synthetic.md", [(1, page)])
    out = search_sources(store, [src], "straße ßstraße")
    assert len(out["results"]) == 1
    hit = out["results"][0]
    assert hit["text"] == page
    assert page[hit["start"] : hit["end"]] == hit["text"]
    assert hit["score"] == 2
    # The first match start is the original offset of the first "straße".
    assert re.search("straße", page, re.IGNORECASE).start() == 0


# ---------------------------------------------------------------------------
# Source whitelist enforcement
# ---------------------------------------------------------------------------


def test_search_never_calls_store_list_and_never_leaves_whitelist(
    store: ArtifactStore,
) -> None:
    allowed = _put_source(store, name="synthetic.md", [(1, "needle in the haystack")])
    foreign = _put_source(store, name="synthetic.md", [(1, "needle also here")])
    calls = {"list": 0, "get": []}

    orig_get = store.get
    orig_list = store.list

    def spy_get(ref: str) -> dict[str, Any]:
        calls["get"].append(ref)
        return orig_get(ref)

    def spy_list(*args: Any, **kwargs: Any) -> list[str]:
        calls["list"] += 1
        return orig_list(*args, **kwargs)

    store.get = spy_get  # type: ignore[method-assign]
    store.list = spy_list  # type: ignore[method-assign]
    try:
        out = search_sources(store, [allowed], "needle")
    finally:
        store.get = orig_get  # type: ignore[method-assign]
        store.list = orig_list  # type: ignore[method-assign]

    assert out["results"][0]["source_ref"] == allowed
    assert calls["list"] == 0
    assert set(calls["get"]) == {allowed}
    assert foreign not in calls["get"]


def test_foreign_ref_rejected(store: ArtifactStore) -> None:
    allowed = _put_source(store, name="synthetic.md", [(1, "alpha beta")])
    with pytest.raises(ValueError):
        search_sources(store, [_ref(7)], "alpha")  # unknown ref
    # A non-source object is a foreign ref too.
    note_ref = store.put("note", {"text": "not a source"})
    with pytest.raises(ValueError):
        search_sources(store, [allowed, note_ref], "alpha")


def test_ref_type_shape_validation(store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "alpha")])
    with pytest.raises(ValueError):
        search_sources(store, ("nope",), "alpha")  # tuple of malformed refs
    with pytest.raises(ValueError):
        search_sources(store, [123], "alpha")  # non-string ref
    with pytest.raises(ValueError):
        search_sources(store, [src, src], "alpha")  # duplicate refs
    with pytest.raises(ValueError):
        search_sources(store, [], "alpha")  # empty
    with pytest.raises(ValueError):
        search_sources(store, "not-a-list", "alpha")


# ---------------------------------------------------------------------------
# Unsupported / malformed arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "123 456",
        "!!!",
        42,
        None,
    ],
)
def test_rejects_query_without_tokens(query: Any) -> None:
    with pytest.raises(ValueError):
        search_sources(object.__new__(ArtifactStore), ["a" * 64], query)  # store unused


@pytest.mark.parametrize("limit", [0, 11, 2.0, "3", True, False, None])
def test_rejects_bad_limit(limit: Any, store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "alpha beta")])
    with pytest.raises(ValueError):
        search_sources(store, [src], "alpha", limit=limit)


@pytest.mark.parametrize("max_chars", [255, 8001, 1.5, "4000", True, None])
def test_rejects_bad_max_chars(max_chars: Any, store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "alpha beta")])
    with pytest.raises(ValueError):
        search_sources(store, [src], "alpha", max_chars=max_chars)


def test_rejects_query_over_256_chars(store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "alpha")])
    with pytest.raises(ValueError):
        search_sources(store, [src], "alpha " * 64)  # 512 chars


def test_rejects_query_over_16_unique_tokens(store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, " ".join(f"w{i}" for i in range(17)))])
    with pytest.raises(ValueError):
        search_sources(store, [src], " ".join(f"w{i}" for i in range(17)))


def test_query_tokens_allow_underscores(store: ArtifactStore) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "Na_K pump and NaK interaction")])
    out = search_sources(store, [src], "Na_K")
    assert len(out["results"]) == 1
    assert "na_k" in out["results"][0]["matched_terms"]


# ---------------------------------------------------------------------------
# Deterministic stable ranking
# ---------------------------------------------------------------------------


def test_stable_ranking_by_score_then_source_page_start(
    store: ArtifactStore,
) -> None:
    src_b = _put_source(
        store,
        name="synthetic.md",
        [
            (1, "zebra apple apple"),  # score 2 (apple+zebra? no: zebra only on b1)
            (2, "apple only"),
        ],
    )
    src_a = _put_source(
        store,
        name="synthetic.md",
        [
            (2, "apple mid apple"),  # score 2
            (1, "apple low"),
        ],
    )
    out = search_sources(store, [src_b, src_a], "apple mid zebra")
    pages = [(h["source_ref"], h["page"]) for h in out["results"]]
    expected = [
        (src_b, 1),  # score 2 (apple, zebra), source order first
        (src_a, 2),  # score 2 (apple, mid), later source
        (src_a, 1),  # score 1
        (src_b, 2),  # score 1
    ]
    assert pages == expected
    scores = [h["score"] for h in out["results"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# No-match behavior
# ---------------------------------------------------------------------------


def test_no_match_returns_empty_results_not_error(
    store: ArtifactStore,
) -> None:
    src = _put_source(store, name="synthetic.md", [(1, "nothing to see"), (2, "still nothing")])
    out = search_sources(store, [src], "quantum flux")
    assert out["results"] == []
    assert out["returned_chars"] == 0
    assert out["truncated"] is False
    assert out["method"] == "lexical_v1"


# ---------------------------------------------------------------------------
# Per-hit and total output limits
# ---------------------------------------------------------------------------


def test_snippet_bounded_600_and_around_match(store: ArtifactStore) -> None:
    filler = "x" * 300
    src = _put_source(store, name="synthetic.md", [(1, filler + "needlenow" + filler)])
    out = search_sources(store, [src], "needle", max_chars=8000)
    hit = out["results"][0]
    assert len(hit["text"]) <= 600
    assert "needle" in hit["text"]
    # Snippet starts up to 120 chars before the first match.
    match_start = re.search("needle", "x" * 300 + "needlenow" + "x" * 300).start()
    assert hit["start"] == max(0, match_start - 120)
    assert hit["end"] - hit["start"] == len(hit["text"])


def test_total_output_bounded_by_max_chars(store: ArtifactStore) -> None:
    pages = [(i + 1, f"needle {i} " + "pad" * 50) for i in range(8)]
    src = _put_source(store, name="synthetic.md", pages)
    out = search_sources(store, [src], "needle", limit=10, max_chars=500)
    total = out["returned_chars"]
    assert total <= 500
    assert total == sum(len(h["text"]) for h in out["results"])
    for h in out["results"]:
        assert h["text"]  # no empty hits
    assert out["truncated"] is True


def test_limit_caps_result_count(store: ArtifactStore) -> None:
    pages = [(i + 1, f"needle {i} some padding text") for i in range(8)]
    src = _put_source(store, name="synthetic.md", pages)
    out = search_sources(store, [src], "needle", limit=3, max_chars=8000)
    assert len(out["results"]) == 3
    assert out["truncated"] is True
    out_all = search_sources(store, [src], "needle", limit=10, max_chars=8000)
    assert len(out_all["results"]) == 8
    assert out_all["truncated"] is False


# ---------------------------------------------------------------------------
# Scan budget rejection (no silent skipping)
# ---------------------------------------------------------------------------


def test_page_scan_budget_exceeded_raises(store: ArtifactStore) -> None:
    # 2001 matching pages: every page contains "q".
    pages = [(i + 1, "q") for i in range(2001)]
    src = _put_source(store, name="synthetic.md", pages)
    with pytest.raises(ValueError):
        search_sources(store, [src], "q")


def test_char_scan_budget_exceeded_raises(store: ArtifactStore) -> None:
    # 300 pages x 8000 chars = 2,400,000 source chars > 2,000,000.
    pages = [(i + 1, "z" * 8000) for i in range(300)]
    src = _put_source(store, name="synthetic.md", pages)
    with pytest.raises(ValueError):
        search_sources(store, [src], "q")


def test_budget_within_limits_ok(store: ArtifactStore) -> None:
    pages = [(i + 1, "z" * 5000) for i in range(200)]  # 1,000,000 chars, 200 pages
    src = _put_source(store, name="synthetic.md", pages)
    out = search_sources(store, [src], "q")
    assert out["results"] == []
    assert out["truncated"] is False


# ---------------------------------------------------------------------------
# Malformed source payload / pages
# ---------------------------------------------------------------------------


def test_malformed_pages_rejected(store: ArtifactStore) -> None:
    bad = store.put(
        "source",
        {
            "blob_ref": None,
            "filename": "b.md",
            "url": None,
            "parser": "utf8",
            "parser_version": "1",
            "pages": [{"number": 1, "text": "ok"}, {"number": 2, "text": 42}],
            "warnings": [],
        },
    )
    with pytest.raises(ValueError):
        search_sources(store, [bad], "ok")


def test_duplicate_page_numbers_rejected(store: ArtifactStore) -> None:
    bad = store.put(
        "source",
        {
            "blob_ref": None,
            "filename": "d.md",
            "url": None,
            "parser": "utf8",
            "parser_version": "1",
            "pages": [{"number": 1, "text": "a"}, {"number": 1, "text": "b"}],
            "warnings": [],
        },
    )
    with pytest.raises(ValueError):
        search_sources(store, [bad], "a")


def test_non_positive_page_number_rejected(store: ArtifactStore) -> None:
    bad = store.put(
        "source",
        {
            "blob_ref": None,
            "filename": "n.md",
            "url": None,
            "parser": "utf8",
            "parser_version": "1",
            "pages": [{"number": 0, "text": "a"}],
            "warnings": [],
        },
    )
    with pytest.raises(ValueError):
        search_sources(store, [bad], "a")


def test_source_filename_and_metadata_not_searched(store: ArtifactStore) -> None:
    # The query token only appears in the filename/metadata, not in any
    # page text: no hits, and nothing is read from payload metadata.
    payload = {
        "blob_ref": None,
        "filename": "zyxword_only_in_name.md",
        "url": None,
        "parser": "utf8",
        "parser_version": "1",
        "pages": [{"number": 1, "text": "nothing to match"}],
        "warnings": [],
    }
    src = store.put("source", payload)
    out = search_sources(store, [src], "zyxword")
    assert out["results"] == []


# ---------------------------------------------------------------------------
# matched_terms semantics: counted from the whole page, snippet may be shorter
# ---------------------------------------------------------------------------


def test_matched_terms_counted_on_whole_page(store: ArtifactStore) -> None:
    # "secondtoken" occurs only far outside the 600-char snippet around the
    # first match; it must still count toward the page score.
    far = "s" * 800 + "secondtoken"
    src = _put_source(store, name="synthetic.md", [(1, "firsttoken " + far)])
    out = search_sources(store, [src], "firsttoken secondtoken")
    hit = out["results"][0]
    assert hit["score"] == 2
    assert "secondtoken" in hit["matched_terms"]
    assert len(hit["text"]) <= 600
    assert "secondtoken" not in hit["text"]  # snippet cannot imply full coverage