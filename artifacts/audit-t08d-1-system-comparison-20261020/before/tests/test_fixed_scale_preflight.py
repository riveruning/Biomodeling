"""The execution boundary must not convert invalid fixed scales into valid ones."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, application, evidence, modeling, simulation, validation
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, State, Parameter, RateRule, SimulationResult
from biomodel_agent.storage import ArtifactStore


BAD_SCALES = [True, '1', 0., -1., float('nan'), float('inf')]


def model():
    return modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                                          rate_rules=(RateRule('x', '-k*x'),)))


def reference():
    return SimulationResult('succeeded', (0., 1.), {'x': (1., math.exp(-.4))})


class Scripted:
    last_usage = {}
    def __init__(self): self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return {'action': 'ask_researcher', 'args': {'question': 'Clarify mechanism.'}}


@pytest.mark.parametrize('value', BAD_SCALES)
def test_application_rejects_bad_scale_before_store_cache_solver(tmp_path, monkeypatch, value):
    store = ArtifactStore(tmp_path / 'store'); before = store.list()
    monkeypatch.setattr(store, 'list', lambda *a: pytest.fail('cache reached'))
    monkeypatch.setattr(simulation, 'simulate', lambda *a, **kw: pytest.fail('solver reached'))
    with pytest.raises(ValueError, match='scale'):
        application.run_reproduction(store, model(), ExperimentSpec((0., 1.)),
                                     reference=reference(), scales={'x': value})
    monkeypatch.undo()
    assert store.list() == before


@pytest.mark.parametrize('value', BAD_SCALES)
def test_agent_rejects_bad_scale_before_model_call_or_task_write(tmp_path, value):
    store = ArtifactStore(tmp_path / 'store'); before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='scale'):
        agent.run_agent(store, provider, [], ExperimentSpec((0., 1.)),
                        reference=reference(), scales={'x': value})
    assert not provider.calls and store.list() == before


@pytest.mark.parametrize('scales', [{}, {'y': 1.}])
def test_missing_reference_column_scale_rejected_early(tmp_path, scales):
    store = ArtifactStore(tmp_path / 'store'); before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='missing a fixed scale'):
        application.run_reproduction(store, model(), ExperimentSpec((0., 1.)),
                                     reference=reference(), scales=scales)
    with pytest.raises(ValueError, match='missing a fixed scale'):
        agent.run_agent(store, provider, [], ExperimentSpec((0., 1.)),
                        reference=reference(), scales=scales)
    assert not provider.calls and store.list() == before


@pytest.mark.parametrize('scales', [{'': 1.}, {3: 1.}, [], '1'])
def test_malformed_scale_mapping_rejected(scales):
    with pytest.raises((ValueError, TypeError), match='scales'):
        validation.validate_scales(scales)


def test_valid_scales_are_copied_not_changed_or_filled():
    scales = {'x': 2, 'unused': .5}; before = deepcopy(scales)
    fixed = validation.validate_scales(scales, ['x'])
    assert scales == before and fixed == {'x': 2., 'unused': .5} and fixed is not scales
    assert validation.validate_scales(None, ['x']) is None


def test_bad_scale_without_reference_is_still_rejected(tmp_path):
    with pytest.raises(ValueError, match='scale'):
        application.run_reproduction(ArtifactStore(tmp_path), model(), ExperimentSpec((0., 1.)), scales={'x': True})


def test_analytical_reference_correct_scale_cache_replay_and_frozen_task(tmp_path):
    store = ArtifactStore(tmp_path / 'store'); exp = ExperimentSpec((0., 1.))
    first = application.run_reproduction(store, model(), exp, reference=reference(), scales={'x': 2})
    run = store.get(first)['payload']
    assert run['comparison']['status'] == 'pass'
    assert run['comparison']['max_scaled_error'] < 1e-5
    assert run['comparison']['scales'] == {'x': 2.}
    cached = store.get(application.run_reproduction(store, model(), exp, reference=reference(), scales={'x': 2}))['payload']
    assert cached['cached_from'] == first
    replay = store.get(application.replay_run(store, first))['payload']
    assert replay['comparison']['status'] == 'pass' and replay['comparison']['scales'] == {'x': 2.}
    path = tmp_path / 'source.txt'; path.write_text('Synthetic source.')
    source = evidence.ingest_source(store, path); provider = Scripted()
    paused = store.get(agent.run_agent(store, provider, [source], exp, reference=reference(), scales={'x': 2}))['payload']
    task = store.get(paused['task_ref'])['payload']
    assert task['scales'] == {'x': 2.} and type(task['scales']['x']) is float
    assert 'scales' not in json.dumps(provider.calls)


def test_legacy_boolean_scale_checkpoint_rejected_before_reply_write(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'store'); path = tmp_path / 'source.txt'; path.write_text('Synthetic source.')
    source = evidence.ingest_source(store, path)
    with monkeypatch.context() as legacy:
        legacy.setattr(validation, 'validate_scales', lambda scales, *a: scales)
        paused = agent.run_agent(store, Scripted(), [source], ExperimentSpec((0., 1.)),
                                 reference=reference(), scales={'x': True})
    before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='scale'):
        agent.resume_agent(store, provider, paused, 'Researcher reply.', respondent='Researcher A')
    assert not provider.calls and store.list() == before


def test_cli_does_not_turn_boolean_scale_into_numeric_pass(tmp_path, capsys):
    from biomodel_agent import cli
    sbml = tmp_path / 'model.xml'; sbml.write_text(model().sbml)
    exp = tmp_path / 'exp.json'; exp.write_text(json.dumps({'times': [0., 1.]}))
    ref = tmp_path / 'reference.json'
    ref.write_text(json.dumps({'times': [0., 1.], 'values': {'x': [1., math.exp(-.4)]}, 'scales': {'x': True}}))
    result = cli.main(['run', str(sbml), '--experiment', str(exp), '--reference', str(ref), '--store', str(tmp_path / 'store')])
    assert result == 1 and 'positive finite number' in capsys.readouterr().err
    assert not ArtifactStore(tmp_path / 'store').list()


def test_wrong_reference_still_fails_with_valid_fixed_scale(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    wrong = SimulationResult('succeeded', (0., 1.), {'x': (0., 0.)})
    ref = application.run_reproduction(store, model(), ExperimentSpec((0., 1.)), reference=wrong, scales={'x': 2.})
    run = store.get(ref)['payload']
    assert run['comparison']['max_scaled_error'] == pytest.approx(.5)
    assert run['comparison']['status'] == 'fail' and run['scientific_status'] == 'failed'
