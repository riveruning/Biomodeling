"""Frozen metadata requirements, immutable source checks and real execution."""
import json
import math
from dataclasses import replace
from copy import deepcopy

import libsbml
import pytest

from biomodel_agent import agent, agent_tools, evidence, modeling
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.parameter_applicability import assess, requirements, validate_proposal
from biomodel_agent.storage import ArtifactStore


SPEC = dict(unit='second^-1', conditions=dict(organism='synthetic-system', temperature_c=37., pH=7., model_version='v1'))
TEXT = ('Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'
        'Parameter k unit: second^-1\nParameter k context.organism: synthetic-system\n'
        'Parameter k context.temperature_c: 37\nParameter k context.pH: 7\nParameter k context.model_version: v1\n')


def setup(tmp_path, text=TEXT):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text(text)
    source = evidence.ingest_source(store, path)
    return store, source, text


def draft(value=.4, unit='second^-1'):
    return ModelDraft('decay', (State('x', 1., unit='dimensionless'),), (Parameter('k', value, unit=unit),),
                      rate_rules=(RateRule('x', '-k*x'),), time_unit='second')


def proposal(source, ref=None, value=.4, unit='second^-1'):
    args = dict(draft=to_dict(draft(value, unit)), evidence=[
        dict(target=target, source_ref=source, page=1, quote=quote) for target, quote in
        [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'), ('rule:x/expression', 'Equation dx/dt = -k*x.')]])
    if ref:
        args['parameter_assessments'] = {'k/value': ref}
    return args


def check(store, source, text, spec=SPEC):
    return assess(store, [source], 'k/value', spec, source, 1, 0, len(text))


def test_compatible_declarations_remain_scientifically_unknown_and_link_to_draft(tmp_path):
    store, source, text = setup(tmp_path)
    result = check(store, source, text)
    assert result['status'] == 'declared_fields_match' and result['semantic_support'] == 'unknown'
    assert result['candidate']['value'] == .4
    assert all(c['status'] == 'match' for c in result['checks'])
    outcome = agent_tools.submit_draft(store, proposal(source, result['assessment_ref']), [source],
                                       parameter_requirements={'k/value': SPEC})
    assert outcome['state'] == 'ready', outcome['problems']
    assert result['assessment_ref'] in store.get(outcome['draft_ref'])['refs']


@pytest.mark.parametrize('before,after,status', [
    ('temperature_c: 37', 'temperature_c: 30', 'mismatch'),
    ('pH: 7', 'pH: 8', 'mismatch'),
    ('organism: synthetic-system', 'organism: other-system', 'mismatch'),
    ('model_version: v1', 'model_version: v2', 'mismatch'),
    ('Parameter k context.organism: synthetic-system\n', '', 'unknown'),
    ('Parameter k unit: second^-1\n', '', 'unknown'),
    ('unit: second^-1', 'unit: minute^-1', 'conversion_required'),
    ('unit: second^-1', 'unit: M', 'mismatch'),
    ('temperature_c: 37', 'temperature_c: 37 for an unrelated condition', 'unknown'),
    ('Parameter k = 0.4.', 'Parameter k = 0.4 * scaling.', 'unknown'),
])
def test_missing_mismatched_and_conversion_cases_cannot_be_adopted(tmp_path, before, after, status):
    store, source, text = setup(tmp_path, TEXT.replace(before, after))
    result = check(store, source, text)
    assert result['status'] == status, result
    checked = validate_proposal(store, draft(), [source], {'k/value': SPEC}, {'k/value': result['assessment_ref']})
    assert checked[0]['status'] == status
    assert not store.list('model')


@pytest.mark.parametrize('extra', ['Parameter k = 0.9.\n', 'Parameter k unit: minute^-1\n',
                                  'Parameter k context.organism: other-system\n'])
def test_duplicate_conflicting_fields_remain_conflicts(tmp_path, extra):
    store, source, text = setup(tmp_path, TEXT+extra)
    assert check(store, source, text)['status'] == 'source_conflict'


def test_forged_status_and_rewritten_value_are_recomputed(tmp_path):
    store, source, text = setup(tmp_path, TEXT.replace('temperature_c: 37', 'temperature_c: 30'))
    result = check(store, source, text)
    payload = dict(store.get(result['assessment_ref'])['payload'])
    payload['status'] = 'declared_fields_match'
    forged = store.put('parameter_assessment', payload, refs=[source])
    checked = validate_proposal(store, draft(), [source], {'k/value': SPEC}, {'k/value': forged})
    assert checked[0]['status'] == 'invalid' and 'recomputed' in checked[0]['reason']


@pytest.mark.parametrize('value,unit', [(.9, 'second^-1'), (.4, 'unknown'), (.4, 'minute^-1')])
def test_actual_draft_must_match_assessed_value_and_unit(tmp_path, value, unit):
    store, source, text = setup(tmp_path)
    result = check(store, source, text)
    checked = validate_proposal(store, draft(value, unit), [source], {'k/value': SPEC}, {'k/value': result['assessment_ref']})
    assert checked[0]['status'] == 'invalid'


def test_assessed_source_must_be_the_cited_value_source(tmp_path):
    store, source, text = setup(tmp_path)
    result = check(store, source, text)
    other_path = tmp_path/'other.txt'
    other_path.write_text('Parameter k = 0.4.\n')
    other = evidence.ingest_source(store, other_path)
    # Assess again against the full current scope, then cite the other source.
    result = assess(store, [source, other], 'k/value', SPEC, source, 1, 0, len(text))
    args = proposal(source, result['assessment_ref'])
    args['evidence'][1]['source_ref'] = other
    out = agent_tools.submit_draft(store, args, [source, other], parameter_requirements={'k/value': SPEC})
    assert out['state'] == 'invalid' and any('assessed source' in p for p in out['problems'])


def test_new_reply_source_invalidates_previous_assessment_scope(tmp_path):
    store, source, text = setup(tmp_path)
    result = check(store, source, text)
    other_path = tmp_path/'reply.txt'
    other_path.write_text('The current version is now v2.')
    other = evidence.ingest_source(store, other_path)
    checked = validate_proposal(store, draft(), [source, other], {'k/value': SPEC}, {'k/value': result['assessment_ref']})
    assert checked[0]['status'] == 'invalid' and 'scope changed' in checked[0]['reason']


@pytest.mark.parametrize('clock', ['minute', 'hour', 'unknown'])
def test_inverse_time_parameter_must_share_an_explicit_model_clock(tmp_path, clock):
    store, source, text = setup(tmp_path)
    result = check(store, source, text)
    checked = validate_proposal(store, replace(draft(), time_unit=clock), [source], {'k/value': SPEC}, {'k/value': result['assessment_ref']})
    assert checked[0]['status'] == 'invalid' and 'model clock' in checked[0]['reason']


def test_missing_assessment_is_repairable_and_not_execution_ready(tmp_path):
    store, source, text = setup(tmp_path)
    result = agent_tools.submit_draft(store, proposal(source), [source], parameter_requirements={'k/value': SPEC})
    assert result['state'] == 'invalid' and 'assessment missing' in result['problems'][0]
    assert not store.list('model')


@pytest.mark.parametrize('spec', [dict(unit='unknown', conditions={}), dict(unit=[], conditions={}),
    dict(unit='second^-1', conditions={'pH': True}), dict(unit='second^-1', conditions={'pH': 20}),
    dict(unit='second^-1', conditions={'temperature_c': '37'}),
    dict(unit='second^-1', conditions={'organism': ''}), dict(unit='second^-1', conditions={'unapproved_field': 'x'})])
def test_bad_frozen_requirements_rejected(spec):
    with pytest.raises(ValueError):
        requirements({'k/value': spec})


def test_partial_source_line_cannot_hide_a_qualifier(tmp_path):
    store, source, text = setup(tmp_path, TEXT+'Parameter k context.temperature_c: 37 for condition B\n')
    end = text.rfind(' for condition B')
    with pytest.raises(ValueError, match='complete source lines'):
        assess(store, [source], 'k/value', SPEC, source, 1, 0, end)


def test_selected_excerpt_cannot_hide_metadata_conflict_elsewhere(tmp_path):
    store, source, text = setup(tmp_path, TEXT+'Parameter k context.organism: other-system\n')
    result = assess(store, [source], 'k/value', SPEC, source, 1, 0, len(TEXT))
    assert result['status'] == 'source_conflict'
    assert result['metadata_conflicts'][0]['start'] == len(TEXT)
    assert result['metadata_conflicts'][0]['quote'] == 'Parameter k context.organism: other-system'


def test_authorized_additional_source_metadata_conflicts_are_visible(tmp_path):
    store, source, text = setup(tmp_path)
    path = tmp_path/'other-metadata.txt'
    path.write_text('Parameter k context.model_version: v2\n')
    other = evidence.ingest_source(store, path)
    result = assess(store, [source, other], 'k/value', SPEC, source, 1, 0, len(text))
    assert result['status'] == 'source_conflict'
    assert result['metadata_conflicts'][0]['source_ref'] == other


@pytest.mark.parametrize('spelling,expected_multiplier', [('second^-1', 1.), ('minute^-1', 60.), ('hour^-1', 3600.)])
def test_inverse_time_units_are_written_to_actual_sbml(spelling, expected_multiplier):
    artifact = modeling.build_model(draft(unit=spelling))
    model = libsbml.readSBMLFromString(artifact.sbml).getModel()
    parameter = model.getParameter('k')
    unit = model.getUnitDefinition(parameter.getUnits()).getUnit(0)
    assert unit.getKind() == libsbml.UNIT_KIND_SECOND
    assert unit.getExponentAsDouble() == -1
    assert unit.getMultiplier() == expected_multiplier
    assert parameter.getValue() == .4  # No automatic numeric rescaling.


class Scripted:
    last_usage = {}
    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []
    def respond(self, messages):
        self.requests.append(deepcopy(messages))
        action = next(self.actions)
        return action(messages) if callable(action) else action


def test_agent_assessment_to_submission_and_real_solver(tmp_path):
    store, source, text = setup(tmp_path)
    def submit(messages):
        assessment_ref = json.loads(messages[-1]['content'])['assessment_ref']
        return dict(action='submit_draft', args=proposal(source, assessment_ref))
    provider = Scripted([dict(action='assess_parameter', args=dict(target='k/value', source_ref=source, page=1, start=0, end=len(text))),
                         submit, dict(action='run', args={}), dict(action='finish', args={})])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.), time_unit='second'),
                          parameter_requirements={'k/value': SPEC})
    result = store.get(ref)['payload']
    assert result['state'] == 'completed', result
    actual = store.get(result['run_ref'])['payload']['simulation']['values']['x'][-1]
    assert actual == pytest.approx(math.exp(-.4), abs=1e-6)
    assert json.loads(provider.requests[0][1]['content'])['parameter_requirements']['k/value'] == SPEC
    assert result['call_count'] == 4
    saved_assessment = result['progress']['parameter_assessments']['k/value']
    assert saved_assessment['status'] == 'declared_fields_match'
    assert saved_assessment['assessment_ref'] in store.list('parameter_assessment')
