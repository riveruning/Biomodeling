"""Verify retained files/results, then freeze the exact T05 delivery."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from biomodel_agent.recovery import implementation_fingerprint

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result(name):
    root = ET.parse(OUT/(name+'.xml')).getroot()
    suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
    return {key: sum(float(s.get(key, 0)) if key == 'time' else int(s.get(key, 0)) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped', 'time')}


def main():
    verification = {name: result(name) for name in ('baseline', 'targeted-01', 'parser-01', 'full-01', 'expanded-01', 'expanded-02', 'full-final')}
    assert verification['baseline']['failures'] == 16
    for name in ('parser-01', 'full-01', 'expanded-02', 'full-final'):
        assert verification[name]['failures'] == verification[name]['errors'] == verification[name]['skipped'] == 0
    baseline = json.loads((OUT/'baseline-hashes.json').read_text())
    assert all((ROOT/p).exists() and sha(ROOT/p) == value for p, value in baseline.items() if p.startswith('tests/'))
    previous = json.loads((ROOT/'artifacts/audit-t04-study-execution-20261019/acceptance.json').read_text())
    assert all((sha(OUT/'before'/p) if (OUT/'before'/p).exists() else baseline.get(p, sha(ROOT/p))) == value
               for p, value in previous['final_hashes'].items())
    files = sorted([str(p.relative_to(ROOT)) for p in (ROOT/'src').rglob('*.py') if str(p.relative_to(ROOT)) not in baseline or sha(p) != baseline[str(p.relative_to(ROOT))]])
    assert set(files) == {'src/biomodel_agent/'+name+'.py' for name in ('bindings', 'agent_tools', 'evidence', 'application', 'agent', 'llm', 'reporting')}
    files += ['tests/test_bindings.py', 'tests/test_evidence_binding_acceptance.py', 'docs/evidence-binding.md',
              'docs/optimization-backlog.md', 'docs/seven-point-status.md']
    diff = []
    for rel in files:
        before = OUT/'before'/rel
        current = ROOT/rel
        old = before.read_text().splitlines(True) if before.exists() else []
        diff.extend(difflib.unified_diff(old, current.read_text().splitlines(True),
                                      fromfile='a/'+rel if before.exists() else '/dev/null', tofile='b/'+rel))
        target = OUT/'final'/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target)
    (OUT/'changes.patch').write_text(''.join(diff))
    scientific = json.loads((OUT/'scientific-results.json').read_text())
    assert all(scientific[n]['state'] == 'completed' for n in ('formula_repair', 'clarification', 'recovery'))
    assert scientific['manual_bypass']['run_count'] == scientific['manual_bypass']['simulation_input_count'] == 0
    acceptance = {
        'task_id': 'T05', 'status': 'accepted', 'accepted_at_utc': datetime.now(timezone.utc).isoformat(),
        'implementation_owner': 'Codex alone, continuing the latest user-authorized implementation arrangement',
        'dsh_or_subagents_used': False,
        'objective': 'Versioned source-position and typed target/value/expression binding with bounded deterministic validation',
        'baseline': {'accepted_full_passed': 926, 'previous_acceptance_hashes_match': True,
                     'all_original_tests_unchanged': True, 'minimal_failures': 16},
        'production_files': [p for p in files if p.startswith('src/')],
        'new_test_files': [p for p in files if p.startswith('tests/')],
        'new_test_cases': verification['full-final']['tests']-926,
        'verification': verification,
        'commands': {
            'baseline': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_binding_acceptance.py --basetemp=/tmp/biomodel-t05-baseline --junitxml=artifacts/audit-t05-evidence-binding-20261019/baseline.xml',
            'targeted-01': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_binding_acceptance.py tests/test_citation_diagnostics.py tests/test_source_and_citation_regressions.py tests/test_run_verdict_acceptance.py --basetemp=/tmp/biomodel-t05-targeted --junitxml=artifacts/audit-t05-evidence-binding-20261019/targeted-01.xml',
            'full-01': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/biomodel-t05-full-01 --junitxml=artifacts/audit-t05-evidence-binding-20261019/full-01.xml',
            'parser-01': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bindings.py --basetemp=/tmp/biomodel-t05-parser --junitxml=artifacts/audit-t05-evidence-binding-20261019/parser-01.xml',
            'expanded-01': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bindings.py tests/test_evidence_binding_acceptance.py --basetemp=/tmp/biomodel-t05-expanded --junitxml=artifacts/audit-t05-evidence-binding-20261019/expanded-01.xml',
            'expanded-02': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bindings.py tests/test_evidence_binding_acceptance.py tests/test_storage_evidence.py --basetemp=/tmp/biomodel-t05-expanded-02 --junitxml=artifacts/audit-t05-evidence-binding-20261019/expanded-02.xml',
            'full-final': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/biomodel-t05-full-final --junitxml=artifacts/audit-t05-evidence-binding-20261019/full-final.xml',
            'scientific_evidence': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python artifacts/audit-t05-evidence-binding-20261019/scientific-evidence.py'},
        'scientific_evidence': 'scientific-results.json',
        'diff': 'changes.patch', 'contract': 'implementation-contract.md', 'execution_plan': 'execution-plan.md',
        'implementation_sha256': implementation_fingerprint(),
        'final_hashes': {p: sha(ROOT/p) for p in files},
        'compatibility': [
            'Generic legacy claims remain readable and retain quote-only coverage; typed execution recomputes support.',
            'No-citation numerical diagnostics retain unverified execution; missing coverage remains unknown.',
            'Known source conflicts and missing numeric values retain priority; explicit hypotheses retain conjecture authority.',
            'T03b exact source fingerprints and result adoption, and T04 study deadlines are preserved.'
        ],
        'limitations': [
            'Binding matched is not semantic support, unit correctness, reaction stoichiometry or scientific truth; semantic_support stays unknown.',
            'Only bounded direct ASCII declarations, restricted expressions and explicitly witnessed aliases are recognized.',
            'Complex language, general equivalences, unit conversion, Chinese/LaTeX/image formulas and cross-page semantics remain unknown.',
            'Scripted decisions plus real calculations are development evidence, not new model capability or independent holdout scores.',
            'No external inference, DSH, subagents, dependency changes, independent evalset reads or c13/c18 transmission.'
        ],
        'next_task': 'T06: thin adapter for mature PDF parser outputs with preserved original PDF, parser version and page/block/original offset provenance'
    }
    (OUT/'acceptance.json').write_text(json.dumps(acceptance, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'status': acceptance['status'], 'full': verification['full-final'], 'all_original_tests_unchanged': True,
                      'production_files': acceptance['production_files']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
