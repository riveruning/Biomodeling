"""Verify T08c scope, regression and actual mock-HTTP/science evidence."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
FILES = ['benchmarks/development/comparison_transport.py', 'benchmarks/development/workflow_adapters.py',
         'tests/test_comparison_transport.py', 'docs/comparison-transport.md', 'docs/workflow-adapters.md',
         'docs/agent-evaluation-protocol.md', 'docs/optimization-backlog.md', 'docs/seven-point-status.md',
         'tasks/audit-t08c-http-transport.json']


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = read(OUT/'baseline-hashes.json')
    changes = [name for name, digest in baseline.items() if sha(ROOT/name) != digest]
    assert set(changes) <= set(FILES), changes
    assert not [name for name in changes if name.startswith(('src/', 'tests/'))]
    assert 'benchmarks/development/agent_evaluation.py' not in changes
    assert 'benchmarks/development/run_agent_comparison.py' not in changes
    prior = read(OUT/'prior-artifact-hashes.json')
    assert all(sha(ROOT/name) == digest for name, digest in prior.items())
    freeze = read(OUT/'control-freeze.json')
    assert freeze['product_toolchain_sha256'] == implementation_fingerprint()
    assert sha(OUT/'control-inputs/scripts.json') == freeze['scripts_sha256']
    scientific = read(OUT/'http-scientific-results.json')
    assert scientific['http_mock_requests_and_receipts'] == 66
    assert scientific['actual_reproduction_runs'] == scientific['convergence_fine_runs'] == 18
    assert scientific['unchanged_numeric_outputs_vs_F1'] == 18
    assert scientific['online_inference_calls'] == 0
    checks = {}
    for path in sorted(OUT.glob('*.xml')):
        suite, = ET.parse(path).getroot().findall('testsuite')
        checks[path.stem] = {key: int(suite.get(key)) for key in ('tests', 'errors', 'failures', 'skipped')}
        checks[path.stem]['time'] = float(suite.get('time'))
    assert checks['full']['tests'] == 1309
    assert all(checks['full'][key] == 0 for key in ('failures', 'errors', 'skipped'))
    assert checks['final-controls']['tests'] == 74 and checks['final-controls']['failures'] == 0
    hashes, patch = {}, []
    for name in FILES:
        original, current, final = OUT/'before'/name, ROOT/name, OUT/'final'/name
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, final)
        previous = original.read_text().splitlines(keepends=True) if original.exists() else []
        if name.startswith('tasks/audit-t08c-'):
            previous = []  # Task card created with the initial plan.
        patch.extend(difflib.unified_diff(previous, current.read_text().splitlines(keepends=True),
                                        fromfile='a/'+name, tofile='b/'+name))
        hashes[name] = sha(current)
    (OUT/'changes.patch').write_text(''.join(patch))
    report = {'task_id': 'T08c', 'status': 'engineering_accepted',
              'T08_actual_same_model_comparison': 'pending', 'checks': checks,
              'baseline_note': 'Two missing-module setup errors establish absent HTTP/preparation capabilities, not regressions in existing product behavior.',
              'new_controls': 74, 'original_product_source_and_tests_unchanged': True,
              'original_scorer_and_scripted_driver_unchanged': True,
              'original_baseline_files': len(baseline), 'modified_baseline_files': changes,
              'prior_artifact_files_unchanged': len(prior), 'product_toolchain_sha256': implementation_fingerprint(),
              'manifest_and_script_identical_to_F1': True,
              'http_mock_execution': read(OUT/'http-mock-execution/engineering.json'),
              'actual_http_receipts': scientific['http_mock_requests_and_receipts'],
              'actual_original_native_task_resumes': len(scientific['native_original_task_resumes']),
              'numeric_diagnostics': 'http-scientific-results.json',
              'preparation_example_status': read(OUT/'preparation-example/preflight.json')['status'],
              'configured_live_provider_and_model': False, 'real_credential_reads': 0,
              'user_comparison_intent': read(OUT/'comparison-intent.json'),
              'unit_credential_controls': 'Only explicit T08C fake/unset environment names; MockTransport uses a fixed dummy token without reading the actual key.',
              'online_inference_calls': 0, 'actual_human_review_decisions': 0,
              'old_USTC_c13_c18_denials_retried': False,
              'changed_files': FILES, 'final_hashes': hashes, 'diff': 'changes.patch',
              'official_protocol_reference': 'https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create',
              'limits': ['Legacy non-streaming Chat Completions subset only; actual provider/model parameter support unverified, no silent fallbacks.',
                         'Declared/returned model, revision and fingerprint are attribution, not provider/model authentication.',
                         'Preparation freezes a proposed concrete scope and cannot grant transmission authorization; CLI has no execution command.',
                         'HTTP mocks and literal scripted decisions are engineering controls, never real model capability results.',
                         'All assessments remain unreviewed and source semantic support unknown.',
                         'HTTP phase timeouts and decoded byte limits have documented bounds; POSIX supervisor supplies the common hard deadline.',
                         'No new papers, dependencies, independent evalset, real credentials or c13/c18 materials accessed.']}
    (OUT/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    inventory = {str(path.relative_to(OUT)): sha(path) for path in sorted(OUT.rglob('*'))
                 if path.is_file() and path.name not in ('delivery-hashes.json', 'finalize.log')}
    (OUT/'delivery-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'checks': checks, 'prior_artifact_files_unchanged': len(prior),
                      'modified_original_files': changes, 'http_mock_execution': report['http_mock_execution']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
