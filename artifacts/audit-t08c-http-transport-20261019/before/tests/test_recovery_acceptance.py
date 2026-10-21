"""Codex-owned acceptance: scripted inference, real tools, real process exits."""
from copy import deepcopy
import json
import multiprocessing as mp
import os

import pytest

from biomodel_agent import agent, application, bundles, recovery, cli, studies, memory
from biomodel_agent.domain.contracts import ExperimentSpec, SimulationResult
from biomodel_agent.storage import ArtifactStore
from test_invocation_acceptance import Scripted, act, fixture, run, phase


def execution(store):
    return store.list('agent_execution')[-1]


def post_result_crash(store, source, proposal, monkeypatch, **kwargs):
    provider = Scripted([{'action': 'submit_draft', 'args': proposal}, act('run')])
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_run_facts', lambda *a: (_ for _ in ()).throw(SystemExit('saved run')))
        with pytest.raises(SystemExit):
            run(store, source, provider, **kwargs)
    return provider, execution(store)


def test_adopt_real_simulation_after_crash_without_running_or_resubmitting_draft(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    original, ref = post_result_crash(store, source, proposal, monkeypatch)
    saved_runs = store.list('run')
    saved_models = store.list('model')
    report = recovery.diagnose_execution(store, ref)
    assert report['status'] == 'ready', report
    assert report['adoptable_result_refs'] == saved_runs
    provider = Scripted([act('finish')])
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('simulation repeated'))
    monkeypatch.setattr(agent.agent_tools, 'submit_draft', lambda *a, **k: pytest.fail('draft repeated'))
    final = store.get(agent.recover_agent(store, provider, ref))
    result = final['payload']
    assert result['state'] == 'completed' and result['call_count'] == 3
    assert len(original.calls) == 2 and len(provider.calls) == 1
    assert result['run_ref'] == saved_runs[0] and store.list('run') == saved_runs
    assert store.list('model') == saved_models and len(store.list('agent_execution')) == 1
    assert store.get(saved_runs[0])['payload']['simulation']['values']['x'][-1] == pytest.approx(.670320046, abs=1e-6)
    assert store.get(saved_runs[0])['payload']['scientific_status'] == 'not_verified'
    assert len(result['recovery_refs']) == 1
    manifest = bundles.export_bundle(store, next(r for r in store.list('agent_run')), tmp_path / 'bundle')
    assert set(result['recovery_refs'] + result['invocation_refs'] + [ref]).issubset(manifest['objects_and_blobs'])


def test_returned_response_is_adopted_at_exhausted_budget(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    old = Scripted([act('ask_researcher', question='Confirm x?')])
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', lambda *a: (_ for _ in ()).throw(SystemExit()))
        with pytest.raises(SystemExit):
            run(store, source, old, max_calls=1)
    provider = Scripted([])
    ref = agent.recover_agent(store, provider, execution(store))
    result = store.get(ref)['payload']
    assert result['state'] == 'awaiting_input' and result['call_count'] == 1
    assert not provider.calls
    calls = [e for e in result['events'] if e['type'] == 'llm_call']
    assert len(calls) == 1 and calls[0]['usage'] == {'total_tokens': 3}


def test_returned_draft_is_rebuilt_from_model_ref_without_tool_repeat(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    put = store.put
    def fault(kind, payload, refs=None):
        ref = put(kind, payload, refs)
        if kind == 'agent_invocation' and payload['name'] == 'submit_draft' and payload['phase'] == 'returned':
            raise SystemExit('after draft return recorded')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fault)
        with pytest.raises(SystemExit):
            run(store, source, Scripted([{'action': 'submit_draft', 'args': proposal}]))
    monkeypatch.setattr(agent.agent_tools, 'submit_draft', lambda *a, **k: pytest.fail('draft repeated'))
    result = store.get(agent.recover_agent(store, Scripted([act('run'), act('finish')]), execution(store)))['payload']
    assert result['state'] == 'completed' and result['call_count'] == 3
    assert len(store.list('run')) == len(store.list('model')) == 1


@pytest.mark.parametrize('action,args', [
    ('search_sources', {'query': 'parameter k'}),
    ('inspect_source', {'page': 1, 'start': 0, 'end': 10}),
    ('load_skill', {'name': 'ode_reproduction'}),
    ('recall_memory', {'query': 'initial x'}),
])
def test_returned_read_feedback_can_be_reconstructed(tmp_path, monkeypatch, action, args):
    store, source, _ = fixture(tmp_path)
    args = dict(args)
    if action == 'inspect_source':
        args['source_ref'] = source
    original = store.put
    def fault(kind, payload, refs=None):
        ref = original(kind, payload, refs)
        if kind == 'agent_invocation' and payload['name'] == action and payload['phase'] == 'returned':
            raise SystemExit('after read record')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fault)
        with pytest.raises(SystemExit):
            run(store, source, Scripted([{'action': action, 'args': args}]), memory_project='recover-read')
    final = store.get(agent.recover_agent(store, Scripted([act('ask_researcher', question='Continue?')]), execution(store)))['payload']
    assert final['state'] == 'awaiting_input' and final['call_count'] == 2
    assert len(phase(store, action, 'reserved')) == len(phase(store, action, 'returned')) == 1


