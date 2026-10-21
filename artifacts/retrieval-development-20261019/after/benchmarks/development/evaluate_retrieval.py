"""Auditable synthetic retrieval comparison; labels never enter the retriever."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from biomodel_agent.retrieval import search_sources
from biomodel_agent.storage import ArtifactStore


def validate_cases(payload):
    if payload.get('schema_version') != 1 or not payload.get('cases'):
        raise ValueError('expected nonempty retrieval fixture v1')
    seen = set()
    for case in payload['cases']:
        if case['id'] in seen:
            raise ValueError('duplicate case id')
        seen.add(case['id'])
        pages = {}
        for source in case['sources']:
            for page in source['pages']:
                key = (source['id'], page['number'])
                if key in pages:
                    raise ValueError('duplicate source/page')
                pages[key] = page['text']
        for anchor in case['anchors']:
            text = pages[(anchor['source_id'], anchor['page'])]
            start, end = anchor['start'], anchor['end']
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or text[start:end] != anchor['quote']):
                raise ValueError('invalid anchor offsets or quote')


def score_output(case, refs, output, *, limit, max_chars):
    """Check exact spans, then score declared anchors without interpreting science."""
    pages = {(refs[s['id']], p['number']): p['text']
             for s in case['sources'] for p in s['pages']}
    hits = output['results']
    if len(hits) > limit or output['returned_chars'] != sum(len(h['text']) for h in hits):
        raise ValueError('invalid result count or returned_chars')
    if output['returned_chars'] > max_chars:
        raise ValueError('output text budget exceeded')
    for hit in hits:
        page = pages[(hit['source_ref'], hit['page'])]  # foreign refs are invalid
        if (type(hit['start']) is not int or type(hit['end']) is not int
                or not 0 <= hit['start'] < hit['end'] <= len(page)
                or page[hit['start']:hit['end']] != hit['text']
                or len(hit['text']) > 600):
            raise ValueError('invalid original-text citation span')
    covered = []
    relevant = []
    anchor_chars = 0
    for hit in hits:
        overlaps = []
        for a in case['anchors']:
            if hit['source_ref'] == refs[a['source_id']] and hit['page'] == a['page']:
                left, right = max(hit['start'], a['start']), min(hit['end'], a['end'])
                if left < right:
                    overlaps.append((left, right))
        relevant.append(bool(overlaps))
        # Count union, so overlapping annotations cannot inflate precision.
        cursor = -1
        for left, right in sorted(overlaps):
            anchor_chars += max(0, right - max(cursor, left))
            cursor = max(cursor, right)
    for a in case['anchors']:
        covered.append(any(h['source_ref'] == refs[a['source_id']] and h['page'] == a['page']
                           and h['start'] <= a['start'] and h['end'] >= a['end'] for h in hits))
    first = next((i + 1 for i, value in enumerate(relevant) if value), None)
    return {'anchor_count': len(covered), 'covered_anchors': sum(covered),
            'all_anchors_covered': all(covered) if covered else not hits,
            'hit_count': len(hits), 'irrelevant_hits': relevant.count(False),
            'reciprocal_rank': 1 / first if first else 0.,
            'snippet_chars': output['returned_chars'],
            'response_json_chars': len(json.dumps(output, ensure_ascii=False, separators=(',', ':'))),
            'annotated_chars': anchor_chars, 'exact_offsets': True}


def evaluate(payload, store, methods, *, limit=3, max_chars=1200):
    validate_cases(payload)
    rows = []
    for case in payload['cases']:
        refs = {s['id']: store.put('source', {'pages': s['pages']}) for s in case['sources']}
        for method in methods:
            # Only authorized sources/query/budgets are passed, never anchors.
            kwargs = {} if method == 'lexical_v1' else {'method': method}
            output = search_sources(store, list(refs.values()), case['query'],
                                    limit=limit, max_chars=max_chars, **kwargs)
            if output.get('method') != method:
                raise ValueError('retriever returned a different method')
            scores = score_output(case, refs, output, limit=limit, max_chars=max_chars)
            rows.append({'case_id': case['id'], 'category': case['category'],
                         'method': method, 'scores': scores, 'output': output})
    summary = {}
    for method in methods:
        selected = [r['scores'] for r in rows if r['method'] == method]
        positives = [s for s in selected if s['anchor_count']]
        anchors = sum(s['anchor_count'] for s in selected)
        hits = sum(s['hit_count'] for s in selected)
        chars = sum(s['snippet_chars'] for s in selected)
        summary[method] = {
            'cases': len(selected), 'anchors': anchors,
            'covered_anchors': sum(s['covered_anchors'] for s in selected),
            'anchor_recall': sum(s['covered_anchors'] for s in selected) / anchors if anchors else None,
            'complete_cases': sum(s['all_anchors_covered'] for s in selected),
            'mean_reciprocal_rank_positive_cases': sum(s['reciprocal_rank'] for s in positives) / len(positives) if positives else None,
            'irrelevant_hit_fraction': sum(s['irrelevant_hits'] for s in selected) / hits if hits else 0.,
            'snippet_chars': chars,
            'response_json_chars': sum(s['response_json_chars'] for s in selected),
            'outside_annotated_span_fraction': 1 - sum(s['annotated_chars'] for s in selected) / chars if chars else 0.,
        }
    return {'status': 'synthetic_development_pending_independent_review',
            'protocol': {'limit': limit, 'max_chars': max_chars,
                         'relevance': 'overlap with declared anchor',
                         'coverage': 'entire anchor contained in one returned snippet',
                         'limitations': ['Annotations are synthetic and not independently reviewed.',
                                        'Non-anchor context is not necessarily irrelevant.',
                                        'Retrieval relevance does not prove semantic support.',
                                        'Character counts are not tokens, fees or Agent performance.']},
            'summary': summary, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).parents[1] / 'inputs/retrieval-development/cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--methods', nargs='+', choices=['lexical_v1', 'passage_v2'], default=['lexical_v1', 'passage_v2'])
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    payload = json.loads(raw)
    validate_cases(payload)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'cases.json').write_bytes(raw)
    result = evaluate(payload, ArtifactStore(args.output / 'store'), args.methods)
    result['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
    result['retrieval_source_sha256'] = hashlib.sha256(
        (Path(__file__).parents[2] / 'src/biomodel_agent/retrieval.py').read_bytes()).hexdigest()
    (args.output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
