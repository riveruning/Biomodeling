"""Deterministic construction from complete literal declaration profiles.

No prose interpretation, rate-law selection, unit inference or missing-value
repair. Existing source inspectors and native gates recheck every proposal.
"""
import math
import re

from . import bindings,evidence,model_contracts as c


def lines(store,source_ref):
    source=store.get(source_ref)
    if source['kind']!='source' or source['payload'].get('parser')!='utf8':raise ValueError('Literal construction requires a complete UTF-8 snapshot')
    payload=source['payload']
    if payload.get('research_input_role') in ('scenario','researcher_reply'):raise ValueError('Attributed scenarios/replies cannot become reported source models')
    if len(payload['pages'])!=1:raise ValueError('Literal construction needs a complete single text snapshot')
    text=evidence._source_page_text(store,source_ref,1)
    if len(text)>64000 or store.get_blob(payload['blob_ref']).decode('utf-8')!=text:raise ValueError('Source exceeds scope or differs from original bytes')
    result=[];cursor=0
    for index,raw in enumerate(text.splitlines(keepends=True)):
        quote=raw.rstrip('\r\n');stripped=quote.strip()
        if stripped:
            offset=cursor+len(quote)-len(quote.lstrip())
            result.append((stripped,dict(id=f'L{source_ref[:8]}_{index}',kind='source',source_ref=source_ref,page=1,
                quote=stripped,start=offset,end=offset+len(stripped))))
        cursor+=len(raw)
    return result


def _comment(line):return line in ('Synthetic development control; not a paper.','Synthetic development declarations; not a literature reproduction.')


def _native(store,source_ref,kind,records):
    from . import boolean,boolean_proposals as bp,stochastic,stochastic_proposals as sp
    module=bp if kind=='boolean' else sp
    names={match[1] for line,_ in records if (match:=module.INITIAL.fullmatch(line))}
    if not names:raise ValueError('Explicit initial declarations required; no states are inferred')
    values={};anchors=[];mapping={};raw_rules={}
    for line,anchor in records:
        parsed=module.parse_declaration(line,names)
        if parsed is None:
            if _comment(line):continue
            raise ValueError('Unsupported or qualified source line: '+anchor['id'])
        target,value=parsed
        if target in values:raise ValueError('Duplicate/conflicting source target: '+target)
        values[target]=value;anchors.append(anchor);mapping[target]=[anchor['id']]
        if kind=='boolean' and (match:=bp.RULE.fullmatch(line)):raw_rules[match[1]]=match[2].strip()
    model=dict(schema_version=1,model_type=kind,id='literal_'+kind+'_'+source_ref[:12])
    if kind=='boolean':
        if set(raw_rules)!=names:raise ValueError('Every Boolean node needs an explicit rule')
        for key in ('time_unit','update','clamps'):
            if key not in values:raise ValueError('Missing explicit source target: '+key)
        model.update(units=dict(time=values['time_unit']),nodes=[dict(id=name,initial=values[name+'/initial'],rule=raw_rules[name]) for name in sorted(names)],
            update=values['update'],clamps={})
        boolean.validate(model);check=bp.inspect(store,model,anchors,mapping,[source_ref]);units={}
    else:
        for key in ('time_unit','state_unit','rate_convention','mixing','kinetics'):
            if key not in values:raise ValueError('Missing explicit source target: '+key)
        ids=sorted(key.removesuffix('/reaction') for key in values if key.endswith('/reaction'))
        reactions=[];units={}
        for name in ids:
            if name+'/rate' not in values or name+'/rate_unit' not in values:raise ValueError('Every reaction needs an explicit stochastic rate and unit')
            reactions.append(dict(id=name,**values[name+'/reaction'],rate=float(values[name+'/rate'])))
            units[name]=values[name+'/rate_unit']
        model.update(units=dict(time=values['time_unit'],state=values['state_unit']),rate_convention=values['rate_convention'],
            species=[dict(id=name,initial=values[name+'/initial']) for name in sorted(names)],reactions=reactions)
        stochastic.validate(model);check=sp.inspect(store,model,units,anchors,mapping,[source_ref])
    if check['state']!='source_bound':
        from .research_projects import SourceBindingError
        raise SourceBindingError('Literal candidate did not pass the existing whole-source binding gate',check)
    return dict(profile='literal_'+kind+'_construction_v1',evidence=anchors,native_draft=model,
        source_evidence_map=mapping,stochastic_rate_units=units)


