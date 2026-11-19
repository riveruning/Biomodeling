"""Agent requests and explicit review of estimation outputs, never paper facts."""
from __future__ import annotations

from copy import deepcopy

import libsbml

from . import estimation, modeling
from .domain.contracts import ModelArtifact, require_id, strict_float
from .domain.serialization import from_dict, to_dict
from .storage import canonical_bytes

REQUEST = 'parameter_estimation_request'
REVIEW = 'parameter_estimation_review'
CANDIDATE = 'estimated_model_candidate'


def request_payload(store, task_ref, source_refs, args):
    task = store.get(task_ref)
    if (task['kind'] != 'agent_task' or task['payload'].get('mode') != 'reproduction'
            or task['payload'].get('estimation_requests') is not True or task['payload'].get('estimation_plan_ref')):
        raise ValueError('estimation requests are not enabled in this task')
    estimation._fields(args, {'targets', 'rationale'})
    targets = args['targets']
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8 or len(set(targets)) != len(targets):
        raise ValueError('request 1..8 distinct parameter IDs; no data, bounds or values')
    for name in targets:
        require_id(name, 'proposed estimation target')
    estimation._text(args['rationale'], 'estimation request rationale')
    if not set(task['payload']['source_refs']) <= set(source_refs):
        raise ValueError('estimation request source scope omits task materials')
    for ref in source_refs:
        if store.get(ref)['kind'] != 'source':
            raise ValueError('estimation request requires source references')
    return dict(version=1, task_ref=task_ref, source_refs=list(source_refs), proposed_targets=targets,
        rationale=args['rationale'], status='pending_operator_protocol', authority='agent_proposal_only',
        scientific_support='unknown', notice='Proposed target IDs, not verified missing facts. Operator must freeze a separate plan; no computation authorized by this request.')


def request(store, task_ref, source_refs, args):
    return store.put(REQUEST, request_payload(store, task_ref, source_refs, args), refs=(task_ref, *source_refs))


def attachment(store, plan_ref, source_refs, experiment):
    plan = estimation.load_plan(store, plan_ref); config = plan['config']
    if list(source_refs) != config['source_refs'] or to_dict(experiment) != config['training'][0]['experiment']:
        raise ValueError('Agent estimation sources/experiment must match the frozen plan exactly')
    return estimation.plan_summary(store, plan_ref)


