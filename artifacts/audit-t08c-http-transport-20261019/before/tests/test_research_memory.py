"""Scoped persistent memory, corrections and task retrieval; no external model."""
from copy import deepcopy
import json
import math

import pytest

from biomodel_agent import agent, agent_tools, bundles, cli, evidence, memory, modeling, skills
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


@pytest.fixture
def setup(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    text = 'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'
    path = tmp_path / 'source.txt'; path.write_text(text)
    source = evidence.ingest_source(store, path)
    artifact = modeling.build_model(ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                                              rate_rules=(RateRule('x', '-k*x'),)))
    model = store.put('model', to_dict(artifact))
    return store, source, model


def add(store, **kwargs):
    return memory.remember(store, **{'project_id': 'p', 'content': 'Check solver tolerance before accepting a run.',
                                   'category': 'experience', 'author': 'Researcher', **kwargs})


def scope(store, source, model=None, project='p'):
    return memory.snapshot(store, project_id=project, source_refs=[source], model_ref=model)


def recall(store, snapshot, source, query='tolerance', **kwargs):
    return memory.recall(store, snapshot, query, source_refs=[source], **kwargs)


def test_reopen_store_project_and_source_isolation(setup):
    store, source, model = setup
    wanted = add(store, source_refs=[source])
    add(store, project_id='other', content='tolerance secret in another project')
    foreign = store.put('source', {'pages': [{'number': 1, 'text': 'private notes'}]})
    add(store, source_refs=[foreign], content='tolerance secret in another paper')
    store = ArtifactStore(store.root)
    result = recall(store, scope(store, source), source)
    assert [r['memory_ref'] for r in result['results']] == [wanted]
    assert result['results'][0]['semantic_support'] == 'unknown'
    assert 'secret' not in json.dumps(result)


def test_model_scoped_decision_requires_matching_model_and_sources(setup):
    store, source, model = setup
    decision = add(store, category='decision', model_ref=model, source_refs=[source], content='Use minute clock.')
    assert decision not in scope(store, source)['memory_refs']
    assert decision in scope(store, source, model)['memory_refs']
    with pytest.raises(ValueError, match='decision requires'):
        add(store, category='decision')
    with pytest.raises(ValueError, match='model artifact'):
        add(store, model_ref=source)


def test_update_and_retract_keep_history_but_invalidate_old_snapshot(setup):
    store, source, model = setup
    old = add(store); frozen = scope(store, source); original = store.get(old)
    new = add(store, supersedes=old, reason='Correct the protocol', content='Check tolerance after updating the solver.')
    assert store.get(old) == original
    with pytest.raises(ValueError, match='stale'):
        recall(store, frozen, source)
    assert scope(store, source)['memory_refs'] == [new]
    retraction = memory.retract(store, new, author='Researcher', reason='Not applicable to current study')
    assert store.get(retraction)['payload']['operation'] == 'retract'
    assert scope(store, source)['memory_refs'] == []
    with pytest.raises(ValueError, match='active'):
        memory.retract(store, old, author='Researcher', reason='Already superseded')


def test_update_cannot_move_project_or_model_scope(setup):
    store, source, model = setup; old = add(store)
    for kwargs in [{'project_id': 'other'}, {'model_ref': model}, {'category': 'preference'}]:
        with pytest.raises(ValueError, match='same project/model/category'):
            add(store, supersedes=old, reason='Changed', **kwargs)
    with pytest.raises(ValueError, match='reason'):
        add(store, supersedes=old)


@pytest.mark.parametrize('kwargs', [{'project_id': ''}, {'content': ''}, {'content': 'x' * 2001},
                                   {'author': ''}, {'category': 'verified_fact'}, {'reason': None}])
def test_bad_memory_rejected_before_writing(setup, kwargs):
    store, _, _ = setup; before = store.list()
    with pytest.raises(ValueError): add(store, **kwargs)
    assert store.list() == before


@pytest.mark.parametrize('kwargs', [{'query': ''}, {'query': '!!!'}, {'query': 'q' * 257},
                                   {'limit': True}, {'max_chars': 10}])
def test_bad_recall_arguments_rejected(setup, kwargs):
    store, source, _ = setup; add(store)
    with pytest.raises(ValueError): recall(store, scope(store, source), source, **kwargs)


