"""Agent-led research framing and bounded, versioned child experiments.

The agent proposes the scientific details. Deterministic tools preserve source
scope, frozen criteria, actual computation facts and project-wide budgets.
"""
from copy import deepcopy
import hashlib
import json
import math
import time
from uuid import uuid4

from . import model_contracts as c, recovery, research_tasks as tasks, research_usage
from .storage import canonical_bytes,REF_RE
from .action_response import normalize_action
from .llm import ModelResponseError,parse_response_action
from .research_schema import MODES, EVIDENCE_KINDS, CRITERION_KINDS, LIMITS

PROJECT='research_project'
FRAME='research_frame'
ITERATION='research_iteration'
ASSESSMENT='research_iteration_assessment'
QUESTION='research_alignment_question'
REPLY='research_alignment_reply'
CONCLUSION='research_conclusion'
SNAPSHOT='research_project_snapshot'
STEP='research_project_operation'
RETURN='research_project_return'
RESPONSE='research_project_response'
PROVIDER_FAILURE='research_project_provider_failure'
RECOVERY='research_project_recovery'
REFERENCE='research_project_reference'
FBA_TASK='research_project_fba_task'
FBA_ATTEMPT='research_project_fba_attempt'
FBA_BINDING='research_project_fba_binding'
FBA_RETURN='research_project_fba_return'
OPERATIONS=('respond','invalid_response','revise_frame','inspect_source','search_sources','search_literature','fetch_literature','fetch_literature_asset','transcribe_literature_asset','scenario_source',
            'index_mm_background','inspect_mm_background','review_expt_background','inspect_background_review',
            'start_expt_reading','record_expt_chunk','inspect_expt_reading',
            'inspect_research_guidance','inspect_transcription',
            'admit_source','bind_source','open_source_iteration','reference_from_source','open_iteration','run_iteration','resume_iteration','inspect_iteration','assess_iteration','ask_alignment','reply_alignment','reply_source','conclude')
DEFAULTS=dict(planner_max_calls=0,max_iterations=3,max_native_calls=18,native_calls_per_iteration=6,
              max_literature_requests=0,max_transcription_calls=0,transcription_model='unlimited-ocr',
              context_max_chars=60000,assumptions='agent_scoped',total_timeout_s=None)
TERMINAL=('completed_with_limits','insufficient_evidence','unsupported','stopped','deadline_exhausted')


class SourceBindingError(ValueError):
    """Retain bounded validator findings without adopting or repairing a model."""
    def __init__(self,message,check):
        issues=check.get('issues',[])
        self.source_binding_diagnostic=dict(state=check['state'],issues=deepcopy(issues[:32]),
                                            issues_truncated=len(issues)>32)
        super().__init__(message)


def text(value,maximum=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValueError('expected bounded nonempty text')
    return value


def strings(value,maximum=16,limit=2000,*,label=None):
    if not isinstance(value,list) or len(value)>maximum:
        raise ValueError('invalid bounded text list'+(f': {label} expects at most {maximum} strings' if label else ''))
    for item in value:text(item,limit)
    return value


def _refs(store,values,kind=None,maximum=32):
    if not isinstance(values,list) or len(values)>maximum or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):
        raise ValueError('expected distinct artifact references')
    for ref in values:
        obj=store.get(ref)
        if kind and obj['kind']!=kind:raise ValueError('unexpected artifact kind')
    return values


def policy(config):
    c.fields(config,(),DEFAULTS);out={**DEFAULTS,**config}
    for key,low,high in [('planner_max_calls',0,50),('max_iterations',1,8),('max_native_calls',1,200),
                         ('native_calls_per_iteration',1,20),('max_literature_requests',0,20),
                         ('max_transcription_calls',0,20),('context_max_chars',16000,120000)]:
        c.integer(out[key],low,high,key)
    model=out['transcription_model']
    if not isinstance(model,str) or not model.strip() or len(model)>64 or any(ord(ch)<32 for ch in model):
        raise ValueError('transcription_model must be a bounded single-line vision profile id')
    if out['assumptions'] not in ('agent_scoped','forbidden'):raise ValueError('invalid hypothesis policy')
    if out['total_timeout_s'] is not None:
        from .deadlines import positive_seconds
        out['total_timeout_s']=positive_seconds(out['total_timeout_s'],'total_timeout_s',604800)
    return out


# Policy fields introduced after the first project schema revision.  Payloads
# written before them existed are legitimate history: they stay readable and
# are NEVER rewritten or silently migrated.
OPTIONAL_POLICY_FIELDS=('max_transcription_calls','transcription_model')


def effective_policy(saved):
    """Validate a stored project policy and return its effective read view.

    The equality gate `policy(saved)==saved` froze every pre-existing project
    the moment an optional field was added to DEFAULTS.  This check keeps the
    exact same strictness for the fields the payload actually stores —
    unknown fields, missing mandatory fields and invalid values still fail —
    while an old payload that legitimately lacks a later OPTIONAL_POLICY_FIELD
    validates under the original schema and resolves the default in THIS READ
    VIEW ONLY.  The stored payload is never modified here, and load()'s
    implementation-fingerprint gate (unchanged) keeps an old-version project
    from executing under new source: compatibility covers history reading and
    bounded provider-receipt recovery, not scientific execution.
    """
    if not isinstance(saved,dict) or not set(DEFAULTS)-set(OPTIONAL_POLICY_FIELDS)<=set(saved):
        raise ValueError('project policy changed')
    effective=policy(saved)
    if {key:effective[key] for key in saved}!=saved:raise ValueError('project policy changed')
    return effective


def normalize(store,config):
    c.fields(config,{'scenario'},{'mode','source_refs','model_refs','reference_refs','policy','constraints','advice'})
    text(config['scenario'])
    if config.get('mode','application') not in ('application','reproduction'):raise ValueError('invalid research mode')
    sources=_refs(store,config.get('source_refs',[]),'source')
    models=_refs(store,config.get('model_refs',[]),maximum=16)
    if any(store.get(r)['kind'] not in ('typed_model','fba_model') for r in models):
        raise ValueError('prepared model must be a native typed/FBA model')
    references=_refs(store,config.get('reference_refs',[]),REFERENCE,8)
    for r in references:_reference(store,r)
    p=dict(version=1,id=uuid4().hex,scenario=config['scenario'],mode=config.get('mode','application'),
           source_refs=sources,model_refs=models,reference_refs=references,policy=policy(config.get('policy',{})),
           constraints=strings(config.get('constraints',[])),implementation_sha256=recovery.implementation_fingerprint(),
           scientific_support='unknown')
    p['created_at']=time.time()
    p['deadline_at']=p['created_at']+p['policy']['total_timeout_s'] if p['policy']['total_timeout_s'] is not None else None
    from .research_advice import freeze
    p['advice']=freeze(store,config['advice'],sources,models) if config.get('advice') is not None else None
    return p


def create(store,config):
    p=normalize(store,config)
    advice=p['advice']['scope']['memory_refs'] if p['advice'] else []
    return store.put(PROJECT,p,refs=(*p['source_refs'],*p['model_refs'],*p['reference_refs'],*advice))


def load(store,ref,*,historical=False):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!=PROJECT or type(p.get('version')) is not int or p['version']!=1:raise ValueError('expected research project')
    fingerprint=p.get('implementation_sha256')
    if historical:
        # Explicit offline cross-source-version read, used only by recover()
        # to adopt one bounded provider receipt: the frozen project keeps its
        # recorded version, deadline and budgets, nothing is migrated and no
        # scientific execution is unlocked. The recorded fingerprint must be
        # a real 64-hex digest from the source version that wrote the store.
        if not isinstance(fingerprint,str) or not REF_RE.fullmatch(fingerprint):
            raise ValueError('historical project lacks a verifiable implementation fingerprint')
    elif fingerprint!=recovery.implementation_fingerprint():
        raise ValueError('project implementation changed; retain historical version')
    # Version-tolerant integrity check (see effective_policy): old payloads
    # without later optional fields stay valid history; nothing is rewritten.
    effective_policy(p['policy'])
    c.number(p['created_at'],0.,1e12,'created_at')
    expected=p['created_at']+p['policy']['total_timeout_s'] if p['policy']['total_timeout_s'] is not None else None
    if p['deadline_at']!=expected:raise ValueError('project deadline changed')
    advice=p['advice']['scope']['memory_refs'] if p.get('advice') else []
    if set(obj['refs'])!=set(p['source_refs']+p['model_refs']+p['reference_refs']+advice):raise ValueError('project input closure changed')
    return p


def _reference_payload(config,actor,authority='operator_supplied_reference_not_independent_review'):
    from .domain.contracts import SimulationResult
    from .domain.serialization import from_dict,to_dict
    from . import validation
    c.fields(config,{'reference','note'},{'scales','threshold','model_type'})
    text(actor,200);text(config['note'])
    if config.get('model_type') in tasks.TYPES[2:]:
        from .typed_references import validate
        if 'scales' in config or 'threshold' in config:raise ValueError('native references freeze scales and threshold inside their explicit protocol')
        reference=validate(config['reference'])
        if reference['model_type']!=config['model_type']:raise ValueError('reference family mismatch')
        return dict(version=1,reference_type='typed_observations',reference=reference,note=config['note'],actor=actor,
            authority=authority,independent=False,scientific_support='unknown')
    if config.get('model_type')=='fba':
        from . import fba
        if 'scales' in config or 'threshold' in config:raise ValueError('FBA reference uses the existing fixed scalar tolerance')
        ref=fba.config(dict(reference=config['reference']))['reference']
        return dict(version=1,reference_type='fba_objective',reference=ref,note=config['note'],actor=actor,
            authority=authority,independent=False,scientific_support='unknown')
    if config.get('model_type','ode')!='ode':raise ValueError('references currently support ODE series and FBA objectives')
    ref=from_dict(SimulationResult,config['reference']);validation.validate_reference(ref)
    scales=validation.validate_scales(config.get('scales'),ref.values)
    if scales is None:raise ValueError('reference needs fixed scales')
    return dict(version=1,reference_type='time_series',reference=to_dict(ref),scales=scales,note=config['note'],actor=actor,
           threshold=c.number(config.get('threshold',.001),1e-12,1.,'threshold'),
           authority=authority,independent=False,scientific_support='unknown')


def register_reference(store,config,actor):return store.put(REFERENCE,_reference_payload(config,actor))


def _reference(store,ref):
    obj=store.get(ref)
    if obj['kind']!=REFERENCE or obj['payload'].get('version')!=1:raise ValueError('invalid research reference')
    if obj['payload'].get('observation_review_ref'):
        from .observation_data import verify
        if set(obj['refs'])!={obj['payload']['observation_review_ref']}:raise ValueError('observation reference closure mismatch')
        verify(store,obj['payload'])
    if obj['payload'].get('source_provenance'):
        from .research_references import verify
        source=obj['payload']['source_provenance']['extraction']['source_ref']
        if set(obj['refs'])!={obj['payload']['project_ref'],source}:raise ValueError('source reference closure mismatch')
        verify(store,obj['payload'])
    return obj['payload']


def rows(store,kind,project_ref):
    return tasks.records(store,kind,project_ref=project_ref)


def own(store,ref,kind,project_ref):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!=kind or p.get('project_ref')!=project_ref or project_ref not in obj['refs']:
        raise ValueError('artifact outside this research project')
    return p


