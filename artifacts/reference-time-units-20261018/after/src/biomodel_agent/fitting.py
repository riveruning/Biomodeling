"""Bounded fitting of the declared rate-rule SBML profile; holdout stays outside."""
from __future__ import annotations
import hashlib
import json
import math
import multiprocessing as mp
import queue
import time

import libsbml
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

from . import expressions, modeling, time_units
from .domain.contracts import ExperimentSpec, ObservationSpec
from .domain.serialization import to_dict, from_dict


class Unsupported(ValueError):
    pass


def _observations(items):
    items = [to_dict(o) if isinstance(o, ObservationSpec) else dict(o) for o in items]
    if not items or len({o['id'] for o in items}) != len(items):
        raise ValueError('observations must be nonempty with unique IDs')
    return items


def _experiment(value):
    return value if isinstance(value, ExperimentSpec) else from_dict(ExperimentSpec, value)


class _Compiler:
    def __init__(self, artifact):
        self.time_scale = time_units.model_time_scale(artifact.sbml)
        report = modeling.validate_artifact(artifact)
        if not report.can_execute:
            raise Unsupported('; '.join(c.message for c in report.checks if c.status == 'fail'))
        m = libsbml.readSBMLFromString(artifact.sbml).getModel()
        if m.getNumReactions() or m.getNumSpecies() or m.getNumFunctionDefinitions() or m.getNumInitialAssignments():
            raise Unsupported('rate_rule_fitting_v1 excludes reactions/species/functions/initial assignments')
        rules = list(m.getListOfRules())
        if not rules or any(not r.isRate() for r in rules):
            raise Unsupported('fitting requires explicit parameter-state rate rules only')
        self.constants = {p.getId():p.getValue() for p in m.getListOfParameters() if p.getConstant()}
        self.initial = {p.getId():p.getValue() for p in m.getListOfParameters() if not p.getConstant()}
        self.compartments = {c.getId():c.getSize() for c in m.getListOfCompartments()}
        self.states = sorted(self.initial)
        by_target = {r.getVariable():r.getMath() for r in rules}
        if set(by_target) != set(self.states):
            raise Unsupported('every nonconstant parameter must have one rate rule')
        allowed = set(self.states)|set(self.constants)|set(self.compartments)|{'time'}
        self.rhs = [expressions.parse_expression(libsbml.formulaToL3String(by_target[s]), allowed) for s in self.states]

    def predict(self, experiment, observations, parameters):
        spec = _experiment(experiment)
        clock = time_units.time_unit_check(spec.time_unit, self.time_scale)
        if clock.status == 'fail':
            raise ValueError(clock.message)
        params = dict(self.constants)
        for name, value in parameters.items():
            if name not in self.constants or not math.isfinite(float(value)):
                raise ValueError(f'invalid fitted constant parameter: {name}')
            params[name] = float(value)
        for name,value in spec.parameter_overrides.items():
            if name not in self.constants or name in parameters:
                raise ValueError(f'unknown or conflicting condition override: {name}')
            params[name] = value
        state = dict(self.initial)
        for name,value in spec.initial_overrides.items():
            if name not in state:
                raise ValueError(f'unknown initial state: {name}')
            state[name] = value
        t0,tlast = spec.times[0],spec.times[-1]
        segments = {s.time:dict(s.values) for s in spec.segments}
        for t,values in segments.items():
            if t < t0 or t > tlast or any(k not in self.constants or k in parameters for k in values):
                raise ValueError('invalid segment or conflict with fitted parameter')
        observations = _observations(observations)
        allowed = set(state)|set(params)|set(self.compartments)|{'time'}
        obs_trees = {o['id']:expressions.parse_expression(o['expression'],allowed) for o in observations}
        columns = {s:[] for s in self.states}
        observed = {o['id']:[] for o in observations}
        rows = {}
        y = np.array([state[s] for s in self.states],dtype=float)
        boundaries = sorted({t0,tlast}|set(segments))
        def env(t,y):
            return {**params,**self.compartments,**dict(zip(self.states,map(float,y))),'time':float(t)}
        def rhs(t,y):
            e = env(t,y)
            return [expressions._eval(tree.body,e) for tree in self.rhs]
        for index,left in enumerate(boundaries):
            params.update(segments.get(left,{}))
            rows[left] = (y.copy(),dict(params))
            if index+1 == len(boundaries):
                break
            right = boundaries[index+1]
            samples = [t for t in spec.times if left < t <= right]
            targets = sorted(set(samples)|{right})
            sol = solve_ivp(rhs,(left,right),y,t_eval=targets,method='DOP853',rtol=1e-9,atol=1e-11)
            if not sol.success or not np.isfinite(sol.y).all():
                raise ValueError('integration failed: '+sol.message)
            for j,t in enumerate(targets):
                rows[t] = (sol.y[:,j],dict(params))
            y = sol.y[:,-1]
        for t in spec.times:
            yy,pp = rows[t]
            e = {**pp,**self.compartments,**dict(zip(self.states,map(float,yy))),'time':t}
            for s,v in zip(self.states,yy):
                columns[s].append(float(v))
            for name,tree in obs_trees.items():
                v = float(expressions._eval(tree.body,e))
                if not math.isfinite(v):
                    raise ValueError('nonfinite observable')
                observed[name].append(v)
        return {'status':'succeeded','times':list(spec.times),'time_unit':spec.time_unit,'states':columns,'observables':observed,'parameters':dict(parameters)}


