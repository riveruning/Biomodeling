#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained synthetic tests for the offline MM/Expt citation index.

Run with either::

    python tests/test_mm_expt_benchmark.py
    python -m unittest tests.test_mm_expt_benchmark
    pytest tests/test_mm_expt_benchmark.py

Only synthetic JATS strings and temporary directories are used: no network,
no real cache files, no SDK state.  Coverage: namespaces + MathML + intro
multi-rid citation graph, external DOCTYPE accepted, mixed citation text with
missing IDs, model-word candidates stay pending_review, missing/bad XML rows
retained, DTD/entity/XInclude rejection, duplicate identity rejection, input
identity conflicts, unchanged cache bytes, and the CLI entry point.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / 'benchmarks' / 'development' / 'mm_expt_benchmark.py'
_spec = importlib.util.spec_from_file_location('mm_expt_benchmark_under_test', str(_MODULE_PATH))
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


# --------------------------------------------------------------------------
# Synthetic JATS fixtures
# --------------------------------------------------------------------------
JATS_MAIN = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving DTD v1.2//EN" "JATS-journalarchiving.dtd">
<article xmlns:xlink="http://www.w3.org/1999/xlink" xml:lang="en" article-type="research-article">
  <front>
    <journal-meta>
      <journal-title-group><journal-title>Journal of Theoretical Biology</journal-title></journal-title-group>
    </journal-meta>
    <article-meta>
      <article-id pub-id-type="pmcid">PMC1234567</article-id>
      <article-id pub-id-type="pmid">30000001</article-id>
      <article-id pub-id-type="doi">10.1000/jtb.2024.001</article-id>
      <title-group><article-title>A predator-prey model with seasonal forcing</article-title></title-group>
      <abstract><p>We model seasonal population dynamics with differential equations.</p></abstract>
      <license license-type="open-access"><license-p>Creative Commons Attribution 4.0</license-p></license>
    </article-meta>
  </front>
  <body>
    <sec id="s1" sec-type="intro">
      <title>Introduction</title>
      <p id="p1">Classical work <xref ref-type="bibr" rid="bib1 bib2"/> established the basis; see also <xref ref-type="bibr" rid="bib3"/> and <xref ref-type="bibr" rid="bib-unknown"/>, while <xref ref-type="fig" rid="fig1"/> shows the setup.</p>
      <p id="p2">Recent approaches <xref ref-type="bibr" rid="bib2"/> track <inline-formula><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>+</mo><mn>1</mn></math></inline-formula> over time.</p>
    </sec>
    <sec id="s2"><title>Methods</title><p>Details of the field survey.</p></sec>
  </body>
  <back>
    <ref-list>
      <ref id="bib1"><element-citation><article-title>A mathematical model of predation</article-title><pub-id pub-id-type="doi">10.1000/ref.1</pub-id><pub-id pub-id-type="pmcid">PMC9990001</pub-id></element-citation></ref>
      <ref id="bib2"><mixed-citation>Field census of hares, <article-title>Population census 1990</article-title>, Journal of Field Ecology.</mixed-citation></ref>
      <ref id="bib3"><element-citation><article-title>An animal model of sepsis in mice</article-title><pub-id pub-id-type="pmid">20000002</pub-id></element-citation></ref>
      <ref id="bib4"><element-citation><article-title>Model behaviour of crowding in pens</article-title></element-citation></ref>
      <ref><mixed-citation>Anonymous 1999. A note mentioning DOI 10.1098/notreal.123 inside plain text only.</mixed-citation></ref>
    </ref-list>
  </back>
</article>
'''

JATS_NO_INTRO = '''<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <article-id pub-id-type="pmcid">PMC7654321</article-id>
      <title-group><article-title>A field survey without an introduction section</article-title></title-group>
    </article-meta>
  </front>
  <body>
    <sec id="s1"><title>Methods</title><p>Transect counts only.</p></sec>
  </body>
  <back><ref-list><ref id="r1"><mixed-citation>Survey 2001.</mixed-citation></ref></ref-list></back>
