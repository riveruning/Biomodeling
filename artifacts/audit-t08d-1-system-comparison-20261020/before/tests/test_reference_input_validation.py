"""Unusable reference results cannot become successful curated CLI inputs."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import agent, application, cli, evidence, modeling, simulation, validation
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State
from biomodel_agent.domain.serialization import SchemaError, to_dict
from biomodel_agent.storage import ArtifactStore


def payload():
    return {'times': [0., 1.], 'values': {'x': [1., .5]}, 'scales': {'x': 1.}}


def model():
    return modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                                          rate_rules=(RateRule('x', '-k*x'),)))


@pytest.mark.parametrize('status', ['failed', 'timed_out', 'unsupported', 'invalid'])
def test_explicit_failed_or_invalid_status_never_becomes_success(status):
    data = payload(); data['status'] = status
    with pytest.raises(ValueError):
        cli._build_reference(data)


@pytest.mark.parametrize('field,value', [('checks', {'checks': 'bad'}), ('backend', 3),
                                         ('elapsed_s', 'slow'), ('diagnostics', [False]),
                                         ('time_unit', 'day'), ('status', None)])
def test_scoring_metadata_does_not_hide_invalid_structured_fields(field, value):
    data = payload(); data[field] = value
    with pytest.raises(ValueError):
        cli._build_reference(data)


def test_full_result_plus_scoring_metadata_keeps_structured_fields():
    original = SimulationResult('succeeded', (0., 1.), {'x': (1., .5)},
                                diagnostics=('external reference generator',), backend='independent', elapsed_s=.3)
    data = {**to_dict(original), 'scales': {'x': 1.}, 'threshold': .001}
    before = deepcopy(data)
    assert cli._build_reference(data) == original and data == before


def test_curated_documented_provenance_is_accepted_without_fabricating_evidence():
    data = {**payload(), 'schema_version': 1, 'track': 'numeric', 'model_id': 'synthetic',
            'reference_methods': ['independent solver'], 'cross_method_error': 0., 'limitations': ['synthetic']}
    result = cli._build_reference(data)
    assert result.status == 'succeeded' and not result.checks.checks


@pytest.mark.parametrize('data', [None, [], {'times': [], 'values': {}},
                                  {**payload(), 'staus': 'failed'},
                                  {**payload(), 'values': {'x': [1., float('nan')]}},
                                  {**payload(), 'times': [1., 0.]},
                                  {**payload(), 'times': [0., 0.]}])
def test_bad_reference_rejected_by_cli(data):
    with pytest.raises(ValueError):
        cli._build_reference(data)


def bad_references():
    return [SimulationResult('failed', (0., 1.), {'x': (1., .5)}),
            SimulationResult('succeeded'),
            SimulationResult('succeeded', (0., 1.), {'x': (1., float('inf'))}),
            SimulationResult('succeeded', (1., 0.), {'x': (1., .5)})]


@pytest.mark.parametrize('reference', bad_references())
def test_application_rejects_before_cache_store_or_solver(tmp_path, monkeypatch, reference):
    store = ArtifactStore(tmp_path / 'store'); before = store.list()
    monkeypatch.setattr(simulation, 'simulate', lambda *a, **kw: pytest.fail('solver called'))
    monkeypatch.setattr(store, 'list', lambda *a: pytest.fail('cache called'))
    with pytest.raises(ValueError, match='reference'):
        application.run_reproduction(store, model(), ExperimentSpec((0., 1.)),
                                     reference=reference, scales={'x': 1.})
    monkeypatch.undo()
    assert store.list() == before


class Scripted:
    last_usage = {}
    def __init__(self): self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return {'action': 'ask_researcher', 'args': {'question': 'Clarify mechanism.'}}


@pytest.mark.parametrize('reference', bad_references())
def test_agent_rejects_before_task_write_or_model_call(tmp_path, reference):
    store = ArtifactStore(tmp_path / 'store'); before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='reference'):
        agent.run_agent(store, provider, [], ExperimentSpec((0., 1.)), reference=reference)
    assert not provider.calls and store.list() == before


def test_old_failed_reference_checkpoint_is_rejected_before_reply_write(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'store'); provider = Scripted()
    source_path = tmp_path / 'source.txt'; source_path.write_text('Synthetic source.')
    source = evidence.ingest_source(store, source_path)
    # Build a genuine paused checkpoint using the old missing-preflight behavior.
    with monkeypatch.context() as legacy:
        legacy.setattr(validation, 'validate_reference', lambda *a: None)
        parent = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)),
                                 reference=bad_references()[0], scales={'x': 1.})
    before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='reference simulation did not succeed'):
        agent.resume_agent(store, provider, parent, 'Researcher reply.', respondent='Researcher A')
    assert not provider.calls and store.list() == before


def test_failed_reference_cli_run_creates_no_run_record(tmp_path, capsys):
    sbml = tmp_path / 'model.xml'; sbml.write_text(model().sbml)
    experiment = tmp_path / 'experiment.json'; experiment.write_text(json.dumps({'times': [0., 1.]}))
    reference = tmp_path / 'reference.json'
    reference.write_text(json.dumps({**payload(), 'status': 'failed'}))
    code = cli.main(['run', str(sbml), '--experiment', str(experiment), '--reference', str(reference),
                     '--store', str(tmp_path / 'store')])
    assert code == 1 and 'reference simulation did not succeed' in capsys.readouterr().err
    assert not ArtifactStore(tmp_path / 'store').list()


@pytest.mark.parametrize('which', ['actual', 'reference'])
@pytest.mark.parametrize('status', ['failed', 'timed_out'])
def test_mapping_comparison_honors_explicit_failure_status(which, status):
    actual = payload(); reference = payload()
    (actual if which == 'actual' else reference)['status'] = status
    with pytest.raises(ValueError, match=f'{which} simulation did not succeed'):
        validation.compare_series(actual, reference, {'x': 1.})
