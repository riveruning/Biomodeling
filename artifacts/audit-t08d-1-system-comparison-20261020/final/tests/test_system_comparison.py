"""Offline native-system interfaces, not model performance tests."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def system():
    spec = importlib.util.spec_from_file_location('system_comparison_test',
        ROOT/'benchmarks/development/system_comparison.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_has_actual_systems_not_workflow_aliases(system):
    m = system.development_manifest()
    assert [s['runtime'] for s in m['systems']] == ['biomodel_agent', 'codex_cli', 'codex_cli']
    assert len(system.schedule(m)) == 30
    assert system.validate_manifest(m)['real_papers'] == 0
    assert m['evidence_kind'] == 'scripted_control'


def test_current_phase_bundle_contains_no_private_or_future_material(system, tmp_path):
    m = system.development_manifest()
    t = next(t for t in system.schedule(m) if t['system_id'] == 'codex-sol' and
             t['case_id'] == 'translation' and t['phase'] == 'initial')
    prepared = system.prepare_task(m, t['id'], tmp_path/'task')
    runtime = tmp_path/'task'/'runtime'
    assert {p.name for p in runtime.iterdir()} == {'source.txt', 'experiment.json', 'TASK.md'}
    source = (runtime/'source.txt').read_text()
    assert 'Researcher clarification' not in source
    assert 'private_rubric' not in (runtime/'TASK.md').read_text()
    assert prepared['status'] == 'prepared_only_no_authorization'


SID = '0199a213-81c0-7800-8aa1-bbab2a035a53'
OTHER = '0199a213-81c0-7800-8aa1-bbab2a035a54'


def events(sid=SID, usage=None, text='Literal offline fixture message'):
    return [
        {'type': 'thread.started', 'thread_id': sid}, {'type': 'turn.started'},
        {'type': 'item.started', 'item': {'id': 'tool', 'type': 'command_execution', 'status': 'in_progress'}},
        {'type': 'item.completed', 'item': {'id': 'tool', 'type': 'command_execution', 'status': 'completed', 'command': 'echo fixture'}},
        {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': text}},
        {'type': 'turn.completed', 'usage': usage},
    ]


def raw_events(value):
    return ('\n'.join(json.dumps(e) for e in value)+'\n').encode()


def capture(tmp_path, value=None, *, state='completed', declared=True, delivery=True):
    tmp_path.mkdir()
    raw = raw_events(events() if value is None else value)
    (tmp_path/'events.jsonl').write_bytes(raw)
    if delivery:
        (tmp_path/'delivery.json').write_text(json.dumps({'state': state,
            'execution_declared': declared, 'question_texts': ['Please specify P initial value'] if state == 'awaiting_input' else [],
            'artifacts': []}))
    return tmp_path


def prepared(system, path, ident='codex-sol--decay--0--initial', *, m=None, parent=None, mode='none'):
    m = system.development_manifest() if m is None else m
    system.prepare_task(m, ident, path, continuation=mode, parent=parent)
    return m


def imported(system, tmp_path, *, ident='codex-sol--decay--0--initial', state='completed', usage=None):
    task, source, output = tmp_path/'task', tmp_path/'capture', tmp_path/'import'
    m = prepared(system, task, ident)
    capture(source, events(usage=usage), state=state)
    record = system.import_codex(task, source, output, exit_code=0, timed_out=False, elapsed_seconds=2)
    return m, record, task, source, output


def test_native_import_preserves_raw_and_never_proves_execution(system, tmp_path):
    m, record, _, source, output = imported(system, tmp_path)
    assert record['status'] == 'finished'
    assert record['observation']['execution_declared'] is True
    assert record['observation']['execution_observed'] is None
    assert record['telemetry']['model_calls'] is None
    assert record['telemetry']['input_tokens'] is None
    assert record['telemetry']['tool_items'] == 1
    assert record['session']['id'] == SID and record['session']['identity_authenticated'] is False
    assert (output/'events.jsonl').read_bytes() == (source/'events.jsonl').read_bytes()
    scored = system.score(m, [record])
    assert scored['rows'][10]['metrics']['correct_completion']['value'] is None
    assert scored['summary']['codex-sol']['metrics']['correct_completion']['denominator'] == 6


def test_explicit_usage_and_cached_tokens_are_retained(system, tmp_path):
    _, r, *_ = imported(system, tmp_path, usage={'input_tokens': 123, 'cached_input_tokens': 100, 'output_tokens': 12, 'reasoning_output_tokens': 5})
    assert {k: r['telemetry'][k] for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')} == {
        'input_tokens': 123, 'cached_input_tokens': 100, 'output_tokens': 12, 'reasoning_output_tokens': 5}


@pytest.mark.parametrize('field,value', [('input_tokens', True), ('output_tokens', -1),
                                      ('input_tokens', 1.2), ('cached_input_tokens', 124)])
def test_invalid_usage_cannot_be_normalized_as_free(system, field, value):
    usage = {'input_tokens': 123, 'cached_input_tokens': 100, 'output_tokens': 12}
    usage[field] = value
    assert system.native.parse_events(raw_events(events(usage=usage)))['status'] == 'invalid_events'


@pytest.mark.parametrize('change', ['two_threads', 'two_turns', 'after_terminal', 'duplicate_item', 'unknown_event', 'unknown_item', 'no_start'])
def test_native_event_order_and_version_guards(system, change):
    data = events()
    if change == 'two_threads':
        data.insert(1, data[0])
    elif change == 'two_turns':
        data.insert(2, data[1])
    elif change == 'after_terminal':
        data.append({'type': 'turn.started'})
    elif change == 'duplicate_item':
        data.insert(-1, data[-2])
    elif change == 'unknown_event':
        data.insert(2, {'type': 'new.vendor.event'})
    elif change == 'unknown_item':
        data[2]['item']['type'] = 'unmapped_tool'
    else:
        data.pop(1)
    result = system.native.parse_events(raw_events(data))
    assert result['status'] == 'invalid_events'
    assert result['diagnostics']


@pytest.mark.parametrize('raw', [b'{"type":"error","type":"turn.completed"}\n',
                               b'{"type":"error","value":NaN}\n', b'\xff\n'])
def test_invalid_json_is_not_silently_discarded(system, raw):
    assert system.native.parse_events(raw)['status'] == 'invalid_events'


def test_partial_events_and_timeout_preserve_delivered_files(system, tmp_path):
    task = tmp_path/'task'
    prepared(system, task)
    source = capture(tmp_path/'capture', state='incomplete')
    raw = raw_events(events()[:-1])+b'{"type":'
    (source/'events.jsonl').write_bytes(raw)
    program = b'raise AssertionError("Submitted programs must remain opaque")\n'
    (source/'simulate.py').write_bytes(program)
    inventory = json.loads((source/'delivery.json').read_text())
    inventory['artifacts'] = [{'path': 'simulate.py', 'role': 'program', 'sha256': system._sha(program)}]
    (source/'delivery.json').write_text(json.dumps(inventory))
    output = tmp_path/'result'
    record = system.import_codex(task, source, output, exit_code=None, timed_out=True, elapsed_seconds=180)
    assert record['status'] == 'timeout'
    assert 'truncated_last_line' in record['diagnostics']
    assert (output/'events.jsonl').read_bytes() == raw
    assert (output/'artifacts/simulate.py').read_bytes() == program


def test_partial_inventory_keeps_earlier_verified_artifacts(system, tmp_path):
    task = tmp_path/'task'; prepared(system, task)
    source = capture(tmp_path/'capture')
    program = b'# opaque partial program\n'
    (source/'program.py').write_bytes(program)
    delivery = json.loads((source/'delivery.json').read_text())
    delivery['artifacts'] = [{'path': 'program.py', 'role': 'program', 'sha256': system._sha(program)},
                             {'path': 'missing.json', 'role': 'trajectory', 'sha256': '0'*64}]
    (source/'delivery.json').write_text(json.dumps(delivery))
    output = tmp_path/'import'
    r = system.import_codex(task, source, output, exit_code=0, timed_out=False, elapsed_seconds=1)
    assert r['status'] == 'invalid_delivery'
    assert (output/'artifacts/program.py').read_bytes() == program
    assert (output/'delivery.original.json').read_bytes() == (source/'delivery.json').read_bytes()


@pytest.mark.parametrize('name', ['../outside', '/etc/passwd', 'dir/../outside', './program.py', 'dir//program.py', 'bad\\file'])
def test_delivery_paths_are_confined(system, tmp_path, name):
    source = capture(tmp_path/'capture')
    value = json.loads((source/'delivery.json').read_text())
    value['artifacts'] = [{'path': name, 'role': 'program', 'sha256': '0'*64}]
    (source/'delivery.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        system.native.read_delivery(source)


def test_symlink_and_fifo_outputs_are_refused_without_blocking(system, tmp_path):
    root = tmp_path/'files'; root.mkdir()
    outside = tmp_path/'outside'; outside.write_bytes(b'private canary')
    (root/'link').symlink_to(outside)
    with pytest.raises(ValueError):
        system.native.read_file(root, 'link')
    import os
    os.mkfifo(root/'fifo')
    with pytest.raises(ValueError):
        system.native.read_file(root, 'fifo')


def test_resume_uses_exact_session_id_and_cumulative_time(system, tmp_path):
    m, parent, _, _, _ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    child_task = tmp_path/'child-task'
    prepared(system, child_task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='resumed')
    plan = system.codex_command_plan(child_task)
    assert '--ephemeral' not in plan['argv'] and '--last' not in plan['argv']
    assert plan['argv'][-3:] == ['resume', SID, '-']
    assert plan['outer_wall_timeout_seconds'] == 178
    source = capture(tmp_path/'child-capture')
    child = system.import_codex(child_task, source, tmp_path/'child-import', exit_code=0, timed_out=False, elapsed_seconds=3)
    assert child['session']['mode'] == 'resumed'
    assert child['telemetry']['cumulative_elapsed_seconds'] == 5
    score = system.score(m, [parent, child])
    r = next(r for r in score['rows'] if r['id'] == child['trial']['id'])
    assert r['cumulative_elapsed_seconds'] == 5
    assert r['metrics']['clarification_recovery']['value'] is None


def test_wrong_resume_identity_is_refused_before_output(system, tmp_path):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    task = tmp_path/'child'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='resumed')
    source = capture(tmp_path/'wrong', events(sid=OTHER))
    out = tmp_path/'refused'
    with pytest.raises(ValueError, match='another session'):
        system.import_codex(task, source, out, exit_code=0, timed_out=False, elapsed_seconds=2)
    assert not out.exists()


def test_fresh_child_does_not_reset_budget_or_get_resume_credit(system, tmp_path):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    task = tmp_path/'fresh'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='fresh')
    source = capture(tmp_path/'new-capture', events(sid=OTHER))
    child = system.import_codex(task, source, tmp_path/'new-import', exit_code=0, timed_out=False, elapsed_seconds=179)
    r = next(r for r in system.score(m, [parent, child])['rows'] if r['id'] == child['trial']['id'])
    assert r['budget_status'] == 'exceeded' and r['cumulative_elapsed_seconds'] == 181
    assert r['metrics']['clarification_recovery']['value'] is False
    assert r['metrics']['within_budget_completion']['value'] is False


@pytest.mark.parametrize('change', ['system', 'repeat', 'failure', 'changed_model'])
def test_parent_binding_and_no_silent_restart(system, tmp_path, change):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    if change == 'system':
        ident = 'codex-astra--translation--0--clarified'
    elif change == 'repeat':
        ident = 'codex-sol--translation--1--clarified'
    else:
        ident = 'codex-sol--translation--0--clarified'
        if change == 'failure': parent['status'] = 'timeout'
        else: m['systems'][1]['model'] = 'explicitly-other-model'
    out = tmp_path/'refused'
    with pytest.raises(ValueError):
        prepared(system, out, ident, m=m, parent=parent, mode='resumed')
    assert not out.exists()


def test_missing_inventory_remains_pending_output_review(system, tmp_path):
    task = tmp_path/'task'; m = prepared(system, task)
    source = capture(tmp_path/'capture', delivery=False)
    record = system.import_codex(task, source, tmp_path/'import', exit_code=0, timed_out=False, elapsed_seconds=2)
    assert record['observation']['state'] == 'not_submitted'
    assert record['observation']['final_messages']
    r = next(r for r in system.score(m, [record])['rows'] if r['id'] == record['trial']['id'])
    assert r['metrics']['correct_completion']['value'] is None


@pytest.mark.parametrize('exit_code,timed_out,expected', [(1, False, 'process_error'), (None, False, 'incomplete_events'),
                                                        (0, True, 'timeout')])
def test_process_metadata_keeps_operational_failures(system, tmp_path, exit_code, timed_out, expected):
    task = tmp_path/'task'; prepared(system, task)
    source = capture(tmp_path/'capture')
    r = system.import_codex(task, source, tmp_path/'import', exit_code=exit_code, timed_out=timed_out, elapsed_seconds=2)
    assert r['status'] == expected


def test_native_error_is_not_dropped_by_later_completion(system):
    data = events(); data.insert(2, {'type': 'error', 'message': 'literal control service failure'})
    assert system.native.parse_events(raw_events(data))['status'] == 'provider_error'


@pytest.mark.parametrize('change', ['runtime_text', 'future_file', 'preparation_flag', 'rubric', 'implementation'])
def test_preparation_freshness_and_visibility_guards(system, tmp_path, change):
    task = tmp_path/'task'; prepared(system, task)
    if change == 'runtime_text': (task/'runtime/source.txt').write_text('altered')
    elif change == 'future_file': (task/'runtime/future-answer.txt').write_text('not authorized')
    elif change == 'preparation_flag':
        p = system._read(task/'preparation.json'); p['status'] = 'approved'; system._write(task/'preparation.json', p)
    else:
        m = system._read(task/'manifest.json')
        if change == 'rubric': m['cases'][0]['phases'][0]['private_rubric']['execution_expected'] = False
        else: m['implementation']['native_capture.py'] = '0'*64
        system._write(task/'manifest.json', m)
    with pytest.raises(ValueError): system.check_task(task)


def test_live_evidence_label_is_not_supported_by_offline_import(system):
    m = system.development_manifest(); m['evidence_kind'] = 'live_model'
    with pytest.raises(ValueError): system.validate_manifest(m)


def test_product_actual_saved_record_imports_without_changing_scoring(system, tmp_path):
    old = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'
    native_manifest = system._read(old/'manifest.json')
    records, receipts = system._read(old/'records.json'), system._read(old/'receipts.json')
    record = next(r for r in records if r['trial_id'] == 'domain_agent--decay--0--initial')
    receipt = next(r for r in receipts if r['trial_id'] == record['trial_id'])
    task = tmp_path/'task'; m = prepared(system, task, 'biomodel--decay--0--initial')
    result = system.import_product(task, native_manifest, record, tmp_path/'import', session_ref=receipt['session_ref'])
    assert result['observation']['execution_observed'] is True
    assert result['assessment'] == system.ASSESSMENT
    assert result['telemetry']['tool_count_kind'] == 'domain_tool_invocations'
    assert system._read(tmp_path/'import/product-record.json') == record
    assert system.score(m, [result])['summary']['biomodel']['metrics']['correct_completion']['unknown'] == 6


def test_fixed_denominator_and_duplicate_guards(system, tmp_path):
    m, r, *_ = imported(system, tmp_path)
    empty = system.score(m, [])
    for s in empty['summary'].values():
        assert s['planned_trials'] == 10
        assert s['metrics']['correct_completion']['counts']['missing_record'] == 6
        assert s['metrics']['targeted_question']['denominator'] == 4
        assert s['telemetry']['input_tokens']['total_if_complete'] is None
    with pytest.raises(ValueError): system.score(m, [r, r])
    r['assessment']['numeric'] = 'pass'
    scored = system.score(m, [r])
    assert scored['summary']['codex-sol']['statuses']['invalid_record'] == 1
    assert scored['summary']['codex-sol']['metrics']['correct_completion']['counts']['invalid_record'] == 1


def test_byte_and_event_limits(system):
    with pytest.raises(ValueError): system.native.parse_events(b'x'*(system.native.MAX_BYTES+1))
    raw = b'{}\n'*(system.native.MAX_EVENTS+1)
    assert system.native.parse_events(raw)['status'] == 'invalid_events'


def test_unknown_execution_does_not_become_confirmed_overblocking(system, tmp_path):
    m, r, *_ = imported(system, tmp_path, state='awaiting_input')
    result = next(x for x in system.score(m, [r])['rows'] if x['id'] == r['trial']['id'])
    assert result['metrics']['overblocking']['value'] is None


def test_utf16_native_stream_is_outside_utf8_profile(system):
    raw = json.dumps(events()[0]).encode('utf-16')+b'\n'
    assert system.native.parse_events(raw)['status'] == 'invalid_events'


def test_malformed_artifact_metadata_is_invalid_in_fixed_denominator(system, tmp_path):
    m, r, *_ = imported(system, tmp_path)
    r['observation']['artifacts'] = [{'path': '../operator-answer', 'role': 'program', 'sha256': '0'*64}]
    result = next(x for x in system.score(m, [r])['rows'] if x['id'] == r['trial']['id'])
    assert result['status'] == 'invalid_record'


def test_cross_case_native_session_reuse_cannot_fake_independent_runs(system, tmp_path):
    m, first, *_ = imported(system, tmp_path)
    task = tmp_path/'second-task'
    prepared(system, task, 'codex-sol--translation--0--initial', m=m)
    source = capture(tmp_path/'second-capture', state='awaiting_input')
    second = system.import_codex(task, source, tmp_path/'second-import', exit_code=0, timed_out=False, elapsed_seconds=2)
    result = system.score(m, [first, second])
    assert all(next(r for r in result['rows'] if r['id'] == record['trial']['id'])['status'] == 'invalid_record'
               for record in (first, second))


def test_fresh_mode_rejects_reused_parent_session(system, tmp_path):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    task = tmp_path/'fresh-task'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='fresh')
    source = capture(tmp_path/'fresh-capture')
    with pytest.raises(ValueError, match='reused parent'):
        system.import_codex(task, source, tmp_path/'refused', exit_code=0, timed_out=False, elapsed_seconds=2)


def test_exhausted_budget_cannot_create_an_inference_command(system, tmp_path):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    parent['telemetry'].update(elapsed_seconds=180, cumulative_elapsed_seconds=180)
    task = tmp_path/'child'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='resumed')
    assert system.check_task(task)[1]['remaining_elapsed_seconds'] == 0
    with pytest.raises(ValueError, match='remaining budget'):
        system.codex_command_plan(task)


@pytest.mark.parametrize('change', ['parent_digest', 'parent_session', 'cumulative_time'])
def test_imported_child_identity_and_time_are_rechecked_by_report(system, tmp_path, change):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    task = tmp_path/'child'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='resumed')
    source = capture(tmp_path/'child-capture')
    child = system.import_codex(task, source, tmp_path/'child-import', exit_code=0, timed_out=False, elapsed_seconds=2)
    if change == 'parent_digest': child['session']['parent_record_sha256'] = '0'*64
    elif change == 'parent_session': child['session']['id'] = OTHER
    else: child['telemetry']['cumulative_elapsed_seconds'] = 2
    result = next(r for r in system.score(m, [parent, child])['rows'] if r['id'] == child['trial']['id'])
    assert result['status'] == 'invalid_record'


def test_missing_parent_keeps_child_budget_unknown(system, tmp_path):
    m, parent, *_ = imported(system, tmp_path, ident='codex-sol--translation--0--initial', state='awaiting_input')
    task = tmp_path/'child'; prepared(system, task, 'codex-sol--translation--0--clarified', m=m, parent=parent, mode='resumed')
    source = capture(tmp_path/'child-capture')
    child = system.import_codex(task, source, tmp_path/'child-import', exit_code=0, timed_out=False, elapsed_seconds=2)
    result = next(r for r in system.score(m, [child])['rows'] if r['id'] == child['trial']['id'])
    assert result['budget_status'] == 'unknown'
    assert result['metrics']['clarification_recovery']['value'] is None


@pytest.mark.parametrize('field,value', [('exit_code', True), ('exit_code', '0'),
                                       ('timed_out', 1), ('elapsed_seconds', float('nan'))])
def test_process_metadata_requires_strict_types(system, tmp_path, field, value):
    task = tmp_path/'task'; prepared(system, task)
    source = capture(tmp_path/'capture')
    options = {'exit_code': 0, 'timed_out': False, 'elapsed_seconds': 2}; options[field] = value
    with pytest.raises(ValueError): system.import_codex(task, source, tmp_path/'refused', **options)
    assert not (tmp_path/'refused').exists()


def test_external_recording_is_still_not_authenticated_live_evidence(system, tmp_path):
    m = system.development_manifest(); m['evidence_kind'] = 'external_recording'
    task = tmp_path/'task'; prepared(system, task, m=m)
    source = capture(tmp_path/'capture')
    r = system.import_codex(task, source, tmp_path/'import', exit_code=0, timed_out=False, elapsed_seconds=2)
    assert r['evidence_kind'] == 'external_recording'
    assert r['session']['identity_authenticated'] is False
    assert system.score(m, [r])['semantic_support'] == 'unknown'


def test_product_fixture_cannot_be_relabelled_external_recording(system, tmp_path):
    old = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'
    records = system._read(old/'records.json')
    record = next(r for r in records if r['trial_id'] == 'domain_agent--decay--0--initial')
    m = system.development_manifest(); m['evidence_kind'] = 'external_recording'
    task = tmp_path/'task'; prepared(system, task, 'biomodel--decay--0--initial', m=m)
    with pytest.raises(ValueError, match='evidence/model'):
        system.import_product(task, system._read(old/'manifest.json'), record, tmp_path/'refused', session_ref='0'*64)


def test_cli_roundtrip_and_existing_output_refusal(system, tmp_path, capsys):
    suite = tmp_path/'suite'
    assert system.main(['plan', '--output', str(suite)]) == 0
    task = tmp_path/'task'
    assert system.main(['prepare', '--manifest', str(suite/'manifest.json'), '--trial', 'codex-sol--decay--0--initial', '--output', str(task)]) == 0
    assert system.main(['check', str(task)]) == 0
    assert system.main(['command-plan', str(task)]) == 0
    source = capture(tmp_path/'capture'); output = tmp_path/'import'
    args = ['import-codex', '--task', str(task), '--capture', str(source), '--output', str(output), '--exit-code', '0', '--elapsed-seconds', '2']
    assert system.main(args) == 0
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert system.main(args) == 2
    assert before == {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    records = tmp_path/'records.json'; system._write(records, [system._read(output/'record.json')])
    scored = tmp_path/'score'
    assert system.main(['score', '--manifest', str(suite/'manifest.json'), '--records', str(records), '--output', str(scored)]) == 0
    assert len(system._read(scored/'results.json')['rows']) == 30


def test_cli_saves_failed_capture_and_returns_failure_status(system, tmp_path, capsys):
    task = tmp_path/'task'; prepared(system, task)
    source = capture(tmp_path/'capture'); output = tmp_path/'timeout'
    assert system.main(['import-codex', '--task', str(task), '--capture', str(source), '--output', str(output),
                        '--timed-out', '--elapsed-seconds', '180']) == 1
    assert system._read(output/'record.json')['status'] == 'timeout'


def test_unknown_or_missing_json_refuses_before_output(system, tmp_path, capsys):
    bad = tmp_path/'bad.json'; bad.write_text('{"x":1,"x":2}')
    out = tmp_path/'refused'
    assert system.main(['prepare', '--manifest', str(bad), '--trial', 'bad', '--output', str(out)]) == 2
    assert not out.exists()
