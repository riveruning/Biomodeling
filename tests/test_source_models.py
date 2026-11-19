"""Literal tools preserve deterministic mathematical and whole-source gates."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from biomodel_agent import evidence,source_models,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
from test_research_projects import frame,step
from test_fba import Scripted,act

ROOT=Path(__file__).parents[1]
CASES=dict(ode='benchmarks/inputs/optimization-followup-20261101/logistic-source.txt',
    boolean='examples/task-proposals/boolean-source.txt',stochastic='examples/source-stochastic/birth-death.txt')


def prepared(tmp_path,kind,text=None):
    store=ArtifactStore(tmp_path/'s');path=ROOT/CASES[kind]
    if text is not None:path=tmp_path/'source.txt';path.write_text(text,encoding='utf-8',newline='\n')
    source=evidence.ingest_source(store,path)
    root=p.create(store,dict(scenario='Use complete literal mathematical declarations.',mode='reproduction',source_refs=[source],policy=dict(planner_max_calls=1)))
    f=frame('strict_reproduction');f['assumptions']=[]
    step(store,root,'revise_frame',frame=f,reason='Freeze strict scope and native checks.')
    return store,root,source


@pytest.mark.parametrize('kind',CASES)
def test_literal_builder_is_readonly_and_existing_gates_execute_its_exact_math(tmp_path,kind):
    store,root,source=prepared(tmp_path,kind)
    before=store.list();candidate=source_models.build(store,source,kind)
    assert store.list()==before
    if kind!='ode':step(store,root,'bind_source',source_ref=source,model_type=kind,reason='Bind exact declaration lines.')
    execution=dict(experiment=dict(times=[0.,1.,2.],time_unit='second')) if kind=='ode' else dict(settings=dict(steps=4)) if kind=='boolean' else dict(settings=dict(duration=2.,samples=5,trajectories=8,seed=123))
    it=step(store,root,'open_source_iteration',source_ref=source,model_type=kind,reason='Choose the reported mathematical declarations.',execution=execution)['iteration_ref']
    run=step(store,root,'run_iteration',iteration_ref=it)
    assert p.facts(store,it)['numeric_status']=='pass'
    if kind!='ode':assert p.facts(store,it)['source_binding_status']=='pass'
    assert p.inspect(store,root)['budget']['native_reserved']==0
    assert not store.list('research_native_provider_call')
    assert store.get(run['result_ref'])['payload']['scientific_support']=='unknown' if kind!='ode' else True


@pytest.mark.parametrize('kind',CASES)
@pytest.mark.parametrize('defect',['missing','conflict','qualified','conditions'])
def test_literal_builder_refuses_missing_conflicting_or_contextual_math(tmp_path,kind,defect):
    raw=(ROOT/CASES[kind]).read_text()
    target={'ode':'Parameter r = 0.6.','boolean':'Rule A = not B.','stochastic':'Stochastic rate birth = 5.'}[kind]
    if defect=='missing':raw=raw.replace(target,'')
    elif defect=='conflict':raw+='\n'+target.replace('0.6','0.7').replace('not B','B').replace('= 5.','= 6.')+'\n'
    elif defect=='qualified':raw=raw.replace(target,target+' But this is uncertain.')
    else:raw+='\nCondition temperature = 37.\n'
    store,root,source=prepared(tmp_path,kind,raw);before=store.list()
    with pytest.raises(ValueError):source_models.build(store,source,kind)
    assert store.list()==before and not store.list('typed_model') and not store.list('modeling_plan')


def test_native_literal_iteration_does_not_forge_frame_evidence(tmp_path):
    store,root,source=prepared(tmp_path,'stochastic')
    with pytest.raises(ValueError,match='source bindings'):
        step(store,root,'open_source_iteration',source_ref=source,model_type='stochastic',reason='Missing frame bindings.',execution=dict(settings={}))
    assert not p.inspect(store,root)['iteration_refs']


@pytest.mark.parametrize('kind',CASES)
@pytest.mark.parametrize('prefix',['Synthetic ','# '])
@pytest.mark.parametrize('statement',[
    'Condition temperature = 37.',
    'Conflicting parameter specification.',
])
def test_scientific_qualifiers_cannot_be_hidden_as_metadata(tmp_path,kind,prefix,statement):
    raw=(ROOT/CASES[kind]).read_text()+'\n'+prefix+statement+'\n'
    store,root,source=prepared(tmp_path,kind,raw)
    before=store.list()
    with pytest.raises(ValueError):
        source_models.build(store,source,kind)
    assert store.list()==before
    assert not p.inspect(store,root)['iteration_refs']


def test_simple_source_tool_contract_runs_through_supervisor_saved_response(tmp_path):
    store,root,source=prepared(tmp_path,'ode')
    provider=Scripted([act('open_source_iteration',source_ref=source,model_type='ode',reason='Use complete literal equations.',
        execution=dict(experiment=dict(times=[0.,1.],time_unit='second')))])
    supervisor.run(store,provider,root)
    view=p.inspect(store,root)
    assert len(view['iteration_refs'])==1 and len(provider.requests)==1 and view['budget']['native_reserved']==0
