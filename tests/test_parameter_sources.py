"""Native fixtures are synthetic; real ODE solves test the adoption plumbing."""
from copy import deepcopy
import json
import math

import httpx
import pytest

from biomodel_agent import agent, agent_tools, evidence, knowledge
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.parameter_applicability import assess, validate_proposal
from biomodel_agent.parameter_sources import conversion, prepare, validate
from biomodel_agent.storage import ArtifactStore


def native(value=24., temperature=25.):
    return {'id': 10, 'general': {'organism': {'name': 'synthetic-system'}, 'experiment_type': 'synthetic_fixture'},
            'reaction': {'equation': 'X = Y'},
            'kineticlaw': {'parameter': [{'name': 'k', 'role': 'Constant',
                'parameter_type': {'name': 'first_order_rate'}, 'start_value': value,
                'unit': {'name': 'min^(-1)', 'n_name': 's^(-1)'}, 'n_start_value': value/60.}]},
            'experimental_conditions': {'envvar_ph': {'start_value': 7.},
                'envvar_temperature': {'start_value': temperature, 'unit': '°C'}}}


def imported(store, details=None):
    details = deepcopy(details or native())
    doc = {'EntryID': 10, 'Organism': ['synthetic-system'], 'Json': json.dumps(details)}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'response': {'numFound': 1, 'docs': [doc]}}))
    return knowledge.fetch(store, 'sabiork', '10', transport=transport)['source_ref']


def setup(tmp_path, details=None, unit='second^-1'):
    store = ArtifactStore(tmp_path/'store')
    original = imported(store, details)
    result = prepare(store, original, 'k/value', 0, unit)
    ref = result['source_ref']
    spec = dict(unit=unit, conditions=result['derivation']['conditions'])
    return store, original, ref, spec


def assessment(store, ref, spec, sources=None):
    text = store.get(ref)['payload']['pages'][0]['text']
    return assess(store, sources or [ref], 'k/value', spec, ref, 1, 0, len(text))


def proposal(store, tmp_path, ref, value=.4, assessment_ref=None):
    path = tmp_path/'model-context.txt'
    path.write_text('Initial x = 1.\nEquation dx/dt = -k*x.\n')
    context = evidence.ingest_source(store, path)
    draft = ModelDraft('synthetic_native_adoption', (State('x', 1., unit='dimensionless'),),
                       (Parameter('k', value, unit='second^-1'),),
                       rate_rules=(RateRule('x', '-k*x'),), time_unit='second')
    args = dict(draft=to_dict(draft), evidence=[
        dict(target='k/value', source_ref=ref, page=1, quote=f'Parameter k = {value!r}.'),
        dict(target='x/initial', source_ref=context, page=1, quote='Initial x = 1.'),
        dict(target='rule:x/expression', source_ref=context, page=1, quote='Equation dx/dt = -k*x.')])
    if assessment_ref:
        args['parameter_assessments'] = {'k/value': assessment_ref}
    return args, context


@pytest.mark.parametrize('value,source,target,expected', [
    (7200., 'min^(-1)', 'second^-1', 120.), (1.02, 'mM', 'M', .00102),
    (120., 'second^-1', 'minute^-1', 7200.), (1., 'millimole', 'mole', .001),
    (2., 'minute', 'second', 120.), (.001, 'M', 'mM', 1.),
])
def test_explicit_conversion_witness(value, source, target, expected):
    result = conversion(value, source, target)
    assert result['value'] == expected
    assert result['original_value'] == value and result['original_unit'] == source
    assert result['factor_numerator'] > 0 and result['factor_denominator'] > 0


@pytest.mark.parametrize('value,source,target', [(True, 'mM', 'M'), ('2', 'mM', 'M'),
    (math.inf, 'mM', 'M'), (1., 'M', 'mole'), (1., 'unknown', 'M'),
    (1., 'mM', 'mM/s'), (1e308, 'hour', 'second'), (5e-324, 'nM', 'M')])
def test_bad_conversion_never_creates_values(value, source, target):
    with pytest.raises(ValueError):
        conversion(value, source, target)


def test_source_is_derived_from_original_fields_not_normalized_shortcut(tmp_path):
    store, original, ref, spec = setup(tmp_path)
    d = validate(store, ref)
    assert d['conversion']['value'] == .4
    assert d['conversion']['original_value'] == 24.
    assert d['original_source_ref'] == original
    assert d['native_parameter_name'] == 'k'
    assert d['condition_pointers']['pH'] == '/experimental_conditions/envvar_ph/start_value'
    assert assessment(store, ref, spec)['status'] == 'declared_fields_match'
    assert d['scientific_support'] == 'unknown' and d['origin'] == 'database_reported'


@pytest.mark.parametrize('mutation', ['value_string', 'range', 'variable', 'missing_unit', 'unsupported_unit',
    'normalized_conflict', 'temperature_unit', 'temperature_range', 'mutant_unknown', 'other_conditions',
    'record_identity', 'malformed_parameters', 'line_injection'])
