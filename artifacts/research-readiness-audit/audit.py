"""Read-only verification of accepted evidence; writes only this audit directory."""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PRIOR = ROOT / 'artifacts/audit-t08c-http-transport-20261019'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(base, inventory):
    problems = [name for name, expected in inventory.items()
                if not (base/name).is_file() or sha(base/name) != expected]
    return {'checked': len(inventory), 'mismatches': problems}


def main():
    accepted = read(PRIOR/'acceptance.json')
    checks = {
        'current_delivery': verify(ROOT, accepted['final_hashes']),
        'archived_delivery': verify(PRIOR, read(PRIOR/'delivery-hashes.json')),
        'older_artifacts': verify(ROOT, read(PRIOR/'prior-artifact-hashes.json')),
    }
    baseline = read(PRIOR/'baseline-hashes.json')
    unchanged = {name: value for name, value in baseline.items()
                 if name not in accepted['modified_baseline_files']}
    checks['unchanged_baseline'] = verify(ROOT, unchanged)
    assert all(not item['mismatches'] for item in checks.values()), checks
    assert implementation_fingerprint() == accepted['product_toolchain_sha256']
    suite, = ET.parse(PRIOR/'full.xml').getroot().findall('testsuite')
    archived_tests = {key: int(suite.get(key)) for key in ('tests', 'errors', 'failures', 'skipped')}
    assert archived_tests == {'tests': 1309, 'errors': 0, 'failures': 0, 'skipped': 0}

    # Reuse the accepted read-only artifact verifier, redirect its sole report
    # destination here. RUN and PRIOR remain its original immutable inputs.
    numeric = load('readiness_numeric_audit', PRIOR/'audit-http-results.py')
    numeric.OUT = OUT
    numeric.main()
    protocol = load('readiness_protocol', ROOT/'benchmarks/development/agent_evaluation.py')
    manifest = read(PRIOR/'http-mock-execution/manifest.json')
    records = read(PRIOR/'http-mock-execution/records.json')
    scores = protocol.score(manifest, records, ROOT)
    metric_probe = {
        'evidence_kind': manifest['evidence_kind'],
        'assessment_authorities': dict(Counter(r['assessment']['authority'] for r in records)),
        'observed_states': dict(Counter(r['observation']['state'] for r in records)),
        'correct_completion_by_workflow': {k: v['correct_completion'] for k, v in scores['summary'].items()},
        'interpretation': 'All 30 records are unreviewed. Each arm reports 0/6 correct completion with unknown=0. This is conservative no-credit reporting, but does not expose pending completion adjudication; do not interpret as measured 0% model capability.',
    }
    assert metric_probe['assessment_authorities'] == {'unreviewed': 30}
    assert all(v == {'numerator': 0, 'denominator': 6, 'unknown': 0, 'rate': 0.0}
               for v in metric_probe['correct_completion_by_workflow'].values())
    (OUT/'metric-probe.json').write_text(json.dumps(metric_probe, ensure_ascii=False, indent=2)+'\n')
    inventory = {str(p.relative_to(ROOT)): sha(p)
                 for folder in ('src', 'tests', 'docs', 'tasks', 'benchmarks/development')
                 for p in sorted((ROOT/folder).rglob('*'))
                 if p.is_file() and '__pycache__' not in p.parts}
    (OUT/'workspace-before.json').write_text(json.dumps(inventory, indent=2)+'\n')
    report = {
        'status': 'accepted_evidence_integrity_verified',
        'scope': 'Current code, acceptance records, saved numeric results and historical report summaries. No independent evalset or denied c13/c18 materials opened.',
        'checks': checks, 'product_toolchain_sha256': implementation_fingerprint(),
        'archived_full_regression': archived_tests,
        'fresh_full_regression_run': False,
        'numeric_verification': 'Recomputed analytic errors from 18 saved executions and checked 66 HTTP receipts plus 4 native resume relations; no new solver executions.',
        'metric_probe': 'metric-probe.json',
        'new_online_inference_calls': 0,
        'real_credentials_read': 0,
        'limits': 'Integrity is not independent scientific validation. Focused pytest results are recorded separately. This is a bounded audit, not an exhaustive security or correctness proof.',
    }
    (OUT/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
