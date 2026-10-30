"""Unified task intake and lifecycle over the six existing native workflows.

The request describes intent and required inputs, not a common mathematical
language. Type selection remains explicit. One local family dispatches exactly
one native Agent task; revisions and index deletion cannot reset that choice.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
import re
from uuid import uuid4

from . import context, model_contracts as contracts, recovery, typed_models
from .domain.contracts import ExperimentSpec, ObservationSpec, SimulationResult, SolverConfig
from .domain.serialization import from_dict, to_dict
from .storage import canonical_bytes, REF_RE

REQUEST='research_task'
EXECUTION='research_task_execution'
BINDING='research_task_binding'
RUN='research_task_run'
TYPES=('ode','fba',*typed_models.TYPES)


def _normalize(store,config):
    contracts.fields(config,{'schema_version','goal'},
        {'model_type','source_refs','model_ref','execution','max_calls','context_max_chars','modeling_context','proposal_max_calls'})
    if type(config['schema_version']) is not int or config['schema_version']!=1:
        raise ValueError('unsupported research task schema')
    goal=config['goal']
    if not isinstance(goal,str) or not goal.strip() or len(goal)>4000:
        raise ValueError('goal needs 1..4000 characters')
    kind=config.get('model_type')
    if kind is not None and kind not in TYPES:raise ValueError('choose a supported model_type or null')
    sources=config.get('source_refs',[])
    if (not isinstance(sources,list) or len(sources)>8 or any(not isinstance(r,str) for r in sources)
            or len(set(sources))!=len(sources)):
        raise ValueError('source_refs requires at most eight distinct source references')
    for ref in sources:
        if store.get(ref)['kind']!='source':raise ValueError('source_refs contains a non-source')
    model=config.get('model_ref')
    if model is not None:
        if not isinstance(model,str) or not REF_RE.fullmatch(model):raise ValueError('invalid model_ref')
        store.get(model)
    execution=config.get('execution',{})
    if not isinstance(execution,dict):raise ValueError('execution must be an object')
    calls=contracts.integer(config.get('max_calls',6),1,50,'max_calls')
    policy=context.policy(config.get('context_max_chars',60000))
    from .modeling_plan import _context
    labels=_context(config.get('modeling_context',{}))
    out=dict(schema_version=1,goal=goal,model_type=kind,source_refs=sources,model_ref=model,
        execution=execution,max_calls=calls,context_max_chars=policy['max_chars'],modeling_context=labels)
    if 'proposal_max_calls' in config:
        out['proposal_max_calls']=contracts.integer(config['proposal_max_calls'],0,3,'proposal_max_calls')
    if len(canonical_bytes(out))>256000:raise ValueError('research task configuration exceeds 256KB')
    return deepcopy(out)


def _records(store,kind,**matches):
    result=[]
    for path in sorted(store.objects_dir.glob('*.json')):
        if not REF_RE.fullmatch(path.stem):continue
        record=store._read_envelope(path.stem)
        p=record['payload']
        if record['kind']==kind and isinstance(p,dict) and all(p.get(k)==v for k,v in matches.items()):
            store.get(path.stem);result.append((path.stem,p))
    return result


@contextmanager
def _owner(store,family):
    from . import local_ownership
    if not isinstance(family,str) or not re.fullmatch('[a-f0-9]{32}',family):raise ValueError('invalid task family')
    directory=store.root/'.research-owners';directory.mkdir(mode=0o700,exist_ok=True)
    if local_ownership.is_redirect(directory):raise ValueError('research ownership directory is a symlink or reparse point')
    try:fd=local_ownership.acquire(directory/(family+'.lock'))
    except local_ownership.OwnerBusy as exc:raise ValueError('research task already has an active owner') from exc
    try:
        yield
    finally:os.close(fd)


def prepare(store,config,*,parent_ref=None,reason=None,selection_ref=None):
    spec=_normalize(store,config)
    parent=load(store,parent_ref) if parent_ref else None
    family=parent['family_id'] if parent else uuid4().hex
    with _owner(store,family):
        return _prepare_owned(store,spec,parent_ref=parent_ref,reason=reason,selection_ref=selection_ref,family=family)


def _prepare_owned(store,config,*,parent_ref=None,reason=None,selection_ref=None,family=None):
    """Caller holds the family owner; shared with explicit proposal adoption."""
    spec=_normalize(store,config);parent=load(store,parent_ref) if parent_ref else None
    family=parent['family_id'] if parent else family
    if _records(store,EXECUTION,family_id=family):
        raise ValueError('task family already dispatched; a revision cannot reset execution budgets')
    from . import task_proposals
    budget=task_proposals.budget(store,family)
    frozen=budget['frozen_spec']
    if frozen and (spec['max_calls']!=frozen['max_calls'] or spec.get('proposal_max_calls',0)!=frozen.get('proposal_max_calls',0)):
        raise ValueError('task revisions cannot change native/proposal budgets after a proposal call reservation')
    revision=parent['revision']+1 if parent else 0
    if revision>16:raise ValueError('research task revision limit exceeded')
    if parent:contracts.label(reason,2000)
    refs=[*spec['source_refs'],*([spec['model_ref']] if spec['model_ref'] else []),*([parent_ref] if parent_ref else [])]
    payload=dict(version=1,family_id=family,revision=revision,parent_ref=parent_ref,
        revision_reason=reason if parent else None,spec=spec,
        implementation_sha256=recovery.implementation_fingerprint(),scientific_support='unknown')
    if selection_ref:
        if not parent_ref:raise ValueError('a proposal selection requires its parent request')
        task_proposals.check_selection(store,selection_ref,spec)
        if store.get(selection_ref)['payload']['request_ref']!=parent_ref:raise ValueError('selection belongs to a different parent')
        payload['selection_ref']=selection_ref;refs.append(selection_ref)
    history=task_proposals.history_refs(store,family)
    if history:payload['proposal_history_refs']=history;refs.extend(history)
    return store.put(REQUEST,payload,refs=tuple(dict.fromkeys(refs)))


def load(store,ref):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!=REQUEST or type(p.get('version')) is not int or p['version']!=1:
        raise ValueError('expected a research_task request')
    if (not isinstance(p.get('family_id'),str) or not re.fullmatch('[a-f0-9]{32}',p['family_id'])
            or type(p.get('revision')) is not int or not 0<=p['revision']<=16):
        raise ValueError('invalid research task lineage')
    if canonical_bytes(p['spec'])!=canonical_bytes(_normalize(store,p['spec'])):
        raise ValueError('research task configuration changed')
    expected=set(p['spec']['source_refs'])|({p['spec']['model_ref']} if p['spec']['model_ref'] else set())
    if p['parent_ref']:
        parent=load(store,p['parent_ref']);expected.add(p['parent_ref'])
        if parent['family_id']!=p['family_id'] or parent['revision']+1!=p['revision']:
            raise ValueError('research task parent mismatch')
        contracts.label(p['revision_reason'],2000)
    elif p['revision']!=0:raise ValueError('research task revision has no parent')
    if p.get('selection_ref'):
        from .task_proposals import check_selection
        check_selection(store,p['selection_ref'],p['spec']);expected.add(p['selection_ref'])
        if store.get(p['selection_ref'])['payload']['request_ref']!=p['parent_ref']:
            raise ValueError('selection parent mismatch')
    if 'proposal_history_refs' in p:
        from .task_proposals import ATTEMPT,RETURN,PROPOSAL
        history=p['proposal_history_refs']
        if not isinstance(history,list) or len(set(history))!=len(history):raise ValueError('invalid proposal history')
        for entry in history:
            record=store.get(entry)
            if record['kind'] not in (ATTEMPT,RETURN,PROPOSAL):raise ValueError('invalid proposal history kind')
            if record['kind']==PROPOSAL:
                family=load(store,record['payload']['request_ref'])['family_id']
            else:
                attempt=record if record['kind']==ATTEMPT else store.get(record['payload']['attempt_ref'])
                family=attempt['payload']['family_id']
            if family!=p['family_id']:raise ValueError('foreign proposal history')
        expected.update(history)
    if set(obj['refs'])!=expected:raise ValueError('research task input closure mismatch')
    return p


def _ode_args(spec):
    from . import time_units, validation
    execution=spec['execution']
    contracts.fields(execution,{'experiment'},{'solver','observations','reference','scales'})
    experiment=from_dict(ExperimentSpec,execution['experiment'])
    if (not 2<=len(experiment.times)<=501 or experiment.times[0]<0 or experiment.times[-1]>10000
            or experiment.initial_overrides or experiment.parameter_overrides or experiment.segments):
        raise ValueError('ODE task requires 2..501 samples in 0..10000 with no mathematical overrides')
    solver=from_dict(SolverConfig,execution.get('solver',dict(timeout_s=15.)))
    contracts.number(solver.timeout_s,.1,60.,'solver timeout_s')
    if solver.max_steps>1000000:raise ValueError('solver max_steps exceeds profile')
    observations=execution.get('observations',[])
    if not isinstance(observations,list) or len(observations)>8:raise ValueError('at most eight observations')
    observations=tuple(from_dict(ObservationSpec,v) for v in observations)
    reference=from_dict(SimulationResult,execution['reference']) if execution.get('reference') is not None else None
    if reference:
        validation.validate_reference(reference)
        time_units.comparison_time_unit(experiment.time_unit,reference.time_unit)
        if tuple(reference.times)!=experiment.times:raise ValueError('reference grid must match experiment')
    scales=validation.validate_scales(execution.get('scales'),reference.values if reference else ())
    if (reference is None)!=(scales is None):raise ValueError('reference and fixed scales must be supplied together')
    return dict(source_refs=spec['source_refs'],experiment=experiment,solver=solver,observations=observations,
                reference=reference,scales=scales,modeling_decisions=True,modeling_context=spec['modeling_context'],max_retries=0)


def preflight(store,ref):
    p=load(store,ref);s=p['spec'];kind=s['model_type'];gaps=[]
    def gap(code,target,question):gaps.append(dict(code=code,input=target,question=question))
    if kind is None:gap('type_required','model_type','Select one supported model type; a research question alone does not select its scientific method.')
    elif kind=='ode':
        if not s['source_refs']:gap('sources_required','source_refs','Import authorized sources containing equations, initial values and parameters.')
        if s['model_ref'] is not None:gap('wrong_workflow','model_ref','ODE intake reconstructs from sources; use the existing direct model-run interface for supplied SBML.')
        if 'experiment' not in s['execution']:gap('experiment_required','execution.experiment','Supply an explicit sampling grid and time unit.')
        else:
            try:_ode_args(s)
            except (ValueError,KeyError,TypeError) as exc:gap('invalid_execution','execution',str(exc))
    else:
        if s['source_refs']:gap('unsupported_source_construction','source_refs','This native route requires a prepared native model; source-to-model construction is not yet implemented.')
        if s['modeling_context']:gap('unsupported_conditions','modeling_context','Native execution does not interpret ODE modeling-context labels.')
        if s['model_ref'] is None:gap('model_required','model_ref','Import a native model for this type; equations/rules/settings must be explicitly supplied.')
        try:
            contracts.fields(s['execution'],(),{'settings'})
            if kind=='fba':
                from . import fba
                fba.config(s['execution'].get('settings'))
                if s['model_ref']:fba.load(store,s['model_ref'])
            else:
                backend=typed_models.backend(kind);settings=backend.config(s['execution'].get('settings'))
                if s['model_ref']:backend.compatible(typed_models.load(store,s['model_ref'],model_type=kind),settings)
        except (ValueError,KeyError,TypeError) as exc:gap('invalid_native_input','model_ref/execution',str(exc))
    if p['implementation_sha256']!=recovery.implementation_fingerprint():
        gap('implementation_changed','implementation','Source changed since this request; prepare a new request revision before dispatch.')
    state='awaiting_type' if kind is None else 'awaiting_input' if gaps else 'ready_to_run'
    return dict(request_ref=ref,family_id=p['family_id'],revision=p['revision'],goal=s['goal'],model_type=kind,
        state=state,gaps=gaps,source_refs=s['source_refs'],model_ref=s['model_ref'],max_calls=s['max_calls'],
        stages=[dict(id='intake',status='complete'),dict(id='type_selection',status='complete' if kind else 'pending'),
                dict(id='input_preparation',status='blocked' if gaps else 'ready'),dict(id='execution',status='pending'),dict(id='report',status='pending')],
        next_action=gaps[0]['question'] if gaps else 'Execute the frozen selected workflow once.',
        scientific_support='unknown',limitations=['Model type is selected explicitly; no autonomous scientific-method inference.',
        'Goal is a descriptive request label; the selected bounded workflow defines actual execution.',
        'Native models retain their own contracts; numerical completion is not scientific approval.'])


def _execution(store,request):
    records=_records(store,EXECUTION,family_id=request['family_id'])
    if len(records)>1:raise ValueError('conflicting research dispatch reservations')
    if not records:return None
    ref,p=records[0]
    if p.get('phase')!='reserved' or type(p.get('version')) is not int or p['version']!=1 or store.get(ref)['refs']!=[p['request_ref']]:
        raise ValueError('invalid research dispatch reservation')
    return ref,p


def _child(store,execution_ref,request_ref):
    records=_records(store,BINDING,execution_ref=execution_ref)
    if len(records)>1:raise ValueError('research dispatch has multiple native tasks')
    if not records:return None
    ref,p=records[0]
    if (p['request_ref']!=request_ref or set(store.get(ref)['refs'])!={request_ref,execution_ref,p['native_task_ref']}
            or store.get(p['native_task_ref'])['kind']!='agent_task'):
        raise ValueError('invalid research/native task binding')
    if store.get(p['native_task_ref'])['payload']['provider']!=store.get(execution_ref)['payload']['provider']:
        raise ValueError('native provider differs from the reserved research dispatch')
    return ref,p['native_task_ref']


def _validate_native(store,request_ref,native_ref):
    from . import agent
    spec=load(store,request_ref)['spec'];obj=store.get(native_ref);p=obj['payload'];task=store.get(p['task_ref'])['payload']
    kind=spec['model_type'];expected='agent_run' if kind=='ode' else 'fba_agent_run' if kind=='fba' else 'typed_agent_run'
    if (obj['kind']!=expected or task['max_calls']!=spec['max_calls']
            or task.get('context_policy')!=context.policy(spec['context_max_chars'])):
        raise ValueError('native result differs from selected workflow/budget')
    if kind=='ode':
        args=_ode_args(spec)
        if (task.get('mode')!='reproduction' or task.get('source_refs')!=spec['source_refs']
                or task.get('modeling_decisions')!={'version':1,'context':spec['modeling_context']}):
            raise ValueError('ODE native task scope changed')
        for key in ('experiment','solver','reference'):
            expected_value=to_dict(args[key]) if args[key] is not None else None
            if canonical_bytes(task[key])!=canonical_bytes(expected_value):raise ValueError('ODE execution input changed')
        if (task['scales']!=args['scales'] or task['observations']!=[to_dict(v) for v in args['observations']]
                or task.get('context_policy')!=context.policy(spec['context_max_chars'])):
            raise ValueError('ODE observations/context changed')
    elif kind=='fba':
        from . import fba, fba_agent_runtime
        checked=fba_agent_runtime.validate_task(store,p['task_ref'])
        if checked['model_ref']!=spec['model_ref'] or checked['settings']!=fba.config(spec['execution'].get('settings')):
            raise ValueError('FBA task input changed')
        if p['run_ref']:fba_agent_runtime.bound_result(store,checked,p['run_ref'])
    else:
        from . import typed_agent_runtime
        checked=typed_agent_runtime.validate_task(store,p['task_ref'])
        if (checked['model_ref']!=spec['model_ref'] or checked['model_type']!=kind
                or checked['settings']!=typed_models.backend(kind).config(spec['execution'].get('settings'))):
            raise ValueError('native typed input changed')
        if p['run_ref']:typed_agent_runtime.bound_result(store,checked,p['run_ref'])
    diagnostic=recovery.diagnose_execution(store,p['execution_ref'])
    readable_error=(p['state']!='completed' and diagnostic['status']=='blocked'
        and bool(diagnostic['reasons']) and set(diagnostic['reasons'])<={'recorded_error_or_interruption_requires_review'})
    if (diagnostic['status']!='finished' and not readable_error) or native_ref not in diagnostic['final_run_refs']:
        raise ValueError('native final result does not match its verified invocation journal')
    return p


def _native_state(store,request_ref,child_ref):
    roots=[]
    for kind in ('agent_run','fba_agent_run','typed_agent_run'):
        roots.extend(_records(store,kind,task_ref=child_ref))
    executions=_records(store,'agent_execution',task_ref=child_ref)
    finalized={p['execution_ref'] for _,p in roots}
    pending=[ref for ref,_ in executions if ref not in finalized]
    parents={p.get('parent_run_ref') for _,p in roots}
    leaves=[ref for ref,_ in roots if ref not in parents]
    if len(pending)>1 or len(leaves)>1:raise ValueError('ambiguous native task continuation history')
    if pending:
        diagnostic=recovery.diagnose_execution(store,pending[0])
        return dict(state='needs_recovery',execution_ref=pending[0],native_run_ref=None,
            call_count=diagnostic['call_count'],run_count=diagnostic['run_count'],
            recovery_status=diagnostic['status'],recovery_reasons=diagnostic['reasons'])
    if not leaves:return dict(state='unknown_effects',execution_ref=None,native_run_ref=None)
    p=_validate_native(store,request_ref,leaves[0])
    questions=[e['question'] for e in p['events'] if e.get('type')=='awaiting_input' and e.get('question')]
    return dict(state=p['state'],execution_ref=p['execution_ref'],native_run_ref=leaves[0],
                call_count=p['call_count'],run_count=p.get('run_count'),run_ref=p.get('run_ref'),
                pending_question=questions[-1] if p['state']=='awaiting_input' and questions else None)


def status(store,request_ref):
    p=load(store,request_ref);view=preflight(store,request_ref);execution=_execution(store,p)
    if execution is None:return _proposal_counts(store,p,view)
    eref,e=execution
    if e['request_ref']!=request_ref:
        view.update(state='another_revision_dispatched',selected_request_ref=e['request_ref'],
            next_action='Inspect the revision that already owns this family; revisions do not create another execution budget.')
        return _proposal_counts(store,p,view)
    child=_child(store,eref,request_ref)
    native=_native_state(store,request_ref,child[1]) if child else dict(state='unknown_effects',call_count=None,run_count=None)
    view.update(native,dispatch_ref=eref,native_task_ref=child[1] if child else None)
    view['next_action']=('Reply using research-task-resume: '+(view.get('pending_question') or 'Inspect the saved question.') if view['state']=='awaiting_input' else
        'Recovery is blocked: '+', '.join(view['recovery_reasons']) if view.get('recovery_status')=='blocked' else
        'Use research-task-recover to inspect/adopt the interrupted native execution; never restart it.' if view['state']=='needs_recovery' else
        'Read/export the saved result.' if view['state']=='completed' else 'Inspect saved receipts and failures; no automatic new execution.')
    if native.get('native_run_ref'):
        wrappers=_records(store,RUN,request_ref=request_ref,native_run_ref=native['native_run_ref'])
        if len(wrappers)>1:raise ValueError('conflicting research result wrappers')
        view['result_ref']=wrappers[0][0] if wrappers else None
        view['stages'][-2]['status']='complete' if native.get('run_ref') else 'pending'
        if view['state']=='awaiting_input':view['stages'][-2]['status']='waiting'
        view['stages'][-1]['status']='complete' if view['state']=='completed' else 'pending'
    return _proposal_counts(store,p,view)


def _proposal_counts(store,request,view):
    from . import task_proposals
    budget=task_proposals.budget(store,request['family_id'])
    native=view.get('call_count',0 if _execution(store,request) is None else None)
    view.update(proposal_call_count=budget['call_count'],proposal_max_calls=request['spec'].get('proposal_max_calls',0),
        proposal_unresolved_attempt_refs=budget['unresolved_attempt_refs'],native_call_count=native,
        total_call_count=native+budget['call_count'] if native is not None else None,
        max_total_calls=request['spec']['max_calls']+request['spec'].get('proposal_max_calls',0))
    if budget['unresolved_attempt_refs'] and _execution(store,request) is None:
        view.update(state='needs_proposal_recovery',next_action='Inspect the reserved proposal attempt; recover only a confirmed saved return, without resubmitting.')
    return view


class _BindingStore:
    def __init__(self,store,request_ref,execution_ref):
        self.store=store;self.request_ref=request_ref;self.execution_ref=execution_ref;self.bound=False
    def __getattr__(self,name):return getattr(self.store,name)
    def put(self,kind,payload,refs=None):
        if kind=='agent_task' and self.bound:raise ValueError('research task cannot dispatch a second native task')
        ref=self.store.put(kind,payload,refs)
        if kind=='agent_task':
            self.store.put(BINDING,dict(version=1,request_ref=self.request_ref,execution_ref=self.execution_ref,native_task_ref=ref),
                refs=(self.request_ref,self.execution_ref,ref))
            self.bound=True
        return ref


def _wrap(store,request_ref,execution_ref,native_ref):
    p=_validate_native(store,request_ref,native_ref)
    prior=_records(store,RUN,request_ref=request_ref,native_run_ref=native_ref)
    if prior:
        if len(prior)!=1:raise ValueError('conflicting research wrappers')
        result(store,prior[0][0])
        return prior[0][0]
    request=load(store,request_ref);child=_child(store,execution_ref,request_ref)
    if child is None or child[1]!=p['task_ref']:raise ValueError('native result belongs to another research dispatch')
    payload=dict(version=1,request_ref=request_ref,dispatch_ref=execution_ref,native_run_ref=native_ref,
        native_task_ref=p['task_ref'],model_type=request['spec']['model_type'],goal=request['spec']['goal'],state=p['state'],
        call_count=p['call_count'],run_count=p.get('run_count'),events=p['events'],scientific_support='unknown')
    from .task_proposals import history_refs,ATTEMPT
    history=history_refs(store,request['family_id'])
    if history:
        count=sum(store.get(r)['kind']==ATTEMPT for r in history)
        payload.update(proposal_history_refs=history,proposal_call_count=count,total_call_count=count+p['call_count'])
    return store.put(RUN,payload,refs=(request_ref,execution_ref,child[0],native_ref,*history))


def execute(store,provider,request_ref):
    request=load(store,request_ref)
    with _owner(store,request['family_id']):
        previous=_execution(store,request)
        if previous:
            view=status(store,request_ref)
            if view.get('native_run_ref'):return _wrap(store,request_ref,previous[0],view['native_run_ref'])
            raise ValueError(view['next_action'])
        view=preflight(store,request_ref)
        if view['state']!='ready_to_run':raise ValueError(view['next_action'])
        if provider is None:raise ValueError('a provider is required to dispatch the task')
        from .task_proposals import budget
        planning=budget(store,request['family_id'])
        if planning['unresolved_attempt_refs']:
            raise ValueError('proposal effects are unknown; inspect the reserved proposal before native dispatch')
        frozen=planning['frozen_spec']
        if frozen and (request['spec']['max_calls']!=frozen['max_calls'] or request['spec'].get('proposal_max_calls',0)!=frozen.get('proposal_max_calls',0)):
            raise ValueError('native/proposal budgets differ from the family reservation')
        from . import agent
        spec=request['spec'];kind=spec['model_type']
        execution_ref=store.put(EXECUTION,dict(version=1,phase='reserved',family_id=request['family_id'],request_ref=request_ref,
            provider=agent._provider_identity(provider)),refs=(request_ref,))
        scoped=_BindingStore(store,request_ref,execution_ref)
        args=dict(max_calls=spec['max_calls'],context_max_chars=spec['context_max_chars'])
        if kind=='ode':args.update(_ode_args(spec))
        else:args.update(model_ref=spec['model_ref'],settings=spec['execution'].get('settings'),recoverable=True)
        native_ref=agent.run_model_agent(scoped,provider,model_type=kind,**args)
        return _wrap(store,request_ref,execution_ref,native_ref)


def resume(store,provider,request_ref,reply,*,respondent):
    from . import agent
    request=load(store,request_ref)
    with _owner(store,request['family_id']):
        view=status(store,request_ref)
        if view['state']!='awaiting_input':raise ValueError('only the current paused native task can resume')
        ref=agent.resume_agent(store,provider,view['native_run_ref'],reply,respondent=respondent)
        return _wrap(store,request_ref,view['dispatch_ref'],ref)


def recover(store,provider,request_ref):
    from . import agent
    request=load(store,request_ref)
    with _owner(store,request['family_id']):
        view=status(store,request_ref)
        if view.get('native_run_ref'):return _wrap(store,request_ref,view['dispatch_ref'],view['native_run_ref'])
        if view['state']!='needs_recovery':raise ValueError(view['next_action'])
        ref=agent.recover_agent(store,provider,view['execution_ref'])
        return _wrap(store,request_ref,view['dispatch_ref'],ref)


def result(store,ref):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!=RUN or type(p.get('version')) is not int or p['version']!=1:raise ValueError('expected a research_task_run result')
    request=load(store,p['request_ref']);execution=_execution(store,request)
    if execution is None or execution[0]!=p['dispatch_ref'] or execution[1]['request_ref']!=p['request_ref']:
        raise ValueError('research result does not match its dispatch reservation')
    if p['model_type']!=request['spec']['model_type'] or p['goal']!=request['spec']['goal'] or p.get('scientific_support')!='unknown':
        raise ValueError('research result changes the frozen question/type/support')
    native=_validate_native(store,p['request_ref'],p['native_run_ref'])
    if p.get('native_task_ref')!=native['task_ref']:raise ValueError('research result names the wrong native task')
    for key in ('state','events','call_count','run_count'):
        if p.get(key)!=native.get(key):raise ValueError('research result differs from its native execution')
    child=_child(store,p['dispatch_ref'],p['request_ref'])
    expected={p['request_ref'],p['dispatch_ref'],child[0],p['native_run_ref']} if child else set()
    from .task_proposals import history_refs
    if history_refs(store,request['family_id']) and 'proposal_history_refs' not in p:
        raise ValueError('research result omitted its proposal accounting history')
    if 'proposal_history_refs' in p:
        from .task_proposals import history_refs,ATTEMPT
        history=history_refs(store,request['family_id'])
        count=sum(store.get(r)['kind']==ATTEMPT for r in history)
        if p['proposal_history_refs']!=history or p.get('proposal_call_count')!=count or p.get('total_call_count')!=count+p['call_count']:
            raise ValueError('research result proposal accounting mismatch')
        expected.update(history)
    if child is None or child[1]!=native['task_ref'] or set(obj['refs'])!=expected:
        raise ValueError('research result dependency closure mismatch')
    return p


def report_lines(store,ref):
    from .timeline import cell
    obj=store.get(ref)
    if obj['kind']==RUN:
        p=result(store,ref)
        from .reporting import render_report
        request=load(store,p['request_ref']);selection=[]
        if request.get('selection_ref'):
            review=store.get(request['selection_ref'])['payload']
            selection=['','## Explicit input selection',
                'Researcher: '+cell(review['reviewer']),'Selection rationale: '+cell(review['reason']),
                'Selection records awareness of unknowns; it is not scientific certification.',
                render_report(store,review['proposal_ref'])]
        return [f"Unified research task: **{cell(p['state'])}**",'Goal: '+cell(p['goal']),
            f"Selected model type: `{p['model_type']}`",f"Request: `{p['request_ref']}`",
            f"Native task: `{p['native_task_ref']}`",'Scientific support: **unknown**.',
            '',render_report(store,p['native_run_ref']),*selection]
    p=load(store,ref);view=status(store,ref)
    return [f"Research intake: **{cell(view['state'])}**",'Goal: '+cell(p['spec']['goal']),
        'Selected model type: '+cell(view['model_type']),'Next: '+cell(view['next_action']),
        f"Calls: proposal {view['proposal_call_count']}/{view['proposal_max_calls']}; native {view['native_call_count']}; total {view['total_call_count']}/{view['max_total_calls']}.",
        *(['Question: '+cell(view['pending_question'])] if view.get('pending_question') else []),
        *['- '+cell(g['question']) for g in view['gaps']],
        'Type selection is explicit; native source-to-model construction remains type-specific.',
        'Scientific support: **unknown**.']
