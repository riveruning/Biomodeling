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


def choices(values):
    return dict(enum=list(values)) if values else False


def optional_reference(values):
    schema = dict(type='null')
    if values:
        schema = dict(anyOf=[choices(values), schema])
    schema['description'] = 'Omit or use null when absent. Otherwise copy an exact owned reference from the current state; never use an ID, label or placeholder.'
    return schema


def frame_schema(source_refs,transcription_refs=()):
    anchor=dict(id=text(48),kind=dict(const='source'),source_ref=choices(source_refs),
        page=dict(type='integer',minimum=1),quote=text(),start=dict(type='integer',minimum=0),end=dict(type='integer',minimum=1))
    source=record(anchor,('id','kind','source_ref','page','quote'))
    source['description']='Prefer unique literal quote only. Omit start/end; tools compute offsets. Never estimate character positions.'
    source['dependentRequired']=dict(start=['end'],end=['start'])
    goal=record({k:v for k,v in anchor.items() if k not in ('source_ref','page')},('id','kind','quote'))
    goal['properties']['kind']=dict(const='goal')
    goal['dependentRequired']=dict(start=['end'],end=['start'])
    goal['description']='A literal scenario quote is attributed input, never empirical evidence.'
    evidence_items=[source,goal]
    if transcription_refs:
        transcription=record(dict(id=text(48),kind=dict(const='transcription'),
            transcription_ref=choices(transcription_refs)),('id','kind','transcription_ref'))
        transcription['description']=('Cite one transcribe_literature_asset receipt of THIS project. The tool '
            'verifies the project->source->asset->blob->transcription binding and records the receipt status; '
            'the transcribed text is agent-assessed, is NOT a literal page quote and can never be submitted as '
            'kind:source evidence or satisfy a strict model declaration binding. When the page location '
            'matters, anchor the literal [unreadable-formula:<asset>] marker text as a separate kind:source '
            'anchor and cite the transcription receipt beside it.')
        evidence_items.append(transcription)
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
        evidence=dict(type='array',maxItems=LIMITS['evidence'],items=dict(oneOf=evidence_items)),
        unresolved=strings(LIMITS['unresolved']))
    schema=record(properties,properties)
    schema['description']='Use evidence anchor IDs from this same frame in evidence_ids; need IDs and question refs are different namespaces. Identifiers follow the supplied native identifier grammar. At least one criterion is required; question_ref is only for alignment.'
    return schema


def _reading_schemas(view):
    result={}
    reading_refs=view.get('background_reading_refs',[])
    if view['source_refs']:
        result['start_expt_reading']=action('start_expt_reading',record(
            dict(source_ref=choices(view['source_refs'])),('source_ref',)))
    if reading_refs:
        selection=record(dict(role=dict(enum=['methods','results','modeling','other']),
                              segment_id=text(48)),('role','segment_id'))
        result['record_expt_chunk']=action('record_expt_chunk',record(dict(
            reading_ref=choices(reading_refs),chunk_id=text(64),chunk_reviewed=dict(type='boolean'),
            study_type=dict(enum=['experimental','review','mathematical_modeling','uncertain']),
            mathematical_modeling=dict(enum=['present','absent','uncertain']),rationale=text(),
            evidence=dict(type='array',items=selection,maxItems=16)),
            ('reading_ref','chunk_id','chunk_reviewed','study_type','mathematical_modeling','rationale','evidence')))
        result['inspect_expt_reading']=action('inspect_expt_reading',record(dict(
            reading_ref=choices(reading_refs),offset=dict(type='integer',minimum=0,maximum=100000),
            limit=dict(type='integer',minimum=1,maximum=10)),('reading_ref',)))
    return result


