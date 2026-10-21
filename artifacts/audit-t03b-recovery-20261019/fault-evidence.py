"""Reproducible offline evidence; scripted actions are not model evaluation."""
from pathlib import Path
import json
import multiprocessing as mp
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'tests'))
from test_invocation_acceptance import Scripted, act, fixture
from test_recovery_acceptance import abrupt
from biomodel_agent import agent, recovery, bundles
from biomodel_agent.storage import ArtifactStore


def main():
    output = Path(__file__).resolve().parent
    results = []
    for point, code in [('provider', 41), ('result', 42), ('read', 44)]:
        directory = output / 'fault-evidence' / point
        directory.mkdir(parents=True, exist_ok=False)
        store, source, proposal = fixture(directory)
        process = mp.get_context('spawn').Process(target=abrupt,
            args=(str(store.root), source, proposal, point))
        process.start(); process.join(30)
        if process.is_alive():
            process.terminate(); process.join(5)
            raise RuntimeError('fault injection process exceeded deadline')
        assert process.exitcode == code
        store = ArtifactStore(store.root)
        execution_ref = store.list('agent_execution')[0]
        report = recovery.diagnose_execution(store, execution_ref)
        (directory / 'before-report.json').write_text(json.dumps(report, indent=2) + '\n')
        before_runs = store.list('run')
        provider = Scripted([act('finish')] if point == 'result' else
                            [act('ask_researcher', question='Continue?')])
        result = dict(case=point, exitcode=code, execution_ref=execution_ref,
                      diagnosis=report['status'], reasons=report['reasons'],
                      calls_before=report['call_count'], compute_before=report['run_count'],
                      scientific_runs_before=len(before_runs))
        try:
            final_ref = agent.recover_agent(store, provider, execution_ref)
        except recovery.RecoveryBlocked:
            assert point == 'provider' and not provider.calls and not store.list('agent_recovery')
            result.update(action='blocked_without_resubmission', new_provider_calls=0)
        else:
            final = store.get(final_ref)['payload']
            manifest = bundles.export_bundle(store, final_ref, directory / 'export')
            result.update(action='continued', state=final['state'], final_ref=final_ref,
                calls_after=final['call_count'], new_provider_calls=len(provider.calls),
                scientific_runs_after=len(store.list('run')), all_invocations_exported=
                set(final['invocation_refs'] + final['recovery_refs']).issubset(manifest['objects_and_blobs']))
            if point == 'result':
                assert store.list('run') == before_runs and final['run_ref'] == before_runs[0]
                saved = store.get(before_runs[0])['payload']
                result.update(same_result_ref=True, endpoint=saved['simulation']['values']['x'][-1],
                              scientific_status=saved['scientific_status'])
            else:
                operations = recovery.diagnose_execution(store, execution_ref)['operations']
                assert sum(op['reserved']['name'] == 'search_sources' for op in operations) == 1
                result.update(read_reservations=1, original_read_reservation_reused=True)
        results.append(result)
    (output / 'fault-results.json').write_text(json.dumps({
        'kind': 'offline_scripted_real_science_and_process_exit_not_model_capability',
        'implementation_sha256': recovery.implementation_fingerprint(), 'cases': results}, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
