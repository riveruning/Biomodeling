"""Declared reference clocks cannot be silently dropped or compared by numbers alone."""
from copy import deepcopy
import json
import math
import pickle

import pytest

from biomodel_agent import agent, application, cli, evidence, fitting, modeling, reporting, simulation, time_units, validation
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, ObservationSpec, Parameter, RateRule, SimulationResult, State
from biomodel_agent.domain.serialization import from_dict, to_dict
from biomodel_agent.storage import ArtifactStore


def series(unit='unknown'):
    return SimulationResult('succeeded', (0., 1.), {'x': (1., math.exp(-.4))}, time_unit=unit)


def decay(unit='minute'):
    return modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),), time_unit=unit))


def test_simulation_result_clock_survives_round_trip_and_old_json_omission():
    result = series('min')
    assert result.time_unit == 'minute'
    assert from_dict(SimulationResult, to_dict(result)) == result
    assert pickle.loads(pickle.dumps(result)) == result
    old = to_dict(result); old.pop('time_unit')
    assert from_dict(SimulationResult, old).time_unit == 'unknown'


@pytest.mark.parametrize('unit', [None, '', 60, 'day'])
def test_result_does_not_coerce_invalid_clock_metadata(unit):
    with pytest.raises(ValueError, match='time_unit'):
        series(unit)


@pytest.mark.parametrize('left, right', [('second', 'minute'), ('minute', 'second'), ('hour', 'minute'),
                                      ('minute', 'unknown'), ('unknown', 'minute')])
@pytest.mark.parametrize('as_dict', [False, True])
def test_identical_numbers_do_not_make_incompatible_clocks_comparable(left, right, as_dict):
    actual, reference = series(left), series(right)
    if as_dict:
        actual, reference = to_dict(actual), to_dict(reference)
    with pytest.raises(ValueError, match='[Tt]ime_unit|[Tt]ime units'):
        validation.compare_series(actual, reference, {'x': 1.})


def test_aliases_match_and_comparison_records_verified_clock():
    result = validation.compare_series(series('mins'), series('minute'), {'x': 1.})
    assert result['status'] == 'pass' and result['time_unit'] == 'minute'
    assert result['time_unit_status'] == 'pass'


def test_both_legacy_unlabelled_dicts_keep_numeric_comparison_explicitly_unknown():
    old = {'times': [0., 1.], 'values': {'x': [1., .5]}}
    result = validation.compare_series(old, old, {'x': 1.})
    assert result['status'] == 'pass' and result['time_unit_status'] == 'unknown'
    assert result['time_unit'] == 'unknown'


def test_simulation_and_fitting_keep_the_requested_clock():
    spec = ExperimentSpec((0., 1.), time_unit='minute')
    actual = simulation.simulate(decay(), spec)
    assert actual.status == 'succeeded' and actual.time_unit == 'minute'
    predicted = fitting.predict(decay(), spec, (ObservationSpec('signal', 'x'),), {'k': .4})
    assert predicted['time_unit'] == 'minute'
    assert predicted['observables']['signal'][-1] == pytest.approx(actual.values['x'][-1], rel=1e-6)


@pytest.mark.parametrize('reference_unit', ['second', 'hour', 'unknown'])
def test_reference_protocol_fails_before_cache_store_or_solver(tmp_path, monkeypatch, reference_unit):
    store = ArtifactStore(tmp_path / 'store')
    before = store.list()
    monkeypatch.setattr(store, 'list', lambda *a: pytest.fail('mismatched reference reached cache'))
    monkeypatch.setattr(simulation, 'simulate', lambda *a, **kw: pytest.fail('mismatched reference reached solver'))
    with pytest.raises(ValueError):
        application.run_reproduction(store, decay(), ExperimentSpec((0., 1.), time_unit='minute'),
                                     reference=series(reference_unit), scales={'x': 1.})
    monkeypatch.undo()
    assert store.list() == before


