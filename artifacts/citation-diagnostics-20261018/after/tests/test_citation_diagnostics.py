"""Ambiguous evidence is diagnosable, never selected or silently repaired."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, agent_tools, evidence
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


TEXT = ('Earlier table: Initial x = 1. Parameter k = 0.4.\n'
        'Confirmed table: Initial x = 1. Parameter k = 0.4.\n'
        'Equation dx/dt = -k*x.\n')


def fixture(tmp_path, text=TEXT):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text(text)
    source = evidence.ingest_source(store, path)
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),))
    data = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}
    return store, source, data


class Scripted:
    last_usage = {}

    def __init__(self, actions):
        self.actions, self.calls = iter(actions), []

    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        action = next(self.actions)
        return action(messages) if callable(action) else action


def act(action, args=None):
    return {'action': action, 'args': args or {}}


def test_all_ambiguous_quotes_reported_without_partial_claim_creation(tmp_path):
    store, source, data = fixture(tmp_path)
    before = deepcopy(data)
    result = agent_tools.submit_draft(store, data, [source])
    assert result['state'] == 'invalid'
    assert data == before
    assert not store.list('claim') and not store.list('model') and not store.list('run')
    assert len(result['problems']) == 2
    diagnostics = result['citation_diagnostics']
    assert [d['target'] for d in diagnostics] == ['x/initial', 'k/value']
    for diagnostic in diagnostics:
        quote = next(c['quote'] for c in data['evidence'] if c['target'] == diagnostic['target'])
        assert diagnostic['source_ref'] == source and diagnostic['page'] == 1
        assert diagnostic['occurrence_count'] == 2 and diagnostic['count_is_exact']
        for candidate in diagnostic['candidates']:
            assert TEXT[candidate['start']:candidate['end']] == quote
            assert TEXT[candidate['context_start']:candidate['context_end']] == candidate['context']
            assert len(candidate['context']) <= 240


def test_unknown_quote_has_no_invented_candidate(tmp_path):
    store, source, data = fixture(tmp_path)
    data['evidence'][0]['quote'] = 'Initial x = 100.'
    result = agent_tools.submit_draft(store, data, [source])
    diagnostic = result['citation_diagnostics'][0]
    assert diagnostic['kind'] == 'quote_not_found' and diagnostic['occurrence_count'] == 0
    assert diagnostic['count_is_exact'] and diagnostic['candidates'] == []
    assert result['state'] == 'invalid' and not store.list('claim')


def test_overlapping_occurrences_are_ambiguous_too(tmp_path):
    store, source, data = fixture(tmp_path, TEXT + 'aaaa\n')
    data['evidence'] = [{'target': 'rule:x/expression', 'source_ref': source, 'page': 1, 'quote': 'aaa'}]
    result = agent_tools.submit_draft(store, data, [source])
    diagnostic = result['citation_diagnostics'][0]
    starts = [c['start'] for c in diagnostic['candidates']]
    assert diagnostic['occurrence_count'] == 2 and starts[1] == starts[0] + 1
    assert not store.list('claim') and not store.list('model')


def test_large_repeated_text_has_bounded_context_and_a_lower_bound_count(tmp_path):
    store, source, data = fixture(tmp_path, TEXT + ('marker ' * 100000))
    data['evidence'] = [{'target': 'rule:x/expression', 'source_ref': source, 'page': 1, 'quote': 'marker'}]
    diagnostic = agent_tools.submit_draft(store, data, [source])['citation_diagnostics'][0]
    assert not diagnostic['count_is_exact'] and diagnostic['occurrence_count'] == 6
    assert len(diagnostic['candidates']) == 5
    assert all(len(c['context']) <= 240 for c in diagnostic['candidates'])


@pytest.mark.parametrize('page', [True, '1', 0, 3])
def test_invalid_pages_never_create_claims_or_candidates(tmp_path, page):
    store, source, data = fixture(tmp_path)
    data['evidence'][0]['page'] = page
    result = agent_tools.submit_draft(store, data, [source])
    assert result['state'] == 'invalid' and result['problems']
    assert not store.list('claim') and not result['citation_diagnostics']


def test_unauthorized_source_is_not_read_for_context(tmp_path):
    store, source, data = fixture(tmp_path)
    secret = tmp_path / 'outside.txt'; secret.write_text('PRIVATE-CONTEXT marker marker')
    outside = evidence.ingest_source(store, secret)
    data['evidence'][0].update(source_ref=outside, quote='marker')
    result = agent_tools.submit_draft(store, data, [source])
    assert result['state'] == 'invalid' and not result['citation_diagnostics']
    assert 'PRIVATE-CONTEXT' not in json.dumps(result)
    assert not store.list('claim')


@pytest.mark.parametrize('defect', ['negative_start', 'past_end', 'invalid_type'])
def test_late_malformed_citation_does_not_leave_partial_claims(tmp_path, defect):
    store, source, data = fixture(tmp_path)
    # Make the early citations valid, so only the final entry is defective.
    for citation in data['evidence'][:2]:
        citation['quote'] = 'Confirmed table: Initial x = 1. Parameter k = 0.4.'
    final = data['evidence'][-1]
    if defect == 'invalid_type':
        final['claim_type'] = 'model_verified'
    else:
        final.pop('quote')
        final.update(start=-1 if defect == 'negative_start' else 0, end=len(TEXT)+1)
    result = agent_tools.submit_draft(store, data, [source])
    assert result['state'] == 'invalid' and result['problems']
    assert not store.list('claim') and not store.list('model')


def test_one_budgeted_repair_can_disambiguate_multiple_targets_and_run_real_solver(tmp_path):
    store, source, data = fixture(tmp_path)

    def choose_confirmed_occurrences(messages):
        feedback = json.loads(messages[-1]['content'])
        assert feedback['state'] == 'invalid'
        assert not store.list('claim')
        repaired = deepcopy(data)
        for diagnostic in feedback['citation_diagnostics']:
            citation = next(c for c in repaired['evidence'] if c['target'] == diagnostic['target'])
            # This is a scripted test actor's choice, not an automatic tool repair.
            chosen = diagnostic['candidates'][-1]
            citation.pop('quote')
            citation.update(start=chosen['start'], end=chosen['end'])
        return act('submit_draft', repaired)

    provider = Scripted([act('submit_draft', data), choose_confirmed_occurrences, act('run'), act('finish')])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.))))['payload']
    assert result['state'] == 'completed' and result['call_count'] == 4 and result['repair_count'] == 1
    first = next(e for e in result['events'] if e.get('tool') == 'submit_draft')
    assert len(first['citation_diagnostics']) == 2
    sim = store.get(result['run_ref'])['payload']
    assert sim['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)
    assert sim['scientific_status'] == 'not_verified'


def test_ignoring_diagnostics_stops_at_the_original_repair_budget(tmp_path):
    store, source, data = fixture(tmp_path)
    provider = Scripted([act('submit_draft', data)] * 3)
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)),
                                      max_repairs=1))['payload']
    assert result['state'] == 'incomplete' and result['call_count'] == 2
    assert result['repair_count'] == 2
    assert not store.list('claim') and not store.list('model') and not store.list('run')
