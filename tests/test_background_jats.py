"""Independent controls for the DSH-derived JATS background extractor."""
from copy import deepcopy
import pytest

from biomodel_agent import background_jats as jats


def article(*, refs='', text='Evidence', rid='R1', ids=None):
    ids = '<article-id pub-id-type="doi">10.1234/mm</article-id>' if ids is None else ids
    return (f'<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
            f'<title-group><article-title>Study</article-title></title-group>{ids}</article-meta></front>'
            f'<body><sec><title>Background</title><p>{text}<xref ref-type="bibr" rid="{rid}"/></p>'
            f'</sec></body><back><ref-list>{refs}</ref-list></back></article>').encode()


def reference(rid='R1', title='Experiment', ids=''):
    return f'<ref id="{rid}"><element-citation><article-title>{title}</article-title>{ids}</element-citation></ref>'


def test_peer_review_subarticles_do_not_become_research_text_or_references():
    history = ('<sub-article article-type="aggregated-review-documents"><body><sec>'
               '<title>Results</title><p>Reviewer comments about a mathematical model.</p>'
               '</sec></body><back><ref-list>'+reference('R2')+'</ref-list></back></sub-article>')
    associated = ('<sub-article article-type="associated-data"><body><sec>'
                  '<title>Data availability</title><p>Associated research data.</p>'
                  '</sec></body></sub-article>')
    raw = article(refs=reference()).replace(b'</article>', (history+associated+'</article>').encode())
    document = jats.extract_background(raw)
    assert [r['reference_id'] for r in document['references']] == ['R1']
    assert all('Reviewer comments' not in b['text'] for b in document['blocks'])
    assert any(b['text']=='Associated research data.' for b in document['blocks'])
    assert any(issue['code']=='review_history_excluded' for issue in document['issues'])


def test_a_review_article_root_is_preserved_for_study_type_assessment():
    raw = article(text='Review article content.').replace(b'<article ', b'<article article-type="review-article" ', 1)
    assert jats.extract_background(raw)['intro_blocks'][0]['text'].startswith('Review article content.')


def test_multi_rid_resolves_each_reference_and_preserves_unknown_rid():
    raw = article(refs=reference()+reference('R2'), rid='R1 R2 missing')
    out = jats.extract_background(raw)
    resolutions = out['intro_blocks'][0]['citations'][0]['resolutions']
    assert [r['resolved'] for r in resolutions] == [True, True, False]
    assert [r['rid'] for r in out['unresolved_citations']] == ['missing']
    assert all(r['intro_context_paths'] == ['/article/body[1]/sec[1]/p[1]'] for r in out['references'])
    assert all(r['eligibility'] == 'pending_review' for r in out['references'])


def test_namespace_background_nested_sections_body_refs_and_identity_conflict():
    raw = article(refs=reference(), ids='<article-id pub-id-type="doi">10.1234/a</article-id>'
                  '<article-id pub-id-type="doi">10.1234/b</article-id>')
    raw = raw.replace(b'<article ', b'<article xmlns="urn:jats" ', 1)
    raw = raw.replace(b'</sec></body>', b'<sec><title>Details</title><p>Nested evidence</p></sec></sec></body>')
    raw = raw.replace(b'</body><back><ref-list>', b'<ref-list>').replace(b'</ref-list></back>', b'</ref-list></body>')
    out = jats.extract_background(raw)
    assert len(out['intro_blocks']) == 2 and len(out['references']) == 1
    assert out['identity']['conflicts']['doi'] is True
    assert len(out['identity']['article_ids']) == 2
    assert out['status'] == 'needs_review'


def test_doi_only_identity_is_sufficient_to_index_candidates():
    out = jats.extract_background(article(refs=reference()))
    assert out['status'] == 'indexed'
    assert not any(i['code'] == 'identity_missing_ids' for i in out['issues'])


