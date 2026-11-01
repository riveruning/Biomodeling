"""Stage-specific action guidance sharing bounds with the research validators.

These JSON Schemas guide the provider. Existing deterministic validators remain
the authority for evidence bindings, native mathematics, budgets and execution.
"""
from .capabilities import TYPES, TYPED_TYPES

MODES=('strict_reproduction','reconstruction','application_modeling','mechanism_exploration')
EVIDENCE_KINDS=('literature','data','mathematics','validation')
CRITERION_KINDS=('native_checks','reference_comparison','source_coverage','alignment','external_review')
LIMITS=dict(modeling_targets=8,unresolved=12,evidence_needs=16,queries=4,assumptions=16,criteria=8,evidence=32)


def text(maximum=4000):return dict(type='string',minLength=1,maxLength=maximum)
def strings(maximum,limit=2000):return dict(type='array',items=text(limit),maxItems=maximum)
def record(properties,required):
    return dict(type='object',properties=properties,required=list(required),additionalProperties=False)
def action(name,args):
    return record(dict(action=dict(const=name),args=args,reason=dict(type='string')),('action','args'))


def frame_schema(source_refs):
    anchor=dict(id=text(48),kind=dict(const='source'),source_ref=dict(enum=list(source_refs)),
        page=dict(type='integer',minimum=1),quote=text(),start=dict(type='integer',minimum=0),end=dict(type='integer',minimum=1))
    source=record(anchor,('id','kind','source_ref','page','quote'))
    source['description']='Prefer unique literal quote only. Omit start/end; tools compute offsets. Never estimate character positions.'
    source['dependentRequired']=dict(start=['end'],end=['start'])
    goal=record({k:v for k,v in anchor.items() if k not in ('source_ref','page')},('id','kind','quote'))
    goal['properties']['kind']=dict(const='goal')
    goal['dependentRequired']=dict(start=['end'],end=['start'])
    goal['description']='A literal scenario quote is attributed input, never empirical evidence.'
    properties=dict(objective=text(),mode=dict(enum=list(MODES)),granularity=text(),
        modeling_targets={**strings(LIMITS['modeling_targets']),'minItems':1},rationale=text(),
        evidence_needs=dict(type='array',maxItems=LIMITS['evidence_needs'],items=record(dict(
            id=text(48),question=text(),kind=dict(enum=list(EVIDENCE_KINDS)),queries=strings(LIMITS['queries'],256)),
            ('id','question','kind','queries'))),
        assumptions=dict(type='array',maxItems=LIMITS['assumptions'],items=record(dict(id=text(48),statement=text(),
            rationale=text(),evidence_ids=strings(32,48),decision=dict(enum=['adopt','defer','reject'])),
            ('id','statement','rationale','evidence_ids','decision'))),
        criteria=dict(type='array',minItems=1,maxItems=LIMITS['criteria'],items=record(dict(id=text(48),
            kind=dict(enum=list(CRITERION_KINDS)),description=text(),required=dict(type='boolean'),
            question_ref=text(64)),('id','kind','description','required'))),
        evidence=dict(type='array',maxItems=LIMITS['evidence'],items=dict(oneOf=[source,goal])),
        unresolved=strings(LIMITS['unresolved']))
    schema=record(properties,properties)
    schema['description']='Use source anchor IDs from this same frame in evidence_ids; need IDs and question refs are different namespaces. Identifiers follow the supplied native identifier grammar. At least one criterion is required; question_ref is only for alignment.'
    return schema


def schemas(view):
    result={}
    if not view['iteration_refs']:
        result['revise_frame']=action('revise_frame',record(dict(reason=text(),
            frame=frame_schema(view['source_refs'])),('reason','frame')))
    if view['frame_ref']:
        execution=dict(type='object',description='Native types use execution.settings. Boolean uses settings.steps and settings.seed, never execution.sweeps. ODE uses execution.experiment with explicit times and time_unit.')
        config=record(dict(model_type=dict(enum=list(TYPES)),source_refs=dict(type='array',items=dict(enum=list(view['source_refs']))),
            model_ref=text(64),execution=execution,max_calls=dict(type='integer',minimum=1,maximum=20)),('model_type',))
        config['allOf']=[dict(
            **{'if':dict(properties=dict(model_type=dict(enum=list(TYPES[1:]))),required=['model_type'])},
            then=dict(properties=dict(execution=record(dict(settings=dict(type='object')),()))))]
        native=dict(oneOf=[dict(type='object',required=['schema_version','model_type','id','units'],
            properties=dict(schema_version=dict(const=1),model_type=dict(enum=list(TYPED_TYPES)))),
            dict(type='object',required=['schema_version','kind','id'],properties=dict(schema_version=dict(const=1),kind=dict(const='fba')))],
            description='Copy the exact native schema structure from the supplied examples; all backend fields and checks still apply. Do not add a top-level initial or omit model_type.')
        properties=dict(reason=text(),config=config,native_draft=native,hypothesis_ids=strings(16,48),
            parent_iteration_ref=dict(enum=view['iteration_refs']),reference_ref=dict(enum=view['reference_refs']),
            decision_plan=dict(type='object'),native_agent=dict(type='boolean'),
            source_evidence_map=dict(type='object',description='Values are lists of 1..4 source anchor IDs from this frame. Boolean keys are <node>/initial, <node>/rule, update, time_unit, clamps. SSA keys are <species>/initial, <reaction>/reaction, <reaction>/rate, <reaction>/rate_unit, time_unit, state_unit, rate_convention, mixing, kinetics.'),
            stochastic_rate_units=dict(type='object',description='Map every reaction ID to its explicitly sourced inverse-time unit; do not infer concentration conversions.'))
        result['open_iteration']=action('open_iteration',record(properties,('reason','config')))
    return result