def _background_schemas(view):
    if view.get('mode') != 'reproduction' and not view.get('background_index_refs'):
        return {}
    result={}
    anchor=record(dict(source_ref=choices(view['source_refs']),page=dict(type='integer',minimum=1),
                       quote=text(),start=dict(type='integer',minimum=0),end=dict(type='integer',minimum=1)),
                  ('source_ref','page','quote'))
    anchor['dependentRequired']=dict(start=['end'],end=['start'])
    anchor['description']='Use an exact unique source quote. Omit offsets unless selecting a repeated occurrence.'
    anchors=dict(type='array',items=anchor,maxItems=8)
    if view['source_refs']:
        result['index_mm_background']=action('index_mm_background',record(
            dict(source_ref=choices(view['source_refs'])),('source_ref',)))
    reading_refs=view.get('background_reading_refs',[])
    for name,key,refs in (
            ('inspect_mm_background','index_ref',view.get('background_index_refs',[])),
            ('inspect_background_review','review_ref',view.get('background_review_refs',[]))):
        if not refs:
            continue
        args={key:choices(refs),'offset':dict(type='integer',minimum=0,maximum=100000),
              'limit':dict(type='integer',minimum=1,maximum=10,
                          description='Upper bound on returned rows. Bounded pages may contain fewer rows; follow next_offset.')}
        if name=='inspect_mm_background':
            args['reference_ids']=dict(type='array',minItems=1,maxItems=10,
                items=dict(type='string',minLength=1,maxLength=1000),
                description='Omit for plain offset/limit paging over the full index. With 1..10 unique IDs, '
                            'only rows whose XML reference_id equals a requested ID exactly (case-sensitive) '
                            'are paged, in request order. Every row of an ID repeated in the source is '
                            'returned, so an ambiguous ID never poses as a unique match, and IDs matching no '
                            'row are listed in missing_reference_ids. offset/limit apply to the selected rows.')
            args['include_intro_contexts']=dict(type='boolean',
                description='Defaults to true. Set false on repeat reads to omit intro body text previews '
                            'while each row keeps its intro_context_paths and intro_context_count; the reply '
                            'reports intro_contexts_included=false and the immutable index is unchanged.')
        else:
            args['section']=dict(enum=['reviews','requirement_reviews'],
                description='Defaults to reviews. Select requirement_reviews to page joint explanations and source evidence.')
        result[name]=action(name,record(args,(key,)))
    coverage_support=dict(enum=['full','partial','uncertain'],
        description='Omit for full (the legacy default). partial and uncertain are recorded but never '
                    'count as covered; every anchor stays bound to this source and verified in its '
                    'experimental sections. Unsupported-format or unqualified sources cannot cover.')
    coverage=record(dict(requirement_id=text(48),explanation=text(),support=coverage_support,
                         evidence={**anchors,'minItems':1}),('requirement_id','explanation','evidence'))
    joint=record(dict(requirement_id=text(48),support=dict(enum=['full','partial','uncertain']),
                      explanation=text(),evidence=anchors),
                 ('requirement_id','support','explanation','evidence'))
    joint['description']=('Explicit joint assessment for one requirement across the qualified experimental '
                          'sources of this same call. evidence holds 0..8 source anchors; full and partial '
                          'need at least one, uncertain may pass none. Each anchor is re-anchored against '
                          'its own source pages and only verified non-intro methods/results/discussion text '
                          'can support coverage. The entry overrides single-source coverage for its '
                          'requirement_id; only support full with every anchor verified counts as covered, '
                          'while partial and uncertain keep the requirement uncovered. Never add partials '
                          'into full and never claim scientific proof.')
    discovery=record(dict(search_ref=dict(type='string',minLength=64,maxLength=64),
                          publication_source=text(16),publication_id=text(32)),
                     ('search_ref','publication_source','publication_id'))
    discovery['description']=('Binding for a full text that search_literature discovered and fetch_literature '
        'fetched but the MM never cited. search_ref must be the exact search receipt recorded on the fetched '
        'source (an operator-imported source keeps its original project search); publication_source/'
        'publication_id must select exactly one structured result row of that search, and its PMCID/MED '
        'PMID/DOI are checked against the article front identity. Never combine with reference_id; never '
        'invent an MM reference_id or a publication id for a discovered paper.')
    review=record(dict(reference_id=text(1000),discovery=discovery,source_ref=optional_reference(view['source_refs']),
                       study_type=dict(enum=['experimental','review','mathematical_modeling','uncertain']),
                       mathematical_modeling=dict(enum=['present','absent','uncertain']),
                       fulltext_reviewed=dict(type='boolean'),rationale=text(),
                       methods_evidence=anchors,results_evidence=anchors,modeling_evidence=anchors,
                       coverage=dict(type='array',items=coverage,maxItems=16)),
                  ('study_type','mathematical_modeling','fulltext_reviewed','rationale',
                   'methods_evidence','results_evidence','modeling_evidence','coverage'))
    review['description']=('Each review binds exactly one source: a direct MM reference_id, or a discovery '
                           'record together with the fetched full-text source_ref. Never both, never neither.')
    review['allOf']=[{'if':dict(required=['discovery']),
                      'then':dict(required=['source_ref'],
                                  properties=dict(source_ref=dict(type='string',minLength=1,maxLength=64)),
                                  **{'not':dict(required=['reference_id'])})},
                     {'if':{'not':dict(required=['discovery'])},
                      'then':dict(required=['reference_id'])}]
    review_item=review
    if reading_refs:
        reading_coverage=record(dict(requirement_id=text(48),explanation=text(),support=coverage_support,
            evidence_ids=dict(type='array',items=text(48),minItems=1,maxItems=8)),
            ('requirement_id','explanation','evidence_ids'))
        compact=record(dict(reading_ref=choices(reading_refs),reference_id=text(1000),discovery=discovery,
            coverage=dict(type='array',items=reading_coverage,maxItems=16)),('reading_ref',))
        compact['oneOf']=[dict(required=['reference_id'],**{'not':dict(required=['discovery'])}),
                          dict(required=['discovery'],**{'not':dict(required=['reference_id'])})]
        compact['description']='Compile source eligibility, full-text coverage and literal evidence from an immutable reading revision. coverage selects its e0/e1 evidence IDs. Final identity and experimental-section gates still apply.'
        review_item=dict(oneOf=[review,compact])
    requirement_id=dict(text(48), pattern='^[A-Za-z][A-Za-z0-9_]{0,47}$',
                        **{'not':dict(enum=['True','False','and','or','not'])})
    requirement=record(dict(id=requirement_id,question=dict(text(),description='Prior empirical biological background underlying the MM research problem. Merge overlapping needs and exclude the MM implementation, simulation algorithms and mathematical analysis. Keep new hypotheses and open research questions in research rationale, with their empirical premises represented here.'),
        mm_context=anchor,mm_context_id=dict(type='string',minLength=2,maxLength=48,
            pattern='^i[0-9]+$',description='Select context_id from this MM index introduction packet.')),
        ('id','question'))
    requirement['oneOf']=[dict(required=['mm_context'],**{'not':dict(required=['mm_context_id'])}),
                          dict(required=['mm_context_id'],**{'not':dict(required=['mm_context'])})]
    if view.get('background_index_refs'):
        result['review_expt_background']=action('review_expt_background',record(dict(
            index_ref=choices(view.get('background_index_refs',[])),
            requirements=dict(type='array',items=requirement,minItems=1,maxItems=16),
            reviews=dict(type='array',items=review_item,maxItems=64),
            requirement_reviews=dict(type='array',items=joint,maxItems=16,
                description='Optional joint cross-source assessments; each requirement_id appears once and '
                            'overrides per-source coverage for that requirement. Omit when per-source '
                            'coverage alone is the intended verdict.')),
            ('index_ref','requirements','reviews')))
    return result