def _provider_failure_closure(store,project_ref,attempt_args,attempt_ref,failure_ref):
    """Verified closure of one retained provider failure receipt.

    One shared check for the live received-but-refused classification, the
    offline recovery claim and history re-reads: the receipt must belong to
    this project and attempt, cite the attempt's original prompt request and
    provider identity, close over project/attempt/request/blob refs, and the
    cited prompt must be the real planner prompt whose recorded scope is the
    canonical hash of its own messages.  Returns (failure payload, blob ref).
    """
    failure=own(store,failure_ref,PROVIDER_FAILURE,project_ref)
    request_ref=attempt_args.get('request_ref');scope=attempt_args.get('scope_sha256')
    provider=attempt_args.get('provider')
    blob_ref=failure.get('model_content_json_blob_ref')
    if (failure.get('version')!=1 or failure.get('attempt_ref')!=attempt_ref
            or failure.get('request_ref')!=request_ref or failure.get('provider')!=provider
            or failure.get('error_kind')!='model_response'
            or not isinstance(blob_ref,str) or not blob_ref
            or not {project_ref,attempt_ref,request_ref,blob_ref}<=set(store.get(failure_ref)['refs'])):
        raise ValueError('provider failure receipt binding mismatch')
    prompt=store.get(request_ref);payload=prompt['payload']
    if (prompt['kind']!='research_project_prompt' or payload.get('project_ref')!=project_ref
            or payload.get('scope_sha256')!=scope or payload.get('provider')!=provider
            or not isinstance(payload.get('messages'),list)
            or hashlib.sha256(canonical_bytes(payload['messages'])).hexdigest()!=scope):
        raise ValueError('provider failure prompt binding mismatch')
    return failure,blob_ref


def history(store,project_ref,*,historical=False):
    load(store,project_ref,historical=historical)
    attempts=rows(store,STEP,project_ref);attempts.sort(key=lambda r:r[1]['sequence'])
    if [p['sequence'] for _,p in attempts]!=list(range(len(attempts))):raise ValueError('ambiguous research operation sequence')
    # Scan linked returns once. Repeating a full store scan for every operation
    # made a second project much slower, despite unchanged scientific work.
    related={RETURN:{},RECOVERY:{},PROVIDER_FAILURE:{}};attempt_ids={r for r,_ in attempts}
    for path in sorted(store.objects_dir.glob('*.json')):
        if not REF_RE.fullmatch(path.stem):continue
        record=store._read_envelope(path.stem);payload=record['payload'];kind=record['kind']
        if kind in related and isinstance(payload,dict) and payload.get('attempt_ref') in attempt_ids:
            store.get(path.stem)
            related[kind].setdefault(payload['attempt_ref'],[]).append((path.stem,payload))
    results={};pending=[]
    for ref,p in attempts:
        expected={project_ref,*([p['response_ref']] if p.get('response_ref') else [])}
        if p['operation'] not in OPERATIONS or set(store.get(ref)['refs'])!=expected:raise ValueError('invalid project operation')
        found=related[RETURN].get(ref,[])
        if len(found)>1:raise ValueError('conflicting project returns')
        if found:
            rr,rp=found[0]
            if rp.get('project_ref')!=project_ref or not {project_ref,ref}<=set(store.get(rr)['refs']):
                raise ValueError('project return binding mismatch')
            if rp['state'] not in ('returned','rejected','unknown'):raise ValueError('invalid project return state')
            failure_ref=rp.get('provider_failure_ref')
            if failure_ref:
                failure=own(store,failure_ref,PROVIDER_FAILURE,project_ref)
                if (failure.get('output_budget_verified') is True
                        or rp['state']=='rejected' and (rp.get('diagnostic') or {}).get('code')=='output_budget'):
                    from .research_output_budget import failure_closure
                    if (p['operation']!='respond' or rp['state']!='rejected'
                            or failure_ref not in store.get(rr)['refs']
                            or (rp.get('diagnostic') or {}).get('code')!='output_budget'):
                        raise ValueError('invalid known output budget return')
                    failure_closure(store,project_ref,p['args'],ref,failure_ref)
            results[ref]=(rr,rp)
        recovered=related[RECOVERY].get(ref,[])
        if len(recovered)>1:raise ValueError('conflicting recoveries')
        if recovered:
            rr,rp=recovered[0]
            if (rp.get('project_ref')!=project_ref or p['operation'] not in ('respond','run_iteration','resume_iteration')
                    or found and found[0][1]['state']!='unknown'):raise ValueError('invalid recovery')
            if rp.get('state')=='rejected':
                # Explicit recovery outcome for a retained-but-refused planner
                # response: no replay, usage stays bound to the original failure.
                if (p['operation']!='respond' or rp.get('result') is not None
                        or not isinstance(rp.get('diagnostic'),dict)
                        or not isinstance(rp.get('confirmation_ref'),str)
                        or rp.get('provider_failure_ref')!=rp['confirmation_ref']):
                    raise ValueError('invalid recovery')
                _provider_failure_closure(store,project_ref,p['args'],ref,rp['confirmation_ref'])
                failures=related[PROVIDER_FAILURE].get(ref,[])
                if (len(failures)!=1 or failures[0][0]!=rp['confirmation_ref']
                        or not found or found[0][1].get('provider_failure_ref')!=rp['confirmation_ref']
                        or rp['confirmation_ref'] not in set(store.get(found[0][0])['refs'])):
                    raise ValueError('invalid recovery')
            elif p['operation']=='respond':
                receipt=own(store,rp['confirmation_ref'],RESPONSE,project_ref)
                if (receipt['attempt_ref']!=ref or receipt['scope_sha256']!=p['args']['scope_sha256']
                        or receipt['provider']!=p['args']['provider']):raise ValueError('recovery receipt mismatch')
                expected_result=dict(response_ref=rp['confirmation_ref'],response=receipt['response'],usage=receipt['usage'])
                if receipt.get('source')=='provider_failure_content':
                    failure_ref=receipt.get('provider_failure_ref')
                    failures=related[PROVIDER_FAILURE].get(ref,[])
                    if (not isinstance(failure_ref,str) or len(failures)!=1 or failures[0][0]!=failure_ref
                            or failure_ref not in set(store.get(rp['confirmation_ref'])['refs'])
                            or not found or found[0][1].get('provider_failure_ref')!=failure_ref
                            or failure_ref not in set(store.get(found[0][0])['refs'])):
                        raise ValueError('recovery receipt mismatch')
                    _provider_failure_closure(store,project_ref,p['args'],ref,failure_ref)
                    expected_result['provider_failure_ref']=failure_ref
                if rp['state']!='returned' or rp['result']!=expected_result:raise ValueError('recovery result mismatch')
            else:
                expected_result=_confirmed_native_result(store,project_ref,p,rp['confirmation_ref'])
                if rp['state']!='returned' or rp['result']!=expected_result:raise ValueError('recovery result mismatch')
            results[ref]=(rr,rp)
        elif not found or found[0][1]['state']=='unknown':pending.append(ref)
    return attempts,results,pending


def inspect(store,project_ref):
    project=load(store,project_ref);limits=effective_policy(project['policy'])
    attempts,returned,pending=history(store,project_ref)
    successful=[(ref,p,returned[ref]) for ref,p in attempts if ref in returned and returned[ref][1]['state']=='returned']
    frames=[];iterations=[];assessments=[];questions=[];replies=[];sources=list(project['source_refs']);references=list(project['reference_refs']);conclusion=None
    background_indexes=[];background_reviews=[];background_readings=[];transcriptions=[]
    for _,attempt,(_,ret) in successful:
        result=ret['result'];op=attempt['operation']
        if op in ('revise_frame','bind_source'):frames.append(result['frame_ref'])
        elif op=='transcribe_literature_asset':
            ref=result.get('transcription_ref')
            if isinstance(ref,str) and ref not in transcriptions:transcriptions.append(ref)
        elif op in ('open_iteration','open_source_iteration'):iterations.append(result['iteration_ref'])
        elif op=='assess_iteration':assessments.append(result['assessment_ref'])
        elif op=='index_mm_background':background_indexes.append(result['index_ref'])
        elif op=='review_expt_background':background_reviews.append(result['review_ref'])
        elif op in ('start_expt_reading','record_expt_chunk') and result['reading_ref'] not in background_readings:
            background_readings.append(result['reading_ref'])
        elif op=='ask_alignment':questions.append(result['question_ref'])
        elif op=='reply_alignment':replies.append(result['reply_ref'])
        elif op=='reference_from_source' and result['reference_ref'] not in references:references.append(result['reference_ref'])
        elif op in ('fetch_literature','reply_source','scenario_source','admit_source'):
            source=result.get('source_ref')
            if source and source not in sources:sources.append(source)
        elif op=='conclude':conclusion=result['conclusion_ref']
    answered={own(store,r,REPLY,project_ref)['question_ref'] for r in replies}
    waiting=[r for r in questions if r not in answered]
    used_planner=sum(p['operation']=='respond' for _,p in attempts)
    used_literature=sum(p['operation'] in ('search_literature','fetch_literature','fetch_literature_asset') for _,p in attempts)
    used_transcription=sum(p['operation']=='transcribe_literature_asset' for _,p in attempts)
    native_reserved=sum(p.get('native_allocation',0) for _,p in attempts)
    frame=own(store,frames[-1],FRAME,project_ref) if frames else None
    outcome=own(store,conclusion,CONCLUSION,project_ref)['outcome'] if conclusion else None
    state='needs_recovery' if pending else 'awaiting_alignment' if waiting else outcome or 'ready'
    from . import research_output_budget as output_budget
    budget_failure=output_budget.pending(attempts,returned)
    budget_plans=output_budget.plans(store,project_ref,attempts,returned)
    if state=='ready' and budget_failure and budget_failure[0] not in budget_plans:
        state='output_budget_exhausted'
    from .research_control import status as control_status
    control=control_status(store,project_ref,project)
    if not pending and not outcome and (control['stop_requested'] or control['deadline_exhausted']):
        state='stopped' if control['stop_requested'] else 'deadline_exhausted'
    consumed={p.get('response_ref') for _,p in attempts if p.get('response_ref')}
    saved=next((returned[ref][1]['result'] for ref,p in attempts
        if p['operation']=='respond' and ref in returned and returned[ref][1]['state']=='returned'
        and returned[ref][1]['result']['response_ref'] not in consumed),None)
    actionable=saved is not None
    if state=='ready' and used_planner>0 and used_planner>=limits['planner_max_calls'] and not actionable:state='planner_budget_exhausted'
    remaining=used_planner<limits['planner_max_calls']
    can_advance=state=='ready' and len(attempts)<200 and (actionable or remaining)
    native_needed=False
    if can_advance and saved:
        response=saved['response'];args=response.get('args') if isinstance(response,dict) else None
        if isinstance(args,dict) and response.get('action') in ('run_iteration','resume_iteration') and args.get('iteration_ref') in iterations:
            iteration=own(store,args['iteration_ref'],ITERATION,project_ref)
            if iteration['executor']=='research_task':
                native=tasks.status(store,iteration['task_ref'])
                native_needed=(response['action']=='resume_iteration' and native['state']=='awaiting_input'
                               or response['action']=='run_iteration' and native['state']=='ready_to_run')
    continuation=dict(can_advance=can_advance,has_saved_response=actionable,
        requires_planner=can_advance and remaining,requires_native_provider=native_needed,
        requires_provider=can_advance and (remaining or native_needed),
        reason='saved_response' if can_advance and actionable else 'planner_available' if can_advance else
            'operation_limit_exhausted' if len(attempts)>=200 else 'planner_budget_exhausted' if state=='ready' else state)
    return dict(project_ref=project_ref,scenario=project['scenario'],mode=project['mode'],state=state,
        continuation=continuation,control=control,
        frame_ref=frames[-1] if frames else None,frame=frame,frame_refs=frames,iteration_refs=iterations,
        assessment_refs=assessments,question_refs=questions,reply_refs=replies,pending_questions=waiting,
        source_refs=sources,model_refs=project['model_refs'],reference_refs=references,conclusion_ref=conclusion,
        background_index_refs=background_indexes,background_review_refs=background_reviews,
        background_reading_refs=background_readings,transcription_refs=transcriptions,
        output_budget_attempt_ref=budget_failure[0] if budget_failure else None,
        output_budget_failure_ref=budget_failure[2] if budget_failure else None,
        output_budget_plan_ref=budget_plans.get(budget_failure[0],(None,None))[0] if budget_failure else None,
        unresolved_operation_refs=pending,budget=dict(planner_calls=used_planner,planner_max_calls=limits['planner_max_calls'],
        literature_requests=used_literature,max_literature_requests=limits['max_literature_requests'],
        transcription_calls=used_transcription,max_transcription_calls=limits['max_transcription_calls'],
        iterations=len(iterations),max_iterations=limits['max_iterations'],native_reserved=native_reserved,
        max_native_calls=limits['max_native_calls']),scientific_support='unknown')