def check_result(store, ref, *, plan_ref=None):
    """Check local object bindings and candidate fields, not execution identity."""
    obj = store.get(ref); p = obj['payload']
    if (obj['kind'] != estimation.RESULT or type(p.get('version')) is not int or p['version'] != 1
            or p.get('profile') != estimation.PROFILE or p.get('scientific_status') != 'not_verified'
            or p.get('output_policy') != 'candidate_estimates_only_no_source_or_model_update'):
        raise ValueError('invalid estimation result profile or scientific status')
    if plan_ref is not None and p.get('plan_ref') != plan_ref:
        raise ValueError('estimation result belongs to another plan')
    plan = estimation.load_plan(store, p['plan_ref']); c = plan['config']
    execution = store.get(p['execution_ref'])
    if (not {p['plan_ref'], p['execution_ref']} <= set(obj['refs'])
            or execution['kind'] != 'parameter_estimation_execution'
            or execution['payload'].get('plan_ref') != p['plan_ref']
            or execution['payload'].get('phase') != 'reserved' or p['plan_ref'] not in execution['refs']):
        raise ValueError('estimation result/execution/plan binding mismatch')
    fit = {}
    expected = {}
    if p['fit_ref'] is not None:
        fit_obj = store.get(p['fit_ref']); fit = fit_obj['payload']
        data = [store.get(r) for r in fit_obj['refs'] if store.get(r)['kind'] == 'estimation_dataset']
        if (p['fit_ref'] not in obj['refs'] or fit_obj['kind'] != 'fitting' or c['model_ref'] not in fit_obj['refs']
                or len(data) != 1 or canonical_bytes(data[0]['payload']) != canonical_bytes(dict(role='training', rows=c['training']))):
            raise ValueError('estimation fit is not bound to its model/training dataset')
        if fit.get('status') == 'succeeded':
            values = fit['best']['parameters']
            if set(values) != set(c['parameters']):
                raise ValueError('estimated parameter set differs from the plan')
            for name, value in values.items():
                value = strict_float(value, 'estimate'); spec = c['parameters'][name]
                if not spec['bounds'][0] <= value <= spec['bounds'][1]:
                    raise ValueError('estimated value outside protocol bounds')
                expected[name] = dict(value=value, unit=spec['unit'], context=c['context'], origin='fitted_estimate',
                    authority='training_only_computation', adoption_status='candidate_only', source_support='unknown',
                    bounds=spec['bounds'], endpoint_spread=(fit.get('uncertainty') or {}).get('endpoint_spread', {}).get(name))
    if (canonical_bytes(expected) != canonical_bytes(p['estimates'])
            or canonical_bytes(estimation._diagnose(fit, c['parameters'])) != canonical_bytes(p['diagnostics'])):
        raise ValueError('estimated values or diagnostics differ from the saved training fit')
    if p['execution_status'] not in ('succeeded', 'failed', 'timed_out', 'unsupported'):
        raise ValueError('invalid estimation execution status')
    completed = p['validation']
    expected_ids = [r['id'] for r in c['validation']]
    if not isinstance(completed, list) or len(completed) > len(expected_ids):
        raise ValueError('invalid estimation validation records')
    for index, item in enumerate(completed):
        estimation._fields(item, {'condition_id', 'status', 'weighted_rmse'})
        if (item['condition_id'] != expected_ids[index] or item['status'] != 'succeeded'
                or strict_float(item['weighted_rmse'], 'validation error') < 0):
            raise ValueError('invalid estimation validation record')
    validation_status = ('succeeded' if len(completed) == len(expected_ids) else 'incomplete') if expected_ids else 'not_run'
    if p['validation_status'] != validation_status:
        raise ValueError('estimation validation status differs from actual records')
    if p['execution_status'] == 'succeeded' and (not expected or validation_status == 'incomplete'):
        raise ValueError('successful estimation lacks a confirmed fit/validation outcome')
    required = {'global_or_structural_identifiability_unknown', 'confidence_intervals_not_run', 'scientific_support_unknown'}
    if (not isinstance(p['unresolved'], list) or any(not isinstance(i, str) for i in p['unresolved'])
            or not required <= set(p['unresolved']) or not set(p['diagnostics']['warnings']) <= set(p['unresolved'])):
        raise ValueError('estimation result omitted required scientific unknowns')
    return p


def review(store, result_ref, *, decision, reviewer, reason, acknowledged_unresolved=()):
    p = check_result(store, result_ref)
    estimation._text(reviewer, 'reviewer', 200); estimation._text(reason, 'review reason')
    if decision not in ('approved_for_exploration', 'rejected'):
        raise ValueError('review can approve only for exploration, or reject')
    if any(store.get(r)['payload']['result_ref'] == result_ref for r in store.list(REVIEW)):
        raise ValueError('estimation result already reviewed; reviews are immutable')
    if (not isinstance(acknowledged_unresolved, (list, tuple))
            or any(not isinstance(i, str) for i in acknowledged_unresolved)
            or len(set(acknowledged_unresolved)) != len(acknowledged_unresolved)):
        raise ValueError('invalid acknowledged unresolved items')
    if decision == 'approved_for_exploration':
        if p['execution_status'] != 'succeeded' or set(acknowledged_unresolved) != set(p['unresolved']):
            raise ValueError('exploratory approval requires succeeded computation and explicit acknowledgement of every unresolved item')
    elif acknowledged_unresolved:
        raise ValueError('rejection does not acknowledge an adoption scope')
    return store.put(REVIEW, dict(version=1, result_ref=result_ref, plan_ref=p['plan_ref'], decision=decision,
        reviewer=reviewer, reason=reason, acknowledged_unresolved=sorted(acknowledged_unresolved),
        authority='operator_review_not_identity_verification', scientific_support='unknown'), refs=(result_ref, p['plan_ref']))


