"""Explicit recovery of verified invocation traces; no unknown-effect retry."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform

from .domain.contracts import ModelArtifact
from .domain.serialization import from_dict
from .invocations import InvocationJournal, REPLAY_CLASSES
from .storage import REF_RE, ArtifactStore, canonical_bytes


class RecoveryBlocked(ValueError):
    """A recovery precondition has not been established."""


def open_readonly_store(root):
    """Existing artifact view without mkdir, SQLite pragmas or index repairs."""
    view = ArtifactStore.__new__(ArtifactStore)
    view.root = Path(root)
    view.objects_dir = view.root / 'objects'
    view.blobs_dir = view.root / 'blobs'
    view.index_path = view.root / 'index.sqlite'
    if not view.objects_dir.is_dir() or not view.blobs_dir.is_dir():
        raise RecoveryBlocked('recovery diagnosis requires an existing artifact store')
    return view


def implementation_fingerprint() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        digest.update(canonical_bytes([str(path.relative_to(root)), path.read_bytes().hex()]))
    versions = {'python': platform.python_version()}
    for name in ('numpy', 'scipy', 'python-libsbml', 'libroadrunner'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'unavailable'
    digest.update(canonical_bytes(versions))
    return digest.hexdigest()


@contextmanager
def execution_owner(store, task_ref):
    if not isinstance(task_ref, str) or not REF_RE.fullmatch(task_ref):
        raise RecoveryBlocked('invalid task reference for ownership')
    if store.get(task_ref)['kind'] != 'agent_task':
        raise RecoveryBlocked('owner reference must be an agent_task')
    from . import local_ownership
    directory = store.root / '.agent-owners'
    directory.mkdir(mode=0o700, exist_ok=True)
    if local_ownership.is_redirect(directory):
        raise RecoveryBlocked('owner directory must not be a symlink')
    try:fd=local_ownership.acquire(directory/(task_ref+'.lock'))
    except local_ownership.OwnerBusy as exc:raise RecoveryBlocked('task already has an active execution owner') from exc
    try:
        yield
    finally:
        os.close(fd)  # close releases flock even during BaseException


def _count(value, limit):
    return type(value) is int and 0 <= value <= limit


def _inventory(store):
    """Read object files, including objects landed before their index commit."""
    records = {}
    for path in sorted(store.objects_dir.glob('*.json')):
        if REF_RE.fullmatch(path.stem):
            # Hash/schema validation for all files; transitive verification is
            # done for relevant records below. Never repair the index here.
            records[path.stem] = store._read_envelope(path.stem)
    return records


def _initial_scope(store, execution, task):
    if task.get('mode') == 'native_typed':
        from .typed_agent_runtime import validate_initial_scope
        validate_initial_scope(store, execution, task)
        return
    if task.get('mode') == 'fba':
        from .fba_agent_runtime import validate_initial_scope
        validate_initial_scope(store, execution, task)
        return
    meta = execution.get('recovery')
    if not isinstance(meta, dict) or type(meta.get('version')) is not int or meta['version'] != 1:
        raise RecoveryBlocked('legacy_or_incompatible_recovery_snapshot')
    if meta.get('implementation_sha256') != implementation_fingerprint():
        raise RecoveryBlocked('implementation_fingerprint_mismatch')
    sources = meta.get('source_refs')
    if not isinstance(sources, list):
        raise RecoveryBlocked('invalid_initial_source_scope')
    for ref in sources:
        if store.get(ref)['kind'] != 'source':
            raise RecoveryBlocked('initial_scope_contains_non_source')
    restored = meta.get('restored')
    parent_ref = execution.get('parent_run_ref')
    if parent_ref is None:
        if restored is not None or sources != task['source_refs']:
            raise RecoveryBlocked('initial_scope_does_not_match_task')
        if execution['base_call_count'] or execution['base_run_count']:
            raise RecoveryBlocked('initial_execution_has_inherited_budget')
        return
    if not isinstance(restored, dict):
        raise RecoveryBlocked('resumed_execution_missing_initial_snapshot')
    parent = store.get(parent_ref)
    previous = parent['payload']
    if (parent['kind'] != 'agent_run' or previous.get('task_ref') != execution['task_ref']
            or previous.get('state') != 'awaiting_input'):
        raise RecoveryBlocked('invalid_resume_parent')
    cp_record = store.get(previous['checkpoint_ref'])
    cp = cp_record['payload']
    if cp_record['kind'] != 'agent_checkpoint' or cp.get('task_ref') != execution['task_ref']:
        raise RecoveryBlocked('invalid_resume_parent_checkpoint')
    clarification_refs = restored.get('clarification_refs', [])
    old_clarifications = previous.get('clarification_refs', [])
    if clarification_refs[:-1] != old_clarifications or len(clarification_refs) != len(old_clarifications) + 1:
        raise RecoveryBlocked('invalid_initial_clarification_lineage')
    clarification_ref = clarification_refs[-1]
    record = store.get(clarification_ref)
    clarification = record['payload']
    if (record['kind'] != 'researcher_clarification' or clarification.get('parent_run_ref') != parent_ref
            or clarification.get('checkpoint_ref') != previous['checkpoint_ref']):
        raise RecoveryBlocked('invalid_initial_clarification')
    if len(sources) != len(cp['source_refs']) + 1 or sources[:-1] != cp['source_refs']:
        raise RecoveryBlocked('invalid_resumed_source_scope')
    source_ref = sources[-1]
    source = store.get(source_ref)['payload']
    if source.get('clarification_ref') != clarification_ref:
        raise RecoveryBlocked('clarification_source_mismatch')
    from .agent import _reply_message
    pending = [e for e in cp['events'] if e.get('type') == 'awaiting_input']
    expected = deepcopy(cp)
    expected['events'].append({'type': 'researcher_reply', 'call': cp['call_count'],
        'parent_run_ref': parent_ref, 'respondent': clarification['respondent'],
        'source_ref': source_ref, 'artifact_refs': [clarification_ref, source_ref]})
    expected['messages'].append(_reply_message(pending[-1]['question'], clarification['respondent'],
                                               source_ref, clarification['reply']))
    expected['checkpoint_refs'] = previous['checkpoint_refs']
    expected['clarification_refs'] = clarification_refs
    if restored != expected:
        raise RecoveryBlocked('initial_resume_snapshot_mismatch')
    if (restored.get('call_count') != execution['base_call_count']
            or restored.get('run_count') != execution['base_run_count']):
        raise RecoveryBlocked('initial_resume_budget_mismatch')


def _returned(store, operation, adoptable, task):
    reserved, terminal = operation['reserved'], operation['terminal']
    name, details = reserved['name'], terminal['details']
    envelope = store.get(operation['terminal_ref'])
    if not isinstance(details, dict):
        raise RecoveryBlocked('returned_details_must_be_object')
    if name == 'respond':
        response = json.loads(details['response_json'])
        if not isinstance(response, dict):
            raise RecoveryBlocked('invalid_cached_response')
    elif name in ('submit_draft', 'submit_plan'):
        required = {'state', 'problems', 'checks', 'draft_ref', 'model_ref', 'hypothesis_ref',
                    'source_review_ref', 'source_review', 'citation_diagnostics'}
        if not required <= details.keys():
            raise RecoveryBlocked('incomplete_draft_return_snapshot')
        for key, kind in (('model_ref', 'model'), ('draft_ref', 'model_draft'),
                          ('hypothesis_ref', 'hypothesis'), ('source_review_ref', 'source_review')):
            ref = details.get(key)
            if ref and (ref not in envelope['refs'] or store.get(ref)['kind'] != kind):
                raise RecoveryBlocked('invalid_draft_result_binding')
        if details['state'] == 'ready' and (not details['model_ref'] or not details['draft_ref']):
            raise RecoveryBlocked('ready_draft_missing_model')
        if name == 'submit_plan':
            from . import native_modeling
            task_ref = store.get(reserved['execution_ref'])['payload']['task_ref']
            scope = reserved['details']
            frozen_sources = store.get(reserved['execution_ref'])['payload']['recovery']['source_refs']
            if scope.get('source_refs') != frozen_sources or not set(native_modeling.refs(details)) <= set(envelope['refs']):
                raise RecoveryBlocked('invalid_decision_submission_scope')
            native_modeling.verify_return(store, task_ref, scope, details)
    elif name == 'request_estimation':
        from . import estimation_workflow as workflow
        ref = details.get('request_ref'); obj = store.get(ref)
        task_ref = store.get(reserved['execution_ref'])['payload']['task_ref']
        expected = workflow.request_payload(store, task_ref, reserved['details']['source_refs'], reserved['details']['args'])
        if (ref not in envelope['refs'] or obj['kind'] != workflow.REQUEST
                or canonical_bytes(obj['payload']) != canonical_bytes(expected)
                or not {task_ref, *expected['source_refs']} <= set(obj['refs'])):
            raise RecoveryBlocked('invalid_estimation_request_binding')
    elif name == 'run_estimation':
        from . import estimation_workflow as workflow
        ref = details.get('result_ref'); plan_ref = reserved['details'].get('plan_ref')
        if not plan_ref or ref not in envelope['refs'] or task.get('estimation_plan_ref') != plan_ref:
            raise RecoveryBlocked('invalid_estimation_result_binding')
        workflow.check_result(store, ref, plan_ref=plan_ref)
        adoptable.append(ref)
    elif name in ('run_typed','inspect_typed'):
        from . import typed_agent_runtime as runtime
        if task.get('mode')!='native_typed' or task.get('typed_protocol')!=runtime.PROFILE:
            raise RecoveryBlocked('typed_operation_outside_recoverable_native_task')
        if name=='run_typed':
            ref=details.get('result_ref')
            if ref not in envelope['refs'] or reserved['details']!=dict(input_ref=task['input_ref']):
                raise RecoveryBlocked('invalid_typed_scientific_return_binding')
            runtime.bound_result(store,task,ref)
            adoptable.append(ref)
        elif canonical_bytes(details)!=canonical_bytes(runtime.inspect(store,task,reserved['details'].get('run_ref'))):
            raise RecoveryBlocked('invalid_typed_inspect_return')
    elif name in ('run_fba', 'inspect_fba'):
        from . import fba_agent_runtime as runtime
        if task.get('mode') != 'fba' or task.get('fba_protocol') != runtime.PROFILE:
            raise RecoveryBlocked('fba_operation_not_supported_in_ode_recovery')
        if name == 'run_fba':
            ref = details.get('result_ref')
            if (ref not in envelope['refs'] or reserved['details'] != dict(model_ref=task['model_ref'], settings=task['settings'])):
                raise RecoveryBlocked('invalid_fba_scientific_return_binding')
            runtime.bound_result(store, task, ref)
            adoptable.append(ref)
        else:
            ref = reserved['details'].get('run_ref')
            if canonical_bytes(details) != canonical_bytes(runtime.inspect(store, task, ref)):
                raise RecoveryBlocked('invalid_fba_inspect_return')
    elif name in ('run', 'run_study', 'run_fba'):
        ref = details['result_ref']
        expected_kind = 'run' if name == 'run' else 'hypothesis_study'
        if ref not in envelope['refs'] or store.get(ref)['kind'] != expected_kind:
            raise RecoveryBlocked('invalid_scientific_result_binding')
        if name == 'run':
            for key, kind in (('model_ref', 'model'), ('draft_ref', 'model_draft')):
                if store.get(reserved['details'][key])['kind'] != kind:
                    raise RecoveryBlocked('invalid_run_input_binding')
            model_ref, draft_ref = (reserved['details'][key] for key in ('model_ref', 'draft_ref'))
            model = store.get(model_ref)
            if draft_ref not in model['refs']:
                raise RecoveryBlocked('run_model_draft_mismatch')
            run = store.get(ref)
            input_ref = run['payload']['input_ref']
            inputs = store.get(input_ref)
            frozen = inputs['payload']
            if inputs['kind'] != 'simulation_input' or input_ref not in run['refs']:
                raise RecoveryBlocked('invalid_simulation_input_binding')
            expected = {key: task[key] for key in ('experiment', 'observations', 'solver', 'reference', 'scales')}
            expected.update(artifact=model['payload'], strict=task['mode'] == 'reproduction',
                nonnegative=[s['id'] for s in store.get(draft_ref)['payload']['states'] if s.get('nonnegative')],
                threshold=1e-3)
            if any(canonical_bytes(frozen.get(key)) != canonical_bytes(value) for key, value in expected.items()):
                raise RecoveryBlocked('scientific_result_frozen_inputs_mismatch')
        else:
            plan_ref = reserved['details']['plan_ref']
            result = store.get(ref)
            if (store.get(plan_ref)['kind'] != 'hypothesis_study_plan'
                    or task.get('study_plan_ref') != plan_ref or result['payload'].get('plan_ref') != plan_ref
                    or plan_ref not in result['refs']):
                raise RecoveryBlocked('invalid_study_plan_binding')
        adoptable.append(ref)
    elif name == 'load_skill' and details.get('scientific_skill_ref'):
        from . import scientific_skills
        protocol = task.get('skill_protocol')
        if not protocol or details['scientific_skill_ref'] not in envelope['refs']:
            raise RecoveryBlocked('invalid_scientific_skill_binding')
        expected = scientific_skills.load(store, protocol, reserved['details']['args']['name'])
        if canonical_bytes(details) != canonical_bytes(expected):
            raise RecoveryBlocked('scientific_skill_return_mismatch')
    elif name == 'investigate_parameter' and task.get('failure_policy'):
        ref = details.get('investigation_ref')
        if ref not in envelope['refs'] or store.get(ref)['kind'] != 'parameter_investigation':
            raise RecoveryBlocked('invalid_investigation_result_binding')
        payload = store.get(ref)['payload']
        args = reserved['details']['args']
        if (details != dict(investigation_ref=ref, **payload)
                or payload['target'] != args['target'] or payload['query'] != args['query']
                or payload['retrieval']['method'] != task.get('retrieval_method', 'lexical_v1')
                or payload['selected_value'] is not None):
            raise RecoveryBlocked('investigation_result_request_mismatch')
        adoptable.append(ref)
    elif name == 'route_failure':
        from . import failure_handling
        ref = details.get('decision_ref')
        policy = task.get('failure_policy')
        if not policy or ref not in envelope['refs']:
            raise RecoveryBlocked('invalid_failure_decision_binding')
        decision = failure_handling.load(store, ref, policy)
        if (decision['descriptor'] != reserved['details']['descriptor']
                or details != dict(decision_ref=ref, **decision['decision'])):
            raise RecoveryBlocked('failure_decision_request_mismatch')
        adoptable.append(ref)
    elif name in ('revise_plan', 'refresh_plan'):
        from . import task_plans
        ref = details.get('plan_ref')
        if not task.get('planning') or ref not in envelope['refs']:
            raise RecoveryBlocked('invalid_task_plan_result_binding')
        plan = task_plans.load(store, task['planning'], ref)
        request = reserved['details']
        expected_parent = request['args']['expected_plan_ref'] if name == 'revise_plan' else request['previous_ref']
        if (plan['previous_ref'] != expected_parent or plan['call'] != reserved['call_count']
                or plan['revision_type'] != ('model_revision' if name == 'revise_plan' else 'source_update')
                or details != dict(plan_ref=ref, revision=plan['revision'], changed_steps=plan['changed_steps'])):
            raise RecoveryBlocked('task_plan_result_request_mismatch')
        if name == 'revise_plan':
            args = request['args']
            steps = task_plans._steps(args['steps'], plan['max_calls'], plan['source_refs'], plan['parameter_targets'])
            if steps != plan['steps'] or args['reason'] != plan['reason']:
                raise RecoveryBlocked('task_plan_revision_request_mismatch')
        elif request['source_refs'] != plan['source_refs']:
            raise RecoveryBlocked('task_plan_refresh_scope_mismatch')
        adoptable.append(ref)
    elif name == 'inspect_source' and type(details.get('page_found')) is not bool:
        raise RecoveryBlocked('invalid_inspect_return')


def diagnose_execution(store, execution_ref):
    report = dict(execution_ref=execution_ref, task_ref=None, status='blocked', reasons=[],
                  operations=[], call_count=0, run_count=0, final_run_refs=[],
                  adoptable_result_refs=[], recovery_refs=[])
    try:
        record = store.get(execution_ref)
        execution = record['payload']
        if record['kind'] != 'agent_execution' or type(execution.get('version')) is not int or execution['version'] != 1:
            raise RecoveryBlocked('incompatible_execution_version')
        task_ref = report['task_ref'] = execution['task_ref']
        task_record = store.get(task_ref)
        task = task_record['payload']
        if task_record['kind'] != 'agent_task' or type(task.get('resume_version')) is not int or task['resume_version'] != 1:
            raise RecoveryBlocked('incompatible_task_version')
        fba_v2 = task.get('mode') == 'fba' and task.get('fba_protocol') == 'fba_agent_v2'
        typed_v2 = task.get('mode') == 'native_typed' and task.get('typed_protocol') == 'typed_agent_v2'
        if task.get('mode') == 'native_typed' and not typed_v2:
            report['call_count'] = report['run_count'] = None
            raise RecoveryBlocked('native_typed_recovery_not_supported_inspect_receipts_no_retry')
        if task.get('mode') == 'fba' and not fba_v2:
            report['call_count'] = report['run_count'] = None
            raise RecoveryBlocked('fba_recovery_not_supported_inspect_frozen_receipts_no_retry')
        max_calls = task['max_calls']
        if (type(max_calls) is not int or not 1 <= max_calls <= 50
                or type(execution.get('max_calls')) is not int or execution['max_calls'] != max_calls
                or type(execution.get('max_runs')) is not int or execution['max_runs'] != 1
                or not _count(execution.get('base_call_count'), max_calls)
                or not _count(execution.get('base_run_count'), 1) or task_ref not in record['refs']):
            raise RecoveryBlocked('invalid_execution_budgets_or_task_binding')
        report['call_count'], report['run_count'] = execution['base_call_count'], execution['base_run_count']
        final_kind = 'typed_agent_run' if typed_v2 else 'fba_agent_run' if fba_v2 else 'agent_run'
        allowed_kinds = ('agent_invocation', final_kind, 'agent_recovery')
        inventory = _inventory(store)
        grouped = {}
        for ref, envelope in inventory.items():
            payload = envelope['payload']
            if envelope['kind'] in allowed_kinds and not isinstance(payload, dict):
                raise RecoveryBlocked('malformed_recovery_object')
            if not isinstance(payload, dict) or payload.get('execution_ref') != execution_ref:
                continue
            if envelope['kind'] not in allowed_kinds:
                continue
            store.get(ref)  # full reference verification, without index repairs
            if envelope['kind'] == final_kind:
                if payload.get('task_ref') != task_ref or execution_ref not in envelope['refs']:
                    raise RecoveryBlocked('invalid_final_run_binding')
                report['final_run_refs'].append(ref)
            elif envelope['kind'] == 'agent_recovery':
                report['recovery_refs'].append(ref)
            else:
                if execution_ref not in envelope['refs']:
                    raise RecoveryBlocked('invocation_missing_execution_binding')
                grouped.setdefault(payload['operation_id'], []).append((ref, payload))
        pairs = []
        for operation_id, rows in grouped.items():
            if not isinstance(operation_id, str) or not operation_id:
                raise RecoveryBlocked('invalid_operation_id')
            reserved = [(r, p) for r, p in rows if p.get('phase') == 'reserved']
            terminals = [(r, p) for r, p in rows if p.get('phase') in ('returned', 'error', 'interrupted')]
            if len(reserved) != 1 or len(terminals) > 1 or len(rows) != len(reserved) + len(terminals):
                raise RecoveryBlocked('ambiguous_operation_phases')
            rref, res = reserved[0]
            tref, term = terminals[0] if terminals else (None, None)
            if (type(res.get('sequence')) is not int or res['sequence'] < 1
                    or type(res.get('version')) is not int or res['version'] != 1
                    or res.get('name') not in REPLAY_CLASSES
                    or res.get('replay_class') != REPLAY_CLASSES[res['name']]
                    or not isinstance(res.get('details'), dict)):
                raise RecoveryBlocked('invalid_or_legacy_reservation')
            if term is not None:
                keys = ('version', 'execution_ref', 'operation_id', 'sequence', 'name',
                        'call_count', 'run_count', 'replay_class')
                if (any(type(term.get(k)) is not type(res.get(k)) or term.get(k) != res.get(k) for k in keys)
                        or rref not in inventory[tref]['refs']):
                    raise RecoveryBlocked('terminal_reservation_mismatch')
            pairs.append(dict(reserved_ref=rref, terminal_ref=tref, reserved=res, terminal=term))
        pairs.sort(key=lambda op: op['reserved']['sequence'])
        report['operations'] = pairs
        calls, runs = report['call_count'], report['run_count']
        for sequence, operation in enumerate(pairs, 1):
            res, term = operation['reserved'], operation['terminal']
            calls += int(res['name'] == 'respond')
            runs += int(res['name'] in ('run', 'run_study', 'run_fba', 'run_typed', 'run_estimation'))
            if (res['sequence'] != sequence or not _count(res.get('call_count'), max_calls)
                    or not _count(res.get('run_count'), 1) or res['call_count'] != calls or res['run_count'] != runs):
                raise RecoveryBlocked('journal_sequence_or_budget_mismatch')
            report['call_count'], report['run_count'] = calls, runs
            if term is None:
                if sequence != len(pairs):
                    raise RecoveryBlocked('unresolved_operation_before_later_work')
                if res['replay_class'] == 'read_only':
                    operation['retry_read_only'] = True
                else:
                    reasons = {'external_unknown': 'external_outcome_unknown',
                        'scientific_execution': 'scientific_outcome_unknown', 'artifact_write': 'partial_artifacts_unknown'}
                    report['reasons'].append(reasons[res['replay_class']])
            elif term['phase'] != 'returned':
                report['reasons'].append('recorded_error_or_interruption_requires_review')
            else:
                _returned(store, operation, report['adoptable_result_refs'], task)
        # Legacy records may still expose verified result references, but do
        # not invent the missing initial execution state.
        _initial_scope(store, execution, task)
        initial_sources = execution['recovery']['source_refs']
        if any(ref not in record['refs'] for ref in initial_sources):
            raise RecoveryBlocked('initial_sources_missing_from_execution_refs')
        if fba_v2 and task['model_ref'] not in record['refs']:
            raise RecoveryBlocked('initial_fba_model_missing_from_execution_refs')
        if typed_v2 and not {task['model_ref'],task['input_ref']}<=set(record['refs']):
            raise RecoveryBlocked('initial_typed_model_or_input_missing')
        ordered_refs = [ref for op in pairs for ref in (op['reserved_ref'], op['terminal_ref']) if ref]
        for receipt_ref in report['recovery_refs']:
            receipt_record = store.get(receipt_ref)
            receipt = receipt_record['payload']
            prefix = receipt.get('invocation_refs')
            if (type(receipt.get('version')) is not int or receipt['version'] != 1
                    or receipt.get('task_ref') != task_ref or receipt.get('method') != 'verified_return_replay'
                    or not isinstance(prefix, list) or prefix != ordered_refs[:len(prefix)]
                    or receipt_record['refs'] != [execution_ref, *prefix]):
                raise RecoveryBlocked('invalid_recovery_receipt_binding')
            prior_operations = [op for op in pairs if op['reserved_ref'] in prefix]
            last = prior_operations[-1]['reserved'] if prior_operations else execution
            expected_calls = last.get('call_count', execution['base_call_count'])
            expected_runs = last.get('run_count', execution['base_run_count'])
            if (type(receipt.get('call_count')) is not int or receipt['call_count'] != expected_calls
                    or type(receipt.get('run_count')) is not int or receipt['run_count'] != expected_runs):
                raise RecoveryBlocked('invalid_recovery_receipt_budget')
        for final_ref in report['final_run_refs']:
            final = store.get(final_ref)['payload']
            cp = store.get(final['checkpoint_ref'])
            if (final.get('invocation_refs') != ordered_refs or final.get('source_refs') != initial_sources
                    or type(final.get('call_count')) is not int or final['call_count'] != calls
                    or cp['kind'] != 'agent_checkpoint' or cp['payload'].get('execution_ref') != execution_ref
                    or cp['payload'].get('invocation_refs') != ordered_refs
                    or set(final.get('recovery_refs', [])) != set(report['recovery_refs'])):
                raise RecoveryBlocked('final_run_journal_mismatch')
            if fba_v2 and (type(final.get('run_count')) is not int or final['run_count'] != runs
                    or cp['payload'].get('run_count') != runs or final.get('model_ref') != task['model_ref']):
                raise RecoveryBlocked('final_fba_model_or_run_budget_mismatch')
            if typed_v2 and (type(final.get('run_count')) is not int or final['run_count']!=runs
                    or cp['payload'].get('run_count')!=runs or final.get('model_ref')!=task['model_ref']
                    or final.get('input_ref')!=task['input_ref'] or final.get('model_type')!=task['model_type']):
                raise RecoveryBlocked('final_typed_model_input_or_budget_mismatch')
        report['status'] = ('blocked' if report['reasons'] else
                            'finished' if report['final_run_refs'] else 'ready')
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        # Report stable failures, never raw provider/transport exceptions.
        reason = str(exc) if isinstance(exc, RecoveryBlocked) else 'invalid_or_unavailable_recovery_record:' + type(exc).__name__
        report['reasons'].append(reason)
        report['status'] = 'blocked'
    return report


class ReplayJournal:
    """Reconstruct local control state from verified returned calls under owner lock."""
    def __init__(self, store, report):
        if report['status'] != 'ready':
            raise RecoveryBlocked('execution is not eligible for continuation')
        self._store, self._operations = store, deepcopy(report['operations'])
        self.execution_ref = report['execution_ref']
        execution = store.get(self.execution_ref)['payload']
        self.calls, self.runs = execution['base_call_count'], execution['base_run_count']
        self.refs, self._position, self.last_usage = [], 0, None
        refs = [ref for op in self._operations for ref in (op['reserved_ref'], op['terminal_ref']) if ref]
        self._live = InvocationJournal.reopen(store, self.execution_ref, refs=refs,
            calls=report['call_count'], runs=report['run_count'], next_sequence=len(self._operations) + 1)

    def assert_consumed(self):
        if self._position != len(self._operations):
            raise RecoveryBlocked('replay ended with unconsumed operations')

    def invoke(self, name, callback, *, details=None, summarize=None, result_refs=None):
        self.last_usage = None
        if self._position >= len(self._operations):
            try:
                return self._live.invoke(name, callback, details=details, summarize=summarize, result_refs=result_refs)
            finally:
                self.calls, self.runs = self._live.calls, self._live.runs
                self.refs = list(self._live.refs)
        operation = self._operations[self._position]
        res, term = operation['reserved'], operation['terminal']
        if name != res['name'] or canonical_bytes(details or {}) != canonical_bytes(res['details']):
            raise RecoveryBlocked('replay request differs from recorded operation')
        self._position += 1
        self.calls, self.runs = res['call_count'], res['run_count']
        self.refs.append(operation['reserved_ref'])
        if term is None:
            if not operation.get('retry_read_only'):
                raise RecoveryBlocked('unknown side effect cannot be replayed')
            try:
                value = callback()
            except BaseException as exc:
                phase = 'error' if isinstance(exc, Exception) else 'interrupted'
                self._live._terminal(name, res['operation_id'], phase, self.calls, self.runs,
                    {'error_type': type(exc).__name__}, operation['reserved_ref'], (), sequence=res['sequence'])
                raise
            summary = summarize(value) if summarize else value
            refs = tuple(ref for ref in (result_refs(value) if result_refs else ()) if ref)
            self._live._terminal(name, res['operation_id'], 'returned', self.calls, self.runs,
                summary if summary is not None else {}, operation['reserved_ref'], refs, sequence=res['sequence'])
            self.refs = list(self._live.refs)
            return value
        self.refs.append(operation['terminal_ref'])
        out = deepcopy(term['details'])
        if name == 'respond':
            self.last_usage = out.get('usage', {})
            return json.loads(out['response_json'])
        if name in ('submit_draft', 'submit_plan'):
            out['artifact'] = (from_dict(ModelArtifact, self._store.get(out['model_ref'])['payload'])
                               if out.get('model_ref') else None)
        elif name in ('run', 'run_study', 'run_fba', 'run_typed', 'run_estimation'):
            return out['result_ref']
        elif name == 'request_estimation':
            return out['request_ref']
        elif name == 'inspect_source':
            args = res['details']['args']
            page = next((p['text'] for p in self._store.get(args['source_ref'])['payload']['pages']
                         if p['number'] == args['page']), None)
            if (page is not None) != out['page_found']:
                raise RecoveryBlocked('immutable source page differs from read record')
            return page
        return out
