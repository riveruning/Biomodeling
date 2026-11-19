"""Frozen three-grid convergence for the cell-centred multi-field PDE system.

Independent continuum Fourier controls: every field solves
u_t = D*u_xx - decay*u (and the tensor product in 2D), so the exact continuum
solution is a decaying sine eigenmode sampled at the cell centres and the
frozen sample times. Both required fields use different diffusion and decay
and both show second-order error convergence. All inputs are synthetic
numeric controls; no literature pools, credentials, independent evaluation
sets or external services are used. The legacy nodal protocol keeps its own
regression coverage in tests/test_spatial_convergence.py and appears here only
as a mixed-protocol negative control.
"""
import json
import math
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from biomodel_agent import cli, pde_system, spatial_convergence as convergence
from biomodel_agent import research_projects as projects, typed_models as models
from biomodel_agent import typed_references as references
from biomodel_agent.storage import ArtifactStore

SPECS=({'id':'u','diffusion':0.25,'decay':0.1,'amplitude':1.0},
       {'id':'v','diffusion':0.05,'decay':0.35,'amplitude':0.8})


def continuum(shape,lengths,amplitude):
    """Exact continuum eigenmode sampled at the cell centres."""
    wave=np.full(shape,float(amplitude))
    for axis_index,(size,length) in enumerate(zip(shape,lengths)):
        axis=(np.arange(size)+0.5)*(length/size)
        form=[1]*len(shape);form[axis_index]=size
        wave=wave*np.sin(np.pi*axis/length).reshape(form)
    return wave


def system_model(shape,lengths,specs,*,boundary='dirichlet_zero',units=None,field_ids=None):
    order=list(field_ids or [spec['id'] for spec in specs])
    by_id={spec['id']:spec for spec in specs}
    fields=[]
    for field_id in order:
        spec=by_id[field_id]
        initial=(np.full(shape,float(spec['constant'])) if 'constant' in spec
                 else continuum(shape,lengths,spec['amplitude']))
        fields.append(dict(id=field_id,diffusion=spec['diffusion'],initial=initial.tolist(),
            reaction=f"0 - decay_{field_id}*{field_id}",nonnegative=True))
    return dict(schema_version=1,model_type='pde',profile=pde_system.PROFILE,
        id='convergence_'+'x'.join(str(size) for size in shape),
        units=deepcopy(units or {'time':'arbitrary time unit','space':'arbitrary length unit',
                                 'state':'dimensionless synthetic concentration'}),
        grid=dict(shape=list(shape),lengths=[float(value) for value in lengths]),
        boundary=boundary,parameters={f"decay_{spec['id']}":spec['decay'] for spec in specs},
        fields=fields,
        note='Synthetic linear Fourier convergence control; not a biological claim.')


def exact_solution(model,field_index,times):
    field=model['fields'][field_index];field_id=field['id']
    rate=(field['diffusion']*sum(math.pi**2/float(length)**2 for length in model['grid']['lengths'])
          +model['parameters'][f"decay_{field_id}"])
    initial=np.asarray(field['initial'],dtype=float)
    return np.exp(-rate*np.asarray(times,dtype=float).reshape((-1,)+(1,)*initial.ndim))*initial


def raw_settings(**changes):
    values=dict(duration=0.5,samples=5,method='RK45',rtol=1e-8,atol=1e-11)
    values.update(changes)
    return values


def _offset(plane,amount):
    if isinstance(plane[0],list):return [_offset(row,amount) for row in plane]
    return [value+amount for value in plane]


