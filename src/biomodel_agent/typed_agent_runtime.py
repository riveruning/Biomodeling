"""Opt-in typed Agent v2: frozen native input, attributed replies and verified replay.

The shared invocation journal and recovery auditor own call/run accounting.
Unknown scientific/provider outcomes never trigger automatic resubmission.
"""
from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4

from . import application, context, typed_models as models
from .invocations import InvocationJournal
from .llm import ProviderError
from .recovery import RecoveryBlocked, ReplayJournal, execution_owner, implementation_fingerprint
from .storage import canonical_bytes

PROFILE = 'typed_agent_v2'


def identity(provider):
    from .agent import _provider_identity
    return _provider_identity(provider)


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} requires 1..{maximum} characters')


def validate_task(store, task_ref):
    obj=store.get(task_ref);p=obj['payload']
    if (obj['kind']!='agent_task' or p.get('mode')!='native_typed' or p.get('typed_protocol')!=PROFILE
            or type(p.get('resume_version')) is not int or p['resume_version']!=1
            or type(p.get('max_calls')) is not int or not 1<=p['max_calls']<=50
            or p.get('source_refs')!=[] or obj['refs']!=[p['model_ref'],p['input_ref']]):
        raise RecoveryBlocked('invalid_or_legacy_native_typed_task')
    inp,model=models.load_input(store,p['input_ref'])
    if (inp['model_ref']!=p['model_ref'] or inp['model_type']!=p['model_type'] or inp['replay_of'] is not None
            or canonical_bytes(inp['settings'])!=canonical_bytes(p['settings'])
            or canonical_bytes(inp['environment'])!=canonical_bytes(p['environment'])):
        raise RecoveryBlocked('typed_task_input_binding_mismatch')
    context.validate_policy(p['context_policy'])
    if canonical_bytes(p['environment'])!=canonical_bytes(application.environment_fingerprint()):
        raise RecoveryBlocked('typed_environment_changed_start_new_task')
    return p

def brief(store,task):
    inp,model=models.load_input(store,task['input_ref'])
    return dict(model_type=task['model_type'],model_ref=task['model_ref'],input_ref=task['input_ref'],
        profile=inp['profile'],units=model['units'],settings=inp['settings'],protocol=PROFILE,
        scientific_support='unknown',limitations=models.backend(task['model_type']).LIMITATIONS)

def bound_result(store,task,ref):
    obj=store.get(ref)
    if obj['kind']!='typed_run' or obj['payload'].get('input_ref')!=task['input_ref']:
        raise RecoveryBlocked('typed_return_does_not_match_frozen_input')
    inp,model,data=models.result(store,ref)
    if inp['model_ref']!=task['model_ref'] or inp['model_type']!=task['model_type'] or inp['replay_of'] is not None:
        raise RecoveryBlocked('typed_return_model_or_type_mismatch')
    return models.summary(store,ref)

def inspect(store, task, run_ref):
    return dict(**brief(store, task), run_ref=run_ref, outcome=bound_result(store, task, run_ref) if run_ref else None)


def _reply_message(question, respondent, clarification_ref, reply):
    return dict(role='user', content=json.dumps(dict(tool='researcher_reply', question=question,
        respondent=respondent, clarification_ref=clarification_ref, reply=reply,
        notice='Attributed execution clarification only; frozen native model, units, settings, seed and budgets cannot change.'), ensure_ascii=False))


