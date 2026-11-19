"""Structured JATS identities, paragraphs and background citation candidates.

Derived from the DSH public-format component and independently corrected.
All references retain pending-review eligibility. XML locators use one-based
sibling indices for each local tag name. Original XML remains the authority.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

__all__ = ["extract_background"]

# ---------------------------------------------------------------------------
# Limits and constants
# ---------------------------------------------------------------------------

MAX_RAW_BYTES = 2 * 1024 * 1024   # hard cap on raw input size (inclusive)
MAX_DEPTH = 64                    # maximum element nesting depth
MAX_NODES = 200000                # maximum number of XML elements

XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ID_TYPES = ("doi", "pmid", "pmcid")
INTRO_SEC_TYPES = frozenset({"intro", "introduction", "background"})
INTRO_TITLE_WORDS = frozenset({"introduction", "background", "引言", "背景", "前言", "绪论"})
REVIEW_HISTORY_TYPES = frozenset({'editor-report', 'aggregated-review-documents',
                                 'author-comment', 'reviewer-report', 'referee-report'})


def is_review_history(element):
    return (_local_name(element.tag) == 'sub-article'
            and (element.get('article-type') or '').strip().lower() in REVIEW_HISTORY_TYPES)

_BARE_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
_PMID_RE = re.compile(r"[1-9][0-9]{0,11}")
_PMCID_RE = re.compile(r"PMC[1-9][0-9]{0,11}", re.IGNORECASE)
_SCHEME_PREFIX_RE = re.compile(r"(doi|pmid|pmcid)\s*:\s*(\S.*)", re.IGNORECASE)
_URL_PREFIX_RE = re.compile(r"https?://", re.IGNORECASE)
_INTERNAL_SUBSET_RE = re.compile(r"<!\s*doctype", re.IGNORECASE)
_ENTITY_RE = re.compile(r"<!\s*entity", re.IGNORECASE)

_CITATION_CONTAINER_NAMES = ("mixed-citation", "citation", "element-citation", "nlm-citation")
_PRIMARY_TITLE_NAMES = ("article-title", "title")
_FALLBACK_TITLE_NAMES = ("chapter-title",)


class _LimitExceeded(Exception):
    """Internal signal: a parse-time structural budget was exceeded."""


class _GuardedTreeBuilder(ET.TreeBuilder):
    """ElementTree target that enforces element-count and nesting budgets."""

    def __init__(self, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH) -> None:
        super().__init__()
        self._max_nodes = max_nodes
        self._max_depth = max_depth
        self._nodes = 0
        self._depth = 0

    def start(self, tag, attrs=None):
        if tag in ("{http://www.w3.org/2001/XInclude}include",
                   "{http://www.w3.org/2001/XInclude}fallback"):
            raise ValueError("external XML inclusions are unsupported")
        self._nodes += 1
        self._depth += 1
        if self._nodes > self._max_nodes:
            raise _LimitExceeded("more than %d XML elements" % self._max_nodes)
        if self._depth > self._max_depth:
            raise _LimitExceeded("element nesting deeper than %d levels" % self._max_depth)
        return super().start(tag, attrs or {})

    def end(self, tag):
        self._depth -= 1
        return super().end(tag)


# ---------------------------------------------------------------------------
# Small text / tag helpers
# ---------------------------------------------------------------------------

def _local_name(tag) -> str:
    """Local part of an (optionally namespaced) tag; '' for non-strings."""
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rpartition("}")[2]
    return tag


def _norm_space(value) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _element_text(elem) -> str:
    """All descendant text of *elem*, whitespace-normalised."""
    return _norm_space("".join(elem.itertext()))


def _element_id(elem):
    """The element's id (plain or xml:id), or None when absent/empty."""
    value = elem.get("id")
    if value is None:
        value = elem.get("{%s}id" % XML_NS)
    value = _norm_space(value)
    return value or None


def _href_of(elem):
    """xlink:href when present, otherwise the plain href attribute."""
    value = elem.get("{%s}href" % XLINK_NS)
    if value is None:
        value = elem.get("href")
    return value


