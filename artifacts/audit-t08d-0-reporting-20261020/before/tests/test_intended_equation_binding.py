"""F1 declaration controls; all seen synthetic data, not scientific review."""
from dataclasses import replace
import math
from pathlib import Path

import pytest

from biomodel_agent import agent_tools, application, bindings, evidence, modeling
from biomodel_agent.domain.contracts import ExperimentSpec, FrozenMap, ModelDraft
from biomodel_agent.domain.serialization import from_dict
from test_evidence_binding_acceptance import setup_case


FORMULA = 'r*X*(1-X/K)'
SENTENCE = 'The author confirms the intended equation is dX/dt = '+FORMULA+'.'
ROOT = Path(__file__).resolve().parents[1]


def checked(text, quote=None, *, target='rule:X/expression', expected=FORMULA):
    quote = text if quote is None else quote
    start = text.index(quote)
    return bindings.check(text, 'a'*64, 1, start, start+len(quote), target,
                          expected, {'X', 'Y', 'r', 'K', 'time'})


@pytest.mark.parametrize('sentence', [
    SENTENCE,
    'The intended equation is dX/dt = '+FORMULA+'.',
    'the AUTHOR confirms THE intended EQUATION is dX/dt = '+FORMULA+'.',
    'The  author confirms   the intended equation is d X / dt = '+FORMULA+'.',
])
def test_only_explicit_intended_forms_match_with_whole_statement_offsets(sentence):
    text = '中文页眉。\n'+sentence+'\nUnrelated appendix.'
    result = checked(text, sentence)
    assert result['status'] == 'matched'
    assert result['semantic_support'] == 'unknown' and result['version'] == 1
    declaration, = result['declarations']
    start = text.index(sentence)
    assert declaration['start'] == start
    assert declaration['end'] == start+len(sentence)-1
    assert text[declaration['start']:declaration['end']] == sentence[:-1]
    assert result['source']['start'] == start
    assert result['source']['end'] == start+len(sentence)


def test_original_logistic_clarification_can_bind_without_material_rewriting():
    text = (ROOT/'benchmarks/inputs/reliability/logistic/clarified/source.txt').read_text()
    result = checked(text, SENTENCE)
    assert result['status'] == 'matched'
    declaration, = result['declarations']
    assert text[declaration['start']:declaration['end']] == SENTENCE[:-1]
    assert result['semantic_support'] == 'unknown'


@pytest.mark.parametrize('quote', [
    'dX/dt = '+FORMULA,
    'the intended equation is dX/dt = '+FORMULA,
    'author confirms the intended equation is dX/dt = '+FORMULA,
    SENTENCE[:-3],
])
def test_cropping_positive_declaration_cannot_hide_confirmation_or_formula(quote):
    result = checked(SENTENCE, quote)
    assert result['status'] == 'mismatch'
    assert 'truncates' in result['reason']


@pytest.mark.parametrize('text', [
    'Maybe '+SENTENCE,
    'Possibly '+SENTENCE,
    'If condition A applies, '+SENTENCE,
    'Only under condition A, '+SENTENCE,
    'It is not true that '+SENTENCE,
    'The author does not confirm the intended equation is dX/dt = '+FORMULA+'.',
    'The author cannot confirm the intended equation is dX/dt = '+FORMULA+'.',
    'The author tentatively confirms the intended equation is dX/dt = '+FORMULA+'.',
    'The author previously confirmed the intended equation is dX/dt = '+FORMULA+'.',
    'The intended equation may be dX/dt = '+FORMULA+'.',
    'The intended equation is not dX/dt = '+FORMULA+'.',
    'The intended equation is perhaps dX/dt = '+FORMULA+'.',
])
@pytest.mark.parametrize('cropped', [False, True])
def test_uncertainty_negation_and_condition_prefixes_cannot_match(text, cropped):
    quote = 'dX/dt = '+FORMULA if cropped else text
    result = checked(text, quote)
    # The existing bare-value grammar may classify "It is ..." as another
    # target (mismatch); either conservative refusal must remain a refusal.
    assert result['status'] in ('unknown', 'mismatch')
    assert result['semantic_support'] == 'unknown'