def test_recall_budget_omits_whole_records_and_never_truncates_decisions(setup):
    store, source, _ = setup
    for i in range(6): add(store, content='tolerance ' + str(i) + 'x' * 500)
    result = recall(store, scope(store, source), source, max_chars=600)
    assert result['returned_chars'] <= 600 and result['truncated']
    assert len(result['results']) == 1 and result['results'][0]['content'].endswith('x' * 500)


def test_frozen_snapshot_does_not_silently_add_new_memories(setup):
    store, source, _ = setup; first = add(store); frozen = scope(store, source)
    second = add(store, content='tolerance new finding')
    assert [h['memory_ref'] for h in recall(store, frozen, source)['results']] == [first]
    assert set(scope(store, source)['memory_refs']) == {first, second}


class Scripted:
    last_usage = {}
    def __init__(self, actions): self.actions = iter(actions); self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return next(self.actions)


def action(action_name, **args): return {'action': action_name, 'args': args}


def proposal(source):
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),), rate_rules=(RateRule('x', '-k*x'),))
    return {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}


def test_memory_search_skill_real_solver_and_hidden_reference(setup, tmp_path):
    store, source, _ = setup; mem = add(store)
    provider = Scripted([action('recall_memory', query='tolerance'), action('load_skill', name='ode_reproduction'),
                         action('search_sources', query='Parameter k'), {'action': 'submit_draft', 'args': proposal(source)},
                         action('run'), action('finish')])
    reference = SimulationResult('succeeded', (0., 1.), {'x': (1., math.exp(-.4))})
    root = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)),
                           reference=reference, scales={'x': 1.}, memory_project='p')
    run = store.get(root)['payload']
    assert run['state'] == 'completed' and run['call_count'] == 6
    scientific = store.get(run['run_ref'])['payload']
    assert scientific['comparison']['status'] == 'pass'
    assert scientific['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-5)
    assert scientific['scientific_status'] == 'not_verified'
    requests = json.dumps(provider.calls)
    assert mem in requests and 'operator_assertion' in requests
    assert '"reference"' not in requests and '"scales"' not in requests
    task = store.get(run['task_ref'])
    assert mem in task['refs'] and task['payload']['memory_scope']['memory_refs'] == [mem]
    exported = bundles.export_bundle(store, root, tmp_path / 'bundle')
    assert mem in exported['objects_and_blobs']


def test_retraction_blocks_resume_before_reply_write_or_provider_call(setup):
    store, source, _ = setup; mem = add(store)
    provider = Scripted([action('ask_researcher', question='Which study condition?')])
    parent = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), memory_project='p')
    memory.retract(store, mem, author='Researcher', reason='Incorrect advice')
    before = store.list(); provider = Scripted([])
    with pytest.raises(ValueError, match='stale'):
        agent.resume_agent(store, provider, parent, 'Condition A.', respondent='Researcher')
    assert not provider.calls and before == store.list()


def test_read_tools_share_original_call_budget_and_cannot_write_memory(setup):
    store, source, _ = setup; add(store)
    provider = Scripted([action('recall_memory', query='tolerance')] * 2)
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)),
                                      memory_project='p', max_calls=2))['payload']
    assert result['state'] == 'incomplete' and result['call_count'] == 2
    assert 'remember' not in agent.ACTIONS and 'retract' not in agent.ACTIONS


def test_memory_cannot_supply_missing_parameter_evidence(setup):
    store, source, _ = setup
    mem = add(store, content='Parameter missing_k = 5.')
    args = proposal(source); args['draft']['parameters'][0]['value'] = 5.
    args['evidence'][1].update(source_ref=mem, quote='Parameter missing_k = 5.')
    result = agent_tools.submit_draft(store, args, [source])
    assert result['state'] != 'ready' and result['artifact'] is None


def test_builtin_skills_are_versioned_copies_and_cannot_load_arbitrary_paths():
    first = skills.load_skill('ode_reproduction'); first['steps'].clear()
    assert skills.load_skill('ode_reproduction')['steps']
    assert all(s['version'] == 1 for s in skills.catalog())
    with pytest.raises(ValueError): skills.load_skill('/tmp/arbitrary.py')