def system_reference(model,raw,*,threshold=0.05,scale_factors=None,time_shift=0.0,axis_shift=0.0,
                     reverse_fields=False,initial_offset=0.0,later_offset=0.0):
    cfg=pde_system.config(raw)
    times=np.linspace(0.,cfg['duration'],cfg['samples'])+time_shift
    shape=model['grid']['shape'];lengths=model['grid']['lengths']
    axes={name:(((np.arange(size)+0.5)*(length/size))+axis_shift).tolist()
          for name,size,length in zip(('x','y'),shape,lengths)}
    field_ids=[field['id'] for field in model['fields']]
    exact=[exact_solution(model,index,times) for index in range(len(field_ids))]
    values=[[exact[index][t].tolist() for index in range(len(field_ids))] for t in range(len(times))]
    scales=[max(1.,float(np.max(np.abs(plane)))) for plane in exact]
    if scale_factors:scales=[scale*factor for scale,factor in zip(scales,scale_factors)]
    if initial_offset:values[0][1]=_offset(values[0][1],initial_offset)
    if later_offset:
        for per_time in values[1:]:per_time[1]=_offset(per_time[1],later_offset)
    if reverse_fields:
        field_ids=list(reversed(field_ids))
        values=[list(reversed(per_time)) for per_time in values]
        scales=list(reversed(scales))
    return dict(schema_version=1,model_type='pde',profile=references.PDE_SYSTEM_PROFILE,
        units=deepcopy(model['units']),times=times.tolist(),axes=axes,field_ids=field_ids,
        values=values,scales=scales,threshold=threshold)


def make_input(store,directory,tag,shape,lengths,specs,*,raw=None,**options):
    raw=raw or raw_settings()
    model_options={key:options.pop(key) for key in ('boundary','units','field_ids') if key in options}
    model=system_model(shape,lengths,specs,**model_options)
    reference=system_reference(model,raw,**options)
    path=Path(directory)/f'{tag}.json';path.write_text(json.dumps(model),encoding='utf-8')
    rr=projects.register_reference(store,dict(model_type='pde',reference=reference,
        note='Synthetic analytic Fourier control; operator-supplied, not independently reviewed.'),'tester')
    return models.prepare(store,models.import_model(store,path),raw,reference_ref=rr)


def prepared_legacy(tmp_path,store):
    refs=[]
    for nodes in (17,33,65):
        x=[i/(nodes-1) for i in range(nodes)]
        initial=[math.sin(math.pi*v) for v in x];initial[0]=initial[-1]=0.
        model=dict(schema_version=1,model_type='pde',id='legacy_sine_'+str(nodes),
            units={'time':'second','space':'millimeter','state':'normalized'},
            length=1.,diffusion=.25,decay=.1,boundary='dirichlet_zero',initial=initial)
        path=tmp_path/f'legacy-{nodes}.json';path.write_text(json.dumps(model))
        reference=dict(schema_version=1,model_type='pde',units=model['units'],profile='field_rmse_v1',
            times=[0.,.5,1.],x=x,
            values=[[math.exp(-(.25*math.pi**2+.1)*t)*value for value in initial] for t in (0.,.5,1.)],
            scale=1.,threshold=.002)
        rr=projects.register_reference(store,dict(model_type='pde',reference=reference,
            note='Legacy nodal sine control.'),'tester')
        refs.append(models.prepare(store,models.import_model(store,path),dict(duration=1.,samples=3),reference_ref=rr))
    return refs


def _cli(capsys,args):
    assert cli.main(args)==0
    return capsys.readouterr().out