</article>
'''

JATS_NO_JATS_PMCID = '''<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <article-id pub-id-type="doi">10.1000/only.doi</article-id>
      <title-group><article-title>Only a DOI is printed here</article-title></title-group>
    </article-meta>
  </front>
  <body>
    <sec id="s1" sec-type="intro"><title>Introduction</title><p>Prior surveys <xref ref-type="bibr" rid="r1"/> exist.</p></sec>
  </body>
  <back><ref-list><ref id="r1"><mixed-citation>Survey 2005.</mixed-citation></ref></ref-list></back>
</article>
'''

MINIMAL_EXTERNAL_DOCTYPE = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                            '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS//EN" "https://example.test/jats.dtd">\n'
                            '<article><front><article-meta>'
                            '<article-id pub-id-type="pmcid">PMC77700001</article-id>'
                            '</article-meta></front><body>'
                            '<sec sec-type="intro"><title>Introduction</title><p>No citations here.</p></sec>'
                            '</body></article>')


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _catalog(*cases):
    return {'schema_version': 1, 'target_mm_cases': 100, 'cases': list(cases)}


def _case(case_id, pmcid, **extra):
    case = {'case_id': case_id, 'domain': extra.pop('domain', 'ecology'), 'mm_pmcid': pmcid}
    case.update(extra)
    return case


def _write_cache(root, pmcid, xml_text):
    directory = Path(root) / 'fulltext'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (pmcid + '.xml')).write_text(xml_text, encoding='utf-8', newline='')


class IndexTestBase(unittest.TestCase):
    """Creates a temporary cache directory from {pmcid: xml or None}."""

    def make_env(self, catalog, files):
        tmp = tempfile.mkdtemp(prefix='mmexpt-')
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = Path(tmp) / 'cache'
        for pmcid, xml_text in files.items():
            if xml_text is not None:
                _write_cache(cache, pmcid, xml_text)
        return tmp, cache, mm.index_catalog(catalog, str(cache))


class TestCitationIndex(IndexTestBase):

    def test_conflicting_citation_alternatives_preserve_both_identifiers(self):
        xml = JATS_MAIN.replace(
            '<pub-id pub-id-type="doi">10.1000/ref.1</pub-id>',
            '<pub-id pub-id-type="doi">10.1000/ref.1</pub-id>'
            '<pub-id pub-id-type="doi">https://doi.org/10.1000/REF.1</pub-id>'
            '<pub-id pub-id-type="doi">10.1000/ref.2</pub-id>')
        _, _, result = self.make_env(_catalog(_case('conflict', 'PMC1234567')),
                                      {'PMC1234567': xml})
        reference = result['cases'][0]['references'][0]
        self.assertIsNone(reference['doi'])
        self.assertEqual(reference['identifier_conflicts'],
                         {'doi': ['10.1000/ref.1', '10.1000/ref.2']})
        self.assertIn('conflicting_doi_identifiers', reference['triage_flags'])
        self.assertEqual(len(reference['pub_ids']), 4)
        self.assertEqual(reference['experimental_eligibility'], 'pending_review')

    def test_background_and_typed_reference_links(self):
        xml = '''<article xmlns:xlink="http://www.w3.org/1999/xlink">
          <front><article-meta><article-id pub-id-type="pmcid">PMC1234567</article-id></article-meta></front>
          <body><sec><title>Background</title><p>Reported experiments
          <xref ref-type="bibr" rid="B1 B2"/>.</p>
          <sec><title>Biological context</title><p>Nested context <xref ref-type="bibr" rid="B1"/>.</p></sec>
          </sec><sec><title>References</title><ref-list>
          <ref id="B1"><mixed-citation>Experimental study.
          <ext-link ext-link-type="doi" xlink:href="10.1000/experiment"/>
          <ext-link ext-link-type="pmid" xlink:href="https://pubmed.ncbi.nlm.nih.gov/12345/"/>
          <ext-link ext-link-type="pmcid" xlink:href="https://pmc.ncbi.nlm.nih.gov/articles/PMC98765/"/>
          </mixed-citation></ref>
          <ref id="B2"><mixed-citation>Unstructured DOI 10.1000/never-inferred.
          <ext-link ext-link-type="doi" xlink:href="https://example.test/10.1000/untrusted"/>
          <ext-link ext-link-type="pmid" xlink:href="not-a-pmid"/>
          <ext-link ext-link-type="google-scholar" xlink:href="doi=10.1000/query"/>
          </mixed-citation></ref>
          </ref-list></sec></body></article>'''
        _, _, result = self.make_env(_catalog(_case('background', 'PMC1234567')),
                                      {'PMC1234567': xml})
        row = result['cases'][0]
        self.assertEqual(row['status'], 'indexed')
        self.assertEqual(row['intro']['paragraph_count'], 2)
        first, second = row['references']
        self.assertEqual((first['doi'], first['pmid'], first['pmcid']),
                         ('10.1000/experiment', '12345', 'PMC98765'))
        self.assertEqual(len(first['intro_citation_contexts']), 2)
        self.assertTrue(all(p['source'] == 'ext-link' for p in first['pub_ids']))
        self.assertEqual(second['pub_ids'], [])
        self.assertIsNone(second['doi'])
        self.assertTrue(all(r['experimental_eligibility'] == 'pending_review'
                            for r in row['references']))

    # -- namespaces + MathML + intro multi-rid citation graph --------------
    def test_namespace_mathml_intro_multi_rid_citation_graph(self):
        catalog = _catalog(_case('case-1', 'PMC1234567', doi='10.1000/jtb.2024.001',
                                 title='A predator-prey model with seasonal forcing',
                                 search_receipt={'pmcid': '1234567'}))
        _, cache, result = self.make_env(catalog, {'PMC1234567': JATS_MAIN})
        self.assertTrue(result['cache_root_present'])
        self.assertEqual(result['cache_layout']['read_only'], True)
        row = result['cases'][0]
        self.assertEqual(row['input_index'], 0)
        self.assertEqual(row['status'], 'indexed')
        self.assertEqual(row['case_id'], 'case-1')
        self.assertEqual(row['domain'], 'ecology')
        self.assertEqual(row['mm_pmcid'], 'PMC1234567')
        self.assertEqual(row['case_declared']['case_id'], 'case-1')

        identity = row['identity']
        self.assertEqual(identity['verification'], 'pmcid_and_doi_verified')
        self.assertFalse(identity['identity_mismatch'])
        self.assertIs(identity['title_match'], True)
        self.assertEqual(identity['case_pmcid_candidates'][1]['normalized'], 'PMC1234567')

        self.assertEqual(row['source']['path'], str(cache / 'fulltext' / 'PMC1234567.xml'))
        self.assertEqual(row['source']['bytes'], len(JATS_MAIN.encode('utf-8')))
        self.assertEqual(row['source']['sha256'], hashlib.sha256(JATS_MAIN.encode('utf-8')).hexdigest())

        self.assertTrue(row['jats']['front_found'])
        self.assertEqual(row['jats']['article_ids']['pmid'], ['30000001'])
        self.assertEqual(row['jats']['license']['license_type'], 'open-access')
        self.assertTrue(row['jats']['abstracts'][0]['text'].startswith('We model seasonal'))

        refs = row['references']
        self.assertEqual([r['ref_id'] for r in refs], ['bib1', 'bib2', 'bib3', 'bib4', None])
        self.assertEqual(refs[0]['doi'], '10.1000/ref.1')
        self.assertEqual(refs[0]['pmcid'], 'PMC9990001')
        self.assertIsNone(refs[4]['doi'])
        self.assertIn('10.1098/notreal.123', refs[4]['citation_text'])
        self.assertEqual(refs[1]['article_title'], 'Population census 1990')
        self.assertTrue(all(r['experimental_eligibility'] == 'pending_review' for r in refs))

        paragraphs = row['intro']['paragraphs']
        self.assertEqual([p['xml_id'] for p in paragraphs], ['p1', 'p2'])
        p1 = paragraphs[0]
        self.assertIn('established the basis', p1['text'])
        self.assertEqual(p1['text_sha256'], hashlib.sha256(p1['text'].encode('utf-8')).hexdigest())
        self.assertEqual(len(p1['citations']), 3)
        self.assertEqual(p1['citations'][0]['rids'], ['bib1', 'bib2'])
        self.assertEqual(p1['citations'][2]['resolutions'],
                         [{'rid': 'bib-unknown', 'ref_id': None, 'ref_index': None}])
        self.assertIn('mathml', paragraphs[1]['content_flags'])
        self.assertIn('formula', paragraphs[1]['content_flags'])

        self.assertEqual(refs[0]['intro_citation_contexts'][0]['paragraph_xml_id'], 'p1')
        self.assertEqual(len(refs[1]['intro_citation_contexts']), 2)
        self.assertEqual(len(refs[2]['intro_citation_contexts']), 1)
        self.assertEqual(refs[3]['intro_citation_contexts'], [])
        unresolved = row['intro']['unresolved_citations']
        self.assertEqual([u['rid'] for u in unresolved], ['bib-unknown'])
        self.assertEqual(unresolved[0]['paragraph_xml_id'], 'p1')
        self.assertIn('reason', unresolved[0])

        counts = row['structure']['counts']
        self.assertEqual(counts['mathml_element'], 1)
        self.assertEqual(counts['inline_formula'], 1)
        self.assertEqual(counts['xref_bibr'], 4)
        self.assertEqual(counts['reference'], 5)
        titles = [t['title'] for t in row['structure']['section_titles']]
        self.assertEqual(titles, ['Introduction', 'Methods'])
        self.assertIn('do not certify', row['structure']['clue_status'])

        summary = result['summary']
        self.assertEqual(summary['references_total'], 5)
        self.assertEqual(summary['intro_citation_contexts_total'], 4)
        self.assertEqual(summary['unresolved_intro_citations_total'], 1)
        self.assertEqual(summary['intro_paragraphs_total'], 2)
        self.assertEqual(summary['cases_identity_verified'], 1)
        self.assertIs(summary['references_all_pending_review'], True)

    # -- external DOCTYPE is accepted --------------------------------------
    def test_external_doctype_accepted(self):
        catalog = _catalog(_case('dt', 'PMC77700001'))
        _, _, result = self.make_env(catalog, {'PMC77700001': MINIMAL_EXTERNAL_DOCTYPE})
        row = result['cases'][0]
        self.assertEqual(row['status'], 'indexed')
        self.assertEqual(row['error'], None)
        self.assertIn('jats_reference_list_empty_or_missing', row['review_flags'])

    # -- pending states -----------------------------------------------------
    def test_missing_intro_or_missing_jats_pmcid_gives_pending_review(self):
        catalog = _catalog(_case('no-intro', 'PMC7654321'),
                           _case('no-jats-pmcid', 'PMC55500001', doi='10.1000/only.doi'))
        _, _, result = self.make_env(catalog, {'PMC7654321': JATS_NO_INTRO,
                                               'PMC55500001': JATS_NO_JATS_PMCID})
        rows = result['cases']
        self.assertEqual([r['status'] for r in rows], ['pending_review', 'pending_review'])
        self.assertIn('intro_section_not_detected', rows[0]['status_reason'])
        self.assertEqual(rows[0]['identity']['verification'], 'pmcid_verified')
        self.assertIn('identity_not_fully_verified', rows[1]['status_reason'])
        self.assertEqual(rows[1]['identity']['verification'], 'doi_matched_without_jats_pmcid')
        self.assertEqual(len(rows[1]['intro']['paragraphs']), 1)
        self.assertEqual(rows[1]['references'][0]['intro_citation_contexts'][0]['rid'], 'r1')

    # -- missing / broken XML rows are retained ------------------------------
    def test_missing_and_invalid_xml_rows_are_retained(self):
        catalog = _catalog(_case('good', 'PMC1234567'),
                           _case('missing', 'PMC22220002'),
                           _case('broken', 'PMC33330003'))
        _, _, result = self.make_env(catalog, {'PMC1234567': JATS_MAIN,
                                               'PMC33330003': '<article><body><p>truncated'})
        rows = result['cases']
        self.assertEqual([r['case_id'] for r in rows], ['good', 'missing', 'broken'])
        self.assertEqual([r['status'] for r in rows], ['indexed', 'error', 'error'])
        self.assertEqual([(r['error'] or {}).get('code') for r in rows],
                         [None, 'missing_fulltext', 'invalid_xml'])
        self.assertTrue(rows[1]['source']['path'].endswith('PMC22220002.xml'))
        self.assertIsNone(rows[1]['source']['sha256'])
        self.assertEqual(rows[1]['case_declared']['case_id'], 'missing')
        self.assertEqual(result['summary']['error_code_counts'],
                         {'missing_fulltext': 1, 'invalid_xml': 1})
        self.assertEqual(result['summary']['cache_files_missing'], 1)

    # -- DTD/entity/XInclude rejection ---------------------------------------
    def test_entity_internal_dtd_and_xinclude_rejected(self):
        entity_doc = '<!DOCTYPE article [<!ENTITY tweak "x">]><article><body/></article>'
        xinclude_doc = ('<article xmlns:xi="http://www.w3.org/2001/XInclude"><body>'
                        '<xi:include href="evil.xml"/></body></article>')
        frontless_doc = '<?xml version="1.0"?><article><body><p>ok</p></body></article>'
        catalog = _catalog(_case('entity', 'PMC88800001'),
                           _case('xinclude', 'PMC88800002'),
                           _case('frontless', 'PMC88800003'))
        _, _, result = self.make_env(catalog, {'PMC88800001': entity_doc,
                                               'PMC88800002': xinclude_doc,
                                               'PMC88800003': frontless_doc})
        rows = result['cases']
        self.assertEqual([r['status'] for r in rows[:2]], ['error', 'error'])
        self.assertEqual([(r['error'] or {}).get('code') for r in rows[:2]],
                         ['disallowed_dtd_or_entity', 'xinclude_rejected'])
        self.assertEqual(rows[2]['status'], 'pending_review')
        self.assertIn('jats_pmcid_absent', rows[2]['review_flags'])

    # -- budgets --------------------------------------------------------------
    def test_tree_byte_and_depth_budgets(self):
        doc = '<article><body>' + ''.join('<p>text %d</p>' % i for i in range(20)) + '</body></article>'
        original_nodes = mm.MAX_XML_NODES
        original_bytes = mm.MAX_FULLTEXT_BYTES
        original_depth = mm.MAX_XML_DEPTH
        try:
            mm.MAX_XML_NODES = 8
            _, _, result = self.make_env(_catalog(_case('budget1', 'PMC55500001')),
                                         {'PMC55500001': doc})
            row = result['cases'][0]
            self.assertEqual(row['error']['code'], 'xml_tree_budget_exceeded')
            self.assertIsNotNone(row['source']['sha256'])
        finally:
            mm.MAX_XML_NODES = original_nodes
        try:
            mm.MAX_FULLTEXT_BYTES = 16
            _, _, result = self.make_env(_catalog(_case('budget2', 'PMC55500002')),
                                         {'PMC55500002': doc})
            self.assertEqual(result['cases'][0]['error']['code'], 'fulltext_byte_budget_exceeded')
        finally:
            mm.MAX_FULLTEXT_BYTES = original_bytes
        deep = '<article><body><sec><sec><sec><p>deep</p></sec></sec></sec></body></article>'
        try:
            mm.MAX_XML_DEPTH = 3
            _, _, result = self.make_env(_catalog(_case('budget3', 'PMC55500003')),
                                         {'PMC55500003': deep})
            self.assertEqual(result['cases'][0]['error']['code'], 'xml_tree_budget_exceeded')
        finally:
            mm.MAX_XML_DEPTH = original_depth

    # -- duplicate identity is a top-level catalog error -----------------------
    def test_duplicate_case_id_and_duplicate_mm_identity_rejected(self):
        with self.assertRaises(mm.CatalogError) as raised:
            mm.index_catalog(_catalog(_case('same', 'PMC77700001'), _case('same', 'PMC77700002')),
                             'unused-cache')
        self.assertIn('duplicate case_id', str(raised.exception))
        with self.assertRaises(mm.CatalogError) as raised:
            mm.index_catalog(_catalog(_case('x1', 'PMC77700001'), _case('x2', '77700001')),
                             'unused-cache')
        message = str(raised.exception)
        self.assertIn('PMC77700001', message)
        self.assertIn('x1', message)
        self.assertIn('x2', message)

    # -- input identity conflicts stay per-row ---------------------------------
    def test_input_identity_conflicts_recorded_per_row(self):
        catalog = _catalog(
            _case('pmc-conflict', '111', search_receipt={'pmcid': '222'}),
            _case('doi-conflict', '333', doi='10.1000/x', metadata={'doi': '10.1000/Y'}))
        _, _, result = self.make_env(catalog, {})
        rows = result['cases']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['status'] == 'error' for r in rows))
        self.assertEqual([r['error']['code'] for r in rows],
                         ['input_identity_conflict', 'input_identity_conflict'])
        self.assertIsNone(rows[0]['source'])
        self.assertIn('PMC111', rows[0]['error']['detail'])
        self.assertIn('PMC222', rows[0]['error']['detail'])
        self.assertIn('10.1000/x', rows[1]['error']['detail'])
        self.assertIn('10.1000/Y', rows[1]['error']['detail'])

    # -- cache-vs-JATS identity mismatch ---------------------------------------
    def test_identity_mismatch_against_jats_fields(self):
        pmc_mismatch = ('<?xml version="1.0"?><article><front><article-meta>'
                        '<article-id pub-id-type="pmcid">PMC9999</article-id>'
                        '</article-meta></front><body>'
                        '<sec sec-type="intro"><title>Introduction</title><p>Text.</p></sec>'
                        '</body></article>')
        doi_mismatch = ('<?xml version="1.0"?><article><front><article-meta>'
                        '<article-id pub-id-type="pmcid">PMC44440002</article-id>'
                        '<article-id pub-id-type="doi">10.1000/journal.a</article-id>'
                        '</article-meta></front><body>'
                        '<sec sec-type="intro"><title>Introduction</title><p>Text.</p></sec>'
                        '</body></article>')
        catalog = _catalog(_case('m1', 'PMC44440001'),
                           _case('m2', 'PMC44440002', doi='10.1000/journal.b'))
        _, _, result = self.make_env(catalog, {'PMC44440001': pmc_mismatch,
                                               'PMC44440002': doi_mismatch})
        rows = result['cases']
        self.assertEqual([r['error']['code'] for r in rows],
                         ['identity_mismatch', 'identity_mismatch'])
        self.assertEqual(rows[0]['identity']['mismatch_fields'], ['pmcid'])
        self.assertEqual(rows[1]['identity']['mismatch_fields'], ['doi'])
        self.assertIsNotNone(rows[0]['source']['sha256'])
        self.assertIsNone(rows[0]['references'])
        self.assertEqual(rows[0]['extraction_withheld_reason'], 'identity_mismatch')
        self.assertEqual(result['summary']['error_code_counts']['identity_mismatch'], 2)
        self.assertEqual(result['summary']['cases_identity_verified'], 0)

    def test_ambiguous_front_identifiers_are_excluded_from_verified_count(self):
        for kind, conflicting in (('doi', '10.1000/conflicting'), ('pmcid', 'PMC9999999')):
            with self.subTest(kind=kind):
                extra = '<article-id pub-id-type="%s">%s</article-id>' % (kind, conflicting)
                xml = JATS_MAIN.replace('</article-meta>', extra + '</article-meta>')
                catalog = _catalog(_case('ambiguous', 'PMC1234567', doi='10.1000/jtb.2024.001'))
                _, _, result = self.make_env(catalog, {'PMC1234567': xml})
                row = result['cases'][0]
                self.assertEqual(row['error']['code'], 'identity_mismatch')
                self.assertEqual(row['identity']['verification'], 'identity_mismatch')
                self.assertEqual(result['summary']['cases_identity_verified'], 0)
                self.assertIsNone(row['references'])

    def test_deep_intro_is_an_error_row_and_later_cases_are_preserved(self):
        xml = ('<article><body><sec sec-type="intro"><p>' + '<x>' * 1100
               + 'Fixture.' + '</x>' * 1100 + '</p></sec></body></article>')
        catalog = _catalog(_case('deep-intro', 'PMC55500001'), _case('healthy', 'PMC1234567'))
        _, _, result = self.make_env(catalog, {'PMC55500001': xml, 'PMC1234567': JATS_MAIN})
        self.assertEqual([r['case_id'] for r in result['cases']], ['deep-intro', 'healthy'])
        self.assertEqual(result['cases'][0]['error']['code'], 'xml_tree_budget_exceeded')
        self.assertEqual(result['cases'][1]['status'], 'indexed')

    # -- triage flags never change eligibility ----------------------------------
    def test_triage_flags_keep_eligibility_pending(self):
        catalog = _catalog(_case('triage', 'PMC1234567'))
        _, _, result = self.make_env(catalog, {'PMC1234567': JATS_MAIN})
        refs = result['cases'][0]['references']
        by_id = {r['ref_id']: r for r in refs if r['ref_id']}
        self.assertEqual(by_id['bib1']['modeling_term_hits'], ['mathematical model'])
        self.assertIn('title_math_modeling_term_triage_only', by_id['bib1']['triage_flags'])
        self.assertEqual(by_id['bib2']['triage_flags'], [])
        self.assertEqual(by_id['bib3']['modeling_term_hits'], [])
        self.assertIn('title_organism_or_disease_model_not_math_evidence', by_id['bib3']['triage_flags'])
        self.assertIn('title_model_word_unclassified_triage_only', by_id['bib4']['triage_flags'])
        self.assertTrue(all(r['experimental_eligibility'] == 'pending_review' for r in refs))

    # -- cache bytes unchanged + deterministic output ----------------------------
    def test_cache_bytes_unchanged_and_deterministic(self):
        tmp = tempfile.mkdtemp(prefix='mmexpt-cache-')
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = Path(tmp) / 'cache'
        _write_cache(cache, 'PMC1234567', JATS_MAIN)
        _write_cache(cache, 'PMC7654321', JATS_NO_INTRO)
        catalog = _catalog(_case('a', 'PMC1234567'), _case('b', 'PMC7654321'))

        def snapshot():
            return {str(p.relative_to(cache)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(cache.rglob('*')) if p.is_file()}

        before = snapshot()
        first = mm.index_catalog(catalog, str(cache))
        middle = snapshot()
        second = mm.index_catalog(catalog, str(cache))
        after = snapshot()
        self.assertEqual(before, middle)
        self.assertEqual(before, after)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual([r['status'] for r in first['cases']], ['indexed', 'pending_review'])

    # -- normalization, digest convention, per-row invalid pmcid ------------------
    def test_pmcid_normalization_and_catalog_digest(self):
        self.assertEqual(mm.normalize_pmcid('12345'), 'PMC12345')
        self.assertEqual(mm.normalize_pmcid(' pmc12345 '), 'PMC12345')
        self.assertEqual(mm.normalize_pmcid('PMC123456789012'), 'PMC123456789012')
        self.assertIsNone(mm.normalize_pmcid('PMC0'))
        self.assertIsNone(mm.normalize_pmcid('0123'))
        self.assertIsNone(mm.normalize_pmcid('PMC1234567890123'))
        self.assertIsNone(mm.normalize_pmcid('ABC123'))
        self.assertIsNone(mm.normalize_pmcid(None))
        catalog = _catalog(_case('bad-pmcid', 'not-a-pmcid'))
        _, _, result = self.make_env(catalog, {})
        row = result['cases'][0]
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['error']['code'], 'invalid_mm_pmcid')
        self.assertEqual(row['case_declared']['case_id'], 'bad-pmcid')
        expected = hashlib.sha256(mm._canonical_bytes(catalog)).hexdigest()
        self.assertEqual(result['catalog_sha256'], expected)
        self.assertEqual(result['catalog_sha256_codec'], mm.CANON_CODEC)
        self.assertIn('canonical', result['catalog_sha256_basis'])
        self.assertEqual(result['target_mm_cases'], 100)
        self.assertEqual(result['planned_cases'], 1)

    # -- top-level catalog structure errors ---------------------------------------
    def test_top_level_catalog_errors(self):
        for broken in (
            {'schema_version': True, 'target_mm_cases': 100, 'cases': [_case('a', 'PMC1')]},
            {'schema_version': 2, 'target_mm_cases': 100, 'cases': [_case('a', 'PMC1')]},
            {'schema_version': 1, 'target_mm_cases': 99, 'cases': [_case('a', 'PMC1')]},
            {'schema_version': 1, 'target_mm_cases': 100},
            {'schema_version': 1, 'target_mm_cases': 100, 'cases': []},
            {'schema_version': 1, 'target_mm_cases': 100, 'cases': 'nope'},
            [_case('a', 'PMC1')],
            'not-an-object',
        ):
            with self.assertRaises(mm.CatalogError):
                mm.index_catalog(broken, 'unused-cache')

    # -- CLI ----------------------------------------------------------------------
    def test_cli_index_roundtrip_and_stable_utf8_output(self):
        tmp = tempfile.mkdtemp(prefix='mmexpt-cli-')
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = Path(tmp) / 'cache'
        _write_cache(cache, 'PMC1234567', JATS_MAIN)
        catalog = _catalog(_case('case-1', 'PMC1234567', domain='ecology-生态',
                                 doi='10.1000/jtb.2024.001',
                                 title='A predator-prey model with seasonal forcing'))
        catalog_path = Path(tmp) / 'catalog.json'
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        out_path = Path(tmp) / 'index.json'
        args = ['index', '--catalog', str(catalog_path), '--cache', str(cache), '--out', str(out_path)]
        self.assertEqual(mm.main(args), 0)
        raw_first = out_path.read_bytes()
        self.assertTrue(raw_first.endswith(b'\n'))
        data = json.loads(raw_first.decode('utf-8'))
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['artifact'], 'mm_expt_citation_index')
        self.assertEqual(data['target_mm_cases'], 100)
        self.assertEqual(data['planned_cases'], 1)
        self.assertEqual(len(data['cases']), 1)
        self.assertFalse(data['task_b_packet_ready'])
        self.assertFalse(data['scientific_evaluation_executed'])
        self.assertTrue(data['curator_only'])
        self.assertFalse(data['constraints']['task_b_packet_ready'])
        self.assertFalse(data['constraints']['scientific_evaluation_executed'])
        self.assertEqual(data['summary']['status_counts'],
                         {'indexed': 1, 'pending_review': 0, 'error': 0})
        self.assertEqual(len(data['catalog_sha256']), 64)
        self.assertIsNotNone(data['catalog_file_bytes_sha256'])
        self.assertIn('生态', raw_first.decode('utf-8'))
        self.assertEqual(mm.main(args), 0)
        self.assertEqual(out_path.read_bytes(), raw_first)

    def test_cli_rejects_invalid_catalog(self):
        tmp = tempfile.mkdtemp(prefix='mmexpt-cli-bad-')
        self.addCleanup(shutil.rmtree, tmp, True)
        bad = Path(tmp) / 'bad.json'
        bad.write_text('{"schema_version": 2, "target_mm_cases": 100, "cases": []}', encoding='utf-8')
        out_path = Path(tmp) / 'index.json'
        args = ['index', '--catalog', str(bad), '--cache', str(tmp), '--out', str(out_path)]
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            self.assertEqual(mm.main(args), 2)
        self.assertIn('catalog error', captured.getvalue())
        self.assertFalse(out_path.exists())
        not_json = Path(tmp) / 'not.json'
        not_json.write_text('not json', encoding='utf-8')
        args = ['index', '--catalog', str(not_json), '--cache', str(tmp), '--out', str(out_path)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mm.main(args), 2)
        self.assertFalse(out_path.exists())


if __name__ == '__main__':
    unittest.main()
