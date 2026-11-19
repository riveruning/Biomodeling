"""Production regression for the shared JATS projection (task dsh48-adopt-projection-47).

The code under test is the audited architecture-span-44 fix, adopted into
production src.  Its origin is the ISOLATED prototype
artifacts/development/dsh-execution-48h/architecture-span-44/variant (task
dsh48-projection-followup-44), where the same cases were proven against the
saved b0cf baseline.  Three defects the round-44 line audit located, on top of
the round-42 marker-projection fix that must keep working:

1. NEW index loses image formulas: background_jats._make_block still flattened
   paragraphs with _element_text, so a textless <inline-graphic>/<math>
   vanished from the indexed text while the stored page keeps the shared
   projection's [unreadable-formula:...] marker.  The index and the page must
   reuse the ONE canonical JATS text projection
   (research_literature._project_element), so a canonical index block is
   byte-identical to its stored page line.
2. Edge/multiple markers dropped from a binding: with a saved legacy
   (pre-projection) index, a paragraph whose only formula sits at an edge
   projects to "A" while the stored line is "A [marker]" (or "[marker] A");
   the old raw-exact-first match bound just "A" and silently lost the
   paragraph's unknown formula content.  The binding must cover the whole
   line's markers, and when that cannot be done reliably the block is
   reported unbound.
3. Plain/formula paragraph identity: a page holding a plain-text paragraph and
   a formula paragraph with the same marker-free text must not let the
   raw-exact-first match bind the indexed block to the OTHER paragraph.  A
   legacy index honestly reports the ambiguity with both candidate regions; a
   canonical index resolves identity through its own canonical text.

Every fixture lives in a pytest tmp_path and every store is created there, so
this file depends on no evidence cache and no machine-specific absolute path.
The real saved Gonze2013 cache regression is a separate artifact verification
(run-apply-projection-47.py stage `gonze`), NOT a regular test: it must not
make the suite depend on this machine holding that cache.
"""
import hashlib
import re
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from biomodel_agent import background_jats as jats, research_background as bg
from biomodel_agent import research_literature as literature
from biomodel_agent import research_projects as p, research_schema
from biomodel_agent.storage import ArtifactStore

TEST_FILE = Path(__file__).resolve()
REPO = TEST_FILE.parents[1]
PRODUCTION_SRC = REPO / 'src' / 'biomodel_agent'
# This file is the production regression: it must run against production src,
# never against an isolated prototype copy that happens to be importable.
assert Path(bg.__file__).resolve() == (PRODUCTION_SRC / 'research_background.py').resolve(), \
    f'research_background imported from {bg.__file__}, not production src'
assert Path(jats.__file__).resolve() == (PRODUCTION_SRC / 'background_jats.py').resolve(), \
    f'background_jats imported from {jats.__file__}, not production src'
assert Path(literature.__file__).resolve() == (PRODUCTION_SRC / 'research_literature.py').resolve(), \
    f'research_literature imported from {literature.__file__}, not production src'

MARKER = re.compile(r'\[unreadable-formula:([^\]]+)\]')

METHOD = 'We measured growth in bacterial cultures.'
RESULT = 'Plasmids increased persistence under the tested conditions.'
EXPT_BODY = ('<sec><title>Introduction</title><p>Prior authors proposed persistence effects.</p></sec>'
             '<sec><title>Methods</title><p>' + METHOD + '</p></sec>'
             '<sec><title>Results</title><p>' + RESULT + '</p></sec>')
REFS = ('<ref id="R1"><element-citation><article-title>Experiment</article-title>'
        '<pub-id pub-id-type="doi">10.1234/expt</pub-id></element-citation></ref>')


def article(doi, body, refs=REFS):
    return (f'<article><front><article-meta><title-group><article-title>Study</article-title>'
            f'</title-group><article-id pub-id-type="doi">{doi}</article-id></article-meta></front>'
            f'<body>{body}</body><back><ref-list>{refs}</ref-list></back></article>').encode()