def test_duplicate_reference_id_is_ambiguous_and_all_rows_survive():
    out = jats.extract_background(article(refs=reference()+reference(title='Other')))
    assert len(out['references']) == 2
    assert out['duplicate_reference_ids'] == ['R1']
    assert out['unresolved_citations'][0]['reason'] == 'duplicate_reference_id'
    assert not out['intro_blocks'][0]['citations'][0]['resolutions'][0]['resolved']


def test_structured_ids_preserved_and_journal_is_not_article_title():
    refs = ('<ref id="R1"><mixed-citation><source>Journal title</source>'
            '<ext-link ext-link-type="doi" xlink:href="https://doi.org/10.1234/TEST"/>'
            '<pub-id pub-id-type="doi">10.1234/other</pub-id>'
            '<ext-link ext-link-type="pmid" xlink:href="https://evil.test/123/"/>'
            '</mixed-citation></ref>')
    out = jats.extract_background(article(refs=refs))
    ref = out['references'][0]
    assert ref['title'] is None
    assert [i['value'] for i in ref['identifiers']] == ['10.1234/test', '10.1234/other', None]
    assert all(i['xml_path'].startswith('/article/back[1]/ref-list[1]/ref[1]/') for i in ref['identifiers'])


@pytest.mark.parametrize('value,kind', [('0','pmid'),('PMC0','pmcid'),('１２３','pmid'),('0001','pmid')])
def test_invalid_identifiers_never_become_usable(value, kind):
    out = jats.extract_background(article(refs=reference(ids=f'<pub-id pub-id-type="{kind}">{value}</pub-id>')))
    assert out['references'][0]['identifiers'][0]['value'] is None


@pytest.mark.parametrize('location', ['body', 'ref', 'front'])
def test_xinclude_is_rejected_in_every_subtree(location):
    node = '<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///forbidden"/>'
    raw = article(refs=reference()).decode()
    closing = {'body':'</p>', 'ref':'</element-citation>', 'front':'</article-meta>'}[location]
    raw = raw.replace(closing, node+closing, 1)
    with pytest.raises(ValueError):
        jats.extract_background(raw.encode())


def test_ordinary_include_tag_is_data():
    out = jats.extract_background(article(refs=reference(), text='Evidence <include>detail</include>'))
    assert 'detail' in out['blocks'][0]['text']


@pytest.mark.parametrize('raw', [b'\xff', b'<article>\x00</article>', b'<article>',
    b'<!DOCTYPE article [<!ENTITY x "data">]><article>&x;</article>',
    b'<article>'+b'<x>'*65+b'</x>'*65+b'</article>',
    b'<article>'+b'<x/>'*200000+b'</article>', b' '*2_097_153])
def test_invalid_or_over_budget_documents_are_rejected(raw):
    with pytest.raises(ValueError):
        jats.extract_background(raw)


def test_external_doctype_does_not_resolve_any_external_resource():
    raw = b'<!DOCTYPE article SYSTEM "https://example.invalid/never-fetched.dtd">'+article(refs=reference())
    assert jats.extract_background(raw)['status'] == 'indexed'


def test_missing_intro_and_references_are_explicit_gaps():
    out = jats.extract_background(article().replace(b'Background', b'Methods'))
    codes = {i['code'] for i in out['issues']}
    assert {'no_intro_section','no_references'} <= codes
    assert out['status'] == 'needs_review'


def test_unicode_text_uses_the_validated_utf8_bytes():
    raw = b'<?xml version="1.0" encoding="ISO-8859-1"?>'+article(refs=reference(),text='细胞实验')
    assert '细胞实验' in jats.extract_background(raw)['intro_blocks'][0]['text']


def test_large_article_above_twenty_thousand_nodes_is_supported():
    raw=article(refs=reference(),text='Measured '+('<italic>x</italic>'*25000))
    out=jats.extract_background(raw)
    assert len(out['references'])==1 and len(out['intro_blocks'])==1


def test_chinese_introduction_title_retains_citation_context():
    raw=article(refs=reference()).replace(b'Background','引言'.encode())
    out=jats.extract_background(raw)
    assert len(out['intro_blocks'])==1 and out['references'][0]['intro_context_paths']