def requirements_actions(view, index_ref):
    from .research_guidance import coordinator_schemas
    result = coordinator_schemas(view)
    initial = _background_schemas(view)['review_expt_background']
    args = initial['properties']['args']['properties']
    args['index_ref'] = choices([index_ref])
    args['reviews'] = dict(type='array', maxItems=0)
    args['requirement_reviews'] = dict(type='array', maxItems=0)
    result['review_expt_background'] = initial
    result['conclude'] = action('conclude', record(dict(
        outcome=dict(enum=['insufficient_evidence', 'unsupported']), summary=text(),
        assessment_refs=dict(type='array', maxItems=0), limitations=strings(16)),
        ('outcome', 'summary', 'assessment_refs', 'limitations')))
    return result


def schemas(view):
    from .modeling_schema import decision_plan_schema
    result=_background_schemas(view)
    if view.get('mode')=='reproduction' or view.get('background_reading_refs'):
        result.update(_reading_schemas(view))
    if not view['iteration_refs']:
        result['revise_frame']=action('revise_frame',record(dict(reason=text(),
            frame=frame_schema(view['source_refs'],view.get('transcription_refs',[]))),('reason','frame')))
    if view['frame_ref']:
        literal=dict(source_ref=choices(view['source_refs']),model_type=dict(enum=['ode','boolean','stochastic']),reason=text())
        result['bind_source']=action('bind_source',record(literal,literal))
        result['open_source_iteration']=action('open_source_iteration',record(dict(**literal,execution=dict(type='object'),
            reference_ref=optional_reference(view['reference_refs']),parent_iteration_ref=optional_reference(view['iteration_refs'])),(*literal,'execution')))
        execution=dict(type='object',description='Native types use execution.settings. Boolean uses settings.steps and settings.seed, never execution.sweeps. ODE uses execution.experiment with explicit times and time_unit.')
        config=record(dict(model_type=dict(enum=list(TYPES)),source_refs=dict(type='array',items=choices(view['source_refs'])),
            model_ref=text(64),execution=execution,max_calls=dict(type='integer',minimum=1,maximum=20)),('model_type',))
        config['allOf']=[dict(
            **{'if':dict(properties=dict(model_type=dict(enum=list(TYPES[1:]))),required=['model_type'])},
            then=dict(properties=dict(execution=record(dict(settings=dict(type='object')),()))))]
        native=dict(oneOf=[dict(type='object',required=['schema_version','model_type','id','units'],
            properties=dict(schema_version=dict(const=1),model_type=dict(enum=list(TYPED_TYPES)))),
            dict(type='object',required=['schema_version','kind','id'],properties=dict(schema_version=dict(const=1),kind=dict(const='fba')))],
            description='Copy the exact native schema structure from the supplied examples; all backend fields and checks still apply. Do not add a top-level initial or omit model_type.')
        properties=dict(reason=text(),config=config,native_draft=native,hypothesis_ids=strings(16,48),
            parent_iteration_ref=optional_reference(view['iteration_refs']),reference_ref=optional_reference(view['reference_refs']),
            decision_plan=decision_plan_schema(view['source_refs']),native_agent=dict(type='boolean'),
            source_evidence_map=dict(type='object',additionalProperties=dict(type='array',items=text(48),minItems=1,maxItems=4),description='Values are lists of 1..4 source anchor IDs from this frame. Boolean keys are <node>/initial, <node>/rule, update, time_unit, clamps. SSA keys are <species>/initial, <reaction>/reaction, <reaction>/rate, <reaction>/rate_unit, time_unit, state_unit, rate_convention, mixing, kinetics.'),
            stochastic_rate_units=dict(type='object',description='Map every reaction ID to its explicitly sourced inverse-time unit; do not infer concentration conversions.'))
        result['open_iteration']=action('open_iteration',record(properties,('reason','config')))
    if view.get('background_index_refs') or view.get('background_reading_refs'):
        from .research_guidance import EXAMPLES, LARGE_ACTIONS
        action_names=[name for name in LARGE_ACTIONS if name in result]
        guidance_args=dict(oneOf=[
            record(dict(section=dict(const='action_schema'),action=choices(action_names)),('section','action')),
            record(dict(section=dict(const='model_example'),example=dict(enum=list(EXAMPLES))),('section','example')),
            record(dict(section=dict(enum=['capabilities','scientific_procedures'])),('section',))])
        result['inspect_research_guidance']=action('inspect_research_guidance',guidance_args)
    return result
