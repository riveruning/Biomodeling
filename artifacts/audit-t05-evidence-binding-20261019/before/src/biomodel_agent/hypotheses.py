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
from dataclasses import replace

import numpy as np

from . import fitting, modeling, simulation
from .domain import serialization
from .domain.contracts import (
    Compartment, ExperimentSpec, ModelDraft, Parameter, RateRule, State, ObservationSpec, SolverConfig,
)
from .deadlines import Deadline, positive_seconds
from .storage import canonical_bytes

__all__ = ["adaptation_candidates", "run_hypothesis_study"]

_OBS_ID = "response"
_OBS_EXPR = "x"
_OBS_SCALE = 1.0          # fixed, documented (no per-row scale supplied)
_XRR_TOL = 1e-3
_T = "second"
_U = "dimensionless"
_UNKNOWN = "unknown"
DEFAULT_TOTAL_TIMEOUT_S = 300.
MAX_TOTAL_TIMEOUT_S = 600.


def _finite_score(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def study_verdict(payload):
    """Derive completion from phase/number facts, never stored success flags.

    Valid negative results and indistinguishability are complete science
    computations. Missing or failed computations do not imply a rejected model.
    """
    candidates = payload.get('candidates') or {}
    reasons, usable, timed_out = [], 0, False
    protocol = payload.get('protocol') or {}
    holdout_count = protocol.get('holdout_count')
    if type(holdout_count) is not int or holdout_count < 0:
        reasons.append('missing_frozen_holdout_count')
    if not candidates:
        reasons.append('no_candidates_evaluated')
    for name, item in candidates.items():
        phases = item.get('phase_status') or {}
        cross = item.get('crosscheck') or {}
        valid = (item.get('fit_status') == 'succeeded' and cross.get('ok') is True
                 and _finite_score(cross.get('scaled_max_error')) and cross['scaled_max_error'] <= _XRR_TOL
                 and _finite_score(item.get('training_weighted_rmse')))
        usable += int(valid)
        if not valid:
            reasons.append(f'{name}:fit_crosscheck_or_training_unavailable')
        for phase in ('build', 'fit', 'crosscheck', 'training_score', 'holdout', 'suggestion'):
            state = phases.get(phase)
            allowed = ('succeeded', 'not_applicable') if phase in ('holdout', 'suggestion') else ('succeeded',)
            if state not in allowed:
                reasons.append(f'{name}:{phase}:{state or "unknown"}')
            timed_out |= state == 'timed_out'
        if holdout_count:
            if (phases.get('holdout') != 'succeeded' or not _finite_score(item.get('heldout_weighted_rmse'))
                    or not _finite_score(item.get('heldout_scaled_rmse'))):
                reasons.append(f'{name}:heldout_result_unavailable')
        if valid and item['training_weighted_rmse'] <= 3 and phases.get('suggestion') != 'succeeded':
            reasons.append(f'{name}:intervention_prediction_unavailable')
    deadline = payload.get('deadline') or {}
    if not _finite_score(deadline.get('elapsed_s')) or not _finite_score(deadline.get('total_timeout_s')):
        reasons.append('missing_deadline_evidence')
    elif deadline['elapsed_s'] >= deadline['total_timeout_s'] or deadline.get('exhausted') is True:
        reasons.append('total_deadline_exhausted')
        timed_out = True
    phase_records = deadline.get('phases', [])
    if not phase_records or not any(p.get('phase') == 'comparison' for p in phase_records):
        reasons.append('missing_comparison_phase_evidence')
    for phase in phase_records:
        if phase.get('status') != 'succeeded':
            reasons.append(f'{phase.get("candidate") or "study"}:{phase.get("phase")}:computation_{phase.get("status")}')
            timed_out |= phase.get('status') == 'timed_out'
    complete = not reasons
    status = 'completed' if complete else 'timed_out' if timed_out else 'incomplete' if usable else 'failed'
    return {'status': status, 'completion_eligible': complete, 'usable_candidates': usable,
            'scientific_status': 'not_verified', 'failure_reasons': list(dict.fromkeys(reasons))}


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



def run_hypothesis_study(store, candidates, training, holdout=(), observations=(), bounds=None,
                         starts=3, seed=17, max_nfev=100, timeout_s=90, separation_threshold=.05,
                         *, total_timeout_s=DEFAULT_TOTAL_TIMEOUT_S, deadline_origin='explicit_execution_budget'):
    deadline = Deadline(total_timeout_s, maximum=MAX_TOTAL_TIMEOUT_S)
    positive_seconds(timeout_s)
    positive_seconds(separation_threshold, 'separation_threshold')
    if not candidates or not training:
        raise ValueError('nonempty candidates and training required')
    obs = [serialization.to_dict(o) if isinstance(o, ObservationSpec) else dict(o)
           for o in observations] if observations else [{'id': 'response', 'expression': 'x'}]
    train = [_condition(c) for c in training]
    if any(row['role'] != 'training' for row in train):
        raise ValueError('optimizer may only receive training conditions')
    data_ref = store.put('dataset', {'conditions': train, 'role': 'training'})
    refs, audit, records = [data_ref], [], {}

    def phase(stage, name, callback, *, cap=None, condition=None):
        start, allotted, value = deadline.elapsed(), 0., None
        status, error_type = 'succeeded', None
        try:
            allotted = deadline.remaining(cap)
            value = callback(allotted)
            deadline.remaining()
            if isinstance(value, dict) and value.get('status') in ('failed', 'timed_out', 'unsupported'):
                status = value['status']
        except TimeoutError:
            status, error_type = 'timed_out', 'TimeoutError'
        except Exception as exc:
            status, error_type = 'failed', type(exc).__name__
        record = {'phase': stage, 'candidate': name, 'status': status,
                  'allotted_s': allotted, 'elapsed_s': deadline.elapsed() - start}
        if condition is not None:
            record['condition_id'] = condition
        if error_type:
            record['error_type'] = error_type
        audit.append(record)
        return value, status

    for name, original in candidates.items():
        item = {'fit_status': 'not_run', 'model_ref': None, 'fit_ref': None,
                'crosscheck': {'ok': False, 'scaled_max_error': None}, 'parameters': None,
                'training_weighted_rmse': None, 'heldout_weighted_rmse': None,
                'heldout_scaled_rmse': None,
                'phase_status': {p: 'not_run' for p in ('build', 'fit', 'crosscheck', 'training_score', 'holdout', 'suggestion')}}
        rec = records[name] = {'item': item, 'artifact': None, 'fit': None}
        def build(_budget):
            hypothesis_ref = store.put('hypothesis', {'claim_type': 'system_conjecture', 'mechanism': name,
                'assumptions': list(original.hypothesis_refs), 'limitations': ['candidate template, not open mechanism discovery']})
            draft = replace(original, hypothesis_refs=(hypothesis_ref,))
            draft_ref = store.put('model_draft', serialization.to_dict(draft), refs=(hypothesis_ref,))
            refs.append(draft_ref)
            artifact = modeling.build_model(draft)
            item['model_ref'] = store.put('model', serialization.to_dict(artifact), refs=(draft_ref,))
            refs.append(item['model_ref'])
            return artifact
        artifact, status = phase('build', name, build)
        item['phase_status']['build'] = status
        if status != 'succeeded':
            continue
        rec['artifact'] = artifact
        fit, status = phase('fit', name, lambda budget: fitting.fit_parameters(artifact, train, obs,
            (bounds or {}).get(name, _default_bounds(original)), starts=starts, seed=seed,
            max_nfev=max_nfev, timeout_s=budget), cap=timeout_s)
        try:
            if not isinstance(fit, dict):
                raise ValueError('fit returned no structured result')
            canonical_bytes(fit)
            if status == 'succeeded' and (fit.get('status') != 'succeeded'
                    or not isinstance(fit.get('best'), dict) or not isinstance(fit['best'].get('parameters'), dict)):
                raise ValueError('fit returned no frozen parameter endpoint')
        except (ValueError, TypeError):
            fit = {'status': status if status != 'succeeded' else 'failed', 'best': None,
                   'starts': [], 'diagnostics': ['no finite structured fitting result']}
            if status == 'succeeded':
                status = 'failed'
                audit[-1]['status'] = status
        item['phase_status']['fit'] = status
        item['fit_status'] = status
        item['fit_ref'] = store.put('fitting', fit, refs=(item['model_ref'], data_ref))
        refs.append(item['fit_ref'])
        rec['fit'] = fit
        if status != 'succeeded':
            continue
        item['parameters'] = fit.get('best', {}).get('parameters')
        def crosscheck(budget):
            parameters = fit['best']['parameters']
            spec = serialization.from_dict(ExperimentSpec, train[0]['experiment'])
            spec = replace(spec, parameter_overrides={**dict(spec.parameter_overrides), **parameters})
            rr = simulation.simulate(artifact, spec, tuple(ObservationSpec(**o) for o in obs),
                                     SolverConfig(timeout_s=budget))
            if rr.status == 'timed_out':
                raise TimeoutError('cross-backend simulation timed out')
            if rr.status != 'succeeded' or rr.times != spec.times:
                raise ValueError('cross-backend simulation failed or returned another time grid')
            predicted = fit['predictions'][train[0]['id']]['observables']
            errors = []
            for observation in obs:
                oid = observation['id']
                left, right = rr.values[oid], predicted[oid]
                if len(left) != len(spec.times) or len(right) != len(spec.times):
                    raise ValueError('cross-backend shape mismatch')
                errors.extend(abs(float(a) - float(b)) for a, b in zip(left, right))
            if not errors or not all(math.isfinite(error) for error in errors):
                raise ValueError('cross-backend results are nonfinite')
            error = max(errors)
            return {'status': 'succeeded' if error <= _XRR_TOL else 'failed', 'ok': error <= _XRR_TOL,
                    'scaled_max_error': error, 'scale': 1., 'parameters': 'frozen fitted values', 'observations': obs}
        cross, status = phase('crosscheck', name, crosscheck, cap=30.)
        item['phase_status']['crosscheck'] = status
        item['crosscheck'] = cross or {'ok': False, 'scaled_max_error': None, 'status': status}
        cross_ref = store.put('crosscheck', item['crosscheck'], refs=(item['fit_ref'],))
        refs.append(cross_ref)
        if status != 'succeeded':
            continue
        def training_score(_budget):
            cost = fit['best']['cost']
            if not _finite_score(cost):
                raise ValueError('invalid training cost')
            score = math.sqrt(2 * cost / sum(len(r['experiment']['times']) * len(obs) for r in train))
            if not _finite_score(score):
                raise ValueError('nonfinite training score')
            return score
        score, status = phase('training_score', name, training_score)
        item['phase_status']['training_score'] = status
        if status == 'succeeded':
            item['training_weighted_rmse'] = score

    # No holdout values enter an optimizer. Read the scoring corpus after all fits.
    hold = [_condition(c) for c in holdout]
    if any(row['role'] != 'holdout' for row in hold):
        raise ValueError('holdout must explicitly carry role holdout')
    hold_ref = store.put('dataset', {'conditions': hold, 'role': 'holdout'})
    refs.append(hold_ref)
    suggestion_predictions = {}
    suggested = dict(train[0]['experiment'])
    suggested['parameter_overrides'] = {**suggested.get('parameter_overrides', {}), 'b': 0.}
    for name, rec in records.items():
        item, fit = rec['item'], rec['fit']
        if item['phase_status']['training_score'] != 'succeeded':
            continue
        scores, statuses = [], []
        for row in hold:
            def heldout_score(budget):
                predicted = fitting.predict(rec['artifact'], row['experiment'], obs,
                    fit['best']['parameters'], timeout_s=budget)['observables']
                return _scores(row, predicted)
            score, status = phase('holdout', name, heldout_score, cap=30., condition=row['id'])
            statuses.append(status)
            if status != 'succeeded':
                break
            scores.append(score)
        item['phase_status']['holdout'] = ('timed_out' if 'timed_out' in statuses else
            'failed' if any(s != 'succeeded' for s in statuses) else 'succeeded' if hold else 'not_applicable')
        if scores and len(scores) == len(hold):
            item['heldout_weighted_rmse'] = math.sqrt(sum(s[0]**2 for s in scores) / len(scores))
            item['heldout_scaled_rmse'] = math.sqrt(sum(s[1]**2 for s in scores) / len(scores))
        if item['training_weighted_rmse'] > 3:
            item['phase_status']['suggestion'] = 'not_applicable'
            continue
        def intervention(budget):
            predicted = fitting.predict(rec['artifact'], suggested, obs, fit['best']['parameters'],
                                         timeout_s=budget)['observables']
            for observation in obs:
                column = predicted[observation['id']]
                if len(column) != len(suggested['times']) or not all(math.isfinite(float(x)) for x in column):
                    raise ValueError('invalid intervention prediction')
            return predicted
        predicted, status = phase('suggestion', name, intervention, cap=30.)
        item['phase_status']['suggestion'] = status
        if status == 'succeeded':
            suggestion_predictions[name] = predicted

    summary = {name: rec['item'] for name, rec in records.items()}
    adequate = {name: item for name, item in summary.items()
                if item['training_weighted_rmse'] is not None and item['training_weighted_rmse'] <= 3}
    def compare(_budget):
        ranking, winner = 'none_adequate', None
        if not hold and adequate:
            ranking = 'indistinguishable_from_training' if len(adequate) > 1 else 'single_training_compatible_candidate'
        elif hold:
            supported = {name: item for name, item in adequate.items()
                         if item['heldout_weighted_rmse'] is not None and item['heldout_weighted_rmse'] <= 3}
            if supported:
                ordered = sorted(supported, key=lambda name: supported[name]['heldout_scaled_rmse'])
                if len(ordered) > 1 and supported[ordered[1]]['heldout_scaled_rmse'] - supported[ordered[0]]['heldout_scaled_rmse'] < separation_threshold:
                    ranking = 'indistinguishable'
                else:
                    ranking, winner = 'best_compatible_candidate', ordered[0]
        gap = 0.
        names = list(suggestion_predictions)
        for index, name in enumerate(names):
            for other in names[index+1:]:
                for observation in obs:
                    for left, right in zip(suggestion_predictions[name][observation['id']], suggestion_predictions[other][observation['id']]):
                        difference = abs(left-right)
                        if not math.isfinite(difference):
                            raise ValueError('nonfinite intervention separation')
                        gap = max(gap, difference)
        return ranking, winner, gap
    compared, comparison_status = phase('comparison', None, compare)
    ranking, winner, gap = compared if comparison_status == 'succeeded' else ('incomplete_computation', None, None)
    from .application import environment_fingerprint
    payload = {'status': 'incomplete', 'environment': environment_fingerprint(), 'candidates': summary,
        'comparison': {'ranking': ranking, 'winner': winner, 'separation_threshold': separation_threshold,
            'adequacy_weighted_rmse': 3, 'note': 'Specified known independent Gaussian noise; no significance test or proof of mechanism'},
        'experiment_suggestion': {'intervention': 'b=0', 'experiment': suggested, 'predictions': suggestion_predictions,
            'max_nominal_prediction_gap': gap, 'scale': 1.,
            'is_verification_of_existing_holdout': any(row['experiment']['parameter_overrides'].get('b') == 0 for row in hold),
            'limitation': 'Conditional on candidate structures and nominal fitted values; parameter uncertainty not fully propagated.'},
        'protocol': {'seed': seed, 'starts': starts, 'max_nfev': max_nfev, 'timeout_s': timeout_s,
            'total_timeout_s': deadline.timeout_s, 'observations': obs, 'training_ref': data_ref,
            'holdout_ref': hold_ref, 'holdout_count': len(hold), 'prediction_timeout_s': 30., 'execution_policy_version': 1},
        'deadline': {'version': 1, 'total_timeout_s': deadline.timeout_s, 'origin': deadline_origin,
            'elapsed_s': deadline.elapsed(), 'phases': audit,
            'cleanup_note': 'Numerical workers are bounded; process reaping and durable result I/O may add cleanup overhead.'},
        'limitations': ['Synthetic, linearized candidate templates; not open mechanism discovery',
            'Local sensitivity and bounds are not global identifiability or confidence intervals',
            'Readout x is a signed deviation, not a concentration', 'd/h units unknown; not fully dimensionally verified',
            'No holdout refitting; no biological truth claim']}
    payload['deadline']['exhausted'] = payload['deadline']['elapsed_s'] >= deadline.timeout_s
    verdict = study_verdict(payload)
    payload.update(verdict)
    if not verdict['completion_eligible']:
        payload['comparison'].update(ranking='incomplete_computation', winner=None)
        payload['experiment_suggestion']['max_nominal_prediction_gap'] = None
    payload['experiment_suggestion']['status'] = ('succeeded' if verdict['completion_eligible'] and adequate else
        'not_applicable' if verdict['completion_eligible'] else 'incomplete')
    return store.put('hypothesis_study', payload, refs=refs)