def inline_graphic(name):
    return ('<inline-formula><inline-graphic xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'xlink:href="{name}"/></inline-formula>')


def plain_source(store, doi, body, refs=REFS):
    """Non-marker source pages: the pre-existing fixture projection (control path)."""
    raw = article(doi, body, refs)
    text, warnings = literature._extract(raw)
    blob = store.put_blob(raw)
    return store.put('source', dict(blob_ref=blob, filename='study.xml', parser=literature.VERSION,
        parser_version='1', pages=[dict(number=1, text=text)], warnings=warnings), refs=(blob,))


def marker_source(store, doi, body, refs=REFS, pages=None):
    """Source whose stored page keeps the shared JATS projection's formula markers."""
    raw = article(doi, body, refs)
    projection = literature.extract_projection(raw)
    rows = [dict(number=1, text=projection['text'])] if pages is None else pages(projection['text'])
    blob = store.put_blob(raw)
    return store.put('source', dict(blob_ref=blob, filename='study.xml', parser=literature.VERSION,
        parser_version='3', pages=rows, warnings=projection['warnings'],
        unresolved_formula=projection['unresolved_formula'],
        unresolved_formula_count=projection['unresolved_formula_count'],
        unresolved_formula_records=projection['unresolved_formula_records'],
        unresolved_formula_truncated=projection['unresolved_formula_truncated'],
        formula_assets=projection['formula_assets']), refs=(blob,))


def literal_anchor(ref, quote):
    return dict(source_ref=ref, page=1, quote=quote)


def new_project(store, mm):
    expt = plain_source(store, '10.1234/expt', EXPT_BODY)
    project = p.create(store, dict(scenario='Reproduce the stated plasmid study.',
                                   mode='reproduction', source_refs=[mm, expt]))
    return project, expt


def review_arguments(index, expt, requirement):
    return dict(index_ref=index, requirements=[requirement],
                reviews=[dict(reference_id='R1', source_ref=expt, study_type='experimental',
                              mathematical_modeling='absent', fulltext_reviewed=True,
                              rationale='Reviewed experimental methods and results with ordinary '
                                        'statistical analysis.',
                              methods_evidence=[literal_anchor(expt, METHOD)],
                              results_evidence=[literal_anchor(expt, RESULT)], modeling_evidence=[],
                              coverage=[dict(requirement_id=requirement['id'],
                                             explanation='Observed persistence change.',
                                             evidence=[literal_anchor(expt, RESULT)])])])


def page_lines(page_text):
    """Newline-delimited lines of a stored page, whitespace-trimmed."""
    return [line.strip() for line in page_text.split('\n') if line.strip()]


def paragraph_projections(raw):
    """Canonical projection of every body <p>, in document order."""
    root = ET.fromstring(raw.decode('utf-8'))
    return [literature._project_element(node, [], []) for node in root.iter()
            if node.tag.split('}')[-1] == 'p']


def legacy_paragraph_texts(raw):
    """Exactly what the pre-projection index builder kept for every body <p>."""
    root = ET.fromstring(raw.decode('utf-8'))
    return [jats._element_text(node) for node in root.iter()
            if node.tag.split('}')[-1] == 'p']


def legacy_index(store, project, mm, texts=None, blocks=None):
    """A saved index object shaped exactly like the pre-projection builder left it.

    The intro block texts are produced by the very flattener the old builder
    used (background_jats._element_text), so this is a verifiable legacy
    adaptation, not an invented shape; blocks=[] is what such an index stores
    for its full block list in the fixtures that need the legacy route.
    """
    source = store.get(mm)['payload']
    raw = store.get_blob(source['blob_ref'])
    if texts is None:
        texts = legacy_paragraph_texts(raw)
    document = dict(identity=None, blocks=blocks if blocks is not None else [],
                    references=[], issues=[], status='indexed',
                    intro_blocks=[dict(text=text, citations=[]) for text in texts])
    payload = dict(version=1, project_ref=project, source_ref=mm, document=document,
                   status='indexed', unsupported_reason=None, curator_only=True,
                   task_b_packet_ready=False)
    return store.put(bg.INDEX, payload, refs=(project, mm))


def assert_verbatim_anchors(store, source_ref, packet, unique=True):
    """Every returned selector must be a verbatim slice of its stored page."""
    pages = {page['number']: page['text'] for page in store.get(source_ref)['payload']['pages']}
    for entry in packet['intro_contexts']:
        anchor = entry['mm_context']
        page = pages[anchor['page']]
        assert page[anchor['start']:anchor['end']] == anchor['quote'], entry['context_id']
        if unique:
            assert page.find(anchor['quote'], anchor['start'] + 1) < 0, entry['context_id']
        assert len(anchor['quote']) <= 4000, entry['context_id']
        for marker in entry.get('formula_markers', []):
            assert marker in anchor['quote'], entry['context_id']


# ---------------------------------------------------------------------------
# 1. the NEW index reuses the canonical projection: image formulas stay visible
# ---------------------------------------------------------------------------

def test_new_index_keeps_the_image_formula_marker_and_equals_the_page_line(tmp_path):
    body = ('<sec><title>Introduction</title>'
            '<p>Biological rhythms are generated by regulatory networks.</p>'
            '<p>Synthesis is linear, except the repression which takes the form of a sigmoidal '
            f'Hill curve {inline_graphic("journal.e001.jpg")} and the Hill coefficient is larger '
            'than eight.</p>'
            '<p>The goal of the present work is to propose a molecular mechanism.</p></sec>')
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body)
    project, expt = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    index_before = deepcopy(store.get(index))
    page = store.get(mm)['payload']['pages'][0]['text']
    assert page.count('[unreadable-formula:journal.e001.jpg]') == 1
    blocks = store.get(index)['payload']['document']['intro_blocks']
    raw = store.get_blob(store.get(mm)['payload']['blob_ref'])
    # Case 1: the indexed paragraph carries the SAME canonical text as the page
    # line, marker included -- a private, lossier flattener is the defect.
    assert '[unreadable-formula:journal.e001.jpg]' in blocks[1]['text']
    assert blocks[1]['text'] in page, 'the index block must be byte-identical to its page line'
    assert blocks[1]['text'] in paragraph_projections(raw), \
        'the index text comes from the shared canonical projection'
    assert blocks[1]['text'] not in legacy_paragraph_texts(raw), \
        'the old flattener provably lost this marker, so this is the new canonical route'
    assert store.get(index) == index_before

    packet = bg.intro_packet(store, project, index)
    assert packet is not None
    assert packet['all_indexed_intro_blocks_supplied'] is True
    assert packet['indexed_intro_blocks'] == 3
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1', 'i2']
    assert 'unbound_intro_blocks' not in packet and 'intro_binding_notice' not in packet
    marked = packet['intro_contexts'][1]
    assert marked['formula_markers'] == ['[unreadable-formula:journal.e001.jpg]']
    assert marked['mm_context']['quote'] == blocks[1]['text'], \
        'the whole canonical paragraph is the anchor, not a marker-free remnant of it'
    assert not any('formula_markers' in row for position, row
                   in enumerate(packet['intro_contexts']) if position != 1)
    assert_verbatim_anchors(store, mm, packet)

    schema = research_schema.schemas(p.inspect(store, project))['review_expt_background'][
        'properties']['args']['properties']['requirements']['items']
    pattern = re.compile(schema['properties']['mm_context_id']['pattern'])
    assert all(pattern.fullmatch(row['context_id']) for row in packet['intro_contexts'])

    result = p.operate(store, project, 'review_expt_background',
                       review_arguments(index, expt, dict(id='persistence',
                           question='How does co-infection affect persistence?',
                           mm_context_id='i1')))
    assert result['status'] == 'complete' and result['qualified_count'] == 1
    saved = store.get(result['review_ref'])['payload']
    assert saved['requirements'][0]['mm_context']['quote'] == marked['mm_context']['quote']
    assert '[unreadable-formula:journal.e001.jpg]' in saved['requirements'][0]['mm_context']['quote']


