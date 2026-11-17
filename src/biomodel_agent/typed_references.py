"""Frozen external observation protocols for native model families.

No interpolation, parameter fitting, unit conversion or scientific certification.
SSA moment discrepancies are descriptive comparisons, not significance tests.
"""
from copy import deepcopy
import math

import numpy as np

from . import model_contracts as c
from .storage import canonical_bytes

PROFILES=dict(stochastic='ensemble_moments_v1',boolean='exact_states_v1',pde='field_rmse_v1',individual='population_rmse_v1')
PDE_SYSTEM_PROFILE='field_system_rmse_v1'
_MAX_SYSTEM_SCALARS=1000000


def _array(value,shape,*,boolean=False,nonnegative=False):
    if len(shape)==0:
        if boolean:
            if type(value) is not bool:raise ValueError('reference states must be JSON booleans')
        elif type(value) not in (int,float) or not math.isfinite(value) or nonnegative and value<0:
            raise ValueError('reference observations must be finite numbers with valid signs')
        return
    if not isinstance(value,list) or len(value)!=shape[0]:raise ValueError('reference array shape mismatch')
    for item in value:_array(item,shape[1:],boolean=boolean,nonnegative=nonnegative)


def _axis(values,minimum,maximum,label):
    if not isinstance(values,list) or not minimum<=len(values)<=maximum:
        raise ValueError(f'{label} need {minimum}..{maximum} explicit finite coordinates')
    _array(values,(len(values),))
    if any(b<=a for a,b in zip(values,values[1:])):raise ValueError(f'{label} must strictly increase')
    return values


def _system_spec(spec):
    """Strict field_system_rmse_v1 protocol: exact grids, per-field scales, no field averages."""
    c.fields(spec,{'schema_version','model_type','units','profile','times','axes','field_ids','values','scales','threshold'})
    if type(spec['schema_version']) is not int or spec['schema_version']!=1:
        raise ValueError('native reference profile/version mismatch')
    if not isinstance(spec['units'],dict) or not 1<=len(spec['units'])<=4:raise ValueError('reference needs explicit native units')
    for key,value in spec['units'].items():c.label(key);c.label(value)
    _axis(spec['times'],2,1001,'reference times')
    if spec['times'][0]<0:raise ValueError('reference times must be nonnegative')
    axes=spec['axes']
    if not isinstance(axes,dict) or set(axes) not in ({'x'},{'x','y'}):
        raise ValueError('reference needs an explicit x axis and an optional y axis')
    shape=[]
    for name in ('x','y'):
        if name in axes:
            _axis(axes[name],3,128,f'reference {name} axis');shape.append(len(axes[name]))
    field_ids=spec['field_ids']
    if not isinstance(field_ids,list) or not 1<=len(field_ids)<=8 or len(set(field_ids))!=len(field_ids):
        raise ValueError('reference needs 1..8 distinct field identifiers')
    for name in field_ids:c.identifier(name)
    if len(spec['times'])*len(field_ids)*math.prod(shape)>_MAX_SYSTEM_SCALARS:
        raise ValueError('field-system reference exceeds 1000000 scalars')
    _array(spec['values'],(len(spec['times']),len(field_ids),*shape))
    scales=spec['scales']
    if not isinstance(scales,list) or len(scales)!=len(field_ids):
        raise ValueError('reference needs exactly one positive scale per field')
    for index,scale in enumerate(scales):c.number(scale,1e-12,1e12,f'scale for field {field_ids[index]!r}')
    c.number(spec['threshold'],1e-12,1.,'threshold')
    return deepcopy(spec)


def validate(spec):
    if not isinstance(spec,dict):raise ValueError('bounded reference object required')
    kind=spec.get('model_type')
    if kind not in PROFILES:raise ValueError('unsupported native reference family')
    system=kind=='pde' and spec.get('profile')==PDE_SYSTEM_PROFILE
    if len(canonical_bytes(spec))>(2000000 if system else 128000):raise ValueError('bounded reference object required')
    if system:return _system_spec(spec)
    common={'schema_version','model_type','units','profile'}
    extra=dict(stochastic={'times','species','mean','variance','mean_scale','variance_scale','threshold','min_trajectories'},
        boolean={'steps','nodes','states'},pde={'times','x','values','scale','threshold'},
        individual={'steps','population','scale','threshold'})[kind]
    c.fields(spec,common|extra)
    if type(spec['schema_version']) is not int or spec['schema_version']!=1 or spec['profile']!=PROFILES[kind]:
        raise ValueError('native reference profile/version mismatch')
    if not isinstance(spec['units'],dict) or not 1<=len(spec['units'])<=4:raise ValueError('reference needs explicit native units')
    for key,value in spec['units'].items():c.label(key);c.label(value)
    clock='times' if kind in ('stochastic','pde') else 'steps'
    grid=spec[clock]
    if not isinstance(grid,list) or not 2<=len(grid)<=1001:raise ValueError('reference needs 2..1001 explicit coordinates')
    _array(grid,(len(grid),),nonnegative=True)
    if any(b<=a for a,b in zip(grid,grid[1:])):raise ValueError('reference coordinates must strictly increase')
    if clock=='steps' and any(type(x) is not int for x in grid):raise ValueError('step coordinates must be integers')
    n=len(grid)
    if kind in ('stochastic','boolean'):
        names=spec['species' if kind=='stochastic' else 'nodes']
        if not isinstance(names,list) or not 1<=len(names)<=32 or len(set(names))!=len(names):raise ValueError('reference needs distinct variable names')
        for name in names:c.identifier(name)
        if kind=='stochastic':
            _array(spec['mean'],(n,len(names)),nonnegative=True);_array(spec['variance'],(n,len(names)),nonnegative=True)
            c.integer(spec['min_trajectories'],2,256,'min_trajectories')
            for key in ('mean_scale','variance_scale'):c.number(spec[key],1e-12,1e12,key)
        else:_array(spec['states'],(n,len(names)),boolean=True)
    elif kind=='pde':
        x=spec['x']
        if not isinstance(x,list) or not 3<=len(x)<=1001:raise ValueError('reference needs an explicit spatial grid')
        _array(x,(len(x),))
        if any(b<=a for a,b in zip(x,x[1:])):raise ValueError('spatial coordinates must strictly increase')
        _array(spec['values'],(n,len(x)))
    else:
        _array(spec['population'],(n,),nonnegative=True)
        if any(type(x) is not int for x in spec['population']):raise ValueError('populations must be integer counts')
    if kind!='boolean':
        c.number(spec['threshold'],1e-12,1.,'threshold')
        if kind!='stochastic':c.number(spec['scale'],1e-12,1e12,'scale')
    return deepcopy(spec)


