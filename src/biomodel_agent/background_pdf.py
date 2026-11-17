"""PDF-text introduction and bibliography extraction with original-page spans.

Derived from the frozen DSH component. Text, page boundaries and uncertain
layout evidence are preserved. All reference eligibility remains pending.
"""

from __future__ import annotations

import math
import re
from collections import Counter

__all__ = [
    "extract_pdf_background",
    "FORMAT",
    "MAX_PAGES",
    "MAX_TOTAL_UTF8_BYTES",
]

__version__ = "1.0.0"
FORMAT = "pdf_text"
MAX_PAGES = 1000
MAX_TOTAL_UTF8_BYTES = 2 * 1024 * 1024

# --------------------------------------------------------------------------
# Section-name vocabularies (full-line matches only, numbering tolerated).
# --------------------------------------------------------------------------

KIND_INTRO = "intro"
KIND_END = "end"
KIND_REFS = "references"
KIND_OTHER = "other"
KIND_SUB = "subheading"

_INTRO_NAMES = [
    "introduction",
    "background",
    "引言",
    "背景",
    "绪论",
    "前言",
]
_END_NAMES = [
    "materials and methods",
    "materials & methods",
    "methods and materials",
    "experimental procedures",
    "experimental section",
    "model and methods",
    "results and discussion",
    "methods",
    "results",
    "discussion",
    "conclusions",
    "conclusion",
    "concluding remarks",
    "summary",
    "材料与方法",
    "实验方法",
    "方法",
    "结果与讨论",
    "结果",
    "讨论",
    "结论",
    "小结",
]
_REFS_NAMES = [
    "reference list",
    "references",
    "bibliography",
    "literature cited",
    "works cited",
    "参考文献列表",
    "参考文献",
    "引用文献",
    "文献",
]
_OTHER_NAMES = [
    "availability of data and materials",
    "authors’ contributions",
    "authors' contributions",
    "author contributions",
    "supplementary material",
    "supplemental material",
    "additional material",
    "competing interests",
    "conflicts of interest",
    "conflict of interest",
    "acknowledgements",
    "acknowledgments",
    "acknowledgement",
    "acknowledgment",
    "data availability",
    "abbreviations",
    "abstract",
    "funding",
    "ethics",
    "摘要",
    "致谢",
    "利益冲突",
    "作者贡献",
    "附加材料",
    "补充材料",
    "缩写",
    "资助",
]
for _names in (_INTRO_NAMES, _END_NAMES, _REFS_NAMES, _OTHER_NAMES):
    _names.sort(key=len, reverse=True)

# Codes that force status="needs_review"; anything else is informational.
_REVIEW_CODES = {
    "intro_not_found",
    "front_page_missing",
    "page_sequence_gap",
    "intro_unterminated",
    "references_not_found",
    "reference_region_empty",
    "reference_style_unsupported",
    "unparsed_reference_fragment",
    "reference_id_gap",
    "reference_id_non_monotonic",
    "duplicate_reference_id",
    "citation_unresolved",
    "citation_range_too_large",
    "identity_title_not_located",
    "interposed_segment_before_running_header",
    "content_after_references",
    "doi_marker_without_valid_doi",
    "identifier_not_wellformed",
}

# --------------------------------------------------------------------------
# Compiled patterns.
# --------------------------------------------------------------------------

_HEADING_NUM = (
    r"(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3}\s*[.)\]]?\s+"
    r"|[IVXLCivxlc]{1,6}\s*[.)]\s+"
    r"|[一二三四五六七八九十]{1,3}\s*[、]\s*)"
)
_HEADING_CACHE = {}

_SENT_END_RE = re.compile(r"[.!?][\"'”’)\]]*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.|table|additional file|supplementary|supplement"
    r"|click here|scheme|equation|plate)\b",
    re.IGNORECASE,
)
_LIST_MARKER_RE = re.compile(r"^(?:[-•*·]|[(]?[0-9]{1,3}[.)]|\[[0-9]{1,3}\])\s+")

_LABEL_BRACKET = re.compile(r"^\s*\[([0-9]{1,4})\]\s*[.,;:]?\s*(.*)$")
_LABEL_DOT = re.compile(r"^\s*([0-9]{1,4})\.\s*([^0-9].*)$")
_LABEL_PAREN = re.compile(r"^\s*([0-9]{1,4})\)\s+(.*)$")
_LABEL_BARE = re.compile(r"^\s*([0-9]{1,4})\s+([A-Z\[（\"“'(一-龥].*)$")
_LABEL_PATTERNS = (_LABEL_BRACKET, _LABEL_DOT, _LABEL_PAREN, _LABEL_BARE)

_CITATION_RE = re.compile(
    r"\[\s*([0-9]{1,4}(?:\s*[-\u2013\u2014]\s*[0-9]{1,4})?"
    r"(?:\s*,\s*[0-9]{1,4}(?:\s*[-\u2013\u2014]\s*[0-9]{1,4})?)*)\s*\]"
)