def test_source_and_new_index_both_surface_the_unknown_formula(tmp_path):
    body = ('<sec><title>Introduction</title>'
            f'<p>Readable math is the alternative route '
            '<disp-formula><alternatives>'
            '<graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="art.f001.jpg"/>'
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo><mn>4</mn>'
            '</math></alternatives></disp-formula> in this paragraph.</p>'
            f'<p>A textless formula <inline-formula> </inline-formula> stays marked.</p></sec>')
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body)
    project, _ = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    source = store.get(mm)['payload']
    page = source['pages'][0]['text']
    blocks = store.get(index)['payload']['document']['intro_blocks']
    # The structured gap ledger stays on the source; the index carries the same
    # visible markers in its own text, so nothing is invisible on either side.
    assert source['unresolved_formula_count'] == 1
    assert '[unreadable-formula:unidentified]' in page
    assert '[unreadable-formula:unidentified]' in blocks[1]['text']
    assert '[unreadable-formula:art.f001.jpg]' not in page, 'a readable alternative is not a hole'
    assert '[unreadable-formula:art.f001.jpg]' not in blocks[0]['text']
    assert any(entry['asset'] == 'art.f001.jpg' for entry in source['formula_assets']), \
        'the skipped graphic stays a recorded unread asset'

    packet = bg.intro_packet(store, project, index)
    assert packet is not None and packet['all_indexed_intro_blocks_supplied'] is True
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1']
    assert [row.get('formula_markers') for row in packet['intro_contexts']] == [
        None, ['[unreadable-formula:unidentified]']]
    assert_verbatim_anchors(store, mm, packet)