@pytest.mark.parametrize('suffix', [
    ' if condition A applies', ' unless condition B applies', ' only under condition A',
    ', but this remains unconfirmed', ', not a confirmed result', ' or dX/dt = r*X',
    ' (unconfirmed)', '?',
])
@pytest.mark.parametrize('cropped', [False, True])
def test_same_clause_suffixes_cannot_be_removed_by_short_quote(suffix, cropped):
    text = SENTENCE[:-1]+suffix+'.'
    result = checked(text, SENTENCE[:-1] if cropped else text)
    assert result['status'] != 'matched'
    assert result['semantic_support'] == 'unknown'


@pytest.mark.parametrize('declaration,target,expected,status', [
    ('The intended equation is dY/dt = '+FORMULA+'.', 'rule:X/expression', FORMULA, 'mismatch'),
    (SENTENCE, 'reaction:X/rate', FORMULA, 'mismatch'),
    (SENTENCE, 'derived:X/expression', FORMULA, 'mismatch'),
    ('The author confirms the intended equation is dX/dt = -r*X*X.', 'rule:X/expression', '-r*X', 'mismatch'),
    ('The intended equation is dX/dt = r*X.', 'rule:X/expression', '-r*X', 'mismatch'),
    ('The intended equation is dX/dt = r*X*(1-X)/K.', 'rule:X/expression', FORMULA, 'unknown'),
    ('The intended equation is dX/dt = '+FORMULA+'*X.', 'rule:X/expression', FORMULA, 'unknown'),
])
def test_existing_target_identity_and_expression_rules_remain(declaration, target, expected, status):
    assert checked(declaration, target=target, expected=expected)['status'] == status


def test_bounded_context_cannot_invent_a_statement_boundary():
    text = 'uncertain '*40+SENTENCE
    assert checked(text, SENTENCE)['status'] == 'unknown'


def test_explicit_mapping_still_requires_cited_witness_and_full_declaration():
    text = 'Symbol Y maps to X; The author confirms the intended equation is dY/dt = r*Y*(1-Y/K).'
    result = bindings.check(text, 'a'*64, 1, 0, len(text), 'rule:X/expression', FORMULA,
                            {'X', 'r', 'K'}, {'Y': 'X'})
    assert result['status'] == 'matched' and len(result['mapping_evidence']) == 1
    declaration, = result['declarations']
    assert text[declaration['start']:declaration['end']].startswith('The author confirms')


def test_actual_submission_and_sbml_recheck_keep_source_unreviewed(tmp_path):
    sentence = 'The author confirms the intended equation is dx/dt = -k*x.'
    store, source, proposal = setup_case(tmp_path, equation_quote=sentence)
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'ready'
    claim = store.get(result['artifact'].element_map['rule:x/expression'][0])['payload']
    assert claim['quote'] == sentence and claim['review_status'] == 'unverified'
    assert claim['binding']['declarations'][0]['start'] == claim['start']
    run = store.get(application.run_reproduction(store, result['artifact'],
                    ExperimentSpec((0., 1.)), strict=True))['payload']
    assert run['source_validation']['binding_status'] == 'matched'
    assert run['source_validation']['semantic_support'] == 'unknown'
    assert run['scientific_status'] == 'not_verified'
    assert run['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-6)


@pytest.mark.parametrize('sentence', [
    'Maybe The author confirms the intended equation is dx/dt = -k*x.',
    'The author does not confirm the intended equation is dx/dt = -k*x.',
    'The author confirms the intended equation is dx/dt = -k*x only under condition A.',
])
def test_qualified_proposals_pause_without_partial_claims(tmp_path, sentence):
    store, source, proposal = setup_case(tmp_path, equation_quote=sentence)
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'awaiting_input'
    assert not store.list('claim') and not store.list('model')


def test_manual_claim_cannot_bypass_new_prose_context_at_strict_execution(tmp_path):
    sentence = 'The author confirms the intended equation is dx/dt = -k*x only under condition A.'
    store, source, proposal = setup_case(tmp_path, equation_quote=sentence)
    text = store.get(source)['payload']['pages'][0]['text']
    links = {}
    for item in proposal['evidence']:
        quote = 'dx/dt = -k*x' if item['target'].startswith('rule:') else item['quote']
        start = text.index(quote)
        links[item['target']] = (evidence.add_claim(store, source, 1, start, start+len(quote), item['target']),)
    draft = replace(from_dict(ModelDraft, proposal['draft']), element_map=FrozenMap(links))
    with pytest.raises(ValueError, match='binding'):
        application.run_reproduction(store, modeling.build_model(draft), ExperimentSpec((0., 1.)), strict=True)
    assert not store.list('run') and not store.list('simulation_input')
