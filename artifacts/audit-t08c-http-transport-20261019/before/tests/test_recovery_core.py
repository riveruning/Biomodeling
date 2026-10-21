"""Codex-owned recovery integrity and ownership tests; no service calls."""
from copy import deepcopy
import json
import multiprocessing as mp
import gc

import pytest

from biomodel_agent import recovery
from biomodel_agent.invocations import InvocationJournal
from biomodel_agent.storage import ArtifactStore


def make(tmp_path, **overrides):
    store = ArtifactStore(tmp_path / 'store')
    source = store.put('source', {'pages': [{'number': 1, 'text': 'Initial x = 1.'}]})
    task = store.put('agent_task', {
        'resume_version': 1, 'max_calls': 4, 'max_repairs': 2, 'max_retries': 2,
        'source_refs': [source], 'mode': 'reproduction', 'provider': {},
        'study_plan_ref': None, 'memory_scope': None,
    }, refs=(source,))
    meta = dict(version=1, implementation_sha256=recovery.implementation_fingerprint(),
                source_refs=[source], restored=None)
    meta.update(overrides)
    journal = InvocationJournal(store, task, recovery=meta, initial_refs=(source,))
    return store, source, task, journal


def response(journal, action='ask_researcher'):
    value = {'action': action, 'args': {'question': 'Initial x?'} if action == 'ask_researcher' else {}}
    journal.invoke('respond', lambda: value, details={'request_messages': [], 'context': {}},
                   summarize=lambda r: {'response_json': json.dumps(r), 'usage': {'total_tokens': 7}})
    return value


def snapshot(root):
    # Close unreachable fixture SQLite connections before comparing on-disk
    # bytes: connection finalizers can checkpoint WAL during unrelated GC.
    gc.collect()
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and not p.name.endswith(('-wal', '-shm'))}


def test_diagnose_is_read_only_and_sequence_is_explicit(tmp_path, monkeypatch):
    store, source, task, j = make(tmp_path)
    response(j)
    before = snapshot(store.root)
    def forbidden(*a, **k):
        pytest.fail('diagnosis attempted a storage mutation or opened SQLite')
    monkeypatch.setattr(store, 'put', forbidden)
    monkeypatch.setattr(store, '_db', forbidden)
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'ready', report
    assert report['call_count'] == 1 and report['run_count'] == 0
    assert report['task_ref'] == task and len(report['operations']) == 1
    pair = report['operations'][0]
    assert pair['reserved']['sequence'] == pair['terminal']['sequence'] == 1
    assert snapshot(store.root) == before
    assert not (store.root / '.agent-owners').exists()


def test_cached_response_and_reads_do_not_call_callbacks_or_add_records(tmp_path):
    store, _, _, j = make(tmp_path)
    value = response(j)
    j.invoke('search_sources', lambda: {'results': [{'text': 'x = 1'}]}, details={'args': {'query': 'x'}})
    report = recovery.diagnose_execution(store, j.execution_ref)
    replay = recovery.ReplayJournal(store, report)
    def forbidden():
        pytest.fail('cached operation callback repeated')
    before = snapshot(store.root)
    assert replay.invoke('respond', forbidden, details={'request_messages': [], 'context': {}}) == value
    assert replay.last_usage == {'total_tokens': 7}
    out = replay.invoke('search_sources', forbidden, details={'args': {'query': 'x'}})
    out['results'][0]['text'] = 'local mutation'
    replay.assert_consumed()
    assert replay.calls == 1 and replay.runs == 0
    assert replay.refs == j.refs and snapshot(store.root) == before


@pytest.mark.parametrize('name,details', [('respond', {'changed': True}),
                                          ('search_sources', {'request_messages': [], 'context': {}})])
def test_replay_mismatch_blocks_before_callback(tmp_path, name, details):
    store, _, _, j = make(tmp_path)
    response(j)
    replay = recovery.ReplayJournal(store, recovery.diagnose_execution(store, j.execution_ref))
    with pytest.raises(recovery.RecoveryBlocked):
        replay.invoke(name, lambda: pytest.fail('mismatch executed'), details=details)
    with pytest.raises(recovery.RecoveryBlocked):
        replay.assert_consumed()


def test_new_calls_after_replay_keep_spent_budget_and_sequence(tmp_path):
    store, _, _, j = make(tmp_path)
    response(j)
    replay = recovery.ReplayJournal(store, recovery.diagnose_execution(store, j.execution_ref))
    response(replay)
    response(replay)
    assert replay.calls == 2
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'ready', report
    assert [o['reserved']['sequence'] for o in report['operations']] == [1, 2]
    assert len(store.list('agent_execution')) == 1


@pytest.mark.parametrize('name,reason', [('respond', 'external_outcome_unknown'),
    ('run', 'scientific_outcome_unknown'), ('run_study', 'scientific_outcome_unknown'),
    ('submit_draft', 'partial_artifacts_unknown')])
def test_unknown_effects_are_not_safe_retries(tmp_path, name, reason):
    store, _, _, j = make(tmp_path)
    j._reserve(name, {})
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'blocked'
    assert any(reason in r for r in report['reasons']), report