_DOI_MARKED = re.compile(
    r"(?:\bdoi\b\s*[:：]?\s*|https?://(?:dx\.)?doi\.org/)"
    r"(10\.[0-9]{4,9}/\S+)",
    re.IGNORECASE,
)
_DOI_BARE = re.compile(r"(?<![0-9A-Za-z._/-])(10\.[0-9]{4,9}/[^\s,;]+)")
_DOI_MARKER_HINT = re.compile(r"\bdoi\b|doi\.org", re.IGNORECASE)
_PMID_RE = re.compile(r"(?:\bPMID\b|\bPubMed\s*ID\b)\s*[:：]?\s*([0-9]{5,9})", re.IGNORECASE)
_PMID_URL_RE = re.compile(
    r"(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/([0-9]{5,9})",
    re.IGNORECASE,
)
_PMCID_RE = re.compile(r"\bPMCID\b\s*[:：]?\s*(PMC[0-9]{5,9})", re.IGNORECASE)
_PMCID_URL_RE = re.compile(r"pmc\.ncbi\.nlm\.nih\.gov/articles/(PMC[0-9]{5,9})", re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s\]\)>]+", re.IGNORECASE)
_ALLOWED_ID_HOSTS = {
    "doi.org",
    "dx.doi.org",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
}
_IDENT_EXT_REJECT = (
    ".gif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".zip", ".gz", ".htm", ".html", ".aspx", ".asp",
    ".php", ".xml", ".svg", ".mov", ".mp4", ".avi", ".css", ".js",
)

_ID_START_RE = re.compile(
    r"open\s*access|research\s+article|original\s+(research\s+)?article"
    r"|review\s+article",
    re.IGNORECASE,
)
_META_LINE = re.compile(
    r"^(?:address(?:es)?|e-?mail|tel(?:ephone)?|fax|correspond\w*|received"
    r"|accepted|revised|published|contributed|presented|available\s+online"
    r"|copyright)\b",
    re.IGNORECASE,
)
_NAME_LINE = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?: [A-Z]\.?)+ [A-Z][A-Za-z'’\-]+$"
    r"|^[A-Z][A-Za-z'’\-]+ [A-Z][A-Za-z'’\-]+$"
)


# --------------------------------------------------------------------------
# Validation.
# --------------------------------------------------------------------------

def _validate_pages(pages):
    """Return (clean pages sorted by number, original page-number order)."""
    if not isinstance(pages, list):
        raise TypeError(
            "pages must be a list of {'number': int, 'text': str} dicts, "
            "got %s" % type(pages).__name__
        )
    if len(pages) == 0:
        raise ValueError("pages must contain at least one page")
    if len(pages) > MAX_PAGES:
        raise ValueError("too many pages: %d > %d" % (len(pages), MAX_PAGES))
    original_order = []
    seen = {}
    clean = []
    total_bytes = 0
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            raise TypeError("pages[%d] must be a dict" % idx)
        if "number" not in page or "text" not in page:
            raise ValueError("pages[%d] must have 'number' and 'text' keys" % idx)
        num = page["number"]
        if isinstance(num, bool) or not isinstance(num, int):
            raise TypeError(
                "pages[%d]['number'] must be a positive int (bool not accepted), "
                "got %s" % (idx, type(num).__name__)
            )
        if num <= 0:
            raise ValueError("pages[%d]['number'] must be a positive int, got %r" % (idx, num))
        text = page["text"]
        if not isinstance(text, str):
            raise TypeError(
                "pages[%d]['text'] must be str, got %s" % (idx, type(text).__name__)
            )
        total_bytes += len(text.encode("utf-8"))
        if num in seen:
            raise ValueError(
                "duplicate page number %d at indices %d and %d" % (num, seen[num], idx)
            )
        seen[num] = idx
        original_order.append(num)
        clean.append({"number": num, "text": text})
    if total_bytes > MAX_TOTAL_UTF8_BYTES:
        raise ValueError(
            "total UTF-8 text exceeds budget: %d > %d bytes"
            % (total_bytes, MAX_TOTAL_UTF8_BYTES)
        )
    clean.sort(key=lambda p: p["number"])
    return clean, original_order


# --------------------------------------------------------------------------
# Text utilities.
# --------------------------------------------------------------------------

def _split_lines(text):
    """Return [(start, end, raw)] with offsets into the original text."""
    out = []
    start = 0
    for line in text.split("\n"):
        raw = line[:-1] if line.endswith("\r") else line
        if raw:
            out.append((start, start + len(raw), raw))
        start += len(line) + 1
    return out


def _normalize_ws(value):
    return re.sub(r"\s+", " ", value).strip()


def _norm_repeat(line):
    """Digit-normalized key used to detect repeated running headers/footers."""
    key = re.sub(r"\s+", " ", line).strip()
    return re.sub(r"[0-9]+", "#", key)


def _is_cjk_char(ch):
    return bool(_CJK_RE.match(ch))


def _ends_sentence(raw):
    s = raw.rstrip()
    if not s:
        return False
    if s.endswith(("。", "！", "？")):
        return True
    return bool(_SENT_END_RE.search(s))


def _starts_sentence(raw):
    s = raw.lstrip()
    if not s:
        return False
    ch = s[0]
    return ch.isupper() or ch.isdigit() or ch in "\"'“‘(" or _is_cjk_char(ch)


def _starts_list_or_caption(raw):
    s = raw.strip()
    if not s:
        return False
    return bool(_CAPTION_RE.match(s) or _LIST_MARKER_RE.match(s))


def _sub_span(span, start, end):
    return {
        "page": span["page"],
        "start": span["start"] + start,
        "end": span["start"] + end,
        "quote": span["quote"][start:end],
    }


def _merge_spans(pieces, page_texts):
    """Merge same-page pieces separated only by whitespace into raw spans."""
    spans = []
    cur = None
    for pc in pieces:
        page = pc["page"]
        start = pc["start"]
        end = pc["end"]
        if cur is not None and cur[0] == page:
            gap = page_texts[page][cur[2]:start]
            if gap.strip() == "":
                cur = (page, cur[1], end)
                continue
            spans.append(cur)
            cur = (page, start, end)
        else:
            if cur is not None:
                spans.append(cur)
            cur = (page, start, end)
    if cur is not None:
        spans.append(cur)
    return [
        {"page": page, "start": start, "end": end, "quote": page_texts[page][start:end]}
        for (page, start, end) in spans
    ]


