"""Freeze engineering acceptance separately from outstanding domain review."""
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


def xml_result(name):
    root = ET.parse(OUT/(name+'.xml')).getroot()
    suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
    return {key: sum(float(s.get(key, 0)) if key == 'time' else int(s.get(key, 0)) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped', 'time')}


def main():
    results = {n: xml_result(n) for n in ('first', 'expanded', 'focused-final', 'full',
                                         'nested-label-baseline', 'final-check', 'full-final')}
    assert results['nested-label-baseline']['failures'] == 1
    assert all(v['failures'] == v['errors'] == v['skipped'] == 0 for n, v in results.items() if n != 'nested-label-baseline')
    baseline = json.loads((OUT/'baseline-hashes.json').read_text())
    retained = [p for p in baseline if p.startswith(('src/', 'tests/')) or p in
                ('benchmarks/development/evaluate_retrieval.py', 'benchmarks/inputs/retrieval-development/cases.json')]
    assert all((ROOT/p).is_file() and sha(ROOT/p) == baseline[p] for p in retained)
    previous = json.loads((ROOT/'artifacts/audit-t06-pdf-adapter-20261019/acceptance.json').read_text())
    assert all((sha(OUT/'before'/p) if (OUT/'before'/p).exists() else baseline.get(p, sha(ROOT/p))) == value
               for p, value in previous['final_hashes'].items())
    assert implementation_fingerprint() == previous['implementation_sha256']
    packet = json.loads((OUT/'review-pack/packet.json').read_text())
    form = json.loads((OUT/'review-pack/review-forms.json').read_text())
    inventory = json.loads((OUT/'review-pack/inventory.json').read_text())
    assert inventory['audit']['real_paper_count'] == 0 and inventory['audit']['paper_families'] == 1
    assert all(c['review']['status'] == 'pending' and c['review']['reviewer'] is None for c in packet['cases'])
    assert all(v['status'] == 'pending' and v['reviewer'] is None and not any(v['checks'].values()) for v in form['decisions'].values())
    assert inventory['audit']['reviewed_comparison_ready'] is False
    comparison = json.loads((OUT/'development-comparison-final/results.json').read_text())
    implementation = json.loads((OUT/'development-comparison-final/implementation.json').read_text())
    assert implementation['evaluation_sha256'] == sha(ROOT/'benchmarks/development/retrieval_review.py')
    assert implementation['retrieval_sha256'] == baseline['src/biomodel_agent/retrieval.py']
    old = json.loads((OUT/'legacy-baseline/results.json').read_text())
    assert comparison['status'] == 'development_diagnostic_pending_domain_review' and comparison['semantic_support'] == 'unknown'
    for name in ('lexical_v1', 'passage_v2'):
        before, after = old['summary'][name], comparison['summary'][name]
        assert before['covered_anchors'] == after['covered_required_spans']
        for field in ('snippet_chars', 'response_json_chars', 'irrelevant_hit_fraction', 'outside_annotated_span_fraction'):
            assert before[field] == after[field]
        assert after['failed_cases'] == 0
    gate = json.loads((OUT/'gate-refusal.log').read_text())
    assert gate['status'] == 'refused' and not (OUT/'reviewed-comparison-refused').exists()
    files = ['benchmarks/development/retrieval_review.py', 'tests/test_retrieval_review_acceptance.py',
             'docs/retrieval-review.md', 'docs/optimization-backlog.md', 'docs/seven-point-status.md']
    diff = []
    for rel in files:
        current, before = ROOT/rel, OUT/'before'/rel
        diff.extend(difflib.unified_diff(before.read_text().splitlines(True) if before.exists() else [],
                                       current.read_text().splitlines(True),
                                       fromfile='a/'+rel if before.exists() else '/dev/null', tofile='b/'+rel))
        target = OUT/'final'/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target)
    (OUT/'changes.patch').write_text(''.join(diff), encoding='utf-8')
    hashes = {str(p.relative_to(OUT/'review-pack')): sha(p) for p in sorted((OUT/'review-pack').rglob('*')) if p.is_file()}
    (OUT/'review-pack-file-hashes.json').write_text(json.dumps(hashes, indent=2)+'\n')
    acceptance = {
        'task_id': 'T07a', 'parent_task': 'T07', 'status': 'engineering_accepted',
        'parent_task_status': 'domain_review_and_T07b_pending',
        'accepted_at_utc': datetime.now(timezone.utc).isoformat(),
        'implementation_owner': 'Codex alone', 'dsh_or_subagents_used': False,
        'user_scope': '先完成审核包，暂不加入新论文',
        'objective': 'Reviewable existing retrieval materials, attribution/freshness/split gates and separated development metrics',
        'baseline': {'accepted_full_passed': 1030, 't06_hashes_match': True,
                     'all_product_sources_and_original_tests_unchanged': True,
                     'original_retrieval_evaluator_and_material_bytes_unchanged': True},
        'new_benchmark_tool': 'benchmarks/development/retrieval_review.py',
        'new_test_file': 'tests/test_retrieval_review_acceptance.py',
        'new_test_cases': results['full-final']['tests']-1030,
        'verification': results,
        'commands': {
            'legacy_baseline': 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python benchmarks/development/evaluate_retrieval.py --output artifacts/audit-t07-retrieval-review-20261019/legacy-baseline',
            'first': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_retrieval_review_acceptance.py --basetemp=/tmp/biomodel-t07-first --junitxml=artifacts/audit-t07-retrieval-review-20261019/first.xml',
            'expanded': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_retrieval_review_acceptance.py tests/test_passage_retrieval.py tests/test_source_retrieval.py --basetemp=/tmp/biomodel-t07-expanded --junitxml=artifacts/audit-t07-retrieval-review-20261019/expanded.xml',
            'focused_final': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_retrieval_review_acceptance.py tests/test_passage_retrieval.py tests/test_source_retrieval.py --basetemp=/tmp/biomodel-t07-final-focused --junitxml=artifacts/audit-t07-retrieval-review-20261019/focused-final.xml',
            'full': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/biomodel-t07-full --junitxml=artifacts/audit-t07-retrieval-review-20261019/full.xml',
            'nested_label_baseline': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_retrieval_review_acceptance.py::test_source_page_cannot_smuggle_annotations_in_extra_metadata --basetemp=/tmp/biomodel-t07-nested-label-baseline --junitxml=artifacts/audit-t07-retrieval-review-20261019/nested-label-baseline.xml',
            'final_check': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_retrieval_review_acceptance.py tests/test_passage_retrieval.py tests/test_source_retrieval.py --basetemp=/tmp/biomodel-t07-final-check --junitxml=artifacts/audit-t07-retrieval-review-20261019/final-check.xml',
            'full_final': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/biomodel-t07-full-final --junitxml=artifacts/audit-t07-retrieval-review-20261019/full-final.xml',
            'prepare': 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python benchmarks/development/retrieval_review.py prepare --output artifacts/audit-t07-retrieval-review-20261019/review-pack',
            'development_diagnostic': 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python benchmarks/development/retrieval_review.py evaluate --packet artifacts/audit-t07-retrieval-review-20261019/review-pack/packet.json --mode development --repeats 3 --output artifacts/audit-t07-retrieval-review-20261019/development-comparison-final',
            'expected_gate_refusal': 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python benchmarks/development/retrieval_review.py evaluate --packet artifacts/audit-t07-retrieval-review-20261019/review-pack/packet.json --mode reviewed --output artifacts/audit-t07-retrieval-review-20261019/reviewed-comparison-refused'},
        'actual_review_state': inventory['audit'],
        'review_packet': 'review-pack/packet.json', 'review_entry': 'review-pack/REVIEW.md',
        'review_form': 'review-pack/review-forms.json', 'packet_file_hashes': 'review-pack-file-hashes.json',
        'development_results': 'development-comparison-final/results.json',
        'development_summary': comparison['summary'],
        'formal_gate': {'status': gate['status'], 'expected_exit_code': 2, 'created_output_directory': False},
        'retrieval_calls': sum(len(r['retrieval_ms']) for r in comparison['rows']),
        'diff': 'changes.patch', 'contract': 'implementation-contract.md', 'execution_plan': 'execution-plan.md',
        'final_hashes': {rel: sha(ROOT/rel) for rel in files},
        'unchanged_retrieval_source_sha256': sha(ROOT/'src/biomodel_agent/retrieval.py'),
        'unchanged_product_implementation_sha256': implementation_fingerprint(),
        'limitations': [
            'No actual domain review is complete; all 18 real packet decisions remain pending. T07 overall is not accepted.',
            'No new papers or independent samples; 18 seen synthetic cases share one conservative family, with zero real papers.',
            'Family and exact normalized-page checks rely on metadata; no near-duplicate, DOI/preprint resolution or independence proof.',
            'Review fields establish attributed completeness/freshness, not reviewer identity or expertise.',
            'Condition A/B conflict label remains a review priority; coverage describes candidate annotations, not verified contradictions.',
            'Timing includes retrieval only with uncontrolled cache/order noise. Characters are not tokens, cost or Agent success.',
            'No ranking/default changes, embedding, inference, new dependencies, independent evalset reads or c13/c18 transmission.'
        ],
        'next_task': 'T07b: actual domain decisions, scope-label corrections and reviewed development comparison; new paper material requires a later authorized scope'
    }
    (OUT/'acceptance.json').write_text(json.dumps(acceptance, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({'status': acceptance['status'], 'parent_task_status': acceptance['parent_task_status'],
                      'full': results['full-final'], 'pending_reviews': 18, 'product_and_original_tests_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
