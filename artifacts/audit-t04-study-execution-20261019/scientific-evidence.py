"""Offline real fitting/deadline/recovery evidence; not an LLM evaluation."""
from contextlib import redirect_stdout
from io import StringIO
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'tests'))
from test_agent_studies import Scripted, act, config
from biomodel_agent import agent, bundles, cli, recovery, studies
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.storage import ArtifactStore


def main():
    output = Path(__file__).resolve().parent
    results = []
    for case in ('valid_positive', 'valid_negative'):
        directory = output / 'evidence' / case
        directory.mkdir(parents=True, exist_ok=False)
        settings = {**config(), 'total_timeout_s': 30., 'seed': 19}
        if case == 'valid_negative':
            settings['holdout'][0]['values']['response'] = [99.] * 5
        store = ArtifactStore(directory / 'store')
        plan = studies.prepare_study(store, settings)
        training, holdout = directory / 'training.json', directory / 'holdout.json'
        training.write_text(json.dumps(settings['training']))
        holdout.write_text(json.dumps(settings['holdout']))
        captured = StringIO()
        with redirect_stdout(captured):
            legacy_code = cli.main(['hypothesis', '--training', str(training), '--holdout', str(holdout),
                '--candidate', 'cand_baseline', '--starts', '1', '--seed', '19', '--max-nfev', '60',
                '--timeout', '20', '--total-timeout', '30', '--store', str(store.root)])
        legacy = json.loads(captured.getvalue())
        assert legacy['plan_ref'] == plan and legacy_code == 0
        captured = StringIO()
        with redirect_stdout(captured):
            offline_code = cli.main(['study-run', plan, '--store', str(store.root)])
        offline = json.loads(captured.getvalue())
        assert offline_code == 0
        source = store.put('source', {'pages': [{'number': 1, 'text': 'Operator-frozen adaptation study.'}]})
        exp = from_dict(ExperimentSpec, settings['training'][0]['experiment'])
        provider = Scripted([act('run_study'), act('finish')])
        agent_ref = agent.run_agent(store, provider, [source], exp, mode='hypothesis', study_plan_ref=plan)
        final = store.get(agent_ref)['payload']
        entries = {}
        for entry, ref in [('hypothesis', legacy['ref']), ('study-run', offline['ref']), ('agent', final['run_ref'])]:
            record = store.get(ref)['payload']
            candidate = record['candidates']['cand_baseline']
            assert record['plan_ref'] == plan and record['completion_eligible']
            entries[entry] = {'ref': ref, 'status': record['status'], 'comparison': record['comparison'],
                'parameters': candidate['parameters'], 'training_weighted_rmse': candidate['training_weighted_rmse'],
                'heldout_weighted_rmse': candidate['heldout_weighted_rmse'],
                'heldout_scaled_rmse': candidate['heldout_scaled_rmse'], 'phase_status': candidate['phase_status'],
                'elapsed_s': record['deadline']['elapsed_s'], 'total_timeout_s': record['protocol']['total_timeout_s']}
        assert len({json.dumps(row['comparison'], sort_keys=True) for row in entries.values()}) == 1
        assert final['state'] == 'completed' and len(provider.calls) == 2
        manifest = bundles.export_bundle(store, agent_ref, directory / 'export')
        results.append({'case': case, 'plan_ref': plan, 'entries': entries, 'agent_ref': agent_ref,
            'scientific_status': 'not_verified', 'agent_calls': 2, 'export_contains_plan': plan in manifest['objects_and_blobs']})

    directory = output / 'evidence' / 'actual_timeout_recovery'
    directory.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(directory / 'store')
    settings = {**config(), 'total_timeout_s': .01}
    plan = studies.prepare_study(store, settings)
    source = store.put('source', {'pages': [{'number': 1, 'text': 'Bounded scientific deadline.'}]})
    exp = from_dict(ExperimentSpec, settings['training'][0]['experiment'])
    previous_summary = studies.result_summary
    def fault(*args):
        raise SystemExit('fault after durable study return before feedback')
    started = time.monotonic()
    studies.result_summary = fault
    try:
        try:
            agent.run_agent(store, Scripted([act('run_study')]), [source], exp, mode='hypothesis', study_plan_ref=plan)
        except SystemExit:
            pass
    finally:
        studies.result_summary = previous_summary
    elapsed = time.monotonic() - started
    execution_ref = store.list('agent_execution')[0]
    report = recovery.diagnose_execution(store, execution_ref)
    assert report['status'] == 'ready'
    result_ref = report['adoptable_result_refs'][0]
    original = store.get(result_ref)
    fit_refs = store.list('fitting')
    final_ref = agent.recover_agent(store, Scripted([act('finish')]), execution_ref)
    final = store.get(final_ref)['payload']
    assert final['state'] == 'incomplete' and final['run_ref'] == result_ref
    assert store.get(result_ref) == original and store.list('fitting') == fit_refs
    assert original['payload']['status'] == 'timed_out' and not mp.active_children()
    bundles.export_bundle(store, final_ref, directory / 'export')
    results.append({'case': 'actual_timeout_recovery', 'total_timeout_s': .01, 'observed_agent_wall_s': elapsed,
        'execution_ref': execution_ref, 'result_ref': result_ref, 'state': final['state'],
        'study_status': original['payload']['status'], 'same_result_ref': True,
        'fit_artifacts_before': len(fit_refs), 'fit_artifacts_after': len(store.list('fitting')),
        'active_numerical_children': len(mp.active_children()), 'calls_after': final['call_count'],
        'deadline': original['payload']['deadline']})
    (output / 'scientific-results.json').write_text(json.dumps({
        'kind': 'offline_scripted_real_fit_and_timeout_not_model_capability',
        'implementation_sha256': recovery.implementation_fingerprint(), 'cases': results}, indent=2) + '\n')
    print(json.dumps({'cases': [r['case'] for r in results], 'all_checks_passed': True}))


if __name__ == '__main__':
    main()