def _transcription_anchor(store,project_ref,raw,view):
    """Verified frame citation of an agent-assessed transcription artifact.

    The frame never copies the transcribed text: a visual transcription is a
    derived agent-assessed result bound (at receipt time) to the official
    asset blob and the vision request, not literal page text.  This anchor
    deterministically re-verifies project -> source -> asset -> blob ->
    transcription ownership and scope, so an assumption, assessment or
    decision can cite the transcription's provenance and recorded status
    without laundering its text into a source quote.  The literal citation
    rules in task_proposals._anchor stay unchanged and strict; a transcription
    anchor can never satisfy a strict model declaration binding (only
    kind='source' anchors can)."""
    from . import research_literature as literature
    c.fields(raw,{'id','kind','transcription_ref'})
    ref=raw['transcription_ref']
    if not isinstance(ref,str):raise ValueError('transcription evidence needs the transcription receipt reference returned by transcribe_literature_asset')
    record=own(store,ref,literature.TRANSCRIPTION,project_ref)
    if record['source_ref'] not in view['source_refs']:
        raise ValueError('transcription evidence source is outside the project source scope')
    return dict(id=c.identifier(raw['id']),kind='transcription',transcription_ref=ref,
        source_ref=record['source_ref'],asset_ref=record['asset_ref'],asset=record['asset'],
        blob_ref=record['blob_ref'],status=record['status'],finish_reason=record.get('finish_reason'),
        output_characters=record['output_characters'],
        output_authority=record['output_authority'],
        notice='Provenance-bound agent-assessed visual transcription. The transcribed text stays in the '
               'transcription artifact: it is not literal page evidence and never a strict declaration source.')


def _frame(store,project_ref,spec):
    project=load(store,project_ref);view=inspect(store,project_ref)
    if len(canonical_bytes(spec))>128000:raise ValueError('research frame exceeds 128KB')
    c.fields(spec,{'objective','mode','granularity','modeling_targets','rationale','evidence_needs','assumptions','criteria','evidence','unresolved'})
    text(spec['objective']);text(spec['granularity']);text(spec['rationale'])
    if spec['mode'] not in MODES:raise ValueError('invalid study mode; frame.mode must be one of '+', '.join(MODES))
    if len(view['frame_refs'])>=8:raise ValueError('frame revision limit exceeded')
    strings(spec['modeling_targets'],LIMITS['modeling_targets'],label='frame.modeling_targets')
    if not spec['modeling_targets']:raise ValueError('agent must identify at least one modeling target')
    strings(spec['unresolved'],LIMITS['unresolved'],label='frame.unresolved')
    from .task_proposals import _anchor
    if not isinstance(spec['evidence'],list) or len(spec['evidence'])>LIMITS['evidence']:raise ValueError('at most 32 literal evidence anchors')
    anchors=[];ids=set()
    for raw in spec['evidence']:
        eid=c.identifier(raw.get('id'))
        if eid in ids or raw.get('kind')=='model':raise ValueError('evidence needs unique literal source/goal IDs')
        ids.add(eid)
        if raw.get('kind')=='transcription':
            anchors.append(_transcription_anchor(store,project_ref,raw,view));continue
        anchors.append(_anchor(store,raw,dict(spec=dict(goal=project['scenario'],source_refs=view['source_refs']))))
    if not isinstance(spec['evidence_needs'],list) or len(spec['evidence_needs'])>LIMITS['evidence_needs']:raise ValueError('invalid evidence plan')
    seen=set()
    for need in spec['evidence_needs']:
        c.fields(need,{'id','question','kind','queries'});nid=c.identifier(need['id'])
        if nid in seen:raise ValueError('duplicate evidence need')
        seen.add(nid);text(need['question']);strings(need['queries'],LIMITS['queries'],256,label=f'frame.evidence_needs[{nid}].queries')
        if need['kind'] not in EVIDENCE_KINDS:
            raise ValueError(f'frame.evidence_needs[{nid}].kind must be literature, data, mathematics or validation')
    if not isinstance(spec['assumptions'],list) or len(spec['assumptions'])>LIMITS['assumptions']:raise ValueError('invalid assumption list')
    seen=set()
    for assumption in spec['assumptions']:
        c.fields(assumption,{'id','statement','rationale','evidence_ids','decision'})
        aid=c.identifier(assumption['id'])
        if aid in seen:raise ValueError('duplicate assumption')
        seen.add(aid);text(assumption['statement']);text(assumption['rationale']);strings(assumption['evidence_ids'],32,48)
        if set(assumption['evidence_ids'])-ids:raise ValueError('assumption cites absent evidence')
        if assumption['decision'] not in ('adopt','defer','reject'):raise ValueError('invalid assumption decision')
    if not isinstance(spec['criteria'],list) or not 1<=len(spec['criteria'])<=LIMITS['criteria']:raise ValueError('agent must define 1..8 criteria')
    seen=set()
    for criterion in spec['criteria']:
        c.fields(criterion,{'id','kind','description','required'},{'question_ref'});cid=c.identifier(criterion['id'])
        if cid in seen:raise ValueError('duplicate criterion')
        seen.add(cid);text(criterion['description'])
        if criterion['kind'] not in CRITERION_KINDS:
            raise ValueError('unsupported criterion evaluator')
        if type(criterion['required']) is not bool:raise ValueError('criterion required must be bool')
        if criterion.get('question_ref'):
            if criterion['kind']!='alignment' or criterion['question_ref'] not in view['question_refs']:raise ValueError('alignment criterion needs an owned question')
    if not any(x['required'] for x in spec['criteria']):raise ValueError('at least one required criterion')
    return {**deepcopy(spec),'evidence':anchors}


def _new_frame(store,project_ref,spec,reason):
    view=inspect(store,project_ref);normalized=_frame(store,project_ref,spec);text(reason)
    previous=view['frame_ref'];refs=[project_ref,*view['source_refs'],*([previous] if previous else [])]
    questions={x['question_ref'] for x in normalized['criteria'] if x.get('question_ref')}
    refs.extend(questions)
    refs.extend(r for r in view['reply_refs'] if own(store,r,REPLY,project_ref)['question_ref'] in questions)
    # Frame -> transcription -> asset/blob/source closure travels with the
    # frame refs, so exports and audits can follow the full provenance chain.
    refs.extend(a['transcription_ref'] for a in normalized['evidence'] if a['kind']=='transcription')
    p=dict(version=1,project_ref=project_ref,previous_ref=previous,revision=len(view['frame_refs'])+1,
           spec=normalized,reason=reason,authority='agent_scientific_proposal',
           post_result_revision=any(facts(store,r)['executed'] for r in view['iteration_refs']),
           scientific_support='unknown')
    return store.put(FRAME,p,refs=refs)


def _import_draft(store,project_ref,frame_ref,raw,mode,hypothesis_ids,binding=None):
    from . import typed_models
    if raw.get('kind')=='fba':
        from . import fba
        model=fba.validate(raw);blob=store.put_blob(canonical_bytes(raw))
        return store.put('fba_model',dict(version=1,model=model,source_blob_ref=blob,format='json',
            adapter=dict(adapter='native_fba_v1',warnings=['Agent hypothesis, not source-certified.']),
            origin='agent_project_hypothesis',project_ref=project_ref,frame_ref=frame_ref,
            hypothesis_ids=hypothesis_ids,study_mode=mode,scientific_support='unknown'),refs=(project_ref,frame_ref,blob))
    model=typed_models._decode(canonical_bytes(raw));kind=model['model_type'];blob=store.put_blob(canonical_bytes(raw))
    p=dict(version=1,model_type=kind,profile=typed_models.backend(kind,model=model).PROFILE,model=model,blob_ref=blob,
           origin='agent_source_bound_project_model' if binding else 'agent_project_hypothesis',
           project_ref=project_ref,frame_ref=frame_ref,hypothesis_ids=hypothesis_ids,study_mode=mode,
           scientific_support='unknown')
    if binding:p['source_binding']=binding
    return store.put('typed_model',p,refs=(project_ref,frame_ref,blob))


def _source_check(store,kind,model,anchors,mapping,sources,units):
    from . import boolean_proposals,stochastic_proposals
    if kind=='boolean':return boolean_proposals.inspect(store,model,anchors,mapping,sources)
    if kind=='stochastic':return stochastic_proposals.inspect(store,model,units,anchors,mapping,sources)
    raise ValueError('strict source-bound draft supports Boolean/SSA; ODE uses the decision-plan route')