def validate_initial_scope(store, execution, task):
    validate_task(store, execution['task_ref'])
    meta = execution.get('recovery')
    if (not isinstance(meta, dict) or type(meta.get('version')) is not int or meta['version'] != 1
            or meta.get('implementation_sha256') != implementation_fingerprint()
            or meta.get('source_refs') != [] or (meta.get('model_ref') != task['model_ref'] or meta.get('input_ref') != task['input_ref'])):
        raise RecoveryBlocked('invalid_typed_initial_snapshot_or_implementation')
    parent_ref = execution.get('parent_run_ref'); restored = meta.get('restored')
    if parent_ref is None:
        if restored is not None or execution['base_call_count'] or execution['base_run_count']:
            raise RecoveryBlocked('invalid_typed_initial_budget')
        return
    parent = store.get(parent_ref); p = parent['payload']
    if (parent['kind'] != 'typed_agent_run' or p.get('version') != 2 or p.get('task_ref') != execution['task_ref']
            or p.get('state') != 'awaiting_input' or not isinstance(restored, dict)):
        raise RecoveryBlocked('invalid_typed_resume_parent')
    cp = store.get(p['checkpoint_ref'])
    if (cp['kind'] != 'agent_checkpoint' or cp['payload'].get('task_ref') != execution['task_ref']
            or cp['payload'].get('typed_protocol')!=PROFILE or cp['payload'].get('state')!='awaiting_input'
            or any(cp['payload'].get(k)!=p.get(k) for k in ('model_ref','input_ref','run_ref','call_count','run_count','events'))
            or p.get('model_ref')!=task['model_ref'] or p.get('input_ref')!=task['input_ref']):
        raise RecoveryBlocked('invalid_typed_parent_checkpoint')
    refs = restored.get('clarification_refs', [])
    if len(refs) != len(p['clarification_refs'])+1 or refs[:-1] != p['clarification_refs']:
        raise RecoveryBlocked('invalid_typed_reply_lineage')
    reply_obj = store.get(refs[-1]); reply = reply_obj['payload']
    if (reply_obj['kind'] != 'typed_agent_clarification' or reply['parent_run_ref'] != parent_ref
            or reply['checkpoint_ref'] != p['checkpoint_ref'] or {parent_ref, p['checkpoint_ref']} - set(reply_obj['refs'])):
        raise RecoveryBlocked('invalid_typed_clarification_binding')
    expected = deepcopy(cp['payload'])
    expected['messages'].append(_reply_message(reply['question'], reply['respondent'], refs[-1], reply['reply']))
    expected['events'].append(dict(type='researcher_reply', parent_run_ref=parent_ref, respondent=reply['respondent'],
        clarification_ref=refs[-1], artifact_refs=[refs[-1]], call=p['call_count']))
    expected['checkpoint_refs'] = p['checkpoint_refs']; expected['clarification_refs'] = refs
    if (canonical_bytes(expected) != canonical_bytes(restored)
            or restored['call_count'] != execution['base_call_count'] or restored['run_count'] != execution['base_run_count']):
        raise RecoveryBlocked('typed_resume_snapshot_or_budget_mismatch')
    context.build_context(restored['messages'], task['context_policy'])
    if restored['run_ref']:
        bound_result(store, task, restored['run_ref'])


def run_agent(store,provider,model_ref,*,model_type,settings=None,max_calls=6,context_max_chars=60000):
    models.load(store,model_ref,model_type=model_type)
    if type(max_calls) is not int or not 1<=max_calls<=50:
        raise ValueError('invalid native Agent call budget')
    policy=context.policy(context_max_chars)
    input_ref=models.prepare(store,model_ref,settings)
    inp,_=models.load_input(store,input_ref)
    task_ref=store.put('agent_task',dict(resume_version=1,mode='native_typed',typed_protocol=PROFILE,
        model_type=model_type,source_refs=[],model_ref=model_ref,input_ref=input_ref,settings=inp['settings'],max_calls=max_calls,
        provider=identity(provider),context_policy=policy,environment=inp['environment']),refs=(model_ref,input_ref))
    with execution_owner(store,task_ref):
        return _execute(store,provider,task_ref)