def test_matched_reference_records_clock_in_result_report_and_replay(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    ref = application.run_reproduction(store, decay(), ExperimentSpec((0., 1.), time_unit='minute'),
                                       reference=series('minute'), scales={'x': 1.})
    run = store.get(ref)['payload']
    assert run['comparison']['status'] == 'pass'
    assert run['comparison']['time_unit_status'] == 'pass'
    assert run['simulation']['time_unit'] == 'minute'
    report = reporting.render_report(store, ref)
    assert 'time_unit_status' in report and 'minute' in report
    replay = store.get(application.replay_run(store, ref))['payload']
    assert replay['comparison']['time_unit_status'] == 'pass'
    frozen = store.get(replay['input_ref'])['payload']
    assert frozen['reference']['time_unit'] == frozen['experiment']['time_unit'] == 'minute'


def test_legacy_numerical_reference_does_not_claim_clock_verified(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    ref = application.run_reproduction(store, decay(), ExperimentSpec((0., 1.)),
                                       reference=series(), scales={'x': 1.})
    result = store.get(ref)['payload']
    assert result['comparison']['status'] == 'pass'
    assert result['comparison']['time_unit_status'] == 'unknown'
    assert result['simulation']['time_unit'] == 'unknown'


def test_cli_shorthand_reference_preserves_unit_even_with_scoring_metadata():
    payload = {'times': [0., 1.], 'values': {'x': [1., .5]},
               'scales': {'x': 1.}, 'threshold': .001, 'time_unit': 'min'}
    built = cli._build_reference(payload)
    assert built.time_unit == 'minute'


def test_cli_does_not_discard_reference_clock_to_allow_a_mismatch(tmp_path, capsys):
    model = tmp_path / 'model.xml'; model.write_text(decay().sbml)
    experiment = tmp_path / 'experiment.json'
    experiment.write_text(json.dumps({'times': [0., 1.], 'time_unit': 'minute'}))
    reference = tmp_path / 'reference.json'
    reference.write_text(json.dumps({'times': [0., 1.], 'values': {'x': [1., .5]},
                                     'time_unit': 'second', 'scales': {'x': 1.}}))
    code = cli.main(['run', str(model), '--experiment', str(experiment), '--reference', str(reference),
                     '--store', str(tmp_path / 'store')])
    assert code != 0 and 'Time units differ' in capsys.readouterr().err
    assert not ArtifactStore(tmp_path / 'store').list('run')


class Scripted:
    last_usage = {}
    def __init__(self):
        self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return {'action': 'ask_researcher', 'args': {'question': 'Please clarify the mechanism.'}}


def test_agent_rejects_mismatched_reference_before_model_calls_or_task_write(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'; path.write_text('Synthetic model source.')
    source = evidence.ingest_source(store, path)
    before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='Time units differ'):
        agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.), time_unit='minute'),
                        reference=series('second'), scales={'x': 1.})
    assert not provider.calls and store.list() == before


def test_agent_frozen_reference_unit_does_not_enter_model_context(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'; path.write_text('Synthetic model source.')
    source = evidence.ingest_source(store, path)
    provider = Scripted()
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.), time_unit='minute'),
                          reference=series('minute'), scales={'x': 1.})
    result = store.get(ref)['payload']; task = store.get(result['task_ref'])['payload']
    assert task['reference']['time_unit'] == 'minute'
    assert 'reference' not in json.dumps(provider.calls)


def test_old_ambiguous_reference_checkpoint_refuses_resume_before_reply_write(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'; path.write_text('Synthetic model source.')
    source = evidence.ingest_source(store, path)
    # Reproduce the old behavior, which did not check the reference clock when
    # creating a paused task. The checkpoint is otherwise made by the real agent.
    with monkeypatch.context() as legacy:
        legacy.setattr(time_units, 'comparison_time_unit', lambda *a: {})
        parent = agent.run_agent(store, Scripted(), [source], ExperimentSpec((0., 1.), time_unit='minute'),
                                 reference=series(), scales={'x': 1.})
    before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='Both series must declare time_unit'):
        agent.resume_agent(store, provider, parent, 'Clarification text.', respondent='Researcher A')
    assert not provider.calls and store.list() == before


def test_resume_preserves_matched_reference_clock_and_keeps_it_internal(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'; path.write_text('Synthetic model source.')
    source = evidence.ingest_source(store, path)
    parent = agent.run_agent(store, Scripted(), [source], ExperimentSpec((0., 1.), time_unit='minute'),
                             reference=series('minute'), scales={'x': 1.})
    original = store.get(parent); provider = Scripted()
    resumed = agent.resume_agent(store, provider, parent, 'Clarification text.', respondent='Researcher A')
    result = store.get(resumed)['payload']; task = store.get(result['task_ref'])['payload']
    assert result['task_ref'] == original['payload']['task_ref']
    assert task['reference']['time_unit'] == task['experiment']['time_unit'] == 'minute'
    assert 'reference' not in json.dumps(provider.calls)
    assert store.get(parent) == original