def _open_iteration(store,project_ref,args):
    project=load(store,project_ref);view=inspect(store,project_ref)
    c.fields(args,{'reason','config'},{'parent_iteration_ref','native_draft','hypothesis_ids','source_evidence_map','stochastic_rate_units','reference_ref','decision_plan','native_agent'})
    if 'native_agent' in args and type(args['native_agent']) is not bool:raise ValueError('native_agent must be bool')
    if not view['frame_ref']:raise ValueError('define a research frame first')
    if len(view['iteration_refs'])>=project['policy']['max_iterations']:raise ValueError('iteration budget exhausted')
    text(args['reason']);frame=own(store,view['frame_ref'],FRAME,project_ref);mode=frame['spec']['mode']
    parent=args.get('parent_iteration_ref')
    if parent is not None and parent not in view['iteration_refs']:raise ValueError('parent iteration outside project')
    hypotheses=args.get('hypothesis_ids',[]);strings(hypotheses,16,48)
    adopted={a['id'] for a in frame['spec']['assumptions'] if a['decision']=='adopt'}
    if set(hypotheses)-adopted:raise ValueError('hypothesis IDs must name adopted assumptions')
    if hypotheses and (mode=='strict_reproduction' or project['policy']['assumptions']=='forbidden'):
        raise ValueError('hypotheses cannot enter strict reproduction or a forbidden-assumption project')
    config=deepcopy(args['config'])
    if not isinstance(config,dict):raise ValueError('config must be an object')
    execution=config.get('execution',{})
    if not isinstance(execution,dict):raise ValueError('config.execution must be an object')
    if 'settings' in execution and not isinstance(execution['settings'],dict):
        raise ValueError('config.execution.settings must be an object')
    if any(k in config.get('execution',{}) for k in ('reference','scales')):
        raise ValueError('use a frozen authorized reference_ref, not agent-generated reference values')
    if 'reference' in config.get('execution',{}).get('settings',{}):raise ValueError('nested reference answers cannot be agent-generated')
    if config.get('model_type') in tasks.TYPES[1:] and set(execution)-{'settings'}:
        raise ValueError('Native settings belong in config.execution.settings; Boolean uses steps, not sweeps.')
    reference=args.get('reference_ref')
    if reference is not None:
        if reference not in view['reference_refs']:raise ValueError('reference outside project')
        rp=_reference(store,reference)
        if rp['reference_type']=='fba_objective':
            if config.get('model_type')!='fba':raise ValueError('FBA objective reference needs an FBA experiment')
            config.setdefault('execution',{}).setdefault('settings',{})['reference']=rp['reference']
        elif rp['reference_type']=='typed_observations':
            if config.get('model_type')!=rp['reference']['model_type'] or args.get('native_agent',False):
                raise ValueError('native references need matching family and direct deterministic execution')
        else:
            if config.get('model_type')!='ode':raise ValueError('series references currently require an ODE experiment')
            config.setdefault('execution',{}).update(reference=rp['reference'],scales=rp['scales'])
    config.setdefault('schema_version',1);config.setdefault('goal',frame['spec']['objective'])
    config.setdefault('source_refs',view['source_refs'][:8]);config.setdefault('max_calls',project['policy']['native_calls_per_iteration'])
    if set(config['source_refs'])-set(view['source_refs']):raise ValueError('iteration sources outside project')
    if mode=='strict_reproduction' and any(store.get(r)['payload'].get('research_input_role') in ('scenario','researcher_reply') for r in config['source_refs']):
        raise ValueError('an application scenario is not reported source evidence for strict reproduction')
    if config.get('proposal_max_calls',0)!=0:raise ValueError('supervisor owns planning; nested proposal calls are not enabled')
    c.integer(config['max_calls'],1,project['policy']['native_calls_per_iteration'],'max_calls')
    native_draft=args.get('native_draft');binding=None
    if native_draft is not None:
        if args.get('decision_plan') is not None or config.get('model_ref'):raise ValueError('choose one mathematical input')
        if not isinstance(native_draft,dict):raise ValueError('native_draft must be a native model object')
        if config.get('model_type')!=native_draft.get('model_type',native_draft.get('kind')):
            field='kind' if config.get('model_type')=='fba' else 'model_type'
            raise ValueError(f'native draft type mismatch: native_draft.{field} must equal {config.get("model_type")!r}; use the supplied native schema')
        if mode=='strict_reproduction':
            check=_source_check(store,config.get('model_type'),native_draft,frame['spec']['evidence'],
                args.get('source_evidence_map',{}),config['source_refs'],args.get('stochastic_rate_units',{}))
            if check['state']!='source_bound':raise SourceBindingError('strict draft lacks complete source bindings',check)
            binding=check.get('binding',dict(profile='literal_boolean_declarations_v1'))
        elif not hypotheses:raise ValueError('a new mathematical model must link to explicit adopted hypotheses')
        config['model_ref']=_import_draft(store,project_ref,view['frame_ref'],native_draft,mode,hypotheses,binding)
    elif config.get('model_ref'):
        owned_models={own(store,r,ITERATION,project_ref)['config'].get('model_ref') for r in view['iteration_refs']}
        if config['model_ref'] not in set(project['model_refs'])|owned_models:raise ValueError('prepared model outside project')
        mp=store.get(config['model_ref'])['payload']
        if mp.get('origin')=='agent_project_hypothesis' and not hypotheses:raise ValueError('retain explicit adopted hypotheses when reusing an agent model')
        if mode=='strict_reproduction':
            if mp.get('origin')!='agent_source_bound_project_model' or mp.get('project_ref')!=project_ref or mp.get('frame_ref')!=view['frame_ref']:
                raise ValueError('strict reuse needs this frame source binding; import a newly bound draft')
            previous=next((own(store,r,ITERATION,project_ref) for r in view['iteration_refs'] if own(store,r,ITERATION,project_ref)['config'].get('model_ref')==config['model_ref']),None)
            if previous:
                args={**args,'source_evidence_map':args.get('source_evidence_map',previous['source_evidence_map']),
                    'stochastic_rate_units':args.get('stochastic_rate_units',previous['stochastic_rate_units'])}
            check=_source_check(store,config['model_type'],mp['model'],frame['spec']['evidence'],
                args.get('source_evidence_map',{}),config['source_refs'],args.get('stochastic_rate_units',{}))
            if check['state']!='source_bound':raise SourceBindingError('reused mathematical input lacks the frozen source binding',check)
            if check['state']!='source_bound' or check.get('binding',dict(profile='literal_boolean_declarations_v1'))!=mp['source_binding']:
                raise ValueError('reused mathematical input lacks the frozen source binding')
    if args.get('decision_plan') is not None:
        from . import modeling_plan,modeling_workflow
        if config.get('model_type')!='ode':raise ValueError('decision plan requires ODE')
        plan=args['decision_plan']
        if plan.get('mode')!=('reproduction' if mode=='strict_reproduction' else 'exploration'):
            raise ValueError('decision plan mode differs from research frame')
        if mode!='strict_reproduction' and not hypotheses:raise ValueError('exploratory ODE needs explicit hypotheses')
        pr=modeling_plan.prepare(store,plan,config['source_refs']);cr=modeling_plan.compile_plan(store,pr)
        compiled=store.get(cr)['payload']
        if compiled.get('state')!='ready':
            # The compilation receipt is already saved immutable. Surface its
            # real bounded findings (state/issues/checks plus plan/compilation
            # refs) so the planner can submit a revised plan; scientific values,
            # citations and source requirements are never guessed or relaxed
            # here, and a ready compilation with a differing dependency closure
            # is still rejected by the execution preflight below.
            issues=compiled.get('issues',[]);checks=compiled.get('checks',[])
            error=ValueError('decision plan compilation is not ready; submit a revised decision plan in a new open_iteration')
            error.compilation_diagnostic=dict(compilation_ref=cr,plan_ref=compiled.get('plan_ref'),
                state=compiled.get('state'),issues=deepcopy(issues[:32]),issues_truncated=len(issues)>32,
                checks=deepcopy(checks[:32]),checks_truncated=len(checks)>32)
            raise error
        execution=deepcopy(config.get('execution',{}))
        if reference:execution['threshold']=rp['threshold']
        task_ref=modeling_workflow.prepare(store,cr,execution);executor='decision_plan'
        allocation=0;task_config=config
    else:
        source_scope=list(config['source_refs'])
        if config.get('model_type')!='ode':config['source_refs']=[]
        normalized=tasks._normalize(store,config)
        if normalized['model_type']=='ode' and mode!='strict_reproduction':
            raise ValueError('application ODE uses an explicit exploratory decision_plan')
        if normalized['model_type']!='ode' and not args.get('native_agent',False):
            if not normalized.get('model_ref'):raise ValueError('prepare mathematical input before direct execution')
            if normalized['model_type']=='fba':
                from . import fba
                fba.load(store,normalized['model_ref']);settings=fba.config(normalized['execution'].get('settings'))
                task_ref=store.put(FBA_TASK,dict(version=1,id=uuid4().hex,project_ref=project_ref,
                    model_ref=normalized['model_ref'],settings=settings,implementation_sha256=recovery.implementation_fingerprint()),
                    refs=(project_ref,normalized['model_ref']))
                executor='fba_execution'
            else:
                from . import typed_models
                task_ref=typed_models.prepare(store,normalized['model_ref'],normalized['execution'].get('settings'),reference_ref=reference)
                executor='typed_execution'
            allocation=0
        else:
            task_ref=tasks.prepare(store,normalized)
            if tasks.preflight(store,task_ref)['state']!='ready_to_run':raise ValueError('iteration input is incomplete; collect evidence or align the research frame first')
            executor='research_task';allocation=normalized['max_calls']
        task_config=normalized
    payload=dict(version=1,project_ref=project_ref,frame_ref=view['frame_ref'],parent_iteration_ref=parent,
        index=len(view['iteration_refs'])+1,reason=args['reason'],study_mode=mode,hypothesis_ids=hypotheses,
        executor=executor,task_ref=task_ref,config=task_config,reference_ref=reference,native_allocation=allocation,
        source_evidence_map=args.get('source_evidence_map',{}),stochastic_rate_units=args.get('stochastic_rate_units',{}),
        source_refs=list(config['source_refs']) if executor=='decision_plan' else source_scope,
        criteria=deepcopy(frame['spec']['criteria']),post_result_protocol=bool(parent and facts(store,parent)['executed']),
        scientific_support='unknown')
    return store.put(ITERATION,payload,refs=(project_ref,view['frame_ref'],task_ref,*([parent] if parent else []),*([reference] if reference else [])))




def _fba_result(store,task_ref):
    from . import fba
    task=store.get(task_ref)
    if task['kind']!=FBA_TASK or task['payload']['implementation_sha256']!=recovery.implementation_fingerprint():
        raise ValueError('invalid or stale frozen FBA task')
    attempts=tasks.records(store,FBA_ATTEMPT,task_ref=task_ref)
    bindings=tasks.records(store,FBA_BINDING,task_ref=task_ref)
    returned=tasks.records(store,FBA_RETURN,task_ref=task_ref)
    if len(attempts)>1 or len(bindings)>1 or len(returned)>1:raise ValueError('conflicting direct FBA records')
    if returned:
        ref=returned[0][1]['run_ref'];result=store.get(ref)['payload']
        if not bindings or result['input_ref']!=bindings[0][1]['input_ref']:raise ValueError('FBA return binding mismatch')
        if not attempts or returned[0][1]['attempt_ref']!=attempts[0][0]:raise ValueError('FBA reservation binding mismatch')
        frozen_input=store.get(result['input_ref'])['payload']
        if frozen_input['model_ref']!=task['payload']['model_ref'] or frozen_input['settings']!=task['payload']['settings']:raise ValueError('FBA frozen input mismatch')
        fba.summary(store,ref);return ref
    if attempts and bindings:
        matches=tasks.records(store,'fba_run',input_ref=bindings[0][1]['input_ref'])
        if len(matches)==1:
            frozen_input=store.get(bindings[0][1]['input_ref'])['payload']
            if (bindings[0][1]['attempt_ref']!=attempts[0][0] or frozen_input['model_ref']!=task['payload']['model_ref']
                    or frozen_input['settings']!=task['payload']['settings']):raise ValueError('FBA frozen input mismatch')
            fba.summary(store,matches[0][0]);return matches[0][0]
    return None


def _execute_fba(store,task_ref):
    from . import fba,typed_models
    with typed_models._owner(store,task_ref):
        previous=_fba_result(store,task_ref)
        if previous:return previous
        if tasks.records(store,FBA_ATTEMPT,task_ref=task_ref):raise ValueError('direct FBA effects unconfirmed; no automatic retry')
        task=store.get(task_ref)['payload']
        attempt=store.put(FBA_ATTEMPT,dict(version=1,task_ref=task_ref),refs=(task_ref,))
        class Bound:
            def __getattr__(self,name):return getattr(store,name)
            def put(self,kind,payload,refs=None):
                ref=store.put(kind,payload,refs)
                if kind=='fba_input':store.put(FBA_BINDING,dict(version=1,task_ref=task_ref,attempt_ref=attempt,input_ref=ref),
                    refs=(task_ref,attempt,ref))
                return ref
        ref=fba.run(Bound(),task['model_ref'],task['settings'])
        store.put(FBA_RETURN,dict(version=1,task_ref=task_ref,attempt_ref=attempt,run_ref=ref),refs=(task_ref,attempt,ref))
        return ref