def _make_block(pieces, page_texts, titles, in_intro):
    spans = _merge_spans(pieces, page_texts)
    text = _normalize_ws(" ".join(pc["raw"] for pc in pieces))
    return {
        "text": text,
        "source_spans": spans,
        "section_titles": titles,
        "in_intro": bool(in_intro),
        "locator": {"kind": FORMAT, "spans": list(spans)},
    }


# --------------------------------------------------------------------------
# Running header / footer detection.
# --------------------------------------------------------------------------

def _detect_running_headers(page_pairs):
    counts = Counter()
    top_counts = Counter()
    for _no, text in page_pairs:
        lines = [raw for (_s, _e, raw) in _split_lines(text) if raw.strip()]
        n = len(lines)
        for i, raw in enumerate(lines):
            if len(raw.strip()) > 200:
                continue
            key = _norm_repeat(raw)
            if sum(ch.isalnum() for ch in key) < 3:
                continue
            counts[key] += 1
            if i <= 4 or i >= n - 5:
                top_counts[key] += 1
    if not counts:
        return {}, set()
    need = max(3, int(math.ceil(0.5 * len(page_pairs))))
    keys = {}
    for key, count in counts.items():
        if count >= need and top_counts.get(key, 0) / count >= 0.6:
            keys[key] = count
    pages = set()
    if keys:
        for no, text in page_pairs:
            for (_s, _e, raw) in _split_lines(text):
                if len(raw.strip()) <= 200 and _norm_repeat(raw) in keys:
                    pages.add(no)
    return keys, pages


def _page_stream(page_no, text, header_keys):
    """Split a page into flow items; content printed before the running
    header is marked detached (reading-order artifact, e.g. a publisher
    notice carried over from the previous page)."""
    lines = _split_lines(text)
    removed = set()
    for i, (_s, _e, raw) in enumerate(lines):
        if len(raw.strip()) > 200:
            continue
        if header_keys and _norm_repeat(raw) in header_keys:
            removed.add(i)
    first_header = min(removed) if removed else None
    detach = False
    if first_header is not None and first_header > 0:
        prefix = [raw for i, (_s, _e, raw) in enumerate(lines) if i < first_header]
        nonempty = [r for r in prefix if r.strip()]
        detach = len(nonempty) >= 2 and sum(len(r) for r in prefix) >= 40
    items = []
    for i, (s, e, raw) in enumerate(lines):
        if i in removed:
            continue
        is_detached = bool(detach and first_header is not None and i < first_header)
        items.append({"page": page_no, "start": s, "end": e, "raw": raw, "detached": is_detached})
    return items, removed


# --------------------------------------------------------------------------
# Headings.
# --------------------------------------------------------------------------

def _heading_re(name):
    rx = _HEADING_CACHE.get(name)
    if rx is None:
        rx = re.compile(
            r"^\s*" + _HEADING_NUM + "?" + re.escape(name) + r"\s*[.:：。]?\s*$",
            re.IGNORECASE,
        )
        _HEADING_CACHE[name] = rx
    return rx


def _classify_heading(line):
    s = line.strip()
    if not s or len(s) > 80:
        return None
    for kind, names in (
        (KIND_REFS, _REFS_NAMES),
        (KIND_INTRO, _INTRO_NAMES),
        (KIND_END, _END_NAMES),
        (KIND_OTHER, _OTHER_NAMES),
    ):
        for name in names:
            if _heading_re(name).match(s):
                # keep the printed heading text (incl. any numbering) so that
                # section_titles stay traceable to the source line
                return kind, s
    return None


def _looks_like_subheading(raw, prev_raw, prev_was_heading, next_raw):
    s = raw.strip()
    if not (2 <= len(s) <= 60):
        return False
    if s.endswith((".", ",", ";", ":", "!", "?", ")", "]", "，", "。", "；", "：")):
        return False
    if not (s[0].isupper() or _is_cjk_char(s[0])):
        return False
    if _CAPTION_RE.match(s) or _LIST_MARKER_RE.match(s):
        return False
    if len(s.split()) > 8 or "  " in s:
        return False
    prev_ok = prev_was_heading or (prev_raw is not None and _ends_sentence(prev_raw))
    if not prev_ok:
        return False
    if next_raw is None:
        return False
    nxt = next_raw.strip()
    if not nxt:
        return False
    return nxt[0].isupper() or _is_cjk_char(nxt[0])


def _next_raw(items, index):
    if index + 1 < len(items) and not items[index + 1]["detached"]:
        return items[index + 1]["raw"]
    return None


# --------------------------------------------------------------------------
# Reference labels.
# --------------------------------------------------------------------------

def _ref_start_like(rest):
    if len(rest) < 20:
        return False
    if ":" in rest:
        return True
    return bool(re.search(r"\b(?:19|20)[0-9]{2}\b", rest))


def _match_ref_label(raw, expected):
    s = raw.strip()
    for pattern in _LABEL_PATTERNS:
        m = pattern.match(s)
        if m is None:
            continue
        num = int(m.group(1))
        rest = (m.group(2) or "").strip()
        if pattern is _LABEL_BARE and rest.startswith(("(", "[")):
            return None
        if expected is None:
            return (num, rest) if num <= 999 else None
        if num == expected:
            return num, rest
        if expected < num <= expected + 5:
            return num, rest
        if num < expected and 1 <= num <= 99 and _ref_start_like(rest):
            return num, rest
        return None
    return None


# --------------------------------------------------------------------------
# Identifiers.
# --------------------------------------------------------------------------

def _clean_identifier_value(value, broken):
    if broken:
        value = re.sub(r"\s+", "", value)
    else:
        value = value.strip()
    return value.rstrip(".,;:)]}>\"'”’")


