"""Explicit, frozen ODE estimation branches over the existing bounded fitter.

Estimated values are candidate outputs, never source claims or automatic model
updates. Validation is downstream of a committed training-only fit artifact.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import math
import os
from uuid import uuid4

from . import application, evidence, fitting, parameter_applicability
from .deadlines import Deadline, positive_seconds
from .domain.contracts import ExperimentSpec, ModelArtifact, ObservationSpec, require_id, strict_float
from .domain.serialization import from_dict, to_dict
from .storage import canonical_bytes

PLAN = 'parameter_estimation_plan'
RESULT = 'parameter_estimation_result'
PROFILE = 'rate_rule_estimation_v1'
LOSS = 'weighted_least_squares_known_sigma_v1'


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('invalid estimation fields; required: ' + ', '.join(sorted(required)))


def _text(value, name, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1..{limit} characters')
    return value


def _integer(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{name} must be an integer in {lo}..{hi}')
    return value


def _context(value):
    # Reuse the existing declared scientific condition vocabulary, not synonyms.
    return parameter_applicability.requirements({'x/value': {'unit': 'dimensionless', 'conditions': value}})['x/value']['conditions']


def _provenance(store, value, sources):
    _fields(value, {'source_ref', 'page', 'start', 'end', 'origin', 'sigma_origin'})
    if value['source_ref'] not in sources:
        raise ValueError('dataset provenance is outside the explicit source scope')
    if value['origin'] not in ('operator_supplied', 'literature_transcription', 'synthetic_control'):
        raise ValueError('dataset origin must be explicit')
    _text(value['sigma_origin'], 'sigma origin')
    page, start, end = (value[k] for k in ('page', 'start', 'end'))
    if any(type(v) is not int for v in (page, start, end)) or page < 1:
        raise ValueError('dataset source offsets must be integers')
    text = evidence._source_page_text(store, value['source_ref'], page)
    if not 0 <= start < end <= len(text) or end - start > 8000:
        raise ValueError('dataset provenance requires a nonempty bounded source span')
    return {**value, 'quote': text[start:end], 'semantic_support': 'unknown'}


def _rows(store, rows, role, observations, sources, context):
    if not isinstance(rows, list) or len(rows) > 8 or (role == 'training' and not rows):
        raise ValueError('estimation requires training data, at most eight conditions per role')
    result, ids = [], set()
    units = {o['id']: o['unit'] for o in observations}
    for row in rows:
        _fields(row, {'id', 'role', 'experiment', 'values', 'sigma', 'units', 'context', 'provenance'})
        name = _text(row['id'], 'condition id', 128)
        if name in ids or row['role'] != role:
            raise ValueError('condition IDs must be unique and data roles explicit')
        ids.add(name)
        spec = from_dict(ExperimentSpec, row['experiment'])
        if not 2 <= len(spec.times) <= 500 or spec.times[0] < 0 or spec.times[-1] > 10000:
            raise ValueError('estimation requires 2..500 samples in 0..10000')
        if row['units'] != units or _context(row['context']) != context:
            raise ValueError('dataset units/context must exactly match the declared protocol; no automatic conversion')
        values, sigma = row['values'], row['sigma']
        if not isinstance(values, dict) or not isinstance(sigma, dict) or set(values) != set(units) or set(sigma) != set(units):
            raise ValueError('data values and sigma columns must equal observable IDs')
        columns, noise = {}, {}
        for oid in units:
            if not isinstance(values[oid], list) or len(values[oid]) != len(spec.times):
                raise ValueError('dataset values must match the exact time grid; missing values are unsupported')
            columns[oid] = [strict_float(v, 'dataset value') for v in values[oid]]
            noise[oid] = positive_seconds(sigma[oid], 'sigma')
        result.append(dict(id=name, role=role, experiment=to_dict(spec), values=columns, sigma=noise,
            units=dict(units), context=context, provenance=_provenance(store, row['provenance'], sources)))
    return result


def _normalize(store, config):
    _fields(config, {'model_ref', 'source_refs', 'observations', 'parameters', 'training', 'validation', 'context', 'rationale'},
            {'parent_run_ref', 'starts', 'seed', 'max_nfev', 'fit_timeout_s', 'total_timeout_s', 'loss'})
    _text(config['rationale'], 'estimation rationale')
    sources = config['source_refs']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 32 or len(set(sources)) != len(sources):
        raise ValueError('estimation needs 1..32 distinct explicitly authorized sources')
    for ref in sources:
        if store.get(ref)['kind'] != 'source':
            raise ValueError('estimation source_refs must be sources')
    model = store.get(config['model_ref'])
    if model['kind'] != 'model':
        raise ValueError('estimation requires an existing ODE model template')
    artifact = from_dict(ModelArtifact, model['payload'])
    profile = fitting.describe_profile(artifact)
    if len(profile['states']) > 32:
        raise ValueError('estimation supports at most 32 states')
    observations = config['observations']
    if not isinstance(observations, list) or not 1 <= len(observations) <= 4:
        raise ValueError('estimation requires 1..4 explicit observables')
    obs = []
    for item in observations:
        _fields(item, {'id', 'expression', 'unit'})
        require_id(item['id'], 'observation')
        _text(item['expression'], 'observable expression', 1000)
        _text(item['unit'], 'observable unit', 128)
        obs.append(to_dict(from_dict(ObservationSpec, item)))
    if len({o['id'] for o in obs}) != len(obs):
        raise ValueError('observable IDs must be unique')
    parameters, bounds = {}, {}
    if not isinstance(config['parameters'], dict) or not 1 <= len(config['parameters']) <= 8:
        raise ValueError('estimation requires 1..8 bounded constant parameters')
    context = _context(config['context'])
    for name, item in sorted(config['parameters'].items()):
        if name not in profile['constant_parameters']:
            raise ValueError('estimation can only target existing constant parameters, not unknown initials or equations')
        _fields(item, {'bounds', 'unit', 'rationale'})
        _text(item['rationale'], 'bound rationale')
        _text(item['unit'], 'parameter unit', 128)
        if item['unit'] != 'unknown':
            parameter_applicability.requirements({name+'/value': {'unit': item['unit'], 'conditions': context}})
        pair = item['bounds']
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('explicit lower and upper bounds required')
        lo, hi = [strict_float(v, 'parameter bound') for v in pair]
        if not lo < hi or not math.isfinite(hi-lo):
            raise ValueError('finite lower < upper parameter bounds required')
        bounds[name] = [lo, hi]
        parameters[name] = dict(bounds=[lo, hi], unit=item['unit'], rationale=item['rationale'])
    training = _rows(store, config['training'], 'training', obs, sources, context)
    validation = _rows(store, config['validation'], 'validation', obs, sources, context)
    if {r['id'] for r in training} & {r['id'] for r in validation}:
        raise ValueError('training and validation IDs must be disjoint')
    if sum(len(r['experiment']['times']) * len(obs) for r in training+validation) > 8000:
        raise ValueError('estimation dataset exceeds 8000 scalar samples')
    # Backend protocol preflight does not simulate or optimize; inspect both roles.
    fitting.preflight(artifact, _backend_rows(training), obs, bounds,
                      validation_experiments=[row['experiment'] for row in validation])
    parent = config.get('parent_run_ref')
    if parent is not None:
        run = store.get(parent)
        if run['kind'] != 'agent_run' or not set(sources) <= set(run['payload']['source_refs']):
            raise ValueError('estimation branch sources must already belong to its parent run')
        if run['payload'].get('model_ref') not in (None, config['model_ref']):
            raise ValueError('estimation branch must use its parent model when one exists')
    if config.get('loss', LOSS) != LOSS:
        raise ValueError('unsupported estimation loss/noise protocol')
    fit_timeout = positive_seconds(config.get('fit_timeout_s', 60.), 'fit_timeout_s', maximum=90.)
    total_timeout = positive_seconds(config.get('total_timeout_s', 120.), 'total_timeout_s', maximum=300.)
    return dict(model_ref=config['model_ref'], source_refs=list(sources), observations=obs, parameters=parameters,
        training=training, validation=validation, context=context, rationale=config['rationale'], parent_run_ref=parent,
        loss=LOSS, starts=_integer(config.get('starts', 3), 'starts', 1, 8),
        seed=_integer(config.get('seed', 17), 'seed', 0, 2**32-1),
        max_nfev=_integer(config.get('max_nfev', 100), 'max_nfev', 1, 1000),
        fit_timeout_s=fit_timeout, total_timeout_s=total_timeout)


def _backend_rows(rows):
    return [{key: deepcopy(row[key]) for key in ('id', 'role', 'experiment', 'values', 'sigma')} for row in rows]


def _raw_config(config):
    value = deepcopy(config)
    for row in value['training'] + value['validation']:
        for key in ('quote', 'semantic_support'):
            row['provenance'].pop(key, None)
    return value


def prepare_plan(store, config):
    normalized = _normalize(store, config)
    refs = [normalized['model_ref'], *normalized['source_refs']]
    if normalized['parent_run_ref']:
        refs.append(normalized['parent_run_ref'])
    return store.put(PLAN, dict(version=1, profile=PROFILE, id=uuid4().hex, config=normalized,
        environment=application.environment_fingerprint(), authority='operator_frozen_estimation_branch',
        scientific_status='not_verified', output_policy='candidate_estimates_only_no_source_or_model_update'),
        refs=tuple(dict.fromkeys(refs)))


def load_plan(store, ref):
    obj = store.get(ref); p = obj['payload']
    if obj['kind'] != PLAN or type(p.get('version')) is not int or p['version'] != 1 or p.get('profile') != PROFILE:
        raise ValueError('unsupported parameter estimation plan')
    config = _normalize(store, _raw_config(p['config']))
    required = {config['model_ref'], *config['source_refs'], *([config['parent_run_ref']] if config['parent_run_ref'] else [])}
    if (canonical_bytes(config) != canonical_bytes(p['config']) or set(obj['refs']) != required
            or p['authority'] != 'operator_frozen_estimation_branch'
            or p['scientific_status'] != 'not_verified'
            or p['output_policy'] != 'candidate_estimates_only_no_source_or_model_update'):
        raise ValueError('estimation plan no longer matches its frozen profile')
    if canonical_bytes(p['environment']) != canonical_bytes(application.environment_fingerprint()):
        raise ValueError('estimation environment changed; prepare a new plan, do not revise history')
    return p


def plan_summary(store, ref):
    p = load_plan(store, ref); c = p['config']
    return dict(plan_ref=ref, profile=PROFILE, model_ref=c['model_ref'], parent_run_ref=c['parent_run_ref'],
        parameters=c['parameters'], observations=c['observations'], context=c['context'], loss=c['loss'],
        training_conditions=len(c['training']), validation_conditions=len(c['validation']),
        budget={k: c[k] for k in ('starts', 'seed', 'max_nfev', 'fit_timeout_s', 'total_timeout_s')},
        notice='Frozen explicit estimation branch. Validation values unavailable in this summary; no automatic adoption or proof of identifiability.')


@contextmanager
def _owner(store, ref):
    # Same local store ownership convention as Agent recovery; lock is not a sandbox.
    from . import local_ownership
    directory = store.root / '.estimation-owners'; directory.mkdir(mode=0o700, exist_ok=True)
    if local_ownership.is_redirect(directory):
        raise ValueError('estimation owner directory must not be a symlink')
    try:fd=local_ownership.acquire(directory/(ref+'.lock'))
    except local_ownership.OwnerBusy as exc:raise ValueError('estimation plan already has an active owner') from exc
    try:
        yield
    finally:
        os.close(fd)


def _records(store, kind, plan_ref):
    return [r for r in store.list(kind) if store.get(r)['payload'].get('plan_ref') == plan_ref]


def _diagnose(fit, parameters):
    uncertainty = fit.get('uncertainty') or {}
    issues = []
    rank = uncertainty.get('rank'); cond = uncertainty.get('condition_number')
    if rank is not None and rank < len(parameters):
        issues.append('local_rank_deficient')
    if cond is not None and cond > 1e8:
        issues.append('local_ill_conditioned_at_declared_heuristic_1e8')
    best = fit.get('best')
    starts = [s for s in fit.get('starts', []) if s.get('status') == 'succeeded']
    if best:
        close = [s for s in starts if s['cost'] <= best['cost'] + 1e-6 * max(1., best['cost'])]
        if close and any(max(s['final'][n] for s in close)-min(s['final'][n] for s in close) > .001*(p['bounds'][1]-p['bounds'][0])
               for n, p in parameters.items()):
            issues.append('distinct_near_equal_cost_endpoints_at_declared_heuristic')
    return dict(local_rank=rank, parameter_count=len(parameters), condition_number=cond,
        warnings=issues, global_identifiability='unknown', confidence_intervals='not_run',
        inference_status='local_diagnostics_only' if best else 'not_estimated',
        diagnostic_policy=dict(version=1, condition_limit=1e8, near_cost_tolerance=1e-6, endpoint_bound_fraction=.001),
        notice='Local diagnostics and endpoint spreads are not uniqueness proofs or confidence intervals; thresholds are engineering heuristics.')


def execute(store, plan_ref):
    plan = load_plan(store, plan_ref); c = plan['config']
    with _owner(store, plan_ref):
        prior = _records(store, RESULT, plan_ref)
        if len(prior) == 1:
            return prior[0]  # Explicit reuse, no resetting budgets or fitting after validation.
        if prior or _records(store, 'parameter_estimation_execution', plan_ref):
            raise ValueError('ambiguous or interrupted estimation; inspect records, do not automatically retry')
        execution = store.put('parameter_estimation_execution', dict(version=1, id=uuid4().hex,
            plan_ref=plan_ref, phase='reserved', recovery_support='no_automatic_retry_unknown_result'), refs=(plan_ref,))
        deadline = Deadline(c['total_timeout_s'])
        artifact = from_dict(ModelArtifact, store.get(c['model_ref'])['payload'])
        train_ref = store.put('estimation_dataset', dict(role='training', rows=c['training']), refs=tuple(c['source_refs']))
        refs = [plan_ref, execution, train_ref]; validation_results = []; audit = []
        fit, fit_ref, status = None, None, 'failed'
        try:
            budget = deadline.remaining(c['fit_timeout_s'])
            audit.append(dict(phase='fit', allotted_s=budget))
            fit = fitting.fit_parameters(artifact, _backend_rows(c['training']), c['observations'],
                {n: p['bounds'] for n, p in c['parameters'].items()}, starts=c['starts'], seed=c['seed'],
                max_nfev=c['max_nfev'], timeout_s=budget)
            canonical_bytes(fit)
            if fit['status'] not in ('succeeded', 'failed', 'timed_out', 'unsupported'):
                raise ValueError('unsupported fit result status')
            if fit['status'] == 'succeeded':
                point = fit['best']['parameters']
                if set(point) != set(c['parameters']):
                    raise ValueError('fitted endpoint parameter set differs from protocol')
                for n, v in point.items():
                    v = strict_float(v, 'fitted value'); lo, hi = c['parameters'][n]['bounds']
                    if not lo <= v <= hi:
                        raise ValueError('fitted endpoint outside frozen bounds')
            fit_ref = store.put('fitting', fit, refs=(c['model_ref'], train_ref))
            refs.append(fit_ref)  # The training-only fit commits before validation is read by the evaluator.
            status = fit['status']
            deadline.remaining()
            if status == 'succeeded':
                for row in c['validation']:
                    budget = deadline.remaining()
                    audit.append(dict(phase='validation', condition_id=row['id'], allotted_s=budget))
                    prediction = fitting.predict(artifact, row['experiment'], c['observations'], point, timeout_s=budget)
                    deadline.remaining()
                    if prediction['times'] != row['experiment']['times']:
                        raise ValueError('validation prediction time grid differs from protocol')
                    residuals = []
                    for oid in row['values']:
                        values = prediction['observables'][oid]
                        if len(values) != len(row['values'][oid]):
                            raise ValueError('validation prediction length mismatch')
                        residuals.extend((strict_float(p, 'prediction')-v)/row['sigma'][oid] for p, v in zip(values, row['values'][oid]))
                    score = math.hypot(*residuals)/math.sqrt(len(residuals))
                    if not math.isfinite(score):
                        raise ValueError('validation score is nonfinite')
                    validation_results.append(dict(condition_id=row['id'], status='succeeded', weighted_rmse=score))
        except TimeoutError as exc:
            status = 'timed_out'; audit.append(dict(phase='execution', error=str(exc)))
        except Exception as exc:
            status = 'failed'; audit.append(dict(phase='execution', error=f'{type(exc).__name__}: {exc}'))
        diagnostics = _diagnose(fit if fit_ref else {}, c['parameters'])
        estimates = {}
        if fit_ref and fit.get('status') == 'succeeded' and fit.get('best'):
            for name, value in fit['best']['parameters'].items():
                estimates[name] = dict(value=value, unit=c['parameters'][name]['unit'], context=c['context'],
                    origin='fitted_estimate', authority='training_only_computation', adoption_status='candidate_only',
                    source_support='unknown', bounds=c['parameters'][name]['bounds'],
                    endpoint_spread=(fit.get('uncertainty') or {}).get('endpoint_spread', {}).get(name))
        unresolved = ['global_or_structural_identifiability_unknown', 'confidence_intervals_not_run', 'scientific_support_unknown']
        if not c['validation']:
            unresolved.append('validation_data_not_supplied')
        if any(p['unit'] == 'unknown' for p in c['parameters'].values()) or any(o['unit'] == 'unknown' for o in c['observations']):
            unresolved.append('declared_units_unknown')
        if any(r['experiment']['time_unit'] == 'unknown' for r in c['training']+c['validation']):
            unresolved.append('time_unit_unknown')
        unresolved.extend(diagnostics['warnings'])
        if status != 'succeeded':
            unresolved.append('scientific_execution_incomplete')
        return store.put(RESULT, dict(version=1, profile=PROFILE, plan_ref=plan_ref, execution_ref=execution,
            fit_ref=fit_ref, execution_status=status,
            scientific_status='not_verified', estimates=estimates, diagnostics=diagnostics,
            validation=validation_results, validation_status=('succeeded' if len(validation_results) == len(c['validation']) else 'incomplete') if c['validation'] else 'not_run',
            unresolved=unresolved, phase_audit=audit, elapsed_s=deadline.elapsed(),
            total_timeout_s=c['total_timeout_s'], output_policy='candidate_estimates_only_no_source_or_model_update'), refs=tuple(refs))


def result_summary(store, ref):
    obj = store.get(ref)
    if obj['kind'] != RESULT:
        raise ValueError('expected parameter estimation result')
    p = obj['payload']
    # No raw validation values or trajectories in a future model-facing summary.
    return {key: deepcopy(p[key]) for key in ('plan_ref', 'execution_status', 'scientific_status', 'estimates',
        'diagnostics', 'validation', 'validation_status', 'unresolved', 'elapsed_s', 'total_timeout_s', 'output_policy')}


def status(store, plan_ref):
    if store.get(plan_ref)['kind'] != PLAN:
        raise ValueError('expected parameter estimation plan')
    results = _records(store, RESULT, plan_ref)
    executions = _records(store, 'parameter_estimation_execution', plan_ref)
    state = 'prepared'
    if executions:
        state = 'reserved_result_unknown_do_not_retry'
    if len(results) == 1:
        state = 'result_recorded'
    if len(results) > 1 or len(executions) > 1:
        state = 'ambiguous_do_not_retry'
    return dict(plan_ref=plan_ref, state=state, execution_refs=executions, result_refs=results,
                notice='Read-only local records; reserved does not prove an active worker or a completed fit.')
