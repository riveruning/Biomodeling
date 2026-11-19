"""Bounded source-text plans and immutable per-chunk agent assessments.

Adapted from the DSH component independently checked on real source receipts.
All offsets refer to original Unicode page text. Scientific support is unknown.
"""
from copy import deepcopy
import hashlib
import json

SCHEMA_PLAN = "dsh_review_chunks/plan/v1"
SCHEMA_RECEIPTS = "dsh_review_chunks/receipts/v1"
STUDY_TYPES = ("experimental", "review", "mathematical_modeling", "uncertain")
MM_VALUES = ("present", "absent", "uncertain")
ROLES = ("methods", "results", "modeling", "other")
MAX_PAGES = 1000
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MIN_MAX_CHARS, MAX_MAX_CHARS = 128, 12000
MAX_EVIDENCE, MAX_RATIONALE = 16, 4000
RESPONSE_FIELDS = frozenset(("chunk_reviewed", "study_type", "mathematical_modeling",
                             "rationale", "evidence"))
EVIDENCE_FIELDS = frozenset(("role", "page", "start", "end", "quote"))
CHUNK_FIELDS = frozenset(("chunk_id", "page", "start", "end", "quote"))
PAGE_FIELDS = frozenset(("page", "chars", "sha256", "empty", "text"))
PLAN_FIELDS = frozenset(("schema", "plan_id", "source_ref", "settings",
                         "pages", "chunks", "empty_pages"))
LEDGER_FIELDS = frozenset(("schema", "plan_id", "receipts"))
RECEIPT_FIELDS = frozenset(("receipt_id", "plan_id", "chunk_id",
                            "response_sha256", "response"))

__all__ = ["make_plan", "record_review", "summarize",
           "ReviewChunksError"]


class ReviewChunksError(ValueError):
    """Malformed input, tampered plan/ledger, or conflicting review."""