def _is_probably_filename(value):
    return value.lower().endswith(_IDENT_EXT_REJECT)


def _overlaps(consumed, start, end):
    for cs, ce in consumed:
        if start < ce and end > cs:
            return True
    return False


def _add_identifier(found, span, match, value, id_type, consumed, issues):
    consumed.append((match.start(), match.end()))
    printed = match.group(0)
    broken = ("\n" in printed) or ("\r" in printed)
    value = _clean_identifier_value(value, broken)
    page = span["page"]
    start = span["start"] + match.start()
    end = span["start"] + match.end()
    if id_type == "doi":
        if _is_probably_filename(value):
            issues.append({
                "code": "identifier_rejected_suffix",
                "detail": "Printed token %r has a 10.x/... shape but ends with a file "
                          "extension; treated as a supplementary-file name, not a DOI." % printed,
                "page": page,
                "start": start,
                "end": end,
            })
            return
        if not re.match(r"^10\.[0-9]{4,9}/\S+$", value):
            issues.append({
                "code": "identifier_not_wellformed",
                "detail": "Printed token %r is not a well-formed DOI after normalization; "
                          "not emitted as an identifier." % printed,
                "page": page,
                "start": start,
                "end": end,
            })
            return
    elif id_type == "pmid":
        if not re.match(r"^[0-9]{5,9}$", value):
            return
    elif id_type == "pmcid":
        if not re.match(r"^PMC[0-9]{5,9}$", value):
            return
    if broken:
        issues.append({
            "code": "identifier_broken_across_lines",
            "detail": "Identifier %r is printed across a line break; whitespace was removed "
                      "to form the candidate and the raw span is kept for verification." % value,
            "page": page,
            "start": start,
            "end": end,
        })
    found.append({
        "type": id_type,
        "value": value,
        "raw": printed,
        "source_spans": [_sub_span(span, match.start(), match.end())],
    })


def _scan_identifiers(spans, issues):
    found = []
    for span in spans:
        quote = span["quote"]
        consumed = []
        for m in _DOI_MARKED.finditer(quote):
            _add_identifier(found, span, m, m.group(1), "doi", consumed, issues)
        for m in _DOI_BARE.finditer(quote):
            if _overlaps(consumed, m.start(), m.end()):
                continue
            _add_identifier(found, span, m, m.group(1), "doi", consumed, issues)
        for m in _PMID_RE.finditer(quote):
            _add_identifier(found, span, m, m.group(1), "pmid", consumed, issues)
        for m in _PMID_URL_RE.finditer(quote):
            _add_identifier(found, span, m, m.group(1), "pmid", consumed, issues)
        for m in _PMCID_RE.finditer(quote):
            _add_identifier(found, span, m, m.group(1), "pmcid", consumed, issues)
        for m in _PMCID_URL_RE.finditer(quote):
            _add_identifier(found, span, m, m.group(1), "pmcid", consumed, issues)
    return found


def _count_doi_markers(spans):
    total = 0
    for span in spans:
        total += len(_DOI_MARKER_HINT.findall(span["quote"]))
    return total


def _check_reference_urls(records, issues):
    hosts = {}
    for rec in records:
        for span in rec["source_spans"]:
            for m in _URL_RE.finditer(span["quote"]):
                host_match = re.match(r"https?://([^/]+)", m.group(0), re.IGNORECASE)
                if not host_match:
                    continue
                host = host_match.group(1).lower()
                if host.startswith("www."):
                    host = host[4:]
                if host in _ALLOWED_ID_HOSTS:
                    continue
                hosts[host] = hosts.get(host, 0) + 1
    if hosts:
        issues.append({
            "code": "url_not_used_as_identifier",
            "detail": "URLs printed in the reference region point to hosts that are not "
                      "DOI/PMID/PMCID resolvers; they stay in the citation text but are "
                      "never converted into identifiers.",
            "hosts": sorted(hosts),
        })


# --------------------------------------------------------------------------
# Reference titles (conservative Vancouver-style heuristic).
# --------------------------------------------------------------------------

def _ref_title(citation_text):
    if len(citation_text) < 25:
        return None
    m = re.match(r"^(.{5,300}?):\s*(.+)$", citation_text, re.DOTALL)
    if m is None:
        return None
    authors = m.group(1)
    rest = m.group(2)
    low = authors.lower()
    if "http" in low or "doi" in low:
        return None
    has_author_shape = bool(re.search(r"\b[A-Z][A-Za-z'’\-]+ [A-Z]{1,3}\b", authors))
    if not has_author_shape and not ("," in authors and re.search(r"[A-Z]", authors)):
        return None
    cut = rest.find(". ")
    if cut == -1:
        title = rest
        after = ""
    else:
        title = rest[:cut]
        after = rest[cut + 2:]
    title = _normalize_ws(title).rstrip(".")
    if not (10 <= len(title) <= 400):
        return None
    if re.match(r"^10\.[0-9]", title):
        return None
    after = after.strip()
    if not after:
        return None
    if not (after[0].isupper() or _is_cjk_char(after[0])):
        return None
    return title


# --------------------------------------------------------------------------
# Flow builder.
# --------------------------------------------------------------------------

