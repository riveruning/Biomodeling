"""Explicit execution of a compiled decision plan, with immutable receipts.

One reservation per frozen task in a local store. A confirmed returned receipt
can be adopted without recomputation; an unknown effect is never retried.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time
from uuid import uuid4

from . import application, modeling_plan as plans, time_units, validation
from .domain.contracts import ExperimentSpec, ObservationSpec, SimulationResult, SolverConfig
from .domain.serialization import from_dict, to_dict
from .model_contracts import fields, number
from .recovery import execution_owner
from .storage import canonical_bytes

PROTOCOL = 'modeling_plan_execution_v1'
EXECUTION = 'modeling_plan_execution'
RECEIPT = 'modeling_plan_receipt'
RUN = 'modeling_plan_run'


def _config(store, compilation_ref, config):
    fields(config, {'experiment'}, {'solver', 'observations', 'reference', 'scales', 'threshold', 'nonnegative'})
    plan, _, artifact = plans.compiled_model(store, compilation_ref)
    experiment = from_dict(ExperimentSpec, config['experiment'])
    if (not 2 <= len(experiment.times) <= 501 or experiment.times[0] < 0 or experiment.times[-1] > 10000
            or experiment.initial_overrides or experiment.parameter_overrides or experiment.segments):
        raise ValueError('execution needs 2..501 samples in 0..10000 and cannot override compiled mathematical choices')
    solver = from_dict(SolverConfig, config.get('solver', dict(timeout_s=15.)))
    number(solver.timeout_s, .1, 60., 'per-integration timeout_s')
    if solver.max_steps > 1000000 or solver.rtol / 10 <= 0 or solver.atol / 10 <= 0:
        raise ValueError('solver settings outside bounded profile')
    clock = time_units.check_experiment_time(artifact.sbml, experiment.time_unit)
    if clock.status == 'fail':
        raise ValueError(clock.message)
    raw_observations = config.get('observations', [])
    if not isinstance(raw_observations, list) or len(raw_observations) > 8:
        raise ValueError('at most eight explicit observations are supported')
    observations = tuple(from_dict(ObservationSpec, item) for item in raw_observations)
    # Reuse the existing bounded mapping preflight, without performing prediction.
    from .fitting import validate_prediction_inputs
    if observations:
        validate_prediction_inputs(artifact, experiment, observations, {})
    selected = next(c for c in plan['spec']['candidates'] if c['id'] == plan['spec']['selected_candidate'])
    names = {s['id'] for s in selected['draft']['states']}
    reference = from_dict(SimulationResult, config['reference']) if config.get('reference') is not None else None
    if reference is not None:
        validation.validate_reference(reference)
        time_units.comparison_time_unit(experiment.time_unit, reference.time_unit)
        if tuple(reference.times) != experiment.times:
            raise ValueError('reference must use the frozen experiment grid')
        if set(reference.values) != names | {o.id for o in observations}:
            raise ValueError('reference columns must match all frozen state and observation outputs')
    scales = validation.validate_scales(config.get('scales'), reference.values if reference is not None else ())
    if reference is not None and scales is None:
        raise ValueError('reference comparison requires explicit fixed scales')
    if reference is None and scales is not None:
        raise ValueError('fixed comparison scales require an explicit reference')
    threshold = number(config.get('threshold', .001), 1e-12, 1., 'threshold')
    nonnegative = config.get('nonnegative', [])
    if (not isinstance(nonnegative, list) or any(not isinstance(n, str) or n not in names for n in nonnegative)
            or len(set(nonnegative)) != len(nonnegative)):
        raise ValueError('nonnegative must name distinct compiled states')
    return dict(experiment=to_dict(experiment), solver=to_dict(solver), observations=[to_dict(o) for o in observations],
        reference=to_dict(reference) if reference is not None else None, scales=scales,
        threshold=threshold, nonnegative=list(nonnegative))


def prepare(store, compilation_ref, config, *, replay_of=None):
    normalized = _config(store, compilation_ref, config)
    if replay_of is not None:
        prior = load_run(store, replay_of)
        old = load_task(store, prior['task_ref'])
        if old['compilation_ref'] != compilation_ref or canonical_bytes(old['config']) != canonical_bytes(normalized):
            raise ValueError('replay must retain the compiled candidate and execution configuration')
    return store.put('agent_task', dict(version=1, protocol=PROTOCOL, id=uuid4().hex,
        mode='modeling_plan_execution', compilation_ref=compilation_ref, config=normalized,
        environment=application.environment_fingerprint(), max_runs=1, replay_of=replay_of,
        recovery_support='confirmed_receipt_adoption_only; unknown effects block retry'),
        refs=(compilation_ref, *([replay_of] if replay_of else [])))


def load_task(store, ref):
    obj = store.get(ref); p = obj['payload']
    fields(p, {'version', 'protocol', 'id', 'mode', 'compilation_ref', 'config', 'environment',
               'max_runs', 'replay_of', 'recovery_support'})
    if (obj['kind'] != 'agent_task' or type(p['version']) is not int or p['version'] != 1
            or p['protocol'] != PROTOCOL or p['mode'] != 'modeling_plan_execution'
            or type(p['max_runs']) is not int or p['max_runs'] != 1
            or p['recovery_support'] != 'confirmed_receipt_adoption_only; unknown effects block retry'):
        raise ValueError('unsupported frozen modeling task')
    expected_refs = {p['compilation_ref'], *([p['replay_of']] if p['replay_of'] else [])}
    if set(obj['refs']) != expected_refs or canonical_bytes(_config(store, p['compilation_ref'], p['config'])) != canonical_bytes(p['config']):
        raise ValueError('task differs from its compiled protocol')
    if canonical_bytes(p['environment']) != canonical_bytes(application.environment_fingerprint()):
        raise ValueError('execution environment changed; prepare a new task explicitly')
    if p['replay_of'] is not None:
        previous = load_run(store, p['replay_of'])
        original_task = load_task(store, previous['task_ref'])
        if (original_task['compilation_ref'] != p['compilation_ref']
                or canonical_bytes(original_task['config']) != canonical_bytes(p['config'])):
            raise ValueError('replay lineage differs from frozen mathematical/execution choices')
    return p


def _records(store, kind, task_ref):
    # The mutable index is not the authority for consumed execution budgets.
    from .storage import REF_RE
    refs = []
    for path in store.objects_dir.glob('*.json'):
        if REF_RE.fullmatch(path.stem):
            record = store._read_envelope(path.stem)
            if record['kind'] == kind and record['payload'].get('task_ref') == task_ref:
                store.get(path.stem)
                refs.append(path.stem)
    return sorted(refs)


def _bound_run(store, ref, task):
    obj = store.get(ref); p = obj['payload']; config = task['config']
    plan, _, artifact = plans.compiled_model(store, task['compilation_ref'])
    strict = plan['spec']['mode'] == 'reproduction'
    expected = dict(artifact=to_dict(artifact), **deepcopy(config), strict=strict, environment=task['environment'])
    inp = store.get(p['input_ref'])
    if (obj['kind'] != 'run' or inp['kind'] != application.SIMULATION_INPUT_KIND or p['input_ref'] not in obj['refs']
            or canonical_bytes(inp['payload']) != canonical_bytes(expected)
            or p.get('cached_from') is not None or p.get('replay_of') is not None
            or p.get('mode') != ('strict_reproduction' if strict else 'structured_execution')
            or canonical_bytes(p.get('environment')) != canonical_bytes(task['environment'])):
        raise ValueError('returned application run differs from frozen modeling task')
    simulation = from_dict(SimulationResult, p['simulation'])
    if simulation.status != p['status']:
        raise ValueError('application status differs from simulation')
    if simulation.status == 'succeeded' and (list(simulation.times) != config['experiment']['times']
            or simulation.time_unit != config['experiment']['time_unit']):
        raise ValueError('application grid or clock differs from frozen task')
    return p


def _facts(record, reference_requested):
    checks = record.get('numeric_validation', {}).get('checks', [])
    blocked = bool(application.blocking_verdict_basis(record))
    numeric_failed = any(c.get('status') == 'fail' and c.get('severity') == 'blocking' for c in checks)
    numerical_pass = all(any(c.get('code') == code and c.get('status') == 'pass' for c in checks)
                         for code in ('finite', 'convergence'))
    comparison = record.get('comparison', {}).get('status', 'not_run')
    expected_comparison = record['mode'] == 'strict_reproduction'
    scientific = application.derive_scientific_status(record)
    eligible = (record['status'] == 'succeeded' and numerical_pass and not blocked and scientific != 'failed'
                and (not reference_requested or comparison == 'pass'))
    return dict(execution_status=record['status'], numeric_status='fail' if numeric_failed else 'pass' if numerical_pass else 'unknown',
        comparison_status=comparison, completion_eligible=eligible,
        scientific_status=scientific, scientific_support='unknown',
        notice='Execution eligibility is not independent scientific verification.', strict_source_gate=expected_comparison,
        reference_requested=reference_requested)


def _receipt(store, ref, task_ref, task):
    obj = store.get(ref); p = obj['payload']
    fields(p, {'version', 'task_ref', 'execution_ref', 'application_run_ref', 'phase', 'error', 'elapsed_s'})
    if obj['kind'] != RECEIPT or type(p['version']) is not int or p['version'] != 1 or p['task_ref'] != task_ref:
        raise ValueError('invalid modeling receipt')
    reservation = store.get(p['execution_ref'])
    if (reservation['kind'] != EXECUTION or type(reservation['payload'].get('version')) is not int
            or reservation['payload']['version'] != 1 or reservation['payload'].get('task_ref') != task_ref
            or reservation['payload'].get('phase') != 'reserved' or set(reservation['refs']) != {task_ref}):
        raise ValueError('reservation/task mismatch')
    expected_refs = {task_ref, p['execution_ref'], *([p['application_run_ref']] if p['application_run_ref'] else [])}
    if set(obj['refs']) != expected_refs:
        raise ValueError('receipt closure mismatch')
    if type(p['elapsed_s']) not in (int, float) or not math.isfinite(p['elapsed_s']) or p['elapsed_s'] < 0:
        raise ValueError('elapsed_s must be an actual nonnegative finite measurement')
    if p['phase'] == 'returned' and p['application_run_ref'] and p['error'] is None:
        facts = _facts(_bound_run(store, p['application_run_ref'], task), task['config']['reference'] is not None)
    elif p['phase'] == 'error' and p['application_run_ref'] is None and isinstance(p['error'], str) and p['error']:
        facts = dict(execution_status='unconfirmed_error', numeric_status='unknown', comparison_status='unknown',
            completion_eligible=False, scientific_status='not_verified', scientific_support='unknown', error=p['error'],
            notice='No confirmed application return; execution effects may have occurred. Do not automatically retry.')
    else:
        raise ValueError('unconfirmed execution receipt')
    return p, facts


def _events(receipt, facts, task, receipt_ref):
    return [dict(type='compilation', state='ready', compilation_ref=task['compilation_ref']),
        dict(type='execution_returned' if receipt['phase'] == 'returned' else 'tool_error',
            tool='run_compiled_plan', outcome=facts, error=receipt['error'],
            application_run_ref=receipt['application_run_ref'], receipt_ref=receipt_ref)]


def _finish(store, receipt_ref, task_ref, task):
    receipt, facts = _receipt(store, receipt_ref, task_ref, task)
    state = 'completed' if facts['completion_eligible'] else 'failed'
    return store.put(RUN, dict(version=1, protocol=PROTOCOL, task_ref=task_ref,
        compilation_ref=task['compilation_ref'], execution_ref=receipt['execution_ref'], receipt_ref=receipt_ref,
        application_run_ref=receipt['application_run_ref'], state=state, summary=facts,
        elapsed_s=receipt['elapsed_s'], events=_events(receipt, facts, task, receipt_ref),
        call_count=0, run_count=1, scientific_support='unknown'),
        refs=(task_ref, receipt_ref, receipt['execution_ref'], *([receipt['application_run_ref']] if receipt['application_run_ref'] else [])))


def execute(store, task_ref):
    task = load_task(store, task_ref)
    with execution_owner(store, task_ref):
        previous = _records(store, RUN, task_ref)
        if len(previous) == 1:
            load_run(store, previous[0]); return previous[0]
        reservations = _records(store, EXECUTION, task_ref); receipts = _records(store, RECEIPT, task_ref)
        if previous or len(reservations) > 1 or len(receipts) > 1:
            raise ValueError('ambiguous execution history; do not retry')
        if len(receipts) == 1 and len(reservations) == 1:
            if store.get(receipts[0])['payload'].get('execution_ref') != reservations[0]:
                raise ValueError('returned receipt is not bound to the reserved execution')
            return _finish(store, receipts[0], task_ref, task)
        if reservations or receipts:
            raise ValueError('reserved outcome unknown; inspect receipts, do not retry')
        execution_ref = store.put(EXECUTION, dict(version=1, id=uuid4().hex, task_ref=task_ref, phase='reserved'), refs=(task_ref,))
        started = time.monotonic(); application_ref = None
        try:
            plan, _, artifact = plans.compiled_model(store, task['compilation_ref']); c = task['config']
            application_ref = application.run_reproduction(store, artifact,
                from_dict(ExperimentSpec, c['experiment']),
                tuple(from_dict(ObservationSpec, o) for o in c['observations']), from_dict(SolverConfig, c['solver']),
                reference=from_dict(SimulationResult, c['reference']) if c['reference'] is not None else None,
                scales=c['scales'], threshold=c['threshold'], strict=plan['spec']['mode'] == 'reproduction',
                use_cache=False, nonnegative=c['nonnegative'])
            phase, error = 'returned', None
        except Exception as exc:
            if application_ref is not None:
                raise  # A partially observed return is not a confirmed zero-effect error.
            phase, error = 'error', f'{type(exc).__name__}: {exc}'[:2000]
        receipt_ref = store.put(RECEIPT, dict(version=1, task_ref=task_ref, execution_ref=execution_ref,
            application_run_ref=application_ref, phase=phase, error=error, elapsed_s=time.monotonic()-started),
            refs=(task_ref, execution_ref, *([application_ref] if application_ref else [])))
        return _finish(store, receipt_ref, task_ref, task)


def load_run(store, ref):
    obj = store.get(ref); p = obj['payload']
    if obj['kind'] != RUN or type(p.get('version')) is not int or p['version'] != 1 or p.get('protocol') != PROTOCOL:
        raise ValueError('unsupported modeling-plan run')
    task = load_task(store, p['task_ref'])
    receipt, facts = _receipt(store, p['receipt_ref'], p['task_ref'], task)
    expected_refs = {p['task_ref'], p['receipt_ref'], receipt['execution_ref'],
                     *([receipt['application_run_ref']] if receipt['application_run_ref'] else [])}
    if (set(obj['refs']) != expected_refs or p.get('execution_ref') != receipt['execution_ref']
            or p.get('application_run_ref') != receipt['application_run_ref']
            or p.get('compilation_ref') != task['compilation_ref'] or p.get('summary') != facts
            or p.get('state') != ('completed' if facts['completion_eligible'] else 'failed')
            or p.get('elapsed_s') != receipt['elapsed_s']
            or type(p.get('call_count')) is not int or p['call_count'] != 0
            or type(p.get('run_count')) is not int or p['run_count'] != 1
            or p.get('events') != _events(receipt, facts, task, p['receipt_ref'])
            or p.get('scientific_support') != 'unknown'):
        raise ValueError('run differs from frozen task and verified receipt')
    return p


def summary(store, ref):
    p = load_run(store, ref)
    plan, compilation, _ = plans.compiled_model(store, p['compilation_ref'])
    return dict(run_ref=ref, task_ref=p['task_ref'], plan_ref=compilation['plan_ref'], mode=plan['spec']['mode'],
        selected_candidate=plan['spec']['selected_candidate'], application_run_ref=p['application_run_ref'],
        state=p['state'], elapsed_s=p['elapsed_s'], assumptions=deepcopy(compilation['assumptions']), **deepcopy(p['summary']))


def status(store, task_ref):
    task = load_task(store, task_ref)
    executions = _records(store, EXECUTION, task_ref); receipts = _records(store, RECEIPT, task_ref); runs = _records(store, RUN, task_ref)
    state = 'prepared'
    if executions:
        state = 'reserved_outcome_unknown_do_not_retry'
    if len(receipts) == 1 and len(executions) == 1:
        _receipt(store, receipts[0], task_ref, task); state = 'confirmed_receipt_available'
    if len(runs) == 1:
        load_run(store, runs[0]); state = 'result_recorded'
    if len(executions) > 1 or len(receipts) > 1 or len(runs) > 1:
        state = 'ambiguous_do_not_retry'
    return dict(task_ref=task_ref, state=state, execution_refs=executions, receipt_refs=receipts, run_refs=runs,
        compilation_ref=task['compilation_ref'], budget_scope='one task in this local store; not cross-store global quota')


def replay(store, ref):
    p = load_run(store, ref); task = load_task(store, p['task_ref'])
    return execute(store, prepare(store, task['compilation_ref'], task['config'], replay_of=ref))


def report_lines(store, ref):
    from .timeline import cell
    obj = store.get(ref); root = obj['payload']
    if obj['kind'] == RUN:
        result = summary(store, ref); compilation_ref = root['compilation_ref']
        compilation = store.get(compilation_ref)['payload']; plan_ref = compilation['plan_ref']
    elif obj['kind'] == plans.COMPILATION:
        compilation_ref = ref; compilation = root; plan_ref = root['plan_ref']; result = None
    elif obj['kind'] == plans.PLAN:
        compilation_ref = None; compilation = None; plan_ref = ref; result = None
    else:
        raise ValueError('unsupported modeling-plan report')
    p = plans.load(store, plan_ref); spec = p['spec']; view = plans.inspect(store, plan_ref)
    lines = ['## Explicit modeling decisions', '', f'Mode: **{cell(spec["mode"])}**',
        f'Plan: `{plan_ref}`; revision: {p["revision"]}; readiness: {view["state"]}',
        f'Selected candidate: {cell(spec["selected_candidate"])}',
        'Scientific support: **unknown**. Mechanisms and interpretations remain unverified; source binding is a bounded check.',
        'Conditions are explicit declarations, not an automated biological applicability review.', '']
    if result:
        lines.extend([f'Execution: **{result["state"]}**; numerical checks: {result["numeric_status"]}; reference: {result["comparison_status"]}',
            f'Elapsed execution time: {result["elapsed_s"]:.3f} s; provider calls: 0.',
            'The timeout applies to each integration, not the entire research task.', ''])
    for candidate in spec['candidates']:
        from .bindings import draft_targets
        from .domain.contracts import ModelDraft
        values_by_target = draft_targets(from_dict(ModelDraft, candidate['draft']))
        lines.extend([f'### Candidate: {cell(candidate["id"])}', '',
            '| Target | Proposed value / expression | Origin | Evidence | Mechanisms | Rationale / alternatives |',
            '|---|---|---|---|---|---|'])
        for d in candidate['decisions']:
            proposed = values_by_target[d['target']]
            values = [d['target'], 'missing' if proposed is None else proposed,
                      d['origin'], ', '.join(d['evidence_ids']), ', '.join(d['mechanism_ids']),
                      d['rationale'] + (' Alternatives: ' + '; '.join(d['alternatives']) if d['alternatives'] else '')]
            lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
        lines.append('')
    for anchor in spec['evidence']:
        lines.append(f'- {cell(anchor["id"])}: source `{anchor["source_ref"]}`, page {anchor["page"]}, offsets [{anchor["start"]}, {anchor["end"]}): {cell(anchor["quote"])}')
    lines.extend(['', '### Remaining issues', ''])
    lines.extend('- ' + cell(i['question']) for i in view['issues'])
    if not view['issues']:
        lines.append('- No structural readiness issue; independent scientific review remains pending.')
    if compilation is not None:
        lines.extend('- Draft gate: ' + cell(i['question']) for i in compilation.get('issues', []))
    if result and root['application_run_ref']:
        from .reporting import render_report
        lines.extend(['', '## Actual application report', '', render_report(store, root['application_run_ref'])])
    return lines
