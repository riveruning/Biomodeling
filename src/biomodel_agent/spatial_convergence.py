"""Predeclared three-grid convergence against frozen external PDE fields.

Two protocol versions share the pde-convergence-prepare/show entrances:

- version 1 (legacy): nested nodal zero-Dirichlet grids of the scalar linear
  reaction-diffusion profile, refinement factor two. Matching initial samples
  at shared nodes does not prove an identical continuum field.
- version 2 (cell-centred system): the multi-field
  ``reaction_diffusion_cartesian_v1`` profile on 1D/2D cell-centred grids
  whose cell count doubles on every axis in the supplied order. Cell-centred
  grids share no nodes, so the nodal sampling rule never applies; each grid's
  declared initial field must equal its own frozen external reference at
  t=0. That per-grid agreement does not certify a common continuum initial
  field, and reasonable point-sampled initial values are never forced to be
  cell averages.

External references stay operator-frozen controls. Scientific support remains
unknown in both versions; no protocol output self-certifies independent
domain review.
"""
import math

import numpy as np

from . import model_contracts as c,pde_system,typed_models
from .storage import canonical_bytes

PROTOCOL='pde_convergence_protocol'
REFERENCE_SCOPE='operator_frozen_external_fields_not_independent_domain_review'
SYSTEM_INITIAL_SCOPE=('each_grid_initial_matches_only_its_own_frozen_reference_time_zero_'
                      'common_continuum_initial_not_certified')


def _inputs(store,refs):
    if not isinstance(refs,list) or len(refs)!=3 or len(set(refs))!=3:raise ValueError('select three distinct frozen PDE inputs')
    from .research_projects import _reference
    rows=[]
    for ref in refs:
        inp,model=typed_models.load_input(store,ref)
        if model['model_type']!='pde' or not inp.get('reference_ref'):
            raise ValueError('convergence needs PDE inputs with external field references')
        rp=_reference(store,inp['reference_ref'])
        if rp['reference_type']!='typed_observations':raise ValueError('frozen field reference required')
        rows.append((inp,model,rp['reference']))
    system=[row[1].get('profile')==pde_system.PROFILE for row in rows]
    if len(set(system))!=1:raise ValueError('convergence inputs must not mix legacy nodal and cell-centred multi-field PDEs')
    return (2,_system_inputs(rows)) if system[0] else (1,_legacy_inputs(rows))


def _legacy_inputs(rows):
    for inp,model,reference in rows:
        if model['boundary']!='dirichlet_zero' or reference['profile']!='field_rmse_v1':
            raise ValueError('convergence needs nodal Dirichlet PDE inputs with external field references')
    def physics(model):return {k:v for k,v in model.items() if k not in ('initial','id','note')}
    first=rows[0]
    for left,right in zip(rows,rows[1:]):
        if len(right[1]['initial'])!=2*(len(left[1]['initial'])-1)+1:raise ValueError('grids must refine by exactly two in the supplied order')
        if canonical_bytes(physics(first[1]))!=canonical_bytes(physics(right[1])):raise ValueError('PDE coefficients, geometry, boundary or units differ')
        if any(not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12) for a,b in zip(left[1]['initial'],right[1]['initial'][::2])):
            raise ValueError('initial fields disagree at shared nodes')
        if {k:v for k,v in first[0]['settings'].items() if k!='timeout_s'}!={k:v for k,v in right[0]['settings'].items() if k!='timeout_s'}:
            raise ValueError('time sampling/configuration must remain fixed across grids')
        if first[2]['scale']!=right[2]['scale'] or first[2]['threshold']!=right[2]['threshold']:raise ValueError('external reference scales and tolerance must remain fixed')
    return rows


