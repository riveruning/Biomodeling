"""Complete guidance for the bounded explicit ODE decision profile.

Structure is provider guidance; modeling_plan remains the semantic authority.
"""
from .research_schema import choices, record, text


def decision_plan_schema(source_refs):
    from .modeling_plan import ORIGINS, RELATIONS
    identifier=dict(type='string',pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    context=dict(type='object',maxProperties=16,propertyNames=text(100),additionalProperties=text(300))
    def entries(item,maximum,minimum=0):
        return dict(type='array',items=item,minItems=minimum,maxItems=maximum)
    ids={**entries(identifier,64),'uniqueItems':True}
    value=dict(type=['number','null'],description='null means an explicit unresolved value, never zero.')
    state=record(dict(id=identifier,initial=value,unit=text(),quantity_kind=dict(enum=['variable','amount','concentration']),
                      nonnegative=dict(type='boolean')),('id','initial'))
    parameter=record(dict(id=identifier,value=value,unit=text(),origin=text()),('id','value'))
    draft=record(dict(id=identifier,states=entries(state,16,1),parameters=entries(parameter,32),
        compartment=dict(const=dict(id='cell',size=1.0,unit='dimensionless')),
        reactions=dict(const=[]),rate_rules=entries(record(dict(state=identifier,expression=text()),('state','expression')),16),
        derived=dict(const=[]),time_unit=text(),element_map=dict(const={}),hypothesis_refs=dict(const=[]),parent_ref=dict(type='null')),
        ('id','states'))
    anchor=record(dict(id=identifier,source_ref=choices(source_refs),page=dict(type='integer',minimum=1,maximum=100000),
        start=dict(type='integer',minimum=0),end=dict(type='integer',minimum=1),quote=text(8000),
        interpretation=text(1000),conditions=context),('id','source_ref','page','quote','interpretation','conditions'))
    anchor['dependentRequired']=dict(start=['end'],end=['start'])
    anchor['description']='Omit both offsets for a unique literal quote. Supplied offsets must match exactly; repeated quotes require explicit occurrence selection.'
    mechanism=record(dict(id=identifier,subject=identifier,relation=dict(enum=sorted(RELATIONS)),object=identifier,
        evidence_ids={**ids,'minItems':1},conditions=context,rationale=text(1000)),
        ('id','subject','relation','object','evidence_ids','conditions','rationale'))
    decision=record(dict(target=text(),origin=dict(enum=sorted(ORIGINS)),evidence_ids=ids,mechanism_ids=ids,
        rationale=text(1000),alternatives=entries(text(1000),8)),
        ('target','origin','evidence_ids','mechanism_ids','rationale','alternatives'))
    decision['description']='Exactly one decision per draft target: <state>/initial, <parameter>/value, rule:<state>/expression. reported requires exact source evidence; assumed is exploration only; unresolved blocks execution.'
    candidate=record(dict(id=identifier,label=text(300),variable_map=dict(type='object',propertyNames=identifier,additionalProperties=identifier),
        draft=draft,decisions=entries(decision,64,1)),('id','label','variable_map','draft','decisions'))
    candidate['description']='variable_map maps entity IDs one-to-one onto ALL draft states. Mechanisms are qualitative and cannot select a rate law.'
    properties=dict(schema_version=dict(const=1),id=identifier,model_type=dict(const='ode'),mode=dict(enum=['reproduction','exploration']),
        context=context,entities=entries(record(dict(id=identifier,label=text(300)),('id','label')),32,1),
        evidence=entries(anchor,64),mechanisms=entries(mechanism,32),candidates=entries(candidate,8,1),
        selected_candidate=dict(anyOf=[identifier,dict(type='null')]))
    schema=record(properties,properties)
    schema['description']='All context/conditions are explicit string mappings ({} if none), not lists. Evidence IDs belong to this plan, not frame/need IDs. Selected candidate must exist. Every evidence/mechanism condition must agree with context. Reproduction cannot adopt assumptions. Native validators check units, source scope, expressions, bindings and completeness.'
    return schema
