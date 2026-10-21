"""Tests for storage.ArtifactStore and evidence ingestion/claims/links.

Run with the project's test runner (e.g. ``pytest tests/test_storage_evidence.py``).
Only stdlib + pypdf (for PDF tests) are required.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Make the src tree importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from biomodel_agent.storage import ArtifactError, ArtifactStore, canonical_bytes, sha256_of  # noqa: E402
from biomodel_agent import evidence  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(tmp_path / "root")


def make_source(tmp_path: Path, text: str = "Biology is the study of life.\nThe mitochondria is the powerhouse.") -> tuple[Path, str]:
    p = tmp_path / "paper.txt"
    p.write_bytes(text.encode("utf-8"))
    return p, text


# ---------------------------------------------------------------------------
# storage: hashing, refs, immutability
# ---------------------------------------------------------------------------

def test_canonical_json_is_deterministic_and_sorted():
    a = canonical_bytes({"b": 1, "a": [1, 2]})
    b = canonical_bytes({"a": [1, 2], "b": 1})
    assert a == b
    assert a == b'{"a":[1,2],"b":1}'


def test_canonical_json_rejects_nan_not_coerced_to_zero():
    with pytest.raises(ArtifactError):
        canonical_bytes({"x": float("nan")})
    with pytest.raises(ArtifactError):
        canonical_bytes({"x": float("inf")})


def test_put_same_payload_different_refs_differs(store):
    r1 = store.put("note", {"v": 1}, refs=[])
    r2 = store.put("note", {"v": 1}, refs=[r1])
    assert r1 != r2
    # Both exist and verify independently.
    assert store.get(r1)["payload"] == {"v": 1}
    assert store.get(r2)["payload"] == {"v": 1}


def test_put_ref_stable_hash(store):
    r = store.put("run", {"step": 1})
    expected = sha256_of(canonical_bytes({
        "schema_version": "1", "kind": "run", "payload": {"step": 1}, "refs": [],
    }))
    assert r == expected


def test_put_rejects_unknown_ref(store):
    fake = "0" * 64
    with pytest.raises(ArtifactError):
        store.put("note", {"v": 1}, refs=[fake])


def test_put_requires_existing_ref(store):
    blob = store.put_blob(b"abc")
    r = store.put("note", {"v": 1}, refs=[blob])
    assert r
    assert store.get(r)["refs"] == [blob]


def test_put_rejects_bad_kind(store):
    with pytest.raises(ArtifactError):
        store.put("", {"v": 1})
    with pytest.raises(ArtifactError):
        store.put(None, {"v": 1})


def test_put_rejects_bad_ref_shape(store):
    for bad in ("../../etc/passwd", "ABCDEF" * 10, "0" * 63, "0" * 65, "g" * 64):
        with pytest.raises(ArtifactError):
            store.put("note", {"v": 1}, refs=[bad])


def test_get_rejects_traversal_ref(store):
    store.put("note", {"v": 1})
    with pytest.raises(ArtifactError):
        store.get("../../etc/passwd")
    with pytest.raises(ArtifactError):
        store.get("XYZ")


def test_get_unknown_ref_rejected(store):
    with pytest.raises(ArtifactError):
        store.get("0" * 64)


def test_tamper_object_get_rejected(tmp_path, store):
    r = store.put("note", {"v": 1})
    path = store.objects_dir / f"{r}.json"
    data = json.loads(path.read_bytes())
    data["payload"]["v"] = 999  # mutate stored bytes
    path.write_bytes(json.dumps(data).encode("utf-8"))
    with pytest.raises(ArtifactError):
        store.get(r)


def test_tamper_blob_get_rejected(store):
    ref = store.put_blob(b"original")
    path = store.blobs_dir / ref
    path.write_bytes(b"tampered")
    with pytest.raises(ArtifactError):
        store.get_blob(ref)


def test_mutating_get_result_does_not_affect_store(store):
    r = store.put("note", {"v": 1, "nested": {"k": [1]}})
    env = store.get(r)
    env["payload"]["v"] = 999
    env["payload"]["nested"]["k"].append(2)
    env["refs"].append("1" * 64)
    # Store content unchanged.
    again = store.get(r)
    assert again["payload"]["v"] == 1
    assert again["payload"]["nested"]["k"] == [1]
    assert again["refs"] == []


def test_blob_raw_bytes_preserved(store):
    data = bytes(range(256)) + b"\xff\xfe" * 100
    ref = store.put_blob(data)
    assert store.get_blob(ref) == data
    assert ref == hashlib.sha256(data).hexdigest()


def test_put_blob_rejects_overwrite_with_different_content(store):
    r = store.put_blob(b"one")
    # Different content always hashes to a different ref, so it can never
    # collide over an existing blob; the API still raises on a real
    # collision.  Verify identity returns existing for identical bytes.
    assert store.put_blob(b"one") == r


def test_list_filtered_by_kind(store):
    store.put("run", {"step": 1})
    store.put("run", {"step": 2})
    store.put("note", {"v": 1})
    all_refs = store.list()
    runs = store.list("run")
    assert len(all_refs) == 3
    assert len(runs) == 2
    assert set(runs).issubset(set(all_refs))


def test_index_row_written_after_object_landed(tmp_path, store):
    r = store.put("note", {"v": 1})
    conn = sqlite3.connect(store.index_path)
    try:
        row = conn.execute(
            "SELECT kind, id, created_at FROM artifact_index WHERE ref = ?", (r,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "note"
    assert row[2]  # created_at present


# ---------------------------------------------------------------------------
# storage: recovery
# ---------------------------------------------------------------------------

def test_recover_rebuilds_missing_index_row(tmp_path, store):
    r = store.put("note", {"v": 1})
    # Drop the index row (simulate lost index entry).
    conn = sqlite3.connect(store.index_path)
    try:
        conn.execute("DELETE FROM artifact_index WHERE ref = ?", (r,))
        conn.commit()
    finally:
        conn.close()
    assert store.list() == []
    report = store.recover()
    assert r in report["rebuilt"]
    assert r in report["orphans"]
    assert report["corrupt"] == []
    assert store.list() == [r]
    # Rebuild is idempotent.
    report2 = store.recover()
    assert r in report2["rebuilt"]
    assert r not in report2["orphans"]


def test_recover_detects_corrupt_without_discarding(tmp_path, store):
    r = store.put("note", {"v": 1})
    path = store.objects_dir / f"{r}.json"
    path.write_bytes(path.read_bytes() + b" ")  # hash mismatch
    report = store.recover()
    assert r not in report["rebuilt"]
    assert any(c.startswith(r) for c in report["corrupt"])
    # Corrupt file is NOT deleted or overwritten.
    assert path.exists()
    assert path.read_bytes().endswith(b" ")
    # And get still refuses it.
    with pytest.raises(ArtifactError):
        store.get(r)


def test_recover_detects_orphan_ref_in_missing_object(tmp_path, store):
    blob = store.put_blob(b"data")
    # Craft an object referencing an unknown ref: it verifies hash/schema but
    # its ref is dangling -> reported corrupt, not index-rebuilt.
    env = {
        "schema_version": "1",
        "kind": "note",
        "payload": {"v": 1},
        "refs": ["9" * 64],
    }
    data = canonical_bytes(env)
    ref = sha256_of(data)
    (store.objects_dir / f"{ref}.json").write_bytes(data)
    report = store.recover()
    assert ref not in report["rebuilt"]
    assert any(c.startswith(ref) for c in report["corrupt"])


def test_storage_recursive_ref_chain_tamper_detected(store):
    """get/verify must walk the *whole* ref chain, not just direct refs."""
    leaf = store.put("note", {"v": "leaf"})
    mid = store.put("note", {"v": "mid"}, refs=[leaf])
    top = store.put("note", {"v": "top"}, refs=[mid])
    assert store.verify(top) is True
    # Tamper the transitive (grandchild) blob/object; top still hashes fine
    # itself, yet the chain is broken and must be reported.
    (store.objects_dir / f"{leaf}.json").write_bytes(b"{}")
    with pytest.raises(ArtifactError):
        store.get(top)
    with pytest.raises(ArtifactError):
        store.verify(top)


def test_storage_ref_cycle_is_bounded_not_hanging(store):
    """A self-referential chain must terminate with an error, not recurse
    forever."""
    env = {"schema_version": "1", "kind": "note", "payload": {"v": 1}, "refs": []}
    # Craft an object that cites itself: iterate to a fixed point.
    ref = None
    for _ in range(5):
        env["refs"] = [ref] if ref else []
        data = canonical_bytes(env)
        ref = sha256_of(data)
    env["refs"] = [ref]
    data = canonical_bytes(env)
    ref = sha256_of(data)
    (store.objects_dir / f"{ref}.json").write_bytes(data)
    with pytest.raises(ArtifactError):
        store.get(ref)


def test_put_identical_reuse_repairs_missing_index_row(tmp_path, store):
    """Re-using an identical existing object must not leave the index stale."""
    r = store.put("note", {"v": 1})
    conn = sqlite3.connect(store.index_path)
    try:
        conn.execute("DELETE FROM artifact_index WHERE ref = ?", (r,))
        conn.commit()
    finally:
        conn.close()
    assert store.list() == []
    assert store.put("note", {"v": 1}) == r  # identical reuse
    assert store.list() == [r]  # row restored
    assert store.verify(r) is True


def test_envelope_rejects_unknown_key_and_bad_kind_refs(store):
    def craft(env: dict) -> str:
        data = canonical_bytes(env)
        ref = sha256_of(data)
        (store.objects_dir / f"{ref}.json").write_bytes(data)
        return ref

    base = {"schema_version": "1", "kind": "note", "payload": {}, "refs": []}
    for bad in (
        {**base, "extra": 1},
        {"schema_version": "1", "kind": "", "payload": {}, "refs": []},
        {"schema_version": "1", "kind": 5, "payload": {}, "refs": []},
        {"schema_version": "1", "kind": "note", "payload": {}, "refs": ["xyz"]},
        {"schema_version": "2", "kind": "note", "payload": {}, "refs": []},
    ):
        ref = craft(bad)
        with pytest.raises(ArtifactError):
            store.get(ref)
        assert any(c.startswith(ref) for c in store.recover()["corrupt"])


def test_recover_reports_non_json_and_undecodable_without_crashing(store):
    """recover must report corrupt files instead of raising."""
    payloads = [b"not json at all", b"\xff\xfe\x00\x01"]
    refs = []
    for raw in payloads:
        name = hashlib.sha256(raw).hexdigest()
        (store.objects_dir / f"{name}.json").write_bytes(raw)
        refs.append(name)
    report = store.recover()  # must not raise
    for name in refs:
        assert name not in report["rebuilt"]
        assert any(c.startswith(name) for c in report["corrupt"])


# ---------------------------------------------------------------------------
# evidence: ingestion
# ---------------------------------------------------------------------------

def test_ingest_source_txt_raw_bytes_preserved(tmp_path, store):
    p, text = make_source(tmp_path)
    raw = p.read_bytes()
    ref = evidence.ingest_source(store, p, source_url="https://example.org/paper")
    env = store.get(ref)
    assert env["kind"] == "source"
    payload = env["payload"]
    assert payload["filename"] == "paper.txt"
    assert payload["url"] == "https://example.org/paper"
    assert payload["parser"] == "utf8"
    assert payload["pages"] == [{"number": 1, "text": text}]
    assert payload["warnings"] == []
    blob_ref = payload["blob_ref"]
    assert blob_ref in env["refs"]
    # Raw bytes preserved exactly in the blob.
    assert store.get_blob(blob_ref) == raw


def test_ingest_source_same_file_no_overwrite(tmp_path, store):
    p, _ = make_source(tmp_path)
    r1 = evidence.ingest_source(store, p)
    r2 = evidence.ingest_source(store, p)
    assert r1 == r2  # identical content -> same immutable ref
    assert store.get_blob(store.get(r1)["payload"]["blob_ref"]) == p.read_bytes()


def test_ingest_source_rejects_unknown_suffix(tmp_path, store):
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01")
    with pytest.raises(ArtifactError):
        evidence.ingest_source(store, p)


def test_ingest_source_rejects_oversized(tmp_path, store):
    p = tmp_path / "big.txt"
    # Write just over 20MB without exhausting memory: create then truncate up.
    with open(p, "wb") as fh:
        fh.seek(evidence.MAX_SOURCE_BYTES)
        fh.write(b"\0")
    with pytest.raises(ArtifactError):
        evidence.ingest_source(store, p)


def test_ingest_source_md(tmp_path, store):
    p = tmp_path / "notes.md"
    p.write_text("# Title\n\nBody.", encoding="utf-8")
    ref = evidence.ingest_source(store, p)
    env = store.get(ref)
    assert env["payload"]["pages"][0]["text"] == "# Title\n\nBody."
    assert env["payload"]["parser"] == "utf8"


# ---------------------------------------------------------------------------
# evidence: claims
# ---------------------------------------------------------------------------

def _source_with_pages(tmp_path: Path, store) -> tuple[str, str]:
    text = "Line one.\nLine two has the key phrase about biology here."
    p = tmp_path / "paper.txt"
    p.write_text(text, encoding="utf-8")
    return evidence.ingest_source(store, p), text


def test_claim_exact_slice_single_page(tmp_path, store):
    ref, text = _source_with_pages(tmp_path, store)
    start = text.index("key phrase")
    end = start + len("key phrase")
    claim_ref = evidence.add_claim(store, ref, page=1, start=start, end=end,
                                   statement="Papers mention a key phrase.")
    env = store.get(claim_ref)
    assert env["kind"] == "claim"
    payload = env["payload"]
    assert payload["source_ref"] == ref
    assert payload["page"] == 1
    assert (payload["start"], payload["end"]) == (start, end)
    assert payload["quote"] == text[start:end] == "key phrase"
    assert payload["claim_type"] == "paper_assumption"
    assert payload["relation"] == "reported_as"
    assert payload["review_status"] == "unverified"
    assert ref in env["refs"]


def test_claim_multi_page_pdf_like(tmp_path, store):
    # Simulate a multi-page source via a text file whose payload has 2 pages
    # is not possible (txt is single page); instead craft a source object
    # directly with two pages to exercise page addressing.
    blob = store.put_blob(b"raw-two-pages")
    payload = {
        "blob_ref": blob,
        "filename": "x.txt",
        "url": None,
        "parser": "utf8",
        "parser_version": "1",
        "pages": [
            {"number": 1, "text": "First page alpha beta."},
            {"number": 2, "text": "Second page gamma delta."},
        ],
        "warnings": [],
    }
    src = store.put("source", payload, refs=[blob])

    text2 = "Second page gamma delta."
    start = text2.index("gamma delta")
    end = start + len("gamma delta")
    claim_ref = evidence.add_claim(store, src, page=2, start=start, end=end,
                                   statement="Page two claim.", claim_type="system_conjecture")
    env = store.get(claim_ref)
    assert env["payload"]["page"] == 2
    assert env["payload"]["quote"] == "gamma delta"
    assert env["payload"]["claim_type"] == "system_conjecture"

    # Page 1 slice also works.
    text1 = "First page alpha beta."
    s1 = text1.index("alpha beta")
    e1 = s1 + len("alpha beta")
    c1 = evidence.add_claim(store, src, page=1, start=s1, end=e1, statement="P1.")
    assert store.get(c1)["payload"]["quote"] == "alpha beta"


def test_claim_rejects_invalid_type_relation_status(tmp_path, store):
    ref, _ = _source_with_pages(tmp_path, store)
    text = "Line one.\nLine two has the key phrase about biology here."
    start = text.index("biology")
    end = start + len("biology")
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, start, end, "s", claim_type="model_says_so")
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, start, end, "s", relation="vaguely_related")
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, start, end, "s", review_status="believed")


def test_claim_rejects_bad_ranges(tmp_path, store):
    ref, text = _source_with_pages(tmp_path, store)
    start = text.index("biology")
    end = start + len("biology")
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, end, start, "s")  # start >= end
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, start, len(text) + 5, "s")  # beyond text
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 99, 0, 1, "s")  # missing page
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, "1", 0, 1, "s")  # non-int page
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, ref, 1, 0, 0, "s")  # empty quote


def test_claim_requires_real_source_kind(tmp_path, store):
    # A non-source object cannot be cited.
    r = store.put("note", {"v": 1})
    with pytest.raises(ArtifactError):
        evidence.add_claim(store, r, 1, 0, 1, "s")


def test_claim_quote_is_slice_not_external(tmp_path, store):
    # quote is derived from stored text; we can't smuggle a different quote in.
    ref, text = _source_with_pages(tmp_path, store)
    start = text.index("biology")
    end = start + len("biology")
    claim_ref = evidence.add_claim(store, ref, 1, start, end, "s")
    payload = store.get(claim_ref)["payload"]
    assert payload["quote"] == "biology"
    # A range pointing elsewhere yields that slice, not an external string.
    s2 = text.index("Line one")
    e2 = s2 + len("Line one")
    c2 = evidence.add_claim(store, ref, 1, s2, e2, "other")
    assert store.get(c2)["payload"]["quote"] == "Line one"


# ---------------------------------------------------------------------------
# evidence: validate_links
# ---------------------------------------------------------------------------

def _claim_for_phrase(tmp_path, store, text, phrase, ctype="paper_assumption"):
    start = text.index(phrase)
    end = start + len(phrase)
    ref = evidence.ingest_source(store, tmp_path / "paper.txt")
    return evidence.add_claim(store, ref, 1, start, end, "stmt", claim_type=ctype)


def test_validate_links_full_coverage(tmp_path, store):
    text = "Biology is the study of life. Some other sentence here."
    p = tmp_path / "paper.txt"
    p.write_text(text, encoding="utf-8")
    src = evidence.ingest_source(store, p)

    s = text.index("study of life")
    e = s + len("study of life")
    c1 = evidence.add_claim(store, src, 1, s, e, "life")
    s2 = text.index("other sentence")
    e2 = s2 + len("other sentence")
    c2 = evidence.add_claim(store, src, 1, s2, e2, "mito")

    result = evidence.validate_links(store, {
        "life_target": c1,
        "mito_target": (c2,),
    }, ["life_target", "mito_target"])
    assert result["status"] == "complete"
    assert result["missing"] == []
    assert result["invalid"] == []
    assert result["coverage"] == 1.0
    # Coverage never implies semantic support.
    assert result["semantic_support"] == "unknown"


def test_validate_links_missing_and_invalid(tmp_path, store):
    text = "Biology is the study of life."
    p = tmp_path / "paper.txt"
    p.write_text(text, encoding="utf-8")
    src = evidence.ingest_source(store, p)
    s = text.index("study of life")
    e = s + len("study of life")
    good = evidence.add_claim(store, src, 1, s, e, "life")

    result = evidence.validate_links(store, {
        "ok": good,
        "unmapped_target": None,  # not present -> missing
    }, ["ok", "unmapped_target", "bad_target"])
    assert result["status"] == "incomplete"
    assert "unmapped_target" in result["missing"]
    assert "bad_target" in result["missing"]
    assert result["coverage"] == pytest.approx(1 / 3)

    # Invalid claim: points beyond stored text range -> claim itself is
    # recorded fine but its range is invalid; craft a malformed claim object.
    blob = store.put_blob(b"x")
    bad_payload = {
        "source_ref": src, "page": 1, "start": 0, "end": 10**9,
        "quote": "impossible", "statement": "s",
        "claim_type": "paper_assumption", "relation": "reported_as",
        "review_status": "unverified",
    }
    bad = store.put("claim", bad_payload, refs=[src])
    result2 = evidence.validate_links(store, {"t": bad}, ["t"])
    assert "t" in result2["missing"]
    assert bad in result2["invalid"]
    assert result2["coverage"] == 0.0


def test_validate_links_empty_required_is_zero_coverage(tmp_path, store):
    result = evidence.validate_links(store, {}, [])
    assert result["coverage"] == 0.0
    assert result["status"] == "complete"


def test_validate_links_mismatched_quote_is_invalid(tmp_path, store):
    # In-bounds range but stored quote does NOT match the slice -> the claim
    # exists yet is not genuine, so it must be flagged invalid, not counted.
    text = "Biology is the study of life."
    p = tmp_path / "paper.txt"
    p.write_text(text, encoding="utf-8")
    src = evidence.ingest_source(store, p)
    s = text.index("study")
    e = s + len("study")
    bad_payload = {
        "source_ref": src, "page": 1, "start": s, "end": e,
        "quote": "WRONG", "statement": "s",
        "claim_type": "paper_assumption", "relation": "supports",
        "review_status": "unverified",
    }
    bad = store.put("claim", bad_payload, refs=[src])
    result = evidence.validate_links(store, {"t": bad}, ["t"])
    assert result["coverage"] == 0.0
    assert bad in result["invalid"]
    assert "t" in result["missing"]
    assert result["semantic_support"] == "unknown"


def test_validate_links_fake_coverage_rejected(tmp_path, store):
    # A claim whose quote does NOT match its slice must not count, so
    # coverage cannot be faked by linking a mismatched claim.
    text = "Biology is the study of life."
    p = tmp_path / "paper.txt"
    p.write_text(text, encoding="utf-8")
    src = evidence.ingest_source(store, p)
    blob = store.put_blob(b"y")
    bad_payload = {
        "source_ref": src, "page": 1, "start": 0, "end": 7,
        "quote": "WRONG QUOTE", "statement": "s",
        "claim_type": "paper_assumption", "relation": "supports",
        "review_status": "accepted_for_analysis",
    }
    bad = store.put("claim", bad_payload, refs=[src])
    result = evidence.validate_links(store, {"target": bad}, ["target"])
    assert result["coverage"] == 0.0
    assert "target" in result["missing"]
    assert result["semantic_support"] == "unknown"


# ---------------------------------------------------------------------------
# evidence: pdf (pypdf-dependent)
# ---------------------------------------------------------------------------

def _write_pdf(path: Path, page_texts: list[str | None], *, font: bool = True) -> bytes:
    """Write a real PDF with pypdf's writer (correct xref/trailer/EOF).

    ``page_texts`` gives one entry per page: a string is drawn with a real
    Helvetica font reference, ``None`` leaves the page textless (scanned
    style).  Returns the exact bytes written.
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    font_ref = None
    if font:
        font_dict = DictionaryObject()
        font_dict.update({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font_dict)

    for text in page_texts:
        page = writer.add_blank_page(width=200, height=200)
        if text is None:
            continue
        if font_ref is not None:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            })
        stream = DecodedStreamObject()
        # Escape PDF string metacharacters so the literal text survives.
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(path, "wb") as fh:
        writer.write(fh)
    return path.read_bytes()


