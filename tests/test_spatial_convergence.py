"""Independent continuum sine solution controls spatial convergence orders."""
import json
import math
from pathlib import Path

import pytest

from biomodel_agent import typed_models as models,spatial_convergence as convergence,research_projects as projects
from biomodel_agent.storage import ArtifactStore


def prepared(tmp_path):
    store=ArtifactStore(tmp_path/'s');refs=[]
    for nodes in (17,33,65):
        x=[i/(nodes-1) for i in range(nodes)];times=[0.,.5,1.]
        initial=[math.sin(math.pi*v) for v in x];initial[0]=initial[-1]=0.
        model=dict(schema_version=1,model_type='pde',id='sine_'+str(nodes),units=dict(time='second',space='millimeter',state='normalized'),
            length=1.,diffusion=.25,decay=.1,boundary='dirichlet_zero',initial=initial)
        path=tmp_path/f'sine-{nodes}.json';path.write_text(json.dumps(model))
        mr=models.import_model(store,path)
        reference=dict(schema_version=1,model_type='pde',units=model['units'],profile='field_rmse_v1',times=times,x=x,
            values=[[math.exp(-(.25*math.pi**2+.1)*t)*value for value in initial] for t in times],scale=1.,threshold=.002)
        rr=projects.register_reference(store,dict(model_type='pde',reference=reference,note='Independent continuum sine eigenfunction.'),'tester')
        refs.append(models.prepare(store,mr,dict(duration=1.,samples=3),reference_ref=rr))
    return store,refs


def test_three_grid_protocol_is_frozen_before_execution_and_reports_second_order(tmp_path):
    store,refs=prepared(tmp_path);protocol=convergence.prepare(store,refs)
    assert convergence.evaluate(store,protocol)['status']=='not_run'
    for ref in refs:models.execute(store,ref)
    before=store.list();result=convergence.evaluate(store,protocol)
    assert store.list()==before and result['status']=='pass' and result['convergence_status']=='pass'
    assert all(1.8<=value<=2.2 for value in result['observed_orders'])
    assert result['scaled_rmse'][0]>result['scaled_rmse'][1]>result['scaled_rmse'][2]>0
    assert result['scientific_support']=='unknown'
    with pytest.raises(ValueError,match='before'):convergence.prepare(store,refs)


def test_reversed_or_duplicate_grid_inputs_are_not_convergence_protocols(tmp_path):
    store,refs=prepared(tmp_path)
    for bad in ([refs[0]]*3,list(reversed(refs))):
        with pytest.raises(ValueError):convergence.prepare(store,bad)
    assert not store.list(convergence.PROTOCOL)
