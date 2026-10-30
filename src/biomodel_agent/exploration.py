"""Explicit frozen exploration of reviewed fitted-model candidates.

Uses the existing application executor and its coarse/refined integrations.
Per-integration limits are not advertised as an OS-level whole-task deadline.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time
from uuid import uuid4

from . import application, bindings, estimation, estimation_workflow, fitting, time_units
from .deadlines import positive_seconds
from .domain.contracts import ExperimentSpec, ModelArtifact, ObservationSpec, SolverConfig, SimulationResult
from .domain.serialization import from_dict, to_dict
from .storage import canonical_bytes

PLAN = 'estimated_exploration_plan'
RUN = 'estimated_exploration_run'
EXECUTION = 'estimated_exploration_execution'
RECEIPT = 'estimated_exploration_receipt'
PROFILE = 'reviewed_estimated_ode_exploration_v1'


def _config(store, candidate_ref, config):
    estimation._fields(config, {'experiment', 'observations', 'solver', 'context', 'rationale'},
                       {'nonnegative', 'convergence_threshold'})
    candidate = estimation_workflow.load_candidate(store, candidate_ref)
    artifact = from_dict(ModelArtifact, store.get(candidate['model_ref'])['payload'])
    original = estimation.load_plan(store, candidate['plan_ref'])['config']
    context = estimation._context(config['context'])
    if context != original['context']:
        raise ValueError('exploration context must match the reviewed estimate; no inferred transfer')
    estimation._text(config['rationale'], 'exploration rationale')
    experiment = from_dict(ExperimentSpec, config['experiment'])
    if not 2 <= len(experiment.times) <= 500 or experiment.times[0] < 0 or experiment.times[-1] > 10000:
        raise ValueError('exploration requires 2..500 samples in 0..10000')
    items = config['observations']
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError('exploration requires 1..8 explicit observations')
    _, symbols = bindings.sbml_targets(artifact.sbml)
    observations, ids = [], set()
    for item in items:
        estimation._fields(item, {'id', 'expression', 'unit'})
        obs = from_dict(ObservationSpec, item)
        estimation._text(obs.expression, 'observable expression', 1000)
        estimation._text(obs.unit, 'observable unit', 128)
        if obs.id in ids or obs.id in symbols:
            raise ValueError('observation IDs must be distinct and must not collide with model symbols')
        ids.add(obs.id); observations.append(obs)
    # This mapping-only preflight performs no fitting or numerical prediction.
    fitting.validate_prediction_inputs(artifact, experiment, observations,
        {name: value['value'] for name, value in candidate['estimated_parameters'].items()})
    clock = time_units.check_experiment_time(artifact.sbml, experiment.time_unit)
    if clock.status == 'fail':
        raise ValueError(clock.message)
    solver = from_dict(SolverConfig, config['solver'])
    positive_seconds(solver.timeout_s, 'per-integration timeout_s', maximum=60.)
    if solver.max_steps > 1000000 or solver.rtol/10 <= 0 or solver.atol/10 <= 0:
        raise ValueError('solver step limit or refined tolerance outside exploration profile')
    nonnegative = config.get('nonnegative', [])
    states = fitting.describe_profile(artifact)['states']
    if (not isinstance(nonnegative, list) or any(not isinstance(n, str) for n in nonnegative)
            or len(set(nonnegative)) != len(nonnegative) or not set(nonnegative) <= set(states)):
        raise ValueError('nonnegative must explicitly name distinct model states')
    threshold = positive_seconds(config.get('convergence_threshold', .001), 'convergence_threshold', maximum=1.)
    return dict(experiment=to_dict(experiment), observations=[to_dict(o) for o in observations], solver=to_dict(solver),
        context=context, rationale=config['rationale'], nonnegative=list(nonnegative), convergence_threshold=threshold)


def prepare_plan(store, candidate_ref, config):
    settings = _config(store, candidate_ref, config)
    candidate = estimation_workflow.load_candidate(store, candidate_ref)
    return store.put(PLAN, dict(version=1, profile=PROFILE, id=uuid4().hex, candidate_ref=candidate_ref,
        model_ref=candidate['model_ref'], config=settings, environment=application.environment_fingerprint(),
        authority='operator_frozen_exploration', scope='exploratory_only', scientific_support='unknown',
        execution_policy=dict(max_application_calls=1, max_integrations=2, timeout_scope='each_integration',
            cache=False, strict=False, reference=None)), refs=(candidate_ref, candidate['model_ref']))


def load_plan(store, ref):
    obj = store.get(ref); p = obj['payload']
    if (obj['kind'] != PLAN or type(p.get('version')) is not int or p['version'] != 1 or p.get('profile') != PROFILE
            or p.get('authority') != 'operator_frozen_exploration' or p.get('scope') != 'exploratory_only'
            or p.get('scientific_support') != 'unknown'):
        raise ValueError('unsupported exploratory execution plan')
    settings = _config(store, p['candidate_ref'], p['config'])
    candidate = estimation_workflow.load_candidate(store, p['candidate_ref'])
    policy = dict(max_application_calls=1, max_integrations=2, timeout_scope='each_integration', cache=False, strict=False, reference=None)
    if (canonical_bytes(settings) != canonical_bytes(p['config']) or p['model_ref'] != candidate['model_ref']
            or set(obj['refs']) != {p['candidate_ref'], p['model_ref']}
            or p['execution_policy'] != policy):
        raise ValueError('exploration plan differs from its frozen candidate/protocol')
    if canonical_bytes(p['environment']) != canonical_bytes(application.environment_fingerprint()):
        raise ValueError('exploration environment changed; prepare a new protocol, do not rewrite history')
    return p


def plan_summary(store, ref):
    p = load_plan(store, ref)
    return dict(plan_ref=ref, profile=PROFILE, candidate_ref=p['candidate_ref'], model_ref=p['model_ref'],
        config=deepcopy(p['config']), execution_policy=deepcopy(p['execution_policy']), scope='exploratory_only',
        scientific_support='unknown', notice='Explicit new exploration; no refitting, reference score or strict paper reproduction.')


def _records(store, kind, plan_ref):
    return [r for r in store.list(kind) if store.get(r)['payload'].get('plan_ref') == plan_ref]


def _bound_application_run(store, ref, plan):
    obj = store.get(ref); p = obj['payload']; c = plan['config']
    if obj['kind'] != 'run' or p.get('mode') != 'structured_execution' or p.get('cached_from') is not None or p.get('replay_of') is not None:
        raise ValueError('exploration requires an actual uncached structured execution')
    inp = store.get(p['input_ref'])
    expected = dict(artifact=store.get(plan['model_ref'])['payload'], experiment=c['experiment'], observations=c['observations'],
        solver=c['solver'], reference=None, scales=None, threshold=c['convergence_threshold'], strict=False,
        nonnegative=c['nonnegative'], environment=plan['environment'])
    if (inp['kind'] != application.SIMULATION_INPUT_KIND or p['input_ref'] not in obj['refs']
            or canonical_bytes(inp['payload']) != canonical_bytes(expected)
            or canonical_bytes(p['environment']) != canonical_bytes(plan['environment'])
            or p.get('comparison', {}).get('status') != 'not_run'):
        raise ValueError('application run does not match the frozen exploratory input')
    simulation = from_dict(SimulationResult, p['simulation'])
    if simulation.status == 'succeeded':
        if (list(simulation.times) != c['experiment']['times'] or simulation.time_unit != c['experiment']['time_unit']
                or not {o['id'] for o in c['observations']} <= set(simulation.values)):
            raise ValueError('exploratory output grid, clock or observations differ from protocol')
    return p


def _facts(run):
    checks = run['numeric_validation']['checks']
    bad = any(c.get('status') == 'fail' and c.get('severity') == 'blocking' for c in checks)
    passed = all(any(c.get('code') == name and c.get('status') == 'pass' for c in checks) for name in ('finite', 'convergence'))
    scientific = application.derive_scientific_status(run)
    eligible = run['status'] == 'succeeded' and passed and not bad and scientific != 'failed'
    status = 'succeeded' if eligible else run['status'] if run['status'] in ('timed_out', 'unsupported') else 'failed'
    return dict(status=status, execution_status=run['status'], numeric_status='fail' if bad else 'pass' if passed else 'not_verified',
        completion_eligible=eligible, scientific_status='failed' if scientific == 'failed' else 'not_verified',
        scientific_support='unknown', scope='exploratory_only', comparison_status='not_run')


def _error_facts(error):
    return dict(status='failed', execution_status=None, numeric_status='not_run', completion_eligible=False,
        scientific_status='not_verified', scientific_support='unknown', scope='exploratory_only', comparison_status='not_run', error=error)


def execute(store, plan_ref):
    plan = load_plan(store, plan_ref); c = plan['config']
    with estimation._owner(store, plan_ref):
        previous = _records(store, RUN, plan_ref)
        if len(previous) == 1:
            load_run(store, previous[0])
            return previous[0]
        if previous or _records(store, EXECUTION, plan_ref):
            raise ValueError('interrupted or ambiguous exploration; inspect receipts, do not automatically repeat')
        reservation = store.put(EXECUTION, dict(version=1, id=uuid4().hex, plan_ref=plan_ref, phase='reserved',
            recovery_support='no_automatic_retry_unknown_result'), refs=(plan_ref,))
        started = time.monotonic(); application_ref = None
        try:
            application_ref = application.run_reproduction(store,
                from_dict(ModelArtifact, store.get(plan['model_ref'])['payload']),
                from_dict(ExperimentSpec, c['experiment']),
                tuple(from_dict(ObservationSpec, item) for item in c['observations']), from_dict(SolverConfig, c['solver']),
                reference=None, scales=None, threshold=c['convergence_threshold'], strict=False, use_cache=False, nonnegative=c['nonnegative'])
            actual = _bound_application_run(store, application_ref, plan)
            facts = _facts(actual); phase, error = 'returned', None
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:2000]
            facts = _error_facts(error); phase = 'error'
        refs = (plan_ref, reservation, *([application_ref] if application_ref else []))
        receipt = store.put(RECEIPT, dict(version=1, plan_ref=plan_ref, execution_ref=reservation, phase=phase,
            application_run_ref=application_ref, error=error), refs=refs)
        return store.put(RUN, dict(version=1, profile=PROFILE, plan_ref=plan_ref, execution_ref=reservation,
            receipt_ref=receipt, application_run_ref=application_ref, summary=facts, elapsed_s=time.monotonic()-started),
            refs=(*refs, receipt))


def load_run(store, ref):
    obj = store.get(ref); p = obj['payload']
    if obj['kind'] != RUN or type(p.get('version')) is not int or p['version'] != 1 or p.get('profile') != PROFILE:
        raise ValueError('unsupported exploratory execution result')
    plan = load_plan(store, p['plan_ref'])
    execution = store.get(p['execution_ref']); receipt = store.get(p['receipt_ref']); r = receipt['payload']
    refs = {p['plan_ref'], p['execution_ref'], *([p['application_run_ref']] if p['application_run_ref'] else [])}
    if (execution['kind'] != EXECUTION or type(execution['payload'].get('version')) is not int or execution['payload']['version'] != 1
            or type(r.get('version')) is not int or r['version'] != 1 or execution['payload'].get('phase') != 'reserved'
            or execution['payload'].get('plan_ref') != p['plan_ref'] or p['plan_ref'] not in execution['refs']
            or receipt['kind'] != RECEIPT or r.get('plan_ref') != p['plan_ref'] or r.get('execution_ref') != p['execution_ref']
            or r.get('application_run_ref') != p['application_run_ref'] or set(receipt['refs']) != refs
            or set(obj['refs']) != refs | {p['receipt_ref']}):
        raise ValueError('exploration plan/reservation/receipt/result binding mismatch')
    if r['phase'] == 'returned' and p['application_run_ref'] and r['error'] is None:
        expected = _facts(_bound_application_run(store, p['application_run_ref'], plan))
    elif r['phase'] == 'error' and isinstance(r['error'], str) and r['error']:
        expected = _error_facts(r['error'])
    else:
        raise ValueError('unconfirmed exploratory return')
    if canonical_bytes(p['summary']) != canonical_bytes(expected) or not math.isfinite(p['elapsed_s']) or p['elapsed_s'] < 0:
        raise ValueError('exploratory summary differs from stored numerical facts')
    return p


def result_summary(store, ref):
    p = load_run(store, ref); plan = load_plan(store, p['plan_ref'])
    candidate = estimation_workflow.load_candidate(store, plan['candidate_ref'])
    original = estimation_workflow.check_result(store, candidate['result_ref'])
    unresolved = list(original['unresolved'])
    if plan['config']['experiment']['time_unit'] == 'unknown' and 'time_unit_unknown' not in unresolved:
        unresolved.append('time_unit_unknown')
    if any(o['unit'] == 'unknown' for o in plan['config']['observations']):
        unresolved.append('exploratory_observation_units_unknown')
    if p['application_run_ref'] and p['summary'].get('error') is None:
        if store.get(p['application_run_ref'])['payload']['source_validation']['units_unknown']:
            unresolved.append('execution_source_units_unknown')
    return dict(run_ref=ref, plan_ref=p['plan_ref'], application_run_ref=p['application_run_ref'],
        candidate_ref=plan['candidate_ref'], model_ref=plan['model_ref'], review_ref=candidate['review_ref'],
        estimation_result_ref=candidate['result_ref'], estimated_parameters=deepcopy(candidate['estimated_parameters']),
        unresolved=unresolved, elapsed_s=p['elapsed_s'], execution_policy=deepcopy(plan['execution_policy']), **deepcopy(p['summary']))


def status(store, plan_ref):
    if store.get(plan_ref)['kind'] != PLAN:
        raise ValueError('expected exploration plan')
    executions = _records(store, EXECUTION, plan_ref); runs = _records(store, RUN, plan_ref)
    state = 'prepared' if not executions else 'reserved_outcome_unknown_do_not_retry'
    if len(runs) == 1: state = 'result_recorded'
    if len(runs) > 1 or len(executions) > 1: state = 'ambiguous_do_not_retry'
    return dict(plan_ref=plan_ref, state=state, execution_refs=executions,
        receipt_refs=_records(store, RECEIPT, plan_ref), run_refs=runs,
        notice='Local records only. A reservation does not prove an active worker or a completed integration.')


def report_lines(store, ref):
    from .reporting import _model_summary
    p = load_run(store, ref); summary = result_summary(store, ref); plan = load_plan(store, p['plan_ref'])
    candidate = estimation_workflow.load_candidate(store, plan['candidate_ref'])
    estimate_plan = estimation.load_plan(store, candidate['plan_ref'])
    review = store.get(candidate['review_ref'])['payload']
    c = plan['config']
    def cell(value): return str(value).replace('\\', '\\\\').replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = ['Scope: **exploratory_only**; fitted parameters are not literature-reported values.',
        f"Execution: **{summary['execution_status']}**; numerical result: **{summary['numeric_status']}**",
        f"Completion eligible: **{summary['completion_eligible']}**; scientific status: **{summary['scientific_status']}**",
        'Scientific support: **unknown**. Reference comparison: **not_run**. No strict paper reproduction claim.', '',
        f"Exploration plan: `{p['plan_ref']}`", f"Candidate: `{plan['candidate_ref']}`",
        f"Estimation plan: `{candidate['plan_ref']}`", f"Training-fit result: `{candidate['result_ref']}`",
        f"Review: `{candidate['review_ref']}` ({review['decision']}; attribution is not identity verification)",
        f"Original model: `{candidate['parent_model_ref']}`", '',
        '## Fitted parameter provenance', '',
        '| Parameter | Fitted value | Declared unit | Origin | Training bound | Endpoint spread (not CI) |',
        '|---|---:|---|---|---|---|']
    for name, value in summary['estimated_parameters'].items():
        lines.append('| ' + ' | '.join(cell(v) for v in (name, value['value'], value['unit'], value['origin'], value['bounds'], value['endpoint_spread'])) + ' |')
    lines.extend(['', 'Declared context (no inferred transfer): `'+cell(json_text(c['context']))+'`',
        'Changed numerical evidence links removed: `'+cell(json_text(candidate['removed_value_bindings']))+'`',
        'SBML units remain as supplied; declared fitted units do not establish dimensional correctness.', '',
        'Training data lineage (source spans bind the input, not its scientific correctness):', ''])
    for row in estimate_plan['config']['training']:
        loc = row['provenance']
        lines.append(f"- {cell(row['id'])}: {loc['origin']}; source `{loc['source_ref']}`, page {loc['page']}, characters {loc['start']}:{loc['end']}.")
        lines.append('  Sigma declaration: '+cell(loc['sigma_origin']))
    lines.extend(['', 'Unresolved scientific items:', *['- '+cell(item) for item in summary['unresolved']], '',
        '## Frozen exploratory settings', '', '```json', json_text(c, indent=2), '```',
        'Budget scope: one application call, at most two integrations (coarse and refined); timeout applies to each simulation worker.',
        'max_steps is the native integrator setting, not a cumulative task step quota.',
        'Preflight, I/O and the complete process do not have an OS-level whole-task deadline. Cache is disabled.',
        'Initial/input overrides are explicit exploratory settings, not newly established source facts.'])
    if p['application_run_ref'] and p['summary'].get('error') is None:
        run = store.get(p['application_run_ref'])['payload']
        lines.extend(_model_summary(store.get(plan['model_ref'])['payload']))
        lines.extend(['', '## Numerical execution', '', f"Application run: `{p['application_run_ref']}`"])
        for key in ('static_validation', 'numeric_validation'):
            lines.append(key+':')
            lines.extend('- '+cell(check['code'])+': **'+cell(check['status'])+'**; '+cell(check['message']) for check in run[key]['checks'])
        source = run['source_validation']
        lines.extend(['', 'Missing source targets (fitted numerical inputs remain estimated): `'+cell(json_text(source['missing']))+'`',
            'Trajectory preview (first 20 samples; full values are in the stored application run):', ''])
        values = run['simulation']['values']; times = run['simulation']['times']; columns = sorted(values)[:12]
        if times and columns:
            lines.extend(['| time ('+cell(c['experiment']['time_unit'])+') | '+' | '.join(cell(n) for n in columns)+' |',
                          '|---|'+'---:|'*len(columns)])
            lines.extend('| '+str(t)+' | '+' | '.join(str(values[n][i]) for n in columns)+' |' for i, t in enumerate(times[:20]))
    elif p['summary'].get('error'):
        lines.extend(['', 'Execution diagnostic: '+cell(p['summary']['error']), 'No confirmed matching numerical return; inspect the execution receipt.'])
    return lines


def json_text(value, **kwargs):
    import json
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, **kwargs)