def predict(artifact, experiment, observations, parameters):
    return _Compiler(artifact).predict(experiment,observations,parameters)


def _prepare(artifact, training, observations, bounds):
    compiler = _Compiler(artifact)
    obs = _observations(observations)
    ids = {o['id'] for o in obs}
    if not bounds or not isinstance(bounds,dict):
        raise ValueError('nonempty explicit bounds required')
    for name,pair in bounds.items():
        if name not in compiler.constants or len(pair) != 2:
            raise ValueError('bounds must target constant parameters')
        lo,hi = map(float,pair)
        if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
            raise ValueError('finite lower < upper bounds required')
    if not training or len({r['id'] for r in training}) != len(training):
        raise ValueError('nonempty unique training conditions required')
    rows = []
    for row in training:
        if row.get('role') != 'training':
            raise ValueError('only training conditions may enter the optimizer')
        spec = _experiment(row['experiment'])
        if set(row['values']) != ids or set(row['sigma']) != ids:
            raise ValueError('dataset columns must equal observation IDs')
        for oid in ids:
            values = row['values'][oid]
            if len(values) != len(spec.times) or not all(math.isfinite(float(v)) for v in values):
                raise ValueError('finite data columns must match time grid')
            sigma = float(row['sigma'][oid])
            if not math.isfinite(sigma) or sigma <= 0:
                raise ValueError('sigma must be positive and finite')
        if set(spec.parameter_overrides)&set(bounds) or any(set(s.values)&set(bounds) for s in spec.segments):
            raise ValueError('condition overrides conflict with fitted parameters')
        # Validate mappings and protocols before spawning.
        compiler.predict(spec,obs,{k:(float(v[0])+float(v[1]))/2 for k,v in bounds.items()})
        rows.append({**row,'experiment':to_dict(spec)})
    return compiler,rows,obs


