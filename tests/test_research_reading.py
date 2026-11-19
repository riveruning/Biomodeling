"""Normal project reading operations preserve source scope and provider receipts."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import bundles, research_projects as projects, research_supervisor as supervisor
from biomodel_agent import research_reading as reading
from biomodel_agent.storage import ArtifactStore
from test_research_background import (METHOD, RESULT, arguments, fetched,
                                       indexed, source, DISC_PMID)


def claim(packet, **changes):
    chunk = packet['next_chunk']
    evidence = []
    for segment in chunk['segments']:
        if METHOD in segment['text']:
            evidence.append(dict(role='methods', segment_id=segment['id']))
        if RESULT in segment['text']:
            evidence.append(dict(role='results', segment_id=segment['id']))
    args = dict(reading_ref=packet['reading_ref'], chunk_id=chunk['chunk_id'], chunk_reviewed=True,
                study_type='experimental', mathematical_modeling='absent',
                rationale='Assessed the supplied primary-article text and ordinary statistics.', evidence=evidence)
    args.update(changes)
    return args


def test_normal_reading_compact_review_evidence_and_export(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    assert packet['summary']['fulltext_reviewed'] is False and packet['next_chunk']
    saved = deepcopy(store.get(packet['reading_ref']))
    args = claim(packet)
    final = projects.operate(store, project, 'record_expt_chunk', args, key='first-chunk')
    assert final['next_chunk'] is None
    assert final['summary']['status'] == 'candidate_agent_assessed'
    assert final['summary']['fulltext_reviewed'] is True
    assert store.get(packet['reading_ref']) == saved
    assert projects.operate(store, project, 'record_expt_chunk', args, key='first-chunk') == final
    detail = projects.operate(store, project, 'inspect_expt_reading', dict(reading_ref=final['reading_ref']))
    results = [item['evidence_id'] for item in detail['evidence'] if item['role'] == 'results']
    review_args = arguments(index, mm, expt)
    review_args['reviews'] = [dict(reading_ref=final['reading_ref'], reference_id='R1', coverage=[
        dict(requirement_id='persistence', explanation='Recorded result supports persistence.', evidence_ids=results)])]
    result = projects.operate(store, project, 'review_expt_background', review_args)
    assert result['qualified_count'] == 1 and result['status'] == 'complete'
    assert final['reading_ref'] in store.get(result['review_ref'])['refs']
    manifest = bundles.export_bundle(store, projects.snapshot(store, project), tmp_path/'bundle')
    assert final['reading_ref'] in manifest['objects_and_blobs']
    assert store.get(result['review_ref'])['payload']['reviews'][0]['coverage'][0]['evidence'][0]['quote'] == RESULT


def test_discovery_selection_recovered_from_original_source_receipts(tmp_path):
    env = fetched(tmp_path)
    packet = projects.operate(env['store'], env['project'], 'start_expt_reading',
                               dict(source_ref=env['fetch']['source_ref']))
    assert packet['discovery_options'] == [dict(search_ref=env['search']['search_ref'],
        publication_source='MED', publication_id=DISC_PMID)]


def test_completed_reading_coordinator_keeps_background_context_within_default_budget(tmp_path):
    from biomodel_agent import context
    from test_research_projects import frame

    store, project, mm, expt, index = indexed(tmp_path)
    for number in range(6):
        extra = source(store, f'10.1234/extra{number}',
                       f'<sec><title>Results</title><p>Additional study {number}.</p></sec>')
        projects.operate(store, project, 'admit_source',
                         dict(source_ref=extra, reason='Background candidate.'), actor='operator')
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk', claim(packet))
    messages = supervisor._messages(store, project)
    assert context.size(messages) <= 60000
    frozen = json.loads(messages[1]['content'])
    state = json.loads(messages[-1]['content'])
    project_data = projects.load(store, project)
    assert frozen['scenario'] == project_data['scenario']
    assert frozen['constraints'] == project_data['constraints']
    assert frozen['policy'] == project_data['policy']
    assert frozen['prepared_models'] == []
    assert frozen['reference_refs'] == project_data['reference_refs']
    assert frozen['initial_source_refs'] == [mm, expt]
    assert len(state['source_catalog']) == 8
    assert index in state['project']['background_index_refs']
    assert final['reading_ref'] in state['project']['background_reading_refs']
    assert 'revise_frame' in state['action_schemas']
    deferred = {'native_model_schema_examples', 'ode_decision_plan_example',
                'capabilities', 'scientific_procedures'}
    assert deferred.isdisjoint(frozen)

    projects.operate(store, project, 'revise_frame',
                     dict(frame=frame(), reason='Proceed to the modeling stage.'), actor='operator')
    modeled_messages = supervisor._messages(store, project)
    assert context.size(modeled_messages) <= 60000


def test_incomplete_reading_and_early_mathematical_exclusion(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    large = source(store, '10.1234/expt', '<sec><title>Methods</title><p>' + METHOD + ' ' + 'a'*34000 +
                   '</p></sec><sec><title>Results</title><p>' + RESULT + '</p></sec>')
    projects.operate(store, project, 'admit_source', dict(source_ref=large, reason='Read the entire source.'), actor='operator')
    first = projects.operate(store, project, 'start_expt_reading', dict(source_ref=large))
    assert first['summary']['total_chunks'] >= 3
    partial = projects.operate(store, project, 'record_expt_chunk', claim(first))
    assert partial['summary']['fulltext_reviewed'] is False and partial['next_chunk']
    with pytest.raises(ValueError, match='latest'):
        projects.operate(store, project, 'record_expt_chunk', claim(first, rationale='Conflicting stale revision.'))
    excluded = projects.operate(store, project, 'record_expt_chunk',
        claim(partial, mathematical_modeling='present', evidence=[]))
    assert excluded['summary']['status'] == 'excluded_agent_assessed'
    assert excluded['summary']['fulltext_reviewed'] is False and excluded['next_chunk'] is None
    args = arguments(index, mm, large)
    args['reviews'] = [dict(reading_ref=excluded['reading_ref'], reference_id='R1')]
    result = projects.operate(store, project, 'review_expt_background', args)
    assert result['excluded_count'] == 1 and result['qualified_count'] == 0


@pytest.mark.parametrize('defect', ['unknown_segment', 'wrong_chunk', 'foreign_project', 'override_flags'])
def test_reading_scopes_and_derived_classification_are_enforced(tmp_path, defect):
    store, project, mm, expt, index = indexed(tmp_path)
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    args = claim(packet)
    if defect == 'unknown_segment':
        args['evidence'] = [dict(role='results', segment_id='invented')]
    elif defect == 'wrong_chunk':
        args['chunk_id'] = '0'*64
    elif defect == 'foreign_project':
        project = projects.create(store, dict(scenario='Other project.', source_refs=[expt]))
    else:
        compact = arguments(index, mm, expt)
        compact['reviews'] = [dict(reading_ref=packet['reading_ref'], reference_id='R1', fulltext_reviewed=True)]
        with pytest.raises(ValueError):
            projects.operate(store, project, 'review_expt_background', compact)
        return
    with pytest.raises(ValueError):
        projects.operate(store, project, 'record_expt_chunk', args)
    assert projects.inspect(store, project)['state'] == 'ready'


def test_unread_chunk_receipt_keeps_pending_without_looping(tmp_path):
    store, project, _, expt, _ = indexed(tmp_path)
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk',
        claim(packet, chunk_reviewed=False, study_type='uncertain', mathematical_modeling='uncertain', evidence=[]))
    assert final['recorded_chunks'] == 1 and final['summary']['reviewed_chunks'] == 0
    assert final['next_chunk'] is None and final['summary']['status'] == 'pending_review'


def test_normal_supervisor_records_focused_prompt_response_and_usage(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    expt = source(store, '10.1234/expt', f'<sec><title>Methods</title><p>{METHOD}</p></sec>'
                  f'<sec><title>Results</title><p>{RESULT}</p></sec>')
    project = projects.create(store, dict(scenario='Review the experimental source.', source_refs=[expt],
                                         policy=dict(planner_max_calls=1)))
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))

    class Provider:
        last_usage = {}

        def respond(self, messages):
            self.messages = deepcopy(messages)
            data = json.loads(messages[-1]['content'])
            assert data['reading'] == packet
            assert data['action_schema']['properties']['action']['const'] == 'record_expt_chunk'
            self.last_usage = dict(prompt_tokens=10, completion_tokens=13, total_tokens=23)
            return dict(action='record_expt_chunk', args=claim(packet))

    provider = Provider()
    supervisor.run(store, provider, project)
    view = projects.inspect(store, project)
    assert len(view['background_reading_refs']) == 2
    assert supervisor.usage(store, project)['reported_total_tokens'] == 23
    response = store.get(store.list(projects.RESPONSE)[0])['payload']
    request = store.get(response['request_ref'])['payload']
    assert request['messages'] == provider.messages
    assert reading.focused_packet(store, project, view) is None


def test_focused_prompt_includes_literal_selection_rejection(tmp_path):
    store, project, _, expt, _ = indexed(tmp_path)
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    with pytest.raises(ValueError):
        projects.operate(store, project, 'record_expt_chunk', claim(packet, evidence=[dict(role='results',segment_id='bad')]))
    data = json.loads(supervisor._messages(store, project)[-1]['content'])
    assert data['previous_rejection']['code'] == 'ValueError'
    assert data['reading']['reading_ref'] == packet['reading_ref']


def test_start_existing_reading_returns_latest_revision(tmp_path):
    store, project, _, expt, _ = indexed(tmp_path)
    first = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk', claim(first))
    repeated = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    assert repeated == final
    assert projects.inspect(store, project)['background_reading_refs'] == [first['reading_ref'], final['reading_ref']]


def test_provider_http_status_is_retained_with_pending_reading(tmp_path, monkeypatch):
    import httpx
    from biomodel_agent.llm import ChatProvider
    store, _, _, expt, _ = indexed(tmp_path)
    project = projects.create(store, dict(scenario='Read the admitted article.', source_refs=[expt],
                                         policy=dict(planner_max_calls=1)))
    projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    monkeypatch.setenv('TEST_READING_KEY', 'test-key-value')
    provider = ChatProvider(model='unit-test', base_url='https://provider.invalid/v1',
        api_key_env='TEST_READING_KEY', transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text='private gateway body')))
    supervisor.run(store, provider, project)
    failure = store.get(store.list(projects.PROVIDER_FAILURE)[0])['payload']
    assert failure['error_kind'] == 'http' and failure['response_diagnostic'] == {'http_status': 503}
    assert projects.inspect(store, project)['state'] == 'needs_recovery'
    assert not store.list(projects.RESPONSE)
    raw = ''.join(path.read_text() for path in store.objects_dir.glob('*.json'))
    assert 'private gateway body' not in raw and 'test-key-value' not in raw


def unheaded_source(tmp_path, *, intro_heading=False, large=False, result_heading=None):
    from biomodel_agent import evidence
    from test_background_html import article
    store, project, mm, _, index = indexed(tmp_path)
    paragraph = f'<p>{RESULT}</p>'
    if intro_heading:
        paragraph = '<section><h2>Introduction</h2>' + paragraph + '</section>'
    if result_heading:
        paragraph = f'<section><h2>{result_heading}</h2>' + paragraph + '</section>'
    if large:
        paragraph += '<p>' + 'Unassessed body text. ' * 700 + '</p>'
    raw = article('10.1234/expt', 'PMC222', paragraph +
                  f'<section><h2>Methods</h2><p>{METHOD}</p></section>')
    path = tmp_path/'unheaded.html'
    path.write_bytes(raw)
    expt = evidence.ingest_source(store, path)
    projects.operate(store, project, 'admit_source',
                     dict(source_ref=expt, reason='Review unheaded primary findings.'), actor='operator')
    return store, project, mm, expt, index


def test_unheaded_findings_require_full_reading_and_record_explicit_assessment(tmp_path):
    from biomodel_agent import research_background as background
    store, project, mm, expt, index = unheaded_source(tmp_path)
    original = deepcopy(store.get(expt))
    document = background._document(store, original['payload'])
    assert any(row['code'] == 'inferred_unheaded_introduction' for row in document['issues'])
    literal = arguments(index, mm, expt)
    before = projects.operate(store, project, 'review_expt_background', literal)
    assert before['pending_count'] == 1 and before['qualified_count'] == 0
    original_review = deepcopy(store.get(before['review_ref']))
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk', claim(packet))
    assert final['summary']['fulltext_reviewed']
    detail = projects.operate(store, project, 'inspect_expt_reading', dict(reading_ref=final['reading_ref']))
    ids = [row['evidence_id'] for row in detail['evidence'] if row['role'] == 'results']
    compact = arguments(index, mm, expt)
    compact['reviews'] = [dict(reading_ref=final['reading_ref'], reference_id='R1', coverage=[
        dict(requirement_id='persistence', explanation='Recorded primary findings.', evidence_ids=ids)])]
    saved_input = deepcopy(compact)
    reviewed = projects.operate(store, project, 'review_expt_background', compact)
    assert reviewed['qualified_count'] == 1 and reviewed['status'] == 'complete'
    saved = store.get(reviewed['review_ref'])['payload']
    assert saved['reviews'][0]['results_section_assessment'] == 'agent_assessed_body_results'
    assert saved['reviews'][0]['results_section_evidence']
    assert saved['scientific_support'] == 'unknown' and saved['independent_review'] == 'pending'
    assert saved['reviews'][0]['coverage'][0]['evidence_in_experimental_sections']
    result_anchor = next(row for row in detail['evidence'] if row['role'] == 'results')
    joint = dict(requirement_id='persistence', support='partial',
                 explanation='Keep the narrower joint judgment explicit.', evidence=[dict(source_ref=expt,
                     **{key: result_anchor[key] for key in ('page', 'start', 'end', 'quote')})])
    revised = projects.operate(store, project, 'review_expt_background',
                               dict(compact, requirement_reviews=[joint]))
    assert revised['partial_requirement_ids'] == ['persistence']
    assert revised['uncovered_requirement_ids'] == ['persistence']
    assert compact == saved_input
    assert store.get(expt) == original and store.get(before['review_ref']) == original_review
    assert background._document(store, original['payload']) == document
    # A literal review cannot supply the reader's immutable role assessment.
    assert projects.operate(store, project, 'review_expt_background', literal)['pending_count'] == 1
    manifest = bundles.export_bundle(store, projects.snapshot(store, project), tmp_path/'bundle')
    assert {final['reading_ref'], expt, original['payload']['blob_ref'], reviewed['review_ref']} <= set(manifest['objects_and_blobs'])


@pytest.mark.parametrize('defect', ['headed_intro', 'partial', 'modeling', 'uncertain', 'other_role'])
def test_unheaded_reading_does_not_promote_incomplete_or_other_evidence(tmp_path, defect):
    store, project, mm, expt, index = unheaded_source(
        tmp_path, intro_heading=defect == 'headed_intro', large=defect == 'partial')
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    args = claim(packet)
    if defect == 'modeling':
        args['mathematical_modeling'] = 'present'
    if defect == 'uncertain':
        args['mathematical_modeling'] = 'uncertain'
    if defect == 'other_role':
        args['evidence'] = [dict(row, role='other') if row['role'] == 'results' else row
                            for row in args['evidence']]
    final = projects.operate(store, project, 'record_expt_chunk', args)
    review = arguments(index, mm, expt)
    review['reviews'] = [dict(reading_ref=final['reading_ref'], reference_id='R1')]
    result = projects.operate(store, project, 'review_expt_background', review)
    assert result['qualified_count'] == 0
    assert result['excluded_count' if defect == 'modeling' else 'pending_count'] == 1
    if defect == 'partial':
        assert not final['summary']['fulltext_reviewed']


@pytest.mark.parametrize('defect', ['format', 'warning', 'heading', 'path', 'figure',
                                   'page', 'start', 'end', 'quote', 'empty'])
def test_unheaded_anchor_helper_requires_exact_paragraph_spans(tmp_path, defect):
    from biomodel_agent import research_background as background
    store, _, _, expt, _ = unheaded_source(tmp_path)
    document = background._document(store, store.get(expt)['payload'])
    block = next(row for row in document['blocks'] if row['text'] == RESULT)
    anchor = dict(block['source_spans'][0], source_ref=expt)
    assert background._reading_result_anchor(document, anchor)
    if defect == 'format':
        document['format'] = 'jats'
    elif defect == 'warning':
        document['issues'] = []
    elif defect == 'heading':
        block['section_titles'] = ['Introduction']
    elif defect == 'path':
        block['locator']['path'] = '/html[1]/p1'
    elif defect == 'figure':
        block['locator']['path'] = '/html[1]/figure[1]/p[1]'
    elif defect == 'page':
        anchor['page'] += 1
    elif defect == 'start':
        anchor['start'] -= 1
    elif defect == 'end':
        anchor['end'] += 1
    elif defect == 'quote':
        anchor['quote'] = 'A different result.'
    else:
        anchor['quote'] = ''
    assert not background._reading_result_anchor(document, anchor)


@pytest.mark.parametrize('heading,qualified', [
    ('Plasmids are unstable in the tested host', True),
    ('Experimental procedures', False), ('Acknowledgements', False),
])
def test_completed_reader_can_bind_topical_results_headings(tmp_path, heading, qualified):
    store, project, mm, expt, index = unheaded_source(tmp_path, result_heading=heading)
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk', claim(packet))
    args = arguments(index, mm, expt)
    args['reviews'] = [dict(reading_ref=final['reading_ref'], reference_id='R1')]
    reviewed = projects.operate(store, project, 'review_expt_background', args)
    assert reviewed['qualified_count'] == int(qualified)
    assert reviewed['pending_count'] == int(not qualified)
    if qualified:
        saved = store.get(reviewed['review_ref'])['payload']['reviews'][0]
        assert saved['results_section_assessment'] == 'agent_assessed_body_results'
