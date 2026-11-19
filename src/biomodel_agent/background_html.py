"""PMC HTML identities, article sections and citation candidates.

Derived from the independently checked DSH component. Original element paths
accompany spans into deterministic text pages. Experimental eligibility remains
pending review and image content remains untranscribed.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

__all__ = ["extract_background"]

FORMAT = "pmc_html"
VERSION = "1.1.0"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_NODES = 200000
MAX_DEPTH = 64
MAX_FORMULA_RECORDS = 100
EQUATION_PREFIX = "[unreviewed-mathml] "
IMAGE_FORMULA_PREFIX = "[unreviewed-formula]"

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                   "link", "meta", "param", "source", "track", "wbr"})
# Subtrees that must never contribute to article text (site chrome/scripts/forms).
_SKIP_TAGS = frozenset({"script", "style", "svg", "iframe", "frame", "frameset",
                        "object", "embed", "applet", "form", "input", "button",
                        "select", "option", "optgroup", "textarea", "canvas",
                        "map", "area", "audio", "video", "source", "track",
                        "nav", "header", "footer", "aside", "dialog", "noscript",
                        "template", "meta", "link", "base", "head", "title"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_UTILITY_LINK_LABELS = frozenset({"doi", "pubmed", "google scholar", "pmc",
                                  "pmc free article", "europe pmc",
                                  "publisher full text", "crossref"})
_ABSTRACT_TITLES = {"abstract", "summary", "摘要"}
_REF_LIKE_RE = re.compile(r"^(?:b|bib|ref|r)\d+$", re.I)
_PMCID_TEXT_RE = re.compile(r"PMCID\s*[:：]\s*PMC\s*(\d{1,12})", re.I)
_PMID_TEXT_RE = re.compile(r"PMID\s*[:：]\s*(\d{1,12})")
_WS_RE = re.compile(r"\s+")
_VALIDATION_MARKERS = ("verify you are human", "are you a robot", "captcha",
                       "enable javascript", "checking your browser",
                       "please complete the security check", "access denied")
_REVIEW_CODES = {"inferred_unheaded_introduction", "duplicate_reference_ids",
                 "unreviewed_formulas", "mathml_not_projected",
                 "conflicting_article_identity", "conflicting_article_titles",
                 "multiple_articles_main_selected", "body_section_not_found",
                 "no_reference_list", "identity_missing", "title_not_found",
                 "unresolved_citations"}


class _ParseLimitError(Exception):
    pass


class _Node:
    __slots__ = ("tag", "attrs", "parent", "children", "path")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children = []
        self.path = ""


class _TreeBuilder(HTMLParser):
    """Element-tree builder; HTML void elements parse without a stack push."""

    def __init__(self, max_nodes, max_depth):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self.stack = [self.root]
        self.count = 1
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def _make(self, tag, attrs):
        adict = {}
        for key, value in attrs:
            if key not in adict:
                adict[key] = value if value is not None else ""
        node = _Node(tag, adict, self.stack[-1])
        self.stack[-1].children.append(node)
        self.count += 1
        if self.count > self.max_nodes:
            raise _ParseLimitError("node limit exceeded")
        return node

    def handle_starttag(self, tag, attrs):
        node = self._make(tag, attrs)
        if tag not in _VOID:
            if len(self.stack) >= self.max_depth:
                raise _ParseLimitError("depth limit exceeded")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._make(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not data:
            return
        self.stack[-1].children.append(data)
        self.count += 1
        if self.count > self.max_nodes:
            raise _ParseLimitError("node limit exceeded")


def _build_tree(text, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    builder = _TreeBuilder(max_nodes, max_depth)
    try:
        builder.feed(text)
        builder.close()
    except _ParseLimitError as exc:
        raise ValueError("PMC HTML limit violated: %s" % exc) from None
    except Exception as exc:  # malformed markup that upsets the parser itself
        raise ValueError("unparsable HTML: %s" % exc) from None
    return builder.root


def _assign_paths(root):
    def walk(node, prefix):
        counters = {}
        for child in node.children:
            if isinstance(child, str):
                continue
            counters[child.tag] = counters.get(child.tag, 0) + 1
            child.path = "%s/%s[%d]" % (prefix, child.tag, counters[child.tag])
            walk(child, child.path)

    walk(root, "")


def _norm(text):
    return _WS_RE.sub(" ", text).strip()


def _classes(node):
    return set((node.attrs.get("class") or "").split())


def _locator(node):
    return {"kind": "html", "path": node.path, "id": node.attrs.get("id") or None}


def _iter_elements(node):
    for child in node.children:
        if isinstance(child, str):
            continue
        yield child
        yield from _iter_elements(child)


def _find_all(node, pred):
    return [n for n in _iter_elements(node) if pred(n)]


def _find_first(node, pred):
    for n in _iter_elements(node):
        if pred(n):
            return n
    return None


def _count_images(node):
    return sum(1 for n in _iter_elements(node) if n.tag == "img")




# --- MathML projection (source notation, never executable) ---------------

_MATH_ATTRS = frozenset({"id", "class", "display", "alttext", "xmlns"})


def _serialize_math(node):
    """Serialize a <math> subtree as inert XML text (structure preserved)."""
    import xml.etree.ElementTree as ET

    def build(current):
        attrs = {key: value for key, value in current.attrs.items()
                 if key.lower() in _MATH_ATTRS and value is not None}
        element = ET.Element(current.tag, attrs)
        for child in current.children:
            if isinstance(child, str):
                if len(element):
                    element[-1].tail = (element[-1].tail or "") + child
                else:
                    element.text = (element.text or "") + child
            elif child.tag in _SKIP_TAGS:
                continue
            else:
                element.append(build(child))
        return element

    return ET.tostring(build(node), encoding="unicode",
                       short_empty_elements=True)


def _math_token(node):
    return EQUATION_PREFIX + _norm(_serialize_math(node))


def _math_display(node):
    if (node.attrs.get("display") or "").lower() == "block":
        return True
    ancestor = node.parent
    while ancestor is not None:
        if "disp-formula" in _classes(ancestor):
            return True
        if ancestor.tag == "section":
            return node.parent is ancestor
        ancestor = ancestor.parent
    return False


def _math_skipped(node):
    """True when a <math> lives under site chrome/script/svg and must not be
    projected into article text (it is reported as not projected instead)."""
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.tag in _SKIP_TAGS:
            return True
        ancestor = ancestor.parent
    return False


# --- text extraction (blocks are atomic; #anchors recorded as citations) ---

def _inline_text(node, citations=None):
    if node.tag == "math":
        return _math_token(node)
    parts = []

    def walk(current):
        for child in current.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in _SKIP_TAGS:
                continue
            elif child.tag == "br":
                parts.append(" ")
            elif child.tag == "img":
                continue
            elif child.tag == "math":
                parts.append(_math_token(child))
            elif child.tag == "a":
                txt = _inline_text(child)
                if citations is not None:
                    href = child.attrs.get("href") or ""
                    if href.startswith("#"):
                        citations.append((href[1:], _norm(txt), child))
                parts.append(txt)
            else:
                walk(child)

    walk(node)
    return _norm("".join(parts))


def _figure_text(fig, citations):
    parts = []

    def walk(current):
        for child in current.children:
            if isinstance(child, str):
                continue
            if child.tag in _SKIP_TAGS or child.tag in ("a", "img"):
                continue
            if child.tag in _HEADING_TAGS or child.tag == "figcaption":
                parts.append(_inline_text(child, citations))
            else:
                walk(child)

    walk(fig)
    return " ".join(p for p in parts if p)


def _table_text(table, citations):
    cells = [_inline_text(el, citations) for el in _iter_elements(table)
             if el.tag in ("td", "th")]
    return " ".join(c for c in cells if c)


def _tablewrap_text(wrap, citations):
    parts = []

    def walk(current):
        for child in current.children:
            if isinstance(child, str):
                continue
            if child.tag in _SKIP_TAGS or child.tag in ("a", "img"):
                continue
            if child.tag in _HEADING_TAGS:
                parts.append(_inline_text(child, citations))
            elif child.tag == "table":
                parts.append(_table_text(child, citations))
            elif ("caption" in _classes(child) or "fn" in _classes(child)
                  or child.tag == "figcaption"):
                parts.append(_inline_text(child, citations))
            else:
                walk(child)

    walk(wrap)
    return " ".join(p for p in parts if p)


def _li_citation_text(li):
    parts = []

    def walk(current):
        for child in current.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in _SKIP_TAGS:
                continue
            elif child.tag == "a":
                if _norm(_inline_text(child)).lower() in _UTILITY_LINK_LABELS:
                    continue
                parts.append(_inline_text(child))
            else:
                walk(child)

    walk(li)
    return _norm("".join(parts))


# --- body blocks ----------------------------------------------------------

def _first_heading_child(section):
    for child in section.children:
        if not isinstance(child, str) and child.tag in _HEADING_TAGS:
            return child
    return None


def _section_type(section):
    found = _classes(section) & {"abstract", "ack", "ref-list", "glossary",
                                 "fn-group", "kwd-group"}
    if found:
        return sorted(found)[0]
    match = re.fullmatch(r"([a-zA-Z][a-zA-Z_-]*?)(\d+)", section.attrs.get("id") or "")
    return match.group(1).lower() if match else "section"


def _is_intro_title(title):
    text = re.sub(r"^\s*(?:\d+[\.\)]\s*|\d+\s+)", "", _norm(title).lower())
    return bool(text) and (text.startswith("introduction")
                           or text.startswith("background")
                           or text.startswith("引言") or text.startswith("背景"))


def _collect_body_blocks(body):
    units, stack = [], []
    state = {"seen_headed": False, "unheaded_intro": False}

    def in_intro_now():
        if any(f[2] for f in stack):
            return True
        return not stack and not state["seen_headed"]

    def emit(node, raw_text, citations, kind):
        text = _norm(raw_text)
        if not text:
            return
        intro = in_intro_now()
        if intro and not stack and not state["seen_headed"] and kind != "heading":
            state["unheaded_intro"] = True
        titles = [f[0] for f in stack if f[0]]
        types = [f[1] for f in stack if f[0]]
        units.append({"node": node, "text": text, "section_titles": titles,
                      "section_sec_types": types, "in_intro": intro,
                      "citations": citations, "images": _count_images(node)})

    def visit(node):
        for child in node.children:
            if isinstance(child, str):
                continue
            tag = child.tag
            if tag in _SKIP_TAGS or tag == "hr":
                continue
            cls = _classes(child)
            if tag == "figure" or "fig" in cls:
                cites = []
                emit(child, _figure_text(child, cites), cites, "figure")
            elif "tw" in cls:
                cites = []
                emit(child, _tablewrap_text(child, cites), cites, "table")
            elif tag == "table" and "disp-formula" in cls:
                cites = []
                text = _table_text(child, cites)
                if not _norm(text):
                    text = IMAGE_FORMULA_PREFIX
                emit(child, text, cites, "table")
            elif tag == "math":
                cites = []
                emit(child, _inline_text(child, cites), cites, "formula")
            elif tag == "section":
                if cls & {"abstract", "ref-list", "kwd-group"}:
                    continue
                heading = _first_heading_child(child)
                title = _norm(_inline_text(heading)) if heading is not None else ""
                if title and title.lower() not in _ABSTRACT_TITLES:
                    state["seen_headed"] = True
                stack.append((title, _section_type(child), _is_intro_title(title)))
                visit(child)
                stack.pop()
            elif tag in ("p", "dt", "dd"):
                cites = []
                emit(child, _inline_text(child, cites), cites, tag)
            elif tag in ("ul", "ol"):
                for item in child.children:
                    if not isinstance(item, str) and item.tag == "li":
                        cites = []
                        emit(item, _inline_text(item, cites), cites, "li")
            elif tag in _HEADING_TAGS:
                cites = []
                emit(child, _inline_text(child, cites), cites, "heading")
            else:
                visit(child)

    if body is not None:
        visit(body)
    return units, state


# --- references -----------------------------------------------------------

def _ref_list_roots(article):
    roots = []
    for node in _iter_elements(article):
        if "ref-list" not in _classes(node):
            continue
        ancestor, nested = node.parent, False
        while ancestor is not None and ancestor is not article:
            if "ref-list" in _classes(ancestor):
                nested = True
                break
            ancestor = ancestor.parent
        if not nested:
            roots.append(node)
    return roots


def _collect_references(article):
    roots = _ref_list_roots(article)
    refs, duplicates, seen = [], [], set()
    for root in roots:
        for li in _iter_elements(root):
            if li.tag != "li":
                continue
            rid = (li.attrs.get("id") or "").strip() or None
            if rid is not None:
                if rid in seen:
                    duplicates.append(rid)
                seen.add(rid)
            identifiers = []
            for anchor in _iter_elements(li):
                if anchor.tag != "a":
                    continue
                href = anchor.attrs.get("href") or ""
                doi_value = _normalize_doi(href)
                if doi_value:
                    identifiers.append({"type": "doi", "value": doi_value,
                                        "raw": href, "locator": _locator(anchor)})
                else:
                    pmid_value = _pmid_from_url(href)
                    if pmid_value:
                        identifiers.append({"type": "pmid", "value": pmid_value,
                                            "raw": href, "locator": _locator(anchor)})
            title = None
            for element in _iter_elements(li):
                if _classes(element) & {"article-title", "ref-title"}:
                    title = _norm(_inline_text(element)) or None
                    break
            refs.append({"reference_index": len(refs) + 1, "reference_id": rid,
                         "title": title, "citation_text": _li_citation_text(li),
                         "identifiers": identifiers, "intro_context_locators": [],
                         "eligibility": "pending_review"})
    return refs, duplicates, bool(roots)


# --- identity validators and collectors -----------------------------------

def _numeric_id(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not text.isascii() or not text.isdigit() or len(text) > 12:
        return None
    if text == "0" or (len(text) > 1 and text[0] == "0"):
        return None
    return text


def _normalize_doi(value):
    if not isinstance(value, str):
        return None
    text = value.strip().strip("<>").strip()
    match = re.match(r"(?i)^https?://(?:dx\.)?doi\.org/(.+)$", text)
    if match:
        text = match.group(1)
    elif text.lower().startswith("doi:"):
        text = text[4:].strip()
    text = text.strip().lower()
    if not text or "?" in text or "#" in text:
        return None
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def _pmid_from_url(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if "?" in text or "#" in text:
        return None
    try:
        parts = urlparse(text)
    except ValueError:
        return None
    if (parts.netloc or "").lower() != "pubmed.ncbi.nlm.nih.gov":
        return None
    match = re.fullmatch(r"/(\d{1,12})/?", parts.path or "")
    return _numeric_id(match.group(1)) if match else None


def _pmcid_from_url(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if "?" in text or "#" in text:
        return None
    try:
        parts = urlparse(text)
    except ValueError:
        return None
    if (parts.scheme or "").lower() not in ("http", "https"):
        return None
    host, path = (parts.netloc or "").lower(), parts.path or ""
    if host == "pmc.ncbi.nlm.nih.gov":
        match = re.fullmatch(r"/articles/(PMC\d{1,12})/?", path, re.I)
    elif host in ("www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"):
        match = re.fullmatch(r"/pmc/articles/(PMC\d{1,12})/?", path, re.I)
    else:
        return None
    if not match:
        return None
    digits = match.group(1)[3:]
    if digits == "0" or (len(digits) > 1 and digits[0] == "0"):
        return None
    return "PMC" + digits


def _pmcid_from_article_id(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:PMC)?(\d{1,12})", value.strip(), re.I)
    if not match:
        return None
    digits = match.group(1)
    if digits == "0" or (len(digits) > 1 and digits[0] == "0"):
        return None
    return "PMC" + digits


def _collect_titles(article, root):
    entries, issues = [], []
    h1s = _find_all(article, lambda n: n.tag == "h1")
    if len(h1s) > 1:
        issues.append({"code": "multiple_h1_titles",
                       "detail": "%d <h1> elements; using the first" % len(h1s)})
    h1_text = ""
    if h1s:
        h1_text = _norm(_inline_text(h1s[0]))
        if h1_text:
            entries.append({"text": h1_text, "locator": _locator(h1s[0]),
                            "source": "h1"})
    for meta in _find_all(root, lambda n: n.tag == "meta"):
        if (meta.attrs.get("name") or "").strip().lower() != "citation_title":
            continue
        content = _norm(meta.attrs.get("content") or "")
        if content and (not h1_text or content != h1_text):
            entries.append({"text": content, "locator": _locator(meta),
                            "source": "meta:citation_title"})
    if not entries:
        issues.append({"code": "title_not_found",
                       "detail": "no article <h1> or citation_title meta found"})
    unique_titles = {e["text"] for e in entries}
    if len(unique_titles) > 1:
        issues.append({"code": "conflicting_article_titles",
                       "detail": "distinct title candidates: %s"
                                 % " vs ".join(sorted(unique_titles))})
    return entries, issues


def _collect_identity(root, article):
    entries = []
    for meta in _find_all(root, lambda n: n.tag == "meta"):
        name = (meta.attrs.get("name") or "").strip().lower()
        content = meta.attrs.get("content") or ""
        if not content.strip():
            continue
        if name == "citation_doi":
            value, kind = _normalize_doi(content), "doi"
        elif name == "citation_pmid":
            value, kind = _numeric_id(content), "pmid"
        elif name in ("citation_fulltext_html_url", "citation_abstract_html_url",
                      "og:url"):
            value, kind = _pmcid_from_url(content), "pmcid"
        else:
            continue
        if value:
            entries.append({"type": kind, "value": value, "raw": content,
                            "locator": _locator(meta), "source": "meta:" + name})
    for element in _iter_elements(root):
        raw = element.attrs.get("data-article-id")
        value = _pmcid_from_article_id(raw) if raw else None
        if value:
            entries.append({"type": "pmcid", "value": value, "raw": raw,
                            "locator": _locator(element),
                            "source": "data-article-id"})

    body = _find_first(article, lambda n: n.tag == "section"
                       and "body" in _classes(n))
    ref_roots = {id(n) for n in _ref_list_roots(article)}

    def visit(node, parent):
        for child in node.children:
            if isinstance(child, str):
                for match in _PMCID_TEXT_RE.finditer(child):
                    digits = _numeric_id(match.group(1))
                    if digits:
                        entries.append({"type": "pmcid", "value": "PMC" + digits,
                                        "raw": match.group(0).strip(),
                                        "locator": _locator(parent),
                                        "source": "frontmatter-text"})
                for match in _PMID_TEXT_RE.finditer(child):
                    value = _numeric_id(match.group(1))
                    if value:
                        entries.append({"type": "pmid", "value": value,
                                        "raw": match.group(0).strip(),
                                        "locator": _locator(parent),
                                        "source": "frontmatter-text"})
                continue
            if child is body or id(child) in ref_roots:
                continue
            if child.tag == "a":
                href = child.attrs.get("href") or ""
                loc = _locator(child)
                for kind, value in (("doi", _normalize_doi(href)),
                                    ("pmid", _pmid_from_url(href)),
                                    ("pmcid", _pmcid_from_url(href))):
                    if value:
                        entries.append({"type": kind, "value": value, "raw": href,
                                        "locator": loc,
                                        "source": "frontmatter-link"})
                        break
                continue
            visit(child, child)

    visit(article, article)
    return entries


def _identity_line(entries):
    collected = {"pmcid": [], "pmid": [], "doi": []}
    labels = {"pmcid": "PMCID", "pmid": "PMID", "doi": "DOI"}
    for entry in entries:
        kind = entry["type"]
        if kind in collected and entry["value"] not in collected[kind]:
            collected[kind].append(entry["value"])
    return " | ".join("%s: %s" % (labels[k], ", ".join(collected[k]))
                      for k in ("pmcid", "pmid", "doi") if collected[k])


# --- public API -----------------------------------------------------------

def extract_background(raw: bytes) -> dict:
    """Parse one public PMC article HTML page into a traceable background dict."""
    if isinstance(raw, (bytearray, memoryview)):
        raw = bytes(raw)
    if not isinstance(raw, bytes):
        raise ValueError("extract_background expects raw UTF-8 bytes")
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds %d byte limit (%d given)"
                         % (MAX_INPUT_BYTES, len(raw)))
    if not raw.strip():
        raise ValueError("empty input")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("input is not valid UTF-8: %s" % exc) from None

    root = _build_tree(text)
    _assign_paths(root)
    lowered = text.lower()
    articles = _find_all(root, lambda n: n.tag == "article")
    if not articles:
        for marker in _VALIDATION_MARKERS:
            if marker in lowered:
                raise ValueError("validation/interstitial page detected (marker %r)"
                                 % marker)
        raise ValueError("no <article> element found; page is not a parsable PMC "
                         "full-text article page")

    issues = []
    if len(articles) == 1:
        article = articles[0]
    else:
        candidates = [a for a in articles if _find_first(
            a, lambda n: n.tag == "section" and "body" in _classes(n))]
        if len(candidates) != 1:
            raise ValueError("multiple <article> elements without a single "
                             "determinable main article body")
        article = candidates[0]
        issues.append({"code": "multiple_articles_main_selected",
                       "detail": "%d articles; selected the one with a main body"
                                 % len(articles)})

    title_entries, title_issues = _collect_titles(article, root)
    issues.extend(title_issues)
    identity_entries = _collect_identity(root, article)
    if not identity_entries:
        issues.append({"code": "identity_missing",
                       "detail": "no verifiable doi/pmid/pmcid in raw HTML"})
    conflicts = {}
    for entry in identity_entries:
        conflicts.setdefault(entry["type"], set()).add(entry["value"])
    conflicted = {k: sorted(v) for k, v in conflicts.items() if len(v) > 1}
    if conflicted:
        issues.append({"code": "conflicting_article_identity",
                       "detail": "; ".join("%s: %s" % (k, " vs ".join(v))
                                           for k, v in sorted(conflicted.items()))})

    body = _find_first(article, lambda n: n.tag == "section"
                       and "body" in _classes(n))
    if body is None:
        issues.append({"code": "body_section_not_found",
                       "detail": "no section with a 'body' class; no body blocks"})
        units = []
        state = {"seen_headed": False, "unheaded_intro": False}
    else:
        units, state = _collect_body_blocks(body)
    if state["unheaded_intro"]:
        issues.append({"code": "inferred_unheaded_introduction",
                       "detail": "paragraphs between abstract and first headed "
                                 "section kept as inferred intro (in_intro=True)"})

    refs, duplicates, has_ref_list = _collect_references(article)
    unique_dups = []
    for rid in duplicates:
        if rid not in unique_dups:
            unique_dups.append(rid)
    if unique_dups:
        issues.append({"code": "duplicate_reference_ids",
                       "detail": "repeated ref-list li ids: %s"
                                 % ", ".join(unique_dups)})
    if not has_ref_list:
        issues.append({"code": "no_reference_list",
                       "detail": "no ref-list section found; references empty"})

    ref_ids = {r["reference_id"] for r in refs if r["reference_id"]}
    by_id = {}
    for ref in refs:
        if ref["reference_id"]:
            by_id.setdefault(ref["reference_id"], []).append(ref)

    unresolved, seen_unresolved = [], set()
    for unit in units:
        loc = _locator(unit["node"])
        if unit["in_intro"]:
            for fragment, _text, _anchor in unit["citations"]:
                for ref in by_id.get(fragment, []):
                    paths = [l["path"] for l in ref["intro_context_locators"]]
                    if loc["path"] not in paths:
                        ref["intro_context_locators"].append(loc)
        for fragment, anchor_text, _anchor in unit["citations"]:
            if fragment in ref_ids or not _REF_LIKE_RE.match(fragment):
                continue
            key = (fragment, loc["path"], anchor_text)
            if key not in seen_unresolved:
                seen_unresolved.add(key)
                unresolved.append({"reference_id": fragment, "locator": loc,
                                   "anchor_text": anchor_text})
    if unresolved:
        issues.append({"code": "unresolved_citations",
                       "detail": "citations to unknown ids: %s"
                                 % ", ".join(u["reference_id"] for u in unresolved)})
    if units and not any(u["in_intro"] for u in units):
        issues.append({"code": "introduction_not_found",
                       "detail": "no introduction heading or leading unheaded "
                                 "paragraphs; intro_blocks empty"})

    image_total = sum(u["images"] for u in units)
    if image_total:
        examples = []
        for unit in units:
            for element in _iter_elements(unit["node"]):
                if element.tag == "img":
                    examples.append(element.path)
                if len(examples) >= 3:
                    break
            if len(examples) >= 3:
                break
        issues.append({"code": "image_not_transcribed",
                       "detail": "%d <img> inside kept blocks not transcribed; "
                                 "image content not reviewed or claimed (%s)"
                                 % (image_total, "; ".join(examples))})

    # canonical page assembly; block spans point into this text
    title_unit = None
    if title_entries and title_entries[0]["source"] == "h1":
        title_unit = {"node": _find_first(article, lambda n: n.tag == "h1"),
                      "text": title_entries[0]["text"], "section_titles": [],
                      "section_sec_types": [], "in_intro": False,
                      "citations": [], "images": 0}
    segments = []
    if title_unit is not None:
        segments.append((title_unit["text"], title_unit))
    elif title_entries:
        segments.append((title_entries[0]["text"], None))
    segments.extend((u["text"], u) for u in units)
    id_line = _identity_line(identity_entries)
    if id_line:
        segments.append((id_line, None))
    if refs:
        segments.append(("References", None))
        segments.extend(("[%d] %s" % (r["reference_index"], r["citation_text"]), None)
                        for r in refs)

    page_parts, position = [], 0
    for index, (seg_text, unit) in enumerate(segments):
        if index:
            page_parts.append("\n")
            position += 1
        start = position
        page_parts.append(seg_text)
        position += len(seg_text)
        if unit is not None:
            unit["spans"] = [{"page": 1, "start": start, "end": position,
                              "quote": seg_text}]
    page_text = "".join(page_parts)

    # MathML projection: locate every <math> in the article, project those that
    # actually enter a kept block, and keep the rest explicitly unprojected.
    math_nodes = [n for n in _iter_elements(article) if n.tag == "math"]
    formula_records, by_node = [], {}
    for index, node in enumerate(math_nodes, start=1):
        record = {"formula_index": index,
                  "formula_id": node.attrs.get("id") or None,
                  "locator": _locator(node),
                  "display": _math_display(node),
                  "representation": "parsed_mathml_serialization",
                  "status": "not_projected",
                  "source_spans": []}
        formula_records.append(record)
        by_node[id(node)] = record

    for unit in ([title_unit] if title_unit is not None else []) + units:
        cursor = 0
        candidates = ([unit["node"]] if unit["node"] is not None else [])
        candidates = candidates + list(_iter_elements(unit["node"]))
        for node in candidates:
            if node.tag != "math" or _math_skipped(node):
                continue
            record = by_node.get(id(node))
            if record is None or not unit.get("spans"):
                continue
            token = _math_token(node)
            index = unit["text"].find(token, cursor)
            if index < 0:
                continue
            cursor = index + len(token)
            start = unit["spans"][0]["start"] + index
            record["status"] = "unreviewed"
            record["source_spans"] = [{"page": 1, "start": start,
                                       "end": start + len(token), "quote": token}]

    unprojected = [r for r in formula_records if r["status"] == "not_projected"]
    if unprojected:
        issues.append({"code": "mathml_not_projected",
                       "detail": "%d <math> element(s) outside projected article text: %s"
                                 % (len(unprojected),
                                    "; ".join(r["locator"]["path"] for r in unprojected[:3]))})
    projected = [r for r in formula_records if r["status"] == "unreviewed"]
    if projected:
        issues.append({"code": "unreviewed_formulas",
                       "detail": "%d MathML formula(s) projected as unreviewed source notation; "
                                 "fractions/scripts kept as structure, not flattened into an "
                                 "executable equation" % len(projected)})
    formula_source_count = len(formula_records)
    formula_sources_truncated = formula_source_count > MAX_FORMULA_RECORDS
    formula_sources = formula_records[:MAX_FORMULA_RECORDS]

    blocks_out = []
    for unit in ([title_unit] if title_unit is not None else []) + units:
        blocks_out.append({"text": unit["text"],
                           "section_titles": unit["section_titles"],
                           "section_sec_types": unit["section_sec_types"],
                           "in_intro": unit["in_intro"],
                           "locator": _locator(unit["node"]),
                           "source_spans": unit.get("spans", [])})
    if not blocks_out and not refs and not title_entries and not identity_entries:
        raise ValueError("no parsable article content found in input")

    status = ("needs_review"
              if any(i["code"] in _REVIEW_CODES for i in issues) else "indexed")
    return {"format": FORMAT, "version": VERSION,
            "identity": {"article_titles": title_entries,
                         "article_ids": identity_entries},
            "blocks": blocks_out,
            "intro_blocks": [b for b in blocks_out if b["in_intro"]],
            "references": refs, "unresolved_citations": unresolved,
            "duplicate_reference_ids": unique_dups,
            "formula_sources": formula_sources,
            "formula_source_count": formula_source_count,
            "formula_sources_truncated": formula_sources_truncated,
            "status": status,
            "issues": issues, "pages": [{"number": 1, "text": page_text}]}


