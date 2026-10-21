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
    try:
        import fcntl
    except ImportError as exc:
        raise RecoveryBlocked('local file ownership is unsupported on this platform') from exc
    directory = store.root / '.agent-owners'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise RecoveryBlocked('owner directory must not be a symlink')
    fd = os.open(directory / (task_ref + '.lock'),
                 os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryBlocked('task already has an active execution owner') from exc
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
    elif name == 'submit_draft':
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
    elif name in ('run', 'run_study'):
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
        max_calls = task['max_calls']
        if (type(max_calls) is not int or not 1 <= max_calls <= 50
                or type(execution.get('max_calls')) is not int or execution['max_calls'] != max_calls
                or type(execution.get('max_runs')) is not int or execution['max_runs'] != 1
                or not _count(execution.get('base_call_count'), max_calls)
                or not _count(execution.get('base_run_count'), 1) or task_ref not in record['refs']):
            raise RecoveryBlocked('invalid_execution_budgets_or_task_binding')
        report['call_count'], report['run_count'] = execution['base_call_count'], execution['base_run_count']
        inventory = _inventory(store)
        grouped = {}
        for ref, envelope in inventory.items():
            payload = envelope['payload']
            if envelope['kind'] in ('agent_invocation', 'agent_run', 'agent_recovery') and not isinstance(payload, dict):
                raise RecoveryBlocked('malformed_recovery_object')
            if not isinstance(payload, dict) or payload.get('execution_ref') != execution_ref:
                continue
            if envelope['kind'] not in ('agent_invocation', 'agent_run', 'agent_recovery'):
                continue
            store.get(ref)  # full reference verification, without index repairs
            if envelope['kind'] == 'agent_run':
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
            runs += int(res['name'] in ('run', 'run_study'))
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
        if name == 'submit_draft':
            out['artifact'] = (from_dict(ModelArtifact, self._store.get(out['model_ref'])['payload'])
                               if out.get('model_ref') else None)
        elif name in ('run', 'run_study'):
            return out['result_ref']
        elif name == 'inspect_source':
            args = res['details']['args']
            page = next((p['text'] for p in self._store.get(args['source_ref'])['payload']['pages']
                         if p['number'] == args['page']), None)
            if (page is not None) != out['page_found']:
                raise RecoveryBlocked('immutable source page differs from read record')
            return page
        return out