def _execute(store, provider, task_ref, restored=None, parent_run_ref=None, journal=None, recovery_refs=None):
    task = validate_task(store, task_ref)
    if identity(provider) != task['provider']:
        raise RecoveryBlocked('Native continuation requires the original provider identity/settings')
    meta = dict(version=1, implementation_sha256=implementation_fingerprint(), source_refs=[],
                model_ref=task['model_ref'], input_ref=task['input_ref'], restored=deepcopy(restored))
    messages = [dict(role='system', content='Operate only this frozen native model, input settings, units and seed. Return {"action":NAME,"args":{}} and optional reason. '
        'Allowed: inspect_typed, run_typed, finish; ask_researcher requires args:{question:str}. Run at most once. '
        'No model, type, settings, units, seed or budget edits. Replies are attributed execution clarifications, not model changes. '
        'Report numerical checks separately; scientific support remains unknown.'),
        dict(role='user', content=json.dumps(brief(store, task)))]
    events=[]; checkpoints=[]; clarification_refs=[]; run_ref=None; calls=runs=0
    if restored is not None:
        messages=deepcopy(restored['messages']); events=deepcopy(restored['events'])
        checkpoints=restored['checkpoint_refs'][:]; clarification_refs=restored['clarification_refs'][:]
        run_ref=restored['run_ref']; calls=restored['call_count']; runs=restored['run_count']
    journal = journal or InvocationJournal(store, task_ref, parent_run_ref=parent_run_ref, calls=calls, runs=runs,
        recovery=meta, initial_refs=(task['model_ref'],task['input_ref']))
    recovery_refs = list(recovery_refs or [])
    state='incomplete'

    def checkpoint():
        refs=[task_ref, task['model_ref'], task['input_ref'], journal.execution_ref, *journal.refs, *recovery_refs, *clarification_refs]
        if run_ref: refs.append(run_ref)
        refs.extend(r for e in events for r in e.get('artifact_refs', []) if r)
        return store.put('agent_checkpoint', dict(resume_version=1, typed_protocol=PROFILE, task_ref=task_ref,
            state=state, source_refs=[], model_ref=task['model_ref'], input_ref=task['input_ref'], run_ref=run_ref,
            call_count=journal.calls, run_count=journal.runs, messages=messages, events=events,
            checkpoint_refs=checkpoints, clarification_refs=clarification_refs, execution_ref=journal.execution_ref,
            invocation_refs=journal.refs, recovery_refs=recovery_refs), refs=tuple(dict.fromkeys(refs)))

    while journal.calls < task['max_calls']:
        try:
            packet, audit = context.build_context(messages, task['context_policy'])
        except context.ContextBudgetExceeded as exc:
            events.append(dict(type='context_budget_exceeded', error=str(exc))); break
        try:
            response=journal.invoke('respond', lambda:provider.respond(packet),
                summarize=lambda r:dict(response_json=json.dumps(r, allow_nan=False)))
        except ProviderError as exc:
            events.append(dict(type='provider_error',call=journal.calls,kind=exc.kind,retryable=False,error='Native provider failed; no automatic retry.')); break
        except Exception:
            events.append(dict(type='provider_error',call=journal.calls,kind='unknown',retryable=False,error='Unknown Native provider failure; inspect receipts.')); break
        events.append(dict(type='llm_call',call=journal.calls,response=response,context=audit))
        if (not isinstance(response,dict) or set(response)-{'action','args','reason'} or not {'action','args'}<=set(response)
                or not isinstance(response['args'],dict) or ('reason' in response and (not isinstance(response['reason'],str) or len(response['reason'])>1000))):
            events.append(dict(type='tool_error',error='Invalid Native response schema.')); break
        action=response['action']; args=response['args']; messages.append(dict(role='assistant',content=json.dumps(response)))
        if action == 'ask_researcher':
            if set(args) != {'question'}:
                events.append(dict(type='tool_error',error='ask_researcher requires only question.')); break
            try: _text(args['question'],'question',1000)
            except ValueError:
                events.append(dict(type='tool_error',error='Invalid Native question.')); break
            events.append(dict(type='awaiting_input',call=journal.calls,question=args['question'],origin='model'))
            state='awaiting_input'; break
        if args:
            events.append(dict(type='tool_error',error='Frozen Native tools take empty args.')); break
        if action == 'inspect_typed':
            out=journal.invoke('inspect_typed',lambda:inspect(store,task,run_ref),details=dict(run_ref=run_ref))
        elif action == 'run_typed':
            if journal.runs:
                events.append(dict(type='tool_error',error='Native scientific run budget is consumed.')); break
            try:
                run_ref=journal.invoke('run_typed',lambda:models.execute(store,task['input_ref']),
                    details=dict(input_ref=task['input_ref']),summarize=lambda r:dict(result_ref=r),result_refs=lambda r:[r])
                out=bound_result(store,task,run_ref)
            except Exception:
                events.append(dict(type='tool_error',tool=action,error='Native result unconfirmed; inspect receipts, no automatic retry.')); break
            events.append(dict(type='tool_result',tool=action,call=journal.calls,outcome=out,artifact_refs=[run_ref]))
            messages.append(dict(role='user',content=json.dumps(dict(tool=action,ok=True,**out))))
            checkpoints.append(checkpoint())
            if not out['completion_eligible']: break
            continue
        elif action == 'finish':
            state='completed' if run_ref and bound_result(store,task,run_ref)['completion_eligible'] else 'incomplete'; break
        else:
            events.append(dict(type='tool_error',error='Unsupported Native action; no tool invoked.')); break
        events.append(dict(type='tool_result',tool=action,call=journal.calls,outcome=out,artifact_refs=[run_ref] if run_ref else []))
        messages.append(dict(role='user',content=json.dumps(dict(tool=action,ok=True,**out))))
        checkpoints.append(checkpoint())
    if isinstance(journal,ReplayJournal): journal.assert_consumed()
    checkpoints.append(checkpoint())
    return _final(store,task_ref,task,state,run_ref,journal,events,checkpoints,clarification_refs,recovery_refs,parent_run_ref)