def test_unknown_external_result_never_retries_or_writes_recovery_receipt(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    put = store.put
    def fault(kind, payload, refs=None):
        if kind == 'agent_invocation' and payload['name'] == 'respond' and payload['phase'] == 'returned':
            raise OSError('lost response record')
        return put(kind, payload, refs)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fault)
        with pytest.raises(OSError):
            run(store, source, Scripted([act('ask_researcher', question='x?')]))
    provider = Scripted([act('finish')])
    count = len(store.list())
    with pytest.raises(recovery.RecoveryBlocked, match='external_outcome_unknown'):
        agent.recover_agent(store, provider, execution(store))
    assert not provider.calls and len(store.list()) == count


def test_partial_scientific_result_without_return_is_not_guessed_or_recomputed(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    original = application.run_reproduction
    def partial(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('partial result exists')
    with monkeypatch.context() as patch:
        patch.setattr(application, 'run_reproduction', partial)
        with pytest.raises(SystemExit):
            run(store, source, Scripted([{'action': 'submit_draft', 'args': proposal}, act('run')]))
    assert len(store.list('run')) == 1
    report = recovery.diagnose_execution(store, execution(store))
    assert report['status'] == 'blocked' and not report['adoptable_result_refs']
    with pytest.raises(recovery.RecoveryBlocked):
        agent.recover_agent(store, Scripted([]), execution(store))
    assert len(store.list('run')) == 1 and not store.list('agent_recovery')


def test_repeat_recovery_of_finalized_execution_is_refused_without_writes(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', lambda *a: (_ for _ in ()).throw(SystemExit()))
        with pytest.raises(SystemExit):
            run(store, source, Scripted([act('ask_researcher', question='x?')]))
    ref = execution(store)
    agent.recover_agent(store, Scripted([]), ref)
    count = len(store.list())
    assert recovery.diagnose_execution(store, ref)['status'] == 'finished'
    with pytest.raises(recovery.RecoveryBlocked):
        agent.recover_agent(store, Scripted([]), ref)
    assert len(store.list()) == count


def test_recovery_can_crash_again_after_a_new_response_and_resume_without_resending(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    _, ref = post_result_crash(store, source, proposal, monkeypatch)
    assistant = agent._assistant
    def fault(text):
        if json.loads(text)['action'] == 'finish':
            raise SystemExit('new paid response was recorded before this crash')
        return assistant(text)
    provider = Scripted([act('finish')])
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', fault)
        with pytest.raises(SystemExit):
            agent.recover_agent(store, provider, ref)
    assert len(provider.calls) == 1
    last = Scripted([])
    final = store.get(agent.recover_agent(store, last, ref))['payload']
    assert final['state'] == 'completed' and final['call_count'] == 3 and not last.calls
    assert len(final['recovery_refs']) == 2 and len(store.list('run')) == 1
    assert [o['reserved']['sequence'] for o in recovery.diagnose_execution(store, ref)['operations']] == list(range(1, 6))


def test_provider_identity_mismatch_refuses_before_receipt(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    _, ref = post_result_crash(store, source, proposal, monkeypatch)
    provider = Scripted([])
    provider.model = 'different'
    before = len(store.list())
    with pytest.raises(recovery.RecoveryBlocked, match='provider'):
        agent.recover_agent(store, provider, ref)
    assert len(store.list()) == before and not provider.calls


def test_owner_applies_to_normal_run_resume_and_recovery_before_side_effects(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    _, ref = post_result_crash(store, source, proposal, monkeypatch)
    task_ref = store.get(ref)['payload']['task_ref']
    before = len(store.list())
    with recovery.execution_owner(store, task_ref):
        with pytest.raises(recovery.RecoveryBlocked, match='owner'):
            agent.recover_agent(store, Scripted([]), ref)
        with pytest.raises(recovery.RecoveryBlocked, match='owner'):
            run(store, source, Scripted([]))
    assert len(store.list()) == before
    parent = run(store, source, Scripted([act('ask_researcher', question='x?')]))
    task_ref = store.get(parent)['payload']['task_ref']
    before = len(store.list())
    with recovery.execution_owner(store, task_ref):
        with pytest.raises(recovery.RecoveryBlocked, match='owner'):
            agent.resume_agent(store, Scripted([]), parent, 'x = 1.', respondent='A')
    assert len(store.list()) == before and not store.list('researcher_clarification')


def test_interrupted_researcher_branch_preserves_attribution_and_inherited_counts(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    parent = run(store, source, Scripted([act('ask_researcher', question='x?')]), max_calls=3)
    original = deepcopy(store.get(parent))
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', lambda *a: (_ for _ in ()).throw(SystemExit()))
        with pytest.raises(SystemExit):
            agent.resume_agent(store, Scripted([act('ask_researcher', question='k?')]),
                               parent, 'Initial x = 1.', respondent='Researcher A')
    ref = execution(store)
    report = recovery.diagnose_execution(store, ref)
    assert report['status'] == 'ready', report
    final = store.get(agent.recover_agent(store, Scripted([]), ref))['payload']
    assert final['state'] == 'awaiting_input' and final['call_count'] == 2
    assert final['parent_run_ref'] == parent and len(final['clarification_refs']) == 1
    assert store.get(parent) == original and len(store.list('researcher_clarification')) == 1


def abrupt(root, source, proposal, point):
    store = ArtifactStore(root)
    if point == 'provider':
        def die(messages):
            os._exit(41)
        provider = Scripted([])
        provider.respond = die
    elif point == 'read':
        from biomodel_agent import retrieval
        def die(*args, **kwargs):
            os._exit(44)
        retrieval.search_sources = die
        provider = Scripted([act('search_sources', query='parameter k')])
    else:
        provider = Scripted([{'action': 'submit_draft', 'args': proposal}, act('run')])
        def die(*args):
            os._exit(42)
        agent._run_facts = die
    run(store, source, provider)


@pytest.mark.parametrize('point,code', [('provider', 41), ('result', 42), ('read', 44)])
def test_process_exit_leaves_durable_state_and_safe_recovery_decision(tmp_path, point, code):
    store, source, proposal = fixture(tmp_path)
    child = mp.get_context('spawn').Process(target=abrupt, args=(str(store.root), source, proposal, point))
    child.start()
    child.join(30)
    if child.is_alive():
        child.terminate(); child.join(5)
        pytest.fail('child exceeded process-exit budget')
    assert child.exitcode == code
    reopened = ArtifactStore(store.root)
    ref = execution(reopened)
    report = recovery.diagnose_execution(reopened, ref)
    if point == 'provider':
        assert report['status'] == 'blocked' and report['call_count'] == 1
        with pytest.raises(recovery.RecoveryBlocked):
            agent.recover_agent(reopened, Scripted([]), ref)
        assert not reopened.list('agent_recovery')
    elif point == 'result':
        assert report['status'] == 'ready' and report['run_count'] == 1
        final = reopened.get(agent.recover_agent(reopened, Scripted([act('finish')]), ref))['payload']
        assert final['state'] == 'completed' and len(reopened.list('run')) == 1
    else:
        assert report['status'] == 'ready' and report['run_count'] == 0
        assert report['operations'][-1]['retry_read_only'] is True
        final = reopened.get(agent.recover_agent(reopened,
            Scripted([act('ask_researcher', question='Continue?')]), ref))['payload']
        assert final['state'] == 'awaiting_input' and final['call_count'] == 2
        assert len(phase(reopened, 'search_sources', 'reserved')) == 1
        assert len(phase(reopened, 'search_sources', 'returned')) == 1


def test_result_for_another_experiment_is_not_adopted(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    _, ref = post_result_crash(store, source, proposal, monkeypatch)
    term_ref, term = phase(store, 'run', 'returned')[0]
    old_run = store.get(term['payload']['details']['result_ref'])
    inputs = store.get(old_run['payload']['input_ref'])
    inputs['payload']['experiment']['times'] = [0., 2.]
    wrong_input = store.put(inputs['kind'], inputs['payload'], refs=inputs['refs'])
    old_run['payload']['input_ref'] = wrong_input
    wrong_run = store.put('run', old_run['payload'], refs=[wrong_input])
    term['payload']['details']['result_ref'] = wrong_run
    refs = [r for r in term['refs'] if r != phase(store, 'run', 'returned')[0][1]['payload']['details']['result_ref']]
    (store.objects_dir / (term_ref + '.json')).unlink()
    with store._db() as conn:
        conn.execute('DELETE FROM artifact_index WHERE ref=?', (term_ref,))
    store.put('agent_invocation', term['payload'], refs=[*refs, wrong_run])
    report = recovery.diagnose_execution(store, ref)
    assert report['status'] == 'blocked' and 'scientific_result_frozen_inputs_mismatch' in report['reasons']


def test_real_study_result_is_adopted_without_fitting_again(tmp_path, monkeypatch):
    from test_agent_studies import config
    from biomodel_agent.domain.serialization import from_dict
    store, source, _ = fixture(tmp_path)
    plan = studies.prepare_study(store, config())
    exp = from_dict(ExperimentSpec, studies.load_plan(store, plan)['config']['training'][0]['experiment'])
    with monkeypatch.context() as patch:
        patch.setattr(studies, 'result_summary', lambda *a: (_ for _ in ()).throw(SystemExit('fit saved')))
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([act('run_study')]), [source], exp,
                            mode='hypothesis', study_plan_ref=plan)
    ref = execution(store)
    report = recovery.diagnose_execution(store, ref)
    assert report['status'] == 'ready', report
    result_ref = report['adoptable_result_refs'][0]
    monkeypatch.setattr(studies, 'execute_study', lambda *a: pytest.fail('study fitted twice'))
    result = store.get(agent.recover_agent(store, Scripted([act('finish')]), ref))['payload']
    assert result['state'] == 'completed' and result['run_ref'] == result_ref
    assert result['call_count'] == 2
    assert len(phase(store, 'run_study', 'reserved')) == 1


def test_negative_scientific_result_does_not_become_completed_after_recovery(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    # Real ODE solve versus a disagreeing reference: failure remains failure.
    _, ref = post_result_crash(store, source, proposal, monkeypatch,
        reference=SimulationResult('succeeded', (0., 1.), {'x': (10., 10.)}), scales={'x': 1.})
    result = store.get(agent.recover_agent(store, Scripted([act('finish')]), ref))['payload']
    assert result['state'] == 'incomplete'
    assert application.derive_scientific_status(store.get(result['run_ref'])['payload']) == 'failed'
    assert len(store.list('run')) == 1


def test_stale_memory_blocks_before_recovery_receipt_or_provider(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    ref = memory.remember(store, project_id='p', content='Check tolerances.',
                          category='experience', author='A', source_refs=[source])
    _, execution_ref = post_result_crash(store, source, proposal, monkeypatch, memory_project='p')
    memory.retract(store, ref, author='A', reason='No longer applicable')
    provider = Scripted([])
    before = store.list()
    with pytest.raises(ValueError, match='stale'):
        agent.recover_agent(store, provider, execution_ref)
    assert store.list() == before and not provider.calls


def test_cli_diagnosis_is_read_only_and_works_without_index(tmp_path, monkeypatch, capsys):
    store, source, _ = fixture(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', lambda *a: (_ for _ in ()).throw(SystemExit()))
        with pytest.raises(SystemExit):
            run(store, source, Scripted([act('ask_researcher', question='x?')]))
    ref = execution(store)
    # A diagnosis must not repair a missing index or initialize a new one.
    store.index_path.unlink()
    before = {str(p): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    assert cli.main(['agent-recovery-status', ref, '--store', str(store.root)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['status'] == 'ready' and summary['call_count'] == 1
    assert 'request_messages' not in json.dumps(summary)
    assert {str(p): p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before


def test_cli_unknown_provider_outcome_needs_no_credentials_and_is_not_retried(tmp_path, monkeypatch, capsys):
    store, source, _ = fixture(tmp_path)
    with pytest.raises(SystemExit):
        run(store, source, Scripted([SystemExit('provider interrupted')]))
    ref = execution(store)
    monkeypatch.delenv('BIOMODEL_API_KEY', raising=False)
    monkeypatch.setattr('biomodel_agent.llm.ChatProvider', lambda *a, **k: pytest.fail('provider constructed'))
    before = store.list()
    assert cli.main(['agent-recover', ref, '--store', str(store.root)]) == 1
    output = capsys.readouterr()
    assert 'requires_review' in output.err or 'requires_review' in output.out
    assert store.list() == before


def active_agent(root, source, pipe):
    store = ArtifactStore(root)
    provider = Scripted([])
    def wait(messages):
        pipe.send('provider_entered')
        pipe.recv()
    provider.respond = wait
    run(store, source, provider)


def test_live_agent_owner_refuses_recovery_in_another_process(tmp_path):
    store, source, _ = fixture(tmp_path)
    ctx = mp.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=active_agent, args=(str(store.root), source, child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv() == 'provider_entered'
        ref = execution(store)
        before = store.list()
        with pytest.raises(recovery.RecoveryBlocked, match='owner'):
            agent.recover_agent(store, Scripted([]), ref)
        assert store.list() == before
    finally:
        process.terminate(); process.join(10)
    assert not process.is_alive()
    # This is now unknown external work, not an expired lease to be retried.
    with pytest.raises(recovery.RecoveryBlocked, match='external_outcome_unknown'):
        agent.recover_agent(store, Scripted([]), ref)