def _unique(items):
    """Order-preserving de-duplication."""
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _iter_subtree(elem, base_path):
    """Yield ``(node, path)`` for every descendant, in document order."""
    counters = {}
    for child in list(elem):
        name = _local_name(child.tag)
        counters[name] = counters.get(name, 0) + 1
        child_path = "%s/%s[%d]" % (base_path, name, counters[name])
        yield child, child_path
        for grandchild, grandchild_path in _iter_subtree(child, child_path):
            yield grandchild, grandchild_path


# ---------------------------------------------------------------------------
# Input gate: DTD/entity scan and guarded parsing
# ---------------------------------------------------------------------------

def _reject_dtd_entities(text: str) -> None:
    """Reject internal DTD subsets and ENTITY declarations (fail-closed)."""
    for match in _INTERNAL_SUBSET_RE.finditer(text):
        end = text.find(">", match.start())
        if end == -1:
            declaration = text[match.start():]
        else:
            declaration = text[match.start():end + 1]
        if "[" in declaration:
            raise ValueError("internal DTD subset (DOCTYPE with [...]) is not allowed")
    if _ENTITY_RE.search(text):
        raise ValueError("ENTITY declarations are not allowed")


def _parse_guarded(raw):
    """Validate and parse raw XML bytes under the module's security budget.

    Raises ValueError for every disallowed input: wrong type, over-size, NUL,
    invalid UTF-8, DTD entities/internal subset, malformed XML, or structural
    overruns.  A plain external DOCTYPE is tolerated and never fetched.
    """
    if isinstance(raw, str):
        raise ValueError("extract_background expects encoded bytes, not str")
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("extract_background expects bytes, got %s" % type(raw).__name__)
    data = bytes(raw)
    if len(data) > MAX_RAW_BYTES:
        raise ValueError("input exceeds the %d byte limit (%d bytes)" % (MAX_RAW_BYTES, len(data)))
    if b"\x00" in data:
        raise ValueError("input contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("input is not valid UTF-8: %s" % error)
    _reject_dtd_entities(text)
    parser = ET.XMLParser(target=_GuardedTreeBuilder())
    try:
        root = ET.fromstring(text, parser=parser)
    except _LimitExceeded as error:
        raise ValueError("XML exceeds structural limits: %s" % error)
    except ET.ParseError as error:
        raise ValueError("input is not well-formed XML: %s" % error)
    if not isinstance(root.tag, str):
        raise ValueError("document root is not an element")
    return root


# ---------------------------------------------------------------------------
# Identifier normalisation
# ---------------------------------------------------------------------------

def _identifier_from_url(url):
    """Normalise an official DOI/PMID/PMCID URL.

    Unknown hosts, query strings, fragments and credentials yield
    ``(None, None)``; nothing is guessed from a URL that is not on an official
    host with the expected path shape.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return (None, None)
    if parts.scheme.lower() not in ("http", "https"):
        return (None, None)
    if parts.query or parts.fragment or parts.username or parts.password:
        return (None, None)  # search/query parameters are never turned into IDs
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if host in ("doi.org", "dx.doi.org"):
        candidate = path.lstrip("/")
        if re.fullmatch(_BARE_DOI_RE, candidate):
            return ("doi", candidate.lower())
        return (None, None)
    if host == "pubmed.ncbi.nlm.nih.gov":
        found = re.fullmatch(r"/([1-9][0-9]{0,11})/?", path)
        if found:
            return ("pmid", found.group(1))
        return (None, None)
    if host == "pmc.ncbi.nlm.nih.gov":
        found = re.fullmatch(r"/articles/(PMC[1-9][0-9]{0,11})/?", path, re.IGNORECASE)
        if found:
            return ("pmcid", found.group(1).upper())
        return (None, None)
    if host == "www.ncbi.nlm.nih.gov":  # legacy official paths
        found = re.fullmatch(r"/pubmed/([1-9][0-9]{0,11})/?", path)
        if found:
            return ("pmid", found.group(1))
        found = re.fullmatch(r"/pmc/articles/(PMC[1-9][0-9]{0,11})/?", path, re.IGNORECASE)
        if found:
            return ("pmcid", found.group(1).upper())
    return (None, None)


def _normalize_identifier(raw_value, declared_type):
    """Best-effort, honest normalisation of one identifier string.

    Returns ``(kind, value)``; both are ``None`` when nothing trustworthy can
    be derived.  Never invents an ID from a value that does not carry its own
    well-formed shape (or a declared type that matches that shape).
    """
    text = _norm_space(raw_value)
    if not text:
        return (None, None)
    declared = _norm_space(declared_type).lower()
    if declared not in ID_TYPES:
        declared = None

    kind = None
    candidate = None
    scheme = _SCHEME_PREFIX_RE.match(text)
    if scheme:
        kind = scheme.group(1).lower()
        candidate = _norm_space(scheme.group(2))
    elif _URL_PREFIX_RE.match(text):
        kind, candidate = _identifier_from_url(text)
    else:
        candidate = text
        if re.fullmatch(_BARE_DOI_RE, candidate):
            kind = "doi"
        elif re.fullmatch(_PMCID_RE, candidate):
            kind = "pmcid"
        elif re.fullmatch(_PMID_RE, candidate):
            kind = declared  # bare digits only count when declared as pmid
    if kind is None or not candidate:
        return (None, None)
    if declared is not None and kind != declared:
        return (None, None)  # declared type contradicts the value's own shape
    if kind == "doi":
        if not re.fullmatch(_BARE_DOI_RE, candidate):
            return (None, None)
        return ("doi", candidate.lower())
    if kind == "pmid":
        if not re.fullmatch(_PMID_RE, candidate):
            return (None, None)
        return ("pmid", candidate)
    if kind == "pmcid":
        if not re.fullmatch(_PMCID_RE, candidate):
            return (None, None)
        return ("pmcid", "PMC" + candidate[3:])
    return (None, None)


def _identifier_entry(raw_value, declared, source, node_path, ref_path, issues):
    """Build one identifiers[] entry and report untrustworthy values."""
    if declared in ID_TYPES:
        _, value = _normalize_identifier(raw_value, declared)
        if value is None:
            issues.append({
                "code": "invalid_identifier",
                "message": "ref %s: %s claims type %r but %r does not yield a usable %s"
                           % (ref_path, source, declared, raw_value, declared),
            })
        return {"type": declared, "value": value, "source": source,
                "raw": raw_value, "xml_path": node_path}
    if declared is None:
        kind, value = _normalize_identifier(raw_value, None)
        return {"type": kind or "untyped", "value": value, "source": source,
                "raw": raw_value, "xml_path": node_path}
    # Other declared types (issn, isbn, ...): kept verbatim, never used for matching.
    text = _norm_space(raw_value)
    return {"type": declared, "value": text or None, "source": source,
            "raw": raw_value, "xml_path": node_path}


# ---------------------------------------------------------------------------
# Identity (front/article-meta)
# ---------------------------------------------------------------------------

def _extract_identity(root, root_path, issues):
    """Collect all article-title / structured article-id candidates."""
    identity = {
        "article_titles": [],
        "article_ids": [],
        "conflicts": {"article_title": False, "doi": False, "pmid": False, "pmcid": False},
    }
    front = None
    front_path = None
    counters = {}
    for child in list(root):
        name = _local_name(child.tag)
        counters[name] = counters.get(name, 0) + 1
        if name == "front" and front is None:
            front = child
            front_path = "%s/%s[%d]" % (root_path, name, counters[name])
    if front is None:
        issues.append({
            "code": "missing_front",
            "message": "no <front> element found; identity fields are empty",
        })
        return identity
    metas = [(node, path) for node, path in _iter_subtree(front, front_path)
             if _local_name(node.tag) == "article-meta"]
    for meta, meta_path in metas:
        for node, node_path in _iter_subtree(meta, meta_path):
            name = _local_name(node.tag)
            if name == "article-title":
                text = _element_text(node)
                if text:
                    identity["article_titles"].append({"value": text, "xml_path": node_path})
            elif name == "article-id":
                declared = _norm_space(node.get("pub-id-type")).lower() or None
                raw_value = _element_text(node)
                kind, value = _normalize_identifier(raw_value, declared)
                identity["article_ids"].append({
                    "type": declared or kind or "untyped",
                    "raw": raw_value,
                    "value": value,
                    "xml_path": node_path,
                })
                if declared in ID_TYPES and value is None:
                    issues.append({
                        "code": "invalid_identifier",
                        "message": "article-id claims type %r but %r does not yield a usable %s"
                                   % (declared, raw_value, declared),
                    })
    # Conflict flags over distinct, non-empty values; all candidates stay listed.
    title_values = _unique([entry["value"] for entry in identity["article_titles"]])
    identity["conflicts"]["article_title"] = len(title_values) > 1
    for id_type in ID_TYPES:
        values = _unique([entry["value"] for entry in identity["article_ids"]
                          if entry["type"] == id_type and entry["value"] is not None])
        identity["conflicts"][id_type] = len(values) > 1
    if not title_values:
        issues.append({
            "code": "identity_missing_article_title",
            "message": "no usable article-title found in front/article-meta",
        })
    if not any(entry["type"] in ID_TYPES and entry["value"] is not None
               for entry in identity["article_ids"]):
        issues.append({"code": "identity_missing_ids",
                       "message": "no usable structured article identifier found"})
    if identity["conflicts"]["article_title"]:
        issues.append({
            "code": "identity_conflict",
            "message": "multiple distinct article-title values kept: %r" % (title_values,),
        })
    conflicting_types = [t for t in ID_TYPES if identity["conflicts"][t]]
    if conflicting_types:
        issues.append({
            "code": "identity_conflict",
            "message": "multiple distinct article-id values kept for type(s): "
                       + ", ".join(conflicting_types),
        })
    return identity


# ---------------------------------------------------------------------------
# Blocks (body paragraphs) and intro detection
# ---------------------------------------------------------------------------

def _section_title(sec_elem) -> str:
    for child in list(sec_elem):
        if _local_name(child.tag) == "title":
            return _element_text(child)
    return ""


def _is_intro_section(sec_elem) -> bool:
    """True for sec-type intro/introduction/background or an exact
    'Introduction'/'Background' section title (case/punctuation tolerant)."""
    sec_type = _norm_space(sec_elem.get("sec-type")).lower()
    if sec_type in INTRO_SEC_TYPES:
        return True
    title = _norm_space(_section_title(sec_elem)).strip(" .:;,")
    return " ".join(title.lower().split()) in INTRO_TITLE_WORDS


def _citation_record(xref, xref_path):
    """One bibr xref: all candidate rids (order-preserving, de-duplicated)."""
    rids = []
    for rid in (xref.get("rid") or "").split():
        if rid not in rids:
            rids.append(rid)
    return {"xml_path": xref_path, "rids": rids}


def _canonical_paragraph_text(p_elem) -> str:
    """One paragraph's text in the ONE shared canonical JATS projection.

    The stored source page (research_literature._extract) and the indexed
    paragraph must carry the very same text, so an indexed paragraph keeps the
    visible [unreadable-formula:...] markers the page keeps and can be bound
    against it verbatim.  A private re-implementation here is exactly the
    divergence that dropped image formulas from the index: _element_text
    flattens a textless <inline-graphic>/<math> to nothing and the paragraph
    then reads as complete with an invisible hole.  Readable MathML/TeX stays
    text, a readable <alternatives> sibling wins over its parallel graphic and
    the skipped graphic stays a recorded unread asset on the source side; the
    structured gap ledger stays on the source, this index carries the visible
    markers, not a second asset list.
    """
    from .research_literature import _project_element
    return _project_element(p_elem, [], [])


def _make_block(p_elem, p_path, section_titles, section_sec_types, in_intro):
    """Build one body-paragraph block dict."""
    citations = []
    for node, node_path in _iter_subtree(p_elem, p_path):
        if _local_name(node.tag) != "xref":
            continue
        if _norm_space(node.get("ref-type")).lower() != "bibr":
            continue
        citations.append(_citation_record(node, node_path))
    text = _canonical_paragraph_text(p_elem)
    return {
        "xml_path": p_path,
        "paragraph_id": _element_id(p_elem),
        "section_titles": list(section_titles),
        "section_sec_types": list(section_sec_types),
        "in_intro": bool(in_intro),
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "citations": citations,
    }


# ---------------------------------------------------------------------------
# References (every <ref> inside any <ref-list>)
# ---------------------------------------------------------------------------

def _reference_title(ref_elem):
    """Structured article or chapter title, preserving missing titles."""
    for names in (_PRIMARY_TITLE_NAMES, _FALLBACK_TITLE_NAMES):
        for node in ref_elem.iter():
            if _local_name(node.tag) in names:
                text = _element_text(node)
                if text:
                    return text
    return None


def _reference_citation_text(ref_elem) -> str:
    """Text of the first citation container, else the whole ref text."""
    for node in ref_elem.iter():
        if _local_name(node.tag) in _CITATION_CONTAINER_NAMES:
            text = _element_text(node)
            if text:
                return text
    return _element_text(ref_elem)


def _extract_ref(ref_elem, ref_path, issues):
    """Build one reference row; all rows are kept even with missing ids."""
    identifiers = []
    for node, node_path in _iter_subtree(ref_elem, ref_path):
        name = _local_name(node.tag)
        if name == "pub-id":
            declared = _norm_space(node.get("pub-id-type")).lower() or None
            identifiers.append(_identifier_entry(
                _element_text(node), declared, "pub-id", node_path, ref_path, issues))
        elif name == "ext-link":
            declared = _norm_space(node.get("ext-link-type")).lower() or None
            if declared not in ID_TYPES:
                continue  # untyped / other ext-links are never mined for IDs
            raw_value = _href_of(node)
            if _norm_space(raw_value) == "":
                issues.append({
                    "code": "invalid_identifier",
                    "message": "ref %s: ext-link typed %r has no href" % (ref_path, declared),
                })
                identifiers.append({"type": declared, "value": None, "source": "ext-link",
                                    "raw": raw_value, "xml_path": node_path})
                continue
            identifiers.append(_identifier_entry(
                raw_value, declared, "ext-link", node_path, ref_path, issues))
    return {
        "reference_index": None,  # assigned after the full walk, in document order
        "reference_id": _element_id(ref_elem),
        "xml_path": ref_path,
        "title": _reference_title(ref_elem),
        "citation_text": _reference_citation_text(ref_elem),
        "identifiers": identifiers,
        "intro_context_paths": [],  # filled after all blocks are known
        "eligibility": "pending_review",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_identity(raw: bytes) -> dict:
    """Read primary article identifiers using the guarded JATS parser."""
    root = _parse_guarded(raw)
    if _local_name(root.tag) != "article":
        raise ValueError("publication identity requires an article root")
    return _extract_identity(root, "/article", [])


def extract_background(raw: bytes) -> dict:
    """Parse JATS XML bytes and extract background-citation facts.

    Returns a JSON-serialisable dict with keys: version, identity, blocks,
    intro_blocks, references, unresolved_citations, duplicate_reference_ids,
    status, issues (see the module docstring for the full schema).  Raises
    ValueError for any disallowed input.  Pure data processing: no file,
    network, or subprocess access, and no reference-eligibility decision is
    ever made (always "pending_review").
    """
    root = _parse_guarded(raw)
    root_name = _local_name(root.tag)
    if root_name != "article":
        raise ValueError("root element must be 'article', got %r" % (root_name or root.tag))
    root_path = "/%s" % root_name
    issues = []
    blocks = []
    references = []
    state = {"saw_body": False}

    def visit(elem, path, section_titles, section_sec_types, in_body, in_intro, in_ref_list):
        if is_review_history(elem):
            issues.append(dict(code='review_history_excluded', xml_path=path,
                               article_type=elem.get('article-type'),
                               message='Peer-review correspondence retained in original XML outside research text.'))
            return
        name = _local_name(elem.tag)
        if name == "body":
            state["saw_body"] = True
        child_in_body = in_body or name == "body"
        child_in_ref_list = in_ref_list or name == "ref-list"
        if name == "sec" and child_in_body:
            section_titles = section_titles + [_section_title(elem)]
            section_sec_types = section_sec_types + [
                _norm_space(elem.get("sec-type")).lower() or None]
            in_intro = in_intro or _is_intro_section(elem)
        if name == "p" and child_in_body and not child_in_ref_list:
            blocks.append(_make_block(elem, path, section_titles, section_sec_types, in_intro))
        if name == "ref":
            if child_in_ref_list:
                references.append(_extract_ref(elem, path, issues))
            return  # ref subtrees never yield blocks or nested references
        counters = {}
        for child in list(elem):
            child_name = _local_name(child.tag)
            counters[child_name] = counters.get(child_name, 0) + 1
            visit(child, "%s/%s[%d]" % (path, child_name, counters[child_name]),
                  section_titles, section_sec_types, child_in_body, in_intro,
                  child_in_ref_list)

    visit(root, root_path, [], [], False, False, False)
    identity = _extract_identity(root, root_path, issues)

    for index, ref in enumerate(references, start=1):
        ref["reference_index"] = index

    # Duplicate reference ids (every row is kept; ambiguity is reported).
    by_id = {}
    for ref in references:
        if ref["reference_id"] is not None:
            by_id.setdefault(ref["reference_id"], []).append(ref)
    duplicate_ids = sorted(rid for rid, group in by_id.items() if len(group) > 1)

    # A multi-rid xref cites several papers. Resolve each rid independently.
    unresolved = []
    saw_citation = False
    for block in blocks:
        for citation in block["citations"]:
            saw_citation = True
            citation["resolutions"] = []
            rids = citation["rids"]
            if not rids:
                unresolved.append({"block_xml_path": block["xml_path"],
                                   "citation_xml_path": citation["xml_path"],
                                   "rid": None, "reason": "xref_without_rid"})
            for rid in rids:
                matches = by_id.get(rid, [])
                resolved = len(matches) == 1
                citation["resolutions"].append({
                    "rid": rid, "resolved": resolved,
                    "reference_paths": [ref["xml_path"] for ref in matches]})
                if not resolved:
                    unresolved.append({
                        "block_xml_path": block["xml_path"],
                        "citation_xml_path": citation["xml_path"], "rid": rid,
                        "reason": "duplicate_reference_id" if matches else "no_matching_reference"})

    # intro_context_paths: candidate-level (any rid match), not resolution-level.
    intro_paths_by_rid = {}
    for block in blocks:
        if not block["in_intro"]:
            continue
        for citation in block["citations"]:
            for rid in citation["rids"]:
                paths = intro_paths_by_rid.setdefault(rid, [])
                if block["xml_path"] not in paths:
                    paths.append(block["xml_path"])
    for ref in references:
        rid = ref["reference_id"]
        ref["intro_context_paths"] = list(intro_paths_by_rid.get(rid, [])) if rid is not None else []

    intro_blocks = [block for block in blocks if block["in_intro"]]

    # Aggregate issues; anything to review forces status="needs_review".
    if not state["saw_body"]:
        issues.append({"code": "missing_body", "message": "no <body> element found"})
    elif not blocks:
        issues.append({"code": "no_body_paragraphs", "message": "body has no paragraphs"})
    if not intro_blocks:
        issues.append({
            "code": "no_intro_section",
            "message": "no intro/introduction/background section found in body",
        })
    if not saw_citation:
        issues.append({
            "code": "no_citations",
            "message": "no bibr xref citations found in any body paragraph",
        })
    if not references:
        issues.append({
            "code": "no_references",
            "message": "no ref-list/ref entries found anywhere in the document",
        })
    if unresolved:
        issues.append({
            "code": "unresolved_citations",
            "message": "%d citation(s) could not be uniquely matched to a reference"
                       % len(unresolved),
        })
    if duplicate_ids:
        issues.append({
            "code": "duplicate_reference_ids",
            "message": "reference id(s) used by more than one ref: " + ", ".join(duplicate_ids),
        })
    missing_id = sum(1 for ref in references if ref["reference_id"] is None)
    if missing_id:
        issues.append({
            "code": "references_missing_id",
            "message": "%d ref element(s) have no id" % missing_id,
        })
    missing_title = sum(1 for ref in references if ref["title"] is None)
    if missing_title:
        issues.append({
            "code": "references_missing_title",
            "message": "%d ref element(s) have no structured article or chapter title" % missing_title,
        })
    missing_text = sum(1 for ref in references if not ref["citation_text"])
    if missing_text:
        issues.append({
            "code": "references_missing_citation_text",
            "message": "%d ref element(s) have empty text" % missing_text,
        })

    return {
        "version": 1,
        "identity": identity,
        "blocks": blocks,
        "intro_blocks": intro_blocks,
        "references": references,
        "unresolved_citations": unresolved,
        "duplicate_reference_ids": duplicate_ids,
        "status": "needs_review" if issues else "indexed",
        "issues": issues,
    }


