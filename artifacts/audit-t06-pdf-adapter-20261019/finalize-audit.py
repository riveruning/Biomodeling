"""Freeze T06 only after actual final checks pass; retain previous failures."""
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def xml_result(name):
    root = ET.parse(OUT/(name+'.xml')).getroot()
    suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
    return {key: sum(float(s.get(key, 0)) if key == 'time' else int(s.get(key, 0)) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped', 'time')}


def main():
    names = ['baseline', 'targeted-01', 'expanded-01', 'expanded-02', 'full-01',
             'native-stdout-baseline', 'full-final', 'expanded-final', 'full-final-02']
    results = {n: xml_result(n) for n in names}
    assert results['baseline']['failures'] == 13 and results['native-stdout-baseline']['failures'] == 1
    assert results['full-final']['failures'] == 1
    for n in ('targeted-01', 'expanded-01', 'expanded-02', 'full-01', 'expanded-final', 'full-final-02'):
        assert results[n]['failures'] == results[n]['errors'] == results[n]['skipped'] == 0
    baseline = json.loads((OUT/'baseline-hashes.json').read_text())
    assert all((ROOT/rel).is_file() and sha(ROOT/rel) == value for rel, value in baseline.items() if rel.startswith('tests/'))
    previous = json.loads((ROOT/'artifacts/audit-t05-evidence-binding-20261019/acceptance.json').read_text())
    assert all((sha(OUT/'before'/p) if (OUT/'before'/p).exists() else baseline.get(p, sha(ROOT/p))) == value
               for p, value in previous['final_hashes'].items())
    production = sorted(str(p.relative_to(ROOT)) for p in (ROOT/'src').rglob('*.py')
                        if str(p.relative_to(ROOT)) not in baseline or sha(p) != baseline[str(p.relative_to(ROOT))])
    assert set(production) == {'src/biomodel_agent/'+name+'.py' for name in ('agent', 'cli', 'evidence', 'llm', 'pdf_adapter')}
    assert sha(ROOT/'requirements.lock') == baseline['requirements.lock']
    assert metadata.version('pymupdf') == '1.28.2'
    assert 'No broken requirements found.' in (OUT/'dependency-check.log').read_text()
    base_versions = {}
    for line in (ROOT/'requirements.lock').read_text().splitlines():
        if '==' in line and not line.startswith('#'):
            name, version = line.split('==')
            actual = metadata.version(name)
            assert actual == version, (name, version, actual)
            base_versions[name] = actual
    files = production+['pyproject.toml', 'requirements-pdf.lock', 'tests/test_pdf_adapter_acceptance.py',
                        'docs/pdf-parser-adapter.md', 'docs/optimization-backlog.md', 'docs/seven-point-status.md']
    diff = []
    for rel in files:
        current, before = ROOT/rel, OUT/'before'/rel
        diff.extend(difflib.unified_diff(before.read_text().splitlines(True) if before.exists() else [],
                                       current.read_text().splitlines(True),
                                       fromfile='a/'+rel if before.exists() else '/dev/null', tofile='b/'+rel))
        dest = OUT/'final'/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, dest)
    (OUT/'changes.patch').write_text(''.join(diff))
    scientific = json.loads((OUT/'final-evidence/pdf-results.json').read_text())
    assert scientific['agent']['state'] == scientific['recovery']['state'] == 'completed'
    assert scientific['agent']['scientific_status'] == 'not_verified'
    assert scientific['bundle']['pdf_and_parser_output_unchanged'] is True
    assert all(v['raw_bytes_retained'] and v['no_child_left'] and v['successful_sources'] == 0
               for v in scientific['injected_failures'].values())
    commands = {
        'baseline': 'tests/test_pdf_adapter_acceptance.py --basetemp=/tmp/biomodel-t06-baseline',
        'targeted-01': 'tests/test_pdf_adapter_acceptance.py tests/test_storage_evidence.py tests/test_evidence_binding_acceptance.py --basetemp=/tmp/biomodel-t06-targeted',
        'expanded-01': 'tests/test_pdf_adapter_acceptance.py --basetemp=/tmp/biomodel-t06-expanded',
        'expanded-02': 'tests/test_pdf_adapter_acceptance.py tests/test_storage_evidence.py tests/test_evidence_binding_acceptance.py --basetemp=/tmp/biomodel-t06-expanded-02',
        'full-01': '--basetemp=/tmp/biomodel-t06-full-01',
        'native-stdout-baseline': 'tests/test_pdf_adapter_acceptance.py::test_native_stdout_diagnostics_do_not_corrupt_application_stdout --basetemp=/tmp/biomodel-t06-native-stdout-baseline',
        'full-final': '--basetemp=/tmp/biomodel-t06-full-final',
        'expanded-final': 'tests/test_pdf_adapter_acceptance.py --basetemp=/tmp/biomodel-t06-expanded-final',
        'full-final-02': '--basetemp=/tmp/biomodel-t06-full-final-02'}
    commands = {n: 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider '+v+
                ' --junitxml=artifacts/audit-t06-pdf-adapter-20261019/'+n+'.xml' for n, v in commands.items()}
    commands['pdf_evidence'] = 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python artifacts/audit-t06-pdf-adapter-20261019/pdf-evidence.py'
    acceptance = {
        'task_id': 'T06', 'status': 'accepted', 'accepted_at_utc': datetime.now(timezone.utc).isoformat(),
        'implementation_owner': 'Codex alone, user explicitly clarified implementation',
        'dsh_or_subagents_used': False,
        'objective': 'Mature local PDF extraction adapter with exact extracted-text provenance, native block geometry and unverified auxiliary tables',
        'baseline': {'accepted_full_passed': 990, 't05_all_accepted_hashes_match': True,
                     'all_original_test_files_unchanged': True, 'minimum_failures': 13},
        'production_files': production, 'new_test_file': 'tests/test_pdf_adapter_acceptance.py',
        'new_test_cases': results['full-final-02']['tests']-990,
        'verification': results, 'commands': commands,
        'optional_dependency': json.loads((OUT/'dependency-install.json').read_text()),
        'base_dependency_versions_unchanged': base_versions,
        'base_lock_unchanged': True,
        'scientific_evidence': 'final-evidence/pdf-results.json', 'preview': 'final-evidence/synthetic-page-1.png',
        'contract': 'implementation-contract.md', 'execution_plan': 'execution-plan.md', 'diff': 'changes.patch',
        'implementation_sha256': implementation_fingerprint(),
        'final_hashes': {rel: sha(ROOT/rel) for rel in files},
        'compatibility': [
            'Existing pypdf and UTF-8 ingestion defaults/payloads remain compatible; selecting a new engine creates a new immutable source.',
            'Only the optional pymupdf backend has the new process deadline and layout/table result.',
            'Page/source/character offset APIs, source whitelist, T05 binding and T03b exact-version result adoption remain in force.',
            'No OCR, formula transcription or cloud upload is silently introduced.'
        ],
        'limitations': [
            'Offsets address extracted page text, not bytes in the original PDF; locators are block-level native coordinates.',
            'Native table data and coordinate-sorted reading order remain unverified; no general Agent table-reasoning tool is added.',
            'OCR and formula recognition are not_run; empty/mixed-image pages and failed tables disclose gaps.',
            'Time/output bounds are not OS RAM quotas or hard real-time guarantees; legacy pypdf retains its previous execution behavior.',
            'Actual parser and ODE evidence uses synthetic PDFs and scripted Agent actions, not new model or independent holdout scores.',
            'Only official dependency packages were downloaded; no project/PDF/independent evaluation/c13/c18 material transmission.'
        ],
        'next_task': 'T07: domain-reviewed retrieval development corpus and annotation protocol; actual paper-disjoint comparison before embedding decisions'
    }
    (OUT/'acceptance.json').write_text(json.dumps(acceptance, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'status': acceptance['status'], 'full': results['full-final-02'],
                      'new_test_cases': acceptance['new_test_cases'], 'original_tests_unchanged': True,
                      'base_dependencies_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