class _Builder:
    """Single pass over the prepared page items producing blocks and the raw
    reference-entry grouping."""

    def __init__(self, page_texts, issues):
        self.page_texts = page_texts
        self.issues = issues
        self.blocks = []
        self.section_stack = []
        self.in_intro = False
        self.intro_found = False
        self.intro_terminated = True
        self.in_refs = False
        self.refs_seen_heading = False
        self.ref_entries = []
        self.cur_ref = None
        self.orphan = []
        self.para = []
        self.detached_run = []
        self.prev_raw = None
        self.prev_was_heading = False
        self.expected_ref = 1
        self.max_ref_seen = 0
        self.seen_ids = {}
        self.dup_ids = set()
        self.gap_numbers = set()
        self.non_monotonic = False

    # -- emission helpers ---------------------------------------------------

    def flush_para(self):
        if self.para:
            self.blocks.append(
                _make_block(self.para, self.page_texts, list(self.section_stack), self.in_intro)
            )
            self.para = []

    def flush_detached(self):
        if self.detached_run:
            pages = sorted({pc["page"] for pc in self.detached_run})
            self.blocks.append(_make_block(self.detached_run, self.page_texts, [], False))
            self.issues.append({
                "code": "interposed_segment_before_running_header",
                "detail": "Content printed before the running header on a page (e.g. a "
                          "publisher notice carried over from the previous page) was kept as "
                          "a separate block, out of the running section/reference flow; the "
                          "reading order around the header is uncertain.",
                "pages": pages,
            })
            self.detached_run = []

    def flush_cur_ref(self):
        if self.cur_ref is not None:
            self.ref_entries.append({"id": self.cur_ref["id"], "pieces": self.cur_ref["pieces"]})
            self.cur_ref = None

    def flush_orphan(self):
        if self.orphan:
            self.ref_entries.append({"id": None, "pieces": list(self.orphan)})
            self.orphan = []

    def finish(self):
        self.flush_para()
        self.flush_detached()
        self.flush_cur_ref()
        self.flush_orphan()

    # -- state transitions --------------------------------------------------

    def _apply_heading(self, heading):
        kind, name = heading
        self.flush_para()
        if kind == KIND_REFS:
            if not self.in_refs:
                self.flush_cur_ref()
                self.flush_orphan()
            self.in_refs = True
            self.refs_seen_heading = True
            if self.in_intro:
                self.intro_terminated = True
            self.in_intro = False
            self.section_stack = [name]
            return
        if kind == KIND_INTRO:
            self.in_refs = False
            self.flush_cur_ref()
            self.flush_orphan()
            self.section_stack = [name]
            self.in_intro = True
            self.intro_found = True
            self.intro_terminated = False
            return
        if kind in (KIND_END, KIND_OTHER):
            if self.in_refs:
                self.flush_cur_ref()
                self.flush_orphan()
                self.issues.append({
                    "code": "content_after_references",
                    "detail": "Heading '%s' found after the reference region had started; "
                              "reference parsing stopped there and the following text is "
                              "treated as body content." % name,
                })
            self.in_refs = False
            self.flush_cur_ref()
            self.flush_orphan()
            if self.in_intro:
                self.intro_terminated = True
            self.in_intro = False
            self.section_stack = [name]
            return
        # subheading: attached under the current top-level section
        if self.section_stack:
            self.section_stack = [self.section_stack[0], name]
        else:
            self.section_stack = [name]

    def _accept_label(self, num):
        count = self.seen_ids.get(num, 0)
        self.seen_ids[num] = count + 1
        if count >= 1:
            self.dup_ids.add(num)
        elif num < self.max_ref_seen:
            self.non_monotonic = True
        if num > self.expected_ref:
            for missing in range(self.expected_ref, num):
                self.gap_numbers.add(missing)
        if num > self.max_ref_seen:
            self.max_ref_seen = num
        if num >= self.expected_ref:
            self.expected_ref = num + 1

    def _feed_reference_line(self, item):
        label = _match_ref_label(item["raw"], self.expected_ref)
        if label is not None:
            num = label[0]
            self.flush_cur_ref()
            self.flush_orphan()
            self.cur_ref = {"id": num, "pieces": [item]}
            self._accept_label(num)
        elif self.cur_ref is not None:
            self.cur_ref["pieces"].append(item)
        else:
            self.orphan.append(item)

    def _feed_body_line(self, item):
        new_para = False
        if self.para:
            prev = self.para[-1]["raw"]
            if _ends_sentence(prev) and _starts_sentence(item["raw"]):
                new_para = True
            if _starts_list_or_caption(item["raw"]):
                new_para = True
        if new_para:
            self.flush_para()
        self.para.append(item)

    # -- main loop ----------------------------------------------------------

    def feed(self, items):
        i = 0
        n = len(items)
        while i < n:
            item = items[i]
            if item["detached"]:
                self.flush_para()
                self.flush_cur_ref()
                self.flush_orphan()
                self.detached_run.append(item)
                i += 1
                continue
            if self.detached_run:
                self.flush_detached()
            raw = item["raw"]
            if not raw.strip():
                i += 1
                continue
            heading = None
            if self.in_refs:
                cls = _classify_heading(raw)
                if cls is not None and cls[0] in (KIND_REFS, KIND_END, KIND_OTHER):
                    heading = cls
            else:
                heading = _classify_heading(raw)
                if heading is None and _looks_like_subheading(
                    raw, self.prev_raw, self.prev_was_heading, _next_raw(items, i)
                ):
                    heading = (KIND_SUB, _normalize_ws(raw))
            if heading is not None:
                self._apply_heading(heading)
                self.prev_raw = raw
                self.prev_was_heading = True
                i += 1
                continue
            self.prev_raw = raw
            self.prev_was_heading = False
            if self.in_refs:
                self._feed_reference_line(item)
            else:
                self._feed_body_line(item)
            i += 1


# --------------------------------------------------------------------------
# Reference records.
# --------------------------------------------------------------------------