def _system_inputs(rows):
    for inp,model,reference in rows:
        if model.get('profile')!=pde_system.PROFILE or reference['profile']!='field_system_rmse_v1':
            raise ValueError('convergence needs cell-centred multi-field PDE inputs with field_system_rmse_v1 references')
    def physics(model):
        return dict(schema_version=model['schema_version'],model_type=model['model_type'],profile=model['profile'],
            units=model['units'],lengths=model['grid']['lengths'],boundary=model['boundary'],
            parameters=model['parameters'],
            fields=[{k:v for k,v in field.items() if k!='initial'} for field in model['fields']])
    first=rows[0]
    for left,right in zip(rows,rows[1:]):
        if [2*size for size in left[1]['grid']['shape']]!=right[1]['grid']['shape']:
            raise ValueError('grids must double the cell count of every axis in the supplied order')
        if canonical_bytes(physics(first[1]))!=canonical_bytes(physics(right[1])):
            raise ValueError('PDE dimension, lengths, boundary, units, field order, diffusion, reactions or parameters differ')
        if {k:v for k,v in first[0]['settings'].items() if k!='timeout_s'}!={k:v for k,v in right[0]['settings'].items() if k!='timeout_s'}:
            raise ValueError('time sampling/configuration must remain fixed across grids')
        if first[2]['scales']!=right[2]['scales'] or first[2]['threshold']!=right[2]['threshold']:
            raise ValueError('external reference scales and tolerance must remain fixed')
    for inp,model,reference in rows:_bind_system_reference(model,inp['settings'],reference)
    return rows


def _bind_system_reference(model,settings,reference):
    """Freeze the per-grid binding between one input and its own reference."""
    shape=model['grid']['shape'];lengths=model['grid']['lengths']
    if not np.array_equal(np.asarray(reference['times'],dtype=float),np.linspace(0.,settings['duration'],settings['samples'])):
        raise ValueError('reference times do not match the frozen time sampling of the input')
    names=('x','y')[:len(shape)]
    if set(reference['axes'])!=set(names):raise ValueError('reference axes do not match the model dimension')
    for name,size,length in zip(names,shape,lengths):
        if not np.array_equal(np.asarray(reference['axes'][name],dtype=float),(np.arange(size)+0.5)*(length/size)):
            raise ValueError('reference axes do not match the frozen cell-centred grid of the input')
    if reference['field_ids']!=[field['id'] for field in model['fields']]:
        raise ValueError('reference field identifiers and order do not match the model fields')
    for index,field in enumerate(model['fields']):
        if not np.array_equal(np.asarray(reference['values'][0][index],dtype=float),np.asarray(field['initial'],dtype=float)):
            raise ValueError("each grid's initial field must equal its own frozen reference time-zero field; a common continuum initial is not certified")


def prepare(store,input_refs,*,min_order=1.8,max_order=2.2):
    c.number(min_order,.1,10.,'min_order');c.number(max_order,min_order,10.,'max_order')
    version,rows=_inputs(store,input_refs)
    if any(typed_models.status(store,ref)['state']!='ready' for ref in input_refs):raise ValueError('freeze convergence protocol before any selected input executes')
    if version==1:
        return store.put(PROTOCOL,dict(version=1,input_refs=input_refs,min_order=min_order,max_order=max_order,
            reference_scope=REFERENCE_SCOPE,scientific_support='unknown'),refs=tuple(input_refs))
    return store.put(PROTOCOL,dict(version=2,input_refs=input_refs,min_order=min_order,max_order=max_order,
        model_profile=pde_system.PROFILE,initial_condition_scope=SYSTEM_INITIAL_SCOPE,
        reference_scope=REFERENCE_SCOPE,scientific_support='unknown'),refs=tuple(input_refs))


def evaluate(store,protocol_ref):
    obj=store.get(protocol_ref);spec=obj['payload']
    if obj['kind']!=PROTOCOL or not isinstance(spec,dict) or type(spec.get('version')) is not int:
        raise ValueError('invalid convergence protocol binding')
    if spec['version']==1:return _evaluate_legacy(store,obj,spec)
    if spec['version']==2:return _evaluate_system(store,obj,spec)
    raise ValueError('invalid convergence protocol binding')


