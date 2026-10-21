"""Source ingestion and claim-level evidence linking.

Rules (task v01-04-storage):

- :func:`ingest_source` reads a source file read-only, refuses anything over
  ``MAX_SOURCE_BYTES`` (20 MB), and stores the *raw bytes* as an immutable
  blob (never overwritten).
- ``.txt`` / ``.md`` are decoded as UTF-8; ``.pdf`` is parsed page-by-page
  with pypdf.  A scanned (textless) PDF yields a warning, not a failure.
  PDF parsing never executes embedded instructions or scripts -- pypdf only
  extracts text.
- Unknown suffixes are rejected.
- The source object payload is::

      {blob_ref, filename, url, parser, parser_version,
       pages: [{number: 1, text: ...}, ...], warnings: [...]}

  and its refs include ``blob_ref``.
- :func:`add_claim` checks source kind / page / start / end, and takes the
  ``quote`` by *slicing the stored page text* -- no external quote string is
  accepted.  Claim types, relations, and review statuses are closed sets;
  a claim can never be upgraded by code fitting.
- :func:`validate_links` maps targets to claim refs, verifies each claim
  exists with a genuinely valid source quote range, reports missing /
  invalid, and always returns ``semantic_support: "unknown"`` (coverage of
  targets by sources does not mean the claims semantically support them).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .storage import ArtifactError, ArtifactStore

MAX_SOURCE_BYTES = 20 * 1024 * 1024

TEXT_SUFFIXES = {".txt": "utf8", ".md": "utf8"}
PDF_SUFFIX = ".pdf"

CLAIM_TYPES = (
    "experimental_observation",
    "paper_assumption",
    "mathematical_definition",
    "model_inference",
    "system_conjecture",
    "human_convention",
)
RELATIONS = ("reported_as", "supports", "opposes", "derived_from")
REVIEW_STATUSES = (
    "unverified",
    "source_checked",
    "disputed",
    "accepted_for_analysis",
)

SOURCE_KIND = "source"
CLAIM_KIND = "claim"

_PYPDF_VERSION: str | None = None


def _pypdf_version() -> str:
    global _PYPDF_VERSION
    if _PYPDF_VERSION is None:
        try:
            import pypdf  # type: ignore

            v = getattr(pypdf, "__version__", None)
            _PYPDF_VERSION = v if isinstance(v, str) and v else "unknown"
        except Exception:
            _PYPDF_VERSION = "unknown"
    return _PYPDF_VERSION


def _pdf_page_texts(data: bytes, warnings: list[str]) -> list[str]:
    try:
        import pypdf  # type: ignore
    except ImportError as exc:
        raise ArtifactError("pypdf is required to parse PDF sources") from exc
    import io

    pages: list[str] = []
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # never execute embedded content; just record
                warnings.append(f"page {i}: text extraction failed: {exc}")
                text = ""
            pages.append(text)
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(f"failed to parse PDF: {exc}") from exc
    if not any(p.strip() for p in pages):
        warnings.append("PDF appears to be scanned (no extractable text)")
    return pages


def ingest_source(store: ArtifactStore, path: str | os.PathLike, source_url: str | None = None) -> str:
    """Ingest a raw source file into the store; returns the source object ref.

    The raw bytes are stored verbatim as a blob (immutable, no overwrite).
    Only ``.txt``, ``.md`` (UTF-8) and ``.pdf`` (pypdf text extraction) are
    accepted; anything else is rejected.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ArtifactError(f"cannot read source file {path!r}: {exc}") from exc
    if size > MAX_SOURCE_BYTES:
        raise ArtifactError(f"source file too large: {size} bytes > {MAX_SOURCE_BYTES}")

    suffix = p.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        parser = "utf8"
    elif suffix == PDF_SUFFIX:
        parser = "pypdf"
    else:
        raise ArtifactError(f"unsupported source suffix {suffix!r} for {p.name}")

    data = p.read_bytes()  # read-only ingestion; bytes preserved exactly
    blob_ref = store.put_blob(data)
    warnings: list[str] = []

    if parser == "utf8":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactError(f"source {p.name} is not valid UTF-8: {exc}") from exc
        pages = [{"number": 1, "text": text}]
    else:
        pages = [
            {"number": n, "text": t} for n, t in enumerate(_pdf_page_texts(data, warnings), start=1)
        ]

    payload = {
        "blob_ref": blob_ref,
        "filename": p.name,
        "url": source_url,
        "parser": parser,
        "parser_version": _pypdf_version() if parser == "pypdf" else "1",
        "pages": pages,
        "warnings": warnings,
    }
    return store.put(SOURCE_KIND, payload, refs=[blob_ref])


def _source_page_text(store: ArtifactStore, source_ref: str, page: int) -> str:
    env = store.get(source_ref)
    if env.get("kind") != SOURCE_KIND:
        raise ArtifactError(f"{source_ref} is not a source object (kind={env.get('kind')!r})")
    payload = env.get("payload")
    if not isinstance(payload, dict):
        raise ArtifactError(f"{source_ref}: malformed source payload")
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ArtifactError(f"{source_ref}: malformed source pages")
    for entry in pages:
        if isinstance(entry, dict) and entry.get("number") == page:
            text = entry.get("text")
            if not isinstance(text, str):
                raise ArtifactError(f"{source_ref} page {page}: text is not a string")
            return text
    raise ArtifactError(f"{source_ref}: page {page} not found")