def _final(store,task_ref,task,state,run_ref,journal,events,checkpoints,clarification_refs,recovery_refs,parent_run_ref):
    refs=[task_ref,task['model_ref'],task['input_ref'],journal.execution_ref,*journal.refs,*checkpoints,*clarification_refs,*recovery_refs]
    if run_ref: refs.append(run_ref)
    if parent_run_ref: refs.append(parent_run_ref)
    return store.put('typed_agent_run',dict(version=2,model_type=task['model_type'],typed_protocol=PROFILE,state=state,
        task_ref=task_ref,source_refs=[],model_ref=task['model_ref'],input_ref=task['input_ref'],run_ref=run_ref,call_count=journal.calls,run_count=journal.runs,
        execution_ref=journal.execution_ref,invocation_refs=journal.refs,events=events,checkpoint_ref=checkpoints[-1],
        checkpoint_refs=checkpoints,clarification_refs=clarification_refs,recovery_refs=recovery_refs,parent_run_ref=parent_run_ref,
        scientific_support='unknown',limitations=['Frozen native execution only; no autonomous method selection or model editing.',
            'Verified returns may be adopted; unknown scientific/provider effects never auto-retry.']),refs=tuple(dict.fromkeys(refs)))


def resume_agent(store,provider,run_ref,reply,*,respondent):
    _text(reply,'reply',8000); _text(respondent,'respondent',200)
    obj=store.get(run_ref); p=obj['payload']
    if obj['kind']!='typed_agent_run' or p.get('version')!=2 or p.get('state')!='awaiting_input':
        raise RecoveryBlocked('only a paused Native v2 run can resume; legacy records cannot be upgraded')
    with execution_owner(store,p['task_ref']):
        task=validate_task(store,p['task_ref']); cp_obj=store.get(p['checkpoint_ref']); cp=cp_obj['payload']
        from .recovery import _inventory
        if any(o['kind']=='agent_execution' and isinstance(o['payload'],dict) and o['payload'].get('parent_run_ref')==run_ref
               for o in _inventory(store).values()):
            raise RecoveryBlocked('typed_pause_already_continued_use_saved_child_or_recovery')
        if (cp_obj['kind']!='agent_checkpoint' or cp.get('typed_protocol')!=PROFILE or cp.get('task_ref')!=p['task_ref']
                or cp.get('model_ref')!=task['model_ref'] or p.get('model_ref')!=task['model_ref']
                or cp.get('input_ref')!=task['input_ref'] or p.get('input_ref')!=task['input_ref']
                or cp.get('source_refs')!=[] or p.get('source_refs')!=[]
                or type(cp.get('call_count')) is not int or type(cp.get('run_count')) is not int
                or not 0<=cp['call_count']<=task['max_calls'] or cp['run_count'] not in (0,1)
                or cp.get('state')!='awaiting_input' or any(cp.get(k)!=p.get(k) for k in
                    ('call_count','run_count','run_ref','events','execution_ref','invocation_refs','clarification_refs','recovery_refs'))):
            raise RecoveryBlocked('Native pause checkpoint mismatch')
        if identity(provider)!=task['provider']: raise RecoveryBlocked('Native resume requires original provider identity/settings')
        if cp['run_ref']: bound_result(store,task,cp['run_ref'])
        if cp['call_count']>=task['max_calls'] or len(p['clarification_refs'])>=8: raise RecoveryBlocked('Native continuation budget exhausted; start a separate task')
        pending=[e for e in cp['events'] if e.get('type')=='awaiting_input']
        if not pending: raise RecoveryBlocked('Native pause has no question')
        question=pending[-1]['question']
        context.build_context([*cp['messages'],_reply_message(question,respondent,'0'*64,reply)],task['context_policy'])
        clarification=store.put('typed_agent_clarification',dict(version=1,parent_run_ref=run_ref,checkpoint_ref=p['checkpoint_ref'],
            question=question,reply=reply,respondent=respondent,authority='researcher_assertion',scientific_support='unknown'),refs=(run_ref,p['checkpoint_ref']))
        restored=deepcopy(cp)
        restored['messages'].append(_reply_message(question,respondent,clarification,reply))
        restored['events'].append(dict(type='researcher_reply',parent_run_ref=run_ref,respondent=respondent,
            clarification_ref=clarification,artifact_refs=[clarification],call=p['call_count']))
        restored['checkpoint_refs']=p['checkpoint_refs']; restored['clarification_refs']=[*p['clarification_refs'],clarification]
        return _execute(store,provider,p['task_ref'],restored,run_ref)


