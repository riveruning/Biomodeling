"""Bounded steady-state LP models, immutable execution, and numeric checks."""
from __future__ import annotations
import json
import math
import multiprocessing as mp
from pathlib import Path
import re
import time
import uuid

from .storage import canonical_bytes

MAX_BYTES = 8_000_000
MAX_METABOLITES = 1000
MAX_REACTIONS = 2000
MAX_NONZERO = 100_000
TOLERANCE = 1e-7
ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')


def number(value, label, *, limit=1e12):
    if type(value) not in (int, float) or abs(value)>limit or not math.isfinite(value):
        raise ValueError(label+' must be a finite bounded number, not a bool')
    return float(value)


def identifier(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError('invalid FBA identifier')
    return value


def _fields(value, required):
    if not isinstance(value,dict) or set(value)!=set(required):raise ValueError('FBA fields must be exactly '+','.join(required))


def validate(model):
    _fields(model,('schema_version','kind','id','formulation','flux_unit','metabolites','reactions','objective'))
    if type(model['schema_version']) is not int or model['schema_version']!=1 or model['kind']!='fba' or model['formulation']!='steady_state':
        raise ValueError('only fba v1 steady_state is supported')
    identifier(model['id'])
    if not isinstance(model['flux_unit'],str) or not model['flux_unit'].strip() or len(model['flux_unit'])>128:
        raise ValueError('declare one flux_unit label, or explicitly unknown')
    if not isinstance(model['metabolites'],list) or not 1<=len(model['metabolites'])<=MAX_METABOLITES:
        raise ValueError('FBA metabolite count exceeds supported limits')
    if not isinstance(model['reactions'],list) or not 1<=len(model['reactions'])<=MAX_REACTIONS:
        raise ValueError('FBA reaction count exceeds supported limits')
    metabolites=[];ids=set()
    for item in model['metabolites']:
        _fields(item,('id','boundary_condition','compartment'))
        name=identifier(item['id'])
        if name in ids or type(item['boundary_condition']) is not bool:raise ValueError('duplicate metabolite or non-boolean boundary_condition')
        identifier(item['compartment']);ids.add(name);metabolites.append(dict(item))
    reactions=[];rids=set();nonzero=0
    for item in model['reactions']:
        _fields(item,('id','stoichiometry','lower_bound','upper_bound'))
        name=identifier(item['id'])
        if name in rids:raise ValueError('duplicate FBA reaction')
        rids.add(name)
        stoich=item['stoichiometry']
        if not isinstance(stoich,dict) or not stoich or set(stoich)-ids:raise ValueError('stoichiometry must name known metabolites')
        stoich={s:number(v,'stoichiometry',limit=1e9) for s,v in stoich.items()}
        if any(v==0 for v in stoich.values()):raise ValueError('explicit zero stoichiometry is unsupported')
        nonzero+=len(stoich)
        lower=number(item['lower_bound'],'lower_bound') if item['lower_bound'] is not None else None
        upper=number(item['upper_bound'],'upper_bound') if item['upper_bound'] is not None else None
        if lower is not None and upper is not None and lower>upper:raise ValueError('lower_bound exceeds upper_bound')
        reactions.append(dict(id=name,stoichiometry=stoich,lower_bound=lower,upper_bound=upper))
    if nonzero>MAX_NONZERO:raise ValueError('FBA nonzero budget exceeded')
    _fields(model['objective'],('sense','coefficients'))
    obj=model['objective']
    if obj['sense'] not in ('maximize','minimize') or not isinstance(obj['coefficients'],dict) or not obj['coefficients'] or set(obj['coefficients'])-rids:
        raise ValueError('objective requires a known sense and reaction coefficients')
    coefficients={r:number(c,'objective coefficient',limit=1e9) for r,c in obj['coefficients'].items()}
    if not any(coefficients.values()):raise ValueError('objective cannot be identically zero')
    return dict(schema_version=1,kind='fba',id=model['id'],formulation='steady_state',flux_unit=model['flux_unit'],
                metabolites=metabolites,reactions=reactions,objective=dict(sense=obj['sense'],coefficients=coefficients))


def config(value=None):
    value={} if value is None else value
    if not isinstance(value,dict) or set(value)-{'method','timeout_s','reference'}:raise ValueError('unsupported FBA solver options')
    method=value.get('method','highs-ds')
    if method not in ('highs-ds','highs-ipm'):raise ValueError('unsupported FBA method')
    timeout=number(value.get('timeout_s',15.),'FBA timeout',limit=60.)
    if timeout<.1:raise ValueError('FBA timeout must be in 0.1..60 seconds')
    reference=value.get('reference')
    if reference is not None:
        _fields(reference,('objective','source'))
        expected=number(reference['objective'],'reference objective')
        if not isinstance(reference['source'],str) or not reference['source'].strip() or len(reference['source'])>1000:
            raise ValueError('reference source must be explicit')
        reference=dict(objective=expected,source=reference['source'])
    return dict(method=method,timeout_s=timeout,reference=reference)


def _no_duplicates(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate FBA JSON key')
        result[k]=v
    return result


def import_model(store,path,*,format='json'):
    if Path(path).stat().st_size>MAX_BYTES:raise ValueError('FBA input size exceeds supported budget')
    data=Path(path).read_bytes()
    if not data or len(data)>MAX_BYTES:raise ValueError('FBA input size outside supported budget')
    if format=='json':
        model=validate(json.loads(data,object_pairs_hook=_no_duplicates))
        metadata=dict(adapter='native_fba_v1',warnings=[])
    elif format=='fbc':
        from .fba_sbml import parse
        model,metadata=parse(data)
    else:raise ValueError('FBA format must be json or fbc')
    blob=store.put_blob(data)
    return store.put('fba_model',dict(version=1,model=model,source_blob_ref=blob,format=format,adapter=metadata,
        scientific_support='unknown',units_status='unknown' if model['flux_unit']=='unknown' else 'declared_only',
        limitations=['Steady-state linear flux constraints only; no dynamics, gene edits, FVA or biological certification.']),refs=(blob,))


def load(store,ref):
    obj=store.get(ref)
    if obj['kind']!='fba_model' or obj['payload']['version']!=1:raise ValueError('model_ref must name a supported fba_model; ODE is not FBA')
    payload=obj['payload']
    if payload['source_blob_ref'] not in obj['refs']:raise ValueError('FBA source provenance is missing')
    original=store.get_blob(payload['source_blob_ref'])
    if not original or len(original)>MAX_BYTES:raise ValueError('FBA source size is invalid')
    if payload['format']=='json':
        derived=validate(json.loads(original,object_pairs_hook=_no_duplicates))
    elif payload['format']=='fbc':
        from .fba_sbml import parse
        derived,_=parse(original)
    else:raise ValueError('unknown FBA source format')
    normalized=validate(payload['model'])
    if canonical_bytes(derived)!=canonical_bytes(normalized):raise ValueError('FBA model differs from recomputed original source')
    return normalized


def matrices(model):
    import numpy as np
    from scipy.sparse import coo_matrix
    balanced=[m['id'] for m in model['metabolites'] if not m['boundary_condition']]
    rows={s:i for i,s in enumerate(balanced)}
    rr=[];cc=[];vv=[]
    for j,r in enumerate(model['reactions']):
        for s,v in r['stoichiometry'].items():
            if s in rows:rr.append(rows[s]);cc.append(j);vv.append(v)
    S=coo_matrix((vv,(rr,cc)),shape=(len(rows),len(model['reactions']))).tocsr()
    c=np.array([model['objective']['coefficients'].get(r['id'],0.) for r in model['reactions']])
    bounds=[(r['lower_bound'],r['upper_bound']) for r in model['reactions']]
    return S,c,bounds


def _worker(model,settings,connection):
    try:
        import scipy
        import numpy as np
        from scipy.optimize import linprog
        S,c,bounds=matrices(model)
        sign=-1. if model['objective']['sense']=='maximize' else 1.
        solved=linprog(sign*c,A_eq=S if S.shape[0] else None,b_eq=np.zeros(S.shape[0]) if S.shape[0] else None,
                       bounds=bounds,method=settings['method'],options={'time_limit':settings['timeout_s'],
                       'primal_feasibility_tolerance':1e-8,'dual_feasibility_tolerance':1e-8})
        status={0:'optimal',1:'limit_reached',2:'infeasible',3:'unbounded',4:'solver_error'}.get(solved.status,'solver_error')
        x=solved.x.tolist() if status=='optimal' and solved.x is not None else None
        connection.send(dict(status=status,solver_status=int(solved.status),message=str(solved.message)[:600],
                             fluxes=x,solver_objective=float(sign*solved.fun) if status=='optimal' and solved.fun is not None else None,
                             scipy_version=scipy.__version__,
                             certificate=dict(equality=solved.eqlin.marginals.tolist(),lower=solved.lower.marginals.tolist(),upper=solved.upper.marginals.tolist())
                             if status=='optimal' else None))
    except Exception as exc:
        connection.send(dict(status='solver_error',solver_status=None,message=type(exc).__name__,fluxes=None,solver_objective=None))
    finally:connection.close()


def solve(model,settings,*,_target=None):
    """Spawn-isolated LP with a parent wall-clock limit including startup."""
    model=validate(model);settings=config(settings)
    start=time.monotonic();ctx=mp.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_target or _worker,args=(model,settings,sender),daemon=True)
    try:
        process.start();sender.close()
        if receiver.poll(max(0.,settings['timeout_s']-(time.monotonic()-start))):
            try:result=receiver.recv()
            except EOFError:result=dict(status='solver_error',message='worker exited without a result',fluxes=None,solver_objective=None)
        else:result=dict(status='time_limit',message='parent wall-clock deadline reached',fluxes=None,solver_objective=None)
    finally:
        sender.close();receiver.close()
        if process.pid is not None:
            process.join(.05)
            if process.is_alive():process.terminate();process.join(.2)
            if process.is_alive():process.kill();process.join(.2)
    result['elapsed_s']=time.monotonic()-start
    return result


def checks(model,settings,result):
    """Recompute feasibility and reference checks; never trust completion flags."""
    import numpy as np
    outcome=dict(status=result.get('status'),objective=None,fluxes=None,checks=[],comparison=dict(status='not_run'),
                 completion_eligible=False,scientific_support='unknown')
    if result.get('status')!='optimal':return outcome
    x=result.get('fluxes');S,c,bounds=matrices(model)
    if (not isinstance(x,list) or len(x)!=len(model['reactions']) or
        any(type(v) not in (int,float) or not math.isfinite(v) for v in x)):
        outcome.update(status='numerical_failure',checks=[dict(code='finite_fluxes',status='fail')]);return outcome
    x=np.array(x,dtype=float);balance=np.asarray(S@x)
    scales=1.+np.asarray(abs(S)@abs(x))
    if not np.isfinite(balance).all() or not np.isfinite(scales).all():
        outcome.update(status='numerical_failure',checks=[dict(code='finite_residuals',status='fail')]);return outcome
    balance_abs=float(max(abs(balance),default=0.));balance_scaled=float(max(abs(balance)/scales,default=0.))
    violation=0.;absolute_violation=0.
    for v,(lower,upper) in zip(x,bounds):
        for bound,sign in ((lower,-1.),(upper,1.)):
            if bound is not None:
                absolute_violation=max(absolute_violation,sign*(float(v)-bound))
                violation=max(violation,sign*(float(v)-bound)/(1.+max(abs(bound),abs(float(v)))))
    objective=float(c@x)
    reported=result.get('solver_objective')
    objective_ok=type(reported) in (int,float) and math.isfinite(reported) and math.isfinite(objective) and abs(reported-objective)<=TOLERANCE*(1.+abs(objective))
    numeric=[dict(code='steady_state',status='pass' if balance_scaled<=TOLERANCE and balance_abs<=TOLERANCE else 'fail',max_abs=balance_abs,max_scaled=balance_scaled,tolerance=TOLERANCE),
             dict(code='flux_bounds',status='pass' if violation<=TOLERANCE and absolute_violation<=TOLERANCE else 'fail',max_abs_violation=absolute_violation,max_scaled_violation=violation,tolerance=TOLERANCE),
             dict(code='objective_consistency',status='pass' if objective_ok else 'fail')]
    if not math.isfinite(objective):
        outcome.update(status='numerical_failure',checks=numeric);return outcome
    # Primal feasibility alone does not prove an optimum. Verify the stored
    # HiGHS dual certificate for the equivalent minimization LP as well.
    cert=result.get('certificate') or {}
    expected_lengths={'equality':S.shape[0],'lower':len(x),'upper':len(x)}
    valid_cert=type(result.get('solver_status')) is int and result['solver_status']==0
    for key,n in expected_lengths.items():
        values=cert.get(key)
        valid_cert=valid_cert and isinstance(values,list) and len(values)==n and all(type(v) in (int,float) and math.isfinite(v) for v in values)
    if valid_cert:
        y=np.array(cert['equality']);lower=np.array(cert['lower']);upper=np.array(cert['upper'])
        sign=-1. if model['objective']['sense']=='maximize' else 1.
        stationarity=sign*c-np.asarray(S.T@y)-lower-upper
        dual_terms=[];sign_error=0.
        for j,(lb,ub) in enumerate(bounds):
            sign_error=max(sign_error,-float(lower[j]),float(upper[j]))
            if lb is None:sign_error=max(sign_error,abs(float(lower[j])))
            else:dual_terms.append(lb*float(lower[j]))
            if ub is None:sign_error=max(sign_error,abs(float(upper[j])))
            else:dual_terms.append(ub*float(upper[j]))
        try:dual_objective=math.fsum(dual_terms)
        except (ValueError,OverflowError):dual_objective=float('inf')
        gap=abs(sign*objective-dual_objective)
        stationary=float(max(abs(stationarity),default=0.))
        valid_cert=all(math.isfinite(v) for v in (dual_objective,gap,stationary,sign_error))
        valid_cert=valid_cert and sign_error<=TOLERANCE and stationary<=TOLERANCE and gap<=TOLERANCE*(1.+abs(objective))
    numeric.append(dict(code='lp_optimality_certificate',status='pass' if valid_cert else 'fail'))
    comparison=dict(status='not_requested')
    if settings['reference'] is not None:
        expected=settings['reference']['objective'];error=abs(objective-expected);threshold=1e-8*(1.+abs(expected))
        comparison=dict(status='pass' if error<=threshold else 'fail',objective=expected,absolute_error=error,
                        threshold=threshold,source=settings['reference']['source'],scope='scalar_objective_only')
    ok=all(c['status']=='pass' for c in numeric)
    outcome.update(status='optimal' if ok else 'numerical_failure',objective=objective,
                   fluxes={r['id']:float(v) for r,v in zip(model['reactions'],x)},checks=numeric,comparison=comparison,
                   completion_eligible=ok and comparison['status']!='fail')
    return outcome


def run(store,model_ref,settings=None,*,replay_of=None):
    model=load(store,model_ref);settings=config(settings)
    from .application import environment_fingerprint
    input_ref=store.put('fba_input',dict(version=1,model_ref=model_ref,settings=settings,environment=environment_fingerprint(),
        feasibility_tolerance=TOLERANCE,reference_tolerance='1e-8*(1+abs(reference))'),refs=(model_ref,))
    execution=store.put('fba_execution',dict(version=1,id=str(uuid.uuid4()),phase='reserved',input_ref=input_ref),refs=(input_ref,))
    result=solve(model,settings)
    # Invalid non-finite solver output is represented as a failed diagnostic,
    # never serialized as invalid JSON and never trusted as a success.
    try:canonical_bytes(result)
    except ValueError:result=dict(status='numerical_failure',message='non-finite or malformed solver output',fluxes=None,solver_objective=None)
    verified=checks(model,settings,result)
    receipt=store.put('fba_execution_receipt',dict(version=1,phase='returned',execution_ref=execution,input_ref=input_ref,result=result),refs=(execution,input_ref))
    refs=[input_ref,execution,receipt]
    if replay_of:refs.append(replay_of)
    return store.put('fba_run',dict(version=1,input_ref=input_ref,execution_ref=execution,receipt_ref=receipt,
        model_ref=model_ref,result=result,verification=verified,replay_of=replay_of,
        limitations=['Optimality relies on HiGHS; objective comparison does not validate a unique flux distribution.',
                     'No dynamics, chemical/biological validation, gene edits or live Agent performance claim.']),refs=tuple(refs))


def summary(store,run_ref):
    obj=store.get(run_ref)
    if obj['kind']!='fba_run':raise ValueError('not an FBA run')
    p=obj['payload'];inputs=store.get(p['input_ref']);inp=inputs['payload']
    execution=store.get(p['execution_ref']);receipt=store.get(p['receipt_ref'])
    if (p.get('version')!=1 or inputs['kind']!='fba_input' or inp.get('version')!=1
        or inp['model_ref']!=p['model_ref'] or p['model_ref'] not in inputs['refs']
        or not {p['input_ref'],p['execution_ref'],p['receipt_ref']}<=set(obj['refs'])
        or execution['kind']!='fba_execution' or execution['payload'].get('phase')!='reserved'
        or execution['payload'].get('input_ref')!=p['input_ref'] or p['input_ref'] not in execution['refs']
        or receipt['kind']!='fba_execution_receipt' or receipt['payload'].get('phase')!='returned'
        or receipt['payload'].get('input_ref')!=p['input_ref'] or receipt['payload'].get('execution_ref')!=p['execution_ref']
        or not {p['input_ref'],p['execution_ref']}<=set(receipt['refs'])
        or canonical_bytes(receipt['payload']['result'])!=canonical_bytes(p['result'])
        or inp.get('feasibility_tolerance')!=TOLERANCE or inp.get('reference_tolerance')!='1e-8*(1+abs(reference))'):
        raise ValueError('FBA input/execution/receipt binding mismatch')
    return checks(load(store,p['model_ref']),config(inp['settings']),receipt['payload']['result'])


def replay(store,run_ref):
    summary(store,run_ref)
    inp=store.get(store.get(run_ref)['payload']['input_ref'])['payload']
    return run(store,inp['model_ref'],inp['settings'],replay_of=run_ref)
