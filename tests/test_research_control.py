"""Cancellation cannot erase paid responses or replay partially completed work."""
import threading

import pytest

from biomodel_agent import research_control as control,research_projects as p,research_supervisor as supervisor,research_usage
from test_fba import Scripted,act
from test_research_projects import setup,opened,step


def test_stop_before_run_is_durable_idempotent_and_exported(tmp_path):
    store,root=setup(tmp_path)
    stop=control.request_stop(store,root,actor='tester',reason='Stop this experiment.')
    assert control.request_stop(store,root,actor='tester',reason='Stop this experiment.')==stop
    provider=Scripted([]);snapshot=supervisor.run(store,provider,root)
    assert not provider.requests and p.inspect(store,root)['state']=='stopped'
    assert stop in store.get(snapshot)['refs']
    with pytest.raises(ValueError,match='concluded'):
        p.operate(store,root,'scenario_source',{})


def test_stop_while_planner_is_in_flight_preserves_response_without_dispatch(tmp_path):
    store,root=setup(tmp_path)
    entered=threading.Event();release=threading.Event();result=[]
    class Blocking:
        requests=0
        def respond(self,messages):
            self.requests+=1;entered.set()
            assert release.wait(10)
            return act('scenario_source')
    provider=Blocking()
    thread=threading.Thread(target=lambda:result.append(supervisor.run(store,provider,root)))
    thread.start()
    try:
        assert entered.wait(10)
        stop=control.request_stop(store,root,actor='tester',reason='Stop during a request.')
    finally:release.set();thread.join(15)
    assert not thread.is_alive() and result and provider.requests==1
    view=p.inspect(store,root)
    assert view['state']=='stopped' and view['continuation']['has_saved_response']
    assert not view['continuation']['can_advance'] and not store.list('source')
    assert len(store.list(p.RESPONSE))==1 and stop in store.get(result[0])['refs']
    supervisor.run(store,provider,root)
    assert provider.requests==1


def test_wall_clock_deadline_includes_pause_and_survives_reopening(tmp_path,monkeypatch):
    now=[100.]
    monkeypatch.setattr(control.time,'time',lambda:now[0])
    store,root=setup(tmp_path,total_timeout_s=10)
    assert p.inspect(store,root)['control']['remaining_s']==10
    now[0]=111.
    from biomodel_agent.storage import ArtifactStore
    reopened=ArtifactStore(store.root)
    assert p.inspect(reopened,root)['state']=='deadline_exhausted'
    provider=Scripted([]);supervisor.run(reopened,provider,root)
    assert not provider.requests


@pytest.mark.parametrize('timeout',[0,-1,True,'10',float('inf'),604801])
def test_deadline_rejects_invalid_values_before_project_write(tmp_path,timeout):
    from biomodel_agent.storage import ArtifactStore
    store=ArtifactStore(tmp_path/'s')
    with pytest.raises(ValueError):p.create(store,dict(scenario='Control',policy=dict(total_timeout_s=timeout)))
    assert not store.list(p.PROJECT)


def test_deadline_after_response_preserves_it_and_does_not_start_next_action(tmp_path,monkeypatch):
    now=[100.];monkeypatch.setattr(control.time,'time',lambda:now[0])
    store,root=setup(tmp_path,total_timeout_s=10)
    class Provider:
        def respond(self,messages):now[0]=111.;return act('scenario_source')
    supervisor.run(store,Provider(),root)
    assert p.inspect(store,root)['state']=='deadline_exhausted'
    assert len(store.list(p.RESPONSE))==1 and not store.list('source')