# ---------------------------------------------------------------------------
# 2. legacy index, edge/multiple markers: the binding covers them or says unbound
# ---------------------------------------------------------------------------

EDGE_BODY = ('<sec><title>Introduction</title>'
             f'<p>{inline_graphic("journal.e010.jpg")} Persistence rises with the copy number</p>'
             f'<p>Persistence falls with the degradation rate {inline_graphic("journal.e011.jpg")}</p>'
             f'<p>{inline_graphic("journal.e012a.jpg")} Persistence follows the growth rate '
             f'{inline_graphic("journal.e012b.jpg")}</p></sec>')


def test_legacy_edge_and_multiple_markers_bind_the_whole_stored_paragraph(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', EDGE_BODY)
    project, _ = new_project(store, mm)
    legacy = legacy_index(store, project, mm)
    legacy_before = deepcopy(store.get(legacy))
    page = store.get(mm)['payload']['pages'][0]['text']
    texts = legacy_paragraph_texts(store.get_blob(store.get(mm)['payload']['blob_ref']))
    assert texts == ['Persistence rises with the copy number',
                     'Persistence falls with the degradation rate',
                     'Persistence follows the growth rate'], 'the legacy flattener lost every marker'
    for text in texts:
        assert text in page, 'each legacy quote still occurs verbatim INSIDE its marked line'

    packet = bg.intro_packet(store, project, legacy)
    assert packet is not None, 'edge formulas must not cost the block its selector'
    assert store.get(legacy) == legacy_before, 'the saved old index object stays byte-unchanged'
    assert packet['all_indexed_intro_blocks_supplied'] is True
    assert 'unbound_intro_blocks' not in packet
    expected = [(['[unreadable-formula:journal.e010.jpg]'], texts[0]),
                (['[unreadable-formula:journal.e011.jpg]'], texts[1]),
                (['[unreadable-formula:journal.e012a.jpg]',
                  '[unreadable-formula:journal.e012b.jpg]'], texts[2])]
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1', 'i2']
    for row, (markers, text) in zip(packet['intro_contexts'], expected):
        quote = row['mm_context']['quote']
        # The binding is the WHOLE stored line, markers included, never the
        # marker-free remnant the raw-exact-first match used to take.
        assert row['formula_markers'] == markers, row['context_id']
        assert all(marker in quote for marker in markers), row['context_id']
        assert ' '.join(MARKER.sub('', quote).split()) == text, row['context_id']
        assert quote != text, row['context_id']
        assert quote in page, row['context_id']
    assert_verbatim_anchors(store, mm, packet)


def test_legacy_repeated_edge_marker_paragraphs_are_reported_unbound(tmp_path):
    # A first block that binds keeps the packet alive, so the ambiguous block's
    # diagnostics stay visible instead of collapsing into "no packet at all"
    # (the documented all-unbound contract of _bind_intro_contexts).
    body = ('<sec><title>Introduction</title>'
            '<p>Unique opening premise about circadian clocks.</p>'
            f'<p>{inline_graphic("journal.e014.jpg")} Persistence rises with the copy number</p>'
            f'<p>{inline_graphic("journal.e015.jpg")} Persistence rises with the copy number</p>'
            '</sec>')
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body)
    project, _ = new_project(store, mm)
    legacy = legacy_index(store, project, mm, texts=[
        'Unique opening premise about circadian clocks.',
        'Persistence rises with the copy number'])

    packet = bg.intro_packet(store, project, legacy)
    assert packet is not None
    assert packet['all_indexed_intro_blocks_supplied'] is False
    assert packet['indexed_intro_blocks'] == 2
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0']
    unbound = packet['unbound_intro_blocks']
    assert [(row['block'], row['reason'], row['matches']) for row in unbound] == [
        (1, 'ambiguous_source_anchor', 2)]
    # Facts, not byte-presence: the two competing regions carry DIFFERENT
    # unknown formula content, so neither can be picked reliably.
    candidates = unbound[0]['candidates']
    assert [(row['page'], row['start'] < row['end']) for row in candidates] == [(1, True), (1, True)]
    assert len({tuple(row['formula_markers']) for row in candidates}) == 2
    assert {tuple(row['formula_markers']) for row in candidates} == {
        ('[unreadable-formula:journal.e014.jpg]',), ('[unreadable-formula:journal.e015.jpg]',)}
    assert 'direct literal mm_context anchor' in packet['intro_binding_notice']
    assert 'unbound_intro_blocks' in packet['intro_binding_notice']
    assert bg.requirements_packet(store, project, p.inspect(store, project)) is None