def facts(store,iteration_ref):
    obj=store.get(iteration_ref);p=obj['payload'];own(store,iteration_ref,ITERATION,p['project_ref'])
    application=None;comparison='not_run';source='unknown';native_calls=0
    if p['executor']=='research_task':
        s=tasks.status(store,p['task_ref']);state=s['state'];native=s.get('native_run_ref')
        if native:application=store.get(native)['payload'].get('run_ref')
        executed=bool(s.get('run_count'));native_calls=s.get('call_count',0 if not s.get('dispatch_ref') else None)
        numeric='pass' if state=='completed' and executed else 'fail' if state in ('failed','incomplete') and executed else 'not_run'
    elif p['executor']=='typed_execution':
        from . import typed_models
        s=typed_models.status(store,p['task_ref']);application=s.get('run_ref');executed=bool(s.get('execution_refs') or application)
        state='completed' if application and s['outcome']['completion_eligible'] else 'failed' if application else 'ready_to_run' if s['state']=='ready' else s['state']
        numeric='not_run'
    elif p['executor']=='fba_execution':
        s={};application=_fba_result(store,p['task_ref']);executed=bool(tasks.records(store,FBA_ATTEMPT,task_ref=p['task_ref']))
        state='completed' if application else 'unknown_effects' if executed else 'ready_to_run';numeric='not_run'
    else:
        from . import modeling_workflow
        s=modeling_workflow.status(store,p['task_ref']);runs=s['run_refs'];executed=bool(s['execution_refs'])
        if runs:
            run=modeling_workflow.load_run(store,runs[0]);application=run['application_run_ref'];state=run['state']
            numeric=run['summary']['numeric_status'];comparison=run['summary']['comparison_status']
        else:state=s['state'];numeric='not_run'
    if application and store.get(application)['kind']=='run':
        data=store.get(application)['payload'];comparison=data.get('comparison',{}).get('status','not_run')
        checks=data.get('numeric_validation',{}).get('checks',[])
        numeric='pass' if data.get('status')=='succeeded' and all(any(x.get('code')==code and x.get('status')=='pass' for x in checks) for code in ('finite','convergence')) else 'fail'
        if data.get('source_validation',{}).get('binding_status') in ('pass','matched'):source='pass'
    elif application and store.get(application)['kind']=='typed_run':
        from . import typed_models
        check=typed_models.summary(store,application)
        numeric='pass' if check['status']=='succeeded' and all(x['status']=='pass' for x in check['checks']) else 'fail'
        comparison=check['comparison']['status']
    elif application and store.get(application)['kind']=='fba_run':
        from . import fba
        check=fba.summary(store,application);numeric='pass' if check['status']=='optimal' and all(x['status']=='pass' for x in check['checks']) else 'fail'
        comparison=check['comparison']['status']
        if p['executor']=='fba_execution' and (numeric=='fail' or comparison=='fail'):state='failed'
    model_ref=p['config'].get('model_ref')
    if model_ref:
        mp=store.get(model_ref)['payload']
        if (mp.get('origin')=='agent_source_bound_project_model' and mp.get('project_ref')==p['project_ref']
                and mp.get('frame_ref')==p['frame_ref']):
            frame=own(store,p['frame_ref'],FRAME,p['project_ref'])
            check=_source_check(store,p['config']['model_type'],mp['model'],frame['spec']['evidence'],
                p['source_evidence_map'],p['source_refs'],p['stochastic_rate_units'])
            expected=check.get('binding',dict(profile='literal_boolean_declarations_v1'))
            if (check['state']=='source_bound' and mp.get('source_binding')==expected
                    and mp.get('project_ref')==p['project_ref'] and mp.get('frame_ref')==p['frame_ref']):source='pass'
    return dict(iteration_ref=iteration_ref,state=state,executed=application is not None,scientific_attempt_reserved=executed,
                native_provider_calls=native_calls,numeric_status=numeric,
                comparison_status=comparison,source_binding_status=source,application_run_ref=application,
                pending_question=s.get('pending_question'),scientific_support='unknown')


def _assess(store,project_ref,args):
    c.fields(args,{'iteration_ref','interpretation','rationale','evidence_ids'})
    view=inspect(store,project_ref)
    if args['iteration_ref'] not in view['iteration_refs']:raise ValueError('iteration outside project')
    iteration=own(store,args['iteration_ref'],ITERATION,project_ref);frame=own(store,iteration['frame_ref'],FRAME,project_ref)
    text(args['rationale']);strings(args['evidence_ids'],32,48)
    if set(args['evidence_ids'])-{e['id'] for e in frame['spec']['evidence']}:raise ValueError('assessment evidence absent from frozen frame')
    if args['interpretation'] not in ('consistent','inconsistent','insufficient','revise'):raise ValueError('invalid interpretation')
    actual=facts(store,args['iteration_ref']);checks=[]
    for criterion in iteration['criteria']:
        kind=criterion['kind']
        status=(actual['numeric_status'] if kind=='native_checks' else actual['comparison_status'] if kind=='reference_comparison' else
                actual['source_binding_status'] if kind=='source_coverage' else 'pass' if kind=='alignment' and any(
                    own(store,r,REPLY,project_ref)['question_ref']==criterion.get('question_ref') for r in view['reply_refs']) else 'unknown')
        checks.append(dict(**criterion,status=status))
    mandatory=dict(numeric=actual['numeric_status'])
    if iteration['study_mode']=='strict_reproduction':mandatory['source_binding']=actual['source_binding_status']
    if iteration.get('reference_ref'):mandatory['reference_comparison']=actual['comparison_status']
    eligible=actual['executed'] and all(v=='pass' for v in mandatory.values()) and all(v['status']=='pass' for v in checks if v['required'])
    if args['interpretation']=='consistent' and not eligible:raise ValueError('required frozen criteria have not passed')
    p=dict(version=1,project_ref=project_ref,iteration_ref=args['iteration_ref'],frame_ref=iteration['frame_ref'],
           interpretation=args['interpretation'],rationale=args['rationale'],evidence_ids=args['evidence_ids'],
           criteria=checks,mandatory_checks=mandatory,facts=actual,criteria_satisfied=eligible,authority='agent_interpretation_not_independent_review',
           scientific_support='unknown')
    return store.put(ASSESSMENT,p,refs=(project_ref,args['iteration_ref'],iteration['frame_ref'],
        *([actual['application_run_ref']] if actual['application_run_ref'] else [])))