def test_cli_memory_lifecycle_and_source_search(setup, tmp_path, capsys):
    store, source, _ = setup
    content = tmp_path / 'note.txt'; content.write_text('Review solver tolerance.')
    common = ['--store', str(store.root)]
    assert cli.main(['memory-add', *common, '--project', 'p', '--content-file', str(content),
                     '--category', 'experience', '--author', 'Researcher']) == 0
    ref = json.loads(capsys.readouterr().out)['memory_ref']
    assert cli.main(['memory-search', *common, '--project', 'p', '--query', 'tolerance']) == 0
    assert json.loads(capsys.readouterr().out)['results'][0]['memory_ref'] == ref
    assert cli.main(['source-search', *common, '--source-ref', source, '--query', 'Parameter']) == 0
    assert json.loads(capsys.readouterr().out)['results'][0]['source_ref'] == source
    assert cli.main(['memory-retract', ref, *common, '--author', 'Researcher', '--reason', 'Obsolete']) == 0
    capsys.readouterr()
    assert cli.main(['memory-search', *common, '--project', 'p', '--query', 'tolerance']) == 0
    assert not json.loads(capsys.readouterr().out)['results']


def test_exported_pause_resumes_with_same_frozen_memory_and_budget(setup, tmp_path):
    store, source, _ = setup; mem = add(store)
    parent = agent.run_agent(store, Scripted([action('ask_researcher', question='Confirm condition?')]),
                             [source], ExperimentSpec((0., 1.)), memory_project='p')
    bundles.export_bundle(store, parent, tmp_path / 'portable')
    reopened = ArtifactStore(tmp_path / 'portable')
    provider = Scripted([action('recall_memory', query='tolerance'),
                         {'action': 'submit_draft', 'args': proposal(source)}, action('run'), action('finish')])
    ref = agent.resume_agent(reopened, provider, parent, 'Condition A.', respondent='Researcher')
    result = reopened.get(ref)['payload']
    assert result['state'] == 'completed' and result['call_count'] == 5
    assert reopened.get(result['task_ref'])['payload']['memory_scope']['memory_refs'] == [mem]
    assert mem in json.dumps(provider.calls)


def test_memory_invalidated_during_task_stops_before_next_call(setup):
    store, source, _ = setup; mem = add(store)
    class Retracting(Scripted):
        def respond(self, messages):
            self.calls.append(deepcopy(messages))
            memory.retract(store, mem, author='Researcher', reason='Incorrect')
            return action('load_skill', name='ode_reproduction')
    provider = Retracting([])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), memory_project='p'))['payload']
    assert result['state'] == 'incomplete' and result['call_count'] == len(provider.calls) == 1
    assert any(e['type'] == 'memory_invalidated' for e in result['events'])


def test_conflicting_update_branches_do_not_silently_choose_one(setup):
    store, source, _ = setup; old = add(store)
    child = add(store, supersedes=old, reason='First update')
    fork = dict(store.get(child)['payload'], id='second-child', content='Conflicting advice')
    store.put(memory.KIND, fork, refs=(old,))
    with pytest.raises(ValueError, match='conflicting update branches'):
        scope(store, source)


def test_memory_disabled_by_default_cannot_search_other_projects(setup):
    store, source, _ = setup; add(store)
    provider = Scripted([action('recall_memory', query='tolerance')])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), max_calls=1))['payload']
    assert result['state'] == 'incomplete'
    assert any(e.get('error') == 'memory is not enabled for this task' for e in result['events'])
    assert 'Check solver tolerance' not in json.dumps(provider.calls)


def test_extra_retrieval_scope_arguments_cannot_expand_source_whitelist(setup):
    store, source, _ = setup
    provider = Scripted([action('search_sources', query='Parameter', source_refs=['not-authorized'])])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), max_calls=1))['payload']
    assert result['state'] == 'incomplete' and any(e['type'] == 'tool_error' for e in result['events'])


def test_snapshot_size_limit_is_explicit(setup):
    store, source, _ = setup
    for i in range(101): add(store, content=f'Note {i}')
    with pytest.raises(ValueError, match='100 entries'):
        scope(store, source)


def test_operator_can_resolve_conflicting_memory_by_retracting_one_head(setup):
    store, source, _ = setup; old = add(store)
    selected = add(store, supersedes=old, reason='First update')
    fork = dict(store.get(selected)['payload'], id='racing-update', content='Alternative advice')
    rejected = store.put(memory.KIND, fork, refs=(old,))
    with pytest.raises(ValueError, match='conflicting'):
        scope(store, source)
    memory.retract(store, rejected, author='Researcher', reason='Keep the reviewed first update')
    assert scope(store, source)['memory_refs'] == [selected]
    assert store.get(rejected)['payload']['content'] == 'Alternative advice'