def recover_agent(store,provider,execution_ref):
    from .recovery import diagnose_execution
    obj=store.get(execution_ref)
    if obj['kind']!='agent_execution': raise RecoveryBlocked('Native recovery requires an agent_execution')
    execution=obj['payload']; task_ref=execution['task_ref']
    with execution_owner(store,task_ref):
        report=diagnose_execution(store,execution_ref)
        if report['status']!='ready': raise RecoveryBlocked(', '.join(report['reasons']) or 'Native execution already finished')
        task=validate_task(store,task_ref)
        if identity(provider)!=task['provider']: raise RecoveryBlocked('Native recovery requires original provider identity/settings')
        refs=[r for op in report['operations'] for r in (op['reserved_ref'],op['terminal_ref']) if r]
        receipt=store.put('agent_recovery',dict(version=1,id=str(uuid4()),execution_ref=execution_ref,task_ref=task_ref,
            call_count=report['call_count'],run_count=report['run_count'],adoptable_result_refs=report['adoptable_result_refs'],
            invocation_refs=refs,method='verified_return_replay'),refs=(execution_ref,*refs))
        return _execute(store,provider,task_ref,deepcopy(execution['recovery']['restored']),execution.get('parent_run_ref'),
            ReplayJournal(store,report),[*report['recovery_refs'],receipt])