def _ode(store,source_ref,records):
    from .domain.contracts import ModelDraft,State,Parameter,RateRule
    from .domain.serialization import to_dict
    from . import modeling_plan
    initial={};parameters={};rules={};anchors=[];decisions=[];clock=None
    patterns=[('initial',re.compile(rf'Initial ({bindings.IDENT})\s*=\s*({bindings.NUMBER})\.?')),
        ('value',re.compile(rf'Parameter ({bindings.IDENT})\s*=\s*({bindings.NUMBER})\.?')),
        ('rule',re.compile(rf'Equation d({bindings.IDENT})/dt\s*=\s*(.+?)\.?'))]
    for line,anchor in records:
        if line in ('Time is measured in seconds.','Time unit = second.','Time unit = second'):
            if clock is not None:raise ValueError('Duplicate clock declaration')
            clock=anchor;continue
        found=None
        for kind,pattern in patterns:
            if match:=pattern.fullmatch(line):found=(kind,match);break
        if found is None:
            if _comment(line):continue
            raise ValueError('Outside the complete literal ODE profile: '+anchor['id'])
        kind,match=found;name=match[1]
        group=initial if kind=='initial' else parameters if kind=='value' else rules
        if name in group:raise ValueError('Duplicate/conflicting source declaration: '+name+'/'+kind)
        value=match[2] if kind=='rule' else float(match[2])
        if kind!='rule' and not math.isfinite(value):raise ValueError('Nonfinite source number')
        group[name]=value;anchors.append(anchor)
        target='rule:'+name+'/expression' if kind=='rule' else name+'/'+kind
        decisions.append(dict(target=target,origin='reported',evidence_ids=[anchor['id']],mechanism_ids=[],
            rationale='Copy the complete literal source declaration without choosing a new law.',alternatives=[]))
    if clock is None or not rules or set(initial)!=set(rules):raise ValueError('Complete second-based clock, initial values and equations for every state are required')
    draft=ModelDraft('literal_ode_'+source_ref[:12],tuple(State(name,initial[name]) for name in sorted(initial)),
        tuple(Parameter(name,parameters[name]) for name in sorted(parameters)),
        rate_rules=tuple(RateRule(name,rules[name]) for name in sorted(rules)),time_unit='second')
    from .modeling import validate_draft
    static=validate_draft(draft)
    if not static.can_execute:
        raise ValueError('Literal declarations do not form a complete executable ODE: '+', '.join(check.code for check in static.checks if check.status=='fail'))
    plan=dict(schema_version=1,id='literal_plan_'+source_ref[:12],model_type='ode',mode='reproduction',context={},
        entities=[dict(id=name,label=name) for name in sorted(initial)],
        evidence=[dict(**{k:v for k,v in a.items() if k!='kind'},interpretation='Exact explicit declaration; biological interpretation unverified.',conditions={}) for a in anchors],
        mechanisms=[],candidates=[dict(id='reported',label='Complete reported equations',variable_map={name:name for name in initial},draft=to_dict(draft),decisions=decisions)],
        selected_candidate='reported')
    modeling_plan.validate(store,plan,[source_ref])
    return dict(profile='literal_ode_construction_v1',evidence=anchors,decision_plan=plan,clock_evidence=clock)


def build(store,source_ref,model_type):
    if model_type not in ('ode','boolean','stochastic'):raise ValueError('Literal construction supports only ODE, Boolean and SSA declarations')
    records=lines(store,source_ref)
    return _ode(store,source_ref,records) if model_type=='ode' else _native(store,source_ref,model_type,records)