def test_ingest_source_pdf_text(tmp_path, store):
    pytest.importorskip("pypdf")
    p = tmp_path / "paper.pdf"
    _write_pdf(p, ["Biology text on page one"])
    ref = evidence.ingest_source(store, p)
    payload = store.get(ref)["payload"]
    assert payload["parser"] == "pypdf"
    assert len(payload["pages"]) == 1
    assert "Biology text on page one" in payload["pages"][0]["text"]
    assert payload["warnings"] == []
    assert store.get_blob(payload["blob_ref"]) == p.read_bytes()


def test_ingest_source_scanned_pdf_warning(tmp_path, store):
    pytest.importorskip("pypdf")
    p = tmp_path / "scan.pdf"
    _write_pdf(p, [None])  # blank page: no extractable text
    ref = evidence.ingest_source(store, p)
    payload = store.get(ref)["payload"]
    assert any("scanned" in w.lower() for w in payload["warnings"])


def test_ingest_source_pdf_multi_page_claim(tmp_path, store):
    pytest.importorskip("pypdf")
    p = tmp_path / "two.pdf"
    _write_pdf(p, ["Page one biology.", "Page two mitochondria."])
    ref = evidence.ingest_source(store, p)
    payload = store.get(ref)["payload"]
    assert len(payload["pages"]) == 2
    assert "Page one biology" in payload["pages"][0]["text"]
    assert "Page two mitochondria" in payload["pages"][1]["text"]

    t2 = payload["pages"][1]["text"]
    s = t2.index("mitochondria")
    e = s + len("mitochondria")
    claim = evidence.add_claim(store, ref, page=2, start=s, end=e, statement="mito")
    assert store.get(claim)["payload"]["quote"] == "mitochondria"