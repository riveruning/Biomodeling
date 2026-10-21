"""Frozen hypothesis study tool: real fitting, scope and holdout isolation."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, agent_tools, bundles, cli, evidence, fitting, reporting, studies
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.storage import ArtifactStore


def condition(name='train', role='training', *, bad=False):
    times = [0., .5, 1., 2., 4.]
    return {'id': name, 'role': role, 'experiment': {'times': times, 'time_unit': 'second'},
            'values': {'response': [99. if bad else 2. * (1. - math.exp(-t)) for t in times]},
            'sigma': {'response': .01}, 'scales': {'response': 1.}}


def config():
    return {'training': [condition()], 'holdout': [condition('private-holdout-id', 'holdout')],
            'candidates': ['cand_baseline'], 'starts': 1, 'max_nfev': 60, 'timeout_s': 20.}


@pytest.fixture
def setup(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'protocol.txt'; path.write_text('Compare only the operator-authorized adaptation templates.')
    source = evidence.ingest_source(store, path)
    plan = studies.prepare_study(store, config())
    exp = from_dict(ExperimentSpec, studies.load_plan(store, plan)['config']['training'][0]['experiment'])
    return store, source, plan, exp


class Scripted:
    last_usage = {}
    def __init__(self, actions): self.actions = iter(actions); self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return next(self.actions)


def act(action, **args): return {'action': action, 'args': args}


def start(setup, provider, **kwargs):
    store, source, plan, exp = setup
    return agent.run_agent(store, provider, [source], exp, mode='hypothesis', study_plan_ref=plan, **kwargs)


def test_plan_is_frozen_and_model_summary_hides_dataset_values(setup):
    store, _, plan, _ = setup
    summary = studies.plan_summary(store, plan)
    assert summary['training_conditions'] == summary['holdout_conditions'] == 1
    assert summary['candidates'] == ['cand_baseline']
    text = json.dumps(summary)
    assert 'private-holdout-id' not in text and 'values' not in summary
    data = config(); same = studies.prepare_study(store, data); data['training'][0]['values']['response'][0] = 42.
    assert same == plan and studies.load_plan(store, plan)['config']['training'][0]['values']['response'][0] == 0.


@pytest.mark.parametrize('change', [
    {'candidates': []}, {'candidates': ['invented']}, {'candidates': ['cand_baseline'] * 2},
    {'starts': True}, {'starts': 9}, {'seed': -1}, {'max_nfev': 1001}, {'max_nfev': '100'},
    {'timeout_s': float('inf')}, {'timeout_s': 91}, {'timeout_s': False},
    {'separation_threshold': 0}, {'bounds': {'k': [0, 10]}}, {'training': []},
])
def test_invalid_plan_rejected_without_writing(setup, change):
    store, _, _, _ = setup; before = store.list()
    with pytest.raises(ValueError): studies.prepare_study(store, {**config(), **change})
    assert store.list() == before


@pytest.mark.parametrize('field,value', [
    ('role', 'holdout'), ('sigma', {'response': True}), ('sigma', {'response': 0}),
    ('scales', {'response': '1'}), ('values', {'wrong': [1] * 5}),
    ('values', {'response': [float('nan')] * 5}), ('values', {'response': [True] * 5}),
    ('values', {'response': [1.]}), ('notes', 'unrecognized'),
])
def test_bad_dataset_protocol_fails_before_any_fit(setup, field, value):
    store, _, _, _ = setup; before = store.list(); data = config(); data['training'][0][field] = value
    with pytest.raises(ValueError): studies.prepare_study(store, data)
    assert store.list() == before


def test_data_roles_and_ids_are_disjoint(setup):
    store, _, _, _ = setup; data = config(); data['holdout'][0]['id'] = 'train'
    with pytest.raises(ValueError, match='disjoint'): studies.prepare_study(store, data)
    data = config(); data['holdout'][0]['role'] = 'training'
    with pytest.raises(ValueError, match='role'): studies.prepare_study(store, data)


@pytest.mark.parametrize('experiment', [
    {'times': [0., 1.], 'time_unit': 'minute'},
    {'times': [0., 1.], 'parameter_overrides': {'k': .7}},
    {'times': [0., 1.], 'parameter_overrides': {'b': 11.}},
    {'times': [0., 1.], 'initial_overrides': {'z': 0.}},
    {'times': [-1., 1.]}, {'times': [0., 10001.]},
])
def test_unsupported_experiment_profile_is_rejected(setup, experiment):
    store, _, _, _ = setup; data = config(); data['training'][0]['experiment'] = experiment
    data['training'][0]['values']['response'] = [0., 1.]
    with pytest.raises(ValueError): studies.prepare_study(store, data)


def test_forged_or_wrong_kind_plan_is_rejected_before_model_call(setup):
    store, source, plan, exp = setup; original = store.get(plan)['payload']
    changed = deepcopy(original); changed['bounds']['cand_baseline']['k'] = [.01, 100.]
    altered = store.put(studies.PLAN_KIND, changed)
    for ref in [source, altered]:
        before = store.list(); provider = Scripted([])
        with pytest.raises(ValueError):
            agent.run_agent(store, provider, [source], exp, mode='hypothesis', study_plan_ref=ref)
        assert not provider.calls and store.list() == before


def test_plan_requires_hypothesis_mode_and_its_frozen_experiment(setup):
    store, source, plan, exp = setup
    with pytest.raises(ValueError, match='hypothesis mode'):
        agent.run_agent(store, Scripted([]), [source], exp, study_plan_ref=plan)
    with pytest.raises(ValueError, match='first frozen training'):
        agent.run_agent(store, Scripted([]), [source], ExperimentSpec((0., 1.)), mode='hypothesis', study_plan_ref=plan)


def test_real_fit_agent_tool_holdout_isolation_and_report(setup, tmp_path, monkeypatch):
    store, _, plan, _ = setup; received = []; original = fitting.fit_parameters
    def spy(artifact, training, *args, **kwargs):
        received.append(deepcopy(training))
        return original(artifact, training, *args, **kwargs)
    monkeypatch.setattr(fitting, 'fit_parameters', spy)
    provider = Scripted([act('load_skill', name='constrained_study'), act('run_study'), act('finish')])
    root = start(setup, provider)
    run = store.get(root)['payload']; result = store.get(run['run_ref'])['payload']
    assert run['state'] == 'completed' and run['call_count'] == 3
    assert len(received) == 1 and all(r['role'] == 'training' and r['id'] == 'train' for r in received[0])
    assert result['plan_ref'] == plan and result['completion_eligible']
    assert result['candidates']['cand_baseline']['parameters']['k'] == pytest.approx(2., abs=1e-4)
    assert result['candidates']['cand_baseline']['parameters']['d'] == pytest.approx(1., abs=1e-4)
    assert result['candidates']['cand_baseline']['heldout_scaled_rmse'] < 1e-5
    assert result['comparison']['winner'] == 'cand_baseline' and result['scientific_status'] == 'not_verified'
    messages = json.dumps(provider.calls)
    assert 'private-holdout-id' not in messages and '"values"' not in messages
    assert 'best_compatible_candidate' in reporting.render_report(store, root)
    exported = bundles.export_bundle(store, root, tmp_path / 'bundle')
    assert plan in exported['objects_and_blobs']


def test_heldout_failure_is_an_honest_negative_result(setup):
    store, source, _, _ = setup; data = config(); data['holdout'] = [condition('negative-holdout', 'holdout', bad=True)]
    plan = studies.prepare_study(store, data)
    provider = Scripted([act('run_study'), act('finish')])
    root = start((store, source, plan, setup[3]), provider)
    run = store.get(root)['payload']; result = store.get(run['run_ref'])['payload']
    assert run['state'] == 'completed' and result['comparison']['ranking'] == 'none_adequate'
    assert result['comparison']['winner'] is None and result['scientific_status'] == 'not_verified'
    assert result['candidates']['cand_baseline']['heldout_weighted_rmse'] > 3.


def test_no_plan_or_nonempty_args_cannot_start_or_modify_a_study(setup, monkeypatch):
    store, source, _, exp = setup
    monkeypatch.setattr(studies, 'execute_study', lambda *a: pytest.fail('unauthorized study executed'))
    provider = Scripted([act('run_study')])
    result = store.get(agent.run_agent(store, provider, [source], exp, max_calls=1))['payload']
    assert result['state'] == 'incomplete' and result['run_ref'] is None
    provider = Scripted([act('run_study', bounds={'k': [.01, 100.]})])
    result = store.get(start(setup, provider, max_calls=1))['payload']
    assert result['state'] == 'incomplete' and result['run_ref'] is None


def test_frozen_study_cannot_submit_another_model(setup, monkeypatch):
    monkeypatch.setattr(agent_tools, 'submit_draft', lambda *a: pytest.fail('changed candidate was submitted'))
    store = setup[0]; provider = Scripted([act('submit_draft', draft={}), act('run')])
    result = store.get(start(setup, provider, max_calls=2))['payload']
    assert result['state'] == 'incomplete' and result['run_ref'] is None
    assert sum(e['type'] == 'tool_error' for e in result['events']) == 2


def test_timeout_only_study_does_not_become_completed(setup, monkeypatch):
    store, _, _, _ = setup
    monkeypatch.setattr(fitting, 'fit_parameters', lambda *a, **kw:
                        {'status': 'timed_out', 'best': None, 'starts': [], 'diagnostics': ['test timeout']})
    provider = Scripted([act('run_study'), act('run_study'), act('finish')])
    result = store.get(start(setup, provider))['payload']
    study = store.get(result['run_ref'])['payload']
    assert result['state'] == 'incomplete' and not study['completion_eligible']
    assert study['usable_candidates'] == 0 and study['comparison']['winner'] is None
    assert len(store.list('fitting')) == 1
    assert any(e['type'] == 'tool_error' and e.get('tool') == 'run_study' for e in result['events'])


def test_resume_after_evaluation_cannot_refit_after_seeing_holdout(setup, monkeypatch):
    store, _, _, _ = setup
    first = start(setup, Scripted([act('run_study'), act('ask_researcher', question='How should results be described?')]))
    original = store.get(first)['payload']; fitted = store.list('fitting')
    monkeypatch.setattr(studies, 'execute_study', lambda *a: pytest.fail('second evaluation executed'))
    provider = Scripted([act('run_study'), act('finish')])
    resumed = store.get(agent.resume_agent(store, provider, first, 'Report the limitations.', respondent='Researcher'))['payload']
    assert resumed['state'] == 'completed' and resumed['call_count'] == 4
    assert resumed['run_ref'] == original['run_ref'] and store.list('fitting') == fitted


def test_failure_diagnostic_is_saved_without_leaking_into_model_context(setup, monkeypatch):
    store, _, _, _ = setup
    def failure(*args): raise RuntimeError('PRIVATE_INTERNAL_CANARY')
    monkeypatch.setattr(studies, 'execute_study', failure)
    provider = Scripted([act('run_study')])
    run = store.get(start(setup, provider))['payload']
    assert run['state'] == 'incomplete' and run['run_ref'] is None
    refs = store.list('study_execution_failure')
    assert len(refs) == 1 and 'PRIVATE_INTERNAL_CANARY' in store.get(refs[0])['payload']['error']
    assert refs[0] in store.get(store.list('agent_run')[-1])['refs']
    assert 'PRIVATE_INTERNAL_CANARY' not in json.dumps(run['events'])


def test_cli_prepare_and_offline_run(setup, tmp_path, capsys):
    store, _, _, _ = setup; data = config()
    train = tmp_path / 'training.json'; train.write_text(json.dumps(data['training']))
    holdout = tmp_path / 'holdout.json'; holdout.write_text(json.dumps(data['holdout']))
    common = ['--store', str(store.root)]
    assert cli.main(['study-prepare', *common, '--training', str(train), '--holdout', str(holdout),
                     '--candidate', 'cand_baseline', '--starts', '1', '--max-nfev', '60']) == 0
    plan = json.loads(capsys.readouterr().out)['plan_ref']
    assert cli.main(['study-run', plan, *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['completion_eligible'] and result['candidates']['cand_baseline']['parameters']['k'] == pytest.approx(2., abs=1e-4)


def test_cli_agent_uses_plan_experiment_without_separate_file(setup, tmp_path, monkeypatch, capsys):
    store, _, plan, _ = setup
    provider = Scripted([act('run_study'), act('finish')])
    import biomodel_agent.llm as llm
    monkeypatch.setattr(llm, 'ChatProvider', lambda **kwargs: provider)
    monkeypatch.setenv('BIOMODEL_API_KEY', 'offline-test-value')
    source_path = tmp_path / 'protocol.txt'
    assert cli.main(['agent', '--store', str(store.root), '--source', str(source_path),
                     '--mode', 'hypothesis', '--study-plan-ref', plan,
                     '--base-url', 'https://example.invalid/v1', '--model', 'scripted']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'completed'


@pytest.mark.parametrize('with_holdout', [False, True])
def test_analytically_equivalent_training_candidates_require_intervention_to_separate(tmp_path, with_holdout):
    # Feedback: x'=2-1.2x-z, z'=.2x. Feed-forward: x'=2(1-z)-x, z'=.2(1-z).
    # Both have x(t)=2.5(exp(-.2t)-exp(-t)) from zero states. Under b=0,
    # the feedback x(t)=(5/3)(1-exp(-1.2t)); this differentiates the structures.
    times = [i * .5 for i in range(61)]
    train = {'id': 'analytic_training', 'role': 'training', 'experiment': {'times': times, 'time_unit': 'second'},
             'values': {'response': [2.5 * (math.exp(-.2*t) - math.exp(-t)) for t in times]},
             'sigma': {'response': .01}, 'scales': {'response': 1.}}
    hold = {'id': 'analytic_intervention', 'role': 'holdout',
            'experiment': {'times': times, 'time_unit': 'second', 'parameter_overrides': {'b': 0.}},
            'values': {'response': [(5/3) * (1 - math.exp(-1.2*t)) for t in times]},
            'sigma': {'response': .01}, 'scales': {'response': 1.}}
    store = ArtifactStore(tmp_path / 'study')
    plan = studies.prepare_study(store, {'training': [train], 'holdout': [hold] if with_holdout else [],
                                       'starts': 1, 'max_nfev': 100, 'timeout_s': 30.})
    result = store.get(studies.execute_study(store, plan))['payload']
    assert result['candidates']['cand_feedback']['training_weighted_rmse'] < .001
    assert result['candidates']['cand_feedforward']['training_weighted_rmse'] < .001
    if with_holdout:
        assert result['comparison']['ranking'] == 'best_compatible_candidate'
        assert result['comparison']['winner'] == 'cand_feedback'
        assert result['candidates']['cand_feedback']['heldout_scaled_rmse'] < 1e-5
    else:
        assert result['comparison']['ranking'] == 'indistinguishable_from_training'
        assert result['comparison']['winner'] is None


@pytest.mark.parametrize('study_mode', [False, True])
def test_skill_catalog_only_advertises_procedures_compatible_with_task(setup, study_mode):
    store, source, _, exp = setup
    unavailable = 'ode_reproduction' if study_mode else 'constrained_study'
    provider = Scripted([act('load_skill', name=unavailable)])
    root = start(setup, provider, max_calls=1) if study_mode else agent.run_agent(store, provider, [source], exp, max_calls=1)
    result = store.get(root)['payload']; task = store.get(result['task_ref'])['payload']
    assert unavailable not in [s['name'] for s in task['skill_catalog']]
    assert result['state'] == 'incomplete'
    assert any(e['type'] == 'tool_error' and e.get('tool') == 'load_skill' for e in result['events'])