def perform(store,project_ref,operation,args,*,provider=None,actor='agent',literature_transport=None):
    if operation in ('bind_source','open_source_iteration'):
        from .source_models import build
        required={'source_ref','model_type','reason'}|({'execution'} if operation=='open_source_iteration' else set())
        c.fields(args,required,{'reference_ref','parent_iteration_ref'} if operation=='open_source_iteration' else ())
        view=inspect(store,project_ref)
        if args['source_ref'] not in view['source_refs']:raise ValueError('literal source outside project')
        if not view['frame_ref']:raise ValueError('define a research frame first')
        frame=own(store,view['frame_ref'],FRAME,project_ref)['spec']
        if frame['mode']!='strict_reproduction':raise ValueError('Literal source construction requires an explicitly strict reproduction frame')
        candidate=build(store,args['source_ref'],args['model_type'])
        if operation=='bind_source':
            revised=deepcopy(frame);by_id={a['id']:a for a in revised['evidence']}
            for anchor in candidate['evidence']:
                if anchor['id'] in by_id and by_id[anchor['id']]!=anchor:raise ValueError('literal evidence ID collision')
                if anchor['id'] not in by_id:revised['evidence'].append(anchor)
            return dict(frame_ref=_new_frame(store,project_ref,revised,args['reason']),profile=candidate['profile'])
        if len(view['source_refs'])>8:raise ValueError('Literal iteration cannot silently truncate project source scope; use a deliberately scoped project')
        options=dict(reason=args['reason'],config=dict(model_type=args['model_type'],source_refs=view['source_refs'],execution=args['execution']))
        if args['model_type']=='ode':options['decision_plan']=candidate['decision_plan']
        else:
            options.update(native_draft=candidate['native_draft'],source_evidence_map=candidate['source_evidence_map'],stochastic_rate_units=candidate['stochastic_rate_units'])
        for key in ('reference_ref','parent_iteration_ref'):
            if key in args:options[key]=args[key]
        return dict(iteration_ref=_open_iteration(store,project_ref,options),profile=candidate['profile'])
    if operation=='admit_source':
        c.fields(args,{'source_ref','reason'})
        if actor!='operator':raise ValueError('Only an explicit operator action may admit an existing workspace source')
        text(args['reason'],2000);_refs(store,[args['source_ref']],'source',1)
        view=inspect(store,project_ref)
        if args['source_ref'] not in view['source_refs'] and len(view['source_refs'])>=32:raise ValueError('project source limit exhausted')
        return dict(source_ref=args['source_ref'],reason=args['reason'],authority='operator_explicit_source_admission')
    """Internal dispatch under project ownership and an immutable reservation."""
    view=inspect(store,project_ref)
    if operation=='revise_frame':
        c.fields(args,{'frame','reason'});return dict(frame_ref=_new_frame(store,project_ref,args['frame'],args['reason']))
    if operation=='reference_from_source':
        from .research_references import create
        return create(store,project_ref,args)
    if operation=='inspect_source':
        c.fields(args,{'source_ref','page','start','end'})
        if args['source_ref'] not in view['source_refs']:raise ValueError('source outside research scope')
        from .evidence import _source_page_text
        c.integer(args['page'],1,100000);source=_source_page_text(store,args['source_ref'],args['page'])
        from .diagnostics import validate_source_span
        validate_source_span(args['source_ref'],args['page'],args['start'],args['end'],len(source))
        # Read ledger: repeated identical spans are the observed stall pattern
        # (the same 0-8000 page window was re-read four times in one live run).
        # The text stays available, but the result states that nothing new was
        # learned and asks for a frame/alignment instead of another read.
        prior, spans = 0, []
        attempts_before, returns_before, _ = history(store, project_ref)
        for _ref, attempt in attempts_before:
            if _ref not in returns_before or attempt.get('operation') != 'inspect_source':
                continue
            earlier = attempt.get('args') or {}
            if (earlier.get('source_ref') == args['source_ref']
                    and earlier.get('page') == args['page']):
                spans.append((earlier.get('start'), earlier.get('end')))
        identical = [(s0, e0) for s0, e0 in spans
                     if s0 == args['start'] and e0 == args['end']]
        start, end = args['start'], args['end']
        covered = sum(max(0, min(end, e0) - max(start, s0))
                      for s0, e0 in spans if s0 is not None and e0 is not None)
        new_characters = max(0, (end - start) - min(end - start, covered))
        result = dict(**args, text=source[start:end], full_page_characters=len(source),
                      read_ledger=dict(reads_of_this_page=len(spans) + 1,
                                       prior_span_occurrences=len(identical),
                                       new_characters=new_characters,
                                       repeat_read=bool(identical)))
        if identical:
            result['diagnostic'] = (
                'This exact span was already read (%d prior occurrence(s)). No new '
                'characters were added. Do not call inspect_source for this span again: '
                'use the retained text, submit the target frame, or ask a precise '
                'alignment question about what is missing (parameters, initial '
                'conditions, units, supplement).' % len(identical))
            if len(identical) >= 2:
                result['read_ledger']['repeated_read_blocked_recommended'] = True
        return result
    if operation=='inspect_transcription':
        # Segment-23 reachability repair: the latest-state projection windows
        # the retained transcription texts, so once more than a few receipts
        # exist (or a bounded tool result ages out of the packet) an older
        # paid-for transcription could otherwise only come back by spending a
        # new vision call. This deterministic read returns the EXACT retained
        # text window of one project-owned receipt at no literature or
        # transcription budget cost and never replays the vision request.
        # Authority stays agent-assessed; the literal source page text remains
        # the only strict declaration evidence.
        c.fields(args,{'transcription_ref'},{'start','end'})
        from . import research_literature as literature
        record=own(store,args['transcription_ref'],literature.TRANSCRIPTION,project_ref)
        if record['source_ref'] not in view['source_refs']:
            raise ValueError('transcription source outside research scope')
        stored=record['raw_output'];bound=literature.MAX_TRANSCRIPTION_OUTPUT_CHARS
        start=c.integer(args.get('start',0),0,len(stored),'start')
        end=c.integer(args.get('end',min(len(stored),start+bound)),start,len(stored),'end')
        if end-start>bound:raise ValueError(f'transcription read exceeds {bound} characters')
        return dict(transcription_ref=args['transcription_ref'],source_ref=record['source_ref'],
            asset_ref=record['asset_ref'],asset=record['asset'],status=record['status'],
            finish_reason=record['finish_reason'],output_token_limit=record['output_token_limit'],
            start=start,end=end,stored_characters=len(stored),
            output_characters=record['output_characters'],output_truncated=record['output_truncated'],
            transcription=stored[start:end],next_start=end if end<len(stored) else None,
            output_authority=record['output_authority'],
            notice='Bounded retained window of the recorded vision reply, verbatim from the receipt: '
                   'agent-assessed input, never independent verification and never literal page text; '
                   'the cited source page stays the authority and scientific_support remains unknown.')
    if operation=='search_sources':
        c.fields(args,{'query'});text(args['query'],256)
        from .retrieval import search_sources
        return search_sources(store,view['source_refs'],args['query'])
    if operation in ('search_literature','fetch_literature','fetch_literature_asset','transcribe_literature_asset'):
        from . import research_literature
        return research_literature.perform(store,project_ref,operation,args,transport=literature_transport,
                                           provider=provider)
    if operation in ('start_expt_reading','record_expt_chunk','inspect_expt_reading'):
        from . import research_reading
        return research_reading.perform(store,project_ref,operation,args)
    if operation=='inspect_research_guidance':
        from . import research_guidance
        return research_guidance.perform(store,project_ref,args)
    if operation in ('index_mm_background','inspect_mm_background','review_expt_background','inspect_background_review'):
        from . import research_background
        return research_background.perform(store,project_ref,operation,args)
    if operation=='open_iteration':return dict(iteration_ref=_open_iteration(store,project_ref,args))
    if operation=='inspect_iteration':
        c.fields(args,{'iteration_ref'},{'path','start','end'})
        if args['iteration_ref'] not in view['iteration_refs']:raise ValueError('iteration outside project')
        actual=facts(store,args['iteration_ref']);ref=actual['application_run_ref']
        if ref is None:return dict(facts=actual,data=None,notice='No scientific output has been saved.')
        obj=store.get(ref);value=obj['payload'];path=args.get('path',[])
        if obj['kind']=='typed_run':
            from . import typed_models
            inp,model,result=typed_models.result(store,ref)
            value=dict(model=model,settings=inp['settings'],result=result,verification=typed_models.summary(store,ref))
        elif obj['kind']=='fba_run':
            from . import fba
            value=dict(model=fba.load(store,value['model_ref']),result=value['result'],verification=fba.summary(store,ref))
        if not isinstance(path,list) or len(path)>8:raise ValueError('inspection path exceeds 8 steps')
        for key in path:
            if isinstance(value,dict) and isinstance(key,str):value=value[key]
            elif isinstance(value,list) and type(key) is int and 0<=key<len(value):value=value[key]
            else:raise ValueError('path does not address scientific output')
        if isinstance(value,list):
            start=c.integer(args.get('start',0),0,len(value));end=c.integer(args.get('end',min(len(value),start+32)),start,len(value))
            if end-start>64:raise ValueError('inspect at most 64 array entries')
            total=len(value);value=value[start:end]
        else:start=end=total=None
        def preview(item,width,depth=0):
            if depth>=6:return dict(omitted=True,type=type(item).__name__)
            if isinstance(item,dict):return {k:preview(v,width,depth+1) for k,v in list(item.items())[:32]}
            if isinstance(item,list):return dict(length=len(item),head=[preview(v,width,depth+1) for v in item[:width]],
                tail=[preview(v,width,depth+1) for v in item[-width:]] if len(item)>width else [],truncated=len(item)>width)
            return item[:1000] if isinstance(item,str) else item
        exact=len(canonical_bytes(value))<=16000
        width=4;data=value if exact else preview(value,width)
        while len(canonical_bytes(data))>16000 and width>1:width-=1;data=preview(value,width)
        if len(canonical_bytes(data))>16000:data=dict(omitted=True,notice='Select a narrower scientific-output path.')
        return dict(application_run_ref=ref,path=path,start=start,end=end,total_entries=total,preview=data,preview_truncated=not exact,
                    notice='Bounded preview only. Inspect relevant paths/windows before claiming a trend or comparison.')
    if operation in ('run_iteration','resume_iteration'):
        c.fields(args,{'iteration_ref','reply_ref'} if operation=='resume_iteration' else {'iteration_ref'})
        if args['iteration_ref'] not in view['iteration_refs']:raise ValueError('iteration outside project')
        p=own(store,args['iteration_ref'],ITERATION,project_ref)
        if p['executor']=='research_task':
            if operation=='run_iteration':result=tasks.execute(store,provider,p['task_ref'])
            else:
                if args['reply_ref'] not in view['reply_refs']:raise ValueError('native reply needs attributed researcher input')
                reply=own(store,args['reply_ref'],REPLY,project_ref)
                question=own(store,reply['question_ref'],QUESTION,project_ref)
                if args['iteration_ref'] not in question['affected_fields']:raise ValueError('reply is not aligned to this iteration')
                result=tasks.resume(store,provider,p['task_ref'],reply['reply'],respondent=reply['respondent'])
        elif p['executor']=='typed_execution':
            if operation!='run_iteration':raise ValueError('deterministic execution does not accept a native-agent reply')
            from .typed_models import execute
            result=execute(store,p['task_ref'])
        elif p['executor']=='fba_execution':
            if operation!='run_iteration':raise ValueError('deterministic execution does not accept a native-agent reply')
            result=_execute_fba(store,p['task_ref'])
        else:
            if operation!='run_iteration':raise ValueError('deterministic decision plan cannot take a native-agent reply')
            from .modeling_workflow import execute
            result=execute(store,p['task_ref'])
        return dict(result_ref=result,iteration_ref=args['iteration_ref'])
    if operation=='assess_iteration':return dict(assessment_ref=_assess(store,project_ref,args))
    if operation=='ask_alignment':
        c.fields(args,{'category','question','affected_fields'},{'options'})
        if args['category'] not in ('objective','granularity','data_access','conflicting_evidence','unsupported_semantics'):
            raise ValueError('invalid alignment category')
        text(args['question']);strings(args['affected_fields'],8,200);strings(args.get('options',[]),4,1000)
        p=dict(version=1,project_ref=project_ref,frame_ref=view['frame_ref'],**deepcopy(args))
        return dict(question_ref=store.put(QUESTION,p,refs=(project_ref,*([view['frame_ref']] if view['frame_ref'] else []))))
    if operation=='reply_alignment':
        if actor=='agent':raise ValueError('the agent cannot impersonate the researcher')
        c.fields(args,{'question_ref','reply','respondent'})
        if args['question_ref'] not in view['pending_questions']:raise ValueError('alignment already answered or outside project')
        text(args['reply']);text(args['respondent'],200)
        p=dict(version=1,project_ref=project_ref,**args,authority='attributed_researcher_input')
        return dict(reply_ref=store.put(REPLY,p,refs=(project_ref,args['question_ref'])))
    if operation=='reply_source':
        c.fields(args,{'reply_ref'})
        if args['reply_ref'] not in view['reply_refs']:raise ValueError('reply outside project')
        reply=own(store,args['reply_ref'],REPLY,project_ref);raw=reply['reply'].encode();blob=store.put_blob(raw)
        source=store.put('source',dict(blob_ref=blob,filename='attributed-researcher-reply.txt',parser='utf8',parser_version='1',
            url=None,pages=[dict(number=1,text=reply['reply'])],warnings=['Attributed researcher input, not a published fact.'],
            reply_ref=args['reply_ref'],research_input_role='researcher_reply'),refs=(blob,args['reply_ref']))
        return dict(source_ref=source)
    if operation=='scenario_source':
        c.fields(args,())
        scenario=load(store,project_ref)['scenario'];blob=store.put_blob(scenario.encode())
        ref=store.put('source',dict(blob_ref=blob,filename='human-application-scenario.txt',parser='utf8',parser_version='1',
            url=None,pages=[dict(number=1,text=scenario)],warnings=['Human scenario, not empirical or reported-model evidence.'],
            research_input_role='scenario',project_ref=project_ref),refs=(project_ref,blob))
        return dict(source_ref=ref)
    if operation=='conclude':
        c.fields(args,{'outcome','summary','assessment_refs','limitations'})
        if args['outcome'] not in TERMINAL:raise ValueError('invalid research conclusion')
        text(args['summary']);strings(args['limitations'],16)
        if not isinstance(args['assessment_refs'],list) or set(args['assessment_refs'])-set(view['assessment_refs']):
            raise ValueError('conclusion cites foreign assessments')
        if args['outcome']=='completed_with_limits':
            if not args['assessment_refs'] or not any(own(store,r,ASSESSMENT,project_ref)['criteria_satisfied']
                    and own(store,r,ASSESSMENT,project_ref)['frame_ref']==view['frame_ref'] for r in args['assessment_refs']):
                raise ValueError('completion requires an actually satisfied assessment')
        p=dict(version=1,project_ref=project_ref,**deepcopy(args),authority='agent_research_summary',scientific_support='unknown')
        return dict(conclusion_ref=store.put(CONCLUSION,p,refs=(project_ref,*args['assessment_refs'])))
    raise ValueError('unsupported research action')


def operate(store,project_ref,operation,args,*,key=None,provider=None,actor='agent',literature_transport=None,response_ref=None):
    if operation not in OPERATIONS or operation in ('respond','invalid_response'):raise ValueError('invalid direct action')
    project=load(store,project_ref)
    with tasks.owner(store,project['id']):
        return _operate_owned(store,project_ref,operation,args,key=key,provider=provider,actor=actor,
                              literature_transport=literature_transport,response_ref=response_ref)