def test_reserved_read_only_reuses_same_reservation(tmp_path):
    store, _, _, j = make(tmp_path)
    response(j)
    j._reserve('search_sources', {'args': {'query': 'x'}})
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'ready', report
    replay = recovery.ReplayJournal(store, report)
    response(replay)
    observed = []
    out = replay.invoke('search_sources', lambda: observed.append('read') or {'results': []},
                        details={'args': {'query': 'x'}})
    replay.assert_consumed()
    assert out == {'results': []} and observed == ['read']
    rows = [store.get(ref)['payload'] for ref in store.list('agent_invocation')]
    assert sum(r['name'] == 'search_sources' and r['phase'] == 'reserved' for r in rows) == 1
    assert sum(r['name'] == 'search_sources' and r['phase'] == 'returned' for r in rows) == 1
    assert replay.calls == 1 and replay.runs == 0


@pytest.mark.parametrize('error', [ValueError('secret diagnostic'), SystemExit('secret interrupt')])
def test_logged_error_and_interrupt_do_not_imply_no_effects(tmp_path, error):
    store, _, _, j = make(tmp_path)
    def fail():
        raise error
    with pytest.raises(type(error)):
        j.invoke('search_sources', fail)
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'blocked'
    assert 'secret' not in json.dumps(report)


def test_missing_index_entry_does_not_hide_reserved_request(tmp_path):
    store, _, _, j = make(tmp_path)
    ref, *_ = j._reserve('respond', {})
    with store._db() as conn:
        conn.execute('DELETE FROM artifact_index WHERE ref=?', (ref,))
    before = snapshot(store.root)
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'blocked' and report['call_count'] == 1
    assert len(report['operations']) == 1
    assert snapshot(store.root) == before
    assert ref not in store.list('agent_invocation')


@pytest.mark.parametrize('change', ['sequence_gap', 'bool_count', 'counter_jump', 'wrong_class',
                                    'duplicate_terminal', 'wrong_reservation_ref'])
def test_inconsistent_or_ambiguous_trace_is_refused(tmp_path, change):
    store, _, _, j = make(tmp_path)
    reserved_ref, op_id, calls, runs = j._reserve('respond', {})
    reserved = store.get(reserved_ref)
    if change == 'sequence_gap':
        reserved['payload']['sequence'] = 2
    elif change == 'bool_count':
        reserved['payload']['call_count'] = True
    elif change == 'counter_jump':
        reserved['payload']['call_count'] = 3
    elif change == 'wrong_class':
        reserved['payload']['replay_class'] = 'read_only'
    if change in ('sequence_gap', 'bool_count', 'counter_jump', 'wrong_class'):
        # Replace fixture inventory, not an actual immutable production record.
        (store.objects_dir / (reserved_ref + '.json')).unlink()
        with store._db() as conn:
            conn.execute('DELETE FROM artifact_index WHERE ref=?', (reserved_ref,))
        reserved_ref = store.put('agent_invocation', reserved['payload'], refs=reserved['refs'])
    payload = deepcopy(reserved['payload'])
    payload.update(phase='returned', details={'response_json': '{"action":"finish","args":{}}', 'usage': {}})
    refs = (j.execution_ref,) if change == 'wrong_reservation_ref' else (j.execution_ref, reserved_ref)
    store.put('agent_invocation', payload, refs=refs)
    if change == 'duplicate_terminal':
        payload['details']['usage'] = {'total_tokens': 1}
        store.put('agent_invocation', payload, refs=refs)
    report = recovery.diagnose_execution(store, j.execution_ref)
    assert report['status'] == 'blocked', report


def test_fingerprint_mismatch_and_legacy_snapshot_are_blocked(tmp_path):
    store, _, task, j = make(tmp_path, implementation_sha256='0' * 64)
    assert recovery.diagnose_execution(store, j.execution_ref)['status'] == 'blocked'
    old = InvocationJournal(store, task)
    assert recovery.diagnose_execution(store, old.execution_ref)['status'] == 'blocked'


def hold_owner(root, task, pipe):
    store = ArtifactStore(root)
    with recovery.execution_owner(store, task):
        pipe.send('held')
        pipe.recv()


def test_process_owner_contention_and_abrupt_exit_release(tmp_path):
    store, _, task, _ = make(tmp_path)
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=hold_owner, args=(str(store.root), task, child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() == 'held'
        with pytest.raises(recovery.RecoveryBlocked):
            with recovery.execution_owner(store, task):
                pytest.fail('concurrent owner admitted')
    finally:
        process.terminate()
        process.join(10)
    assert not process.is_alive()
    with recovery.execution_owner(store, task):
        pass
    assert list((store.root / '.agent-owners').iterdir())


def test_owner_validates_ref_and_task_kind_before_lock_path(tmp_path):
    store, source, _, _ = make(tmp_path)
    for ref in ('../escape', source):
        with pytest.raises(ValueError):
            with recovery.execution_owner(store, ref):
                pytest.fail('invalid lock admitted')
    assert not (store.root / '.agent-owners').exists()