def _evaluate_legacy(store,obj,spec):
    c.fields(spec,{'version','input_refs','min_order','max_order','reference_scope','scientific_support'})
    if type(spec['version']) is not int or spec['version']!=1 or set(obj['refs'])!=set(spec['input_refs']):raise ValueError('invalid convergence protocol binding')
    if spec['scientific_support']!='unknown' or spec['reference_scope']!=REFERENCE_SCOPE:raise ValueError('convergence protocol cannot certify scientific support')
    c.number(spec['min_order'],.1,10.);c.number(spec['max_order'],spec['min_order'],10.)
    version,rows=_inputs(store,spec['input_refs'])
    if version!=1:raise ValueError('invalid convergence protocol binding')
    errors=[];comparisons=[];runs=[]
    for ref,(inp,model,reference) in zip(spec['input_refs'],rows):
        status=typed_models.status(store,ref)
        if status['state']!='returned':return dict(status='not_run',reason='All three confirmed runs are required; unknown work is never repeated.',scientific_support='unknown')
        run=status['run_ref'];verified=typed_models.summary(store,run);runs.append(run)
        if verified['status']!='succeeded' or any(x['status']!='pass' for x in verified['checks']):
            return dict(status='fail',reason='Numerical checks failed.',scientific_support='unknown')
        _,_,out=typed_models.result(store,run);data=out['data']
        if data['times']!=reference['times'] or data['x']!=reference['x']:return dict(status='fail',reason='External field grid mismatch.',scientific_support='unknown')
        actual=np.asarray(data['values'],dtype=float);expected=np.asarray(reference['values'],dtype=float)
        if actual.shape!=expected.shape:raise ValueError('external field shape mismatch')
        error=float(np.sqrt(np.mean(((actual-expected)/reference['scale'])**2)))
        if not math.isfinite(error):return dict(status='fail',reason='Nonfinite reference discrepancy.',scientific_support='unknown')
        errors.append(error);comparisons.append(verified['comparison']['status'])
    orders=[math.log(a/b,2) if a>0 and b>0 else None for a,b in zip(errors,errors[1:])]
    convergence=('unknown' if any(v is None for v in orders) else
        'pass' if all(spec['min_order']<=v<=spec['max_order'] for v in orders) else 'fail')
    return dict(status='pass' if convergence=='pass' and all(v=='pass' for v in comparisons) else 'fail' if convergence!='unknown' else 'unknown',
        convergence_status=convergence,scaled_rmse=errors,observed_orders=orders,reference_comparisons=comparisons,run_refs=runs,
        scientific_support='unknown',limitations=['Initial samples agree only at shared nodes; continuum equivalence is not proven.',
            'Near-zero/exact errors make observed order undefined. Field agreement and spatial convergence do not certify biology.'])


def _system_coordinates(data,reference):
    """Exact frozen-coordinate check of returned data against one reference."""
    if not isinstance(data,dict) or data.get('field_ids')!=reference['field_ids']:return False
    times=pde_system._numeric_array(data.get('times'),(len(reference['times']),))
    if times is None or not np.array_equal(times,np.asarray(reference['times'],dtype=float)):return False
    axes=data.get('axes')
    if not isinstance(axes,dict) or set(axes)!=set(reference['axes']):return False
    for name,axis in reference['axes'].items():
        observed=pde_system._numeric_array(axes.get(name),(len(axis),))
        if observed is None or not np.array_equal(observed,np.asarray(axis,dtype=float)):return False
    return True


def _order(coarse,fine):
    if not(coarse>0 and fine>0):return None
    value=math.log2(coarse)-math.log2(fine)
    return value if math.isfinite(value) else None