def _operate_owned(store,project_ref,operation,args,*,key=None,provider=None,actor='agent',
                   literature_transport=None,callback=None,response_ref=None):
    project=load(store,project_ref);key=key or uuid4().hex;text(key,100)
    signature=hashlib.sha256(canonical_bytes(dict(operation=operation,args=args,actor=actor,response_ref=response_ref))).hexdigest()
    attempts,returned,pending=history(store,project_ref)
    existing=[(r,p) for r,p in attempts if p['key']==key]
    if existing:
        ref,p=existing[0]
        if p['signature']!=signature:raise ValueError('operation key reused with different inputs')
        if ref in returned and returned[ref][1]['state']=='returned':return returned[ref][1]['result']
        raise ValueError('operation already attempted; inspect its saved outcome without replay')
    view=inspect(store,project_ref)
    from .research_control import checkpoint
    checkpoint()
    if pending:raise ValueError('an operation outcome is unknown; no automatic replay')
    if operation=='respond':
        from .research_output_budget import validate_provider
        validate_provider(store,project_ref,args.get('provider'))
    if view['state'] in TERMINAL:raise ValueError('project has concluded')
    if view['pending_questions'] and operation!='reply_alignment':raise ValueError('researcher alignment is pending')
    if len(attempts)>=200:raise ValueError('research operation limit exceeded')
    if response_ref is not None:
        response=own(store,response_ref,RESPONSE,project_ref)
        if any(p.get('response_ref')==response_ref for _,p in attempts):raise ValueError('planner response already consumed')
        if operation!='invalid_response' and canonical_bytes(normalize_action(response['response']))!=canonical_bytes(dict(action=operation,args=args)):
            raise ValueError('action differs from the saved planner response')
    if operation in ('run_iteration','resume_iteration'):
        c.fields(args,{'iteration_ref','reply_ref'} if operation=='resume_iteration' else {'iteration_ref'})
        if args['iteration_ref'] not in view['iteration_refs']:raise ValueError('iteration outside project')
        iteration=own(store,args['iteration_ref'],ITERATION,project_ref)
        if operation=='resume_iteration':
            if iteration['executor']!='research_task' or args['reply_ref'] not in view['reply_refs']:raise ValueError('resume requires a paused native agent and attributed reply')
            reply=own(store,args['reply_ref'],REPLY,project_ref);question=own(store,reply['question_ref'],QUESTION,project_ref)
            if args['iteration_ref'] not in question['affected_fields']:raise ValueError('reply is not aligned to this iteration')
            if tasks.status(store,iteration['task_ref'])['state']!='awaiting_input':raise ValueError('native agent is not awaiting input')
            if provider is None:raise ValueError('native provider required for continuation')
        elif iteration['executor']=='research_task':
            native=tasks.status(store,iteration['task_ref'])
            if native['state']=='ready_to_run' and provider is None:raise ValueError('native provider required')
            if native['state'] in ('needs_recovery','unknown_effects'):raise ValueError('inspect native recovery; do not restart')
    if operation in ('search_literature','fetch_literature','fetch_literature_asset','transcribe_literature_asset'):
        from .research_literature import validate
        validate(store,project_ref,operation,args)
    budget=view['budget'];limits=effective_policy(project['policy'])
    if operation=='respond' and budget['planner_calls']>=limits['planner_max_calls']:raise ValueError('planner call budget exhausted')
    if operation in ('search_literature','fetch_literature','fetch_literature_asset') and budget['literature_requests']>=limits['max_literature_requests']:
        raise ValueError('literature request budget exhausted')
    if operation=='transcribe_literature_asset' and budget['transcription_calls']>=limits['max_transcription_calls']:
        raise ValueError('transcription call budget exhausted; keep the [unreadable-formula:...] markers '
                         'unresolved, record the remaining gap in the frame or ask_alignment, and never '
                         'invent formula content')
    allocation=0
    if operation=='open_iteration':
        if not isinstance(args,dict) or not isinstance(args.get('config'),dict):raise ValueError('iteration needs config')
        needs_native_agent=args['config'].get('model_type')=='ode' or args.get('native_agent',False)
        allocation=0 if args.get('decision_plan') is not None or not needs_native_agent else args['config'].get('max_calls',limits['native_calls_per_iteration'])
        c.integer(allocation,0,limits['native_calls_per_iteration'])
        if budget['native_reserved']+allocation>limits['max_native_calls']:raise ValueError('project native budget exhausted')
    attempt=store.put(STEP,dict(version=1,project_ref=project_ref,sequence=len(attempts),key=key,operation=operation,
        signature=signature,args=deepcopy(args),actor=actor,native_allocation=allocation,response_ref=response_ref),
        refs=(project_ref,*([response_ref] if response_ref else [])))
    started=research_usage.monotonic()
    try:
        result=callback(attempt) if callback is not None else perform(store,project_ref,operation,args,
            provider=provider,actor=actor,literature_transport=literature_transport)
    except Exception as exc:
        failure_ref=getattr(exc,'project_failure_ref',None)
        failure=None
        if failure_ref:
            if operation!='respond':raise ValueError('provider failure receipt binding mismatch') from None
            failure=own(store,failure_ref,PROVIDER_FAILURE,project_ref)
            if (failure['attempt_ref']!=attempt or failure['request_ref']!=args['request_ref']
                    or failure['provider']!=args.get('provider')):
                raise ValueError('provider failure receipt binding mismatch') from None
            if failure.get('output_budget_verified') is True:
                from .research_output_budget import failure_closure
                failure_closure(store,project_ref,args,attempt,failure_ref)
            if failure['error_kind']=='model_response' and failure.get('model_content_json_blob_ref'):
                # A received-but-refused answer is only classified as such
                # under the same full binding closure that recovery and
                # history re-reads verify.
                try:_provider_failure_closure(store,project_ref,args,attempt,failure_ref)
                except ValueError:raise ValueError('provider failure receipt binding mismatch') from None
        confirmed=getattr(exc,'receipt_ref',None)
        observed=None
        if confirmed:
            receipt=store.get(confirmed)
            if receipt['kind'] not in ('research_literature_request','research_asset_transcription_request') or receipt['payload'].get('project_ref')!=project_ref:confirmed=None
            elif (receipt['kind']=='research_asset_transcription_request'
                    and receipt['payload'].get('response_received') is False):
                # A request went out and no response was confirmed: the
                # effect is unknown, so this attempt must NOT be recorded as
                # a confirmed rejection.  The receipt still belongs to THIS
                # attempt and stays referenced by the saved return below, so
                # the diagnosis is never orphaned; automatic replay remains
                # impossible (the project freezes for inspection).
                observed=confirmed;confirmed=None
        state='rejected' if confirmed or operation not in ('respond','run_iteration','resume_iteration','search_literature','fetch_literature','fetch_literature_asset','transcribe_literature_asset') else 'unknown'
        diagnostic=dict(stage=operation,code=type(exc).__name__,message=str(exc)[:800]) if state=='rejected' else None
        citation=getattr(exc,'citation_diagnostic',None)
        if diagnostic is not None and citation is not None:diagnostic['citation']=citation
        tool_diagnostic=getattr(exc,'tool_diagnostic',None)
        if diagnostic is not None and tool_diagnostic is not None:diagnostic['tool_diagnostic']=tool_diagnostic
        binding=getattr(exc,'source_binding_diagnostic',None)
        if diagnostic is not None and binding is not None:diagnostic['source_binding']=binding
        compilation=getattr(exc,'compilation_diagnostic',None)
        compilation_refs=()
        if diagnostic is not None and compilation is not None:
            diagnostic['compilation']=compilation
            compilation_refs=_compilation_evidence_refs(store,compilation)
        if failure:
            if failure.get('output_budget_verified') is True:
                state='rejected'
                diagnostic=dict(stage=operation,code='output_budget',
                    message='The provider finished at its output cap without an executable action. '
                            'Paused until an explicit higher-cap plan within remaining calls; no automatic retry.',
                    response=failure['response_diagnostic'])
            elif failure['error_kind']=='model_response' and failure.get('model_content_json_blob_ref'):
                # The provider returned content and the extracted message is
                # retained, but it was refused as a malformed planner action
                # before any tool ran. Record it as received-but-rejected with
                # sanitized format feedback so a bounded correction can follow.
                state='rejected'
                diagnostic=dict(stage=operation,code=failure['error_kind'],
                    message='A provider response was received but refused before any tool executed: it is not exactly one valid planner action.'
                            ' Reply with exactly one JSON object {"action": "<name>", "args": {...}} and no other text or tool-call markup.',
                    response=failure['response_diagnostic'])
                if isinstance(exc,ModelResponseError):diagnostic['parser']=str(exc)[:300]
            else:
                diagnostic=dict(stage=operation,code=failure['error_kind'],message='Provider request failed; inspect the saved diagnostic without resending.',
                                response=failure['response_diagnostic'])
        elapsed=research_usage.elapsed_since(started)
        store.put(RETURN,dict(version=1,project_ref=project_ref,attempt_ref=attempt,state=state,result=None,
                            error_type=type(exc).__name__,diagnostic=diagnostic,
                            **({'elapsed_s':elapsed} if elapsed is not None else {}),
                            **({'provider_failure_ref':failure_ref} if failure_ref else {})),
                  refs=(project_ref,attempt,*([confirmed] if confirmed else []),*([observed] if observed else []),
                        *([failure_ref] if failure_ref else []),*compilation_refs))
        raise
    refs=[]
    if isinstance(result,dict):
        for name,value in result.items():
            if name.endswith('_ref') and isinstance(value,str):
                store.get(value);refs.append(value)
    elapsed=research_usage.elapsed_since(started)
    store.put(RETURN,dict(version=1,project_ref=project_ref,attempt_ref=attempt,state='returned',result=deepcopy(result),
                        **({'elapsed_s':elapsed} if elapsed is not None else {})),
              refs=(project_ref,attempt,*refs))
    return result


def _recovery_provenance(project,payload,historical):
    """Recovery provenance for an offline cross-source-version claim: keep the
    frozen project's original implementation fingerprint next to the
    fingerprint of the code performing this bounded normalization, and record
    that the compatibility never widens beyond provider receipt adoption."""
    if not historical:return payload
    return dict(**payload,implementation_sha256=project['implementation_sha256'],
        recovery_implementation_sha256=recovery.implementation_fingerprint(),
        compatibility='offline_provider_receipt_recovery_only')


def recover(store,project_ref,attempt_ref):
    """Adopt a bound saved planner response; never resend an unknown request.

    A project frozen by an older source version can still return its offline
    provider receipt through the explicit historical read: that read is used
    only to classify one unique respond-attempt failure, never for native or
    scientific operations, which stay gated on the original implementation
    version. An expired deadline does not block the offline claim, and the
    claim never extends the deadline or resets budgets.
    """
    historical=False
    try:project=load(store,project_ref)
    except ValueError as exc:
        if str(exc)!='project implementation changed; retain historical version':raise
        historical=True
    project=load(store,project_ref,historical=historical)
    with tasks.owner(store,project['id']):
        attempts,returned,pending=history(store,project_ref,historical=historical)
        if attempt_ref not in dict(attempts):raise ValueError('operation outside project')
        attempt=dict(attempts)[attempt_ref]
        if historical and attempt['operation']!='respond':
            raise ValueError('historical recovery only adopts provider respond receipts;'
                             ' scientific execution stays gated on the original version')
        if attempt_ref not in pending:
            if attempt_ref in returned:return returned[attempt_ref][1]['result']
            raise ValueError('operation is not recoverable')
        if attempt['operation'] in ('run_iteration','resume_iteration'):
            iteration=own(store,attempt['args']['iteration_ref'],ITERATION,project_ref)
            if iteration['executor']=='research_task':
                view=tasks.status(store,iteration['task_ref'])
                if not view.get('native_run_ref'):raise ValueError('no confirmed native result; inspect the child protocol without retry')
                ref=tasks.recover(store,None,iteration['task_ref'])
            elif iteration['executor']=='typed_execution':
                from . import typed_models
                view=typed_models.status(store,iteration['task_ref'])
                if not view.get('run_ref'):raise ValueError('no confirmed typed result; do not retry')
                ref=view['run_ref'];typed_models.summary(store,ref)
            elif iteration['executor']=='fba_execution':
                ref=_fba_result(store,iteration['task_ref'])
                if ref is None:raise ValueError('no confirmed FBA result; do not retry')
            else:
                from . import modeling_workflow
                view=modeling_workflow.status(store,iteration['task_ref'])
                if view['state'] not in ('result_recorded','confirmed_receipt_available'):raise ValueError('no confirmed numerical receipt; do not retry')
                ref=modeling_workflow.execute(store,iteration['task_ref'])
            result=_confirmed_native_result(store,project_ref,attempt,ref)
            store.put(RECOVERY,dict(version=1,project_ref=project_ref,attempt_ref=attempt_ref,state='returned',
                confirmation_ref=ref,result=result),refs=(project_ref,attempt_ref,ref))
            return result
        if attempt['operation']!='respond':raise ValueError('inspect the saved receipt; unknown external effects are not retried')
        receipts=tasks.records(store,RESPONSE,attempt_ref=attempt_ref,project_ref=project_ref)
        if len(receipts)>1:raise ValueError('no unique confirmed planner response; do not resend')
        if not receipts or receipts[0][1].get('source')=='provider_failure_content':
            existing_response_ref=receipts[0][0] if receipts else None
            recovered=_recover_provider_failure(store,project_ref,attempt,attempt_ref,returned,
                                                response_ref=existing_response_ref)
            if recovered is None:raise ValueError('no unique confirmed planner response; do not resend')
            state,confirmation,result,extra_refs,diagnostic=recovered
            payload=dict(version=1,project_ref=project_ref,attempt_ref=attempt_ref,state=state,
                confirmation_ref=confirmation,result=result)
            if diagnostic is not None:
                payload['diagnostic']=diagnostic;payload['provider_failure_ref']=confirmation
            store.put(RECOVERY,_recovery_provenance(project,payload,historical),refs=(project_ref,attempt_ref,confirmation,*extra_refs))
            return result
        response_ref,p=receipts[0]
        if p['scope_sha256']!=attempt['args']['scope_sha256'] or p['provider']!=attempt['args']['provider']:
            raise ValueError('saved response scope/provider mismatch')
        result=dict(response_ref=response_ref,response=p['response'],usage=p['usage'])
        payload=dict(version=1,project_ref=project_ref,attempt_ref=attempt_ref,state='returned',
                              confirmation_ref=response_ref,result=result)
        store.put(RECOVERY,_recovery_provenance(project,payload,historical),refs=(project_ref,attempt_ref,response_ref))
        return result


