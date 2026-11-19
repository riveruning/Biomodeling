"""Independent analytic/discrete controls and mismatched reference negatives."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from biomodel_agent import typed_models as models,typed_references as references,research_projects as projects
from biomodel_agent.storage import ArtifactStore
from test_research_projects import frame,step

ROOT=Path(__file__).parents[1]


def fixture(kind):
    model=json.loads((ROOT/f'examples/model-types/{kind}.json').read_text())
    common=dict(schema_version=1,model_type=kind,units=model['units'],profile=references.PROFILES[kind])
    if kind=='boolean':
        model['nodes']=[dict(id='A',initial=False,rule='not A')]
        settings=dict(steps=3)
        spec=dict(common,steps=[0,1,2,3],nodes=['A'],states=[[False],[True],[False],[True]])
    elif kind=='stochastic':
        model['species'][0]['initial']=0
        model['reactions']=model['reactions'][:1];model['reactions'][0]['rate']=2.
        settings=dict(duration=2.,samples=3,trajectories=256,seed=19)
        # Pure births at rate 2 have a Poisson(2t) distribution.
        spec=dict(common,times=[0.,1.,2.],species=['X'],mean=[[0.],[2.],[4.]],variance=[[0.],[2.],[4.]],
            mean_scale=10.,variance_scale=10.,threshold=.1,min_trajectories=128)
    elif kind=='pde':
        model.update(boundary='periodic',initial=[1.]*8)
        settings=dict(duration=2.,samples=3)
        spec=dict(common,times=[0.,1.,2.],x=[(i+.5)/8 for i in range(8)],
            values=[[math.exp(-.1*t)]*8 for t in range(3)],scale=1.,threshold=1e-8)
    else:
        model['probabilities']=dict(death=0.,move=0.,divide=0.)
        settings=dict(steps=3)
        spec=dict(common,steps=[0,1,2,3],population=[2]*4,scale=2.,threshold=1e-8)
    return model,settings,spec


@pytest.mark.parametrize('kind',references.PROFILES)
def test_native_reference_is_frozen_before_real_execution_and_replay(tmp_path,kind):
    model,settings,spec=fixture(kind);store=ArtifactStore(tmp_path/'s')
    path=tmp_path/'model.json';path.write_text(json.dumps(model),encoding='utf-8')
    mr=models.import_model(store,path)
    rr=projects.register_reference(store,dict(model_type=kind,reference=spec,note='Independent synthetic control.'),'tester')
    inp=models.prepare(store,mr,settings,reference_ref=rr)
    assert not store.list('typed_run') and rr in store.get(inp)['refs']
    run=models.execute(store,inp);summary=models.summary(store,run)
    assert summary['comparison']['status']=='pass' and summary['completion_eligible']
    assert summary['scientific_support']=='unknown'
    replay=models.replay(store,run)
    assert models.load_input(store,store.get(replay)['payload']['input_ref'])[0]['reference_ref']==rr
    assert models.summary(store,replay)['comparison']['status']=='pass'


@pytest.mark.parametrize('kind',references.PROFILES)
def test_reference_mismatch_and_unit_mismatch_cannot_pass(kind):
    model,settings,spec=fixture(kind);backend=models.backend(kind);settings=backend.config(settings)
    outcome=dict(status='succeeded',data=backend.execute(model,settings))
    bad=deepcopy(spec)
    if kind=='boolean':bad['states'][0][0]=True
    elif kind=='stochastic':bad['mean']=[[1000.]]*3
    elif kind=='pde':bad['values']=[[1000.]*8]*3
    else:bad['population']=[2000]*4
    assert references.compare(bad,model,settings,outcome)['status']=='fail'
    bad=deepcopy(spec);bad['units']['time']='different_unit'
    with pytest.raises(ValueError,match='units mismatch'):references.compare(bad,model,settings,outcome)
    bad=deepcopy(spec);clock='times' if kind in ('stochastic','pde') else 'steps';bad[clock][-1]+=1
    assert references.compare(bad,model,settings,outcome)['status']=='fail'


@pytest.mark.parametrize('defect',['boolean_number','nonfinite','wrong_shape','too_few_trajectories','unknown_field'])
def test_invalid_reference_protocols_are_rejected(defect):
    kind='boolean' if defect=='boolean_number' else 'stochastic'
    model,settings,spec=fixture(kind)
    if defect=='boolean_number':spec['states'][0][0]=0
    elif defect=='nonfinite':spec['mean'][0][0]=float('nan')
    elif defect=='wrong_shape':spec['variance'][0].append(1.)
    elif defect=='too_few_trajectories':settings['trajectories']=1
    else:spec['accepted']=True
    with pytest.raises(ValueError):references.compatible(spec,model,models.backend(kind).config(settings))


def test_project_reports_reference_failure_separately_from_numerical_success(tmp_path):
    model,settings,spec=fixture('boolean');spec['states'][0][0]=True
    store=ArtifactStore(tmp_path/'s')
    rr=projects.register_reference(store,dict(model_type='boolean',reference=spec,note='Deliberate negative reference.'),'tester')
    root=projects.create(store,dict(scenario='Toggle control.',reference_refs=[rr]))
    step(store,root,'revise_frame',frame=frame(),reason='Freeze criteria.')
    it=step(store,root,'open_iteration',reason='Negative external comparison.',native_draft=model,hypothesis_ids=['H1'],
        reference_ref=rr,config=dict(model_type='boolean',execution=dict(settings=settings)))['iteration_ref']
    step(store,root,'run_iteration',iteration_ref=it)
    facts=projects.facts(store,it)
    assert facts['numeric_status']=='pass' and facts['comparison_status']=='fail' and facts['state']=='failed'