def add_claim(
    store: ArtifactStore,
    source_ref: str,
    page: int,
    start: int,
    end: int,
    statement: str,
    claim_type: str = "paper_assumption",
    relation: str = "reported_as",
    review_status: str = "unverified",
) -> str:
    """Record a claim anchored to an exact slice of stored source text.

    ``quote`` is derived by slicing the stored page text at ``start:end``;
    no external quote string is accepted.  Ranges must be valid integers with
    ``0 <= start < end <= len(text)`` and a non-empty quote.  Types,
    relations, and review statuses must be members of the closed sets;
    review status ``source_checked`` means the quote is faithful to the
    source -- it does *not* assert the quoted fact is true.
    """
    store.get(source_ref)  # verify the source exists + hash/refs intact, but
    # keep source_ref as the 64-hex ref string: never overwrite it with the
    # decoded envelope (that would store a dict and break ref validation).
    for name, value in (("page", page), ("start", start), ("end", end)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArtifactError(f"{name} must be an int, got {value!r}")
    if page < 1:
        raise ArtifactError(f"page must be >= 1, got {page}")
    if start < 0 or end <= start:
        raise ArtifactError(f"invalid range start={start} end={end}")
    if not isinstance(statement, str) or not statement:
        raise ArtifactError("statement must be a non-empty string")
    if claim_type not in CLAIM_TYPES:
        raise ArtifactError(f"invalid claim_type {claim_type!r}; allowed: {list(CLAIM_TYPES)}")
    if relation not in RELATIONS:
        raise ArtifactError(f"invalid relation {relation!r}; allowed: {list(RELATIONS)}")
    if review_status not in REVIEW_STATUSES:
        raise ArtifactError(f"invalid review_status {review_status!r}; allowed: {list(REVIEW_STATUSES)}")

    text = _source_page_text(store, source_ref, page)
    if end > len(text):
        raise ArtifactError(f"end={end} exceeds page {page} text length {len(text)}")
    quote = text[start:end]
    if not quote:
        raise ArtifactError("quote slice is empty")

    payload = {
        "source_ref": source_ref,
        "page": page,
        "start": start,
        "end": end,
        "quote": quote,
        "statement": statement,
        "claim_type": claim_type,
        "relation": relation,
        "review_status": review_status,
    }
    return store.put(CLAIM_KIND, payload, refs=[source_ref])


def validate_links(
    store: ArtifactStore,
    element_map: dict[str, str | tuple[str, ...]],
    required_targets: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Check that required targets are linked by valid claims.

    ``element_map`` maps each target to a claim ref or a tuple of claim
    refs.  A claim *counts* only if:

    - the claim object exists and verifies (hash + refs);
    - its ``source_ref`` exists and is a source;
    - ``page`` exists in that source and ``0 <= start < end <= len(text)``;
    - the stored ``quote`` equals ``page_text[start:end]`` (the quote range
      is *genuinely* valid against stored text).

    Targets with no mapping go to ``missing``; mapped but failing claims go
    to ``invalid``.  ``coverage`` is the fraction of required targets with at
    least one valid claim.  A source existing for a claim does *not* mean the
    claim semantically supports the target, so ``semantic_support`` is
    always ``"unknown"``.
    """
    required = list(required_targets)
    missing: list[str] = []
    invalid: list[str] = []
    covered = 0

    for target in required:
        mapping = element_map.get(target)
        if mapping is None:
            missing.append(target)
            continue
        refs = mapping if isinstance(mapping, (tuple, list)) else (mapping,)
        if not refs:
            missing.append(target)
            continue
        ok = False
        for claim_ref in refs:
            if _claim_exists_quoted(store, claim_ref):
                ok = True
                break
            invalid.append(claim_ref)
        if ok:
            covered += 1
        else:
            # Mapped but no valid claim: the target is not covered.
            missing.append(target)

    total = len(required)
    coverage = (covered / total) if total else 0.0
    status = "complete" if not missing and not invalid else "incomplete"
    return {
        "status": status,
        "missing": missing,
        "invalid": invalid,
        "coverage": coverage,
        "semantic_support": "unknown",
    }


def _claim_exists_quoted(store: ArtifactStore, claim_ref: str) -> bool:
    """The quote-bearing validity check used for coverage: the claim must
    exist, verify, cite a real source page, AND its stored ``quote`` must
    equal the stored page text sliced at ``start:end``.  A mismatched or
    out-of-bounds quote means the claim does not genuinely support its
    target, so it is invalid rather than merely uncovered."""
    try:
        env = store.get(claim_ref)
    except ArtifactError:
        return False
    if env.get("kind") != CLAIM_KIND:
        return False
    payload = env.get("payload")
    if not isinstance(payload, dict):
        return False
    source_ref = payload.get("source_ref")
    page = payload.get("page")
    start = payload.get("start")
    end = payload.get("end")
    quote = payload.get("quote")
    if not isinstance(source_ref, str) or not isinstance(quote, str):
        return False
    if isinstance(page, bool) or not isinstance(page, int):
        return False
    if isinstance(start, bool) or not isinstance(start, int):
        return False
    if isinstance(end, bool) or not isinstance(end, int):
        return False
    if not (0 <= start < end):
        return False
    try:
        text = _source_page_text(store, source_ref, page)
    except ArtifactError:
        return False
    if end > len(text):
        return False
    return text[start:end] == quote