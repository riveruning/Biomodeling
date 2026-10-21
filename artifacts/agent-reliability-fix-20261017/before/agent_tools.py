"""Typed proposal tool: evidence is created by tools, never by model assertions."""
from dataclasses import replace
import math
import re
from .domain.contracts import FrozenMap, ModelDraft
from .domain.serialization import from_dict, to_dict
from . import evidence, modeling

NUMBER = re.compile(r'(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?!\w|\.\d)')


def submit_draft(store,args,source_refs,mode='reproduction',allowed_mechanisms=()):
    result = dict(state='invalid',artifact=None,model_ref=None,draft_ref=None,hypothesis_ref=None,checks=[],problems=[])
    try:
        if mode not in {'reproduction','hypothesis'}:
            raise ValueError('unknown research mode')
        if not isinstance(args,dict) or set(args)-{'draft','evidence','mechanism'}:
            raise ValueError('unknown proposal arguments')
        if args['draft'].get('element_map') or args['draft'].get('hypothesis_refs') or args['draft'].get('parent_ref'):
            raise ValueError('lineage and evidence refs are assigned by tools')
        draft = from_dict(ModelDraft,args['draft'])
        required = {p.id+'/value':p.value for p in draft.parameters}
        required.update({s.id+'/initial':s.initial for s in draft.states})
        required.update({'rule:'+r.state+'/expression':'expression' for r in draft.rate_rules})
        required.update({'reaction:'+r.id+'/rate':'expression' for r in draft.reactions})
        required.update({'derived:'+d.id+'/expression':'expression' for d in draft.derived})
        optional = {'time_unit',draft.compartment.id+'/size',draft.compartment.id+'/unit'}
        optional.update(s.id+'/unit' for s in draft.states)
        optional.update(p.id+'/unit' for p in draft.parameters)
        optional.update(d.id+'/unit' for d in draft.derived)
        links,quotes = {},{}
        items = args.get('evidence',[])
        if not isinstance(items,list):
            raise ValueError('evidence must be a list')
        for item in items:
            if not isinstance(item,dict) or set(item)-{'target','source_ref','page','start','end','quote','claim_type'}:
                raise ValueError('unknown citation arguments')
            if item['source_ref'] not in source_refs:
                raise ValueError('citation source outside authorized task')
            if item['target'] not in set(required)|optional:
                raise ValueError(f'Unknown citation target {item["target"]!r}; allowed targets: {sorted(set(required)|optional)}')
            item = dict(item)
            if 'quote' in item:
                if 'start' in item or 'end' in item or not isinstance(item['quote'],str) or not item['quote']:
                    raise ValueError('provide either a literal quote or explicit offsets')
                source = store.get(item['source_ref'])['payload']
                page = next((p['text'] for p in source['pages'] if p['number']==item['page']),None)
                if page is None or page.count(item['quote']) != 1:
                    raise ValueError('quote must occur exactly once on the source page; use more context')
                item['start'] = page.index(item['quote'])
                item['end'] = item['start']+len(item['quote'])
            for key in ('page','start','end'):
                if type(item[key]) is not int:
                    raise ValueError('citation offsets must be integers')
            if not 0 < item['end']-item['start'] <= 8000:
                raise ValueError('citation must be nonempty and at most 8000 characters')
            ref = evidence.add_claim(store,item['source_ref'],item['page'],item['start'],item['end'],item['target'],item.get('claim_type','paper_assumption'))
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
        refs = [r for group in links.values() for r in group]+([hypothesis] if hypothesis else [])
        result.update(draft_ref=store.put('model_draft',to_dict(draft),refs=refs),hypothesis_ref=hypothesis)
        static = modeling.validate_draft(draft)
        result['checks'] = [to_dict(c) for c in static.checks]
        missing = [k for k,v in required.items() if v is None]
        if mode == 'reproduction':
            for target,value in required.items():
                q = quotes.get(target,[])
                if not q:
                    missing.append(target+': citation missing')
                elif isinstance(value,(int,float)) and not any(any(math.isclose(float(m.group()),value,rel_tol=1e-12,abs_tol=0.) for m in NUMBER.finditer(text)) for text in q):
                    missing.append(target+': numeric value not present in cited text')
        if missing:
            result.update(state='awaiting_input',problems=missing)
            return result
        if not static.can_execute:
            result['problems'] = [c.message for c in static.checks if c.status == 'fail']
            return result
        artifact = modeling.build_model(draft)
        result.update(state='ready',artifact=artifact,model_ref=store.put('model',to_dict(artifact),refs=(result['draft_ref'],)))
        result['checks'].append(dict(code='semantic_support',status='unknown',severity='warning',message='Exact quote existence and numeric match do not prove semantic support.'))
    except (ValueError,KeyError,TypeError) as exc:
        result['problems'].append(str(exc))
    return result