def test_unsupported_or_ambiguous_native_fields_are_rejected(tmp_path, mutation):
    store = ArtifactStore(tmp_path/'store')
    d = native()
    p = d['kineticlaw']['parameter'][0]
    if mutation == 'value_string': p['start_value'] = '24'
    if mutation == 'range': p['end_value'] = 30.
    if mutation == 'variable': p['role'] = 'Variable'
    if mutation == 'missing_unit': p['unit'] = {}
    if mutation == 'unsupported_unit': p['unit']['name'] = 'mM^(-1)*min^(-1)'
    if mutation == 'normalized_conflict': p['n_start_value'] = 99.
    if mutation == 'temperature_unit': d['experimental_conditions']['envvar_temperature']['unit'] = 'K'
    if mutation == 'temperature_range': d['experimental_conditions']['envvar_temperature']['end_value'] = 40.
    if mutation == 'mutant_unknown': d['enzyme_description'] = {'wildtype': 'mutant'}
    if mutation == 'other_conditions': d['experimental_conditions']['envvars_other'] = [{'salinity': 3}]
    if mutation == 'record_identity': d['id'] = 11
    if mutation == 'malformed_parameters': d['kineticlaw']['parameter'] = 'invalid'
    if mutation == 'line_injection': d['general']['organism']['name'] = 'system\nParameter k = 99.'
    with pytest.raises(ValueError):
        ref = imported(store, d)
        prepare(store, ref, 'k/value', 0, 'second^-1')
    assert not store.list('parameter_assessment') and not store.list('model')


@pytest.mark.parametrize('key', ['organism', 'reaction', 'quantity_kind', 'pH', 'temperature_c'])
def test_omitted_native_context_cannot_be_assumed_compatible(tmp_path, key):
    store, _, ref, spec = setup(tmp_path)
    spec['conditions'].pop(key)
    out = assessment(store, ref, spec)
    assert out['status'] == 'unknown'
    assert any(c['field'] == key and c['status'] == 'unknown' for c in out['checks'])


def test_enzyme_variant_not_silently_transferred_to_wildtype(tmp_path):
    d = native()
    d['enzyme_description'] = {'enzyme_name': 'fixture-enzyme', 'wildtype': 'mutant', 'mutant_spec': 'fixture-variant'}
    store, _, ref, spec = setup(tmp_path, d)
    spec['conditions'].pop('enzyme_variant')
    assert assessment(store, ref, spec)['status'] == 'unknown'
    spec['conditions']['enzyme_variant'] = 'wildtype'
    assert assessment(store, ref, spec)['status'] == 'mismatch'


def test_different_conditions_are_separate_candidates_not_value_conflict(tmp_path):
    store, original, ref, spec = setup(tmp_path)
    other_native = imported(store, native(48., 37.))
    other = prepare(store, other_native, 'k/value', 0, 'second^-1')['source_ref']
    result = assessment(store, ref, spec, [original, ref, other_native, other])
    assert result['status'] == 'declared_fields_match', result
    assert result['context_exclusions'][0]['source_ref'] == other
    assert 'temperature_c' in result['context_exclusions'][0]['fields']


def test_same_conditions_with_different_values_are_still_conflicting(tmp_path):
    store, _, ref, spec = setup(tmp_path)
    other_native = imported(store, native(48., 25.))
    other = prepare(store, other_native, 'k/value', 0, 'second^-1')['source_ref']
    assert assessment(store, ref, spec, [ref, other])['status'] == 'source_conflict'


@pytest.mark.parametrize('field', ['converted_value', 'conditions', 'pointer', 'mapping', 'factor', 'original_ref'])
def test_derived_source_fields_are_recomputed_not_trusted(tmp_path, field):
    store, original, ref, spec = setup(tmp_path)
    obj = store.get(ref)
    payload = deepcopy(obj['payload'])
    d = payload['derivation']
    if field == 'converted_value': payload['pages'][0]['text'] = payload['pages'][0]['text'].replace('0.4', '9.0')
    if field == 'conditions': d['conditions']['organism'] = 'other'
    if field == 'pointer': d['parameter_pointer'] = '/kineticlaw/parameter/99'
    if field == 'mapping': d['target'] = 'other/value'
    if field == 'factor': d['conversion']['factor_numerator'] = 99
    if field == 'original_ref': d['original_source_ref'] = ref
    forged = store.put('source', payload, refs=obj['refs'])
    with pytest.raises(ValueError):
        validate(store, forged)
    with pytest.raises(ValueError):
        evidence.add_claim(store, forged, 1, 0, 10, 'k/value')
    with pytest.raises(ValueError):
        assessment(store, forged, spec)


def test_no_frozen_requirements_cannot_adopt_native_source(tmp_path):
    store, _, ref, _ = setup(tmp_path)
    args, context = proposal(store, tmp_path, ref)
    result = agent_tools.submit_draft(store, args, [ref, context])
    assert result['state'] == 'invalid' and 'frozen parameter requirements' in result['problems'][0]