# ---------------------------------------------------------------------------
# 3. plain vs formula paragraph with equal marker-free text: identity, not bytes
# ---------------------------------------------------------------------------

IDENTITY_BODY = ('<sec><title>Introduction</title>'
                 '<p>Unique opening premise about circadian clocks.</p>'
                 '<p>The repression term follows a Hill curve.</p>'
                 f'<p>The repression term {inline_graphic("journal.e013.jpg")} follows a Hill '
                 'curve.</p></sec>')
UNIQUE_LINE = 'Unique opening premise about circadian clocks.'
PLAIN_LINE = 'The repression term follows a Hill curve.'
MARKED_LINE = ('The repression term [unreadable-formula:journal.e013.jpg] follows a Hill curve.')


def test_legacy_index_does_not_bind_the_formula_paragraph_to_the_plain_one(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', IDENTITY_BODY)
    project, _ = new_project(store, mm)
    page = store.get(mm)['payload']['pages'][0]['text']
    assert PLAIN_LINE in page and MARKED_LINE in page
    # A first block that binds keeps the packet (and its diagnostics) alive;
    # the legacy index carries the marker-free text of the FORMULA paragraph.
    legacy = legacy_index(store, project, mm, texts=[UNIQUE_LINE, PLAIN_LINE])
    legacy_before = deepcopy(store.get(legacy))

    packet = bg.intro_packet(store, project, legacy)
    assert store.get(legacy) == legacy_before
    assert packet is not None, 'both lines still contain the quote, so this is ambiguity, not absence'
    assert packet['all_indexed_intro_blocks_supplied'] is False
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0']
    unbound = packet['unbound_intro_blocks']
    assert [(row['block'], row['reason'], row['matches']) for row in unbound] == [
        (1, 'ambiguous_source_anchor', 2)]
    candidates = unbound[0]['candidates']
    # One candidate IS the plain paragraph, one IS the formula paragraph; the
    # index cannot tell them apart, so neither is silently chosen.
    assert sorted(bool(row['formula_markers']) for row in candidates) == [False, True]
    assert bg.requirements_packet(store, project, p.inspect(store, project)) is None
    # The honest route out stays open: a direct literal anchor on either line.
    for quote in (PLAIN_LINE, MARKED_LINE):
        bound = bg._anchor(store, mm, literal_anchor(mm, quote))
        assert page[bound['start']:bound['end']] == quote


def test_canonical_index_binds_each_paragraph_to_its_own_line(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', IDENTITY_BODY)
    project, _ = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    blocks = store.get(index)['payload']['document']['intro_blocks']
    assert [block['text'] for block in blocks] == [UNIQUE_LINE, PLAIN_LINE, MARKED_LINE]
    assert MARKER.search(blocks[1]['text']) is None and MARKER.search(blocks[2]['text']) is not None, \
        'the canonical index keeps the formula in its own paragraph text'

    packet = bg.intro_packet(store, project, index)
    assert packet is not None and packet['all_indexed_intro_blocks_supplied'] is True
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1', 'i2']
    plain, marked = (row['mm_context'] for row in packet['intro_contexts'][1:])
    # Identity comes from the canonical text of each block, not from the mere
    # presence of the quote bytes: the plain block must land on the plain line.
    assert plain['quote'] == PLAIN_LINE and 'unreadable-formula' not in plain['quote']
    assert marked['quote'] == MARKED_LINE
    assert packet['intro_contexts'][2].get('formula_markers') == [
        '[unreadable-formula:journal.e013.jpg]']
    assert packet['intro_contexts'][1].get('formula_markers') is None
    assert (plain['start'], plain['end']) != (marked['start'], marked['end'])
    assert_verbatim_anchors(store, mm, packet)


# ---------------------------------------------------------------------------
# 4. round-42 regressions that must keep holding (adapted to the canonical index)
# ---------------------------------------------------------------------------

def test_marker_block_on_a_later_page_binds_on_that_page(tmp_path):
    body = ('<sec><title>Introduction</title>'
            '<p>First premise about biological rhythms.</p>'
            '<p>Second premise about cooperative binding.</p>'
            f'<p>Third premise {inline_graphic("journal.e002.jpg")} closes the introduction.</p>'
            '</sec>')

    def pages(text):
        # The stored source is split at a real line boundary; the marker line
        # becomes the first line of page 2.
        lines = text.split('\n')
        cut = next(number for number, line in enumerate(lines) if 'unreadable-formula' in line)
        return [dict(number=1, text='\n'.join(lines[:cut])),
                dict(number=2, text='\n'.join(lines[cut:]))]

    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body, pages=pages)
    project, _ = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    packet = bg.intro_packet(store, project, index)
    assert packet is not None and packet['all_indexed_intro_blocks_supplied'] is True
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1', 'i2']
    third = packet['intro_contexts'][2]['mm_context']
    assert third['page'] == 2
    assert third['quote'] == store.get(index)['payload']['document']['intro_blocks'][2]['text']
    assert '[unreadable-formula:journal.e002.jpg]' in third['quote']
    assert packet['intro_contexts'][0]['mm_context']['page'] == 1
    assert_verbatim_anchors(store, mm, packet)


def test_unbound_blocks_get_honest_diagnostics_stable_ids_and_generic_fallback(tmp_path):
    body = ('<sec><title>Introduction</title>'
            '<p>Unique first premise for the compact selector.</p>'
            '<p>Duplicated premise text for the ambiguous block.</p>'
            '<p>Third premise sentence that appears once in the stored page.</p></sec>'
            '<sec><title>Results</title><p>Duplicated premise text for the ambiguous block.</p>'
            '<p>Unique follow-up sentence in the results.</p></sec>')
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body)
    project, expt = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']

    packet = bg.intro_packet(store, project, index)
    assert packet is not None, 'bound blocks keep their selectors; the rest is diagnosed'
    assert packet['all_indexed_intro_blocks_supplied'] is False
    assert packet['indexed_intro_blocks'] == 3
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0', 'i1']
    assert packet['intro_contexts'][1]['mm_context']['quote'] == \
        'Third premise sentence that appears once in the stored page.'
    unbound = packet['unbound_intro_blocks']
    assert [(row['block'], row['reason'], row['matches']) for row in unbound] == [
        (1, 'ambiguous_source_anchor', 2)]
    assert unbound[0]['text_head'] == 'Duplicated premise text for the ambiguous block.'
    assert unbound[0]['text_truncated'] is False
    assert unbound[0]['text_sha256'] == hashlib.sha256(
        b'Duplicated premise text for the ambiguous block.').hexdigest()
    assert unbound[0]['formula_markers'] == []
    assert 'direct literal mm_context anchor' in packet['intro_binding_notice']
    assert 'unbound_intro_blocks' in packet['intro_binding_notice']
    assert_verbatim_anchors(store, mm, packet)
    assert bg.requirements_packet(store, project, p.inspect(store, project)) is None, \
        'an incomplete packet falls back to the generic reading path'

    page = store.get(mm)['payload']['pages'][0]['text']
    direct = 'Unique first premise for the compact selector.'
    assert page.find(direct) >= 0 and page.find(direct, page.find(direct) + 1) < 0
    bound = bg._anchor(store, mm, literal_anchor(mm, direct))
    assert page[bound['start']:bound['end']] == direct
    result = p.operate(store, project, 'review_expt_background', review_arguments(
        index, expt, dict(id='persistence', question='How does co-infection affect persistence?',
                          mm_context=literal_anchor(mm, direct))))
    assert result['status'] == 'complete'