def test_inner_calls_are_metered_and_complete_tokens_are_reconciled(tmp_path,monkeypatch):
    clock=iter([0.,0.5,1.,1.25,2.,4.,10.,13.])
    monkeypatch.setattr(research_usage,'monotonic',lambda:next(clock))
    store,root,it=opened(tmp_path,native_agent=True,planner_max_calls=1)
    class UsageScripted(Scripted):
        def respond(self,messages):
            response=super().respond(messages)
            self.last_usage=dict(total_tokens=7 if self is planner else 100)
            return response
    planner=UsageScripted([act('run_iteration',iteration_ref=it)])
    native=UsageScripted([act('run_typed'),act('finish')])
    snapshot=supervisor.run(store,planner,root,native_provider_factory=lambda _:native)
    usage=supervisor.usage(store,root)
    assert usage['native_provider_calls']==2
    assert usage['native_usage']['complete_total_tokens']==200
    assert usage['complete_total_tokens']==207
    timing=usage['operation_elapsed']
    assert timing['by_operation']['revise_frame']['complete_elapsed_s']==0.5
    assert timing['by_operation']['open_iteration']['complete_elapsed_s']==0.25
    assert timing['by_operation']['respond']['complete_elapsed_s']==2.
    assert timing['by_operation']['run_iteration']['complete_elapsed_s']==3.
    assert timing['complete_elapsed_s']==5.75
    assert set(usage['native_usage']['call_refs']+usage['native_usage']['return_refs'])<=set(store.get(snapshot)['refs'])
    assert p.facts(store,it)['numeric_status']=='pass'


def test_missing_inner_usage_is_unknown_not_zero(tmp_path):
    store,root,it=opened(tmp_path,native_agent=True,planner_max_calls=1)
    planner=Scripted([act('run_iteration',iteration_ref=it)])
    native=Scripted([act('run_typed'),act('finish')])
    supervisor.run(store,planner,root,native_provider_factory=lambda _:native)
    usage=supervisor.usage(store,root)
    assert usage['native_usage']['missing_usage_calls']==2
    assert usage['native_usage']['complete_total_tokens'] is None and usage['complete_total_tokens'] is None


def test_successful_operation_elapsed_is_saved_and_grouped_in_public_usage(tmp_path,monkeypatch):
    store,root=setup(tmp_path)
    clock=iter([10.,10.5]);monkeypatch.setattr(research_usage,'monotonic',lambda:next(clock))
    result=step(store,root,'scenario_source')
    assert result['source_ref'] in p.inspect(store,root)['source_refs']
    returned=p.rows(store,p.RETURN,root)
    assert len(returned)==1 and returned[0][1]['state']=='returned' and returned[0][1]['elapsed_s']==0.5
    usage=supervisor.usage(store,root);timing=usage['operation_elapsed']
    assert timing['by_operation']['scenario_source']==dict(attempts=1,recorded_elapsed_s=0.5,
        missing_elapsed_attempts=0,complete_elapsed_s=0.5)
    assert timing['by_operation']['run_iteration']==dict(attempts=0,recorded_elapsed_s=0,
        missing_elapsed_attempts=0,complete_elapsed_s=0)
    assert timing['attempts']==1 and timing['recorded_elapsed_s']==0.5
    assert timing['missing_elapsed_attempts']==0 and timing['complete_elapsed_s']==0.5
    assert 'do not add' in timing['semantics'] and 'human alignment' in timing['semantics']
    assert usage['planner_complete_elapsed_s']==0 and usage['native_usage']['complete_elapsed_s']==0


def test_exception_terminal_return_saves_operation_elapsed(tmp_path,monkeypatch):
    store,root=setup(tmp_path)
    clock=iter([20.,20.25]);monkeypatch.setattr(research_usage,'monotonic',lambda:next(clock))
    with pytest.raises(ValueError):step(store,root,'revise_frame',frame={},reason='Invalid frame.')
    returned=p.rows(store,p.RETURN,root)
    assert len(returned)==1 and returned[0][1]['state']=='rejected'
    assert returned[0][1]['elapsed_s']==0.25 and returned[0][1]['diagnostic']['stage']=='revise_frame'
    usage=supervisor.usage(store,root);group=usage['operation_elapsed']['by_operation']['revise_frame']
    assert group==dict(attempts=1,recorded_elapsed_s=0.25,missing_elapsed_attempts=0,complete_elapsed_s=0.25)
    assert usage['operation_elapsed']['complete_elapsed_s']==0.25


