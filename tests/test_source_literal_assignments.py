"""Partial numeric tokens must not manufacture or resolve scientific facts."""
import pytest

from biomodel_agent import evidence
from biomodel_agent.source_review import review_sources
from biomodel_agent.storage import ArtifactStore


def review(tmp_path, text, target='k/value'):
    path = tmp_path / 'source.txt'
    path.write_text(text)
    store = ArtifactStore(tmp_path / 'store')
    source = evidence.ingest_source(store, path)
    return review_sources(store, [source], [target])


@pytest.mark.parametrize('expression', ['1/2', '1 + 2', '1 - 2', '1*2', '1**2',
                                       '1^2', '1%2', '1e-', '1e', '1.2.3',
                                       '1x', '1 = 2'])
def test_numeric_prefix_of_an_expression_is_not_a_new_parameter_fact(tmp_path, expression):
    result = review(tmp_path, f'Parameter k = 0.5.\nExpression k = {expression}.')
    assert not result['issues']
    assert not result['resolutions']
    assert result['semantic_support'] == 'unknown'


@pytest.mark.parametrize('target, statement', [('k/value', 'Parameter k'), ('x/initial', 'Initial x')])
def test_partial_expression_cannot_clear_an_explicit_missing_value(tmp_path, target, statement):
    result = review(tmp_path, f'{statement} is missing.\nResearcher clarification:\n{statement} = 1/2.', target)
    assert [i['kind'] for i in result['issues']] == ['missing_source_value']
    assert not result['resolutions']


def test_expression_cannot_erase_preexisting_direct_numeric_conflicts(tmp_path):
    result = review(tmp_path, 'k = 0.5.\nk = 0.7.\nAuthor clarification:\nk = 1/2.')
    assert [i['kind'] for i in result['issues']] == ['conflicting_declared_values']
    assert not result['resolutions']


@pytest.mark.parametrize('marker', ['', '- ', '* '])
def test_markdown_bullets_do_not_exempt_arithmetic_rhs_from_checking(tmp_path, marker):
    result = review(tmp_path, f'k is missing.\nAuthor clarification:\n{marker}k = 1/2.')
    assert result['issues'] and not result['resolutions']


@pytest.mark.parametrize('literal', ['0.5', '+0.5', '.5', '5e-1', '5E-1', '0.50'])
def test_complete_decimal_and_scientific_literals_still_resolve_missing_values(tmp_path, literal):
    result = review(tmp_path, f'k is missing.\nResearcher clarification:\nk = {literal}.')
    assert not result['issues']
    assert result['resolutions'][0]['evidence']['value'] == .5


def test_negative_literal_and_time_unit_exponents_remain_supported(tmp_path):
    result = review(tmp_path, 'k is missing.\nAuthor correction:\nk = -2.5e-3 min^-1.')
    assert not result['issues']
    facts = {r['target']: r['evidence'] for r in result['resolutions']}
    assert facts['k/value']['value'] == -.0025
    assert facts['k/unit']['unit'] == 'min^-1'


def test_expression_after_unit_is_not_a_literal_rate_correction(tmp_path):
    result = review(tmp_path, 'k = 0.2 per second.\nk = 0.2 per minute.\n'
                    'Researcher correction:\nk = 0.2 per minute * 3.')
    assert result['issues'] and not result['resolutions']


def test_explicit_unit_conversion_chain_keeps_existing_supported_behavior(tmp_path):
    result = review(tmp_path, 'k = 0.1 per minute.\nk = 0.1 per hour.\n'
                    'Researcher correction:\nk = 0.1 per minute = 6 per hour.')
    assert not result['issues']
    assert {r['target'] for r in result['resolutions']} == {'k/value', 'k/unit'}
