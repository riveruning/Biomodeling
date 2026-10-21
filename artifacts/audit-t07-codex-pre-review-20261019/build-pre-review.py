"""Freeze Codex's attributed pre-review and a separate, still-pending candidate.

This artifact script does not approve reviews or modify product/source materials.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ORIGINAL = ROOT / 'artifacts/audit-t07-retrieval-review-20261019/review-pack/packet.json'

spec = importlib.util.spec_from_file_location('review_protocol', ROOT / 'benchmarks/development/retrieval_review.py')
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main():
    packet = json.loads(ORIGINAL.read_text(encoding='utf-8'))
    assessments = json.loads((OUT / 'case-assessments.json').read_text(encoding='utf-8'))
    original_audit = protocol.validate_packet(packet)
    assert [c['id'] for c in packet['cases']] == [c['id'] for c in assessments]
    records = []
    for case, assessment in zip(packet['cases'], assessments):
        evidence = []
        pages = {(s['id'], p['number']): p['text'] for s in case['sources'] for p in s['pages']}
        for anchor in case['anchors']:
            text = pages[anchor['source_id'], anchor['page']]
            assert text[anchor['start']:anchor['end']] == anchor['quote']
            evidence.append({**anchor, 'exact_source_slice': True})
        records.append({**assessment, 'original_case_sha256': protocol.case_digest(packet, case),
                        'original_expectation': case['expectation'], 'original_category': case['category'],
                        'anchor_evidence': evidence,
                        'source_pages_sha256': {s['id']: s['pages_sha256'] for s in case['sources']}})

    candidate = deepcopy(packet)
    case = next(c for c in candidate['cases'] if c['id'] == 'clarification_scope')
    case['category'] = 'condition_scope'
    case['expectation'] = 'answerable'
    for anchor in case['anchors']:
        anchor['role'] = 'support_candidate'
    case['annotation_note'] = (
        'Codex AI development pre-review correction candidate, not independently domain-approved. '
        'Authorized clarification assigns X(0)=2 nmol to condition A and X(0)=5 nmol to condition B; '
        'these are compatible conditional initial values. Retrieve both sources and preserve attribution. '
        'The query can be answered as a condition-to-value mapping; a single execution still needs the target condition. '
        'Both anchors remain required. Seen synthetic development material only.'
    )
    candidate_audit = protocol.validate_packet(candidate)
    assert candidate_audit['review_status'] == {'pending': 18}
    assert not candidate_audit['reviewed_comparison_ready']
    changed = [a['id'] for a, b in zip(packet['cases'], candidate['cases']) if a != b]
    assert changed == ['clarification_scope']
    assert all(a['sources'] == b['sources'] and a['query'] == b['query'] and a['review'] == b['review']
               for a, b in zip(packet['cases'], candidate['cases']))
    for a, b in zip(packet['cases'], candidate['cases']):
        assert [{k: v for k, v in x.items() if k != 'role'} for x in a['anchors']] == [
            {k: v for k, v in x.items() if k != 'role'} for x in b['anchors']]
    assert protocol.digest(protocol.content(packet)) != protocol.digest(protocol.content(candidate))
    original_form = json.loads(ORIGINAL.with_name('review-forms.json').read_text(encoding='utf-8'))
    try:
        protocol.apply_reviews(candidate, original_form)
    except ValueError as exc:
        stale_form = {'refused': True, 'error': str(exc)}
    else:
        raise AssertionError('Original review form unexpectedly accepted for revised annotations')

    protocol.write_review_pack(candidate, OUT / 'candidate-pack')
    before = json.dumps(packet, ensure_ascii=False, indent=2).splitlines(keepends=True)
    after = json.dumps(candidate, ensure_ascii=False, indent=2).splitlines(keepends=True)
    (OUT / 'candidate-annotations.patch').write_text(''.join(difflib.unified_diff(
        before, after, fromfile=str(ORIGINAL.relative_to(ROOT)), tofile='candidate-pack/packet.json')), encoding='utf-8')
    report = {
        'schema_version': 1, 'task': 'T07b_codex_pre_review', 'status': 'ai_development_pre_review_complete',
        'reviewer': 'Codex', 'reviewer_kind': 'AI development self-review',
        'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'independence': 'not_independent_of_Codex_annotation_author',
        'human_or_expert_approval': False, 'formal_review_decisions_written': False,
        'source_packet': str(ORIGINAL.relative_to(ROOT)),
        'source_packet_file_sha256': hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(),
        'source_packet_content_sha256': protocol.digest(protocol.content(packet)),
        'candidate_packet_content_sha256': protocol.digest(protocol.content(candidate)),
        'summary': {'cases': len(records), 'recommendations': dict(Counter(r['recommendation'] for r in records)),
                    'exact_source_spans': sum(len(r['anchor_evidence']) for r in records),
                    'source_span_mismatches': 0, 'changed_candidate_cases': changed,
                    'new_papers': 0, 'human_approved_cases': 0},
        'original_packet_audit': original_audit, 'candidate_packet_audit': candidate_audit,
        'old_review_form_for_candidate': stale_form,
        'scientific_review_method': 'Read all provided case texts and target statements; preserve conditions, units, symbols, provenance, missingness and conflicting scopes. Exact spans and hashes checked deterministically.',
        'cases': records,
        'limitations': [
            'AI pre-review shares the Codex development origin; it is not an independent domain expert review.',
            'All 18 cases are seen synthetic controls, grouped conservatively as one family; zero real papers.',
            'Reported declarations are checked against supplied text, not experiments or a complete physical model.',
            'No papers, independent evalset, external inference, online sources or c13/c18 materials were accessed.',
            'Candidate remains pending and is only for development; semantic_support remains unknown.',
            'Deterministic retrieval of the injection case does not establish LLM injection resistance.'
        ]
    }
    write_json(OUT / 'pre-review.json', report)
    lines = ['# T07：Codex 逐案预审', '',
             '已完成 18 个已见合成案例的逐案预审：17 项建议保留候选标注，1 项建议修订；21 个候选片段的原文切片全部匹配。', '',
             '**归属：Codex AI 开发自查。** 原标注也来自 Codex，本轮没有独立领域审核人，没有填写任何人工 approved；原包保持不变。', '',
             '审核范围是所给文本对查询的支持、变量/单位/条件、缺口与冲突、来源分组。没有核验真实实验或完整模型，不能据此宣称科学语义验证通过。', '',
             '## 需要修订的标签', '',
             '`clarification_scope` 的澄清明确说：条件 A 为 2 nmol，条件 B 为 5 nmol。两种条件的初值可以同时成立，旧 `conflicting` 预期错误。', '',
             '独立保存的 [候选包](candidate-pack/REVIEW.md) 改为 `condition_scope` / `answerable`，两条引用均为 `support_candidate`，仍要求全部覆盖。它回答条件到初值的对应；单次模拟仍要确定条件。研究者澄清保留自身来源归属。', '',
             '没有修改原文、查询、偏移、预算或原审核状态。只有这一项的类别、预期、两条角色及标注说明变更，见 [精确差异](candidate-annotations.patch)。新包的全部 18 项仍 pending，旧审核表因内容哈希变化被拒绝。', '',
             '## 逐案判断', '',
             '| 案例 | 建议 | 文本支持的回答 | 审核依据与边界 |',
             '|---|---|---|---|']
    for r in records:
        recommendation = '修订候选' if r['recommendation'] == 'revise_annotation' else '保留候选'
        lines.append(f"| `{r['id']}` | {recommendation} | {r['answer']} | {r['reason']} {r['scope_limit']} |")
    lines += ['', '## 后续', '',
              '1. 独立审核人重点确认条件作用域修订，以及参数单位、amount/concentration 和“未定位/明确缺失”的边界。可直接在候选包的新审核表逐案记录决定；本预审只提供可核对的依据。',
              '2. 目前只能运行 development 诊断。reviewed 模式仍需实际审核记录，T07 整体不结项。即使审核完成，这些已见合成案例仍只能用于开发回归。',
              '3. 不因该标签修订切换默认检索器或引入 embedding；它不修复长段窗口与词汇差异两个已知检索失败。', '',
              '原始逐案数据和来源坐标见 [pre-review.json](pre-review.json)。开发复算及冻结文件校验另见 [verification.json](verification.json)。', '']
    (OUT / 'REVIEW.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'summary': report['summary'], 'candidate_audit': candidate_audit, 'old_form': stale_form}, ensure_ascii=False))


if __name__ == '__main__':
    main()