def test_saved_legacy_index_without_any_marker_keeps_its_object_and_diagnostics(tmp_path):
    body = ('<sec><title>Introduction</title>'
            '<p>Unique first premise for the compact selector.</p>'
            '<p>Absent premise opening clause with the stored tail.</p></sec>')
    store = ArtifactStore(tmp_path / 'store')
    mm = marker_source(store, '10.1234/mm', body)
    project, _ = new_project(store, mm)
    page = store.get(mm)['payload']['pages'][0]['text']
    assert 'Absent premise opening clause with the stored tail.' in page
    legacy = legacy_index(store, project, mm, texts=[
        'Unique first premise for the compact selector.',
        'Absent premise opening clause with a divergent tail.'])
    legacy_before = deepcopy(store.get(legacy))

    packet = bg.intro_packet(store, project, legacy)
    assert packet is not None
    assert packet['all_indexed_intro_blocks_supplied'] is False
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0']
    unbound = packet['unbound_intro_blocks']
    assert [(row['block'], row['reason'], row['matches']) for row in unbound] == [
        (1, 'no_source_anchor', 0)]
    assert unbound[0]['text_head'] == 'Absent premise opening clause with a divergent tail.'
    assert store.get(legacy) == legacy_before, 'the saved old index object stays byte-unchanged'

    direct = 'Absent premise opening clause'
    occurrences = [at for at in range(len(page)) if page.startswith(direct, at)]
    assert len(occurrences) == 1
    bound = bg._anchor(store, mm, literal_anchor(mm, direct))
    assert page[bound['start']:bound['end']] == direct


def test_normal_non_formula_mm_keeps_the_exact_legacy_packet(tmp_path):
    context = 'Co-infection can alter plasmid persistence.'
    body = f'<sec><title>Background</title><p>{context}</p></sec>'
    store = ArtifactStore(tmp_path / 'store')
    mm = plain_source(store, '10.1234/mm', body)
    project, _ = new_project(store, mm)
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']

    packet = bg.intro_packet(store, project, index)
    assert packet is not None
    assert set(packet) == {'index_ref', 'source_ref', 'identity', 'intro_contexts',
                           'all_indexed_intro_blocks_supplied', 'indexed_intro_blocks',
                           'document_issues', 'source_warnings', 'scientific_support'}
    assert packet['all_indexed_intro_blocks_supplied'] is True
    assert [row['context_id'] for row in packet['intro_contexts']] == ['i0']
    assert all('formula_markers' not in row for row in packet['intro_contexts'])
    assert packet['intro_contexts'][0]['reference_ids'] == []
    assert_verbatim_anchors(store, mm, packet)
    assert bg.requirements_packet(store, project, p.inspect(store, project)) is not None
