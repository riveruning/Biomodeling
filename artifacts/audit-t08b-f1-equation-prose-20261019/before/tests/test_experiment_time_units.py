"""An explicit experimental clock is enforced before numerical execution."""
from copy import deepcopy
import json
import math
import pickle

import libsbml
import pytest

from biomodel_agent import agent, application, cli, evidence, fitting, modeling, simulation, time_units
from biomodel_agent.domain.contracts import ExperimentSpec, ModelArtifact, ModelDraft, ObservationSpec, Parameter, RateRule, State
from biomodel_agent.domain.serialization import from_dict, to_dict
from biomodel_agent.storage import ArtifactStore


def decay(unit='second', k=.4):
    return modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', k),),
                                           rate_rules=(RateRule('x', '-k*x'),), time_unit=unit))


@pytest.mark.parametrize('alias, expected', [('s', 'second'), ('SECONDS', 'second'), ('sec', 'second'),
                                          (' min ', 'minute'), ('minutes', 'minute'),
                                          ('h', 'hour'), ('hrs', 'hour'), ('unknown', 'unknown')])
def test_time_unit_round_trip_is_canonical_and_immutable(alias, expected):
    spec = ExperimentSpec((0., 1.), time_unit=alias)
    assert spec.time_unit == expected
    assert from_dict(ExperimentSpec, to_dict(spec)) == spec
    assert pickle.loads(pickle.dumps(spec)) == spec
    with pytest.raises(AttributeError):
        spec.time_unit = 'second'


@pytest.mark.parametrize('unit', ['', None, 60, True, 'day', 'dimensionless', 'per minute'])
def test_invalid_or_unsupported_experiment_time_units_are_not_coerced(unit):
    with pytest.raises(ValueError, match='time_unit'):
        ExperimentSpec((0., 1.), time_unit=unit)


def test_legacy_json_omission_remains_unknown():
    legacy = from_dict(ExperimentSpec, {'times': [0., 1.]})
    assert legacy.time_unit == 'unknown'
    assert time_units.check_experiment_time(decay().sbml, legacy.time_unit).status == 'unknown'


@pytest.mark.parametrize('unit, seconds', [('second', 1.), ('minute', 60.), ('hour', 3600.)])
def test_generated_model_clock_is_read_from_its_definition(unit, seconds):
    assert time_units.model_time_scale(decay(unit).sbml) == seconds
    assert time_units.check_experiment_time(decay(unit).sbml, unit).status == 'pass'


def custom_clock(multiplier, scale=0, kind=libsbml.UNIT_KIND_SECOND, exponent=1.):
    doc = libsbml.readSBMLFromString(decay('minute').sbml)
    model = doc.getModel()
    definition = model.getUnitDefinition('minute')
    definition.setId('custom_clock')
    unit = definition.getUnit(0)
    unit.setMultiplier(multiplier)
    unit.setScale(scale)
    unit.setKind(kind)
    unit.setExponent(exponent)
    model.setTimeUnits('custom_clock')
    return ModelArtifact('custom', libsbml.writeSBMLToString(doc))


def test_custom_definition_uses_multiplier_and_decimal_scale():
    artifact = custom_clock(6., scale=1)
    assert time_units.model_time_scale(artifact.sbml) == 60.
    assert time_units.check_experiment_time(artifact.sbml, 'min').status == 'pass'


def test_a_unit_called_minute_cannot_override_its_actual_definition():
    doc = libsbml.readSBMLFromString(decay('minute').sbml)
    doc.getModel().getUnitDefinition('minute').getUnit(0).setMultiplier(3600.)
    check = time_units.check_experiment_time(libsbml.writeSBMLToString(doc), 'minute')
    assert check.status == 'fail' and '3600 seconds/unit' in check.message


@pytest.mark.parametrize('artifact', [custom_clock(60., kind=libsbml.UNIT_KIND_METRE),
                                     custom_clock(60., exponent=-1.), custom_clock(0.),
                                     custom_clock(-60.), custom_clock(1., scale=999)])
def test_unresolved_or_nonphysical_clock_is_not_guessed(artifact):
    assert time_units.model_time_scale(artifact.sbml) is None
    assert time_units.check_experiment_time(artifact.sbml, 'minute').status == 'fail'


def test_missing_model_time_units_are_unknown_even_when_experiment_is_explicit():
    artifact = decay('unknown')
    assert time_units.model_time_scale(artifact.sbml) is None
    assert time_units.check_experiment_time(artifact.sbml, 'second').status == 'fail'
    assert time_units.check_experiment_time(artifact.sbml, 'unknown').status == 'unknown'


def test_simulation_mismatch_stops_before_spawning(monkeypatch):
    monkeypatch.setattr(simulation.multiprocessing, 'get_context', lambda *a: pytest.fail('wrong clock reached worker'))
    result = simulation.simulate(decay('second'), ExperimentSpec((0., 1.), time_unit='minute'))
    assert result.status == 'failed' and not result.times
    assert any(c.code == 'experiment_time_unit' and c.status == 'fail' for c in result.checks.checks)


def test_application_mismatch_stops_before_cache_and_store_writes(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'store')
    before = store.list()
    monkeypatch.setattr(store, 'list', lambda *a: pytest.fail('clock mismatch reached cache'))
    monkeypatch.setattr(simulation, 'simulate', lambda *a: pytest.fail('clock mismatch reached solver'))
    with pytest.raises(ValueError, match='differs from the SBML model clock'):
        application.run_reproduction(store, decay(), ExperimentSpec((0., 1.), time_unit='minute'))
    monkeypatch.undo()
    assert store.list() == before


