"""Check actual saved trajectories and disclosure of the shared parser limit."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from biomodel_agent.storage import ArtifactStore

OUT=Path(__file__).resolve().parent
RUN=OUT/'development-execution'


def main():
    records=json.loads((RUN/'records.json').read_text())
    receipts=json.loads((RUN/'receipts.json').read_text())
    assert len(records)==len(receipts)==30
    assert Counter(r['observation']['state'] for r in records)=={'completed':12,'awaiting_input':18}
    by_id={r['trial_id']:r for r in records}; runs=[]; series=defaultdict(list); limits=[]
    for receipt in receipts:
        record=by_id[receipt['trial_id']]; store=ArtifactStore(receipt['store_root'])
        state=store.get(receipt['session_ref'])['payload']
        assert record['assessment']['authority']=='unreviewed'
        assert record['assessment']['source_support']==record['assessment']['numeric']=='unknown'
        if receipt['audit']['native_agent_run_ref']:
            native=store.get(receipt['audit']['native_agent_run_ref'])['payload']
            if record['parent_trial_id']:
                prior=next(r for r in receipts if r['trial_id']==record['parent_trial_id'])
                assert native['parent_run_ref']==prior['audit']['native_agent_run_ref']
        if receipt['audit']['run_ref']:
            run=store.get(receipt['audit']['run_ref'])['payload']
            assert run['status']=='succeeded' and run['cached_from'] is None
            assert run['scientific_status']=='not_verified'
            assert run['source_validation']['semantic_support']=='unknown'
            numeric=run['numeric_validation']['checks']
            assert all(any(c['code']==k and c['status']=='pass' for c in numeric) for k in ('finite','convergence'))
            sim=run['simulation']; case=state['trial']['case_id']
            expected={}
            if case=='decay': expected['A']=[2*math.exp(-.4*t) for t in sim['times']]
            elif case=='translation':
                expected['M']=[2-math.exp(-.4*t) for t in sim['times']]
                expected['P']=[6+3*math.exp(-.4*t)-8.8*math.exp(-.2*t) for t in sim['times']]
            else: raise AssertionError('An unrecognized scientific case executed')
            errors={name:max(abs(a-b) for a,b in zip(sim['values'][name],values)) for name,values in expected.items()}
            # Report the actual global analytic errors; do not loosen the
            # exploratory 2e-6 check after seeing its failure. Product numeric
            # acceptance remains its unchanged finite/convergence protocol.
            fine=store.get(run['convergence_ref'])['payload']['result']
            fine_errors={name:max(abs(a-b) for a,b in zip(fine['values'][name],values)) for name,values in expected.items()}
            assert all(abs(sim['values'][name][0]-values[0])<1e-12 for name,values in expected.items())
            series[case].append(sim['values'])
            runs.append({'trial_id':record['trial_id'],'run_ref':receipt['audit']['run_ref'],
                'solver_backend':sim['backend'],'cached_from':run['cached_from'],
                'max_absolute_error_vs_analytic':errors,'refined_max_absolute_error_vs_analytic':fine_errors,
                'initial_exploratory_2e_6_check_satisfied':all(v<2e-6 for v in errors.values()),
                'last_values':{k:v[-1] for k,v in sim['values'].items()},
                'scientific_status':run['scientific_status'],'semantic_support':'unknown'})
        if '--logistic--' in record['trial_id'] and record['trial_id'].endswith('clarified'):
            assert record['observation']['state']=='awaiting_input' and not record['observation']['executed']
            assert any('binding unknown' in q for q in record['observation']['question_texts'])
            limits.append({'trial_id':record['trial_id'],'questions':record['observation']['question_texts'],
                'literal_source_sentence':'The author confirms the intended equation is dX/dt = r*X*(1-X/K).',
                'cause':'Shared bounded declaration grammar cannot bind the intended-equation prose. No material rewriting or product relaxation applied.'})
    differences={}
    for case,values in series.items():
        baseline=values[0]
        worst=max(abs(a-b) for data in values[1:] for name in baseline for a,b in zip(data[name],baseline[name]))
        assert worst<1e-10
        differences[case]={'actual_runs':len(values),'max_difference_across_workflows_and_repeats':worst}
    report={'status':'engineering_invariants_passed_analytic_diagnostics_recorded','scope':'Scripted decisions, actual tools, seen synthetic development controls; not model performance',
        'planned_phases':30,'scripted_provider_requests':sum(r['telemetry']['model_calls'] for r in records),
        'online_inference_calls':0,'actual_reproduction_runs':len(runs),'convergence_fine_runs':len(runs),
        'technical_completed_phases':12,'initial_scripted_questions':12,'clarified_phases_blocked_by_shared_grammar':len(limits),
        'human_review_decisions':0,'scientific_runs':runs,'cross_workflow_numeric_comparison':differences,
        'shared_limit':limits,'source_support':'unknown',
        'analytic_check_limit':'The initial exploratory absolute 2e-6 check failed on translation and is retained in scientific-results-first.log. No replacement global-error pass threshold adopted, solver settings and outputs unchanged. Finite/convergence checks remain the existing product criterion.',
        'next':'Track bounded declaration grammar follow-up separately; no score or benchmark relabeling.'}
    (OUT/'scientific-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('scientific_runs','shared_limit')},ensure_ascii=False))


if __name__=='__main__': main()