def _fit_worker(payload,out):
    try:
        artifact,rows,observations,bounds,starts,seed,max_nfev = payload
        compiler = _Compiler(artifact)
        names = sorted(bounds)
        lo = np.array([bounds[n][0] for n in names],dtype=float)
        hi = np.array([bounds[n][1] for n in names],dtype=float)
        size = sum(len(r['experiment']['times'])*len(observations) for r in rows)
        def residual(theta):
            params = dict(zip(names,map(float,theta)))
            result = []
            for r in rows:
                p = compiler.predict(r['experiment'],observations,params)['observables']
                for o in observations:
                    oid = o['id']
                    result.extend((np.array(p[oid])-np.array(r['values'][oid]))/r['sigma'][oid])
            return np.array(result)
        rng = np.random.default_rng(seed)
        records = []
        successful = []
        for index in range(starts):
            initial = (lo+hi)/2 if index == 0 else rng.uniform(lo,hi)
            failures = [0]
            def target(theta):
                try:
                    return residual(theta)
                except (ValueError,ArithmeticError):
                    failures[0] += 1
                    return np.full(size,1e6)
            rec = {'start':index,'initial':dict(zip(names,map(float,initial))),'status':'failed'}
            try:
                opt = least_squares(target,initial,bounds=(lo,hi),max_nfev=max_nfev)
                valid = residual(opt.x)  # penalized or invalid endpoint never succeeds
                rec.update(final=dict(zip(names,map(float,opt.x))),cost=float(valid@valid/2),nfev=int(opt.nfev),integration_failures=failures[0],error=None)
                if opt.success and np.isfinite(valid).all():
                    rec['status'] = 'succeeded'
                    successful.append((rec,opt))
                else:
                    rec['error'] = str(opt.message)
            except Exception as exc:
                rec['error'] = f'{type(exc).__name__}: {exc}'
            records.append(rec)
        if not successful:
            out.put({'status':'failed','best':None,'starts':records,'diagnostics':['all starts failed']})
            return
        best,opt = min(successful,key=lambda pair:pair[0]['cost'])
        sv = np.linalg.svd(opt.jac,compute_uv=False)
        rank = int(np.linalg.matrix_rank(opt.jac))
        cond = float(sv[0]/sv[-1]) if rank == len(names) and sv[-1]>0 else None
        predictions = {r['id']:compiler.predict(r['experiment'],observations,best['final']) for r in rows}
        spread = {n:[min(r['final'][n] for r,_ in successful),max(r['final'][n] for r,_ in successful)] for n in names}
        out.put({'status':'succeeded','best':{'parameters':best['final'],'cost':best['cost'],'start':best['start'],'nfev':best['nfev']},'starts':records,'predictions':predictions,'uncertainty':{'method':'local_sensitivity','rank':rank,'parameter_count':len(names),'condition_number':cond,'global_identifiability':'unknown','confidence_intervals':'not_run','endpoint_spread':spread,'limitations':['endpoint ranges and bounds are not confidence intervals','local rank does not prove global or structural identifiability']},'diagnostics':[]})
    except Exception as exc:
        out.put({'status':'failed','best':None,'starts':[],'diagnostics':[f'{type(exc).__name__}: {exc}']})


def fit_parameters(artifact,training,observations,bounds,starts=3,seed=17,max_nfev=100,timeout_s=90):
    started = time.monotonic()
    if isinstance(starts,bool) or not isinstance(starts,int) or not 1<=starts<=8:
        raise ValueError('starts must be an integer between 1 and 8')
    if isinstance(max_nfev,bool) or not isinstance(max_nfev,int) or not 1<=max_nfev<=1000:
        raise ValueError('max_nfev must be between 1 and 1000')
    if isinstance(seed,bool) or not isinstance(seed,int) or not math.isfinite(timeout_s) or timeout_s<=0:
        raise ValueError('finite positive timeout and integer seed required')
    try:
        _,rows,obs = _prepare(artifact,training,observations,bounds)
    except Unsupported as exc:
        return {'status':'unsupported','best':None,'starts':[],'diagnostics':[str(exc)]}
    ctx = mp.get_context('spawn')
    out = ctx.Queue()
    process = ctx.Process(target=_fit_worker,args=((artifact,rows,obs,bounds,starts,seed,max_nfev),out),daemon=True)
    process.start()
    try:
        try:
            result = out.get(timeout=timeout_s)
        except queue.Empty:
            result = {'status':'timed_out','best':None,'starts':[],'diagnostics':['fit budget exhausted']}
    finally:
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        out.close()
        out.join_thread()
    digest = lambda obj: hashlib.sha256(json.dumps(obj,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    result.update(training_data_hash=digest(rows),noise_protocol_hash=digest([r['sigma'] for r in rows]),wall_time_s=time.monotonic()-started,backend='SciPy/DOP853/least_squares',seed=seed,
                  optimizer={'bounds':{k:list(map(float,v)) for k,v in bounds.items()},'starts':starts,'seed':seed,'max_nfev':max_nfev,'timeout_s':timeout_s,'method':'least_squares/trf','rtol':1e-9,'atol':1e-11,'profile':'rate_rule_fitting_v1','noise':'independent Gaussian with declared known sigma'})
    return result
