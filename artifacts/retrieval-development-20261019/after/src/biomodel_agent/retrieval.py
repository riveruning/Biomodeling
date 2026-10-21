"""Versioned lexical retrieval over task-authorized original text.

Implements the public contract in
``artifacts/context-memory-development/retrieval-contract.md`` (v1):

- ``search_sources(store, source_refs, query, *, limit=5, max_chars=4000)``
- Only the supplied source refs are searched.  ``store.list`` is never
  called; no other files, networks, golds, memory or evaluation artifacts
  are touched.  Source text is untrusted data and is never executed.
- Standard library only.  Deterministic ranking; output and scan budgets
  are enforced with ``ValueError`` on budget exceed.
"""

from __future__ import annotations

import re
from typing import Any

from .evidence import SOURCE_KIND
from .storage import ArtifactError, ArtifactStore

# Hard scan budgets per request (contract: enforce, raise on exceed).
MAX_PAGES_PER_REQUEST = 2000
MAX_SOURCE_CHARS_PER_REQUEST = 2_000_000

MAX_SNIPPET_CHARS = 600
SNIPPET_CONTEXT = 120

MAX_SOURCE_REFS = 64
MAX_QUERY_CHARS = 256
MAX_UNIQUE_TOKENS = 16

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
METHODS = ("lexical_v1", "passage_v2")
PASSAGE_CHARS = 480
PASSAGE_OVERLAP = 256  # at least the maximum query-token length


class RetrievalError(ValueError):
    """Contract violation: bad refs, bad arguments, or budget exceeded."""


def validate_method(method: Any) -> str:
    if not isinstance(method, str) or method not in METHODS:
        raise RetrievalError(f"retrieval method must be one of {METHODS}")
    return method


