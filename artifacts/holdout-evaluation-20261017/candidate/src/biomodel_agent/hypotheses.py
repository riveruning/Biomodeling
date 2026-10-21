"""B-candidate constrained hypothesis study: build, fit, holdout-compare, report.

Scope (v01-07-study-core):
- ``adaptation_candidates()`` returns three candidate :class:`ModelDraft`
  templates (feedback / feed-forward / baseline), each built from an empty
  draft -- explicit rate-rule mechanisms, not open mechanism search.
- ``run_hypothesis_study(...)`` fits every candidate on *training* conditions
  only (the parent process keeps ``holdout`` and never passes it to fitting),
  cross-checks each best fit once against libRoadRunner, evaluates frozen
  best params on holdout (no refit), compares, and stores one
  ``hypothesis_study`` artifact.

Honesty rules enforced here:
- readout ``x`` is a *signed* deviation, never declared a nonnegative
  concentration (``quantity_kind='variable'``);
- local sensitivity/Jacobian diagnostics are not global/structural
  identifiability proofs and no confidence interval is reported;
- candidate templates + linearized scenario are documented in
  ``hypothesis_refs`` as ``system_conjecture`` metadata, not a paper fact;
- a candidate whose libRoadRunner cross-check fails is not scientifically
  usable and cannot win;
- no generator truth is produced or consumed by this product code.
"""
from __future__ import annotations

import math
import platform

import numpy as np

from . import fitting, modeling, simulation
from .domain import serialization
from .domain.contracts import (
    Compartment, ExperimentSpec, ModelDraft, Parameter, RateRule, State, ObservationSpec,
)

__all__ = ["adaptation_candidates", "run_hypothesis_study"]

_OBS_ID = "response"
_OBS_EXPR = "x"
_OBS_SCALE = 1.0          # fixed, documented (no per-row scale supplied)
_XRR_TOL = 1e-3
_T = "second"
_U = "dimensionless"
_UNKNOWN = "unknown"


def _state(id: str) -> State:
    return State(id=id, initial=0.0, unit=_U, quantity_kind="variable")


def _param(id: str, value: float) -> Parameter:
    return Parameter(id=id, value=value, unit=_UNKNOWN, origin="template_prior")


def _draft(cid: str, states, params, rules) -> ModelDraft:
    conj = (f"system_conjecture: candidate template {cid}; linearized local scenario; "
            "constrained ODE draft built from empty; not open mechanism discovery; "
            "not a paper fact; d/h unit unknown (time is second, inverse-second not asserted)")
    return ModelDraft(id=cid, states=states, parameters=params,
                      compartment=Compartment(id="cell", size=1.0, unit=_U),
                      rate_rules=rules, time_unit=_T, hypothesis_refs=(conj,))


def adaptation_candidates() -> dict[str, ModelDraft]:
    """Three constrained candidate templates, built from empty (no scanning)."""
    feedback = _draft(
        "cand_feedback", (_state("x"), _state("z")),
        (_param("k", 2.0), _param("d", 1.2), _param("h", 0.2),
         _param("u", 1.0), _param("b", 1.0)),
        (RateRule("x", "k*u - d*x - b*z"), RateRule("z", "h*x")))
    feedforward = _draft(
        "cand_feedforward", (_state("x"), _state("z")),
        (_param("k", 2.0), _param("d", 1.0), _param("h", 0.2),
         _param("u", 1.0), _param("b", 1.0)),
        (RateRule("x", "k*(u - b*z) - d*x"), RateRule("z", "h*(u - z)")))
    baseline = _draft(
        "cand_baseline", (_state("x"),),
        (_param("k", 2.0), _param("d", 1.0), _param("u", 1.0), _param("b", 1.0)),
        (RateRule("x", "k*u - d*x"),))
    return {"cand_feedback": feedback, "cand_feedforward": feedforward,
            "cand_baseline": baseline}


def _default_bounds(draft: ModelDraft) -> dict[str, tuple[float, float]]:
    bounds = {"k": (0.5, 4.0), "d": (0.6, 1.8)}
    if any(r.state == "z" for r in draft.rate_rules):
        bounds["h"] = (0.1, 0.4)
    return bounds