def _fail(msg):
    raise ReviewChunksError(msg)


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _cjson(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _sha(obj):
    return hashlib.sha256(_cjson(obj)).hexdigest()


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_entry(page, text):
    return {"page": page, "text": text, "chars": len(text),
            "sha256": _sha_text(text), "empty": text == ""}


def _build_chunks(source_ref, entries, max_chars, overlap):
    """Deterministic per-page windows; step = max_chars - overlap >= 1."""
    # Bound materialized overlap expansion before allocating chunk dictionaries.
    step = max_chars - overlap
    counts = [0 if e['chars'] == 0 else 1 + max(0, (e['chars'] - max_chars + step - 1) // step)
              for e in entries]
    expanded_chars = sum(e['chars'] + max(0, count - 1) * overlap
                         for e, count in zip(entries, counts))
    if sum(counts) > 10000 or expanded_chars > 8 * 1024 * 1024:
        _fail('overlap expansion exceeds 10000 chunks or 8MiB of chunk text')
    chunks = []
    for entry in entries:
        text, page, n = entry["text"], entry["page"], entry["chars"]
        if n == 0:
            continue
        start = 0
        while start < n:
            end = min(start + max_chars, n)
            quote = text[start:end]
            chunks.append({
                "chunk_id": _sha({"schema": SCHEMA_PLAN, "source_ref": source_ref,
                                  "page_sha256": entry["sha256"], "page": page,
                                  "start": start, "end": end, "quote": quote}),
                "page": page, "start": start, "end": end, "quote": quote})
            if end == n:
                break
            start += max_chars - overlap
    return chunks


def make_plan(source_ref, pages, max_chars=6000, overlap=200):
    if not isinstance(source_ref, str) or not source_ref:
        _fail("source_ref must be a non-empty string")
    if not isinstance(pages, list):
        _fail("pages must be a list of {page, text} dicts")
    if not _is_int(max_chars) or not MIN_MAX_CHARS <= max_chars <= MAX_MAX_CHARS:
        _fail("max_chars must be an integer in [%d, %d]" % (MIN_MAX_CHARS, MAX_MAX_CHARS))
    if not _is_int(overlap) or not 0 <= overlap < max_chars:
        _fail("overlap must be an integer with 0 <= overlap < max_chars")
    if len(pages) > MAX_PAGES:
        _fail("more than %d pages" % MAX_PAGES)
    seen, entries, total = set(), [], 0
    for item in pages:
        if not isinstance(item, dict) or frozenset(item) != frozenset(("page", "text")):
            _fail('each page must be a dict with exactly the keys "page" and "text"')
        page, text = item["page"], item["text"]
        if not _is_int(page) or page < 1:
            _fail("page numbers must be positive integers (bool rejected)")
        if not isinstance(text, str):
            _fail("page text must be a string")
        if page in seen:
            _fail("duplicate page number %r" % (page,))
        seen.add(page)
        total += len(text.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            _fail("total UTF-8 size of pages exceeds 2MiB")
        entries.append(_page_entry(page, text))
    entries.sort(key=lambda e: e["page"])
    chunks = _build_chunks(source_ref, entries, max_chars, overlap)
    core = {"schema": SCHEMA_PLAN, "source_ref": source_ref,
            "settings": {"max_chars": max_chars, "overlap": overlap},
            "pages": [{"page": e["page"], "chars": e["chars"], "sha256": e["sha256"]}
                      for e in entries],
            "chunks": [c["chunk_id"] for c in chunks]}
    plan = dict(core)
    plan["plan_id"] = _sha(core)
    plan["pages"] = [dict(e) for e in entries]
    plan["chunks"] = chunks
    plan["empty_pages"] = [e["page"] for e in entries if e["empty"]]
    return plan


def _plan_ctx(plan):
    """Re-verify the whole plan against a deterministic rebuild; any external
    edit of raw text, metadata, offsets, quotes or ids is rejected here."""
    if not isinstance(plan, dict) or frozenset(plan) != PLAN_FIELDS:
        _fail("bad plan structure")
    if plan["schema"] != SCHEMA_PLAN:
        _fail("unknown plan schema")
    if not isinstance(plan["source_ref"], str) or not plan["source_ref"]:
        _fail("plan source_ref must be a non-empty string")
    settings = plan["settings"]
    if (not isinstance(settings, dict)
            or frozenset(settings) != frozenset(("max_chars", "overlap"))):
        _fail("bad plan settings")
    max_chars, overlap = settings["max_chars"], settings["overlap"]
    if not _is_int(max_chars) or not MIN_MAX_CHARS <= max_chars <= MAX_MAX_CHARS:
        _fail("bad plan max_chars")
    if not _is_int(overlap) or not 0 <= overlap < max_chars:
        _fail("bad plan overlap")
    pages = plan["pages"]
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        _fail("bad plan pages")
    seen, total, entries = set(), 0, []
    for p in pages:
        if not isinstance(p, dict) or frozenset(p) != PAGE_FIELDS:
            _fail("bad plan page entry")
        page, text = p["page"], p["text"]
        if not _is_int(page) or page < 1 or not isinstance(text, str):
            _fail("bad plan page number or text")
        if page in seen:
            _fail("duplicate plan page number %r" % (page,))
        seen.add(page)
        if p["chars"] != len(text) or p["empty"] != (text == ""):
            _fail("page metadata mismatch for page %r" % (page,))
        if _sha_text(text) != p["sha256"]:
            _fail("page content digest mismatch (raw text modified) on page %r" % (page,))
        total += len(text.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            _fail("total UTF-8 size of pages exceeds 2MiB")
        entries.append(p)
    if [p["page"] for p in pages] != sorted(seen):
        _fail("plan pages must be sorted by page number")
    rebuilt = _build_chunks(plan["source_ref"], entries, max_chars, overlap)
    chunks = plan["chunks"]
    if not isinstance(chunks, list) or len(chunks) != len(rebuilt):
        _fail("plan chunk list mismatch")
    for got, exp in zip(chunks, rebuilt):
        if not isinstance(got, dict) or frozenset(got) != CHUNK_FIELDS or got != exp:
            _fail("chunk tampered: id/offset/quote mismatches deterministic rebuild")
    if plan["empty_pages"] != [p["page"] for p in pages if p["empty"]]:
        _fail("plan empty_pages mismatch")
    core = {"schema": SCHEMA_PLAN, "source_ref": plan["source_ref"],
            "settings": {"max_chars": max_chars, "overlap": overlap},
            "pages": [{"page": p["page"], "chars": p["chars"], "sha256": p["sha256"]}
                      for p in pages],
            "chunks": [c["chunk_id"] for c in chunks]}
    if plan["plan_id"] != _sha(core):
        _fail("plan_id mismatch (plan was modified)")
    return {"plan_id": plan["plan_id"], "chunks": chunks,
            "by_id": {c["chunk_id"]: c for c in chunks},
            "text_of": {p["page"]: p["text"] for p in pages}}


def _validate_response(response, chunk=None, page_text=None):
    if not isinstance(response, dict) or frozenset(response) != RESPONSE_FIELDS:
        _fail("response must have exactly the fields %s" % sorted(RESPONSE_FIELDS))
    if type(response["chunk_reviewed"]) is not bool:
        _fail("chunk_reviewed must be a bool")
    if response["study_type"] not in STUDY_TYPES:
        _fail("bad study_type")
    if response["mathematical_modeling"] not in MM_VALUES:
        _fail("bad mathematical_modeling value")
    rationale = response["rationale"]
    if not isinstance(rationale, str) or not rationale or len(rationale) > MAX_RATIONALE:
        _fail("rationale must be a non-empty string of at most %d chars" % MAX_RATIONALE)
    evidence = response["evidence"]
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        _fail("evidence must be a list of at most %d items" % MAX_EVIDENCE)
    for ev in evidence:
        if not isinstance(ev, dict) or frozenset(ev) != EVIDENCE_FIELDS:
            _fail("each evidence item must have exactly the fields %s" % sorted(EVIDENCE_FIELDS))
        role, page = ev["role"], ev["page"]
        start, end, quote = ev["start"], ev["end"], ev["quote"]
        if role not in ROLES:
            _fail("bad evidence role")
        if not _is_int(page) or page < 1:
            _fail("evidence page must be a positive integer")
        if not _is_int(start) or not _is_int(end) or not 0 <= start < end:
            _fail("evidence start/end must be integers with 0 <= start < end")
        if not isinstance(quote, str) or not quote:
            _fail("evidence quote must be a non-empty string")
        if chunk is not None:
            if page != chunk["page"]:
                _fail("evidence page %r is not the chunk page %r" % (page, chunk["page"]))
            if start < chunk["start"] or end > chunk["end"]:
                _fail("evidence range [%d, %d) lies outside the chunk" % (start, end))
            if page_text[start:end] != quote:
                _fail("evidence quote does not equal the original text at [start, end); "
                      "a quote found elsewhere is not accepted")


def _ledger_ctx(plan, receipts):
    ctx = _plan_ctx(plan)
    if receipts is None:
        return ctx, {"schema": SCHEMA_RECEIPTS, "plan_id": ctx["plan_id"], "receipts": {}}
    if not isinstance(receipts, dict) or frozenset(receipts) != LEDGER_FIELDS:
        _fail("bad receipts ledger structure")
    if receipts["schema"] != SCHEMA_RECEIPTS:
        _fail("unknown receipts schema")
    if receipts["plan_id"] != ctx["plan_id"]:
        _fail("receipts are bound to a different plan_id; they cannot be reused")
    recs = receipts["receipts"]
    if not isinstance(recs, dict):
        _fail("receipts mapping must be a dict keyed by chunk_id")
    for cid, rec in recs.items():
        if not isinstance(cid, str) or not isinstance(rec, dict) or frozenset(rec) != RECEIPT_FIELDS:
            _fail("bad receipt record")
        if rec["plan_id"] != ctx["plan_id"] or rec["chunk_id"] != cid:
            _fail("receipt binding mismatch")
        chunk = ctx["by_id"].get(cid)
        if chunk is None:
            _fail("receipt references a chunk_id unknown to this plan")
        _validate_response(rec["response"], chunk, ctx["text_of"][chunk["page"]])
        rsha = _sha(rec["response"])
        if rec["response_sha256"] != rsha:
            _fail("receipt response digest mismatch (recorded response was modified)")
        if rec["receipt_id"] != _sha({"plan_id": ctx["plan_id"], "chunk_id": cid,
                                      "response_sha256": rsha}):
            _fail("receipt_id mismatch")
    return ctx, {"schema": SCHEMA_RECEIPTS, "plan_id": ctx["plan_id"],
                 "receipts": deepcopy(recs)}


def record_review(plan, receipts, chunk_id, response):
    """Validate one review and return a NEW ledger; inputs are never mutated.
    Identical rewrite of the same chunk_id is idempotent; a different response
    for an already-recorded chunk_id is refused and the old record is kept."""
    ctx, ledger = _ledger_ctx(plan, receipts)
    if not isinstance(chunk_id, str) or chunk_id not in ctx["by_id"]:
        _fail("unknown chunk_id for this plan")
    chunk = ctx["by_id"][chunk_id]
    _validate_response(response, chunk, ctx["text_of"][chunk["page"]])
    rsha = _sha(response)
    rid = _sha({"plan_id": ctx["plan_id"], "chunk_id": chunk_id,
                "response_sha256": rsha})
    existing = ledger["receipts"].get(chunk_id)
    if existing is not None:
        if existing["response_sha256"] == rsha:
            return ledger
        _fail("conflicting response for chunk %s; earlier record kept" % chunk_id[:16])
    out = {"schema": SCHEMA_RECEIPTS, "plan_id": ctx["plan_id"],
           "receipts": deepcopy(ledger["receipts"])}
    out["receipts"][chunk_id] = {"receipt_id": rid, "plan_id": ctx["plan_id"],
                                 "chunk_id": chunk_id, "response_sha256": rsha,
                                 "response": deepcopy(response)}
    return out


def summarize(plan, receipts):
    """Aggregate a validated ledger. Every verdict is agent_assessed only."""
    ctx, ledger = _ledger_ctx(plan, receipts)
    chunks = ctx["chunks"]
    total = len(chunks)
    reviewed, claims, evidence = set(), [], []
    for cid, rec in ledger["receipts"].items():
        resp = rec["response"]
        if resp["chunk_reviewed"] is True:
            reviewed.add(cid)
        claims.append(resp)
        evidence.extend(dict(ev, chunk_id=cid) for ev in resp["evidence"])
    evidence.sort(key=lambda e: (e["page"], e["start"], e["end"],
                                 e["chunk_id"], e["role"], e["quote"]))
    excluded = any(c["study_type"] in ("review", "mathematical_modeling")
                   or c["mathematical_modeling"] == "present" for c in claims)
    fulltext = total > 0 and len(reviewed) == total
    roles = {e["role"] for e in evidence}
    if excluded:
        status = "excluded_agent_assessed"
    elif (fulltext and any(c["study_type"] == "experimental" for c in claims)
          and all(c["mathematical_modeling"] == "absent" for c in claims)
          and {"methods", "results"} <= roles):
        status = "candidate_agent_assessed"
    else:
        status = "pending_review"
    return {"total_chunks": total,
            "reviewed_chunks": len(reviewed),
            "unreviewed_chunk_ids": [c["chunk_id"] for c in chunks
                                     if c["chunk_id"] not in reviewed],
            "fulltext_reviewed": fulltext,
            "evidence": evidence,
            "agent_assessed": True,
            "scientific_support": "unknown",
            "status": status}
