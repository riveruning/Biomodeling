"""Verify the offline delivery, preserved history and actual regression."""
import ast
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
FILES = ['benchmarks/development/system_comparison.py', 'benchmarks/development/native_capture.py',
         'tests/test_system_comparison.py', 'tests/test_workflow_adapters.py', 'docs/system-comparison.md', 'docs/current-support.md',
         'docs/optimization-backlog.md', 'docs/seven-point-status.md',
         'tasks/research-next-phase.json', 'tasks/audit-t08d-1-system-comparison.json']
INITIAL_NEW = {'tests/test_system_comparison.py', 'tasks/audit-t08d-1-system-comparison.json'}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = read(OUT/'baseline-hashes.json')
    changes = [name for name, value in baseline.items() if sha(ROOT/name) != value]
    assert set(changes) <= set(FILES), changes
    assert not [name for name in changes if name.startswith('src/') or
                name.startswith('tests/') and name not in ('tests/test_system_comparison.py', 'tests/test_workflow_adapters.py')]
    old_tree = ast.parse((OUT/'before/tests/test_workflow_adapters.py').read_text())
    new_tree = ast.parse((ROOT/'tests/test_workflow_adapters.py').read_text())
    target_name = 'test_hard_deadline_also_bounds_the_scientific_tool'
    def other_nodes(tree):
        return [ast.dump(node, include_attributes=False) for node in tree.body
                if not isinstance(node, ast.FunctionDef) or node.name != target_name]
    assert other_nodes(old_tree) == other_nodes(new_tree)
    for name in ('agent_evaluation.py', 'evaluation_reporting.py', 'workflow_adapters.py', 'comparison_transport.py', 'run_agent_comparison.py'):
        key = 'benchmarks/development/'+name
        assert sha(ROOT/key) == baseline[key]
    prior = read(OUT/'prior-artifact-hashes.json')
    inherited = read(ROOT/'artifacts/audit-t08c-http-transport-20261019/prior-artifact-hashes.json')
    for name, value in {**prior, **inherited}.items():
        assert sha(ROOT/name) == value, name
    fingerprint = implementation_fingerprint()
    assert fingerprint == read(ROOT/'artifacts/audit-t08d-0-reporting-20261020/acceptance.json')['product_toolchain_sha256']
    checks = {}
    for path in sorted(OUT.glob('*.xml')):
        suite, = ET.parse(path).getroot().findall('testsuite')
        checks[path.stem] = {k: int(suite.get(k)) for k in ('tests', 'errors', 'failures', 'skipped')}
        checks[path.stem]['seconds'] = float(suite.get('time'))
    assert checks['final-controls']['tests'] == 67
    assert checks['full-confirmation']['tests'] == 1427 and checks['v01-deterministic']['tests'] == 3
    assert all(checks[label][k] == 0 for label in ('final-controls', 'full-confirmation', 'v01-deterministic')
               for k in ('errors', 'failures', 'skipped'))
    spec = importlib.util.spec_from_file_location('system_final_audit', ROOT/'benchmarks/development/system_comparison.py')
    system = importlib.util.module_from_spec(spec); spec.loader.exec_module(system)
    run = OUT/'offline-replay'
    manifest, records = read(run/'manifest.json'), read(run/'records.json')
    assert system.score(manifest, records) == read(run/'results.json')
    for task in (run/'tasks').iterdir():
        system.check_task(task)
    engineering = read(run/'engineering.json')
    assert engineering['pending_completion_adjudications'] == 18 and engineering['initial_gap_phases'] == 12
    assert engineering['actual_native_Codex_model_runs'] == 0
    probe = read(OUT/'isolation-probe-final/result.json')
    assert probe['status'] == 'offline_OS_visibility_probe_passed'
    assert all(Path(path).is_absolute() and blocked for path, blocked in probe['forbidden_reads_denied'].items())
    assert probe['native_Codex_execution_verified'] is False
    system.check_task(OUT/'isolation-probe-final/task')
    hashes, patch, links = {}, [], []
    for name in FILES:
        p = ROOT/name; target = OUT/'final'/name
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, target)
        hashes[name] = sha(p)
        original = OUT/'before'/name
        previous = original.read_text().splitlines(keepends=True) if original.exists() and name not in INITIAL_NEW else []
        patch.extend(difflib.unified_diff(previous, p.read_text().splitlines(keepends=True),
                                        fromfile='a/'+name if previous else '/dev/null', tofile='b/'+name))
        if name in ('docs/system-comparison.md', 'docs/current-support.md'):
            for link in re.findall(r'\]\(([^)]+)\)', p.read_text()):
                if not link.startswith(('http:', 'https:', '#')):
                    assert (p.parent/link).exists(), (name, link)
                    links.append([name, link])
    plan = read(ROOT/'tasks/research-next-phase.json')
    assert next(t for t in plan['tasks'] if t['id'] == 'T08d-1')['status'] == 'engineering_accepted'
    assert plan['next_task'] == 'T07b-T07c-design'
    (OUT/'changes.patch').write_text(''.join(patch))
    result = {'task_id': 'T08d-1', 'status': 'engineering_accepted', 'owner': 'Codex alone',
        'protocol': system.PROTOCOL, 'checks': checks, 'new_controls': 67,
        'baseline_note': 'Two missing-module setup errors, not a product regression.',
        'preserved_boundary_failures': 'Three reporting/UTF8/artifact-metadata gaps reproduced and fixed; cross-case session guard passed.',
        'original_product_sources_unchanged': True,
        'only_existing_test_change': 'tests/test_workflow_adapters.py::'+target_name,
        'existing_tests_outside_this_function_unchanged': True,
        'V01': read(OUT/'v01-evidence.json'),
        'legacy_scorers_and_execution_transports_unchanged': True,
        'product_toolchain_sha256': fingerprint,
        'prior_archive_files_unchanged': len(prior), 'inherited_archive_checks': len(inherited),
        'offline_replay': engineering, 'offline_visibility_probe': probe,
        'probe_history': ['sandbox NETLINK_ROUTE denial retained', 'first host probe had two relative checks and is not sufficient absolute-path evidence',
                          'final authorized host probe checks existing absolute paths and separate network namespace'],
        'actual_Codex_commands_invoked': 'version and help only; inference argv values were plans',
        'online_inference_calls': 0, 'new_comparison_scientific_runs': 0,
        'regression_tests_use_actual_scientific_backends': True,
        'real_credentials_read_by_comparison_code': False,
        'independent_evalset_or_c13_c18_materials_read': False,
        'new_evaluation_papers_added': False, 'changed_files': FILES,
        'final_hashes': hashes, 'modified_baseline_files': changes, 'document_links_verified': len(links),
        'next_task': plan['next_task'],
        'official_reference': 'https://learn.chatgpt.com/docs/non-interactive-mode',
        'limits': ['Offline preparation/import/report only; supervised authorized inference and independent submission execution remain future work.',
                   'External recordings and declared sessions/models are attribution, not authenticated live-model evidence.',
                   'Historical product imports and fabricated native events are interface controls, not a new system performance comparison.',
                   'The OS probe tests fixed Python only; native auth/session/runtime mounts require separate verification.',
                   'Import stopped captures; path checks are not a hostile concurrent-filesystem sandbox.',
                   'Wall budgets are recorded, not enforced by this importer; native model-call counts and scientific execution remain unknown.',
                   'Strict native event profile may need explicit revision for later CLI schemas; unsupported events retain diagnostics.',
                   'Independent review, real-paper comparison and same-model attribution remain pending; V01 test admission is stabilized, not production throughput or host scheduling.']}
    (OUT/'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    inventory = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
                 if p.is_file() and p.name not in ('delivery-hashes.json', 'finalize.log')}
    (OUT/'delivery-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('task_id', 'status', 'checks', 'prior_archive_files_unchanged', 'next_task')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
