from copy import deepcopy

import pytest

from biomodel_agent import failure_handling, research_tasks as tasks
from test_fba import Scripted, act
from test_research_tasks import setup


def test_public_ode_dispatch_stops_two_read_failures_without_new_opt_in_or_refund(tmp_path):
    store,config,normal=setup(tmp_path,'ode')
    request=tasks.prepare(store,config);source=config['source_refs'][0]
    provider=Scripted([act('inspect_source',source_ref=source,page=1,start=0,end=8000),
                       act('inspect_source',source_ref=source,page=1,start=0,end=7999),*normal])
    result=tasks.execute(store,provider,request)
    view=tasks.result(store,result)
    assert view['state']=='incomplete' and view['call_count']==2 and view['run_count']==0
    assert len(provider.requests)==2 and not store.list('run')
    wrapper=store.get(result)['payload']
    child=store.get(wrapper['native_task_ref'])['payload']
    assert child['failure_policy']==failure_handling.POLICY
    assert child['failure_policy']['version']==2
    assert child['max_calls']==config.get('max_calls',6)
    assert tasks.execute(store,None,request)==result and len(provider.requests)==2
    routes=[event for event in wrapper['events'] if event.get('tool')=='route_failure']
    assert [event['route'] for event in routes]==['repair','stop']


def test_native_wrapper_rejects_missing_or_weakened_frozen_policy(tmp_path):
    store,config,actions=setup(tmp_path,'ode');request=tasks.prepare(store,config)
    result=tasks.execute(store,Scripted(actions),request)
    wrapper=store.get(result)['payload'];native=store.get(wrapper['native_run_ref'])
    task=store.get(wrapper['native_task_ref'])
    for policy in (None,dict(failure_handling.POLICY,max_stagnant_failures=99)):
        payload=deepcopy(task['payload']);payload['failure_policy']=policy
        forged_task=store.put(task['kind'],payload,refs=task['refs'])
        payload=deepcopy(native['payload']);payload['task_ref']=forged_task
        forged=store.put(native['kind'],payload,refs=[*native['refs'],forged_task])
        with pytest.raises(ValueError,match='ODE native task scope changed'):
            tasks._validate_native(store,request,forged)
