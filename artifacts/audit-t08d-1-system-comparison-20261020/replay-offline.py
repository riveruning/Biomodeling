"""Thirty-phase interface control: ten old product records, twenty fake JSONL captures."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from biomodel_agent.recovery import open_readonly_store

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
spec = importlib.util.spec_from_file_location('system_offline_replay', ROOT/'benchmarks/development/system_comparison.py')
system = importlib.util.module_from_spec(spec); spec.loader.exec_module(system)
RUN = OUT/'offline-replay'; RUN.mkdir(exist_ok=False)
old = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'
old_manifest = system._read(old/'manifest.json')
old_records = {r['trial_id']: r for r in system._read(old/'records.json')}
old_receipts = {r['trial_id']: r for r in system._read(old/'receipts.json')}
m = system.development_manifest()
system._write(RUN/'manifest.json', m); system._write(RUN/'schedule.json', system.schedule(m))
records, parents, previous_receipts, command_plans = [], {}, {}, []
verified_native_product_parents = []
for t in system.schedule(m):
    key = (t['system_id'], t['case_id'], t['repeat'])
    parent = parents.get(key)
    task = RUN/'tasks'/t['id']
    system.prepare_task(m, t['id'], task, continuation='resumed' if parent else 'none', parent=parent)
    output = RUN/'imports'/t['id']
    if t['runtime'] == 'biomodel_agent':
        ident = f'domain_agent--{t["case_id"]}--{t["repeat"]}--{t["phase"]}'
        old_record, receipt = old_records[ident], old_receipts[ident]
        record = system.import_product(task, old_manifest, old_record, output,
            session_ref=receipt['session_ref'], parent_session_ref=parent['session']['id'] if parent else None)
        if parent:
            old_parent = previous_receipts[key]
            store = open_readonly_store(receipt['store_root'])
            native_run = store.get(receipt['audit']['native_agent_run_ref'])['payload']
            assert native_run['parent_run_ref'] == old_parent['audit']['native_agent_run_ref']
            verified_native_product_parents.append(t['id'])
        previous_receipts[key] = receipt
    else:
        command_plan = system.codex_command_plan(task)
        command_plans.append({'trial_id': t['id'], **command_plan})
        capture = RUN/'literal-fixtures'/t['id']; capture.mkdir(parents=True)
        sid = str(uuid5(NAMESPACE_URL, 'offline-control:'+':'.join(map(str, key))))
        complete = t['case_id'] == 'decay' or t['phase'] == 'clarified'
        events = [{'type': 'thread.started', 'thread_id': sid}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'id': 'tool', 'type': 'command_execution',
                'command': 'literal interface fixture; no command actually executed', 'status': 'completed'}},
            {'type': 'item.completed', 'item': {'id': 'message', 'type': 'agent_message',
                'text': 'Literal offline interface control; not an actual model response.'}},
            {'type': 'turn.completed', 'usage': None}]
        (capture/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        artifacts = []
        if complete:
            program = b'raise RuntimeError("Interface fixture only, not a scientific solution")\n'
            (capture/'program.py').write_bytes(program)
            artifacts = [{'path': 'program.py', 'role': 'program', 'sha256': system._sha(program)}]
        system._write(capture/'delivery.json', {'state': 'completed' if complete else 'awaiting_input',
            'execution_declared': complete, 'question_texts': [] if complete else [
                'What is the initial P value?' if t['case_id'] == 'translation' else 'Which X equation is intended?'],
            'artifacts': artifacts})
        record = system.import_codex(task, capture, output, exit_code=0, timed_out=False, elapsed_seconds=0.01)
    records.append(record); parents[key] = record
    system._write(RUN/'records.json', records)
result = system.score(m, records)
assert len(records) == 30 and Counter(r['status'] for r in records) == {'finished': 30}
assert Counter(r['observation']['state'] for r in records) == {'completed': 18, 'awaiting_input': 12}
assert sum(s['metrics']['correct_completion']['counts']['pending_adjudication'] for s in result['summary'].values()) == 18
assert all(s['metrics']['correct_completion']['true'] == 0 for s in result['summary'].values())
assert all(r['assessment'] == system.ASSESSMENT for r in records)
assert all(s['telemetry']['input_tokens']['total_if_complete'] is None for s in result['summary'].values())
assert len(verified_native_product_parents) == 4
for plan in command_plans:
    assert '--ephemeral' not in plan['argv'] and '--last' not in plan['argv']
system._write(RUN/'results.json', result); system._write(RUN/'command-plans.json', command_plans)
summary = {'status': 'offline_interface_replay_verified', 'planned_phases': 30,
    'old_product_records_imported': 10, 'literal_native_event_fixtures_imported': 20,
    'pending_completion_adjudications': 18, 'initial_gap_phases': 12,
    'actual_old_native_product_resume_relations_verified': verified_native_product_parents,
    'literal_Codex_session_resume_relations': 8, 'actual_native_Codex_model_runs': 0,
    'new_comparison_scientific_runs': 0, 'online_inference_calls': 0,
    'actual_independent_review_decisions': 0,
    'limit': 'Native events are deliberately fabricated controls and opaque dummy programs, never model or scientific results. Product scientific effects are historical T08c effects, not new executions.'}
system._write(RUN/'engineering.json', summary)
print(json.dumps(summary))