def test_system_1d_three_grid_protocol_reports_per_field_second_order(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=[make_input(store,directory,f'sys1d-{shape[0]}',shape,(1.0,),SPECS,
                     raw=raw_settings(timeout_s=20.+5*index))
          for index,shape in enumerate(((8,),(16,),(32,)))]
    protocol=convergence.prepare(store,refs)
    payload=store.get(protocol)['payload']
    assert payload['version']==2 and payload['model_profile']==pde_system.PROFILE
    assert payload['input_refs']==refs and payload['scientific_support']=='unknown'
    assert payload['reference_scope']==convergence.REFERENCE_SCOPE
    assert payload['initial_condition_scope']==convergence.SYSTEM_INITIAL_SCOPE
    assert convergence.evaluate(store,protocol)['status']=='not_run'
    for ref in refs:models.execute(store,ref)
    before=store.list();result=convergence.evaluate(store,protocol)
    assert store.list()==before and convergence.evaluate(store,protocol)==result
    assert result['status']=='pass' and result['convergence_status']=='pass'
    assert result['field_order']==['u','v'] and result['per_field_status']=={'u':'pass','v':'pass'}
    for field_id in ('u','v'):
        errors=result['per_field_scaled_rmse'][field_id]
        assert errors[0]>errors[1]>errors[2]>0
        assert all(1.8<=order<=2.2 for order in result['per_field_observed_orders'][field_id])
    assert all(value=='pass' for value in result['reference_comparisons'])
    assert all(1.8<=order<=2.2 for order in result['observed_orders'])
    assert result['scientific_support']=='unknown'
    json.dumps(result,allow_nan=False)
    with pytest.raises(ValueError,match='before'):convergence.prepare(store,refs)


def test_system_2d_nonsquare_three_grid_protocol_reports_per_field_orders(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=[make_input(store,directory,f'sys2d-{index}',shape,(1.0,1.3),SPECS,
                     raw=raw_settings(duration=0.2))
          for index,shape in enumerate(((6,8),(12,16),(24,32)))]
    protocol=convergence.prepare(store,refs)
    assert store.get(protocol)['payload']['version']==2
    assert convergence.evaluate(store,protocol)['status']=='not_run'
    for ref in refs:models.execute(store,ref)
    before=store.list();result=convergence.evaluate(store,protocol)
    assert store.list()==before
    assert result['status']=='pass' and result['convergence_status']=='pass'
    assert result['field_order']==['u','v'] and result['per_field_status']=={'u':'pass','v':'pass'}
    for field_id in ('u','v'):
        errors=result['per_field_scaled_rmse'][field_id]
        assert errors[0]>errors[1]>errors[2]>0
        assert all(1.8<=order<=2.2 for order in result['per_field_observed_orders'][field_id])
    assert all(value=='pass' for value in result['reference_comparisons'])
    json.dumps(result,allow_nan=False)


def test_exact_zero_field_error_stays_unknown_per_field(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    specs=(*SPECS,{'id':'w','diffusion':0.0,'decay':0.0,'constant':0.5})
    refs=[make_input(store,directory,f'zero-{shape[0]}',shape,(1.0,),specs)
          for shape in ((8,),(16,),(32,))]
    protocol=convergence.prepare(store,refs)
    for ref in refs:models.execute(store,ref)
    result=convergence.evaluate(store,protocol)
    assert result['status']=='unknown' and result['convergence_status']=='unknown'
    assert result['per_field_status']=={'u':'pass','v':'pass','w':'unknown'}
    assert result['per_field_scaled_rmse']['w']==[0.,0.,0.]
    assert result['per_field_observed_orders']['w']==[None,None]
    assert all(1.8<=order<=2.2 for order in result['per_field_observed_orders']['u'])
    json.dumps(result,allow_nan=False)


def test_single_field_failure_cannot_be_hidden_by_the_aggregate(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    specs=({'id':'u','diffusion':0.25,'decay':0.1,'amplitude':1.0},
           {'id':'v','diffusion':0.01,'decay':0.35,'amplitude':0.8})
    refs=[make_input(store,directory,f'mask-{shape[0]}',shape,(1.0,),specs,
                     later_offset=0.001 if index==0 else 0.0)
          for index,shape in enumerate(((8,),(16,),(32,)))]
    protocol=convergence.prepare(store,refs)
    for ref in refs:models.execute(store,ref)
    result=convergence.evaluate(store,protocol)
    assert result['status']=='fail' and result['convergence_status']=='fail'
    assert result['per_field_status']=={'u':'pass','v':'fail'}
    assert result['per_field_observed_orders']['v'][0]>2.2
    # The scale-normalised aggregate alone would have looked convergent.
    assert all(1.8<=order<=2.2 for order in result['observed_orders'])
    assert all(value=='pass' for value in result['reference_comparisons'])


def test_missing_or_unknown_runs_are_never_solved_by_evaluate(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=[make_input(store,directory,f'unknown-{shape[0]}',shape,(1.0,),SPECS)
          for shape in ((8,),(16,),(32,))]
    protocol=convergence.prepare(store,refs)
    before=store.list();result=convergence.evaluate(store,protocol)
    assert result['status']=='not_run' and result['scientific_support']=='unknown'
    assert store.list()==before and not store.list('typed_execution') and not store.list('typed_run')
    store.put('typed_execution',dict(version=1,input_ref=refs[0],phase='reserved',id=uuid4().hex),
              refs=(refs[0],))
    assert models.status(store,refs[0])['state']=='unknown_effects'
    assert convergence.evaluate(store,protocol)['status']=='not_run'
    assert not store.list('typed_run')
    with pytest.raises(ValueError):convergence.prepare(store,refs)


def test_legacy_and_system_inputs_cannot_share_a_protocol(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    system=[make_input(store,directory,f'sys-{shape[0]}',shape,(1.0,),SPECS)
            for shape in ((8,),(16,),(32,))]
    legacy=prepared_legacy(tmp_path,store)
    for mixed in ([legacy[0],system[0],system[1]],[system[0],legacy[1],system[2]]):
        with pytest.raises(ValueError):convergence.prepare(store,mixed)
    assert not store.list(convergence.PROTOCOL)
    protocol=convergence.prepare(store,legacy)
    assert store.get(protocol)['payload']['version']==1


def test_partially_doubled_axes_are_rejected(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=[make_input(store,directory,f'partial-{index}',shape,(1.0,1.3),SPECS,
                     raw=raw_settings(duration=0.2))
          for index,shape in enumerate(((6,8),(12,16),(24,16)))]
    with pytest.raises(ValueError):convergence.prepare(store,refs)
    assert not store.list(convergence.PROTOCOL)


def _defect_triple(store,directory,defect):
    shapes=[(8,),(16,),(32,)]
    specs=[SPECS,SPECS,SPECS]
    raws=[raw_settings(),raw_settings(),raw_settings()]
    options=[{},{},{}]
    if defect=='shape_not_doubled':shapes[2]=(24,)
    elif defect=='diffusion_changed':
        specs[2]=({'id':'u','diffusion':0.3,'decay':0.1,'amplitude':1.0},SPECS[1])
    elif defect=='parameter_changed':
        specs[2]=({'id':'u','diffusion':0.25,'decay':0.1,'amplitude':1.0},
                  {'id':'v','diffusion':0.05,'decay':0.4,'amplitude':0.8})
    elif defect=='units_changed':
        options[2]['units']={'time':'different time unit','space':'arbitrary length unit',
                             'state':'dimensionless synthetic concentration'}
    elif defect=='field_order_changed':options[2]['field_ids']=('v','u')
    elif defect=='boundary_changed':options[2]['boundary']='no_flux'
    elif defect=='duration_changed':raws[2]=raw_settings(duration=0.4)
    elif defect=='samples_changed':raws[2]=raw_settings(samples=7)
    elif defect=='method_changed':raws[2]=raw_settings(method='BDF')
    elif defect=='scales_changed':options[2]['scale_factors']=[1.05,1.0]
    elif defect=='threshold_changed':options[2]['threshold']=0.5
    elif defect=='times_changed':options[2]['time_shift']=0.01
    elif defect=='axes_changed':options[2]['axis_shift']=1e-6
    elif defect=='field_ids_changed':options[2]['reverse_fields']=True
    elif defect=='initial_mismatch':options[2]['initial_offset']=0.01
    refs=[make_input(store,directory,f'defect-{index}',shape,(1.0,),specs[index],
                     raw=raws[index],**options[index])
          for index,shape in enumerate(shapes)]
    if defect=='duplicate':refs=[refs[1]]*3
    elif defect=='reversed':refs=list(reversed(refs))
    return refs


@pytest.mark.parametrize('defect',['duplicate','reversed','shape_not_doubled','diffusion_changed',
    'parameter_changed','units_changed','field_order_changed','boundary_changed','duration_changed',
    'samples_changed','method_changed','scales_changed','threshold_changed','times_changed',
    'axes_changed','field_ids_changed','initial_mismatch'])
def test_inconsistent_system_triples_are_never_frozen(tmp_path,defect):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=_defect_triple(store,directory,defect)
    with pytest.raises(ValueError):convergence.prepare(store,refs)
    assert not store.list(convergence.PROTOCOL)


def test_forged_protocol_bindings_are_rejected(tmp_path):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    system=[make_input(store,directory,f'sys-{shape[0]}',shape,(1.0,),SPECS)
            for shape in ((8,),(16,),(32,))]
    legacy=prepared_legacy(tmp_path,store)

    def forge(payload,refs):
        return store.put(convergence.PROTOCOL,payload,refs=tuple(refs))

    def system_payload(**changes):
        payload=dict(version=2,input_refs=system,min_order=1.8,max_order=2.2,
            model_profile=pde_system.PROFILE,initial_condition_scope=convergence.SYSTEM_INITIAL_SCOPE,
            reference_scope=convergence.REFERENCE_SCOPE,scientific_support='unknown')
        payload.update(changes)
        return payload

    forged_legacy=dict(version=2,input_refs=legacy,min_order=1.8,max_order=2.2,
        model_profile=pde_system.PROFILE,initial_condition_scope=convergence.SYSTEM_INITIAL_SCOPE,
        reference_scope=convergence.REFERENCE_SCOPE,scientific_support='unknown')
    with pytest.raises(ValueError):convergence.evaluate(store,forge(forged_legacy,legacy))
    with pytest.raises(ValueError):convergence.evaluate(store,forge(dict(
        version=1,input_refs=system,min_order=1.8,max_order=2.2,
        reference_scope=convergence.REFERENCE_SCOPE,scientific_support='unknown'),system))
    with pytest.raises(ValueError):convergence.evaluate(store,
        forge(system_payload(model_profile='linear_reaction_diffusion_1d_v1'),system))
    with pytest.raises(ValueError):convergence.evaluate(store,
        forge(system_payload(initial_condition_scope='shared_continuum_initial_certified'),system))
    with pytest.raises(ValueError):convergence.evaluate(store,forge(system_payload(input_refs=legacy),system))
    with pytest.raises(ValueError):convergence.evaluate(store,forge(system_payload(version=3),system))
    protocol=convergence.prepare(store,system)
    assert convergence.evaluate(store,protocol)['status']=='not_run'


def test_convergence_cli_entries_accept_the_system_profile(tmp_path,capsys):
    store=ArtifactStore(tmp_path/'store');directory=tmp_path/'models';directory.mkdir()
    refs=[make_input(store,directory,f'cli-{shape[0]}',shape,(1.0,),SPECS)
          for shape in ((8,),(16,),(32,))]
    root=str(tmp_path/'store')
    protocol=json.loads(_cli(capsys,['pde-convergence-prepare',*refs,'--store',root]))['protocol_ref']
    assert store.get(protocol)['payload']['version']==2
    shown=json.loads(_cli(capsys,['pde-convergence-show',protocol,'--store',root]))
    assert shown['status']=='not_run' and shown['scientific_support']=='unknown'


@pytest.mark.parametrize("coarse,fine", [(5e-324, 1e308), (1e308, 5e-324)])
def test_observed_order_handles_full_finite_float_range(coarse, fine):
    expected = math.log2(coarse) - math.log2(fine)
    observed = convergence._order(coarse, fine)
    assert observed is not None and math.isfinite(observed)
    assert observed == pytest.approx(expected)
