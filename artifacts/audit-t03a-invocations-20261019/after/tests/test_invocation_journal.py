"""Pure journal boundary tests; no Agent, external model or numerical work."""
import importlib
import json

import pytest

from biomodel_agent.storage import ArtifactStore


def journal(store, **kwargs):
    cls = importlib.import_module('biomodel_agent.invocations').InvocationJournal
    task = store.put('agent_task', {'max_calls': kwargs.pop('max_calls',2)})
    return cls(store,task,**kwargs)


def rows(store):
    return [store.get(ref) for ref in store.list('agent_invocation')]


def test_reservation_is_visible_before_callback_and_has_stable_operation_id(tmp_path):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    details={'request_messages':[{'role':'user','content':'task data'}]}
    def callback():
        record=rows(store)[0]['payload']
        assert record['phase']=='reserved' and record['call_count']==1
        assert ledger.calls==1 and ledger.runs==0
        details['request_messages'][0]['content']='changed later'
        return {'action':'finish','args':{}}
    assert ledger.invoke('respond',callback,details=details)['action']=='finish'
    reserved,returned=rows(store)
    assert reserved['payload']['operation_id']==returned['payload']['operation_id']
    assert [r['payload']['phase'] for r in (reserved,returned)]==['reserved','returned']
    assert reserved['payload']['details']['request_messages'][0]['content']=='task data'
    assert reserved['payload']['execution_ref']==ledger.execution_ref
    assert all(ref in ledger.refs for ref in store.list('agent_invocation'))


def test_errors_and_interruptions_never_release_attempt_budget_or_record_raw_exception(tmp_path):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    secret='hidden-provider-credential'
    def fail():
        raise RuntimeError(secret)
    with pytest.raises(RuntimeError,match=secret):
        ledger.invoke('respond',fail)
    assert ledger.calls==1 and rows(store)[-1]['payload']['phase']=='error'
    assert secret not in json.dumps(rows(store))
    def interrupt():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ledger.invoke('respond',interrupt)
    assert ledger.calls==2 and rows(store)[-1]['payload']['phase']=='interrupted'
    assert rows(store)[-1]['payload']['details']['error_type']=='KeyboardInterrupt'
    seen=[]
    with pytest.raises(ValueError):
        ledger.invoke('respond',lambda:seen.append(True))
    assert seen==[] and len(rows(store))==4


def test_compute_budget_is_reserved_once_before_callback_and_never_reset_after_failure(tmp_path):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    def fail():
        assert ledger.runs==1
        assert rows(store)[0]['payload']['run_count']==1
        raise ValueError('controlled failure')
    with pytest.raises(ValueError):
        ledger.invoke('run',fail)
    assert ledger.runs==1
    seen=[]
    with pytest.raises(ValueError):
        ledger.invoke('run_study',lambda:seen.append(True))
    assert seen==[] and len(rows(store))==2


@pytest.mark.parametrize('kwargs',[{'calls':True},{'calls':-1},{'calls':3},
                                  {'runs':True},{'runs':-1},{'runs':2}])
def test_inherited_budget_fields_are_strict(kwargs,tmp_path):
    store=ArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        journal(store,**kwargs)
    assert not store.list('agent_execution')


def test_unknown_operation_cannot_execute_or_create_record(tmp_path):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    seen=[]
    with pytest.raises(ValueError):
        ledger.invoke('arbitrary_python',lambda:seen.append(True))
    assert not seen and not rows(store)


def test_failed_terminal_write_keeps_only_reservation_and_no_duplicate_callback(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    original=store.put
    def fail(kind,payload,refs=None):
        if kind=='agent_invocation' and payload['phase']=='returned':
            raise OSError('terminal write failed')
        return original(kind,payload,refs)
    monkeypatch.setattr(store,'put',fail)
    entered=[]
    with pytest.raises(OSError):
        ledger.invoke('respond',lambda:entered.append(True))
    assert entered==[True] and ledger.calls==1
    assert [r['payload']['phase'] for r in rows(store)]==['reserved']


def test_tool_result_references_are_explicit_and_exportable(tmp_path):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    result_ref=store.put('run',{'status':'failed'})
    ledger.invoke('run',lambda:result_ref,summarize=lambda ref:{'result_ref':ref},result_refs=lambda ref:[ref])
    returned=rows(store)[-1]
    assert result_ref in returned['refs']
    assert returned['payload']['phase']=='returned'  # technical return, not scientific pass
    assert returned['payload']['replay_class']=='scientific_execution'


def test_failed_reservation_does_not_advance_local_counter(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path)
    ledger=journal(store)
    original=store.put
    def fail(kind,payload,refs=None):
        if kind=='agent_invocation':
            raise OSError('reservation unavailable')
        return original(kind,payload,refs)
    monkeypatch.setattr(store,'put',fail)
    seen=[]
    with pytest.raises(OSError):
        ledger.invoke('respond',lambda:seen.append(True))
    assert not seen and ledger.calls==0 and ledger.refs==[]


@pytest.mark.parametrize('limit',[True,0,51,'2'])
def test_frozen_call_limit_is_strict(tmp_path,limit):
    store=ArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        journal(store,max_calls=limit)
    assert not store.list('agent_execution')
