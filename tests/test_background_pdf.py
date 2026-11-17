"""PDF text extraction retains literal source locations and uncertainty."""
import pytest

from biomodel_agent.background_pdf import extract_pdf_background, MAX_TOTAL_UTF8_BYTES


def extract(text, number=1):
    return extract_pdf_background([dict(number=number, text=text)])


def check_spans(value, pages):
    if isinstance(value, dict):
        if {'page', 'start', 'end', 'quote'} <= value.keys():
            assert pages[value['page']][value['start']:value['end']] == value['quote']
        for child in value.values():
            check_spans(child, pages)
    elif isinstance(value, list):
        for child in value:
            check_spans(child, pages)


def test_numeric_continuation_retains_parenthesized_protein_name():
    text = ('Introduction\nPrior work [1,2].\nMethods\nWe measured oxygen.\nReferences\n'
            '1. Alpha A: Hypoxia inducible factor\n'
            '1 (HIF-1) in hypoxia induced apoptosis. J Clin Pathol 2004, 1:12.\n'
            '2. Beta B: Oxygen measurement. Journal 2005, 2:22.')
    result = extract(text)
    assert [r['reference_id'] for r in result['references']] == ['1', '2']
    assert 'HIF-1' in result['references'][0]['citation_text']
    assert result['duplicate_reference_ids'] == []
    assert all(r['eligibility'] == 'pending_review' for r in result['references'])
    assert result['references'][0]['intro_context_locators']
    check_spans(result, {1: text})


def test_explicit_duplicate_labels_preserve_ambiguity():
    result = extract('Introduction\nPrior work [1].\nMethods\nMeasurements.\nReferences\n'
                     '1. Alpha A: First experiment. Journal 2001, 1:12.\n'
                     '1. Beta B: Different experiment. Journal 2002, 2:22.')
    assert [r['reference_id'] for r in result['references']] == ['1', '1']
    assert result['duplicate_reference_ids'] == ['1']
    assert result['status'] == 'needs_review'


def test_cross_page_reference_and_intro_keep_original_spans():
    pages = {1: 'Introduction\nPrior work [1-2] continues',
             2: 'across pages.\nMethods\nMeasurements.\nReferences\n'
                '1. Alpha A: First experiment. Journal 2001, 1:12.\n'
                '2. Beta B: Second experiment.',
             3: 'Journal 2002, 2:22.'}
    result = extract_pdf_background([dict(number=n, text=t) for n, t in pages.items()])
    assert {s['page'] for s in result['intro_blocks'][0]['source_spans']} == {1, 2}
    assert {s['page'] for s in result['references'][1]['source_spans']} == {2, 3}
    assert all(r['intro_context_locators'] for r in result['references'])
    check_spans(result, pages)


def test_missing_front_page_and_page_gap_are_explicit():
    result = extract_pdf_background([dict(number=4, text='Methods\nMeasurements.'),
                                    dict(number=2, text='Introduction\nPrior experiments.')])
    codes = {i['code'] for i in result['issues']}
    assert {'front_page_missing', 'page_sequence_gap', 'pages_reordered'} <= codes
    assert result['identity']['article_ids'] == []
    assert result['identity']['article_titles'] is None
    assert result['status'] == 'needs_review'


def test_abstract_and_unheaded_bibliography_remain_unresolved():
    result = extract('Abstract\nPrior work [1].\nMethods\nMeasurements.\n'
                     '1. Alpha A: Experiment. Journal 2001, 1:12.')
    assert result['intro_blocks'] == []
    assert result['references'] == []
    assert {'intro_not_found', 'references_not_found'} <= {i['code'] for i in result['issues']}


@pytest.mark.parametrize('pages', [[], None, [dict(number=True, text='x')],
    [dict(number=0, text='x')], [dict(number=1, text=3)],
    [dict(number=1, text='x'), dict(number=1, text='y')],
    [dict(number=1, text='文' * (MAX_TOTAL_UTF8_BYTES // 3 + 1))]])
def test_invalid_or_oversized_input_is_rejected(pages):
    with pytest.raises((TypeError, ValueError)):
        extract_pdf_background(pages)
