"""Freeze T08d-0 delivery, immutable history and actual regression evidence."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
FILES = ['benchmarks/development/evaluation_reporting.py', 'tests/test_evaluation_reporting.py',
         'docs/evaluation-reporting.md', 'docs/current-support.md', 'docs/agent-study-tool.md',
         'docs/optimization-backlog.md', 'docs/seven-point-status.md', 'README.md',
         'tasks/research-next-phase.json', 'tasks/audit-t08d-0-reporting.json']
INITIAL_NEW = {'tests/test_evaluation_reporting.py', 'tasks/audit-t08d-0-reporting.json'}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = read(OUT/'baseline-hashes.json')
    changes = [name for name, value in baseline.items() if sha(ROOT/name) != value]
    assert set(changes) <= set(FILES), changes
    assert not [name for name in changes if name.startswith('src/')
                or name.startswith('tests/') and name != 'tests/test_evaluation_reporting.py']
    assert sha(ROOT/'benchmarks/development/agent_evaluation.py') == baseline['benchmarks/development/agent_evaluation.py']
    assert all(sha(ROOT/name) == value for name, value in read(OUT/'prior-artifact-hashes.json').items())
    product_hash = implementation_fingerprint()
    assert product_hash == read(ROOT/'artifacts/audit-t08c-http-transport-20261019/acceptance.json')['product_toolchain_sha256']
    checks = {}
    for path in sorted(OUT.glob('*.xml')):
        suite, = ET.parse(path).getroot().findall('testsuite')
        checks[path.stem] = {key: int(suite.get(key)) for key in ('tests', 'errors', 'failures', 'skipped')}
        checks[path.stem]['seconds'] = float(suite.get('time'))
    assert checks['final-controls']['tests'] == 51
    assert checks['full-confirmation']['tests'] == 1360
    assert all(checks[name][key] == 0 for name in ('final-controls', 'full-confirmation')
               for key in ('errors', 'failures', 'skipped'))
    invariants = read(OUT/'report-invariants.json')
    assert invariants['pending_completion_adjudications'] == 18 and invariants['initial_gap_phases'] == 12
    assert invariants['legacy_json_results_identical']
    actual = OUT/'actual-saved-records-report'
    assert read(actual/'results-v1.json') == read(OUT/'legacy-v1-results-before.json')
    hashes, patch, links = {}, [], []
    for name in FILES:
        p = ROOT/name
        target = OUT/'final'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        hashes[name] = sha(p)
        original = OUT/'before'/name
        before = original.read_text().splitlines(keepends=True) if original.exists() and name not in INITIAL_NEW else []
        patch.extend(difflib.unified_diff(before, p.read_text().splitlines(keepends=True),
                                         fromfile='a/'+name if before else '/dev/null', tofile='b/'+name))
        if name in ('docs/evaluation-reporting.md', 'docs/current-support.md'):
            for link in re.findall(r'\]\(([^)]+)\)', p.read_text()):
                if not link.startswith(('http:', 'https:', '#')):
                    assert (p.parent/link).exists(), (name, link)
                    links.append([name, link])
    plan = read(ROOT/'tasks/research-next-phase.json')
    assert plan['next_task'] == 'T08d-1'
    assert next(t for t in plan['tasks'] if t['id'] == 'T08d-0')['status'] == 'engineering_accepted'
    (OUT/'changes.patch').write_text(''.join(patch))
    result = {
        'task_id': 'T08d-0', 'status': 'engineering_accepted', 'owner': 'Codex alone',
        'reporting_protocol': 'agent_evaluation_report_v2', 'unchanged_input_protocol': 'agent_evaluation_v1',
        'checks': checks, 'new_controls': 51,
        'baseline_note': 'Two missing-module errors establish absence of report v2, not an existing product regression.',
        'preserved_development_failures': [
            'expanded: positive fixture marked target labels reviewed but did not populate the synthetic mappings; test setup corrected',
            'boundary: same fixture issue plus not-applicable parent question criterion incorrectly treated as pending recovery; reporter fixed and negative control retained',
            'first full suite: two unchanged 0.6-second tool-deadline probes timed out before scientific-call admission; isolated three-case run passed; no old source/test/timeout changed; all failed artifacts and one complete confirmation run retained'],
        'actual_saved_record_results': invariants,
        'original_product_sources_and_existing_tests_unchanged': True,
        'legacy_scorer_and_execution_transport_unchanged': True,
        'prior_artifact_files_unchanged': len(read(OUT/'prior-artifact-hashes.json')),
        'product_toolchain_sha256': product_hash,
        'existing_baseline_files': len(baseline), 'modified_baseline_files': changes,
        'changed_files': FILES, 'final_hashes': hashes,
        'local_document_links_verified': len(links),
        'next_task': 'T08d-1', 'T07_independent_domain_review': 'pending',
        'T08_actual_current_version_real_model_comparison': 'pending',
        'online_inference_calls': 0, 'new_comparison_scientific_runs': 0,
        'regression_tests_use_actual_scientific_backends': True,
        'real_credentials_read': False, 'new_evaluation_papers_added': False,
        'independent_evalset_or_c13_c18_materials_read': False,
        'limits': [
            'Only reporting over v1 seen synthetic development inputs; no new runtime or Codex system execution yet.',
            'Unknown-rate bounds are descriptive identification bounds, not statistical confidence intervals.',
            'Attributed judgments, model identity and evidence contents are not independently authenticated.',
            'Fixture tests and reporting improvements do not establish model capability gains.',
            'Current support matrix and study deadline documentation updated without changing scientific tool behavior.']
    }
    result['limits'].append('Two first-run short-deadline probe failures remain disclosed despite isolated and full confirmation results; scheduling sensitivity is observed, not a proven root-cause diagnosis.')
    (OUT/'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    inventory = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
                 if p.is_file() and p.name not in ('delivery-hashes.json', 'finalize.log')}
    (OUT/'delivery-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
    print(json.dumps({key: result[key] for key in ('task_id', 'status', 'checks', 'prior_artifact_files_unchanged', 'next_task')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