def _require_strict_int(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetrievalError(f"{name} must be a strict int in [{lo}, {hi}], got {value!r}")
    if not (lo <= value <= hi):
        raise RetrievalError(f"{name} must be in [{lo}, {hi}], got {value}")
    return value


def _tokenize(query: str) -> list[str]:
    """Case-insensitive Unicode word tokens; underscore allowed inside words.

    Returns unique tokens in first-seen order; empty list when the query
    contains no tokens (which the caller rejects).
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(query):
        original = match.group(0)
        key = original.casefold()
        lowered = original.lower()
        tok = lowered if len(lowered) == len(original) else original
        if key not in seen:
            seen.add(key)
            tokens.append(tok)
    return tokens


def _validate_query(query: Any) -> tuple[str, list[str]]:
    if not isinstance(query, str) or not query:
        raise RetrievalError(f"query must be a nonempty string, got {query!r}")
    if len(query) > MAX_QUERY_CHARS:
        raise RetrievalError(f"query must be at most {MAX_QUERY_CHARS} chars, got {len(query)}")
    tokens = _tokenize(query)
    if not tokens:
        raise RetrievalError("query must contain at least one word token")
    if len(tokens) > MAX_UNIQUE_TOKENS:
        raise RetrievalError(
            f"query must have at most {MAX_UNIQUE_TOKENS} unique tokens, got {len(tokens)}"
        )
    return query, tokens


def _validate_source_refs(store: ArtifactStore, source_refs: Any) -> list[str]:
    """Validate that the supplied refs are 1..64 distinct strings, all of
    which exist in the store as *source* objects.  Everything is validated
    before any search happens.  Unknown or foreign refs are rejected
    (ValueError, not silently skipped)."""
    if not isinstance(source_refs, (list, tuple)):
        raise RetrievalError(
            f"source_refs must be a list or tuple, got {type(source_refs).__name__}"
        )
    if not (1 <= len(source_refs) <= MAX_SOURCE_REFS):
        raise RetrievalError(
            f"source_refs must contain 1..{MAX_SOURCE_REFS} refs, got {len(source_refs)}"
        )
    refs: list[str] = []
    seen: set[str] = set()
    for ref in source_refs:
        if not isinstance(ref, str):
            raise RetrievalError(f"source_ref must be a string, got {ref!r}")
        if ref in seen:
            raise RetrievalError(f"duplicate source_ref {ref!r}")
        seen.add(ref)
        try:
            env = store.get(ref)
        except ArtifactError as exc:
            raise RetrievalError(f"unknown source_ref {ref!r}: {exc}") from exc
        if env.get("kind") != SOURCE_KIND:
            raise RetrievalError(
                f"source_ref {ref!r} is not a source object (kind={env.get('kind')!r})"
            )
        refs.append(ref)
    return refs


def _validate_pages(pages: Any, source_ref: str) -> list[dict[str, Any]]:
    """Validate a source's pages list: a list of objects whose ``number``
    is a positive int (unique within the source) and whose ``text`` is a
    string.  Returns a fresh copy of the entries for iteration."""
    if not isinstance(pages, list):
        raise RetrievalError(f"{source_ref}: source pages must be a list")
    out: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for i, entry in enumerate(pages):
        if not isinstance(entry, dict):
            raise RetrievalError(f"{source_ref}: pages[{i}] is not an object")
        number = entry.get("number")
        text = entry.get("text")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise RetrievalError(f"{source_ref}: pages[{i}].number must be a positive int")
        if number in seen_numbers:
            raise RetrievalError(f"{source_ref}: duplicate page number {number}")
        if not isinstance(text, str):
            raise RetrievalError(f"{source_ref}: page {number} text must be a string")
        seen_numbers.add(number)
        out.append({"number": number, "text": text})
    return out


def search_sources(
    store: ArtifactStore,
    source_refs: list[str] | tuple[str, ...],
    query: str,
    *,
    limit: int = 5,
    max_chars: int = 4000,
    method: str = "lexical_v1",
) -> dict[str, Any]:
    """Bounded deterministic lexical search over task-authorized sources.

    Returns::

        {
          "query": <str>,
          "method": "lexical_v1",
          "results": [
            {"source_ref", "page", "start", "end", "text",
             "matched_terms", "score"}, ...
          ],
          "returned_chars": <int>,
          "truncated": <bool>,
        }

    Ranking: higher distinct-token match count first, then source order,
    page number, snippet start.  One result per matching page; the snippet
    is an exact original-text substring of at most 600 chars around an
    actual match, with total output bounded by ``max_chars``.
    This remains the default ``lexical_v1`` for compatibility. Explicit
    ``passage_v2`` instead ranks bounded paragraphs/windows, matches complete
    Unicode word tokens and can return several passages from the same page.
    Its matched_terms/score describe only the final returned snippet.
    """
    _require_strict_int("limit", limit, 1, 10)
    _require_strict_int("max_chars", max_chars, 256, 8000)
    query, tokens = _validate_query(query)
    refs = _validate_source_refs(store, source_refs)
    validate_method(method)
    if method == "passage_v2":
        return _search_passages(store, refs, query, tokens, limit, max_chars)

    # ---- scan phase: enforce hard budgets, then rank deterministically ----
    candidates: list[dict[str, Any]] = []
    pages_scanned = 0
    chars_scanned = 0
    for ref in refs:
        env = store.get(ref)
        payload = env.get("payload")
        if not isinstance(payload, dict):
            raise RetrievalError(f"{ref}: malformed source payload")
        pages = _validate_pages(payload.get("pages"), ref)
        for page in pages:
            pages_scanned += 1
            chars_scanned += len(page["text"])
            if pages_scanned > MAX_PAGES_PER_REQUEST:
                raise RetrievalError(
                    f"scan budget exceeded: {pages_scanned} pages > "
                    f"{MAX_PAGES_PER_REQUEST} per request"
                )
            if chars_scanned > MAX_SOURCE_CHARS_PER_REQUEST:
                raise RetrievalError(
                    f"scan budget exceeded: {chars_scanned} source chars > "
                    f"{MAX_SOURCE_CHARS_PER_REQUEST} per request"
                )
            hits = _scan_page(page["text"], tokens)
            if hits:
                candidates.append(
                    {
                        "source_ref": ref,
                        "page": page["number"],
                        "start": hits["first_match"],
                        "snippet": hits["snippet"],
                        "original_text": page["text"],
                        "match_start": hits["match_start"],
                        "match_end": hits["match_end"],
                        "matched_terms": hits["matched_terms"],
                        "score": len(hits["matched_terms"]),
                    }
                )

    # Deterministic order: score desc, then source order (input order),
    # then page number, then snippet start.
    order = {ref: i for i, ref in enumerate(refs)}
    candidates.sort(
        key=lambda c: (
            -c["score"],
            order[c["source_ref"]],
            c["page"],
            c["start"],
        )
    )

    # ---- output phase: limit + char budget ----
    results: list[dict[str, Any]] = []
    used = 0
    for cand in candidates:
        if len(results) >= limit:
            break
        available = min(MAX_SNIPPET_CHARS, max_chars - used)
        if available < cand['match_end'] - cand['match_start']:
            continue
        start = cand['start']
        if cand['match_end'] > start + available:
            start = max(0, cand['match_start'] - (available - (cand['match_end'] - cand['match_start'])) // 2)
        text = cand['original_text'][start:start + available]
        if not text:
            continue
        results.append(
            {
                "source_ref": cand["source_ref"],
                "page": cand["page"],
                "start": start,
                "end": start + len(text),
                "text": text,
                "matched_terms": cand["matched_terms"],
                "score": cand["score"],
            }
        )
        used += len(text)
        if used >= max_chars:
            break
    truncated = len(candidates) > len(results)

    return {
        "query": query,
        "method": "lexical_v1",
        "results": results,
        "returned_chars": used,
        "truncated": truncated,
    }


def _scan_page(text: str, tokens: list[str]) -> dict[str, Any] | None:
    """Find query-token matches in the ORIGINAL text using case-insensitive
    ``re`` searches with escaped tokens.  Matching is done on the original
    text (never on casefold) so that snippet offsets stay exact even for
    casefold length-changing Unicode.

    Returns ``None`` when no token matches (no empty hits), else a dict with
    ``first_match`` (smallest match start), ``snippet`` (exact original
    substring, <=600 chars), and ``matched_terms`` (unique query tokens that
    actually match, in query order).
    """
    first_match: int | None = None
    match_end: int | None = None
    matched: list[str] = []
    for token in tokens:
        m = re.search(re.escape(token), text, re.IGNORECASE)
        if m is None:
            continue
        matched.append(token)
        if first_match is None or m.start() < first_match:
            first_match = m.start()
            match_end = m.end()
    if first_match is None:
        return None
    start = max(0, first_match - SNIPPET_CONTEXT)
    end = min(len(text), start + MAX_SNIPPET_CHARS)
    return {
        "first_match": start,
        "match_start": first_match,
        "match_end": match_end,
        "snippet": text[start:end],
        "matched_terms": matched,
    }


def _passage_ranges(text: str):
    """Pack original paragraphs; split long ones with overlapping exact ranges."""
    pending = None
    previous = 0
    boundaries = ((m.start(), m.end()) for m in re.finditer(r"\n[ \t]*\n", text))
    from itertools import chain
    for end, following in chain(boundaries, [(len(text), len(text))]):
        start = previous
        previous = following
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        if pending is not None and end - pending[0] <= PASSAGE_CHARS:
            pending = (pending[0], end)
            continue
        if pending is not None:
            yield pending
            pending = None
        if end - start <= PASSAGE_CHARS:
            pending = (start, end)
        else:
            for left in range(start, end, PASSAGE_CHARS - PASSAGE_OVERLAP):
                right = min(end, left + PASSAGE_CHARS)
                yield left, right
                if right == end:
                    break
    if pending is not None:
        yield pending


def _passage_matches(text, patterns, start, end):
    # Search original text: Unicode length and word boundaries are never based
    # on a normalized copy. The extra character preserves the right boundary
    # when a window ends inside a longer identifier.
    return [(token, m.start(), m.end()) for token, pattern in patterns
            for m in pattern.finditer(text, start, min(len(text), end + 1))
            if m.end() <= end]


def _search_passages(store, refs, query, tokens, limit, max_chars):
    """Versioned whole-token passage ranking; relevance is not semantic support.

    Rank by distinct terms, capped term frequency / passage word count, then
    source input order, page number and offset. Multiple passages per page are
    allowed, so conflicting statements are not resolved or suppressed by page.
    """
    patterns = [(t, re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE))
                for t in tokens]
    candidates = []
    pages_scanned = chars_scanned = 0
    for source_order, ref in enumerate(refs):
        payload = store.get(ref).get('payload')
        if not isinstance(payload, dict):
            raise RetrievalError(f'{ref}: malformed source payload')
        for page in _validate_pages(payload.get('pages'), ref):
            text = page['text']
            pages_scanned += 1
            chars_scanned += len(text)
            if pages_scanned > MAX_PAGES_PER_REQUEST or chars_scanned > MAX_SOURCE_CHARS_PER_REQUEST:
                raise RetrievalError('scan budget exceeded for passage retrieval')
            for start, end in _passage_ranges(text):
                matches = _passage_matches(text, patterns, start, end)
                if not matches:
                    continue
                counts = {token: sum(t == token for t, _, _ in matches) for token in tokens}
                coverage = sum(bool(n) for n in counts.values())
                density = sum(min(3, n) for n in counts.values()) / max(1, len(_TOKEN_RE.findall(text[start:end])))
                candidates.append({'ref': ref, 'order': source_order, 'page': page['number'],
                                   'start': start, 'end': end, 'text': text,
                                   'first': min(matches, key=lambda m: (m[1], m[2])),
                                   'coverage': coverage, 'density': density})
    candidates.sort(key=lambda c: (-c['coverage'], -c['density'], c['order'], c['page'], c['start']))
    results = []
    used = 0
    for cand in candidates:
        if len(results) >= limit:
            break
        available = min(cand['end'] - cand['start'], max_chars - used)
        first = cand['first']
        if available < first[2] - first[1]:
            continue
        start = cand['start']
        if first[2] > start + available:
            start = max(start, first[1] - (available - (first[2] - first[1])) // 2)
        end = min(cand['end'], start + available)
        # Suppress substantially overlapping windows, not distinct statements.
        if any(h['source_ref'] == cand['ref'] and h['page'] == cand['page']
               and max(0, min(end, h['end']) - max(start, h['start']))
                   >= .5 * min(end - start, h['end'] - h['start']) for h in results):
            continue
        matched = {token for token, _, _ in _passage_matches(cand['text'], patterns, start, end)}
        if not matched:
            continue
        snippet = cand['text'][start:end]
        results.append({'source_ref': cand['ref'], 'page': cand['page'], 'start': start, 'end': end,
                        'text': snippet, 'matched_terms': [t for t in tokens if t in matched],
                        'score': len(matched)})
        used += len(snippet)
    return {'query': query, 'method': 'passage_v2', 'results': results,
            'returned_chars': used, 'truncated': len(candidates) > len(results)}