def _build_references(builder, page_texts, issues):
    records = []
    for idx, entry in enumerate(builder.ref_entries):
        spans = _merge_spans(entry["pieces"], page_texts)
        citation_text = _normalize_ws(" ".join(pc["raw"] for pc in entry["pieces"]))
        identifiers = _scan_identifiers(spans, issues)
        if _count_doi_markers(spans) > 0 and not any(i["type"] == "doi" for i in identifiers):
            issues.append({
                "code": "doi_marker_without_valid_doi",
                "detail": "Reference text prints a DOI marker but no well-formed DOI was "
                          "extractable; no identifier was invented.",
                "reference_index": idx,
            })
        record = {
            "reference_index": idx,
            "reference_id": str(entry["id"]) if entry["id"] is not None else None,
            "title": _ref_title(citation_text),
            "citation_text": citation_text,
            "source_spans": spans,
            "identifiers": identifiers,
            "intro_context_locators": [],
            "eligibility": "pending_review",
        }
        records.append(record)
        if entry["id"] is None:
            issues.append({
                "code": "unparsed_reference_fragment",
                "detail": "Reference-region text could not be split into labelled entries; "
                          "kept verbatim as references[%d] with reference_id=None."
                          % idx,
                "reference_index": idx,
                "pages": sorted({s["page"] for s in spans}),
            })
    if builder.ref_entries and all(e["id"] is None for e in builder.ref_entries):
        issues.append({
            "code": "reference_style_unsupported",
            "detail": "No printed numeric labels matched in the reference region "
                      "(author-year or other unsupported style). The region text is "
                      "preserved verbatim as unparsed entries; no IDs were invented.",
        })
    if builder.refs_seen_heading and not builder.ref_entries:
        issues.append({
            "code": "reference_region_empty",
            "detail": "A reference heading was found but the region contained no text.",
        })
    if builder.gap_numbers:
        issues.append({
            "code": "reference_id_gap",
            "detail": "Reference labels missing inside the observed numeric range; the "
                      "cited labels stay in unresolved_citations.",
            "missing_labels": sorted(builder.gap_numbers),
        })
    if builder.dup_ids:
        issues.append({
            "code": "duplicate_reference_id",
            "detail": "Reference labels printed on more than one entry; all occurrences "
                      "kept and listed in duplicate_reference_ids.",
            "labels": sorted(builder.dup_ids),
        })
    if builder.non_monotonic:
        issues.append({
            "code": "reference_id_non_monotonic",
            "detail": "Reference labels do not appear in ascending order; possible column "
                      "or reading-order artifact.",
        })
    _check_reference_urls(records, issues)
    return records


# --------------------------------------------------------------------------
# In-text citations.
# --------------------------------------------------------------------------

def _expand_citation(body):
    labels = []
    too_large = False
    for token in body.split(","):
        token = token.strip()
        if not token:
            continue
        parts = re.split(r"\s*[-\u2013\u2014]\s*", token)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            low = int(parts[0])
            high = int(parts[1])
            if high < low:
                low, high = high, low
            if high - low > 50:
                too_large = True
                labels.append("%d-%d" % (low, high))
            else:
                labels.extend(str(v) for v in range(low, high + 1))
        elif token.isdigit():
            labels.append(str(int(token)))
        else:
            labels.append(token)
    return labels, too_large


def _collect_citations(blocks, records, issues):
    ref_ids = {r["reference_id"] for r in records if r["reference_id"] is not None}
    locators = {}
    unresolved = []
    unresolved_count = 0
    range_flag = False
    for block in blocks:
        for span in block["source_spans"]:
            for match in _CITATION_RE.finditer(span["quote"]):
                labels, too_large = _expand_citation(match.group(1))
                resolved = [x for x in labels if x in ref_ids]
                missing = [x for x in labels if x not in ref_ids]
                if resolved and block["in_intro"]:
                    for rid in resolved:
                        locators.setdefault(rid, []).append({"kind": FORMAT, "spans": [span]})
                if missing or too_large:
                    unresolved.append({
                        "raw": match.group(0),
                        "source_spans": [_sub_span(span, match.start(), match.end())],
                        "labels": labels,
                        "resolved": resolved,
                        "missing": missing,
                    })
                    unresolved_count += 1
                if too_large:
                    range_flag = True
    for record in records:
        if record["reference_id"] is not None:
            record["intro_context_locators"] = locators.get(record["reference_id"], [])
    if unresolved_count:
        issues.append({
            "code": "citation_unresolved",
            "detail": "%d in-text numeric citation(s) could not be resolved to a parsed "
                      "reference label; details are in unresolved_citations."
                      % unresolved_count,
            "count": unresolved_count,
        })
    if range_flag:
        issues.append({
            "code": "citation_range_too_large",
            "detail": "A bracketed numeric range spans more than 50 labels and was not "
                      "expanded; it is kept verbatim in unresolved_citations.",
        })
    return unresolved


# --------------------------------------------------------------------------
# Identity (first page only).
# --------------------------------------------------------------------------

def _filter_title_lines(lines):
    kept = []
    for (s, e, raw) in lines:
        st = raw.strip()
        if not st:
            continue
        if st.startswith(("*", "©")) or _META_LINE.match(st) or _NAME_LINE.match(st):
            continue
        kept.append((s, e, st))
    return kept


def _contiguous_spans(lines, page_no, text):
    pieces = [{"page": page_no, "start": s, "end": e, "raw": r} for (s, e, r) in lines]
    return _merge_spans(pieces, {page_no: text})


