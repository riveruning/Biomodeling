"""Audit durable HTTP receipts and actual science separately from model scores."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from biomodel_agent.recovery import open_readonly_store

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
RUN = OUT/'http-mock-execution'
PRIOR = ROOT/'artifacts/audit-t08b-f1-equation-prose-20261019/development-execution'


def read(path):
    return json.loads(path.read_text())


def main():
    records, receipts = read(RUN/'records.json'), read(RUN/'receipts.json')
    old_records = {r['trial_id']: r for r in read(PRIOR/'records.json')}
    old_receipts = {r['trial_id']: r for r in read(PRIOR/'receipts.json')}
    by_id = {r['trial_id']: r for r in records}
    receipt_by_id = {r['trial_id']: r for r in receipts}
    assert len(records) == len(receipts) == 30
    assert Counter(r['status'] for r in records) == {'finished': 30}
    assert Counter(r['observation']['state'] for r in records) == {'completed': 18, 'awaiting_input': 12}
    counts, native_resumes, science = 0, [], []
    series = defaultdict(list)
    for receipt in receipts:
        r = by_id[receipt['trial_id']]
        assert r['observation'] == old_records[r['trial_id']]['observation']
        assert r['evidence_kind'] == 'scripted_control'
        assert r['assessment']['authority'] == 'unreviewed'
        assert r['assessment']['source_support'] == r['assessment']['numeric'] == 'unknown'
        assert r['telemetry']['input_tokens'] is None and r['telemetry']['output_tokens'] is None
        store = open_readonly_store(receipt['store_root'])
        assert len(receipt['audit']['transport_exchange_refs']) == r['telemetry']['model_calls']
        for ref in receipt['audit']['transport_exchange_refs']:
            entry = store.get(ref)
            assert entry['kind'] == 'evaluation_transport_exchange'
            exchange = entry['payload']['exchange']
            assert exchange['outcome'] == 'ok' and exchange['http_status'] == 200
            request_ref, = entry['refs']
            assert request_ref in receipt['audit']['request_refs']
            request = store.get(request_ref)
            import hashlib
            raw = json.dumps(request['payload']['request'], ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
            assert exchange['request_body_sha256'] == hashlib.sha256(raw).hexdigest()
            reservation_ref, = request['refs']
            assert store.get(reservation_ref)['kind'] == 'evaluation_reservation'
            counts += 1
        native = receipt['audit']['native_agent_run_ref']
        if native and r['parent_trial_id']:
            parent = receipt_by_id[r['parent_trial_id']]
            assert store.get(native)['payload']['parent_run_ref'] == parent['audit']['native_agent_run_ref']
            native_resumes.append(r['trial_id'])
        if not receipt['audit']['run_ref']:
            continue
        run = store.get(receipt['audit']['run_ref'])['payload']
        assert run['status'] == 'succeeded' and run['cached_from'] is None
        assert run['scientific_status'] == 'not_verified'
        assert run['source_validation']['binding_status'] == 'matched'
        assert run['source_validation']['semantic_support'] == 'unknown'
        checks = run['numeric_validation']['checks']
        assert all(any(c['code'] == k and c['status'] == 'pass' for c in checks) for k in ('finite', 'convergence'))
        sim = run['simulation']
        case = store.get(receipt['session_ref'])['payload']['trial']['case_id']
        expected = ({'A': [2*math.exp(-.4*t) for t in sim['times']]} if case == 'decay' else
                    {'M': [2-math.exp(-.4*t) for t in sim['times']],
                     'P': [6+3*math.exp(-.4*t)-8.8*math.exp(-.2*t) for t in sim['times']]} if case == 'translation' else
                    {'X': [2/(1+9*math.exp(-.8*t)) for t in sim['times']]})
        fine = store.get(run['convergence_ref'])['payload']['result']
        errors = {k: max(abs(a-b) for a, b in zip(sim['values'][k], values)) for k, values in expected.items()}
        fine_errors = {k: max(abs(a-b) for a, b in zip(fine['values'][k], values)) for k, values in expected.items()}
        old_receipt = old_receipts[r['trial_id']]
        old_store = open_readonly_store(old_receipt['store_root'])
        old_run = old_store.get(old_receipt['audit']['run_ref'])['payload']
        assert all(sim[key] == old_run['simulation'][key] for key in ('times', 'values', 'status', 'backend', 'time_unit'))
        series[case].append(sim['values'])
        science.append({'trial_id': r['trial_id'], 'run_ref': receipt['audit']['run_ref'],
                        'max_absolute_error_vs_analytic': errors, 'refined_max_absolute_error_vs_analytic': fine_errors,
                        'scientific_status': run['scientific_status'], 'semantic_support': 'unknown'})
    assert counts == 66 and len(native_resumes) == 4 and len(science) == 18
    for values in series.values():
        assert len(values) == 6 and all(value == values[0] for value in values)
    result = {'status': 'engineering_and_numeric_invariants_passed', 'planned_phases': 30,
              'observed_states': {'completed': 18, 'awaiting_input': 12}, 'http_mock_requests_and_receipts': counts,
              'online_inference_calls': 0, 'actual_reproduction_runs': 18, 'convergence_fine_runs': 18,
              'unchanged_numeric_outputs_vs_F1': 18, 'native_original_task_resumes': native_resumes,
              'per_case_actual_runs': {case: len(values) for case, values in series.items()},
              'max_numeric_difference_across_workflows': 0, 'source_support': 'unknown',
              'human_review_decisions': 0, 'scientific_runs': science,
              'limit': 'Literal scripted decisions over in-memory HTTP. Numeric diagnostics do not authenticate model identity, provide semantic approval or constitute real model comparison. Existing solver checks/settings and all prior precision failures remain unchanged.'}
    (OUT/'http-scientific-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'scientific_runs'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