def _condition(entry):
    if entry.get('role') not in {'training','holdout'}:
        raise ValueError('dataset role must be explicit')
    spec = entry['experiment']
    spec = spec if isinstance(spec, ExperimentSpec) else serialization.from_dict(ExperimentSpec,spec)
    return {**entry,'experiment':serialization.to_dict(spec)}


def _scores(row,predictions):
    weighted,scaled = [],[]
    for oid,values in row['values'].items():
        predicted = predictions[oid]
        sigma = float(row['sigma'][oid])
        scale = float(row.get('scales',{}).get(oid,1.))
        if len(values)!=len(predicted) or sigma<=0 or scale<=0:
            raise ValueError('invalid held-out observation shape or scale')
        for actual,predicted_value in zip(values,predicted):
            error = float(actual)-float(predicted_value)
            if not math.isfinite(error):
                raise ValueError('nonfinite held-out data or prediction')
            weighted.append((error/sigma)**2)
            scaled.append((error/scale)**2)
    if not weighted:
        raise ValueError('empty comparison')
    return math.sqrt(sum(weighted)/len(weighted)),math.sqrt(sum(scaled)/len(scaled))


def run_hypothesis_study(store,candidates,training,holdout=(),observations=(),bounds=None,
                         starts=3,seed=17,max_nfev=100,timeout_s=90,separation_threshold=.05):
    if not candidates or not training or not math.isfinite(separation_threshold) or separation_threshold<=0:
        raise ValueError('nonempty candidates/training and positive separation threshold required')
    obs = [serialization.to_dict(o) if isinstance(o,ObservationSpec) else dict(o) for o in observations] if observations else [{'id':'response','expression':'x'}]
    train = [_condition(c) for c in training]
    if any(r['role']!='training' for r in train):
        raise ValueError('optimizer may only receive training conditions')
    # Data are preserved in full; only train is sent to the fitting worker.
    data_ref = store.put('dataset',{'conditions':train,'role':'training'})
    refs = [data_ref]
    records = {}
    for name,draft in candidates.items():
        hypothesis_ref = store.put('hypothesis',{'claim_type':'system_conjecture','mechanism':name,'assumptions':list(draft.hypothesis_refs),'limitations':['candidate template, not open mechanism discovery']})
        from dataclasses import replace
        draft = replace(draft,hypothesis_refs=(hypothesis_ref,))
        draft_ref = store.put('model_draft',serialization.to_dict(draft),refs=(hypothesis_ref,))
        artifact = modeling.build_model(draft)
        model_ref = store.put('model',serialization.to_dict(artifact),refs=(draft_ref,))
        fit = fitting.fit_parameters(artifact,train,obs,(bounds or {}).get(name,_default_bounds(draft)),starts=starts,seed=seed,max_nfev=max_nfev,timeout_s=timeout_s)
        fit_ref = store.put('fitting',fit,refs=(model_ref,data_ref))
        refs.append(fit_ref)
        rec = {'artifact':artifact,'fit':fit,'model_ref':model_ref,'fit_ref':fit_ref,'xrr':{'ok':False,'scaled_max_error':None}}
        if fit['status']=='succeeded':
            try:
                parameters = fit['best']['parameters']
                spec = serialization.from_dict(ExperimentSpec,train[0]['experiment'])
                spec = replace(spec,parameter_overrides={**dict(spec.parameter_overrides),**parameters})
                rr = simulation.simulate(artifact,spec,tuple(ObservationSpec(**o) for o in obs))
                predicted = fit['predictions'][train[0]['id']]['observables']
                if rr.status!='succeeded':
                    raise ValueError(str(rr.diagnostics))
                error = max(abs(a-b) for o in obs for a,b in zip(rr.values[o['id']],predicted[o['id']]))
                rec['xrr'] = {'ok':error<=1e-3,'scaled_max_error':error,'scale':1.,'parameters':'frozen fitted values','observations':obs}
            except Exception as exc:
                rec['xrr']['error'] = f'{type(exc).__name__}: {exc}'
        cross_ref = store.put('crosscheck',rec['xrr'],refs=(fit_ref,))
        refs.append(cross_ref)
        records[name] = rec
    # Holdout is read only after all optimization and cross-checks have ended.
    hold = [_condition(c) for c in holdout]
    if any(r['role']!='holdout' for r in hold):
        raise ValueError('holdout must explicitly carry role holdout')
    hold_ref = store.put('dataset',{'conditions':hold,'role':'holdout'})
    refs.append(hold_ref)
    summary = {}
    for name,rec in records.items():
        fit = rec['fit']
        item = {'fit_status':fit['status'],'model_ref':rec['model_ref'],'fit_ref':rec['fit_ref'],'crosscheck':rec['xrr'],'parameters':fit.get('best',{}).get('parameters') if fit.get('best') else None,'training_weighted_rmse':None,'heldout_weighted_rmse':None,'heldout_scaled_rmse':None}
        if fit['status']=='succeeded' and rec['xrr']['ok']:
            item['training_weighted_rmse'] = math.sqrt(2*fit['best']['cost']/sum(len(r['experiment']['times'])*len(obs) for r in train))
            try:
                scores = [_scores(r,fitting.predict(rec['artifact'],r['experiment'],obs,fit['best']['parameters'])['observables']) for r in hold]
                if scores:
                    item['heldout_weighted_rmse'] = math.sqrt(sum(s[0]**2 for s in scores)/len(scores))
                    item['heldout_scaled_rmse'] = math.sqrt(sum(s[1]**2 for s in scores)/len(scores))
            except Exception as exc:
                item['prediction_error'] = str(exc)
        summary[name] = item
    adequate = {n:r for n,r in summary.items() if r['training_weighted_rmse'] is not None and r['training_weighted_rmse']<=3}
    ranking,winner = 'none_adequate',None
    if not hold and adequate:
        ranking = 'indistinguishable_from_training' if len(adequate)>1 else 'single_training_compatible_candidate'
    elif hold:
        supported = {n:r for n,r in adequate.items() if r['heldout_weighted_rmse'] is not None and r['heldout_weighted_rmse']<=3}
        if supported:
            ordered = sorted(supported,key=lambda n:supported[n]['heldout_scaled_rmse'])
            if len(ordered)>1 and supported[ordered[1]]['heldout_scaled_rmse']-supported[ordered[0]]['heldout_scaled_rmse']<separation_threshold:
                ranking = 'indistinguishable'
            else:
                ranking,winner = 'best_compatible_candidate',ordered[0]
    # Conditional prediction-based suggestion; never a promise about a real experiment.
    suggested = dict(train[0]['experiment'])
    suggested['parameter_overrides'] = {**suggested.get('parameter_overrides',{}),'b':0.}
    suggestion_predictions = {}
    for name,rec in records.items():
        if name in adequate:
            try:
                suggestion_predictions[name] = fitting.predict(rec['artifact'],suggested,obs,rec['fit']['best']['parameters'])['observables']
            except ValueError:
                pass
    gap = 0.
    names = list(suggestion_predictions)
    for i,name in enumerate(names):
        for other in names[i+1:]:
            gap = max(gap,max(abs(a-b) for o in obs for a,b in zip(suggestion_predictions[name][o['id']],suggestion_predictions[other][o['id']])))
    suggestion = {'intervention':'b=0','experiment':suggested,'predictions':suggestion_predictions,'max_nominal_prediction_gap':gap,'scale':1.,'is_verification_of_existing_holdout':any(r['experiment']['parameter_overrides'].get('b')==0 for r in hold),'limitation':'Conditional on candidate structures and nominal fitted values; parameter uncertainty not fully propagated.'}
    from .application import environment_fingerprint
    payload = {'status':'completed','environment':environment_fingerprint(),'candidates':summary,'comparison':{'ranking':ranking,'winner':winner,'separation_threshold':separation_threshold,'adequacy_weighted_rmse':3,'note':'Specified known independent Gaussian noise; no significance test or proof of mechanism'},'experiment_suggestion':suggestion,'protocol':{'seed':seed,'starts':starts,'max_nfev':max_nfev,'timeout_s':timeout_s,'observations':obs,'training_ref':data_ref,'holdout_ref':hold_ref},'limitations':['Synthetic, linearized candidate templates; not open mechanism discovery','Local sensitivity and bounds are not global identifiability or confidence intervals','Readout x is a signed deviation, not a concentration','d/h units unknown; not fully dimensionally verified','No holdout refitting; no biological truth claim']}
    return store.put('hypothesis_study',payload,refs=refs)