def test_matched_minute_clock_runs_without_rescaling_grid():
    result = simulation.simulate(decay('minute'), ExperimentSpec((0., .5, 1.), time_unit='min'))
    assert result.status == 'succeeded' and result.times == (0., .5, 1.)
    assert result.values['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)
    assert any(c.code == 'experiment_time_unit' and c.status == 'pass' for c in result.checks.checks)


def test_legacy_unknown_clock_is_reported_without_relabeling():
    result = simulation.simulate(decay('unknown'), ExperimentSpec((0., 1.)))
    assert result.status == 'succeeded'
    assert result.values['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)
    assert any(c.code == 'experiment_time_unit' and c.status == 'unknown' for c in result.checks.checks)


def test_explicit_alias_cache_and_replay_keep_the_frozen_clock(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    artifact = decay('minute')
    first = application.run_reproduction(store, artifact, ExperimentSpec((0., 1.), time_unit='min'))
    second = application.run_reproduction(store, artifact, ExperimentSpec((0., 1.), time_unit='minute'))
    assert store.get(second)['payload']['cached_from'] == first
    replay = store.get(application.replay_run(store, first))['payload']
    frozen = store.get(replay['input_ref'])['payload']
    assert frozen['experiment']['time_unit'] == 'minute'
    assert replay['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)


def test_fitting_rejects_wrong_clock_before_integration(monkeypatch):
    monkeypatch.setattr(fitting, 'solve_ivp', lambda *a, **kw: pytest.fail('wrong clock reached fitter integration'))
    with pytest.raises(ValueError, match='differs from the SBML model clock'):
        fitting.predict(decay(), ExperimentSpec((0., 1.), time_unit='minute'),
                        (ObservationSpec('signal', 'x'),), {'k': .4})


def test_fit_validation_rejects_wrong_clock_before_optimizer(tmp_path, monkeypatch):
    monkeypatch.setattr(fitting.mp, 'get_context', lambda *a: pytest.fail('wrong clock reached optimizer worker'))
    training = [{'id': 'a', 'role': 'training',
                 'experiment': to_dict(ExperimentSpec((0., 1.), time_unit='minute')),
                 'values': {'signal': [1., .7]}, 'sigma': {'signal': .01}}]
    with pytest.raises(ValueError, match='differs from the SBML model clock'):
        fitting.fit_parameters(decay(), training, (ObservationSpec('signal', 'x'),), {'k': (.1, 1.)})


def test_prediction_with_matched_clock_agrees_with_simulation():
    spec = ExperimentSpec((0., .5, 1.), time_unit='minute')
    prediction = fitting.predict(decay('minute'), spec, (ObservationSpec('signal', 'x'),), {'k': .4})
    assert prediction['observables']['signal'][-1] == pytest.approx(math.exp(-.4), rel=1e-8)


TEXT = 'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\nTime is measured in minutes.\n'


def source_proposal(tmp_path, store, unit):
    path = tmp_path / 'source.txt'; path.write_text(TEXT)
    source = evidence.ingest_source(store, path)
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),), time_unit=unit)
    return source, {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}


class Scripted:
    last_usage = {}
    def __init__(self, actions):
        self.actions, self.calls = iter(actions), []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return next(self.actions)


def act(name, args=None):
    return {'action': name, 'args': args or {}}


def test_agent_sees_frozen_clock_and_can_repair_it_within_existing_budget(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    source, wrong = source_proposal(tmp_path, store, 'second')
    correct = deepcopy(wrong); correct['draft']['time_unit'] = 'minute'
    provider = Scripted([act('submit_draft', wrong), act('submit_draft', correct), act('run'), act('finish')])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.), time_unit='min')))['payload']
    assert result['state'] == 'completed' and result['repair_count'] == 1 and result['call_count'] == 4
    brief = json.loads(provider.calls[0][1]['content'])['task']['experiment']
    assert brief['time_unit'] == 'minute'
    feedback = json.loads(provider.calls[1][-1]['content'])
    assert feedback['state'] == 'invalid' and 'model clock' in feedback['problems'][0]
    assert len(store.list('run')) == 1
    frozen = store.get(result['task_ref'])['payload']
    assert frozen['experiment']['time_unit'] == 'minute'


def test_resume_cannot_change_the_original_experiment_clock(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    source, wrong = source_proposal(tmp_path, store, 'second')
    parent = agent.run_agent(store, Scripted([act('ask_researcher', {'question': 'Confirm interpretation?'})]),
                             [source], ExperimentSpec((0., 1.), time_unit='minute'))
    # A valid response is followed by finish: the wrong-clock proposal must not run.
    provider = Scripted([act('submit_draft', wrong), act('finish')])
    result = store.get(agent.resume_agent(store, provider, parent, 'Please change the timetable to seconds.',
                                         respondent='Researcher A'))['payload']
    assert result['state'] == 'incomplete' and not store.list('run')
    assert store.get(result['task_ref'])['payload']['experiment']['time_unit'] == 'minute'


def test_cli_accepts_explicit_experiment_clock_and_reports_mismatch(tmp_path, capsys):
    artifact_path = tmp_path / 'model.xml'; artifact_path.write_text(decay().sbml)
    experiment_path = tmp_path / 'experiment.json'
    experiment_path.write_text(json.dumps({'times': [0., 1.], 'time_unit': 'minute'}))
    code = cli.main(['run', str(artifact_path), '--experiment', str(experiment_path),
                     '--store', str(tmp_path / 'store')])
    assert code != 0
    assert 'model clock' in capsys.readouterr().err
