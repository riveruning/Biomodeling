"""Verify the audit-only scope and record delivery evidence."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


before = json.loads((OUT/'workspace-before.json').read_text())
changed = [name for name, value in before.items()
           if not (ROOT/name).is_file() or sha(ROOT/name) != value]
assert not changed, changed
files = ['docs/research-readiness-audit.md', 'tasks/research-next-phase.json']
current = {str(p.relative_to(ROOT)) for folder in ('src', 'tests', 'docs', 'tasks', 'benchmarks/development')
           for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert current-set(before) == set(files), current-set(before)
plan = json.loads((ROOT/files[1]).read_text())
ids = {task['id'] for task in plan['tasks']}
assert len(ids) == len(plan['tasks']) and plan['next_task'] in ids
assert all(set(task['depends_on']) <= ids for task in plan['tasks'])
document = ROOT/files[0]
local_links = [link for link in re.findall(r'\]\(([^)]+)\)', document.read_text())
               if not link.startswith(('http:', 'https:', '#'))]
assert all((document.parent/link).exists() for link in local_links)
suite, = ET.parse(OUT/'focused.xml').getroot().findall('testsuite')
tests = {key: int(suite.get(key)) for key in ('tests', 'errors', 'failures', 'skipped')}
assert tests == {'tests': 121, 'errors': 0, 'failures': 0, 'skipped': 0}
patch = []
for name in files:
    patch.extend(difflib.unified_diff([], (ROOT/name).read_text().splitlines(keepends=True),
                                    fromfile='/dev/null', tofile='b/'+name))
(OUT/'documents.patch').write_text(''.join(patch))
report = {
    'status': 'audit_and_plan_delivered',
    'scope': 'Evidence audit and next-phase planning; no product implementation this turn',
    'prior_workspace_files_verified_unchanged': len(before),
    'modified_existing_files': changed,
    'new_documents': {name: sha(ROOT/name) for name in files},
    'local_document_links_verified': len(local_links),
    'fresh_focused_regression': tests,
    'fresh_test_seconds': float(suite.get('time')),
    'archived_full_regression_passed': 1309,
    'fresh_full_regression': False,
    'new_tests_added': 0,
    'confirmed_reporting_issue': 'Unreviewed completed outcomes receive no success credit but unknown=0; pending adjudication needs distinct reporting',
    'confirmed_documentation_drift': 'agent-study-tool.md deadline description predates studies.py/T04 v2 total deadline',
    'next_task': plan['next_task'],
    'new_online_model_calls': 0,
    'new_scientific_solver_runs': 0,
    'independent_evalset_read': False,
    'new_evaluation_papers_added': False,
    'real_credentials_read': False,
    'limits': 'Saved artifacts are verified, not a new independent domain audit. Focused tests are engineering controls; scientific review and actual current-version comparisons remain pending.'
}
(OUT/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
inventory = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
             if p.is_file() and p.name not in ('delivery-hashes.json', 'finalize.log')}
(OUT/'delivery-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
print(json.dumps(report, ensure_ascii=False))