def compatible(reference,model,settings):
    spec=validate(reference)
    if spec['model_type']!=model['model_type'] or spec['units']!=model['units']:
        raise ValueError('reference model family/units mismatch; conversions must be reviewed before freezing')
    if spec['model_type']=='pde':
        from . import pde_system
        expected=PDE_SYSTEM_PROFILE if model.get('profile')==pde_system.PROFILE else PROFILES['pde']
        if spec['profile']!=expected:raise ValueError('reference profile does not match the model PDE profile')
    if model['model_type']=='stochastic' and settings['trajectories']<spec['min_trajectories']:
        raise ValueError('SSA reference requires the frozen minimum trajectory count')
    return spec


def _compare_system(spec,data,base):
    """Per-field scale-normalised RMSE on the exact frozen grid; one bad field fails."""
    from . import pde_system
    if not isinstance(data,dict) or data.get('field_ids')!=spec['field_ids']:
        return dict(base,status='fail',reason='Frozen coordinates/variables mismatch: field_ids')
    times=pde_system._numeric_array(data.get('times'),(len(spec['times']),))
    if times is None or not np.array_equal(times,spec['times']):
        return dict(base,status='fail',reason='Frozen coordinates/variables mismatch: times')
    axes=data.get('axes')
    if not isinstance(axes,dict) or set(axes)!=set(spec['axes']):
        return dict(base,status='fail',reason='Frozen coordinates/variables mismatch: axes')
    for name,expected_axis in spec['axes'].items():
        observed_axis=pde_system._numeric_array(axes[name],(len(expected_axis),))
        if observed_axis is None or not np.array_equal(observed_axis,expected_axis):
            return dict(base,status='fail',reason='Frozen coordinates/variables mismatch: axes')
    shape=(len(spec['times']),len(spec['field_ids']),*[len(spec['axes'][name]) for name in ('x','y') if name in spec['axes']])
    observed=pde_system._numeric_array(data.get('values'),shape)
    if observed is None or not np.isfinite(observed).all():
        return dict(base,status='fail',reason='Invalid observation shape or values.')
    expected=np.asarray(spec['values'],dtype=float).reshape(shape)
    observed=observed.astype(float);scores={}
    for index,field_id in enumerate(spec['field_ids']):
        value=float(np.sqrt(np.mean(((observed[:,index]-expected[:,index])/spec['scales'][index])**2)))
        if not math.isfinite(value):return dict(base,status='fail',reason='Invalid observation shape or values.')
        scores[field_id]=value
    passed=all(value<=spec['threshold'] for value in scores.values())
    return dict(base,status='pass' if passed else 'fail',metric='per_field_scale_rmse',scores=scores,threshold=spec['threshold'],
        note='Per-field fixed-scale RMSE on the exact frozen grid and units; every declared field must meet the threshold separately.')


def compare(reference,model,settings,result):
    spec=compatible(reference,model,settings);kind=model['model_type']
    base=dict(profile=spec['profile'],scientific_support='unknown',independent=False)
    if result.get('status')!='succeeded':return dict(base,status='not_run',reason='No complete numerical output.')
    data=result['data']
    if kind=='pde' and spec['profile']==PDE_SYSTEM_PROFILE:return _compare_system(spec,data,base)
    clock='times' if kind in ('stochastic','pde') else 'steps'
    for key in [clock]+(['species'] if kind=='stochastic' else ['nodes'] if kind=='boolean' else ['x'] if kind=='pde' else []):
        if data[key]!=spec[key]:return dict(base,status='fail',reason='Frozen coordinates/variables mismatch: '+key)
    if kind=='boolean':
        matched=data['states']==spec['states']
        return dict(base,status='pass' if matched else 'fail',metric='exact_state_sequence',matched=matched)
    keys=('mean','variance') if kind=='stochastic' else ('values',) if kind=='pde' else ('population',)
    scores={}
    for key in keys:
        a=np.asarray(data[key],dtype=float);b=np.asarray(spec[key],dtype=float)
        if a.shape!=b.shape or not np.isfinite(a).all():return dict(base,status='fail',reason='Invalid observation shape or values.')
        scale=spec[key+'_scale'] if kind=='stochastic' else spec['scale']
        scores[key]=float(np.sqrt(np.mean(((a-b)/scale)**2)))
    passed=all(math.isfinite(value) and value<=spec['threshold'] for value in scores.values())
    return dict(base,status='pass' if passed else 'fail',metric='fixed_scale_rmse',scores=scores,threshold=spec['threshold'],
        note='SSA moments use descriptive ddof=0 population variance; no p-value or biological validation is implied.' if kind=='stochastic' else 'Comparison uses the exact frozen grid and units.')
