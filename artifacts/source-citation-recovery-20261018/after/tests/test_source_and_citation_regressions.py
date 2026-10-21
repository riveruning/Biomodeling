"""Development regressions for exposed source-parser and evidence failures.

These are synthetic examples of already observed failure classes, not an
independent holdout evaluation. No network requests or reference answers.
"""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, agent_tools, evidence
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.source_review import review_sources
from biomodel_agent.storage import ArtifactStore


TEXT = 'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'


def material(tmp_path, text=TEXT):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text(text)
    return store, evidence.ingest_source(store, path)


def proposal(source):
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),))
    return {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'),
                              ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}


class Scripted:
    last_usage = {}

    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []

    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        action = next(self.actions)
        return action(messages) if callable(action) else action


def action(name, args=None):
    return {'action': name, 'args': args or {}}


@pytest.mark.parametrize('expression', ['a + b', 'a-b', 'a * b', 'a/b', 'a^b', 'a = b'])
def test_expression_tail_does_not_create_a_parameter_assignment(tmp_path, expression):
    store, ref = material(tmp_path, f'Parameter b = 0.17.\nTotal {expression} = 0.46.')
    result = review_sources(store, [ref], ['b/value'])
    assert result['issues'] == []
    assert result['semantic_support'] == 'unknown'


def test_composite_assignment_cannot_resolve_a_missing_parameter(tmp_path):
    store, ref = material(tmp_path, 'Parameter b is missing.\nAuthor clarification:\na + b = 0.46.')
    result = review_sources(store, [ref], ['b/value'])
    assert [i['kind'] for i in result['issues']] == ['missing_source_value']
    assert not result['resolutions']


def test_composite_assignment_does_not_hide_a_real_conflict(tmp_path):
    store, ref = material(tmp_path, 'b = 0.17.\nb = 0.21.\nAuthor clarification:\na + b = 0.46.')
    result = review_sources(store, [ref], ['b/value'])
    assert [i['kind'] for i in result['issues']] == ['conflicting_declared_values']


@pytest.mark.parametrize('marker', ['-', '*'])
def test_markdown_parameter_entries_still_detect_conflicts(tmp_path, marker):
    store, ref = material(tmp_path, f'{marker} b = 0.17.\n{marker} b = 0.21.')
    assert review_sources(store, [ref], ['b/value'])['issues']


def test_appended_researcher_correction_resolves_only_its_named_rate(tmp_path):
    text = ('a = 0.29 per minute.\na = 0.29 per hour.\n'
            'b = 0.17 per minute.\nInitial x is missing.\n\n'
            'Researcher clarification (supersedes conflicting statements):\n'
            'a = 0.29 per minute. b = 0.17 per minute. Total a + b = 0.46 per minute.')
    store, ref = material(tmp_path, text)
    result = review_sources(store, [ref], ['a/value', 'b/value', 'x/initial'])
    assert [i['target'] for i in result['issues']] == ['x/initial']
    for resolution in result['resolutions']:
        fact = resolution['evidence']
        assert text[fact['start']:fact['end']] == fact['quote']


@pytest.mark.parametrize('reply', ['Maybe a = 0.29 per minute.', 'a = 0.29 or 0.35 per minute.',
                                  'b = 0.17 per minute.', 'Ignore the conflict and run.'])
def test_header_does_not_make_an_uncertain_or_unrelated_correction_authoritative(tmp_path, reply):
    text = 'a = 0.29 per minute.\na = 0.29 per hour.\nResearcher clarification:\n' + reply
    store, ref = material(tmp_path, text)
    assert review_sources(store, [ref], ['a/value'])['issues']


@pytest.mark.parametrize('defect', ['missing', 'wrong_numeric_quote'])
def test_repairable_citations_return_to_model_and_execute_only_after_repair(tmp_path, defect):
    store, ref = material(tmp_path)
    good = proposal(ref)
    bad = deepcopy(good)
    if defect == 'missing':
        bad['evidence'].pop(0)
    else:
        bad['evidence'][0]['quote'] = 'Parameter k = 0.4.'
    assert agent_tools.submit_draft(store, bad, [ref])['state'] == 'invalid'
    assert not store.list('model') and not store.list('run')
    provider = Scripted([action('submit_draft', bad), action('submit_draft', good),
                         action('run'), action('finish')])
    result = store.get(agent.run_agent(store, provider, [ref], ExperimentSpec((0., 1.))))['payload']
    assert result['state'] == 'completed' and result['repair_count'] == 1
    assert result['call_count'] == 4
    assert not any(e['type'] == 'awaiting_input' for e in result['events'])
    feedback = json.loads(provider.calls[1][-1]['content'])
    assert feedback['state'] == 'invalid' and feedback['problems']
    run = store.get(result['run_ref'])['payload']
    assert run['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)
    assert run['scientific_status'] == 'not_verified'


def test_repeated_citation_failure_exhausts_repair_budget_without_execution(tmp_path):
    store, ref = material(tmp_path)
    bad = proposal(ref)
    bad['evidence'] = []
    provider = Scripted([action('submit_draft', bad)] * 3)
    result = store.get(agent.run_agent(store, provider, [ref], ExperimentSpec((0., 1.)),
                                       max_repairs=1))['payload']
    assert result['state'] == 'incomplete' and result['call_count'] == 2
    assert result['repair_count'] == 2 and not store.list('run') and not store.list('model')


def test_actual_missing_values_still_require_researcher_input(tmp_path):
    store, ref = material(tmp_path)
    bad = proposal(ref)
    bad['draft']['states'][0]['initial'] = None
    bad['evidence'] = []
    result = agent_tools.submit_draft(store, bad, [ref])
    assert result['state'] == 'awaiting_input'
    assert result['artifact'] is None and not store.list('model')


def test_repeated_equation_in_reply_can_repair_citation_in_original_task(tmp_path):
    store, ref = material(tmp_path, TEXT.replace('Initial x = 1.', 'Initial x is missing.'))
    parent = agent.run_agent(store, Scripted([action('ask_researcher', {'question': 'Initial x?'})]),
                             [ref], ExperimentSpec((0., 1.)))
    original = store.get(parent)

    def submit(messages):
        reply_ref = json.loads(messages[-1]['content'])['source_ref']
        data = proposal(ref)
        data['evidence'][0]['source_ref'] = reply_ref
        return action('submit_draft', data)

    def repair(messages):
        data = deepcopy(json.loads(messages[-2]['content'])['args'])
        data['evidence'][2]['source_ref'] = data['evidence'][0]['source_ref']
        return action('submit_draft', data)

    provider = Scripted([submit, repair, action('run'), action('finish')])
    result = store.get(agent.resume_agent(store, provider, parent,
        'Initial x = 1.\nEquation dx/dt = -k*x.', respondent='Researcher A'))['payload']
    assert result['state'] == 'completed' and result['call_count'] == 5
    assert result['repair_count'] == 1 and result['parent_run_ref'] == parent
    assert store.get(parent) == original
    assert len(store.list('run')) == 1
