"""T07a review freshness, leakage, metric separation and no-label ranking."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from biomodel_agent import retrieval
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('retrieval_review_tool', ROOT/'benchmarks/development/retrieval_review.py')
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def packet():
    legacy = {'cases': [{'id': 'decay', 'category': 'parameter', 'query': 'parameter k',
        'sources': [{'id': 'main', 'pages': [{'number': 1, 'text': 'Parameter k = 0.4. Buffer pH = 0.4.'}]}],
        'anchors': [{'source_id': 'main', 'page': 1, 'start': 0, 'end': 18, 'quote': 'Parameter k = 0.4.'}],
        'annotation_note': 'Synthetic control, not real domain review.'}]}
    # Correct offsets must be frozen from the exact original text.
    legacy['cases'][0]['anchors'][0]['end'] = len(legacy['cases'][0]['anchors'][0]['quote'])
    return review.build_packet(legacy)


def approved(p):
    p = deepcopy(p)
    for c in p['cases']:
        c['review'].update(status='approved', reviewer='Synthetic reviewer B (test only)',
                           reviewed_at='2026-10-19T10:00:00+00:00', notes='Synthetic test decision, not an actual domain approval.',
                           checks={k: True for k in review.REVIEW_CHECKS})
        c['review']['reviewed_sha256'] = review.case_digest(p, c)
    return p


def hit_output(case, refs, start, end):
    text = case['sources'][0]['pages'][0]['text']
    return {'query': case['query'], 'method': 'lexical_v1',
            'results': [{'source_ref': refs['main'], 'page': 1, 'start': start, 'end': end, 'text': text[start:end]}],
            'returned_chars': end-start, 'truncated': False}


def test_existing_material_packet_has_no_invented_review_or_paper_count():
    legacy = json.loads((ROOT/'benchmarks/inputs/retrieval-development/cases.json').read_text())
    p = review.build_packet(legacy)
    audit = review.validate_packet(p)
    assert audit['cases'] == 18 and audit['required_spans'] == 21
    assert audit['real_paper_count'] == 0 and audit['paper_families'] == 1
    assert audit['review_status'] == {'pending': 18} and audit['semantic_support'] == 'unknown'
    assert audit['reviewed_comparison_ready'] is False
    for original, converted in zip(legacy['cases'], p['cases']):
        assert converted['query'] == original['query']
        assert [s['pages'] for s in converted['sources']] == [s['pages'] for s in original['sources']]


def test_pending_gate_refuses_before_any_store_write_or_ranking(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path/'store')
    monkeypatch.setattr(store, 'put', lambda *a, **k: pytest.fail('pending review wrote source'))
    monkeypatch.setattr(retrieval, 'search_sources', lambda *a, **k: pytest.fail('pending review reached ranker'))
    with pytest.raises(ValueError, match='review gate'):
        review.evaluate(packet(), store, mode='reviewed')


@pytest.mark.parametrize('defect', ['value', 'query', 'category', 'author', 'budget', 'family', 'role'])
def test_approved_review_expires_on_material_or_protocol_change(defect):
    p = approved(packet()); c = p['cases'][0]
    if defect == 'value':
        c['sources'][0]['pages'][0]['text'] = c['sources'][0]['pages'][0]['text'].replace('0.4', '0.5')
        c['sources'][0]['pages_sha256'] = review.digest(c['sources'][0]['pages'])
        c['anchors'][0]['quote'] = c['anchors'][0]['quote'].replace('0.4', '0.5')
    elif defect == 'budget':
        p['budget']['limit'] = 2
    elif defect == 'author':
        p['annotation_author'] = 'another annotator'
    elif defect == 'family':
        c['paper_family_id'] = 'changed-family'
    elif defect == 'role':
        c['sources'][0]['role'] = 'supplement'
    else:
        c[defect] = 'changed'
    with pytest.raises(ValueError, match='stale review'):
        review.validate_packet(p)


@pytest.mark.parametrize('defect', ['self_review', 'empty_notes', 'unchecked_scope', 'missing_time', 'naive_time'])
def test_approved_flag_alone_is_insufficient(defect):
    p = approved(packet()); r = p['cases'][0]['review']
    if defect == 'self_review': r['reviewer'] = p['annotation_author']
    if defect == 'empty_notes': r['notes'] = ''
    if defect == 'unchecked_scope': r['checks']['target_scope'] = False
    if defect == 'missing_time': r['reviewed_at'] = None
    if defect == 'naive_time': r['reviewed_at'] = '2026-10-19T10:00:00'
    with pytest.raises(ValueError): review.validate_packet(p)


def test_frozen_review_forms_do_not_mutate_candidates_and_go_stale(tmp_path):
    p = packet(); original = deepcopy(p)
    review.write_review_pack(p, tmp_path/'pack')
    form = json.loads((tmp_path/'pack/review-forms.json').read_text())
    assert review.apply_reviews(p, form)['cases'][0]['review']['status'] == 'pending'
    form['decisions']['decay'] = approved(p)['cases'][0]['review']
    ready = review.apply_reviews(p, form)
    assert review.gate(ready, 'reviewed')[0]['review_status'] == {'approved': 1}
    assert p == original
    p['cases'][0]['query'] = 'another query'
    with pytest.raises(ValueError, match='stale'):
        review.apply_reviews(p, form)


@pytest.mark.parametrize('defect', ['same_family', 'duplicate_content', 'seen_promoted'])
def test_group_and_exposure_checks_prevent_leakage(defect):
    p = packet(); c = deepcopy(p['cases'][0]); c['id'] = 'copy'
    c['split'] = 'comparison_candidate'
    if defect == 'duplicate_content':
        p['origin'] = 'paper_development'; p['exposure'] = 'not_established'; c['paper_family_id'] = 'other-paper'
    elif defect == 'seen_promoted':
        c['paper_family_id'] = 'other-paper'
        c['sources'][0]['pages'][0]['text'] += ' new text'
        c['sources'][0]['pages_sha256'] = review.digest(c['sources'][0]['pages'])
    else:
        p['origin'] = 'paper_development'
        p['exposure'] = 'not_established'
    p['cases'].append(c)
    with pytest.raises(ValueError, match='family|duplicate material|seen development'):
        review.validate_packet(p)


@pytest.mark.parametrize('defect', ['boolean_page', 'wrong_quote', 'duplicate_span', 'foreign_source', 'wrong_hash'])
def test_bad_annotations_fail_before_review(defect):
    p = packet(); c = p['cases'][0]; a = c['anchors'][0]
    if defect == 'boolean_page': a['page'] = True
    if defect == 'wrong_quote': a['quote'] = 'invented'
    if defect == 'duplicate_span': c['anchors'].append(dict(a, id='duplicate'))
    if defect == 'foreign_source': a['source_id'] = 'not-authorized'
    if defect == 'wrong_hash': c['sources'][0]['pages_sha256'] = '0'*64
    with pytest.raises(ValueError): review.validate_packet(p)


def test_relevance_overlap_and_complete_evidence_are_different():
    p = packet(); c = p['cases'][0]; refs = {'main': 'source'}
    # A clipped number is exact source text and relevant, yet not the whole declaration.
    text = c['sources'][0]['pages'][0]['text']; start = text.index('0.4')
    score = review.score_output(c, refs, hit_output(c, refs, start, start+3), p['budget'])
    assert score['exact_offsets'] and score['irrelevant_hits'] == 0
    assert score['covered_required_spans'] == 0 and score['semantic_support'] == 'unknown'
    wrong = text.index('Buffer')
    score = review.score_output(c, refs, hit_output(c, refs, wrong, len(text)), p['budget'])
    assert score['covered_required_spans'] == 0 and score['hits_without_required_evidence_overlap'] == 1


def test_full_conflict_requires_both_sides_and_explicit_missing_is_positive_evidence():
    p = packet(); c = p['cases'][0]; refs = {'main': 'source'}
    second = 'Parameter k = 0.7.'
    text = c['sources'][0]['pages'][0]['text'] + '\n'+second
    c['sources'][0]['pages'][0]['text'] = text
    c['anchors'][0]['role'] = 'conflict_candidate'
    c['anchors'].append({'id': 'other', 'source_id': 'main', 'page': 1, 'start': text.index(second),
                         'end': len(text), 'quote': second, 'role': 'conflict_candidate'})
    c['expectation'] = 'conflicting'
    one = review.score_output(c, refs, hit_output(c, refs, 0, c['anchors'][0]['end']), p['budget'])
    both = review.score_output(c, refs, hit_output(c, refs, 0, len(text)), p['budget'])
    assert one['covered_required_spans'] == 1 and not one['conflict_complete'] and both['conflict_complete']
    missing = {'id': 'm', 'category': 'missing', 'query': 'k', 'sources': [{'id': 'main', 'pages': [{'number': 1, 'text': 'k not reported.'}]}],
               'anchors': [{'source_id': 'main', 'page': 1, 'start': 0, 'end': 15, 'quote': 'k not reported.'}], 'annotation_note': 'control'}
    c = review.build_packet({'cases': [missing]})['cases'][0]
    scores = review.score_output(c, refs, hit_output(c, refs, 0, 15), p['budget'])
    assert scores['explicit_missing_retrieved'] and scores['covered_required_spans'] == 1


@pytest.mark.parametrize('defect', ['foreign', 'boolean_offset', 'duplicate_hit', 'wrong_text', 'boolean_count'])
def test_scorer_rejects_invalid_citations_and_counts(defect):
    p = packet(); c = p['cases'][0]; refs = {'main': 'source'}
    out = hit_output(c, refs, 0, c['anchors'][0]['end'])
    if defect == 'foreign': out['results'][0]['source_ref'] = 'foreign'
    if defect == 'boolean_offset': out['results'][0]['start'] = False
    if defect == 'duplicate_hit':
        out['results'].append(deepcopy(out['results'][0])); out['returned_chars'] *= 2
    if defect == 'wrong_text': out['results'][0]['text'] = 'invented'
    if defect == 'boolean_count': out['returned_chars'] = True
    with pytest.raises(ValueError): review.score_output(c, refs, out, p['budget'])


def test_failure_denominators_and_no_label_data_enter_retrieval(tmp_path, monkeypatch):
    p = packet(); store = ArtifactStore(tmp_path/'store')
    seen = []
    def failing(store, refs, query, **kwargs):
        seen.append((query, kwargs))
        assert kwargs == {'method': 'lexical_v1', **p['budget']}
        assert all(set(store.get(ref)['payload']) == {'pages', 'evaluation_document_id'} for ref in refs)
        raise retrieval.RetrievalError('injected budget failure')
    monkeypatch.setattr(retrieval, 'search_sources', failing)
    result = review.evaluate(p, store, methods=['lexical_v1'])
    scores = result['summary']['lexical_v1']
    assert scores['failed_cases'] == 1 and scores['required_spans'] == 1 and scores['required_span_recall'] == 0
    assert seen and result['semantic_support'] == 'unknown'


def test_empty_cases_have_no_invented_perfect_precision(tmp_path):
    p = packet(); c = p['cases'][0]
    c.update(expectation='not_located', anchors=[], query='absent_token')
    result = review.evaluate(p, ArtifactStore(tmp_path/'store'), repeats=1)
    for score in result['summary'].values():
        assert score['required_span_recall'] is None and score['irrelevant_hit_fraction'] is None


def test_pack_and_cli_review_refusal_are_readable_and_leave_no_output(tmp_path, capsys):
    path = tmp_path/'review'
    review.write_review_pack(packet(), path)
    assert (path/'REVIEW.md').exists() and list((path/'sources').glob('*.txt'))
    output = tmp_path/'forbidden-comparison'
    code = review.main(['evaluate', '--packet', str(path/'packet.json'), '--mode', 'reviewed', '--output', str(output)])
    assert code == 2 and not output.exists()
    assert json.loads(capsys.readouterr().out)['status'] == 'refused'


def test_approved_test_fixture_can_compare_without_becoming_a_holdout(tmp_path):
    result = review.evaluate(approved(packet()), ArtifactStore(tmp_path/'store'), mode='reviewed', repeats=2)
    assert result['status'] == 'reviewed_development_comparison' and result['usage'] == 'development_only'
    assert all(s['covered_required_spans'] == 1 for s in result['summary'].values())


def test_synthetic_seen_status_cannot_be_laundered():
    p = packet(); p['exposure'] = 'not_established'
    with pytest.raises(ValueError, match='seen exposure'):
        review.validate_packet(p)


def test_all_excluded_is_not_a_perfect_empty_comparison():
    p = approved(packet()); p['cases'][0]['review']['status'] = 'excluded'
    with pytest.raises(ValueError, match='no included cases'):
        review.gate(p, 'reviewed')


@pytest.mark.parametrize('args', [['--repeats', '0'], ['--methods', 'lexical_v1', 'lexical_v1']])
def test_invalid_run_configuration_refused_before_output_creation(tmp_path, args, capsys):
    path = tmp_path/'pack'; review.write_review_pack(packet(), path)
    output = tmp_path/'invalid'
    assert review.main(['evaluate', '--packet', str(path/'packet.json'), '--output', str(output), *args]) == 2
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)['status'] == 'refused'


def test_invalid_citation_counts_as_error_without_dropping_evidence_denominator(tmp_path, monkeypatch):
    p = packet()
    def fake(store, refs, query, **kwargs):
        return {'query': query, 'method': kwargs['method'], 'results': [
            {'source_ref': 'foreign', 'page': 1, 'start': 0, 'end': 1, 'text': 'P'}], 'returned_chars': 1}
    monkeypatch.setattr(retrieval, 'search_sources', fake)
    result = review.evaluate(p, ArtifactStore(tmp_path/'store'), repeats=1)
    for scores in result['summary'].values():
        assert scores['invalid_citation_cases'] == 1 and scores['citation_valid_fraction'] == 0
        assert scores['required_spans'] == 1 and scores['covered_required_spans'] == 0


def test_identical_text_in_distinct_documents_does_not_collapse_source_identity(tmp_path):
    p = packet(); c = p['cases'][0]
    c['sources'].append(dict(deepcopy(c['sources'][0]), id='supplement', role='supplement'))
    result = review.evaluate(p, ArtifactStore(tmp_path/'store'), methods=['lexical_v1'], repeats=1)
    row = result['rows'][0]
    assert row['status'] == 'ok'
    assert len({h['source_ref'] for h in row['output']['results']}) == 2


def test_partial_approved_review_still_requires_all_case_decisions(tmp_path):
    p = approved(packet()); c = deepcopy(p['cases'][0]); c['id'] = 'pending'
    c['review'] = packet()['cases'][0]['review']; p['cases'].append(c)
    with pytest.raises(ValueError, match='review gate'):
        review.evaluate(p, ArtifactStore(tmp_path/'store'), mode='reviewed')


def test_input_json_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path):
    path = tmp_path/'bad.json'
    for text in ['{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}']:
        path.write_text(text)
        with pytest.raises(ValueError): review._read(path)


def test_actual_review_document_displays_condition_scope_warning(tmp_path):
    p = review.build_packet(json.loads((ROOT/'benchmarks/inputs/retrieval-development/cases.json').read_text()))
    review.write_review_pack(p, tmp_path/'pack')
    assert 'condition A/B statements may be compatible' in (tmp_path/'pack/REVIEW.md').read_text()


def test_timing_median_averages_even_samples(tmp_path):
    p = packet(); result = review.evaluate(p, ArtifactStore(tmp_path/'store'), methods=['lexical_v1'], repeats=1)
    rows = result['rows']; rows[0]['retrieval_ms'] = [1., 9.]
    assert review._summary(rows)['retrieval_ms_median'] == 5.


def test_source_page_cannot_smuggle_annotations_in_extra_metadata():
    p = packet(); source = p['cases'][0]['sources'][0]
    source['pages'][0]['reviewer_answer'] = 'PRIVATE_ANNOTATION_MUST_NOT_ENTER_SOURCE_STORE'
    source['pages_sha256'] = review.digest(source['pages'])
    with pytest.raises(ValueError, match='page fields'):
        review.validate_packet(p)