def _compilation_evidence_refs(store,diagnostic):
    """Verified compilation evidence for a rejected decision plan: the saved
    compilation receipt and its underlying plan join the RETURN ref closure so
    snapshot exports retain the exact blocking findings and their source
    evidence.  Only refs that actually verify in the store are included."""
    try:
        compilation_ref=diagnostic.get('compilation_ref');plan_ref=diagnostic.get('plan_ref')
        if not isinstance(compilation_ref,str) or not isinstance(plan_ref,str):return ()
        obj=store.get(compilation_ref)
        if obj['kind']!='modeling_plan_compilation' or obj['payload'].get('plan_ref')!=plan_ref:
            return ()
        return (compilation_ref,plan_ref) if plan_ref in set(obj['refs']) else ()
    except ValueError:return ()


def _recover_provider_failure(store,project_ref,attempt,attempt_ref,returned,*,response_ref=None):
    """Classify a legacy received-but-unparsed planner response without any
    network call, extra planner call or tool replay.

    The single unique failure receipt must pass the full binding closure for
    this attempt (ownership, original request/prompt scope and provider, ref
    closure over project/attempt/request/blob) and the attempt's original
    unknown RETURN must reference exactly that failure.  When the strictly
    parsed raw text normalizes (e.g. DSML), a derived structured RESPONSE with
    explicit provenance is returned through the existing confirmed-response
    adoption path, genuinely linked to the original failure ref and keeping
    the originally reported usage counted once.  A still-unparseable complete
    answer is recorded as an explicit received-but-rejected recovery so the
    project receives format feedback and can advance within its remaining
    budget.  Any tampered, ambiguous or unconfirmed binding returns None and
    the unknown outcome keeps blocking automatic replay.
    """
    failures=tasks.records(store,PROVIDER_FAILURE,attempt_ref=attempt_ref,project_ref=project_ref)
    if len(failures)!=1:return None
    failure_ref,failure=failures[0]
    try:failure,blob_ref=_provider_failure_closure(store,project_ref,attempt['args'],attempt_ref,failure_ref)
    except ValueError:return None
    original=returned.get(attempt_ref)
    if (original is None or original[1].get('provider_failure_ref')!=failure_ref
            or failure_ref not in set(store.get(original[0])['refs'])):return None
    try:raw=json.loads(store.get_blob(blob_ref).decode('ascii'))
    except (ValueError,UnicodeDecodeError):
        raise ValueError('provider failure content blob is not a readable JSON string') from None
    if not isinstance(raw,str) or len(raw)>128000:
        raise ValueError('provider failure content blob is not a readable JSON string') from None
    model=failure['provider'].get('model') if isinstance(failure['provider'],dict) else ''
    try:response=parse_response_action(raw,model=model)
    except ModelResponseError:
        if response_ref is not None:raise ValueError('saved normalized response differs from retained content') from None
        feedback=dict(stage='respond',code='model_response',
            message='A previously received provider response was retained but remains refused: it is not exactly one valid planner action.'
                    ' Reply with exactly one JSON object {"action": "<name>", "args": {...}} and no other text or tool-call markup.',
            response=failure.get('response_diagnostic'))
        return ('rejected',failure_ref,None,(),feedback)
    usage=failure.get('usage') if isinstance(failure.get('usage'),dict) else {}
    derived=dict(version=1,project_ref=project_ref,attempt_ref=attempt_ref,
        request_ref=attempt['args']['request_ref'],scope_sha256=attempt['args']['scope_sha256'],
        provider=attempt['args']['provider'],response=deepcopy(response),usage=usage,
        source='provider_failure_content',provider_failure_ref=failure_ref,
        model_content_json_blob_ref=blob_ref,
        model_content_authority='untrusted_model_output_not_an_action')
    elapsed=failure.get('elapsed_s')
    if type(elapsed) in (int,float) and math.isfinite(elapsed) and elapsed>=0:derived['elapsed_s']=elapsed
    refs=(project_ref,attempt_ref,attempt['args']['request_ref'],blob_ref,failure_ref)
    if response_ref is None:
        response_ref=store.put(RESPONSE,derived,refs=refs)
    else:
        saved=store.get(response_ref)
        if saved['kind']!=RESPONSE or saved['payload']!=derived or set(saved['refs'])!=set(refs):
            raise ValueError('saved normalized response differs from retained content')
    return ('returned',response_ref,
            dict(response_ref=response_ref,response=deepcopy(response),usage=usage,provider_failure_ref=failure_ref),
            (blob_ref,failure_ref),None)


def _confirmed_native_result(store,project_ref,attempt,result_ref):
    iteration_ref=attempt['args']['iteration_ref'];iteration=own(store,iteration_ref,ITERATION,project_ref)
    if iteration['executor']=='research_task':
        run=tasks.result(store,result_ref)
        if run['request_ref']!=iteration['task_ref']:raise ValueError('native return belongs to another iteration')
    elif iteration['executor']=='typed_execution':
        from . import typed_models
        typed_models.summary(store,result_ref)
        if store.get(result_ref)['payload']['input_ref']!=iteration['task_ref']:raise ValueError('typed result belongs to another iteration')
    elif iteration['executor']=='fba_execution':
        if _fba_result(store,iteration['task_ref'])!=result_ref:raise ValueError('FBA result belongs to another iteration')
    else:
        from .modeling_workflow import load_run
        if load_run(store,result_ref)['task_ref']!=iteration['task_ref']:raise ValueError('numerical return belongs to another iteration')
    return dict(result_ref=result_ref,iteration_ref=iteration_ref)


def snapshot(store,project_ref):
    view=inspect(store,project_ref);attempts,returns,_=history(store,project_ref)
    view['iteration_facts']={r:facts(store,r) for r in view['iteration_refs']}
    from .research_supervisor import usage
    view['planner_usage']=usage(store,project_ref)
    refs=[project_ref,*[r for r,_ in attempts],*[r for r,_ in rows(store,RETURN,project_ref)],
          *[r for r,_ in rows(store,RECOVERY,project_ref)],*[r for r,_ in rows(store,REFERENCE,project_ref)]]
    for iteration_ref in view['iteration_refs']:
        iteration=own(store,iteration_ref,ITERATION,project_ref);task_ref=iteration['task_ref']
        if iteration['executor']=='typed_execution':
            for kind in ('typed_execution','typed_execution_receipt','typed_run'):
                refs.extend(r for r,_ in tasks.records(store,kind,input_ref=task_ref))
        elif iteration['executor']=='fba_execution':
            for kind in (FBA_ATTEMPT,FBA_BINDING,FBA_RETURN):refs.extend(r for r,_ in tasks.records(store,kind,task_ref=task_ref))
            for _,binding in tasks.records(store,FBA_BINDING,task_ref=task_ref):
                for kind in ('fba_execution','fba_execution_receipt','fba_run'):
                    refs.extend(r for r,_ in tasks.records(store,kind,input_ref=binding['input_ref']))
        elif iteration['executor']=='decision_plan':
            from . import modeling_workflow
            for kind in (modeling_workflow.EXECUTION,modeling_workflow.RECEIPT,modeling_workflow.RUN):
                refs.extend(r for r,_ in tasks.records(store,kind,task_ref=task_ref))
        else:
            request=tasks.load(store,task_ref)
            refs.extend(r for r,_ in tasks.records(store,tasks.EXECUTION,family_id=request['family_id']))
            bindings=tasks.records(store,tasks.BINDING,request_ref=task_ref);refs.extend(r for r,_ in bindings)
            for _,binding in bindings:
                child=binding['native_task_ref'];refs.append(child)
                for execution_ref,_ in tasks.records(store,'agent_execution',task_ref=child):
                    refs.append(execution_ref)
                    refs.extend(r for r,_ in tasks.records(store,'agent_invocation',execution_ref=execution_ref))
            status=tasks.status(store,task_ref)
            for key in ('native_run_ref','result_ref'):
                if status.get(key):refs.append(status[key])
    from .research_output_budget import PLAN as output_budget_plan
    refs.extend(r for r,_ in rows(store,output_budget_plan,project_ref))
    refs.extend(view['control']['stop_refs'])
    refs.extend(view['planner_usage']['native_usage']['call_refs'])
    refs.extend(view['planner_usage']['native_usage']['return_refs'])
    return store.put(SNAPSHOT,dict(version=1,project_ref=project_ref,view=view),refs=tuple(dict.fromkeys(refs)))


def report_lines(store,ref):
    obj=store.get(ref)
    if obj['kind']==SNAPSHOT:view=obj['payload']['view'];project_ref=obj['payload']['project_ref']
    else:project_ref=ref;view=inspect(store,ref)
    p=load(store,project_ref)
    lines=['Research project: '+p['scenario'],'State: **'+view['state']+'**',
           'Scientific support: **unknown**. Agent interpretations are not independent review.',
           '', '## Research budget',json.dumps(view['budget'],ensure_ascii=False,indent=2)]
    if view['frame_ref']:
        lines+=['','## Agent research frame','~~~json',json.dumps(own(store,view['frame_ref'],FRAME,project_ref),ensure_ascii=False,indent=2),'~~~']
    for iteration_ref in view['iteration_refs']:
        iteration=own(store,iteration_ref,ITERATION,project_ref)
        lines+=['','## Iteration '+str(iteration['index']),'Reason: '+iteration['reason'],
                'Study mode: '+iteration['study_mode'],'Parent: '+str(iteration['parent_iteration_ref']),
                '~~~json',json.dumps(view.get('iteration_facts',{}).get(iteration_ref) or facts(store,iteration_ref),ensure_ascii=False,indent=2),'~~~']
    for assessment_ref in view['assessment_refs']:
        lines+=['','## Agent assessment','~~~json',json.dumps(own(store,assessment_ref,ASSESSMENT,project_ref),ensure_ascii=False,indent=2),'~~~']
    for reply_ref in view['reply_refs']:
        lines+=['','## Attributed researcher reply','~~~json',json.dumps(own(store,reply_ref,REPLY,project_ref),ensure_ascii=False,indent=2),'~~~']
    if view['conclusion_ref']:
        lines+=['','## Bounded conclusion','~~~json',json.dumps(own(store,view['conclusion_ref'],CONCLUSION,project_ref),ensure_ascii=False,indent=2),'~~~']
    return lines



