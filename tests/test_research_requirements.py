"""Introduction-focused requirement derivation retains normal review validation."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import research_background as background, research_projects as projects
from biomodel_agent import research_supervisor as supervisor
from test_research_background import arguments, indexed, source
from test_research_provider_integration import adapter


def packet(store, project):
    return background.requirements_packet(store, project, projects.inspect(store, project))


def test_all_indexed_intro_anchors_bind_and_review_completion_leaves_focus(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    before = deepcopy(store.get(index))
    supplied = packet(store, project)
    assert supplied['all_indexed_intro_blocks_supplied']
    assert supplied['source_ref'] == mm and supplied['index_ref'] == index
    assert supplied['intro_contexts'][0]['reference_ids'] == ['R1']
    page = store.get(mm)['payload']['pages'][0]['text']
    for row in supplied['intro_contexts']:
        anchor = row['mm_context']
        assert page[anchor['start']:anchor['end']] == anchor['quote']
    messages = supervisor._messages(store, project)
    assert messages[0]['content'] == supervisor.REQUIREMENTS_PROMPT
    assert json.loads(messages[-1]['content'])['background_requirements'] == supplied
    args = arguments(index, mm, expt)
    args['reviews'] = []
    reviewed = projects.operate(store, project, 'review_expt_background', args)
    assert reviewed['qualified_count'] == 0 and reviewed['uncovered_requirement_ids'] == ['persistence']
    assert packet(store, project) is None
    assert supervisor._messages(store, project)[0]['content'] == supervisor.BACKGROUND_PROMPT
    assert store.get(index) == before


@pytest.mark.parametrize('condition', ['long', 'ambiguous'])
def test_large_or_ambiguous_intro_preserves_generic_reading_path(tmp_path, condition):
    store, project, _, _, _ = indexed(tmp_path)
    text = 'Biological background observations. ' * (400 if condition == 'long' else 5)
    body = f'<sec><title>Introduction</title><p>{text}</p></sec>'
    if condition == 'ambiguous':
        body += f'<sec><title>Results</title><p>{text}</p></sec>'
    mm = source(store, '10.1234/fallback', body)
    projects.operate(store, project, 'admit_source', dict(source_ref=mm, reason='Index another MM.'), actor='operator')
    projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))
    assert packet(store, project) is None
    assert supervisor._messages(store, project)[0]['content'] == supervisor.BACKGROUND_PROMPT


def test_long_bound_paragraph_is_supplied_completely_in_legal_quote_sizes(tmp_path):
    store, project, _, _, _ = indexed(tmp_path)
    text = 'Measured biological associations. ' * 200
    mm = source(store, '10.1234/paragraph', f'<sec><title>Introduction</title><p>{text}</p></sec>')
    projects.operate(store, project, 'admit_source', dict(source_ref=mm, reason='Read a long introduction.'), actor='operator')
    index = projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    supplied = packet(store, project)
    full = store.get(index)['payload']['document']['intro_blocks'][0]['text']
    anchors = [row['mm_context'] for row in supplied['intro_contexts']]
    assert all(0 < len(anchor['quote']) <= 4000 for anchor in anchors)
    assert ''.join(anchor['quote'] for anchor in anchors) == full
    assert all(left['end'] == right['start'] for left, right in zip(anchors, anchors[1:]))


def test_each_new_mm_index_has_its_own_pending_requirement_stage(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    args = arguments(index, mm, expt)
    args['reviews'] = []
    projects.operate(store, project, 'review_expt_background', args)
    assert packet(store, project) is None
    other = source(store, '10.1234/other-mm', '<sec><title>Introduction</title><p>Another biological premise.</p></sec>')
    projects.operate(store, project, 'admit_source', dict(source_ref=other, reason='Another MM article.'), actor='operator')
    other_index = projects.operate(store, project, 'index_mm_background', dict(source_ref=other))['index_ref']
    assert packet(store, project)['index_ref'] == other_index
    assert packet(store, project)['source_ref'] == other


def test_real_chat_adapter_saves_model_requirements_and_keeps_gaps(tmp_path, monkeypatch):
    store, _, mm, expt, _ = indexed(tmp_path)
    project = projects.create(store, dict(scenario='Extract prior empirical premises for this MM.',
        mode='reproduction', source_refs=[mm, expt], constraints=['Retain open biological questions.'],
        policy=dict(planner_max_calls=1)))
    index = projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    supplied = packet(store, project)
    anchor = supplied['intro_contexts'][0]['mm_context']
    args = dict(index_ref=index, requirements=[dict(id='persistence',
        question='What prior experiments establish effects on persistence?', mm_context=anchor)], reviews=[])
    provider, requests = adapter(monkeypatch, [dict(action='review_expt_background', args=args,
        reason='The initial needs remain unassessed by experimental sources.')])
    supervisor.run(store, provider, project)
    assert len(requests) == 1 and requests[0]['messages'][0]['content'] == supervisor.REQUIREMENTS_PROMPT
    frozen = json.loads(requests[0]['messages'][1]['content'])
    assert frozen['scenario'] == projects.load(store, project)['scenario']
    assert frozen['constraints'] == ['Retain open biological questions.']
    view = projects.inspect(store, project)
    review = store.get(view['background_review_refs'][-1])['payload']
    assert review['reviews'] == [] and review['uncovered_requirement_ids'] == ['persistence']
    assert review['scientific_support'] == 'unknown' and review['independent_review'] == 'pending'
    assert supervisor.usage(store, project)['reported_total_tokens'] == 12


def test_no_empirical_premises_can_end_with_explicit_gap(tmp_path, monkeypatch):
    store, _, mm, _, _ = indexed(tmp_path)
    project = projects.create(store, dict(scenario='Inspect whether empirical premises can be established.',
        mode='reproduction', source_refs=[mm], policy=dict(planner_max_calls=1)))
    projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))
    provider, _ = adapter(monkeypatch, [dict(action='conclude', args=dict(outcome='insufficient_evidence',
        summary='Empirical premises remain unresolved in this diagnostic response.', assessment_refs=[],
        limitations=['No experimental evidence has been established.']))])
    supervisor.run(store, provider, project)
    view = projects.inspect(store, project)
    assert view['state'] == 'insufficient_evidence' and not view['background_review_refs']