def _extract_identity(page_no, text, removed_idx, issues):
    content = [
        (s, e, raw)
        for i, (s, e, raw) in enumerate(_split_lines(text))
        if i not in removed_idx
    ]
    start_idx = None
    for k, (_s, _e, raw) in enumerate(content):
        st = raw.strip()
        if len(st) <= 60 and _ID_START_RE.search(st):
            start_idx = k + 1
            break
    abstract_idx = None
    for k, (_s, _e, raw) in enumerate(content):
        if _heading_re("abstract").match(raw.strip()):
            abstract_idx = k
            break

    candidates = []
    if start_idx is not None:
        end_a = len(content)
        for k in range(start_idx, len(content)):
            st = content[k][2].strip()
            if st.startswith(("*", "©")) or _META_LINE.match(st) or _NAME_LINE.match(st):
                end_a = k
                break
        kept_a = _filter_title_lines(content[start_idx:end_a])
        end_b = abstract_idx if (abstract_idx is not None and abstract_idx > start_idx) else len(content)
        kept_b = _filter_title_lines(content[start_idx:end_b])
        if kept_a:
            confidence = 0.85 if kept_b == kept_a else 0.7
            candidates.append((kept_a, confidence,
                               "front_matter_between_open_access_marker_and_author_or_address_line"))
        if kept_b and kept_b != kept_a:
            candidates.append((kept_b, 0.5,
                               "front_matter_between_open_access_marker_and_abstract_heading"))
    elif abstract_idx is not None:
        kept = _filter_title_lines(content[:abstract_idx])
        if kept:
            candidates.append((kept, 0.3, "fallback_lines_before_abstract_heading"))

    article_titles = None
    if candidates:
        out = []
        texts = []
        for lines, confidence, method in candidates:
            cand_text = _normalize_ws(" ".join(st for (_s, _e, st) in lines))
            out.append({
                "text": cand_text,
                "source_spans": _contiguous_spans(lines, page_no, text),
                "confidence": confidence,
                "method": method,
            })
            texts.append(cand_text)
        if len(texts) > 1:
            issues.append({
                "code": "identity_multiple_title_candidates",
                "detail": "First-page title candidates disagree; all are kept with their "
                          "spans and confidence for review. None is promoted silently.",
            })
        article_titles = out
    else:
        issues.append({
            "code": "identity_title_not_located",
            "detail": "No article title candidate located on the first page (no "
                      "open-access/research-article marker with an author or abstract "
                      "anchor); article_titles is None rather than guessed.",
        })

    article_ids = []
    whole = {"page": page_no, "start": 0, "end": len(text), "quote": text}
    marked = list(_DOI_MARKED.finditer(text))
    consumed = [(m.start(), m.end()) for m in marked]
    for m in marked:
        value = _clean_identifier_value(m.group(1), False)
        if value and not _is_probably_filename(value):
            article_ids.append({
                "type": "doi",
                "value": value,
                "raw": m.group(0),
                "source_spans": [_sub_span(whole, m.start(), m.end())],
                "confidence": 0.7,
            })
    for m in _DOI_BARE.finditer(text):
        if _overlaps(consumed, m.start(), m.end()):
            continue
        value = _clean_identifier_value(m.group(1), False)
        if value and not _is_probably_filename(value):
            article_ids.append({
                "type": "doi",
                "value": value,
                "raw": m.group(0),
                "source_spans": [_sub_span(whole, m.start(), m.end())],
                "confidence": 0.4,
            })
    for matcher, id_type, confidence in (
        (_PMID_RE, "pmid", 0.7),
        (_PMID_URL_RE, "pmid", 0.7),
        (_PMCID_RE, "pmcid", 0.7),
        (_PMCID_URL_RE, "pmcid", 0.7),
    ):
        for m in matcher.finditer(text):
            value = _clean_identifier_value(m.group(1), False)
            article_ids.append({
                "type": id_type,
                "value": value,
                "raw": m.group(0),
                "source_spans": [_sub_span(whole, m.start(), m.end())],
                "confidence": confidence,
            })
    if article_ids:
        ids_out = article_ids
    else:
        ids_out = None
        issues.append({
            "code": "identity_ids_not_found",
            "detail": "No explicit article identifier (DOI/PMID/PMCID) is printed on the "
                      "first page; article_ids is None. Identifiers from the reference "
                      "region are never reused as the article identity.",
        })
    return {"article_titles": article_titles, "article_ids": ids_out}


# --------------------------------------------------------------------------
# Document-level issues.
# --------------------------------------------------------------------------

def _document_level_issues(blocks, records, page_content, issues):
    hyphens = 0
    double_spaces = 0
    symbol_gaps = 0
    for block in blocks:
        for span in block["source_spans"]:
            hyphens += len(re.findall(r"[A-Za-z]-\r?\n", span["quote"]))
            double_spaces += span["quote"].count("  ")
            if re.search(r"\s=\s", span["quote"]) or "Ќ" in span["quote"]:
                symbol_gaps += 1
    for record in records:
        for span in record["source_spans"]:
            hyphens += len(re.findall(r"[A-Za-z]-\r?\n", span["quote"]))
            double_spaces += span["quote"].count("  ")
    if hyphens:
        issues.append({
            "code": "line_break_hyphens_preserved",
            "detail": "%d line-break hyphen(s) kept verbatim; de-hyphenation of extracted "
                      "text is ambiguous and was not attempted." % hyphens,
            "count": hyphens,
        })
    if double_spaces:
        issues.append({
            "code": "extraction_spacing_artifacts",
            "detail": "%d run(s) of multiple spaces kept verbatim; single-character "
                      "extraction artifacts (e.g. 'becaus e') cannot be detected reliably "
                      "and are never repaired silently." % double_spaces,
            "count": double_spaces,
        })
    if symbol_gaps:
        issues.append({
            "code": "missing_symbols_or_equation_gaps",
            "detail": "Lines contain orphan '=' comparisons or unmapped glyphs (e.g. 'Ќ'); "
                      "equations/symbols are partly missing from the text layer and were "
                      "not reconstructed.",
            "count": symbol_gaps,
        })
    for page_no in sorted(page_content):
        info = page_content[page_no]
        if info["chars"] < 800 and info["has_figure"]:
            issues.append({
                "code": "figure_caption_page",
                "detail": "Page %d carries almost only figure caption text; the figure "
                          "images themselves are absent from the text layer." % page_no,
                "page": page_no,
            })
        elif info["chars"] < 200:
            issues.append({
                "code": "low_text_page",
                "detail": "Page %d contains very little text (%d chars); possibly an "
                          "image-only page." % (page_no, info["chars"]),
                "page": page_no,
            })
    issues.append({
        "code": "reading_order_assumption",
        "detail": "Page order = ascending page number; within a page the extracted line "
                  "order is trusted. Column order, floated figures/equations and OCR gaps "
                  "are not reconstructed; citations or identifiers split across a page "
                  "boundary may be missed.",
    })
    issues.append({
        "code": "paragraph_segmentation_heuristic",
        "detail": "Paragraph breaks inferred from sentence-final punctuation plus a "
                  "capital/CJK line start; abbreviations like 'et al.' can cause extra "
                  "breaks. Text is never merged across section boundaries.",
    })
    issues.append({
        "code": "subsection_headings_heuristic",
        "detail": "Only vocabulary headings (multilingual intro/methods/results/... list) "
                  "are authoritative; other short standalone lines are heuristic "
                  "subsection candidates and may over- or under-split. The intro ends "
                  "only at vocabulary headings.",
    })