def _candidate_material(store, review_ref):
    review_obj = store.get(review_ref); r = review_obj['payload']
    if review_obj['kind'] != REVIEW or r.get('decision') != 'approved_for_exploration':
        raise ValueError('model candidate requires explicit exploratory approval')
    p = check_result(store, r['result_ref'])
    reviews = [ref for ref in store.list(REVIEW) if store.get(ref)['payload']['result_ref'] == r['result_ref']]
    if (reviews != [review_ref] or r.get('plan_ref') != p['plan_ref']
            or not {r['result_ref'], p['plan_ref']} <= set(review_obj['refs'])
            or set(r.get('acknowledged_unresolved', [])) != set(p['unresolved'])
            or p['execution_status'] != 'succeeded'):
        raise ValueError('ambiguous approval or changed estimation scope')
    c = estimation.load_plan(store, p['plan_ref'])['config']
    base = from_dict(ModelArtifact, store.get(c['model_ref'])['payload'])
    document = libsbml.readSBMLFromString(base.sbml); model = document.getModel()
    for name, estimate in p['estimates'].items():
        parameter = model.getParameter(name)
        if parameter is None or not parameter.getConstant() or parameter.setValue(estimate['value']) != libsbml.LIBSBML_OPERATION_SUCCESS:
            raise ValueError('estimated model target is not an editable constant')
    # A changed value cannot inherit its old numerical source/claim binding.
    changed = {name+'/value' for name in p['estimates']}
    links = {key: value for key, value in base.element_map.items() if key not in changed}
    artifact = ModelArtifact(base.id, libsbml.writeSBMLToString(document), element_map=links, parent_ref=c['model_ref'])
    report = modeling.validate_artifact(artifact)
    if not report.can_execute:
        raise ValueError('estimated model candidate failed structural validation')
    return p, c, artifact, (c['model_ref'], r['result_ref'], review_ref)


def _candidate_payload(p, c, model_ref, review_ref, result_ref):
    return dict(version=1, model_ref=model_ref, parent_model_ref=c['model_ref'],
        result_ref=result_ref, plan_ref=p['plan_ref'], review_ref=review_ref,
        estimated_parameters=deepcopy(p['estimates']), removed_value_bindings=sorted(name+'/value' for name in p['estimates']),
        scope='exploratory_model_only', scientific_status='not_verified', source_support='unknown',
        notice='Separate model candidate. No source/claim created, no parent task changed, no strict reproduction completion.')


def prepare_candidate(store, review_ref):
    p, c, artifact, refs = _candidate_material(store, review_ref)
    previous = [ref for ref in store.list(CANDIDATE) if store.get(ref)['payload']['review_ref'] == review_ref]
    if previous:
        if len(previous) != 1:
            raise ValueError('ambiguous estimated model candidates')
        load_candidate(store, previous[0])
        return previous[0]
    model_ref = store.put('model', to_dict(artifact), refs=refs)
    payload = _candidate_payload(p, c, model_ref, review_ref, refs[1])
    return store.put(CANDIDATE, payload, refs=(model_ref, *refs, p['plan_ref']))


def load_candidate(store, ref):
    """Rebuild the reviewed model read-only; no candidate mutation or execution."""
    obj = store.get(ref); value = obj['payload']
    if obj['kind'] != CANDIDATE or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('unsupported estimated model candidate')
    p, c, artifact, refs = _candidate_material(store, value['review_ref'])
    expected = _candidate_payload(p, c, value['model_ref'], value['review_ref'], refs[1])
    model = store.get(value['model_ref'])
    if (canonical_bytes(value) != canonical_bytes(expected) or set(obj['refs']) != {value['model_ref'], *refs, p['plan_ref']}
            or model['kind'] != 'model' or set(model['refs']) != set(refs)
            or canonical_bytes(model['payload']) != canonical_bytes(to_dict(artifact))):
        raise ValueError('estimated candidate no longer matches its review, fitted model or removed numerical bindings')
    return deepcopy(value)
