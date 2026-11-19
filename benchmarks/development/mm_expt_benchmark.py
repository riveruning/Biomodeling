#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline JATS citation index for the MM + Expt benchmark catalog.

Task dsh-mm-expt-citation-index-20261111 -- development tool, curator only.

Scope (deliberately narrow; no network, no LLM, no experiment execution):

* ``index_catalog(catalog, cache_root)`` validates a catalog object
  (``schema_version=1``, ``target_mm_cases=100``, ``cases=[...]``) and reads,
  for every case, exactly one frozen cache file:
  ``<cache_root>/fulltext/<mm_pmcid>.xml`` with ``mm_pmcid`` normalised to
  ``PMC`` + a positive integer (the SDK PMCID convention).  The cache is never
  written to and no other path is read.
* The result is ONE auditable JSON-serialisable dict: one row per input case
  (rows are never dropped), source path/bytes/sha256, identity fields from
  JATS placed next to the catalog/API candidate fields (explicit
  ``identity_mismatch`` on PMCID/DOI conflicts), structure counts as
  complexity *clues*, full intro paragraphs (normalised text + XML locator +
  text sha256) with their ``xref ref-type="bibr"`` citation evidence mapped
  onto the complete reference list (reference id, article-title, DOI/PMID/
  PMCID from ``<pub-id>`` or typed ``<ext-link>`` identifiers, preserving
  their provenance. Identifiers are never inferred from citation prose).
* Every reference keeps ``experimental_eligibility='pending_review'``; title
  term hits are ``triage_flags`` only and animal/disease model wording is
  never treated as mathematical-model evidence.  MathML/formula originals
  stay in the frozen cache file; the index stores counts/flags, never
  transcriptions.
* The output is ``curator_only``: ``task_b_packet_ready=false`` and
  ``scientific_evaluation_executed=false``.

Explicit per-row errors: missing file, non-regular file, byte budget
(>20 MiB), non-UTF-8, NUL byte, ``<!ENTITY`` declaration / internal DTD
subset, XInclude element, invalid XML, node/depth budget (>200000 nodes,
deeper than 128 levels).  Top-level catalog errors (exception
``CatalogError``): malformed catalog, duplicate ``case_id``, duplicate MM
PMCID.  A plain external DOCTYPE declaration is accepted.

CLI: ``python benchmarks/development/mm_expt_benchmark.py index --catalog
FILE --cache DIR --out FILE`` -- stable UTF-8 JSON, atomic single-file write.

catalog_sha256 convention: sha256 over the canonical bytes of the parsed
catalog object, produced by the codec recorded in ``catalog_sha256_codec``
(the SDK ``canonical_bytes`` when importable, else a documented stdlib
fallback); recompute with the same codec on the same parsed object.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ['index_catalog', 'main', 'CatalogError', 'normalize_pmcid']

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
SCHEMA_VERSION = 1
INDEX_VERSION = '3'
TARGET_MM_CASES = 100
FULLTEXT_DIRNAME = 'fulltext'
MAX_FULLTEXT_BYTES = 20_971_520   # 20 MiB: complex papers of at least 20 MB must pass
MAX_XML_NODES = 200_000
MAX_XML_DEPTH = 128               # depth of the root element counts as 1
MAX_SECTION_TITLES = 1000
XINCLUDE_NS = '{http://www.w3.org/2001/XInclude}'
CASE_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
PMC_INT_RE = re.compile(r'[1-9][0-9]{0,11}')
DTD_ENTITY_RE = re.compile(r'<!\s*ENTITY|<!DOCTYPE[^>]*\[', re.I | re.S)

STATUS_INDEXED = 'indexed'
STATUS_PENDING = 'pending_review'
STATUS_ERROR = 'error'

# Triage term lists are heuristics for curator triage only; they are never
# evidence and never change experimental_eligibility.
MATH_MODELING_TERMS = (
    'mathematical model', 'mathematical modelling', 'mathematical modeling',
    'computational model', 'computational modelling', 'computational modeling',
    'in silico', 'simulation', 'simulations', 'simulated',
    'dynamical system', 'dynamical systems', 'differential equation',
    'differential equations', 'ordinary differential', 'partial differential',
    'agent-based model', 'network model', 'stochastic model',
    'compartmental model',
)
ORGANISM_DISEASE_MODEL_TERMS = (
    'animal model', 'mouse model', 'mice model', 'rat model', 'murine model',
    'disease model', 'model organism', 'cell model', 'in vivo model',
    'in vitro model', 'xenograft model', 'transgenic model',
    'knockout model', 'organoid model', 'patient-derived model',
)
COUNT_KEYS = {
    'inline-formula': 'inline_formula',
    'disp-formula': 'disp_formula',
    'table': 'table_element',
    'table-wrap': 'table_wrap',
    'fig': 'figure',
    'sec': 'section',
    'p': 'paragraph',
    'abstract': 'abstract',
}

# Reuse the SDK canonicaliser when importable; the recorded codec keeps the
# catalog_sha256 convention auditable either way.
try:
    _SDK_SRC = str(Path(__file__).resolve().parents[2] / 'src')
    if Path(_SDK_SRC, 'biomodel_agent').is_dir() and _SDK_SRC not in sys.path:
        sys.path.insert(0, _SDK_SRC)
    from biomodel_agent.storage import canonical_bytes as _sdk_canonical_bytes
