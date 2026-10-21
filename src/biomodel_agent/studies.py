"""Frozen, bounded adaptation-study plans shared by CLI and the agent tool.

The operator selects supported candidate templates and data before inference.
Holdout values never appear in the model-facing plan/result summaries.
"""
from __future__ import annotations

import math
from copy import deepcopy

from . import hypotheses
from .domain.contracts import ExperimentSpec
from .domain.serialization import from_dict, to_dict
from .storage import canonical_bytes

PLAN_KIND = 'hypothesis_study_plan'
PROFILE = 'adaptation_study_v2'
LEGACY_PROFILE = 'adaptation_study_v1'
_CONFIG_KEYS = {'training', 'holdout', 'candidates', 'starts', 'seed', 'max_nfev', 'timeout_s', 'separation_threshold'}


def _integer(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{name} must be an integer in {lo}..{hi}')
    return value


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{name} must be finite' + (' and positive' if positive else ''))
    return value


def _conditions(rows, role, candidates):
    if not isinstance(rows, (list, tuple)) or len(rows) > 16 or (role == 'training' and not rows):
        raise ValueError(f'{role} requires at most 16 conditions (training must be nonempty)')
    result, ids, samples = [], set(), 0
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'id', 'role', 'experiment', 'values', 'sigma', 'scales'}:
            raise ValueError(f'invalid {role} condition fields')
        if row.get('role') != role:
            raise ValueError(f'{role} condition role must be explicit')
        name = row.get('id')
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or name in ids:
            raise ValueError(f'{role} conditions need unique nonempty IDs of at most 128 characters')
        ids.add(name)
        spec = row.get('experiment')
        spec = spec if isinstance(spec, ExperimentSpec) else from_dict(ExperimentSpec, spec)
        if not 2 <= len(spec.times) <= 500 or spec.times[0] < 0 or spec.times[-1] > 10000:
            raise ValueError('study experiments require 2..500 samples between 0 and 10000')
        if spec.time_unit not in ('second', 'unknown'):
            raise ValueError('adaptation templates require experiment time_unit second or explicit unknown')
        if spec.segments:
            raise ValueError('study profile supports constant condition overrides, not input segments')
        shared_states = set.intersection(*(set(s.id for s in d.states) for d in candidates.values()))
        if not set(spec.initial_overrides).issubset(shared_states):
            raise ValueError('initial overrides must target a state in every selected candidate')
        if not set(spec.parameter_overrides).issubset({'u', 'b'}):
            raise ValueError('only u/b interventions are allowed; fitted parameters remain frozen by the plan')
        if any(not 0 <= v <= 10 for v in spec.parameter_overrides.values()):
            raise ValueError('study u/b condition overrides must be between 0 and 10')
        values, sigma, scales = row.get('values'), row.get('sigma'), row.get('scales', {'response': 1.})
        if any(not isinstance(item, dict) or set(item) != {'response'} for item in (values, sigma, scales)):
            raise ValueError('study values, sigma and scales must match the fixed response observation')
        column = values['response']
        if not isinstance(column, (list, tuple)) or len(column) != len(spec.times):
            raise ValueError('response data must match the experiment time grid')
        result.append({'id': name, 'role': role, 'experiment': to_dict(spec),
                       'values': {'response': [_number(x, 'response') for x in column]},
                       'sigma': {'response': _number(sigma['response'], 'sigma', positive=True)},
                       'scales': {'response': _number(scales['response'], 'scale', positive=True)}})
        samples += len(column)
    if samples > 2000:
        raise ValueError(f'{role} exceeds the 2000-sample budget')
    return result


def _payload(config, *, version=2):
    allowed = _CONFIG_KEYS | ({'total_timeout_s'} if version == 2 else set())
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('unknown study configuration fields')
    available = hypotheses.adaptation_candidates()
    names = config.get('candidates', list(available))
    if (not isinstance(names, (list, tuple)) or not 1 <= len(names) <= 3
            or any(not isinstance(n, str) or n not in available for n in names) or len(set(names)) != len(names)):
        raise ValueError('select 1..3 distinct supported adaptation candidates')
    candidates = {name: available[name] for name in names}
    training = _conditions(config.get('training'), 'training', candidates)
    holdout = _conditions(config.get('holdout', []), 'holdout', candidates)
    if {r['id'] for r in training} & {r['id'] for r in holdout}:
        raise ValueError('training and holdout IDs must be disjoint')
    starts = _integer(config.get('starts', 3), 'starts', 1, 8)
    seed = _integer(config.get('seed', 17), 'seed', 0, 2**32 - 1)
    evaluations = _integer(config.get('max_nfev', 100), 'max_nfev', 1, 1000)
    timeout = _number(config.get('timeout_s', 30.), 'timeout_s', positive=True)
    if timeout > 90:
        raise ValueError('study timeout_s must not exceed 90 seconds per candidate fit')
    threshold = _number(config.get('separation_threshold', .05), 'separation_threshold', positive=True)
    normalized = {'training': training, 'holdout': holdout, 'candidates': list(names),
                  'starts': starts, 'seed': seed, 'max_nfev': evaluations,
                  'timeout_s': timeout, 'separation_threshold': threshold}
    if version == 2:
        from .deadlines import positive_seconds
        normalized['total_timeout_s'] = positive_seconds(config.get('total_timeout_s', hypotheses.DEFAULT_TOTAL_TIMEOUT_S),
            'total_timeout_s', maximum=hypotheses.MAX_TOTAL_TIMEOUT_S)
    return {'schema_version': version, 'profile': PROFILE if version == 2 else LEGACY_PROFILE, 'config': normalized,
            'candidates': {name: to_dict(draft) for name, draft in candidates.items()},
            'bounds': {name: {key: list(pair) for key, pair in hypotheses._default_bounds(draft).items()}
                       for name, draft in candidates.items()},
            'observations': [{'id': 'response', 'expression': 'x'}],
            'authority': 'operator_frozen_protocol', 'scientific_status': 'not_verified'}