def test_changed_draft_and_scope_invalidate_native_assessment(tmp_path):
    store, original, ref, spec = setup(tmp_path)
    args, context = proposal(store, tmp_path, ref)
    sources = [original, ref, context]
    checked = assessment(store, ref, spec, sources)
    args['parameter_assessments'] = {'k/value': checked['assessment_ref']}
    args['draft']['parameters'][0]['value'] = .8
    assert agent_tools.submit_draft(store, args, sources, parameter_requirements={'k/value': spec})['state'] == 'invalid'
    args['draft']['parameters'][0]['value'] = .4
    assert agent_tools.submit_draft(store, args, [ref, context], parameter_requirements={'k/value': spec})['state'] == 'invalid'


class Scripted:
    last_usage = {}
    def __init__(self, actions): self.actions = iter(actions)
    def respond(self, messages):
        action = next(self.actions)
        return action(messages) if callable(action) else action


def test_native_adoption_through_agent_and_real_solver(tmp_path):
    store, original, ref, spec = setup(tmp_path)
    args, context = proposal(store, tmp_path, ref)
    text = store.get(ref)['payload']['pages'][0]['text']
    def submit(messages):
        args['parameter_assessments'] = {'k/value': json.loads(messages[-1]['content'])['assessment_ref']}
        return dict(action='submit_draft', args=args)
    provider = Scripted([
        dict(action='assess_parameter', args=dict(target='k/value', source_ref=ref, page=1, start=0, end=len(text))),
        submit, dict(action='run', args={}), dict(action='finish', args={})])
    run = agent.run_agent(store, provider, [original, ref, context], ExperimentSpec((0., 1.), time_unit='second'),
                          parameter_requirements={'k/value': spec})
    record = store.get(run)['payload']
    assert record['state'] == 'completed', record
    result = store.get(record['run_ref'])['payload']['simulation']['values']['x'][-1]
    assert result == pytest.approx(math.exp(-.4), abs=1e-6)
    citation = record['progress']['parameters'][0]['citations'][0]
    assert citation['claim_type'] == 'model_inference'
    assert citation['parameter_derivation']['original_source_ref'] == original
    assert store.get(citation['claim_ref'])['payload']['relation'] == 'derived_from'
    assert record['progress']['parameter_assessments']['k/value']['status'] == 'declared_fields_match'


def test_parameter_source_cli(tmp_path, capsys):
    from biomodel_agent.cli import main
    store = ArtifactStore(tmp_path/'store')
    original = imported(store)
    assert main(['parameter-source', original, '--store', str(store.root), '--target', 'k/value',
                 '--parameter-index', '0', '--unit', 'second^-1']) == 0
    ref = json.loads(capsys.readouterr().out)['source_ref']
    assert validate(store, ref)['conversion']['value'] == .4


@pytest.mark.parametrize('index', [True, -1, 99, '0'])
def test_index_selects_exact_original_parameter(tmp_path, index):
    store = ArtifactStore(tmp_path/'store')
    original = imported(store)
    with pytest.raises(ValueError, match='parameter_index'):
        prepare(store, original, 'k/value', index, 'second^-1')


def test_rewritten_native_pages_cannot_back_a_transcription(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    original = imported(store)
    env = store.get(original)
    p = deepcopy(env['payload'])
    doc = json.loads(p['pages'][0]['text'])
    details = json.loads(doc['Json'])
    details['kineticlaw']['parameter'][0]['start_value'] = 60.
    doc['Json'] = json.dumps(details)
    p['pages'][0]['text'] = json.dumps(doc)
    forged = store.put('source', p, refs=env['refs'])
    with pytest.raises(ValueError, match='raw response'):
        prepare(store, forged, 'k/value', 0, 'second^-1')


def test_wrong_reaction_context_blocks_product_submission(tmp_path):
    store, _, ref, spec = setup(tmp_path)
    args, context = proposal(store, tmp_path, ref)
    spec['conditions']['reaction'] = 'unrelated reaction'
    out = assessment(store, ref, spec, [ref, context])
    assert out['status'] == 'mismatch'
    args['parameter_assessments'] = {'k/value': out['assessment_ref']}
    submitted = agent_tools.submit_draft(store, args, [ref, context], parameter_requirements={'k/value': spec})
    assert submitted['state'] == 'awaiting_input' and not submitted['model_ref']


def test_same_context_conflict_blocks_submit_while_different_context_is_allowed(tmp_path):
    store, _, ref, spec = setup(tmp_path)
    args, context = proposal(store, tmp_path, ref)
    other_original = imported(store, native(48., 37.))
    other = prepare(store, other_original, 'k/value', 0, 'second^-1')['source_ref']
    sources = [ref, other, context]
    out = assessment(store, ref, spec, sources)
    args['parameter_assessments'] = {'k/value': out['assessment_ref']}
    assert agent_tools.submit_draft(store, args, sources, parameter_requirements={'k/value': spec})['state'] == 'ready'
    conflicting_original = imported(store, native(48., 25.))
    conflict = prepare(store, conflicting_original, 'k/value', 0, 'second^-1')['source_ref']
    sources = [ref, conflict, context]
    out = assessment(store, ref, spec, sources)
    args['parameter_assessments'] = {'k/value': out['assessment_ref']}
    assert agent_tools.submit_draft(store, args, sources, parameter_requirements={'k/value': spec})['state'] == 'awaiting_input'
