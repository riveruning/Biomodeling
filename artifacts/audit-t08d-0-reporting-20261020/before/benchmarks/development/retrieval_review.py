"""Offline retrieval review preparation and development measurement, protocol v1."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import time

from biomodel_agent import retrieval
from biomodel_agent.storage import ArtifactStore

PROTOCOL = 'retrieval_review_v1'
REQUIRED_ROLES = {'support_candidate', 'conflict_candidate', 'missing_statement'}
ROLES = REQUIRED_ROLES | {'related_context', 'distractor'}
REVIEW_CHECKS = {'source_span', 'target_scope', 'answerability', 'paper_grouping'}
MAX_PACKET_BYTES = 10_000_000
ID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in {low}..{high}')
    return value


def _id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('invalid material/case identifier')
    return value


def content(packet):
    return {**packet, 'cases': [{k: v for k, v in c.items() if k != 'review'} for c in packet['cases']]}


def case_digest(packet, case):
    return digest({'protocol': packet['protocol'], 'corpus_id': packet['corpus_id'],
                   'origin': packet['origin'], 'exposure': packet['exposure'],
                   'annotation_author': packet['annotation_author'], 'budget': packet['budget'],
                   'case': {k: v for k, v in case.items() if k != 'review'}})


def build_packet(legacy):
    """Convert already-seen synthetic controls; do not invent domain approvals."""
    packet = {'schema_version': 1, 'protocol': PROTOCOL, 'corpus_id': 'retrieval-development-20261019',
              'origin': 'synthetic_development', 'exposure': 'seen_development',
              'usage': 'development_only', 'annotation_author': 'Codex development candidate annotations',
              'budget': {'limit': 3, 'max_chars': 1200}, 'cases': []}
    for old in legacy['cases']:
        category = old['category']
        expectation = ('conflicting' if category == 'conflict' else 'explicit_missing' if category == 'missing'
                       else 'not_located' if not old['anchors'] else 'answerable')
        note = old['annotation_note']
        if old['id'] == 'clarification_scope':
            note += ' Review priority: condition A/B statements may be compatible; the legacy conflict label is only a retrieval candidate, not a verified scientific contradiction.'
        role = {'conflicting': 'conflict_candidate', 'explicit_missing': 'missing_statement'}.get(expectation, 'support_candidate')
        sources = []
        for s in old['sources']:
            sources.append({**deepcopy(s), 'role': 'supplement' if s['id'] == 'supplement' else
                            'clarification' if s['id'] == 'clarification' else 'main',
                            'pages_sha256': digest(s['pages'])})
        packet['cases'].append({'id': old['id'], 'category': category, 'query': old['query'],
                                'paper_family_id': 'shared-synthetic-development-template', 'split': 'development',
                                'sources': sources,
                                'anchors': [dict(deepcopy(a), id=f'a{i}', role=role) for i, a in enumerate(old['anchors'], 1)],
                                'expectation': expectation, 'annotation_note': note,
                                'review': {'status': 'pending', 'reviewer': None, 'reviewed_at': None,
                                           'reviewed_sha256': None, 'notes': '',
                                           'checks': {k: False for k in sorted(REVIEW_CHECKS)}}})
    validate_packet(packet)
    return packet


def validate_packet(packet):
    """Validate all material/review state before any storage or retrieval."""
    if not isinstance(packet, dict) or set(packet) != {'schema_version', 'protocol', 'corpus_id', 'origin', 'exposure', 'usage', 'annotation_author', 'budget', 'cases'}:
        raise ValueError('unknown or missing packet fields')
    if type(packet['schema_version']) is not int or packet['schema_version'] != 1 or packet['protocol'] != PROTOCOL:
        raise ValueError('unsupported review protocol')
    _id(packet['corpus_id'])
    if (packet['origin'] not in ('synthetic_development', 'paper_development') or
            packet['exposure'] not in ('seen_development', 'not_established') or packet['usage'] != 'development_only'):
        raise ValueError('review packet cannot assert independent holdout status')
    if packet['origin'] == 'synthetic_development' and packet['exposure'] != 'seen_development':
        raise ValueError('synthetic development material must retain seen exposure')
    if not isinstance(packet['annotation_author'], str) or not packet['annotation_author'].strip():
        raise ValueError('annotation author required')
    budget = packet['budget']
    if not isinstance(budget, dict) or set(budget) != {'limit', 'max_chars'}:
        raise ValueError('invalid frozen budget')
    _integer(budget['limit'], 'limit', 1, 10)
    _integer(budget['max_chars'], 'max_chars', 256, 8000)
    if not isinstance(packet['cases'], list) or not 1 <= len(packet['cases']) <= 200:
        raise ValueError('expected 1..200 cases')
    ids, families, page_splits, total_chars = set(), {}, {}, 0
    for case in packet['cases']:
        if not isinstance(case, dict) or set(case) != {'id', 'category', 'query', 'paper_family_id', 'split', 'sources', 'anchors', 'expectation', 'annotation_note', 'review'}:
            raise ValueError('unknown or missing case fields')
        ident = _id(case['id'])
        if ident in ids:
            raise ValueError('duplicate case id')
        ids.add(ident)
        family = _id(case['paper_family_id'])
        split = case['split']
        if split not in ('development', 'comparison_candidate'):
            raise ValueError('invalid split')
        if family in families and families[family] != split:
            raise ValueError('paper family crosses splits (main/supplement/version leakage)')
        families[family] = split
        if packet['exposure'] == 'seen_development' and split != 'development':
            raise ValueError('seen development material cannot be promoted to a comparison split')
        retrieval._validate_query(case['query'])
        if not isinstance(case['category'], str) or not case['category'] or not isinstance(case['annotation_note'], str):
            raise ValueError('invalid case category/annotation note')
        sources, pages, page_count, chars = set(), {}, 0, 0
        if not isinstance(case['sources'], list) or not 1 <= len(case['sources']) <= 64:
            raise ValueError('expected 1..64 authorized documents per case')
        for source in case['sources']:
            if not isinstance(source, dict) or set(source) != {'id', 'role', 'pages', 'pages_sha256'} or source['role'] not in ('main', 'supplement', 'clarification', 'correction'):
                raise ValueError('invalid document fields/role')
            sid = _id(source['id'])
            if sid in sources:
                raise ValueError('duplicate source id')
            sources.add(sid)
            if source['pages_sha256'] != digest(source['pages']):
                raise ValueError('material hash mismatch')
            if not isinstance(source['pages'], list) or any(not isinstance(p, dict) or set(p) != {'number', 'text'} for p in source['pages']):
                raise ValueError('source page fields must contain only number and text; labels belong outside sources')
            for page in retrieval._validate_pages(source['pages'], sid):
                pages[sid, page['number']] = page['text']
                page_count += 1
                chars += len(page['text'])
                # Conservatively catch identical normalized nonblank pages in
                # different splits, even if family identifiers were changed.
                normalized = ' '.join(page['text'].split()).casefold()
                if normalized:
                    key = digest(normalized)
                    if key in page_splits and page_splits[key] != split:
                        raise ValueError('duplicate material crosses splits')
                    page_splits[key] = split
        total_chars += chars
        if page_count > 2000 or chars > 2_000_000 or total_chars > 4_000_000:
            raise ValueError('material scan budget exceeded')
        if not isinstance(case['anchors'], list) or len(case['anchors']) > 200:
            raise ValueError('invalid annotation count')
        aids, spans = set(), set()
        roles = Counter()
        for a in case['anchors']:
            if not isinstance(a, dict) or set(a) != {'id', 'source_id', 'page', 'start', 'end', 'quote', 'role'}:
                raise ValueError('invalid annotation fields')
            aid = _id(a['id'])
            if aid in aids or a['role'] not in ROLES:
                raise ValueError('duplicate annotation id or invalid evidence role')
            aids.add(aid)
            if any(type(a[k]) is not int for k in ('page', 'start', 'end')):
                raise ValueError('annotation positions must be strict integers')
            key = (a['source_id'], a['page'])
            text = pages.get(key)
            span = (*key, a['start'], a['end'])
            if text is None or not 0 <= a['start'] < a['end'] <= len(text) or text[a['start']:a['end']] != a['quote']:
                raise ValueError('invalid annotation source/span/quote')
            if span in spans:
                raise ValueError('duplicate annotation span inflates denominators')
            spans.add(span)
            roles[a['role']] += 1
        required = sum(roles[k] for k in REQUIRED_ROLES)
        expectation = case['expectation']
        if (expectation not in ('answerable', 'conflicting', 'explicit_missing', 'not_located', 'inaccessible') or
                expectation == 'answerable' and not roles['support_candidate'] or
                expectation == 'conflicting' and roles['conflict_candidate'] < 2 or
                expectation == 'explicit_missing' and not roles['missing_statement'] or
                expectation in ('not_located', 'inaccessible') and required):
            raise ValueError('answerability and required evidence roles disagree')
        review = case['review']
        if not isinstance(review, dict) or set(review) != {'status', 'reviewer', 'reviewed_at', 'reviewed_sha256', 'notes', 'checks'}:
            raise ValueError('invalid review fields')
        if review['status'] not in ('pending', 'approved', 'changes_requested', 'excluded'):
            raise ValueError('invalid review status')
        if not isinstance(review['checks'], dict) or set(review['checks']) != REVIEW_CHECKS or any(type(v) is not bool for v in review['checks'].values()):
            raise ValueError('review checklist requires named booleans')
        if review['status'] in ('approved', 'excluded'):
            if (not isinstance(review['reviewer'], str) or not review['reviewer'].strip() or
                    review['reviewer'].strip().casefold() == packet['annotation_author'].strip().casefold() or not isinstance(review['notes'], str) or not review['notes'].strip()):
                raise ValueError('attributed reviewer distinct from author and review notes required')
            try:
                stamp = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('timezone required')
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('review time with timezone required') from exc
            if review['reviewed_sha256'] != case_digest(packet, case):
                raise ValueError('stale review: content/group/budget changed')
            if review['status'] == 'approved' and not all(review['checks'].values()):
                raise ValueError('approved review needs complete scientific/scope checklist')
    return {'cases': len(ids), 'paper_families': len(families),
            'documents': sum(len(c['sources']) for c in packet['cases']),
            'required_spans': sum(a['role'] in REQUIRED_ROLES for c in packet['cases'] for a in c['anchors']),
            'review_status': dict(Counter(c['review']['status'] for c in packet['cases'])),
            'reviewed_comparison_ready': (all(c['review']['status'] in ('approved', 'excluded') for c in packet['cases'])
                                          and any(c['review']['status'] == 'approved' for c in packet['cases'])),
            'real_paper_count': 0 if packet['origin'] == 'synthetic_development' else None,
            'semantic_support': 'unknown'}


def gate(packet, mode):
    audit = validate_packet(packet)
    if mode not in ('development', 'reviewed'):
        raise ValueError('mode must be development or reviewed')
    if mode == 'reviewed' and any(c['review']['status'] not in ('approved', 'excluded') for c in packet['cases']):
        raise ValueError('review gate: complete attributed decisions required before comparison')
    selected = [c for c in packet['cases'] if c['review']['status'] != 'excluded']
    if not selected:
        raise ValueError('review gate: no included cases')
    return audit, selected


def apply_reviews(packet, form):
    validate_packet(packet)
    if not isinstance(form, dict) or set(form) != {'protocol', 'content_sha256', 'decisions'} or form['protocol'] != PROTOCOL or form['content_sha256'] != digest(content(packet)) or not isinstance(form['decisions'], dict):
        raise ValueError('stale or incompatible review form')
    out = deepcopy(packet)
    by_id = {c['id']: c for c in out['cases']}
    if set(form['decisions'])-set(by_id):
        raise ValueError('review for unknown case')
    for ident, decision in form['decisions'].items():
        by_id[ident]['review'] = deepcopy(decision)
    validate_packet(out)
    return out


def _union_length(spans):
    end, total = -1, 0
    for a, b in sorted(spans):
        total += max(0, b-max(a, end))
        end = max(end, b)
    return total


def score_output(case, refs, output, budget):
    pages = {(refs[s['id']], p['number']): p['text'] for s in case['sources'] for p in s['pages']}
    hits = output['results']
    if not isinstance(hits, list) or len(hits) > budget['limit']:
        raise ValueError('invalid hit count')
    _integer(output['returned_chars'], 'returned_chars', 0, budget['max_chars'])
    if output['returned_chars'] != sum(len(h['text']) for h in hits):
        raise ValueError('returned character count mismatch')
    seen, page_ranges = set(), defaultdict(list)
    overlaps, candidates, annotated_chars = [], [], 0
    required = [a for a in case['anchors'] if a['role'] in REQUIRED_ROLES]
    for h in hits:
        key = (h['source_ref'], h['page'])
        if any(type(h[k]) is not int for k in ('page', 'start', 'end')) or key not in pages:
            raise ValueError('foreign source/page or invalid offset type')
        if not 0 <= h['start'] < h['end'] <= len(pages[key]) or pages[key][h['start']:h['end']] != h['text'] or len(h['text']) > 600:
            raise ValueError('invalid exact source citation')
        span = (*key, h['start'], h['end'])
        if span in seen:
            raise ValueError('duplicate retrieval hit')
        seen.add(span)
        page_ranges[key].append((h['start'], h['end']))
        matched, required_match = [], False
        for a in case['anchors']:
            if h['source_ref'] == refs[a['source_id']] and h['page'] == a['page']:
                left, right = max(h['start'], a['start']), min(h['end'], a['end'])
                if left < right and a['role'] != 'distractor':
                    matched.append((left, right))
                    required_match |= a['role'] in REQUIRED_ROLES
        annotated_chars += _union_length(matched)
        overlaps.append(bool(matched))
        candidates.append(required_match)
    covered = [any(h['source_ref'] == refs[a['source_id']] and h['page'] == a['page'] and
                   h['start'] <= a['start'] and h['end'] >= a['end'] for h in hits) for a in required]
    first = next((i+1 for i, flag in enumerate(candidates) if flag), None)
    chars = output['returned_chars']
    return {'required_spans': len(required), 'covered_required_spans': sum(covered),
            'all_required_spans_covered': all(covered) if required else None,
            'conflict_complete': all(covered) if case['expectation'] == 'conflicting' else None,
            'explicit_missing_retrieved': all(covered) if case['expectation'] == 'explicit_missing' else None,
            'empty_result': not hits, 'hit_count': len(hits), 'irrelevant_hits': overlaps.count(False),
            'hits_without_required_evidence_overlap': candidates.count(False),
            'required_evidence_reciprocal_rank': 1/first if first else 0.,
            'snippet_chars': chars, 'annotated_chars': annotated_chars,
            'unique_source_chars': sum(_union_length(s) for s in page_ranges.values()),
            'response_json_chars': len(json.dumps(output, ensure_ascii=False, separators=(',', ':'))),
            'exact_offsets': True, 'semantic_support': 'unknown'}


def _summary(rows):
    valid = [r for r in rows if r['status'] == 'ok']
    scores = [r['scores'] for r in valid]
    denominator = sum(r['required_spans'] for r in rows)
    hit_count = sum(s['hit_count'] for s in scores)
    chars = sum(s['snippet_chars'] for s in scores)
    positives = sum(r['required_spans'] > 0 for r in rows)
    conflicts = [r for r in rows if r['expectation'] == 'conflicting']
    missing = [r for r in rows if r['expectation'] == 'explicit_missing']
    negatives = [r for r in rows if r['expectation'] == 'not_located']
    invalid = sum(r.get('error_stage') == 'citation_validation' for r in rows)
    times = sorted(t for r in rows for t in r['retrieval_ms'])
    return {'cases': len(rows), 'failed_cases': len(rows)-len(valid), 'required_spans': denominator,
            'citation_valid_cases': len(valid), 'invalid_citation_cases': invalid,
            'citation_valid_fraction': len(valid)/(len(valid)+invalid) if len(valid)+invalid else None,
            'not_located_cases': len(negatives),
            'empty_not_located_results': sum(r['status'] == 'ok' and r['scores']['empty_result'] for r in negatives),
            'covered_required_spans': sum(s['covered_required_spans'] for s in scores),
            'required_span_recall': sum(s['covered_required_spans'] for s in scores)/denominator if denominator else None,
            'complete_positive_cases': sum(s['all_required_spans_covered'] is True for s in scores),
            'positive_cases': positives,
            'mean_required_evidence_reciprocal_rank': sum(s['required_evidence_reciprocal_rank'] for s in scores)/positives if positives else None,
            'conflict_cases': len(conflicts), 'complete_conflict_cases': sum(r['status'] == 'ok' and r['scores']['conflict_complete'] for r in conflicts),
            'explicit_missing_cases': len(missing), 'retrieved_missing_cases': sum(r['status'] == 'ok' and r['scores']['explicit_missing_retrieved'] for r in missing),
            'irrelevant_hit_fraction': sum(s['irrelevant_hits'] for s in scores)/hit_count if hit_count else None,
            'outside_annotated_span_fraction': 1-sum(s['annotated_chars'] for s in scores)/chars if chars else None,
            'duplicate_context_fraction': 1-sum(s['unique_source_chars'] for s in scores)/chars if chars else None,
            'snippet_chars': chars, 'response_json_chars': sum(s['response_json_chars'] for s in scores),
            'retrieval_ms_total': sum(times), 'retrieval_ms_median': median(times) if times else None}


def _run_config(packet, mode, methods, repeats):
    audit, selected = gate(packet, mode)
    _integer(repeats, 'repeats', 1, 5)
    if not isinstance(methods, (tuple, list)) or not methods or any(not isinstance(m, str) for m in methods) or len(set(methods)) != len(methods):
        raise ValueError('expected unique nonempty methods')
    for method in methods:
        retrieval.validate_method(method)
    return audit, selected


def evaluate(packet, store, *, mode='development', methods=('lexical_v1', 'passage_v2'), repeats=3):
    audit, selected = _run_config(packet, mode, methods, repeats)
    rows = []
    for case in selected:
        # Opaque document identity keeps identical main/supplement text distinct.
        # No case labels, annotations, expected values or reviewer notes are stored.
        refs = {s['id']: store.put('source', {'pages': [{'number': p['number'], 'text': p['text']} for p in s['pages']],
                'evaluation_document_id': digest(s['id'])}) for s in case['sources']}
        for method in methods:
            row = {'case_id': case['id'], 'category': case['category'], 'paper_family_id': case['paper_family_id'],
                   'review_status': case['review']['status'], 'expectation': case['expectation'], 'method': method,
                   'required_spans': sum(a['role'] in REQUIRED_ROLES for a in case['anchors']),
                   'retrieval_ms': [], 'status': 'ok'}
            try:
                stage = 'retrieval'
                output = None
                for _ in range(repeats):
                    start = time.perf_counter_ns()
                    try:
                        current = retrieval.search_sources(store, list(refs.values()), case['query'], method=method, **packet['budget'])
                    finally:
                        row['retrieval_ms'].append((time.perf_counter_ns()-start)/1e6)
                    row['output'] = deepcopy(current)
                    if current.get('method') != method or current.get('query') != case['query']:
                        raise ValueError('retriever changed method or query')
                    if output is not None and output != current:
                        raise ValueError('retriever outputs are not deterministic')
                    output = deepcopy(current)
                stage = 'citation_validation'
                row.update(output=output, scores=score_output(case, refs, output, packet['budget']))
            except (ValueError, KeyError, TypeError) as exc:
                row.update(status='error', error_stage=stage, error=f'{type(exc).__name__}: {exc}', scores=None)
            rows.append(row)
    return {'status': 'reviewed_development_comparison' if mode == 'reviewed' else 'development_diagnostic_pending_domain_review',
            'protocol': PROTOCOL, 'mode': mode, 'usage': 'development_only', 'audit': audit,
            'packet_content_sha256': digest(content(packet)), 'reviewed_packet_sha256': digest(packet),
            'budget': deepcopy(packet['budget']), 'repeats': repeats, 'method_order': list(methods),
            'summary': {m: _summary([r for r in rows if r['method'] == m]) for m in methods},
            'by_category': {m: {c: _summary([r for r in rows if r['method'] == m and r['category'] == c])
                                for c in sorted({r['category'] for r in rows})} for m in methods},
            'by_paper_family': {m: {f: _summary([r for r in rows if r['method'] == m and r['paper_family_id'] == f])
                                    for f in sorted({r['paper_family_id'] for r in rows})} for m in methods},
            'by_review_status': {m: {s: _summary([r for r in rows if r['method'] == m and r['review_status'] == s])
                                     for s in sorted({r['review_status'] for r in rows})} for m in methods},
            'rows': rows, 'semantic_support': 'unknown',
            'limitations': ['All results remain development evidence; no independent holdout or Agent capability score.',
                            'Attributed review checks freshness and completeness, not reviewer identity or qualification.',
                            'Span overlap/coverage does not prove semantic or biological support.',
                            'Unannotated context is not necessarily irrelevant. Characters are not tokens or costs.',
                            'Timing covers retrieval only, excludes parsing, review, source import and LLM; cache/order/noise are uncontrolled.']}


def _read(path):
    with Path(path).open('rb') as source:
        raw = source.read(MAX_PACKET_BYTES+1)
    if len(raw) > MAX_PACKET_BYTES:
        raise ValueError('review input byte budget exceeded')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('duplicate JSON key')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def write_review_pack(packet, destination):
    audit = validate_packet(packet)
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=False)
    _write(output/'packet.json', packet)
    _write(output/'review-forms.json', {'protocol': PROTOCOL, 'content_sha256': digest(content(packet)),
           'decisions': {c['id']: dict(deepcopy(c['review']), reviewed_sha256=case_digest(packet, c)) for c in packet['cases']}})
    _write(output/'inventory.json', {'audit': audit, 'source_content_sha256': digest(content(packet)),
                                    'cases': [{'id': c['id'], 'category': c['category'], 'family': c['paper_family_id'],
                                               'split': c['split'], 'expectation': c['expectation'],
                                               'sources': [{k: v for k, v in s.items() if k != 'pages'} for s in c['sources']]}
                                              for c in packet['cases']]})
    sources = output/'sources'; sources.mkdir()
    lines = ['# T07 检索材料审核包', '', '全部为已见合成开发材料；领域审核尚未完成。',
             '字符偏移基于 packet.json 中的原页文本，不是 UTF-8 字节偏移。相关、完整覆盖与科学支持分开审核。',
             'review-forms.json 仅预填内容哈希；审核人、带时区时间、备注和检查项均需实际审核后填写。',
             '审核记录是归属声明，不认证身份或专业资质。正文/补充/更正应保持同一家庭；本包保守归为一个同源开发家庭。',
             '来源副本供阅读；packet.json 为评分文本权威。修改材料或标签后必须重新生成哈希并重新审核。', '']
    for c in packet['cases']:
        lines += [f'## {c["id"]}', '', f'类别：{c["category"]}；预期：{c["expectation"]}；状态：{c["review"]["status"]}',
                  f'内容 SHA256：`{case_digest(packet, c)}`', '查询（原文）：', '```json', json.dumps(c['query'], ensure_ascii=False), '```']
        for s in c['sources']:
            for p in s['pages']:
                name = f'{c["id"]}--{s["id"]}--p{p["number"]}.txt'
                (sources/name).write_text(p['text'], encoding='utf-8')
                lines += [f'- {s["role"]} / {s["id"]} / 页 {p["number"]}：[原页文本](sources/{name})']
        lines += ['', '标注备注与审核优先项：', '```json', json.dumps(c['annotation_note'], ensure_ascii=False), '```',
                  '候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：', '```json',
                  json.dumps(c['anchors'], ensure_ascii=False, indent=2), '```', '']
    (output/'REVIEW.md').write_text('\n'.join(lines), encoding='utf-8')
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--legacy-cases', type=Path, default=Path(__file__).parents[1]/'inputs/retrieval-development/cases.json')
    prepare.add_argument('--output', type=Path, required=True)
    for name in ('check', 'evaluate'):
        p = sub.add_parser(name)
        p.add_argument('--packet', type=Path, required=True)
        p.add_argument('--reviews', type=Path)
        if name == 'evaluate':
            p.add_argument('--mode', choices=['development', 'reviewed'], default='development')
            p.add_argument('--methods', choices=retrieval.METHODS, nargs='+', default=list(retrieval.METHODS))
            p.add_argument('--repeats', type=int, default=3)
            p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            packet = build_packet(_read(args.legacy_cases))
            audit = write_review_pack(packet, args.output)
            print(json.dumps(audit, ensure_ascii=False))
            return 0
        packet = _read(args.packet)
        validate_packet(packet)
        if args.reviews:
            packet = apply_reviews(packet, _read(args.reviews))
        if args.action == 'check':
            print(json.dumps(validate_packet(packet), ensure_ascii=False))
            return 0
        _run_config(packet, args.mode, args.methods, args.repeats)  # preflight before output/store
        args.output.mkdir(parents=True, exist_ok=False)
        result = evaluate(packet, ArtifactStore(args.output/'store'), mode=args.mode,
                          methods=args.methods, repeats=args.repeats)
        _write(args.output/'packet.json', packet)
        _write(args.output/'results.json', result)
        _write(args.output/'implementation.json', {'protocol': PROTOCOL,
               'retrieval_sha256': hashlib.sha256(Path(retrieval.__file__).read_bytes()).hexdigest(),
               'evaluation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        print(json.dumps({'status': result['status'], 'summary': result['summary']}, ensure_ascii=False))
        return 1 if any(r['status'] == 'error' for r in result['rows']) else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
