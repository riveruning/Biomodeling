"""Actual saved solver results; decisions are literal fixtures, never an LLM."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from biomodel_agent.storage import ArtifactStore
from biomodel_agent.recovery import open_readonly_store

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
RUN = OUT/'development-execution'
PRIOR = ROOT/'artifacts/audit-t08b-workflow-adapters-20261019/development-execution'


def main():
    records = json.loads((RUN/'records.json').read_text())
    receipts = json.loads((RUN/'receipts.json').read_text())
    old_records = json.loads((PRIOR/'records.json').read_text())
    old_receipts = json.loads((PRIOR/'receipts.json').read_text())
    assert len(records) == len(receipts) == 30
    assert Counter(r['observation']['state'] for r in records) == {'completed': 18, 'awaiting_input': 12}
    by_id = {r['trial_id']: r for r in records}
    old_by_id = {r['trial_id']: r for r in old_records}
    old_receipt_by_id = {r['trial_id']: r for r in old_receipts}
    receipts_by_id = {r['trial_id']: r for r in receipts}
    runs, resumed, changes = [], [], []
    series = defaultdict(list)
    for receipt in receipts:
        record = by_id[receipt['trial_id']]
        store = ArtifactStore(receipt['store_root'])
        state = store.get(receipt['session_ref'])['payload']
        assert record['assessment']['authority'] == 'unreviewed'
        assert record['assessment']['source_support'] == record['assessment']['numeric'] == 'unknown'
        assert record['status'] == 'finished'
        if old_by_id[record['trial_id']]['observation']['state'] != record['observation']['state']:
            assert '--logistic--' in record['trial_id'] and record['trial_id'].endswith('--clarified')
            changes.append(record['trial_id'])
        if receipt['audit']['native_agent_run_ref']:
            native = store.get(receipt['audit']['native_agent_run_ref'])['payload']
            if record['parent_trial_id']:
                parent = receipts_by_id[record['parent_trial_id']]
                assert native['parent_run_ref'] == parent['audit']['native_agent_run_ref']
                resumed.append({'trial_id': record['trial_id'], 'parent_trial_id': record['parent_trial_id'],
                                'native_parent_ref': native['parent_run_ref']})
        run_ref = receipt['audit']['run_ref']
        if not run_ref:
            assert record['observation']['state'] == 'awaiting_input'
            assert record['trial_id'].endswith('--initial')
            continue
        run = store.get(run_ref)['payload']
        assert run['status'] == 'succeeded' and run['cached_from'] is None
        assert run['scientific_status'] == 'not_verified'
        assert run['source_validation']['binding_status'] == 'matched'
        assert run['source_validation']['semantic_support'] == 'unknown'
        checks = run['numeric_validation']['checks']
        assert all(any(c['code'] == k and c['status'] == 'pass' for c in checks) for k in ('finite', 'convergence'))
        sim = run['simulation']
        case = state['trial']['case_id']
        if case == 'decay':
            expected = {'A': [2*math.exp(-.4*t) for t in sim['times']]}
        elif case == 'translation':
            expected = {'M': [2-math.exp(-.4*t) for t in sim['times']],
                        'P': [6+3*math.exp(-.4*t)-8.8*math.exp(-.2*t) for t in sim['times']]}
        elif case == 'logistic':
            expected = {'X': [2/(1+9*math.exp(-.8*t)) for t in sim['times']]}
        else:
            raise AssertionError('unrecognized scientific case executed')
        assert set(expected) == set(sim['values'])
        fine = store.get(run['convergence_ref'])['payload']['result']
        assert sim['times'] == fine['times']
        errors, fine_errors = {}, {}
        for name, values in expected.items():
            assert len(values) == len(sim['values'][name]) == len(fine['values'][name])
            assert abs(sim['values'][name][0]-values[0]) < 1e-12
            errors[name] = max(abs(a-b) for a, b in zip(sim['values'][name], values))
            fine_errors[name] = max(abs(a-b) for a, b in zip(fine['values'][name], values))
            assert math.isfinite(errors[name]) and math.isfinite(fine_errors[name])
        # Old completed phases must retain exactly the same solver output.
        old_receipt = old_receipt_by_id[record['trial_id']]
        if old_receipt['audit']['run_ref']:
            old_store = open_readonly_store(old_receipt['store_root'])
            old_run = old_store.get(old_receipt['audit']['run_ref'])['payload']
            # Runtime duration is telemetry, not a solver output identity.
            assert all(old_run['simulation'][key] == sim[key]
                       for key in ('status', 'times', 'values', 'backend', 'time_unit'))
        if case == 'logistic':
            claim = next(store.get(ref)['payload'] for ref in store.list('claim')
                         if store.get(ref)['payload']['statement'] == 'rule:X/expression')
            assert claim['claim_type'] == 'human_convention' and claim['review_status'] == 'unverified'
            assert claim['quote'] == 'The author confirms the intended equation is dX/dt = r*X*(1-X/K).'
            declaration, = claim['binding']['declarations']
            assert declaration['start'] == claim['start'] and declaration['end'] == claim['end']-1
        series[case].append(sim['values'])
        runs.append({'trial_id': record['trial_id'], 'run_ref': run_ref, 'solver_backend': sim['backend'],
                     'cached_from': run['cached_from'], 'max_absolute_error_vs_analytic': errors,
                     'refined_max_absolute_error_vs_analytic': fine_errors,
                     'old_exploratory_2e_6_check_satisfied': all(v < 2e-6 for v in errors.values()),
                     'scientific_status': run['scientific_status'], 'semantic_support': 'unknown'})
    assert len(changes) == 6 and len(resumed) == 4 and len(runs) == 18
    comparisons = {}
    for case, trajectories in series.items():
        assert len(trajectories) == 6
        baseline = trajectories[0]
        worst = max(abs(a-b) for data in trajectories[1:] for name in baseline
                    for a, b in zip(data[name], baseline[name]))
        assert worst < 1e-10
        comparisons[case] = {'actual_runs': len(trajectories), 'max_difference_across_workflows_and_repeats': worst}
    report = {'status': 'engineering_invariants_passed_analytic_diagnostics_recorded',
              'scope': 'Seen synthetic development controls; scripted decisions and actual tools, not model performance',
              'planned_phases': 30, 'technical_completed_phases': 18, 'initial_scripted_questions': 12,
              'changed_states': changes, 'unchanged_previously_completed_numeric_results': 12,
              'scripted_provider_requests': sum(r['telemetry']['model_calls'] for r in records),
              'online_inference_calls': 0, 'actual_reproduction_runs': len(runs),
              'convergence_fine_runs': len(runs), 'actual_native_agent_resumes': resumed,
              'scientific_runs': runs, 'cross_workflow_numeric_comparison': comparisons,
              'source_support': 'unknown', 'human_review_decisions': 0,
              'analytic_limit': 'Global analytic errors are diagnostics. The prior exploratory 2e-6 failure remains immutable; no replacement threshold or solver setting change is adopted. Existing finite/convergence checks remain the product criterion.'}
    (OUT/'scientific-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('scientific_runs', 'actual_native_agent_resumes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
