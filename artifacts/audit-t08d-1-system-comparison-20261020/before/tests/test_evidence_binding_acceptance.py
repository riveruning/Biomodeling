"""Independent T05 controls: target support is not keyword/number presence."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, agent_tools, application, evidence, modeling
from biomodel_agent.domain.contracts import ExperimentSpec, FrozenMap, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore
from test_invocation_acceptance import Scripted, act


def setup_case(tmp_path, *, value=.4, expression='-k*x', parameter_quote='Parameter k = 0.4.',
               equation_quote='Equation dx/dt = -k*x.', extra='', initial_quote='Initial x = 1.'):
    store = ArtifactStore(tmp_path / 'store')
    text = '\n'.join([initial_quote, parameter_quote, equation_quote, extra])
    source = store.put('source', {'pages': [{'number': 1, 'text': text}]})
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', value),),
                       rate_rules=(RateRule('x', expression),))
    proposal = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', initial_quote), ('k/value', parameter_quote),
                              ('rule:x/expression', equation_quote)]]}
    return store, source, proposal


@pytest.mark.parametrize('parameter_quote', ['Buffer pH = 0.4.', 'Parameter q = 0.4.',
    'Parameter k = 7; buffer pH = 0.4.', 'Initial k = 0.4.'])
def test_same_number_in_another_target_does_not_support_parameter(tmp_path, parameter_quote):
    store, source, proposal = setup_case(tmp_path, parameter_quote=parameter_quote)
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'invalid'
    assert not store.list('model') and not store.list('claim')


@pytest.mark.parametrize('quote,expression', [('Equation dx/dt = -k*x*x.', '-k*x'),
    ('Equation dx/dt = k*x.', '-k*x'), ('Equation dy/dt = -k*x.', '-k*x')])
def test_formula_or_lhs_mismatch_cannot_become_a_ready_draft(tmp_path, quote, expression):
    store, source, proposal = setup_case(tmp_path, equation_quote=quote, expression=expression)
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'invalid' and not store.list('model') and not store.list('claim')


@pytest.mark.parametrize('page_text,quote', [('Total a + k = 0.4.', 'k = 0.4'),
    ('Parameter k = 0.4/2.', 'Parameter k = 0.4'),
    ('Maybe Parameter k = 0.4.', 'Parameter k = 0.4')])
def test_cropped_quote_cannot_hide_arithmetic_or_uncertainty(tmp_path, page_text, quote):
    store, source, proposal = setup_case(tmp_path, parameter_quote=page_text)
    proposal['evidence'][1]['quote'] = quote
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] != 'ready' and result['artifact'] is None
    assert not store.list('model') and not store.list('claim')


def test_formula_prefix_cannot_hide_an_extra_factor(tmp_path):
    store, source, proposal = setup_case(tmp_path, equation_quote='Equation dx/dt = -k*x*x.')
    proposal['evidence'][2]['quote'] = 'Equation dx/dt = -k*x'
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] != 'ready' and not store.list('model')


def test_unsupported_wording_stays_unknown_and_requires_clarification(tmp_path):
    store, source, proposal = setup_case(tmp_path, equation_quote='The response relaxes according to the displayed figure.')
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'awaiting_input' and not store.list('model')
    assert any(d.get('status') == 'unknown' for d in result['binding_diagnostics'])


def test_valid_direct_bindings_preserve_source_positions_without_semantic_promotion(tmp_path):
    store, source, proposal = setup_case(tmp_path)
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'ready'
    for ref in store.list('claim'):
        claim = store.get(ref)['payload']
        binding = claim['binding']
        assert binding['version'] == 1 and binding['status'] == 'matched'
        assert binding['target'] == claim['statement']
        assert binding['source'] == {'source_ref': source, 'page': 1, 'start': claim['start'], 'end': claim['end']}
        assert claim['review_status'] == 'unverified'
    run = store.get(application.run_reproduction(store, result['artifact'], ExperimentSpec((0., 1.)), strict=True))['payload']
    assert run['source_validation']['binding_status'] == 'matched'
    assert run['source_validation']['semantic_support'] == 'unknown' and run['scientific_status'] == 'not_verified'
    assert run['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-6)


def test_explicit_symbol_mapping_is_checked_against_cited_declarations(tmp_path):
    quote = 'Symbol X maps to x; Symbol a maps to k; Equation dX/dt = -a*X.'
    store, source, proposal = setup_case(tmp_path, equation_quote=quote)
    proposal['evidence'][2]['symbol_map'] = {'X': 'x', 'a': 'k'}
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'ready'
    claim = next(store.get(r)['payload'] for r in store.list('claim') if store.get(r)['payload']['statement'].startswith('rule:'))
    assert claim['binding']['symbol_map'] == {'X': 'x', 'a': 'k'}
    assert len(claim['binding']['mapping_evidence']) == 2


def test_asserted_mapping_without_source_witness_is_not_trusted(tmp_path):
    store, source, proposal = setup_case(tmp_path, equation_quote='Equation dX/dt = -a*X.')
    proposal['evidence'][2]['symbol_map'] = {'X': 'x', 'a': 'k'}
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'awaiting_input' and not store.list('model') and not store.list('claim')


def test_manually_built_model_cannot_bypass_wrong_binding_or_populate_cache(tmp_path):
    store, source, proposal = setup_case(tmp_path, parameter_quote='Buffer pH = 0.4.')
    text = store.get(source)['payload']['pages'][0]['text']
    links = {}
    for item in proposal['evidence']:
        start = text.index(item['quote'])
        links[item['target']] = (evidence.add_claim(store, source, 1, start, start+len(item['quote']), item['target']),)
    from dataclasses import replace
    from biomodel_agent.domain.serialization import from_dict
    draft = replace(from_dict(ModelDraft, proposal['draft']), element_map=FrozenMap(links))
    with pytest.raises(ValueError, match='binding'):
        application.run_reproduction(store, modeling.build_model(draft), ExperimentSpec((0., 1.)), strict=True)
    assert not store.list('run') and not store.list('simulation_input')


def test_targeted_feedback_repairs_formula_within_original_budget(tmp_path):
    store, source, good = setup_case(tmp_path)
    bad = deepcopy(good)
    bad['draft']['rate_rules'][0]['expression'] = '-k*x*x'
    provider = Scripted([{'action': 'submit_draft', 'args': bad},
                         {'action': 'submit_draft', 'args': good}, act('run'), act('finish')])
    root = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), max_repairs=1)
    result = store.get(root)['payload']
    assert result['state'] == 'completed' and result['repair_count'] == 1 and result['call_count'] == 4
    feedback = json.loads(provider.calls[1][-1]['content'])
    diagnostic = next(d for d in feedback['binding_diagnostics'] if d['target'] == 'rule:x/expression')
    assert diagnostic['status'] == 'mismatch' and diagnostic['source']['source_ref'] == source
    run = store.get(result['run_ref'])['payload']
    assert run['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-6)
    assert run['source_validation']['binding_status'] == 'matched' and run['scientific_status'] == 'not_verified'
    from biomodel_agent import bundles
    manifest = bundles.export_bundle(store, root, tmp_path/'bundle')
    reopened = ArtifactStore(tmp_path/'bundle')
    assert source in manifest['objects_and_blobs']
    for ref in store.list('claim'):
        assert reopened.get(ref) == store.get(ref)


def test_unknown_binding_pauses_and_researcher_formula_restores_execution(tmp_path):
    from test_agent_resume import Scripted as Flexible
    store, source, proposal = setup_case(tmp_path, equation_quote='The response relaxes according to the displayed figure.')
    parent = agent.run_agent(store, Flexible([{'action': 'submit_draft', 'args': proposal}]), [source], ExperimentSpec((0., 1.)))
    assert store.get(parent)['payload']['state'] == 'awaiting_input' and not store.list('claim')
    def clarified(messages):
        info = json.loads(messages[-1]['content'])
        fixed = deepcopy(proposal)
        fixed['evidence'][2].update(source_ref=info['source_ref'], quote='Equation dx/dt = -k*x.')
        return {'action': 'submit_draft', 'args': fixed}
    result = store.get(agent.resume_agent(store, Flexible([clarified, act('run'), act('finish')]), parent,
                                         'Equation dx/dt = -k*x.', respondent='Researcher A'))['payload']
    assert result['state'] == 'completed' and result['call_count'] == 4
    formula = next(store.get(r)['payload'] for r in store.list('claim') if store.get(r)['payload']['statement'].startswith('rule:'))
    assert formula['claim_type'] == 'human_convention' and formula['binding']['status'] == 'matched'
    assert store.get(result['run_ref'])['payload']['scientific_status'] == 'not_verified'


@pytest.mark.parametrize('defect', ['actual_value_changed', 'wrong_target_reuse', 'unsupported_binding_version', 'asserted_status'])
def test_stored_binding_is_not_authority_over_source_or_executable_model(tmp_path, defect):
    from dataclasses import replace
    from biomodel_agent.domain.serialization import from_dict
    store, source, proposal = setup_case(tmp_path)
    result = agent_tools.submit_draft(store, proposal, [source])
    draft = from_dict(ModelDraft, proposal['draft'])
    links = dict(result['artifact'].element_map)
    if defect == 'actual_value_changed':
        draft = replace(draft, parameters=(Parameter('k', .6),))
    elif defect == 'wrong_target_reuse':
        links['k/value'] = links['x/initial']
    else:
        ref = links['k/value'][0]
        claim = deepcopy(store.get(ref)['payload'])
        claim['binding']['version' if defect == 'unsupported_binding_version' else 'reason'] = 999 if defect == 'unsupported_binding_version' else 'approved by caller'
        links['k/value'] = (store.put('claim', claim, refs=(source,)),)
    artifact = modeling.build_model(replace(draft, element_map=FrozenMap(links)))
    with pytest.raises(ValueError, match='binding'):
        application.run_reproduction(store, artifact, ExperimentSpec((0., 1.)), strict=True)
    assert not store.list('run') and not store.list('simulation_input')


def test_binding_gate_runs_before_cache_lookup_even_with_prior_numeric_result(tmp_path, monkeypatch):
    from dataclasses import replace
    from biomodel_agent.domain.serialization import from_dict
    store, source, proposal = setup_case(tmp_path, parameter_quote='Buffer pH = 0.4.')
    text = evidence._source_page_text(store, source, 1)
    links = {}
    for item in proposal['evidence']:
        start = text.index(item['quote'])
        links[item['target']] = (evidence.add_claim(store, source, 1, start, start+len(item['quote']), item['target']),)
    artifact = modeling.build_model(replace(from_dict(ModelDraft, proposal['draft']), element_map=FrozenMap(links)))
    first = store.get(application.run_reproduction(store, artifact, ExperimentSpec((0., 1.))))['payload']
    assert first['source_validation']['binding_status'] == 'mismatch'
    assert first['scientific_status'] == 'not_verified'
    old_inputs = store.list('simulation_input')
    list_objects = store.list
    def no_cache(kind=None):
        assert kind != 'run', 'cache inspected before checking typed source binding'
        return list_objects(kind)
    monkeypatch.setattr(store, 'list', no_cache)
    with pytest.raises(ValueError, match='binding'):
        application.run_reproduction(store, artifact, ExperimentSpec((0., 1.)), strict=True)
    assert store.list('simulation_input') == old_inputs


def test_recovery_adopts_bound_claims_without_creating_claims_or_repeating_solver(tmp_path, monkeypatch):
    from test_recovery_acceptance import post_result_crash
    store, source, proposal = setup_case(tmp_path)
    _, execution = post_result_crash(store, source, proposal, monkeypatch)
    claims = store.list('claim')
    run_refs = store.list('run')
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('solver repeated'))
    monkeypatch.setattr(agent_tools, 'submit_draft', lambda *a, **k: pytest.fail('claim submission repeated'))
    result = store.get(agent.recover_agent(store, Scripted([act('finish')]), execution))['payload']
    assert result['state'] == 'completed' and store.list('claim') == claims and store.list('run') == run_refs
    assert store.get(result['run_ref'])['payload']['source_validation']['binding_status'] == 'matched'


@pytest.mark.parametrize('expression', ['-pow(x,2)*k', '-k*x'])
def test_actual_sbml_binding_includes_derived_assignments_and_normalized_formulas(tmp_path, expression):
    from biomodel_agent.domain.contracts import Derived
    from biomodel_agent import reporting
    store, source, proposal = setup_case(tmp_path, expression=expression, equation_quote='Equation dx/dt = '+expression+'.',
                                         extra='Derived y = 2*x.')
    proposal['draft']['derived'] = [to_dict(Derived('y', 'x+x'))]
    proposal['evidence'].append({'target': 'derived:y/expression', 'source_ref': source, 'page': 1, 'quote': 'Derived y = 2*x.'})
    result = agent_tools.submit_draft(store, proposal, [source])
    assert result['state'] == 'ready'
    ref = application.run_reproduction(store, result['artifact'], ExperimentSpec((0., 1.)), strict=True)
    facts = store.get(ref)['payload']['source_validation']
    assert facts['binding_status'] == 'matched' and facts['binding_coverage'] == 1.
    assert 'derived:y/expression' in facts['required_targets']
    assert 'Target binding: **matched**' in reporting.render_report(store, ref)
