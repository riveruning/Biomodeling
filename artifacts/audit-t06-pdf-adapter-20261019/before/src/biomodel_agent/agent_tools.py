"""Typed proposal tool: evidence is created by tools, never by model assertions."""
from dataclasses import replace
import math
import re
from .domain.contracts import FrozenMap, ModelDraft
from .domain.serialization import from_dict, to_dict
from . import bindings, evidence, modeling
from .source_review import review_sources

NUMBER = re.compile(r'(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?!\w|\.\d)')
MAX_CITATION_CANDIDATES = 5


def _quote_locations(page, quote):
    """Bounded overlapping matches; diagnostics never choose a citation."""
    starts = []
    cursor = 0
    while len(starts) <= MAX_CITATION_CANDIDATES:
        start = page.find(quote, cursor)
        if start < 0:
            break
        starts.append(start)
        cursor = start + 1
    exact = len(starts) <= MAX_CITATION_CANDIDATES
    candidates = []
    for start in starts[:MAX_CITATION_CANDIDATES]:
        left, right = max(0, start-80), min(len(page), start+160)
        candidates.append({'start': start, 'end': start+len(quote),
                           'context_start': left, 'context_end': right,
                           'context': page[left:right]})
    return len(starts), exact, candidates


def submit_draft(store,args,source_refs,mode='reproduction',allowed_mechanisms=()):
    result = dict(state='invalid',artifact=None,model_ref=None,draft_ref=None,hypothesis_ref=None,checks=[],problems=[],source_review=None,source_review_ref=None,citation_diagnostics=[],binding_diagnostics=[])
    try:
        if mode not in {'reproduction','hypothesis'}:
            raise ValueError('unknown research mode')
        if not isinstance(args,dict) or set(args)-{'draft','evidence','mechanism'}:
            raise ValueError('unknown proposal arguments')
        if args['draft'].get('element_map') or args['draft'].get('hypothesis_refs') or args['draft'].get('parent_ref'):
            raise ValueError('lineage and evidence refs are assigned by tools')
        draft = from_dict(ModelDraft,args['draft'])
        required = bindings.draft_targets(draft)
        names = bindings.draft_symbols(draft)
        review = review_sources(store, source_refs, required)
        review_ref = store.put('source_review', review, refs=tuple(source_refs))
        result.update(source_review=review, source_review_ref=review_ref)
        if mode == 'reproduction' and review['issues']:
            result.update(state='awaiting_input',
                          draft_ref=store.put('model_draft',to_dict(draft),refs=(review_ref,)),
                          problems=[issue['question'] for issue in review['issues']])
            return result
        optional = {'time_unit',draft.compartment.id+'/size',draft.compartment.id+'/unit'}
        optional.update(s.id+'/unit' for s in draft.states)
        optional.update(p.id+'/unit' for p in draft.parameters)
        optional.update(d.id+'/unit' for d in draft.derived)
        links,quotes = {},{}
        items = args.get('evidence',[])
        if not isinstance(items,list):
            raise ValueError('evidence must be a list')
        normalized = []
        for item in items:
            if not isinstance(item,dict) or set(item)-{'target','source_ref','page','start','end','quote','claim_type','symbol_map'}:
                raise ValueError('unknown citation arguments')
            if item['source_ref'] not in source_refs:
                raise ValueError('citation source outside authorized task')
            if item['target'] not in set(required)|optional:
                raise ValueError(f'Unknown citation target {item["target"]!r}; allowed targets: {sorted(set(required)|optional)}')
            item = dict(item)
            if type(item['page']) is not int:
                raise ValueError('citation offsets must be integers')
            if 'quote' in item:
                if 'start' in item or 'end' in item or not isinstance(item['quote'],str) or not item['quote']:
                    raise ValueError('provide either a literal quote or explicit offsets')
                if len(item['quote']) > 8000:
                    raise ValueError('citation must be nonempty and at most 8000 characters')
                source = store.get(item['source_ref'])['payload']
                page = next((p['text'] for p in source['pages'] if p['number']==item['page']),None)
                if page is None:
                    raise ValueError('citation page not found in the authorized source')
                count, exact, candidates = _quote_locations(page, item['quote'])
                if count != 1:
                    result['problems'].append(item['target']+': quote must occur exactly once on the source page; use more context or explicit offsets')
                    result['citation_diagnostics'].append({
                        'target': item['target'], 'source_ref': item['source_ref'],
                        'page': item['page'],
                        'kind': 'quote_not_found' if not count else 'quote_not_unique',
                        'occurrence_count': count, 'count_is_exact': exact,
                        'candidates': candidates,
                        'instruction': 'Choose the source occurrence that supports this target, then submit a new draft with explicit start/end or a unique literal quote. No claim was created for this proposal.'})
                    continue
                item['start'] = candidates[0]['start']
                item['end'] = item['start']+len(item['quote'])
            for key in ('page','start','end'):
                if type(item[key]) is not int:
                    raise ValueError('citation offsets must be integers')
            if not 0 < item['end']-item['start'] <= 8000:
                raise ValueError('citation must be nonempty and at most 8000 characters')
            source_payload = store.get(item['source_ref'])['payload']
            page = next((p['text'] for p in source_payload['pages'] if p['number']==item['page']),None)
            if page is None or not 0 <= item['start'] < item['end'] <= len(page):
                raise ValueError('citation offsets outside the authorized source page')
            claim_type = ('human_convention' if source_payload.get('clarification_ref')
                          else item.get('claim_type','paper_assumption'))
            if claim_type not in evidence.CLAIM_TYPES:
                raise ValueError(f'invalid claim_type {claim_type!r}; allowed: {list(evidence.CLAIM_TYPES)}')
            normalized.append((item, claim_type))
        if result['problems']:
            return result
        for item, _ in normalized:
            if item['target'] in required:
                text = evidence._source_page_text(store, item['source_ref'], item['page'])
                result['binding_diagnostics'].append(bindings.check(
                    text, item['source_ref'], item['page'], item['start'], item['end'],
                    item['target'], required[item['target']], names, item.get('symbol_map')))
        if mode == 'reproduction':
            bad = [d for d in result['binding_diagnostics'] if d['status'] != 'matched']
            missing_citations = [k+': citation missing' for k in required if not any(i['target'] == k for i, _ in normalized)]
            if bad or missing_citations:
                state = ('awaiting_input' if any(v is None for v in required.values()) else
                         'invalid' if missing_citations or any(d['status'] == 'mismatch' for d in bad) else 'awaiting_input')
                result.update(state=state, problems=missing_citations+[d['target']+': binding '+d['status']+': '+d['reason'] for d in bad],
                              draft_ref=store.put('model_draft', to_dict(draft), refs=(review_ref,)))
                return result
        for item,claim_type in normalized:
            ref = evidence.add_claim(store,item['source_ref'],item['page'],item['start'],item['end'],item['target'],claim_type,
                                     expected=required.get(item['target']), allowed_symbols=names,
                                     symbol_map=item.get('symbol_map'))
            links.setdefault(item['target'],[]).append(ref)
            quotes.setdefault(item['target'],[]).append(store.get(ref)['payload']['quote'])
        hypothesis = None
        if mode == 'hypothesis':
            if args.get('mechanism') not in allowed_mechanisms:
                result['state'] = 'failed'
                raise ValueError('proposed mechanism outside authorized space')
            hypothesis = store.put('hypothesis',{'claim_type':'system_conjecture','mechanism':args['mechanism'],'assumptions':['Proposed equations and nominal parameters; not established biological facts'],'source_refs':list(source_refs)},refs=tuple(source_refs))
            draft = replace(draft,hypothesis_refs=(hypothesis,))
        draft = replace(draft,element_map=FrozenMap(links))
        refs = [r for group in links.values() for r in group]+([hypothesis] if hypothesis else [])+[review_ref]
        result.update(draft_ref=store.put('model_draft',to_dict(draft),refs=refs),hypothesis_ref=hypothesis)
        static = modeling.validate_draft(draft)
        result['checks'] = [to_dict(c) for c in static.checks]
        missing = [k for k,v in required.items() if v is None]
        citation_problems = []
        if mode == 'reproduction':
            for target,value in required.items():
                q = quotes.get(target,[])
                if not q:
                    citation_problems.append(target+': citation missing')
        if mode == 'reproduction':
            for resolution in review['resolutions']:
                target, fact = resolution['target'], resolution['evidence']
                if target not in required:
                    continue
                source_payload = store.get(fact['source_ref'])['payload']
                if not source_payload.get('clarification_ref'):
                    continue
                linked_sources = {store.get(r)['payload']['source_ref'] for r in links.get(target, [])}
                if fact['source_ref'] not in linked_sources:
                    citation_problems.append(target+': cite the researcher clarification used to resolve this target')
                if 'value' in fact and isinstance(required[target], (int, float)):
                    if not math.isclose(required[target], fact['value'], rel_tol=1e-12, abs_tol=0.):
                        missing.append(target+': draft value differs from the researcher clarification; provide an explicit matching declaration')
        if missing:
            result.update(state='awaiting_input',problems=missing+citation_problems)
            return result
        if citation_problems:
            # The source is available to the model: repair its citation within
            # the existing budget instead of asking the researcher to repeat it.
            result.update(state='invalid',problems=citation_problems)
            return result
        if not static.can_execute:
            result['problems'] = [c.message for c in static.checks if c.status == 'fail']
            return result
        artifact = modeling.build_model(draft)
        result.update(state='ready',artifact=artifact,model_ref=store.put('model',to_dict(artifact),refs=(result['draft_ref'],)))
        result['checks'].append(dict(code='semantic_support',status='unknown',severity='warning',message='Bounded target binding does not establish semantic support, units, or scientific truth.'))
    except (ValueError,KeyError,TypeError) as exc:
        result['problems'].append(str(exc))
    return result