def test_base_exception_leaves_operation_elapsed_unknown_without_fake_return(tmp_path,monkeypatch):
    store,root=setup(tmp_path)
    def interrupt(*args,**kwargs):raise KeyboardInterrupt('unconfirmed')
    monkeypatch.setattr(p,'perform',interrupt)
    with pytest.raises(KeyboardInterrupt):step(store,root,'scenario_source')
    assert not p.rows(store,p.RETURN,root)
    view=p.inspect(store,root)
    assert view['state']=='needs_recovery' and len(view['unresolved_operation_refs'])==1
    usage=supervisor.usage(store,root);group=usage['operation_elapsed']['by_operation']['scenario_source']
    assert group==dict(attempts=1,recorded_elapsed_s=0,missing_elapsed_attempts=1,complete_elapsed_s=None)
    assert usage['operation_elapsed']['complete_elapsed_s'] is None
    assert supervisor.usage(store,root)==usage and len(p.rows(store,p.STEP,root))==1


def test_legacy_and_invalid_operation_elapsed_are_unknown_not_zero(tmp_path):
    store,root=setup(tmp_path);missing=object()
    def legacy(sequence,elapsed=missing):
        key='legacy-'+str(sequence)
        attempt=store.put(p.STEP,dict(version=1,project_ref=root,sequence=sequence,key=key,
            operation='scenario_source',signature=key,args={},actor='agent',native_allocation=0,response_ref=None),
            refs=(root,))
        payload=dict(version=1,project_ref=root,attempt_ref=attempt,state='rejected',result=None,error_type='ValueError')
        if elapsed is not missing:payload['elapsed_s']=elapsed
        store.put(p.RETURN,payload,refs=(root,attempt))
    legacy(0,0.25);legacy(1);legacy(2,-1.);legacy(3,True)
    usage=supervisor.usage(store,root);timing=usage['operation_elapsed']
    assert timing['by_operation']['scenario_source']==dict(attempts=4,recorded_elapsed_s=0.25,
        missing_elapsed_attempts=3,complete_elapsed_s=None)
    assert timing['missing_elapsed_attempts']==3 and timing['complete_elapsed_s'] is None
    assert timing['by_operation']['respond']==dict(attempts=0,recorded_elapsed_s=0,
        missing_elapsed_attempts=0,complete_elapsed_s=0)
    invalid=[-1.,True,float('nan'),float('inf'),float('-inf')]
    attempts=[(str(i),{'operation':'scenario_source'}) for i in range(len(invalid)+1)]
    returns={str(i):('return-'+str(i),{'elapsed_s':value}) for i,value in enumerate(invalid)}
    checked=research_usage.operation_elapsed(attempts,returns,p.OPERATIONS)
    group=checked['by_operation']['scenario_source']
    assert group['attempts']==len(invalid)+1 and group['recorded_elapsed_s']==0
    assert group['missing_elapsed_attempts']==len(invalid)+1 and group['complete_elapsed_s'] is None
    assert checked['complete_elapsed_s'] is None


def test_recovered_confirmed_response_keeps_elapsed_unknown_without_new_attempt(tmp_path,monkeypatch):
    store,root=setup(tmp_path,planner_max_calls=1);provider=Scripted([act('scenario_source')]);original=store.put
    def interrupted(kind,payload,refs=None):
        if kind==p.RETURN and payload.get('result',{}).get('response_ref'):
            raise KeyboardInterrupt('after confirmed response')
        return original(kind,payload,refs)
    with monkeypatch.context() as patch:
        patch.setattr(store,'put',interrupted)
        with pytest.raises(KeyboardInterrupt):supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert view['state']=='needs_recovery' and len(view['unresolved_operation_refs'])==1
    p.recover(store,root,view['unresolved_operation_refs'][0])
    attempts,_,pending=p.history(store,root)
    assert len(attempts)==1 and not pending
    first=supervisor.usage(store,root);second=supervisor.usage(store,root)
    assert first==second
    group=first['operation_elapsed']['by_operation']['respond']
    assert group['attempts']==1 and group['recorded_elapsed_s']==0
    assert group['missing_elapsed_attempts']==1 and group['complete_elapsed_s'] is None
    assert first['operation_elapsed']['complete_elapsed_s'] is None
    assert first['planner_complete_elapsed_s'] is not None
    supervisor.run(store,None,root)
    assert len(provider.requests)==1
    usage=supervisor.usage(store,root)
    assert usage['operation_elapsed']['by_operation']['respond']['attempts']==1
    assert len(p.rows(store,p.STEP,root))==2 and len(p.rows(store,p.RECOVERY,root))==1
    assert supervisor.usage(store,root)==usage