except Exception:
    _sdk_canonical_bytes = None

CANON_CODEC_SDK = 'biomodel_agent.storage.canonical_bytes'
CANON_CODEC_STDLIB = 'stdlib_json_sorted_compact_ensure_ascii_false_utf8'
CANON_CODEC = CANON_CODEC_SDK if _sdk_canonical_bytes is not None else CANON_CODEC_STDLIB
CATALOG_SHA256_BASIS = ('sha256 over the canonical JSON bytes of the parsed catalog object, '
                        'produced by catalog_sha256_codec; recompute with the same codec on the '
                        'same parsed object to verify (raw catalog file bytes are not involved)')


class CatalogError(ValueError):
    """Top-level catalog problem: malformed structure, duplicate case_id or duplicate MM PMCID."""


class CaseXmlError(Exception):
    """A cached full-text XML file violates the supported input contract."""

    def __init__(self, code, detail=''):
        super().__init__(code + (': ' + detail if detail else ''))
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _canonical_bytes(value):
    if _sdk_canonical_bytes is not None:
        produced = _sdk_canonical_bytes(value)
        if isinstance(produced, str):
            return produced.encode('utf-8')
        return bytes(produced)
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def normalize_pmcid(value):
    """Normalise '123', 'PMC123' or 'pmc123' to 'PMC123'; return None if unsupported."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if token[:3].upper() == 'PMC':
        token = token[3:]
    if not PMC_INT_RE.fullmatch(token):
        return None
    return 'PMC' + token


def _local(node):
    tag = node.tag
    if not isinstance(tag, str):
        return None
    return tag.split('}', 1)[1] if tag.startswith('{') else tag


def _norm_text(node):
    """Documented text normalisation: itertext concatenation, whitespace-collapsed."""
    return ' '.join(''.join(node.itertext()).split())


def _descendants(node, local_name):
    return [n for n in node.iter() if _local(n) == local_name]


def _title_key(value):
    return ' '.join(value.split()).casefold() if isinstance(value, str) else None


# --------------------------------------------------------------------------
# XML loading and single-pass scan
# --------------------------------------------------------------------------
def _load_xml(raw):
    if len(raw) > MAX_FULLTEXT_BYTES:
        raise CaseXmlError('fulltext_byte_budget_exceeded',
                           '%d bytes exceed the %d byte budget' % (len(raw), MAX_FULLTEXT_BYTES))
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise CaseXmlError('invalid_utf8', str(exc))
    if '\x00' in text:
        raise CaseXmlError('invalid_xml', 'NUL byte in document')
    if DTD_ENTITY_RE.search(text):
        raise CaseXmlError('disallowed_dtd_or_entity',
                           'ENTITY declarations and internal DTD subsets are unsupported; '
                           'a plain external DOCTYPE declaration is fine')
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise CaseXmlError('invalid_xml', str(exc))


def _scan_document(root):
    """One bounded pass: budgets, counts, intro sections, paragraphs, refs."""
    counts = {key: 0 for key in ('inline_formula', 'disp_formula', 'mathml_element',
                                 'table_element', 'table_wrap', 'figure', 'section',
                                 'paragraph', 'xref_bibr', 'ref_list', 'reference', 'abstract')}
    state = {
        'nodes': 0, 'max_depth': 0, 'counts': counts, 'front': None,
        'section_titles': [], 'section_titles_truncated': False,
        'intro_sections': [], 'paragraphs': [], 'refs': [],
    }

    def descendants_with_paths(node, base):
        out = []
        stack = [(node, base, base.count('/') + 1)]
        while stack:
            element, element_path, depth = stack.pop()
            if depth > MAX_XML_DEPTH:
                raise CaseXmlError('xml_tree_budget_exceeded',
                                   'intro paragraph exceeds the XML depth budget')
            if element is not node:
                out.append((element, element_path))
            child_counts = {}
            children = []
            for child in element:
                child_local = _local(child)
                if child_local is None:
                    continue
                child_counts[child_local] = child_counts.get(child_local, 0) + 1
                child_path = element_path + '/' + child_local + '[' + str(child_counts[child_local]) + ']'
                children.append((child, child_path, depth + 1))
                if len(out) + len(stack) + len(children) > MAX_XML_NODES:
                    raise CaseXmlError('xml_tree_budget_exceeded',
                                       'intro paragraph exceeds the XML node budget')
            stack.extend(reversed(children))
        return out

    def walk(node, path, depth, in_body, in_intro, in_reflist):
        local = _local(node)
        if local is None:
            return
        state['nodes'] += 1
        if state['nodes'] > MAX_XML_NODES:
            raise CaseXmlError('xml_tree_budget_exceeded', 'more than %d XML nodes' % MAX_XML_NODES)
        if depth > MAX_XML_DEPTH:
            raise CaseXmlError('xml_tree_budget_exceeded',
                               'deeper than %d element levels (root = 1)' % MAX_XML_DEPTH)
        if depth > state['max_depth']:
            state['max_depth'] = depth
        if node.tag.startswith(XINCLUDE_NS):
            raise CaseXmlError('xinclude_rejected', 'XInclude element at ' + path)
        count_key = COUNT_KEYS.get(local)
        if count_key is not None:
            counts[count_key] += 1
        if local == 'math':
            counts['mathml_element'] += 1
        elif local == 'xref' and (node.get('ref-type') or '').strip().casefold() == 'bibr':
            counts['xref_bibr'] += 1
        elif local == 'ref-list':
            counts['ref_list'] += 1
        elif local == 'ref' and in_reflist:
            counts['reference'] += 1
            state['refs'].append({'element': node, 'xml_path': path})
        if local == 'front' and state['front'] is None:
            state['front'] = node
        next_intro = in_intro
        if local == 'sec' and in_body:
            title_element = None
            for child in node:
                if _local(child) == 'title':
                    title_element = child
                    break
            section_title = _norm_text(title_element) if title_element is not None else ''
            if len(state['section_titles']) < MAX_SECTION_TITLES:
                state['section_titles'].append({'xml_path': path, 'title': section_title or None})
            else:
                state['section_titles_truncated'] = True
            sec_type = (node.get('sec-type') or '').strip().casefold()
            if not in_intro and (sec_type in ('intro', 'introduction', 'background')
                                 or section_title.casefold() in ('introduction', 'background')):
                next_intro = True
                state['intro_sections'].append({'xml_path': path, 'sec_type': node.get('sec-type'),
                                                'title': section_title or None})
        if local == 'p' and in_intro:
            text = _norm_text(node)
            content_flags = []
            for descendant in node.iter():
                descendant_local = _local(descendant)
                if descendant_local in ('inline-formula', 'disp-formula') and 'formula' not in content_flags:
                    content_flags.append('formula')
                if descendant_local == 'math' and 'mathml' not in content_flags:
                    content_flags.append('mathml')
                if descendant_local == 'table' and 'table' not in content_flags:
                    content_flags.append('table')
            citations = []
            for element, element_path in descendants_with_paths(node, path):
                if _local(element) == 'xref' and (element.get('ref-type') or '').strip().casefold() == 'bibr':
                    citations.append({'xref_xml_path': element_path, 'rid_raw': element.get('rid'),
                                      'rids': [], 'resolutions': []})
            state['paragraphs'].append({
                'index': len(state['paragraphs']), 'xml_id': node.get('id'), 'xml_path': path,
                'text': text, 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'content_flags': content_flags, 'citations': citations,
            })
        child_in_body = in_body or local == 'body'
        child_counts = {}
        for child in node:
            child_local = _local(child)
            if child_local is None:
                continue
            child_counts[child_local] = child_counts.get(child_local, 0) + 1
            walk(child, path + '/' + child_local + '[' + str(child_counts[child_local]) + ']',
                 depth + 1, child_in_body, next_intro, in_reflist or local == 'ref-list')

    root_local = _local(root)
    walk(root, (root_local or 'root') + '[1]', 1, False, False, False)
    return state


def _linked_identifier(node):
    kind = (node.get('ext-link-type') or '').strip().casefold()
    if kind not in ('doi', 'pmid', 'pmcid'):
        return None
    raw = node.get('{http://www.w3.org/1999/xlink}href') or node.get('href')
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if '://' in value:
        try:
            url = urlsplit(value)
        except ValueError:
            return None
        hosts = {'doi': {'doi.org', 'dx.doi.org'},
                 'pmid': {'pubmed.ncbi.nlm.nih.gov'},
                 'pmcid': {'pmc.ncbi.nlm.nih.gov', 'www.ncbi.nlm.nih.gov'}}
        if (url.scheme not in ('http', 'https') or url.hostname not in hosts[kind]
                or url.username or url.password or url.query or url.fragment):
            return None
        value = url.path.strip('/')
        if kind == 'pmcid':
            value = re.sub(r'^(?:pmc/)?articles/', '', value)
    valid = (re.fullmatch(r'10\.\d{4,9}/\S+', value, re.I) if kind == 'doi'
             else re.fullmatch(r'[1-9][0-9]{0,11}', value) if kind == 'pmid'
             else normalize_pmcid(value))
    if not valid:
        return None
    return {'type': kind, 'value': value, 'source': 'ext-link', 'href': raw}


def _finalize_references(scan):
    """Build reference entries, triage flags and intro citation contexts."""
    references = []
    by_id = {}
    for position, item in enumerate(scan['refs']):
        element = item['element']
        ref_id = element.get('id')
        entry = {
            'ref_index': position, 'ref_id': ref_id, 'xml_path': item['xml_path'],
            'label': None, 'article_title': None, 'citation_text': _norm_text(element),
            'pub_ids': [], 'doi': None, 'pmid': None, 'pmcid': None,
            'identifier_conflicts': {},
            'modeling_term_hits': [], 'organism_disease_model_term_hits': [],
            'triage_flags': [], 'experimental_eligibility': 'pending_review',
            'intro_citation_contexts': [],
        }
        for descendant in element.iter():
            descendant_local = _local(descendant)
            if descendant_local == 'label' and entry['label'] is None:
                entry['label'] = _norm_text(descendant) or None
            elif descendant_local == 'article-title' and entry['article_title'] is None:
                entry['article_title'] = _norm_text(descendant) or None
            elif descendant_local == 'pub-id':
                pub_type = (descendant.get('pub-id-type') or '').strip().casefold()
                pub_value = _norm_text(descendant)
                if pub_value:
                    entry['pub_ids'].append({'type': pub_type or None, 'value': pub_value})
            elif descendant_local == 'ext-link':
                linked = _linked_identifier(descendant)
                if linked:
                    entry['pub_ids'].append(linked)
        for published in entry['pub_ids']:
            if entry['doi'] is None and published['type'] == 'doi':
                entry['doi'] = published['value']
            elif entry['pmid'] is None and published['type'] == 'pmid':
                entry['pmid'] = published['value']
            elif entry['pmcid'] is None and published['type'] == 'pmcid':
                entry['pmcid'] = normalize_pmcid(published['value'])
        for kind in ('doi', 'pmid', 'pmcid'):
            variants = {}
            for published in entry['pub_ids']:
                if published['type'] != kind:
                    continue
                value = published['value'].strip()
                key = value.casefold()
                if kind == 'doi':
                    key = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', key)
                elif kind == 'pmcid':
                    key = normalize_pmcid(value) or key
                variants.setdefault(key, value)
            if len(variants) > 1:
                entry['identifier_conflicts'][kind] = list(variants.values())
                entry[kind] = None
                entry['triage_flags'].append('conflicting_' + kind + '_identifiers')
        title_low = (entry['article_title'] or '').casefold()
        entry['modeling_term_hits'] = [term for term in MATH_MODELING_TERMS if term in title_low]
        entry['organism_disease_model_term_hits'] = [term for term in ORGANISM_DISEASE_MODEL_TERMS
                                                     if term in title_low]
        if entry['modeling_term_hits']:
            entry['triage_flags'].append('title_math_modeling_term_triage_only')
        elif re.search(r'\bmodel', title_low):
            entry['triage_flags'].append('title_model_word_unclassified_triage_only')
        if entry['organism_disease_model_term_hits']:
            entry['triage_flags'].append('title_organism_or_disease_model_not_math_evidence')
        references.append(entry)
        if isinstance(ref_id, str) and ref_id:
            by_id.setdefault(ref_id, []).append(entry)
    duplicate_ids = sorted(ref_id for ref_id, group in by_id.items() if len(group) > 1)

    def unresolved_record(citation, paragraph, rid, reason):
        return {'rid': rid, 'reason': reason, 'paragraph_index': paragraph['index'],
                'paragraph_xml_id': paragraph['xml_id'],
                'paragraph_xml_path': paragraph['xml_path'],
                'paragraph_text_sha256': paragraph['text_sha256'],
                'xref_xml_path': citation['xref_xml_path']}

    unresolved = []
    for paragraph in scan['paragraphs']:
        for citation in paragraph['citations']:
            rid_raw = citation['rid_raw']
            rids = rid_raw.split() if isinstance(rid_raw, str) else []
            citation['rids'] = rids
            if not rids:
                citation['resolutions'].append({'rid': None, 'ref_id': None, 'ref_index': None})
                unresolved.append(unresolved_record(citation, paragraph, None,
                                                    'bibr xref without a usable rid attribute'))
                continue
            for rid in rids:
                matches = by_id.get(rid) or []
                if matches:
                    for match in matches:
                        match['intro_citation_contexts'].append({
                            'rid': rid, 'paragraph_index': paragraph['index'],
                            'paragraph_xml_id': paragraph['xml_id'],
                            'paragraph_xml_path': paragraph['xml_path'],
                            'paragraph_text_sha256': paragraph['text_sha256'],
                            'xref_xml_path': citation['xref_xml_path'],
                        })
                        citation['resolutions'].append({'rid': rid, 'ref_id': match['ref_id'],
                                                        'ref_index': match['ref_index']})
                else:
                    citation['resolutions'].append({'rid': rid, 'ref_id': None, 'ref_index': None})
                    unresolved.append(unresolved_record(citation, paragraph, rid,
                                                        'rid does not match any reference id in the full reference list'))
    return references, unresolved, duplicate_ids


def _front_metadata(front):
    if front is None:
        return {'front_found': False, 'title': None, 'pmcid_raw': None, 'pmid_raw': None,
                'doi_raw': None, 'article_ids': {}, 'abstracts': [], 'license': None,
                'multiple_distinct_pmcids': False, 'multiple_distinct_dois': False}
    article_ids = {}
    for element in _descendants(front, 'article-id'):
        id_type = (element.get('pub-id-type') or '').strip().casefold()
        value = _norm_text(element)
        if id_type and value:
            article_ids.setdefault(id_type, []).append(value)

    def first(kind):
        values = article_ids.get(kind) or []
        return values[0] if values else None

    distinct_pmcs = {normalize_pmcid(value) for value in article_ids.get('pmcid', [])}
    distinct_pmcs.discard(None)
    distinct_dois = {value.strip().casefold() for value in article_ids.get('doi', [])}
    title_elements = _descendants(front, 'article-title')
    license_element = next(iter(_descendants(front, 'license')), None)
    license_info = None
    if license_element is not None:
        license_ref = next(iter(_descendants(license_element, 'license_ref')), None)
        license_text = next(iter(_descendants(license_element, 'license-p')), None)
        license_info = {
            'license_type': license_element.get('license-type'),
            'license_ref': (_norm_text(license_ref) or None) if license_ref is not None else None,
            'description': (_norm_text(license_text) or None) if license_text is not None else None,
        }
    return {
        'front_found': True,
        'title': (_norm_text(title_elements[0]) or None) if title_elements else None,
        'pmcid_raw': first('pmcid'), 'pmid_raw': first('pmid'), 'doi_raw': first('doi'),
        'article_ids': article_ids,
        'abstracts': [{'abstract_type': element.get('abstract-type'), 'text': _norm_text(element)}
                      for element in _descendants(front, 'abstract')],
        'license': license_info,
        'multiple_distinct_pmcids': len(distinct_pmcs) > 1,
        'multiple_distinct_dois': len(distinct_dois) > 1,
    }


# --------------------------------------------------------------------------
# Per-case indexing
# --------------------------------------------------------------------------
def _identity_candidates(case):
    """Collect declared identity fields from the case and its metadata/search_receipt."""
    pmcid_candidates = [('mm_pmcid', case.get('mm_pmcid'))]
    doi_candidates = []
    title_candidates = []
    shape_notes = []
    for key in ('doi', 'title'):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            (doi_candidates if key == 'doi' else title_candidates).append((key, value.strip()))
    for holder in ('metadata', 'search_receipt'):
        sub = case.get(holder)
        if sub is None:
            continue
        if not isinstance(sub, dict):
            shape_notes.append(holder + '_not_a_dict_ignored_for_identity')
            continue
        value = sub.get('pmcid')
        if value is not None:
            pmcid_candidates.append((holder + '.pmcid', value))
        value = sub.get('doi')
        if isinstance(value, str) and value.strip():
            doi_candidates.append((holder + '.doi', value.strip()))
        value = sub.get('title')
        if isinstance(value, str) and value.strip():
            title_candidates.append((holder + '.title', value.strip()))
    return pmcid_candidates, doi_candidates, title_candidates, shape_notes


def _read_fulltext(path):
    if path.is_symlink():
        raise CaseXmlError('fulltext_not_regular_file',
                           'cached full text must be a regular file, not a symlink: ' + str(path))
    if not path.exists():
        raise CaseXmlError('missing_fulltext', 'expected frozen cache file is absent: ' + str(path))
    if not path.is_file():
        raise CaseXmlError('fulltext_not_regular_file',
                           'cached full text is not a regular file: ' + str(path))
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CaseXmlError('fulltext_unreadable', str(exc))
    if size > MAX_FULLTEXT_BYTES:
        raise CaseXmlError('fulltext_byte_budget_exceeded',
                           'file has %d bytes, budget is %d' % (size, MAX_FULLTEXT_BYTES))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CaseXmlError('fulltext_unreadable', str(exc))
    if len(raw) > MAX_FULLTEXT_BYTES:
        raise CaseXmlError('fulltext_byte_budget_exceeded',
                           'file grew to %d bytes, budget is %d' % (len(raw), MAX_FULLTEXT_BYTES))
    return raw


def _index_case(position, case, cache_root):
    row = {
        'input_index': position, 'case_id': None, 'domain': None,
        'mm_pmcid': None, 'mm_pmcid_declared': None,
        'status': None, 'status_reason': [], 'error': None, 'review_flags': [],
        'case_declared': case, 'source': None, 'identity': None, 'jats': None,
        'structure': None, 'intro': None, 'references': None,
        'extraction_withheld_reason': None,
    }
    if not isinstance(case, dict):
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'invalid_case_structure', 'detail': 'case is not a JSON object'}
        return row
    case_id = case.get('case_id')
    if isinstance(case_id, str) and CASE_ID_RE.fullmatch(case_id):
        row['case_id'] = case_id
    else:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'invalid_case_id',
                        'detail': 'case_id must be a safe short id matching [A-Za-z0-9][A-Za-z0-9_.-]{0,63}'}
        return row
    domain = case.get('domain')
    if isinstance(domain, str) and domain.strip() and len(domain) <= 256:
        row['domain'] = domain.strip()
    else:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'invalid_domain',
                        'detail': 'domain must be a non-empty string of at most 256 characters'}
        return row
    declared_pmcid = case.get('mm_pmcid')
    row['mm_pmcid_declared'] = declared_pmcid
    normalized_pmcid = normalize_pmcid(declared_pmcid)
    if normalized_pmcid is None:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'invalid_mm_pmcid',
                        'detail': 'mm_pmcid must be digits or a PMC prefix plus a positive integer '
                                  '(PMC[1-9][0-9]{0,11} after normalisation)'}
        return row
    row['mm_pmcid'] = normalized_pmcid

    pmcid_candidates, doi_candidates, title_candidates, shape_notes = _identity_candidates(case)
    row['review_flags'].extend(shape_notes)
    resolved_pmcs = []
    for field, value in pmcid_candidates:
        candidate = normalize_pmcid(value)
        if candidate is None:
            if field != 'mm_pmcid':
                row['review_flags'].append('unparseable_candidate_pmcid:' + field)
            continue
        resolved_pmcs.append((field, value, candidate))
    if len({candidate for _, _, candidate in resolved_pmcs}) > 1:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'input_identity_conflict',
                        'detail': 'distinct normalised PMCID candidates inside the case: '
                                  + ', '.join('%s from %s' % (candidate, field)
                                              for field, _, candidate in resolved_pmcs)}
        return row
    doi_by_key = {}
    for field, value in doi_candidates:
        doi_by_key.setdefault(value.casefold(), (field, value))
    if len(doi_by_key) > 1:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'input_identity_conflict',
                        'detail': 'distinct DOI candidates inside the case: '
                                  + ', '.join('%s from %s' % (value, field)
                                              for field, value in doi_by_key.values())}
        return row
    case_doi = None
    case_doi_source = None
    if doi_by_key:
        case_doi_source, case_doi = next(iter(doi_by_key.values()))

    identity = {
        'case_pmcid': normalized_pmcid,
        'case_pmcid_candidates': [{'field': field, 'declared': value, 'normalized': candidate}
                                  for field, value, candidate in resolved_pmcs],
        'case_doi': case_doi, 'case_doi_source': case_doi_source,
        'case_titles': [{'value': value, 'source_field': field} for field, value in title_candidates],
        'jats_pmcid': None, 'jats_pmcid_raw': None, 'jats_pmid_raw': None,
        'jats_doi': None, 'jats_title': None, 'title_match': None,
        'identity_mismatch': False, 'mismatch_fields': [], 'verification': 'unverified',
    }
    row['identity'] = identity

    fulltext_path = cache_root / FULLTEXT_DIRNAME / (normalized_pmcid + '.xml')
    try:
        raw = _read_fulltext(fulltext_path)
    except CaseXmlError as exc:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': exc.code, 'detail': exc.detail}
        if exc.code == 'missing_fulltext':
            row['source'] = {'path': str(fulltext_path), 'bytes': None, 'sha256': None}
        return row
    row['source'] = {'path': str(fulltext_path), 'bytes': len(raw),
                     'sha256': hashlib.sha256(raw).hexdigest()}
    try:
        root = _load_xml(raw)
        scan = _scan_document(root)
        references, unresolved, duplicate_ref_ids = _finalize_references(scan)
    except CaseXmlError as exc:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': exc.code, 'detail': exc.detail}
        return row

    meta = _front_metadata(scan['front'])
    row['jats'] = {'front_found': meta['front_found'], 'title': meta['title'],
                   'article_ids': meta['article_ids'], 'abstracts': meta['abstracts'],
                   'license': meta['license']}
    identity['jats_pmcid_raw'] = meta['pmcid_raw']
    identity['jats_pmcid'] = normalize_pmcid(meta['pmcid_raw'])
    identity['jats_pmid_raw'] = meta['pmid_raw']
    identity['jats_doi'] = meta['doi_raw']
    identity['jats_title'] = meta['title']
    if identity['case_titles'] and meta['title']:
        identity['title_match'] = (_title_key(identity['case_titles'][0]['value'])
                                   == _title_key(meta['title']))

    pmcid_verified = False
    if meta['pmcid_raw'] is not None:
        if identity['jats_pmcid'] is None:
            row['review_flags'].append('jats_pmcid_unparseable')
        elif identity['jats_pmcid'] != normalized_pmcid:
            identity['mismatch_fields'].append('pmcid')
        else:
            pmcid_verified = True
    else:
        row['review_flags'].append('jats_pmcid_absent')
    doi_verified = False
    if case_doi and meta['doi_raw']:
        if case_doi.strip().casefold() != meta['doi_raw'].strip().casefold():
            identity['mismatch_fields'].append('doi')
        else:
            doi_verified = True
    if meta['multiple_distinct_pmcids']:
        row['review_flags'].append('jats_multiple_distinct_pmcids')
        identity['mismatch_fields'].append('pmcid_ambiguous')
    if meta['multiple_distinct_dois']:
        row['review_flags'].append('jats_multiple_distinct_dois')
        identity['mismatch_fields'].append('doi_ambiguous')
    identity['identity_mismatch'] = bool(identity['mismatch_fields'])
    if identity['identity_mismatch']:
        identity['verification'] = 'identity_mismatch'
    elif pmcid_verified and doi_verified:
        identity['verification'] = 'pmcid_and_doi_verified'
    elif pmcid_verified:
        identity['verification'] = 'pmcid_verified'
    elif doi_verified:
        identity['verification'] = 'doi_matched_without_jats_pmcid'
    else:
        identity['verification'] = 'unverified'

    if identity['identity_mismatch']:
        row['status'] = STATUS_ERROR
        row['error'] = {'code': 'identity_mismatch',
                        'detail': 'catalog identity conflicts with the JATS fields ('
                                  + ', '.join(identity['mismatch_fields']) + '); extraction withheld'}
        row['extraction_withheld_reason'] = 'identity_mismatch'
        return row

    structure = {
        'byte_count': len(raw), 'sha256': row['source']['sha256'],
        'node_count': scan['nodes'], 'max_depth': scan['max_depth'],
        'budgets': {'max_bytes': MAX_FULLTEXT_BYTES, 'max_nodes': MAX_XML_NODES,
                    'max_depth': MAX_XML_DEPTH},
        'counts': scan['counts'],
        'section_titles': scan['section_titles'],
        'section_titles_truncated': scan['section_titles_truncated'],
        'duplicate_reference_ids': duplicate_ref_ids,
        'clue_status': 'structure counts and section titles are complexity clues for human review '
                       'only; they do not certify biological complexity, MM identity or numerical '
                       'authenticity',
        'mathml_preservation': 'MathML/formula originals stay untouched in the frozen cache XML '
                               'recorded by source path/bytes/sha256; this index stores counts and '
                               'flags only, never a transcription',
    }
    if duplicate_ref_ids:
        row['review_flags'].append('duplicate_reference_ids')
    if scan['counts']['reference'] == 0:
        row['review_flags'].append('jats_reference_list_empty_or_missing')
    row['structure'] = structure

    intro = {
        'found': bool(scan['intro_sections']),
        'sections': scan['intro_sections'],
        'paragraph_count': len(scan['paragraphs']),
        'paragraphs': scan['paragraphs'],
        'citation_context_count': sum(len(entry['intro_citation_contexts']) for entry in references),
        'unresolved_citations': unresolved,
    }
    row['intro'] = intro
    row['references'] = references

    pending_reasons = []
    if not intro['found']:
        pending_reasons.append('intro_section_not_detected')
    if identity['verification'] in ('unverified', 'doi_matched_without_jats_pmcid'):
        pending_reasons.append('identity_not_fully_verified')
    row['status_reason'] = pending_reasons
    row['status'] = STATUS_INDEXED if not pending_reasons else STATUS_PENDING
    return row


# --------------------------------------------------------------------------
# Catalog-level index
# --------------------------------------------------------------------------
def _summarize(rows, planned):
    status_counts = {STATUS_INDEXED: 0, STATUS_PENDING: 0, STATUS_ERROR: 0}
    error_codes = {}
    references_total = 0
    references_cited_in_intro = 0
    intro_paragraphs_total = 0
    citation_contexts_total = 0
    unresolved_citations_total = 0
    files_read = 0
    files_missing = 0
    identity_verified = 0
    intro_found = 0
    pending_flags = []
    for row in rows:
        status_counts[row['status']] += 1
        if row['error']:
            code = row['error']['code']
            error_codes[code] = error_codes.get(code, 0) + 1
            if code == 'missing_fulltext':
                files_missing += 1
        if row['source'] and row['source']['sha256']:
            files_read += 1
        if row['identity'] and row['identity']['verification'] in ('pmcid_and_doi_verified',
                                                                   'pmcid_verified'):
            identity_verified += 1
        if row['intro'] is not None:
            if row['intro']['found']:
                intro_found += 1
            intro_paragraphs_total += row['intro']['paragraph_count']
            citation_contexts_total += row['intro']['citation_context_count']
            unresolved_citations_total += len(row['intro']['unresolved_citations'])
        if row['references'] is not None:
            references_total += len(row['references'])
            references_cited_in_intro += sum(1 for entry in row['references']
                                             if entry['intro_citation_contexts'])
            pending_flags.extend(entry['experimental_eligibility'] == 'pending_review'
                                 for entry in row['references'])
    return {
        'planned_cases': planned, 'status_counts': status_counts,
        'indexed': status_counts[STATUS_INDEXED],
        'pending_review': status_counts[STATUS_PENDING],
        'error_rows': status_counts[STATUS_ERROR],
        'error_code_counts': error_codes,
        'cache_files_read': files_read, 'cache_files_missing': files_missing,
        'cases_identity_verified': identity_verified, 'cases_intro_found': intro_found,
        'references_total': references_total,
        'references_cited_in_intro': references_cited_in_intro,
        'references_all_pending_review': None if references_total == 0 else all(pending_flags),
        'intro_paragraphs_total': intro_paragraphs_total,
        'intro_citation_contexts_total': citation_contexts_total,
        'unresolved_intro_citations_total': unresolved_citations_total,
    }


def index_catalog(catalog, cache_root, *, catalog_bytes=None):
    """Index a catalog against a read-only full-text cache; return one auditable dict."""
    if not isinstance(catalog, dict):
        raise CatalogError('catalog must be a JSON object')
    if type(catalog.get('schema_version')) is not int or catalog['schema_version'] != SCHEMA_VERSION:
        raise CatalogError('catalog.schema_version must be the integer %d' % SCHEMA_VERSION)
    if catalog.get('target_mm_cases') != TARGET_MM_CASES:
        raise CatalogError('catalog.target_mm_cases must be %d' % TARGET_MM_CASES)
    cases = catalog.get('cases')
    if not isinstance(cases, list) or not cases:
        raise CatalogError('catalog.cases must be a non-empty list')
    cache_root = Path(cache_root)

    seen_case_ids = {}
    seen_pmcs = {}
    for position, case in enumerate(cases):
        case_id = case.get('case_id') if isinstance(case, dict) else None
        if isinstance(case_id, str) and case_id:
            if case_id in seen_case_ids:
                raise CatalogError('duplicate case_id %r at input positions %d and %d'
                                   % (case_id, seen_case_ids[case_id], position))
            seen_case_ids[case_id] = position
        normalized = normalize_pmcid(case.get('mm_pmcid')) if isinstance(case, dict) else None
        if normalized is not None:
            if normalized in seen_pmcs:
                raise CatalogError('duplicate MM identity %s: case_id %r (position %d) and case_id '
                                   '%r (position %d) point at the same MM paper; one paper cannot '
                                   'fill multiple case rows'
                                   % (normalized, seen_pmcs[normalized][1], seen_pmcs[normalized][0],
                                      case_id, position))
            seen_pmcs[normalized] = (position, case_id)

    rows = [_index_case(position, case, cache_root) for position, case in enumerate(cases)]
    summary = _summarize(rows, len(cases))
    notices = [
        'Offline read-only file index: no network, no LLM, no experiment execution; the cache is '
        'left byte-identical.',
        'Every reference keeps experimental_eligibility=pending_review; title term hits are '
        'triage_flags only and animal/disease model wording is never treated as '
        'mathematical-model evidence.',
        'Citations, abstracts and structure counts are candidates and review clues; Expt full-text '
        'methods/results evidence is a later, separate step.',
        'curator_only output: task_b_packet_ready=false and scientific_evaluation_executed=false; '
        'this index must not be used as a task-B input packet.',
        'catalog_sha256 convention: ' + CATALOG_SHA256_BASIS,
    ]
    if len(cases) != TARGET_MM_CASES:
        notices.append('planned_cases (%d) differs from target_mm_cases (%d); input rows are never '
                       'dropped or padded.' % (len(cases), TARGET_MM_CASES))
    return {
        'artifact': 'mm_expt_citation_index',
        'schema_version': SCHEMA_VERSION,
        'index_version': INDEX_VERSION,
        'catalog_sha256': hashlib.sha256(_canonical_bytes(catalog)).hexdigest(),
        'catalog_sha256_codec': CANON_CODEC,
        'catalog_sha256_basis': CATALOG_SHA256_BASIS,
        'catalog_file_bytes_sha256': hashlib.sha256(catalog_bytes).hexdigest()
                                     if isinstance(catalog_bytes, (bytes, bytearray)) else None,
        'target_mm_cases': TARGET_MM_CASES,
        'planned_cases': len(cases),
        'cache_root': str(cache_root),
        'cache_root_present': cache_root.exists(),
        'cache_layout': {'fulltext_dir': FULLTEXT_DIRNAME, 'filename': '<mm_pmcid>.xml',
                         'read_only': True,
                         'only_path_read_per_case': '<cache_root>/fulltext/<mm_pmcid>.xml'},
        'offline': {'network': 'not_used', 'llm': 'not_used', 'experiment_execution': 'not_run',
                    'cache_mutated': False},
        'case_row_semantics': {
            'input_index': '0-based position of the case in catalog.cases; every input case keeps '
                           'exactly one row',
            'case_declared': 'the input case object echoed unchanged for traceability',
            'status_values': {
                STATUS_INDEXED: 'parsed, identity verified (JATS PMCID) and intro section found',
                STATUS_PENDING: 'parsed and fully retained but a curator must resolve the listed '
                                'status_reason items',
                STATUS_ERROR: 'hard problem recorded in error.code; nothing is silently dropped',
            },
            'extraction_withheld_reason': 'set when, e.g., identity_mismatch blocks using the file '
                                          'for this case',
        },
        'cases': rows,
        'summary': summary,
        'catalog_top_level_extra_keys': sorted(str(key) for key in catalog
                                               if key not in ('schema_version', 'target_mm_cases',
                                                              'cases')),
        'curator_only': True,
        'task_b_packet_ready': False,
        'scientific_evaluation_executed': False,
        'constraints': {
            'curator_only': True,
            'task_b_packet_ready': False,
            'scientific_evaluation_executed': False,
            'experimental_eligibility_default': 'pending_review',
            'structure_counts': 'complexity clues for human review only; they certify nothing',
            'next_step': 'curation and a separate Expt-evidence implementation; this index is not '
                         'a model-building input packet',
        },
        'notices': notices,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp',
                                        dir=str(path.parent))
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _build_parser():
    parser = argparse.ArgumentParser(
        prog='mm_expt_benchmark.py',
        description='Offline JATS citation index for the MM+Expt benchmark catalog '
                    '(curator-only development tool).')
    sub = parser.add_subparsers(dest='command', metavar='COMMAND', required=True)
    index = sub.add_parser('index', help='build one auditable citation index JSON from a catalog '
                                         'and a read-only full-text cache')
    index.add_argument('--catalog', required=True,
                       help='catalog JSON file (schema_version=1, target_mm_cases=100, cases=[...])')
    index.add_argument('--cache', required=True,
                       help='cache root; reads only <cache>/fulltext/<mm_pmcid>.xml')
    index.add_argument('--out', required=True,
                       help='output JSON file (stable UTF-8 JSON, written atomically)')
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    catalog_path = Path(args.catalog)
    try:
        raw = catalog_path.read_bytes()
    except OSError as exc:
        print('mm_expt_benchmark: cannot read catalog %s: %s' % (catalog_path, exc), file=sys.stderr)
        return 2
    try:
        catalog = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        print('mm_expt_benchmark: catalog is not valid UTF-8 JSON: %s' % exc, file=sys.stderr)
        return 2
    try:
        result = index_catalog(catalog, args.cache, catalog_bytes=raw)
    except CatalogError as exc:
        print('mm_expt_benchmark: catalog error: %s' % exc, file=sys.stderr)
        return 2
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    _atomic_write(Path(args.out), payload)
    counts = result['summary']['status_counts']
    print('mm_expt_benchmark: indexed %d cases (indexed=%d pending_review=%d error=%d) -> %s'
          % (result['planned_cases'], counts[STATUS_INDEXED], counts[STATUS_PENDING],
             counts[STATUS_ERROR], args.out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
