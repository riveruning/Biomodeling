"""Verify scope and archive the actual F1 delivery after checks complete."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
FILES = ['src/biomodel_agent/bindings.py', 'tests/test_intended_equation_binding.py',
         'docs/evidence-binding.md', 'docs/workflow-adapters.md', 'docs/optimization-backlog.md',
         'docs/seven-point-status.md', 'tasks/audit-t08b-f1-equation-prose.json']


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = read(OUT/'baseline-hashes.json')
    changed = [name for name, digest in baseline.items() if sha(ROOT/name) != digest]
    assert set(changed) <= set(FILES), changed
    original_test_changes = [n for n in changed if n.startswith('tests/')]
    assert not original_test_changes
    prior = read(OUT/'prior-artifact-hashes.json')
    assert all(sha(ROOT/n) == digest for n, digest in prior.items())
    freeze = read(OUT/'control-freeze.json')
    assert implementation_fingerprint() == freeze['new_toolchain_sha256']
    assert read(OUT/'development-execution/manifest.json') == read(OUT/'control-inputs/manifest.json')
    scripts = OUT/'control-inputs/scripts.json'
    assert sha(scripts) == freeze['scripts_sha256']
    checks = {}
    for path in sorted(OUT.glob('*.xml')):
        suite, = ET.parse(path).getroot().findall('testsuite')
        checks[path.stem] = {k: int(suite.get(k)) for k in ('tests', 'failures', 'errors', 'skipped')}
        checks[path.stem]['time'] = float(suite.get('time'))
    assert checks['full']['tests'] == 1235
    assert checks['full']['failures'] == checks['full']['errors'] == checks['full']['skipped'] == 0
    assert checks['timeout-recheck']['tests'] == 3 and checks['timeout-recheck']['failures'] == 0
    engineering = read(OUT/'development-execution/engineering.json')
    scientific = read(OUT/'scientific-results.json')
    assert engineering['observed_states'] == {'completed': 18, 'awaiting_input': 12}
    assert scientific['actual_reproduction_runs'] == scientific['convergence_fine_runs'] == 18
    assert scientific['scripted_provider_requests'] == 66 and scientific['online_inference_calls'] == 0
    patch = []
    hashes = {}
    for name in FILES:
        current = ROOT/name
        previous = OUT/'before'/name
        final = OUT/'final'/name
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, final)
        before = previous.read_text().splitlines(keepends=True) if previous.exists() else []
        # The task card was created with the plan before baseline capture.
        if name.startswith('tasks/audit-t08b-f1-'):
            before = []
        patch.extend(difflib.unified_diff(before, current.read_text().splitlines(keepends=True),
                                        fromfile='a/'+name, tofile='b/'+name))
        hashes[name] = sha(current)
    (OUT/'changes.patch').write_text(''.join(patch))
    report = {'task_id': 'T08b-F1', 'status': 'engineering_accepted',
              'parent_T08_actual_model_comparison': 'pending', 'checks': checks,
              'baseline_note': 'Initial 18 failures included two overstrict test status expectations (existing value grammar can conservatively return mismatch). Corrected before product edit: 16 failed / 47 passed. Both raw logs preserved.',
              'focused_initial_issue': '223 passed / 1 failed: a pre-existing 0.6-second domain timeout control expired before scientific reservation (executed=false). Meter/result/store preserved; isolated all-three recheck passed; full run used original unchanged tests and deadline.',
              'new_controls': 63, 'original_tests_modified': original_test_changes,
              'prior_artifact_files_unchanged': len(prior), 'original_baseline_files': len(baseline),
              'original_baseline_files_changed': changed, 'control_freeze': freeze,
              'actual_execution': engineering, 'scientific_diagnostics': 'scientific-results.json',
              'actual_native_domain_resumes': len(scientific['actual_native_agent_resumes']),
              'strict_SBML_revalidation_exercised': True,
              'binding_schema_version': 1, 'semantic_support': 'unknown',
              'implementation_fingerprint_guard_unchanged': True,
              'source_materials_and_scripts_unchanged': True,
              'original_completed_solver_outputs_unchanged': 12,
              'changed_files': FILES, 'final_hashes': hashes, 'diff': 'changes.patch',
              'limits': ['Two explicit English forms only; no general language understanding, cross-sentence scope proof, units or biological endorsement.',
                         'All controls are seen synthetic development tasks and literal scripted decisions; assessments remain unreviewed.',
                         'Analytic errors are diagnostics under unchanged solver settings. No post hoc precision threshold was adopted.',
                         'Legacy source fingerprints are not migrated or bypassed; the replay has a new manifest and new stores.',
                         'T07 independent review and T08 actual same-model comparisons/ablations remain pending.',
                         'No online inference, credentials, new papers, dependencies, independent evalset or rejected c13/c18 USTC transmission.']}
    (OUT/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    # Exclude this inventory itself; include all reproducible scripts and raw
    # evidence, including original failures and the timeout artifact copy.
    inventory = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
                 if p.is_file() and p.name not in ('delivery-hashes.json', 'finalize.log')}
    (OUT/'delivery-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'checks': checks, 'changed_files': FILES,
                      'prior_artifact_files_unchanged': len(prior), 'actual_execution': engineering}, ensure_ascii=False))


if __name__ == '__main__':
    main()