def prepare_study(store, config):
    """Validate and freeze the complete study before any model or fitting call."""
    return store.put(PLAN_KIND, _payload(config))


def load_plan(store, plan_ref):
    record = store.get(plan_ref)
    if record['kind'] != PLAN_KIND:
        raise ValueError('study_plan_ref must identify a hypothesis study plan')
    payload = record['payload']
    if not isinstance(payload, dict) or 'config' not in payload:
        raise ValueError('invalid study plan')
    # Reject altered schemas, templates, bounds or scoring rules, rather than
    # silently changing a frozen protocol after a software upgrade.
    version = payload.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('unsupported study plan version')
    if canonical_bytes(payload) != canonical_bytes(_payload(payload['config'], version=version)):
        raise ValueError('study plan does not match its versioned profile; prepare a new plan')
    return payload


def plan_summary(store, plan_ref):
    plan = load_plan(store, plan_ref)
    config = plan['config']
    return {'plan_ref': plan_ref, 'profile': plan['profile'], 'candidates': config['candidates'],
            'training_conditions': len(config['training']), 'holdout_conditions': len(config['holdout']),
            'starts': config['starts'], 'max_nfev': config['max_nfev'], 'timeout_s': config['timeout_s'],
            'total_timeout_s': config.get('total_timeout_s', hypotheses.DEFAULT_TOTAL_TIMEOUT_S),
            'deadline_origin': 'operator_frozen_protocol' if plan['schema_version'] == 2 else 'legacy_execution_default',
            'notice': 'Operator-frozen conjecture templates. Run once; no candidate/bound/data changes. '
                      'Holdout values are unavailable. Fit and ranking do not prove biological truth.'}


def execute_study(store, plan_ref):
    plan = load_plan(store, plan_ref)
    from .domain.contracts import ModelDraft
    candidates = {name: from_dict(ModelDraft, value) for name, value in plan['candidates'].items()}
    config = deepcopy(plan['config'])
    result_ref = hypotheses.run_hypothesis_study(
        store, candidates, config['training'], holdout=config['holdout'],
        observations=plan['observations'], bounds=plan['bounds'], starts=config['starts'],
        seed=config['seed'], max_nfev=config['max_nfev'], timeout_s=config['timeout_s'],
        separation_threshold=config['separation_threshold'],
        total_timeout_s=config.get('total_timeout_s', hypotheses.DEFAULT_TOTAL_TIMEOUT_S),
        deadline_origin='operator_frozen_protocol' if plan['schema_version'] == 2 else 'legacy_execution_default')
    record = store.get(result_ref)
    return store.put('hypothesis_study', {**record['payload'], 'plan_ref': plan_ref,
                                          **hypotheses.study_verdict(record['payload'])},
                     refs=tuple(dict.fromkeys([plan_ref, *record['refs']])))


def result_summary(store, result_ref):
    record = store.get(result_ref)
    if record['kind'] != 'hypothesis_study':
        raise ValueError('result must identify a hypothesis study')
    result = record['payload']
    verdict = hypotheses.study_verdict(result)
    comparison = deepcopy(result['comparison'])
    if not verdict['completion_eligible']:
        comparison.update(ranking='incomplete_computation', winner=None)
    return {'execution_status': verdict['status'], 'scientific_status': 'not_verified',
            'usable_candidates': verdict['usable_candidates'],
            'completion_eligible': verdict['completion_eligible'], 'failure_reasons': verdict['failure_reasons'],
            'total_timeout_s': result.get('protocol', {}).get('total_timeout_s'),
            'comparison': comparison,
            'candidates': {name: {key: deepcopy(candidate[key]) for key in
                ('fit_status', 'parameters', 'training_weighted_rmse', 'heldout_weighted_rmse', 'heldout_scaled_rmse', 'phase_status') if key in candidate}
                for name, candidate in result['candidates'].items()},
            'note': 'Only training data entered fitting. Frozen holdout scoring; no refitting or mechanism proof.'}