def _evaluate_system(store,obj,spec):
    c.fields(spec,{'version','input_refs','min_order','max_order','reference_scope','scientific_support',
                   'model_profile','initial_condition_scope'})
    if (set(obj['refs'])!=set(spec['input_refs']) or spec['model_profile']!=pde_system.PROFILE
            or spec['initial_condition_scope']!=SYSTEM_INITIAL_SCOPE):
        raise ValueError('invalid convergence protocol binding')
    if spec['scientific_support']!='unknown' or spec['reference_scope']!=REFERENCE_SCOPE:raise ValueError('convergence protocol cannot certify scientific support')
    c.number(spec['min_order'],.1,10.);c.number(spec['max_order'],spec['min_order'],10.)
    version,rows=_inputs(store,spec['input_refs'])
    if version!=2:raise ValueError('invalid convergence protocol binding')
    runs=[];comparisons=[];per_field={};field_ids=None
    for ref,(inp,model,reference) in zip(spec['input_refs'],rows):
        status=typed_models.status(store,ref)
        if status['state']!='returned':return dict(status='not_run',reason='All three confirmed runs are required; unknown work is never repeated.',scientific_support='unknown')
        run=status['run_ref'];verified=typed_models.summary(store,run);runs.append(run)
        if verified['status']!='succeeded' or any(x['status']!='pass' for x in verified['checks']):
            return dict(status='fail',reason='Numerical checks failed.',scientific_support='unknown')
        _,_,out=typed_models.result(store,run);data=out['data']
        if field_ids is None:field_ids=list(reference['field_ids'])
        elif field_ids!=list(reference['field_ids']):raise ValueError('external field identity mismatch')
        if not _system_coordinates(data,reference):return dict(status='fail',reason='External field grid mismatch.',scientific_support='unknown')
        shape=(len(reference['times']),len(reference['field_ids']),
               *[len(reference['axes'][name]) for name in ('x','y') if name in reference['axes']])
        actual=pde_system._numeric_array(data.get('values'),shape)
        expected=np.asarray(reference['values'],dtype=float)
        if actual is None or expected.shape!=shape:raise ValueError('external field shape mismatch')
        if not np.isfinite(actual).all():return dict(status='fail',reason='Nonfinite reference discrepancy.',scientific_support='unknown')
        for index,field_id in enumerate(field_ids):
            with np.errstate(over='ignore',invalid='ignore'):
                value=float(np.sqrt(np.mean(((actual[:,index]-expected[:,index])/reference['scales'][index])**2)))
            if not math.isfinite(value):return dict(status='fail',reason='Nonfinite reference discrepancy.',scientific_support='unknown')
            per_field.setdefault(field_id,[]).append(value)
        comparisons.append(verified['comparison']['status'])
    per_orders={};per_status={}
    for field_id,errors in per_field.items():
        orders=[_order(a,b) for a,b in zip(errors,errors[1:])]
        per_orders[field_id]=orders
        per_status[field_id]=('fail' if any(v is not None and not spec['min_order']<=v<=spec['max_order'] for v in orders)
                              else 'unknown' if any(v is None for v in orders) else 'pass')
    convergence_status=('fail' if any(v=='fail' for v in per_status.values())
                        else 'unknown' if any(v!='pass' for v in per_status.values()) else 'pass')
    aggregate=[]
    for grid in range(3):
        with np.errstate(over='ignore',invalid='ignore'):
            value=float(np.sqrt(np.mean([per_field[field_id][grid]**2 for field_id in field_ids])))
        aggregate.append(value if math.isfinite(value) else None)
    observed=[_order(a,b) if a is not None and b is not None else None for a,b in zip(aggregate,aggregate[1:])]
    status=('fail' if convergence_status=='fail' or any(v!='pass' for v in comparisons)
            else 'unknown' if convergence_status=='unknown' else 'pass')
    return dict(status=status,convergence_status=convergence_status,field_order=field_ids,
        per_field_scaled_rmse=per_field,per_field_observed_orders=per_orders,per_field_status=per_status,
        scaled_rmse=aggregate,observed_orders=observed,reference_comparisons=comparisons,run_refs=runs,
        scientific_support='unknown',
        limitations=['Cell-centred grids share no nodes; each initial field is checked only against its own frozen reference at t=0.',
            'A common continuum initial field is not certified, and point-sampled initial values are not required to be cell averages.',
            'Near-zero/exact errors make the observed order undefined. Field agreement and spatial convergence do not certify biology.'])


def command(args):
    import json
    import sys
    from .storage import ArtifactStore
    from .recovery import open_readonly_store
    try:
        store=ArtifactStore(args.store) if args.command=='pde-convergence-prepare' else open_readonly_store(args.store)
        out=(dict(protocol_ref=prepare(store,args.input_refs,min_order=args.min_order,max_order=args.max_order))
            if args.command=='pde-convergence-prepare' else evaluate(store,args.ref))
        print(json.dumps(out,ensure_ascii=False,allow_nan=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('PDE convergence operation failed: '+str(exc),file=sys.stderr);return 1


def register(sub,add_store):
    parser=sub.add_parser('pde-convergence-prepare',help='freeze three nested PDE inputs and external field references before execution')
    add_store(parser);parser.add_argument('input_refs',nargs=3);parser.add_argument('--min-order',type=float,default=1.8);parser.add_argument('--max-order',type=float,default=2.2);parser.set_defaults(func=command)
    parser=sub.add_parser('pde-convergence-show',help='derive observed spatial orders from three confirmed numerical results')
    add_store(parser);parser.add_argument('ref');parser.set_defaults(func=command)
