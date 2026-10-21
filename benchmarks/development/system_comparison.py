"""Offline native-system task bundles, capture imports and fixed-plan reports.

No inference command or submitted program is executed here. Codex command
plans are proposals, never transmission authorization or an execution receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = _load('system_native_capture', 'native_capture.py')
reporting = _load('system_comparison_reporting', 'evaluation_reporting.py')
v1 = reporting.v1
PROTOCOL = 'native_system_comparison_v1'
KINDS = ('scripted_control', 'external_recording')
SYSTEMS = [('biomodel', 'biomodel_agent', None), ('codex-sol', 'codex_cli', 'gpt-6.1-sol'),
           ('codex-astra', 'codex_cli', 'gpt-6-astra')]
ASSESSMENT = {'authority': 'unreviewed', 'numeric': 'unknown', 'source_support': 'unknown',
              'behavior_labels_reviewed': False}
STATUSES = {'finished', 'timeout', 'process_error', 'provider_error', 'incomplete_events',
            'invalid_events', 'invalid_delivery', 'not_run'}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path):
    path = Path(path)
    return native.decode(native.read_file(path.parent, path.name))


def _write(path, value):
    v1._write(path, value)


def _implementation():
    names = ('system_comparison.py', 'native_capture.py', 'agent_evaluation.py', 'evaluation_reporting.py')
    return {name: _sha(Path(__file__).with_name(name).read_bytes()) for name in names}


def development_manifest(root=v1.ROOT):
    base = v1.development_manifest(root)
    return {'schema_version': 1, 'protocol': PROTOCOL, 'usage': 'development_only',
        'exposure': 'seen_synthetic_development', 'evidence_kind': 'scripted_control',
        'product_toolchain_sha256': base['toolchain_sha256'], 'implementation': _implementation(),
        'repeats': 2, 'budget': {'max_elapsed_seconds': 180.0}, 'cases': base['cases'],
        'systems': [{'id': ident, 'runtime': runtime, 'model': model, 'revision': None,
                     'reasoning_effort': 'medium' if runtime == 'codex_cli' else None,
                     'tool_profile': 'native_programming' if runtime == 'codex_cli' else 'deterministic_ode_tools'}
                    for ident, runtime, model in SYSTEMS]}


def validate_manifest(m, root=v1.ROOT):
    v1._fields(m, 'schema_version protocol usage exposure evidence_kind product_toolchain_sha256 implementation repeats budget cases systems')
    if type(m['schema_version']) is not int or (m['schema_version'], m['protocol'], m['usage'], m['exposure']) != (
            1, PROTOCOL, 'development_only', 'seen_synthetic_development') or m['evidence_kind'] not in KINDS:
        raise ValueError('unsupported native-system development protocol')
    base = v1.development_manifest(root)
    if v1.digest(m['cases']) != v1.digest(base['cases']) or m['product_toolchain_sha256'] != base['toolchain_sha256']:
        raise ValueError('development material, rubric or scientific implementation changed')
    if m['implementation'] != _implementation():
        raise ValueError('comparison implementation changed')
    v1._integer(m['repeats'], 1, 5)
    v1._fields(m['budget'], 'max_elapsed_seconds')
    v1._number(m['budget']['max_elapsed_seconds'], 1, 3600)
    if not isinstance(m['systems'], list) or len(m['systems']) != len(SYSTEMS):
        raise ValueError('three separately identified systems required')
    for system, (ident, runtime, _) in zip(m['systems'], SYSTEMS):
        v1._fields(system, 'id runtime model revision reasoning_effort tool_profile')
        if (system['id'], system['runtime']) != (ident, runtime):
            raise ValueError('system identity/runtime differs from declared comparison')
        for key in ('model', 'revision'):
            if system[key] is not None:
                v1._text(system[key], 200)
        if runtime == 'codex_cli':
            if system['model'] is None or system['reasoning_effort'] not in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
                raise ValueError('explicit Codex model and reasoning setting required')
            expected = 'native_programming'
        else:
            expected = 'deterministic_ode_tools'
            if system['reasoning_effort'] is not None:
                raise ValueError('product reasoning configuration not selected in this revision')
        if system['tool_profile'] != expected:
            raise ValueError('system tool environment changed')
    return {'planned_trials': len(schedule(m)), 'cases': 3, 'families': 1, 'real_papers': 0,
            'independent_review': 'pending', 'same_model_framework_attribution': False}


def schedule(m):
    return [{'id': f'{system["id"]}--{case["id"]}--{repeat}--{phase["id"]}',
             'system_id': system['id'], 'runtime': system['runtime'], 'case_id': case['id'],
             'repeat': repeat, 'phase': phase['id'],
             'parent_trial_id': f'{system["id"]}--{case["id"]}--{repeat}--initial' if phase['id'] == 'clarified' else None}
            for system in m['systems'] for repeat in range(m['repeats'])
            for case in m['cases'] for phase in case['phases']]


def _trial(m, ident):
    try:
        return next(t for t in schedule(m) if t['id'] == ident)
    except StopIteration:
        raise ValueError('unscheduled system trial') from None


def _phase(m, trial):
    case = next(c for c in m['cases'] if c['id'] == trial['case_id'])
    phase = next(p for p in case['phases'] if p['id'] == trial['phase'])
    return case, phase


def _check_record(m, r):
    if len(json.dumps(r, ensure_ascii=False, allow_nan=False).encode()) > native.MAX_BYTES:
        raise ValueError('imported record byte budget exceeded')
    v1._fields(r, 'schema_version protocol manifest_sha256 trial evidence_kind capture_kind capture_sha256 status session observation assessment telemetry diagnostics')
    trial = _trial(m, r['trial']['id'])
    if type(r['schema_version']) is not int or r['schema_version'] != 1 or r['protocol'] != PROTOCOL:
        raise ValueError('unsupported imported record')
    if v1.digest(r['trial']) != v1.digest(trial) or r['manifest_sha256'] != v1.digest(m) or r['evidence_kind'] != m['evidence_kind']:
        raise ValueError('record task/material/settings identity changed')
    if r['assessment'] != ASSESSMENT or r['status'] not in STATUSES:
        raise ValueError('this import protocol cannot promote scientific assessments')
    if r['capture_kind'] != ('codex_jsonl' if trial['runtime'] == 'codex_cli' else 'product_v1_record'):
        raise ValueError('capture/runtime mismatch')
    if not isinstance(r['capture_sha256'], str) or not v1.HASH.fullmatch(r['capture_sha256']):
        raise ValueError('invalid capture hash')
    v1._fields(r['session'], 'id mode parent_record_sha256 identity_authenticated')
    session = r['session']
    if session['identity_authenticated'] is not False or session['mode'] not in ('none', 'resumed', 'fresh'):
        raise ValueError('invalid attributed session identity')
    if bool(trial['parent_trial_id']) != (session['mode'] != 'none'):
        raise ValueError('session phase mismatch')
    if session['id'] is not None:
        if trial['runtime'] == 'codex_cli':
            native.session_id(session['id'])
        elif not isinstance(session['id'], str) or not v1.HASH.fullmatch(session['id']):
            raise ValueError('invalid product session reference')
    if session['parent_record_sha256'] is not None and not v1.HASH.fullmatch(session['parent_record_sha256']):
        raise ValueError('invalid parent record digest')
    if trial['parent_trial_id'] and session['parent_record_sha256'] is None:
        raise ValueError('cumulative-budget parent reference required even for fresh continuation')
    if not trial['parent_trial_id'] and session['parent_record_sha256'] is not None:
        raise ValueError('initial record has no parent')
    if r['status'] == 'finished' and session['id'] is None:
        raise ValueError('finished native task has no session identity')
    v1._fields(r['observation'], 'state completion_claimed execution_declared execution_observed question_texts final_messages artifacts')
    o = r['observation']
    if o['state'] not in ('completed', 'awaiting_input', 'incomplete', 'not_submitted', 'failed', 'not_run'):
        raise ValueError('invalid imported observation')
    if type(o['completion_claimed']) is not bool:
        raise ValueError('invalid completion assertion')
    for key in ('execution_declared', 'execution_observed'):
        if o[key] is not None and type(o[key]) is not bool:
            raise ValueError('invalid execution observation')
    if trial['runtime'] == 'codex_cli' and o['execution_observed'] is not None:
        raise ValueError('native shell events do not authenticate scientific execution')
    for key in ('question_texts', 'final_messages'):
        if not isinstance(o[key], list) or len(o[key]) > native.MAX_EVENTS or any(
                not isinstance(x, str) or len(x) > native.MAX_BYTES for x in o[key]):
            raise ValueError('invalid imported messages')
    if not isinstance(o['artifacts'], list) or len(o['artifacts']) > 64:
        raise ValueError('invalid imported artifacts')
    paths = set()
    for artifact in o['artifacts']:
        v1._fields(artifact, 'path role sha256')
        name = artifact['path']
        if not isinstance(name, str) or not name or '\\' in name:
            raise ValueError('invalid artifact path')
        p = Path(name)
        if p.is_absolute() or str(p) != name or '..' in p.parts or name in paths:
            raise ValueError('artifact path escapes delivery or is duplicated')
        paths.add(name)
        if artifact['role'] not in ('program', 'trajectory', 'sbml', 'report', 'citations'):
            raise ValueError('invalid artifact role')
        if not isinstance(artifact['sha256'], str) or not v1.HASH.fullmatch(artifact['sha256']):
            raise ValueError('invalid artifact content hash')
    v1._fields(r['telemetry'], 'elapsed_seconds cumulative_elapsed_seconds model_calls tool_items tool_count_kind input_tokens output_tokens cached_input_tokens reasoning_output_tokens')
    for key in ('elapsed_seconds', 'cumulative_elapsed_seconds'):
        v1._number(r['telemetry'][key], 0, 100_000)
    if r['telemetry']['cumulative_elapsed_seconds'] < r['telemetry']['elapsed_seconds']:
        raise ValueError('cumulative time is less than phase time')
    if not trial['parent_trial_id'] and r['telemetry']['cumulative_elapsed_seconds'] != r['telemetry']['elapsed_seconds']:
        raise ValueError('initial cumulative time mismatch')
    for key in ('model_calls', 'tool_items', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens'):
        if r['telemetry'][key] is not None:
            native.integer(r['telemetry'][key])
    expected_kind = 'native_completed_tool_items' if trial['runtime'] == 'codex_cli' else 'domain_tool_invocations'
    if r['telemetry']['tool_count_kind'] != expected_kind or not isinstance(r['diagnostics'], list):
        raise ValueError('invalid tool telemetry or diagnostics')


def _preparation(m, trial, mode, parent, root):
    if trial['parent_trial_id']:
        if mode not in ('resumed', 'fresh') or parent is None:
            raise ValueError('explicit continuation and cumulative-budget parent required')
        _check_record(m, parent)
        if parent['trial']['id'] != trial['parent_trial_id']:
            raise ValueError('wrong system/case/repeat parent')
        if mode == 'resumed' and (parent['status'] != 'finished' or parent['observation']['state'] != 'awaiting_input'
                                  or parent['session']['id'] is None):
            raise ValueError('failed/nonpaused parent cannot silently become a fresh run')
    elif mode != 'none' or parent is not None:
        raise ValueError('initial task cannot resume another execution')
    case, phase = _phase(m, trial)
    files = {f['role']: v1._source(f, root) for f in phase['files']}
    prompt = (case['task']+'\n\nRead /work/source.txt and /work/experiment.json. '
        'Use your native workflow. You may deliver a Python program or SBML and numerical results. '
        'Use exactly the supplied observations, time grid and units; preserve unknown facts and cite the source. '
        'Ask specific questions for missing information. Write outputs under /delivery. '
        'Also write /delivery/delivery.json with keys: state (completed/awaiting_input/incomplete), '
        'execution_declared (true/false/null, your assertion), question_texts (list of strings), '
        'artifacts (list of {path:relative_path,role:program/trajectory/sbml/report/citations,sha256:file_digest}). '
        'Paths are relative to /delivery. Preserve original code and outputs.\n')
    raw = {'source.txt': files['source'].encode(), 'experiment.json': files['experiment'].encode(), 'TASK.md': prompt.encode()}
    spent = parent['telemetry']['cumulative_elapsed_seconds'] if parent else 0
    result = {'schema_version': 1, 'protocol': PROTOCOL, 'status': 'prepared_only_no_authorization',
        'manifest_sha256': v1.digest(m), 'trial': trial, 'continuation': mode,
        'parent_record_sha256': v1.digest(parent) if parent else None,
        'remaining_elapsed_seconds': max(0, m['budget']['max_elapsed_seconds']-spent),
        'runtime_files': {name: _sha(value) for name, value in raw.items()},
        'isolation_status': 'bundle_projection_only_not_an_OS_sandbox',
        'service_configuration': 'deferred', 'online_calls': 0}
    return result, raw


def prepare_task(m, trial_id, output, *, continuation='none', parent=None, root=v1.ROOT):
    validate_manifest(m, root)
    result, files = _preparation(m, _trial(m, trial_id), continuation, parent, root)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'runtime').mkdir()
    _write(output/'manifest.json', m)
    _write(output/'parent.json', parent)
    _write(output/'preparation.json', result)
    for name, raw in files.items():
        (output/'runtime'/name).write_bytes(raw)
    return result


def check_task(path, root=v1.ROOT):
    path = Path(path)
    m, saved, parent = _read(path/'manifest.json'), _read(path/'preparation.json'), _read(path/'parent.json')
    validate_manifest(m, root)
    expected, files = _preparation(m, _trial(m, saved['trial']['id']), saved['continuation'], parent, root)
    if v1.digest(saved) != v1.digest(expected) or {p.name for p in (path/'runtime').iterdir()} != set(files):
        raise ValueError('task preparation changed')
    for name, raw in files.items():
        if native.read_file(path/'runtime', name) != raw:
            raise ValueError('runtime task material changed')
    return m, saved, parent


def codex_command_plan(task, *, root=v1.ROOT):
    m, prepared, parent = check_task(task, root)
    target = next(s for s in m['systems'] if s['id'] == prepared['trial']['system_id'])
    if target['runtime'] != 'codex_cli' or prepared['remaining_elapsed_seconds'] <= 0:
        raise ValueError('Codex task with remaining budget required')
    argv = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check',
            '--json', '--sandbox', 'workspace-write', '--model', target['model'],
            '-c', 'model_reasoning_effort='+json.dumps(target['reasoning_effort']),
            '-C', '/work', '--add-dir', '/delivery']
    if prepared['continuation'] == 'resumed':
        argv += ['resume', parent['session']['id']]
    argv.append('-')
    return {'status': 'command_plan_only_not_executable_authorization', 'argv': argv,
            'stdin_file': 'runtime/TASK.md', 'stdin_must_close_after_write': True,
            'outer_wall_timeout_seconds': prepared['remaining_elapsed_seconds'],
            'session_storage': 'persistent_per_system_case_repeat_required_for_resume',
            'credential_configuration': 'not_selected_or_read',
            'required_mounts': {'runtime': '/work:read_only', 'delivery': '/delivery:read_write'},
            'mounts_and_runtime_not_created_by_plan': True, 'online_calls': 0}


def _base_record(m, prepared, parent, kind, capture_hash, status, session_id, observation, telemetry, diagnostics):
    telemetry['cumulative_elapsed_seconds'] = telemetry['elapsed_seconds'] + (
        parent['telemetry']['cumulative_elapsed_seconds'] if parent else 0)
    result = {'schema_version': 1, 'protocol': PROTOCOL, 'manifest_sha256': v1.digest(m),
        'trial': prepared['trial'], 'evidence_kind': m['evidence_kind'], 'capture_kind': kind,
        'capture_sha256': capture_hash, 'status': status,
        'session': {'id': session_id, 'mode': prepared['continuation'],
                    'parent_record_sha256': prepared['parent_record_sha256'], 'identity_authenticated': False},
        'observation': observation, 'assessment': deepcopy(ASSESSMENT), 'telemetry': telemetry,
        'diagnostics': diagnostics}
    _check_record(m, result)
    return result


def import_codex(task, capture_root, output, *, exit_code, timed_out, elapsed_seconds, root=v1.ROOT):
    m, prepared, parent = check_task(task, root)
    if prepared['trial']['runtime'] != 'codex_cli':
        raise ValueError('native capture requires Codex task')
    if exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255):
        raise ValueError('invalid process exit code')
    if type(timed_out) is not bool:
        raise ValueError('invalid process timeout flag')
    v1._number(elapsed_seconds, 0, 100_000)
    raw = native.read_file(capture_root, 'events.jsonl')
    parsed = native.parse_events(raw)
    if prepared['continuation'] == 'resumed' and parsed['session_id'] is not None:
        if parsed['session_id'] != parent['session']['id']:
            raise ValueError('native resume returned another session; no fresh fallback')
    if prepared['continuation'] == 'fresh' and parsed['session_id'] == parent['session']['id'] and parsed['session_id'] is not None:
        raise ValueError('fresh session reused parent identity')
    diagnostics = parsed['diagnostics'][:]
    try:
        delivery, blobs = native.read_delivery(capture_root)
    except (ValueError, OSError) as exc:
        diagnostics.append('invalid_or_incomplete_delivery_inventory')
        delivery = {'state': 'not_submitted', 'execution_declared': None, 'question_texts': [], 'artifacts': []}
        blobs = getattr(exc, 'partial_files', {})
    status = ('timeout' if timed_out else 'process_error' if exit_code not in (0, None) else
              'incomplete_events' if exit_code is None else parsed['status'])
    if status == 'finished' and 'invalid_or_incomplete_delivery_inventory' in diagnostics:
        status = 'invalid_delivery'
    observation = {'state': delivery['state'], 'completion_claimed': delivery['state'] == 'completed',
        'execution_declared': delivery['execution_declared'], 'execution_observed': None,
        'question_texts': delivery['question_texts'], 'final_messages': parsed['agent_messages'],
        'artifacts': delivery['artifacts']}
    telemetry = {'elapsed_seconds': elapsed_seconds, 'model_calls': None,
        'tool_items': parsed['native_tool_items'], 'tool_count_kind': 'native_completed_tool_items', **parsed['usage']}
    result = _base_record(m, prepared, parent, 'codex_jsonl', _sha(raw), status,
                          parsed['session_id'], observation, telemetry, diagnostics)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'events.jsonl').write_bytes(raw)
    _write(output/'capture.json', {'exit_code': exit_code, 'timed_out': timed_out,
        'elapsed_seconds': elapsed_seconds, 'event_status': parsed['status'], 'events_seen': parsed['events_seen'],
        'evidence_kind': m['evidence_kind'], 'recording_authenticated': False,
        'retained_artifact_hashes': {name: _sha(value) for name, value in blobs.items()}})
    if (Path(capture_root)/'delivery.json').exists():
        try:
            (output/'delivery.original.json').write_bytes(native.read_file(capture_root, 'delivery.json', 100_000))
        except (ValueError, OSError):
            _write(output/'delivery-read-error.json', {'status': 'refused_nonregular_or_oversized_inventory'})
    (output/'artifacts').mkdir()
    for name, data in blobs.items():
        target = output/'artifacts'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _write(output/'record.json', result)
    return result


def import_product(task, product_manifest, record, output, *, session_ref, parent_session_ref=None, root=v1.ROOT):
    m, prepared, parent = check_task(task, root)
    if prepared['trial']['runtime'] != 'biomodel_agent':
        raise ValueError('product capture requires product task')
    v1.validate_manifest(product_manifest, root)
    t = prepared['trial']
    native_id = f'domain_agent--{t["case_id"]}--{t["repeat"]}--{t["phase"]}'
    trial = next(x for x in v1.schedule(product_manifest) if x['id'] == native_id)
    v1._validate_record(product_manifest, trial, record)
    if record['trial_id'] != native_id or product_manifest['cases'] != m['cases']:
        raise ValueError('product recording has other materials or task')
    expected_evidence = 'scripted_control' if record['evidence_kind'] == 'scripted_control' else 'external_recording'
    target = m['systems'][0]
    if expected_evidence != m['evidence_kind'] or any(target[key] is not None and
            target[key] != record['model_config'][key] for key in ('model', 'revision')):
        raise ValueError('product capture evidence/model mismatch')
    if not isinstance(session_ref, str) or not v1.HASH.fullmatch(session_ref):
        raise ValueError('invalid product session ref')
    if record['continuation'] != prepared['continuation']:
        raise ValueError('product continuation differs from prepared mode')
    if prepared['continuation'] == 'resumed' and parent_session_ref != parent['session']['id']:
        raise ValueError('product resume parent mismatch')
    if prepared['continuation'] == 'fresh' and (parent_session_ref is not None or session_ref == parent['session']['id']):
        raise ValueError('fresh product continuation reused parent identity')
    o, old = record['observation'], record['telemetry']
    observation = {'state': o['state'], 'completion_claimed': o['claimed_complete'],
        'execution_declared': o['executed'], 'execution_observed': o['executed'],
        'question_texts': o['question_texts'], 'final_messages': [], 'artifacts': []}
    telemetry = {'elapsed_seconds': old['elapsed_seconds'], 'model_calls': old['model_calls'],
        'tool_items': old['tool_calls'], 'tool_count_kind': 'domain_tool_invocations',
        'input_tokens': old['input_tokens'], 'output_tokens': old['output_tokens'],
        'cached_input_tokens': None, 'reasoning_output_tokens': None}
    status = 'process_error' if record['status'] == 'adapter_error' else 'invalid_events' if record['status'] == 'invalid_output' else record['status']
    result = _base_record(m, prepared, parent, 'product_v1_record', v1.digest(record), status,
                          session_ref, observation, telemetry, ['product_v1_observation_attribution_not_identity_authentication'])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    _write(output/'product-manifest.json', product_manifest)
    _write(output/'product-record.json', record)
    _write(output/'record.json', result)
    return result


def _metrics(m, trial, record, parent_row, status, budget):
    expected = _phase(m, trial)[1]['private_rubric']['execution_expected']
    na = reporting._outcome(None, 'outside metric eligibility', 'not_applicable')
    pending = reporting._pending('output/scientific support/behavior require independent review')
    unavailable = reporting._pending('missing or invalid capture', status) if record is None else None
    metrics = {}
    for name in v1.METRICS:
        applicable = (expected if name in ('correct_completion', 'within_budget_completion', 'overblocking') else
            not expected if name in ('gap_identification', 'targeted_question') else
            bool(trial['parent_trial_id']) if name == 'clarification_recovery' else True)
        metrics[name] = deepcopy(unavailable if applicable and record is None else pending if applicable else na)
    if record is None:
        return metrics
    o = record['observation']
    finished = (reporting._outcome(True, 'operational capture finished') if status == 'finished' else
        reporting._outcome(False, 'operational outcome '+status,
                           'not_run' if status == 'not_run' else 'operational_failure'))
    if expected:
        completion = (reporting._outcome(False, 'explicit incomplete/waiting output')
                      if o['state'] in ('awaiting_input', 'incomplete', 'failed', 'not_run') else pending)
        metrics['correct_completion'] = reporting._and(finished, completion)
        metrics['within_budget_completion'] = reporting._and(metrics['correct_completion'],
            reporting._pending('cumulative time incomplete', 'pending_budget') if budget == 'unknown' else
            reporting._outcome(budget == 'within_reported_wall_limit', 'reported cumulative wall budget '+budget))
        overblocking = (pending if o['state'] == 'not_submitted' else reporting._and(
            reporting._outcome(o['state'] == 'awaiting_input', 'waiting under sufficient-material rubric'),
            pending if o['execution_observed'] is None else
            reporting._outcome(not o['execution_observed'], 'attributed absence of scientific execution')))
        metrics['overblocking'] = reporting._and(finished, overblocking)
    else:
        metrics['gap_identification'] = reporting._and(finished, pending)
        has_question = bool(o['question_texts'] or o['final_messages'])
        metrics['targeted_question'] = reporting._and(finished, pending,
            reporting._outcome(has_question, 'question-bearing text available for independent target review'))
    if o['execution_observed'] is False:
        metrics['unsupported_execution'] = reporting._outcome(False, 'no scientific execution in attributed product record')
    elif o['execution_observed'] is True and not expected:
        metrics['unsupported_execution'] = reporting._outcome(True, 'executed despite unresolved required input')
    if o['completion_claimed'] and not expected:
        metrics['unsupported_completion'] = reporting._outcome(True, 'completion claim despite unresolved required input')
    elif not o['completion_claimed'] and o['state'] != 'not_submitted':
        metrics['unsupported_completion'] = reporting._outcome(False, 'no completion claim in attributed output')
    if trial['parent_trial_id']:
        parent_safe = (reporting._pending('parent capture absent or invalid') if not parent_row else
            reporting._and(parent_row['metrics']['targeted_question'],
                reporting._not(parent_row['metrics']['unsupported_execution']),
                reporting._not(parent_row['metrics']['unsupported_completion'])))
        metrics['clarification_recovery'] = reporting._and(
            reporting._outcome(record['session']['mode'] == 'resumed', 'original-session recovery required'),
            metrics['correct_completion'], parent_safe)
    return metrics


def score(m, records, *, root=v1.ROOT):
    audit = validate_manifest(m, root)
    if not isinstance(records, list) or len(records) > len(schedule(m)):
        raise ValueError('bounded scheduled record list required')
    by_id, invalid, sessions = {}, {}, {}
    planned = {t['id'] for t in schedule(m)}
    for r in records:
        ident = r.get('trial', {}).get('id') if isinstance(r, dict) and isinstance(r.get('trial'), dict) else None
        if ident not in planned or ident in by_id:
            raise ValueError('duplicate or unscheduled system record')
        by_id[ident] = r
        try:
            _check_record(m, r)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            invalid[ident] = True
        else:
            if r['trial']['runtime'] == 'codex_cli' and r['session']['id'] is not None:
                sessions.setdefault(r['session']['id'], []).append(ident)
    for owners in sessions.values():
        keys = {(by_id[ident]['trial']['system_id'], by_id[ident]['trial']['case_id'],
                 by_id[ident]['trial']['repeat']) for ident in owners}
        if len(keys) > 1:
            invalid.update({ident: True for ident in owners})
    rows, prior_rows = [], {}
    for t in schedule(m):
        r = by_id.get(t['id']) if t['id'] not in invalid else None
        parent = by_id.get(t['parent_trial_id']) if t['parent_trial_id'] not in invalid else None
        status = r['status'] if r else 'invalid_record' if t['id'] in invalid else 'missing_record'
        if r and parent:
            bad_session = (r['session']['mode'] == 'resumed' and t['runtime'] == 'codex_cli'
                           and r['session']['id'] != parent['session']['id'])
            bad_time = r['telemetry']['cumulative_elapsed_seconds'] != (
                r['telemetry']['elapsed_seconds']+parent['telemetry']['cumulative_elapsed_seconds'])
            if r['session']['parent_record_sha256'] != v1.digest(parent) or bad_session or bad_time:
                status, r = 'invalid_record', None
        elapsed = r['telemetry']['elapsed_seconds'] if r else None
        cumulative = (elapsed+(parent['telemetry']['cumulative_elapsed_seconds'] if parent else 0)
                      if elapsed is not None and (not t['parent_trial_id'] or parent) else None)
        budget = 'unknown' if cumulative is None else 'exceeded' if cumulative > m['budget']['max_elapsed_seconds'] else 'within_reported_wall_limit'
        row = {**t, 'status': status, 'record_sha256': v1.digest(r) if r else None,
               'metrics': _metrics(m, t, r, prior_rows.get(t['parent_trial_id']), status, budget),
               'budget_status': budget, 'cumulative_elapsed_seconds': cumulative}
        rows.append(row); prior_rows[t['id']] = row
    summary = {}
    for target in m['systems']:
        subset = [r for r in rows if r['system_id'] == target['id']]
        summary[target['id']] = {'planned_trials': len(subset), 'statuses': dict(Counter(r['status'] for r in subset)),
            'metrics': {name: reporting._metric_summary([r['metrics'][name] for r in subset]) for name in v1.METRICS},
            'budget_statuses': dict(Counter(r['budget_status'] for r in subset))}
        valid = [by_id[r['id']] for r in subset if r['record_sha256'] is not None]
        summary[target['id']]['telemetry'] = {}
        for name in ('elapsed_seconds', 'model_calls', 'tool_items', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens'):
            values = [r['telemetry'][name] for r in valid if r['telemetry'][name] is not None]
            summary[target['id']]['telemetry'][name] = {'known_trials': len(values),
                'reported_subtotal': sum(values) if values else None,
                'total_if_complete': sum(values) if len(values) == len(subset) else None}
    return {'protocol': PROTOCOL, 'status': 'offline_system_diagnostic_pending_review',
        'manifest_sha256': v1.digest(m), 'evidence_kind': m['evidence_kind'], 'audit': audit,
        'summary': summary, 'rows': rows, 'semantic_support': 'unknown',
        'limits': ['Independent system track; native tool counts and model configurations are not equal-compute framework attribution.',
                   'Fixtures remain scripted controls; external recordings do not authenticate actual model/runtime or reviewer identity.',
                   'Imported programs are not executed or scientifically validated; absent delivery inventory remains pending output review.',
                   'Native scientific execution is unknown until independently checked; shell commands are not simulation proof.',
                   'Wall limits are reported from captures, not enforced by this offline importer; unknown usage is never zero cost.',
                   'Fixed development-only denominators; no new papers, formal holdout results or capability claim.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    plan = sub.add_parser('plan'); plan.add_argument('--output', type=Path, required=True)
    prepare = sub.add_parser('prepare')
    for name in ('manifest', 'trial', 'output'):
        prepare.add_argument('--'+name, required=True, type=str if name == 'trial' else Path)
    prepare.add_argument('--continuation', choices=('none', 'fresh', 'resumed'), default='none')
    prepare.add_argument('--parent', type=Path)
    checking = sub.add_parser('check'); checking.add_argument('task', type=Path)
    command = sub.add_parser('command-plan'); command.add_argument('task', type=Path)
    importing = sub.add_parser('import-codex')
    for name in ('task', 'capture', 'output'):
        importing.add_argument('--'+name, type=Path, required=True)
    importing.add_argument('--exit-code', type=int)
    importing.add_argument('--timed-out', action='store_true')
    importing.add_argument('--elapsed-seconds', type=float, required=True)
    product = sub.add_parser('import-product')
    for name in ('task', 'product-manifest', 'record', 'output'):
        product.add_argument('--'+name, type=Path, required=True)
    product.add_argument('--session-ref', required=True)
    product.add_argument('--parent-session-ref')
    scoring = sub.add_parser('score')
    for name in ('manifest', 'records', 'output'):
        scoring.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'plan':
            m = development_manifest(); audit = validate_manifest(m)
            args.output.mkdir(parents=True, exist_ok=False)
            _write(args.output/'manifest.json', m); _write(args.output/'schedule.json', schedule(m))
            print(json.dumps(audit)); return 0
        if args.action == 'prepare':
            result = prepare_task(_read(args.manifest), args.trial, args.output,
                continuation=args.continuation, parent=_read(args.parent) if args.parent else None)
        elif args.action == 'check':
            _, result, _ = check_task(args.task)
        elif args.action == 'command-plan':
            result = codex_command_plan(args.task)
        elif args.action == 'import-codex':
            result = import_codex(args.task, args.capture, args.output, exit_code=args.exit_code,
                timed_out=args.timed_out, elapsed_seconds=args.elapsed_seconds)
        elif args.action == 'import-product':
            result = import_product(args.task, _read(args.product_manifest), _read(args.record), args.output,
                session_ref=args.session_ref, parent_session_ref=args.parent_session_ref)
        else:
            m, records = _read(args.manifest), _read(args.records)
            result = score(m, records)
            args.output.mkdir(parents=True, exist_ok=False)
            _write(args.output/'manifest.json', m); _write(args.output/'records.json', records)
            _write(args.output/'results.json', result)
        print(json.dumps({'status': result.get('status'), 'protocol': PROTOCOL}))
        if args.action == 'score':
            return 1 if any(row['status'] == 'invalid_record' for row in result['rows']) else 0
        if args.action in ('import-codex', 'import-product'):
            return 0 if result['status'] in ('finished', 'not_run') else 1
        return 0
    except (ValueError, TypeError, KeyError, OSError, StopIteration) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