# --------------------------------------------------------------------------
# Public entry point.
# --------------------------------------------------------------------------

def extract_pdf_background(pages):
    """Extract introduction and references from extracted PDF text pages.

    Returns a dict with keys: version, format, identity, blocks, intro_blocks,
    references, unresolved_citations, duplicate_reference_ids, status, issues.
    Raises TypeError/ValueError on invalid input (see module docstring).
    """
    clean, original_order = _validate_pages(pages)
    issues = []
    numbers = [p["number"] for p in clean]
    gaps = [{"after": a, "before": b} for a, b in zip(numbers, numbers[1:]) if b - a > 1]
    if gaps:
        issues.append({"code": "page_sequence_gap", "gaps": gaps,
                       "detail": "Some intermediate source pages are absent."})
    if original_order != [p["number"] for p in clean]:
        issues.append({
            "code": "pages_reordered",
            "detail": "Input page order differs from ascending page number; pages were "
                      "processed in ascending numeric order.",
            "input_order": original_order,
        })

    header_keys, header_pages = _detect_running_headers(
        [(p["number"], p["text"]) for p in clean]
    )
    if header_keys:
        issues.append({
            "code": "running_headers_removed",
            "detail": "Repeated running header/footer lines were dropped from the flow "
                      "only (raw page text and all offsets are untouched). Matched by "
                      "digit-normalized equality on >= max(3, 50%) of pages with top/bottom "
                      "placement on >= 60% of their occurrences; no unique body line is "
                      "removed.",
            "patterns": sorted(header_keys),
            "pages": sorted(header_pages),
        })
    elif len(clean) >= 4:
        issues.append({
            "code": "running_headers_not_detected",
            "detail": "No repeated header/footer lines observed; any page furniture stays "
                      "inside blocks.",
        })

    page_texts = {}
    items = []
    removed_by_page = {}
    for page in clean:
        page_texts[page["number"]] = page["text"]
        page_items, removed = _page_stream(page["number"], page["text"], header_keys)
        items.extend(page_items)
        if removed:
            removed_by_page[page["number"]] = removed

    page_content = {}
    for item in items:
        if item["detached"]:
            continue
        info = page_content.setdefault(item["page"], {"chars": 0, "has_figure": False})
        info["chars"] += len(item["raw"])
        if re.search(r"[Ff]igure\s*[0-9]", item["raw"]):
            info["has_figure"] = True

    builder = _Builder(page_texts, issues)
    builder.feed(items)
    builder.finish()
    blocks = builder.blocks

    records = _build_references(builder, page_texts, issues)
    unresolved = _collect_citations(blocks, records, issues)

    first = clean[0]
    if first["number"] == 1:
        identity = _extract_identity(
            first["number"], first["text"], removed_by_page.get(first["number"], set()), issues
        )
    else:
        identity = {"article_titles": None, "article_ids": []}
        issues.append({"code": "front_page_missing",
                       "detail": "Article identity requires the original first page."})

    if not builder.intro_found or not any(b["in_intro"] for b in blocks):
        issues.append({
            "code": "intro_not_found",
            "detail": "No Introduction/Background/引言/背景/绪论/前言 heading with body "
                      "content was found; intro_blocks is empty. The abstract (if any) is "
                      "deliberately not treated as the intro.",
        })
    elif not builder.intro_terminated:
        issues.append({
            "code": "intro_unterminated",
            "detail": "The intro section was never closed by a vocabulary heading "
                      "(methods/results/discussion/...); it may absorb later sections.",
        })

    if builder.refs_seen_heading is False:
        issues.append({
            "code": "references_not_found",
            "detail": "No References/Bibliography/参考文献 heading was found; references "
                      "is empty rather than guessed from body text.",
        })

    _document_level_issues(blocks, records, page_content, issues)

    status = "needs_review" if any(i["code"] in _REVIEW_CODES for i in issues) else "indexed"

    return {
        "version": __version__,
        "format": FORMAT,
        "identity": identity,
        "blocks": blocks,
        "intro_blocks": [b for b in blocks if b["in_intro"]],
        "references": records,
        "unresolved_citations": unresolved,
        "duplicate_reference_ids": [str(x) for x in sorted(builder.dup_ids)],
        "status": status,
        "issues": issues,
    }


