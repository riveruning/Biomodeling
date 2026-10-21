"""Validate delivery data and preserve exact original-file/annotation differences."""
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
OLD = ROOT / 'artifacts/audit-t07-retrieval-review-20261019'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    frozen = load(OUT / 'baseline-hashes.json')
    changed = [name for name, expected in frozen.items() if sha(ROOT / name) != expected]
    assert not changed, changed
    py_files = {str(p.relative_to(ROOT)) for folder in ('src', 'tests') for p in (ROOT / folder).rglob('*.py')}
    assert py_files == {p for p in frozen if p.startswith(('src/', 'tests/'))}
    source_packet = load(OLD / 'review-pack/packet.json')
    candidate = load(OUT / 'candidate-pack/packet.json')
    pre_review = load(OUT / 'pre-review.json')
    assert len(pre_review['cases']) == 18
    assert Counter(r['recommendation'] for r in pre_review['cases']) == {'retain_candidate': 17, 'revise_annotation': 1}
    assert pre_review['independence'] == 'not_independent_of_Codex_annotation_author'
    assert not pre_review['human_or_expert_approval'] and not pre_review['formal_review_decisions_written']
    for old_case, new_case, record in zip(source_packet['cases'], candidate['cases'], pre_review['cases']):
        assert old_case['id'] == new_case['id'] == record['id']
        assert old_case['sources'] == new_case['sources']
        assert old_case['query'] == new_case['query']
        assert old_case['review'] == new_case['review']
        assert record['anchor_evidence'] == [dict(a, exact_source_slice=True) for a in old_case['anchors']]
    assert Counter(c['review']['status'] for c in candidate['cases']) == {'pending': 18}
    assert candidate['budget'] == source_packet['budget']
    assert load(OUT / 'candidate-check.log')['reviewed_comparison_ready'] is False

    before = load(OLD / 'development-comparison-final/results.json')
    after = load(OUT / 'candidate-development/results.json')
    assert before['budget'] == after['budget'] and before['repeats'] == after['repeats'] == 3
    assert before['method_order'] == after['method_order']
    old_rows = {(r['method'], r['case_id']): r for r in before['rows']}
    new_rows = {(r['method'], r['case_id']): r for r in after['rows']}
    assert len(old_rows) == len(new_rows) == 36 and old_rows.keys() == new_rows.keys()
    for key, row in new_rows.items():
        assert row['status'] == old_rows[key]['status'] == 'ok'
        assert row['output'] == old_rows[key]['output'], key
        assert len(row['retrieval_ms']) == 3
    for method in after['method_order']:
        for metric, value in after['summary'][method].items():
            if metric not in {'conflict_cases', 'complete_conflict_cases', 'retrieval_ms_total', 'retrieval_ms_median'}:
                assert value == before['summary'][method][metric], (method, metric)
        assert new_rows[method, 'clarification_scope']['scores']['all_required_spans_covered'] is True
        assert after['summary'][method]['conflict_cases'] == 2
    assert after['summary']['lexical_v1']['complete_conflict_cases'] == 1
    assert after['summary']['passage_v2']['complete_conflict_cases'] == 2
    assert after['status'] == 'development_diagnostic_pending_domain_review'
    assert after['semantic_support'] == 'unknown'
    refusal = load(OUT / 'reviewed-refusal.log')
    assert refusal['status'] == 'refused' and 'review gate' in refusal['error']
    assert not (OUT / 'reviewed-refused').exists()

    docs = ('docs/retrieval-review.md', 'docs/optimization-backlog.md', 'docs/seven-point-status.md')
    patches = []
    for name in docs:
        target = ROOT / name
        snapshot = OUT / 'final' / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(target.read_bytes())
        patches += difflib.unified_diff((OUT / 'before' / name).read_text(encoding='utf-8').splitlines(keepends=True),
                                       target.read_text(encoding='utf-8').splitlines(keepends=True),
                                       fromfile='before/' + name, tofile='final/' + name)
    (OUT / 'documentation.patch').write_text(''.join(patches), encoding='utf-8')
    link_count = 0
    for path in [OUT / 'REVIEW.md', OUT / 'candidate-pack/REVIEW.md'] + [ROOT / name for name in docs]:
        for label, target in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            linked = path.parent / target.split('#', 1)[0]
            if linked.resolve() == OUT / 'verification.json':
                continue  # This output is written below.
            assert linked.exists(), (path, label, target)
            link_count += 1
    verification = {
        'status': 'passed', 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'AI development pre-review delivery and candidate data only; not domain expert acceptance',
        'frozen_files_checked': len(frozen), 'changed_frozen_files': changed,
        'src_and_test_python_paths_unchanged': True,
        'original_packet_and_forms_unchanged': True, 'retrieval_and_evaluation_code_unchanged': True,
        'new_candidate_cases': 18, 'changed_candidate_cases': ['clarification_scope'],
        'pending_decisions': 18, 'approved_decisions': 0,
        'exact_annotation_slices': 21, 'changed_original_quotes_offsets_or_sources': 0,
        'retrieval_rows_with_identical_original_outputs': len(new_rows),
        'retrieval_calls': sum(len(r['retrieval_ms']) for r in new_rows.values()),
        'development_summary': after['summary'],
        'formal_review_gate': {**refusal, 'expected_command_exit_code': 2, 'created_output_directory': False},
        'old_form_for_revised_content': pre_review['old_review_form_for_candidate'],
        'local_markdown_links_checked': link_count,
        'product_tests_rerun': False, 'reason': 'No product, benchmark tool or test code changed; validation targets changed data and review gates.',
        'last_prior_full_regression': {'task': 'T07a', 'passed': 1075, 'rerun_this_round': False},
        'new_papers': 0, 'external_model_calls': 0, 'independent_evalset_access': False,
        'remaining': ['Independent attributed domain review of candidate annotations; T07 remains open.']
    }
    (OUT / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    hashes = {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*')
              if p.is_file() and p.name not in {'delivery-hashes.json', 'verification.log'}}
    (OUT / 'delivery-hashes.json').write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'passed', 'frozen_files': len(frozen), 'source_spans': 21,
                      'identical_output_rows': len(new_rows), 'retrieval_calls': verification['retrieval_calls'],
                      'formal_pending': 18, 'links_checked': link_count}, ensure_ascii=False))


if __name__ == '__main__':
    main()
