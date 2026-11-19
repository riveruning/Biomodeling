"""Explicit FBA Agent route over one operator-frozen LP, never an ODE draft."""
import json
from . import fba
from .invocations import InvocationJournal
from .llm import ProviderError


def run_agent(store,provider,model_ref,*,settings=None,max_calls=6,recoverable=False,context_max_chars=60000):
    if type(recoverable) is not bool:raise ValueError('recoverable must be a bool')
    if recoverable:
        from .fba_agent_runtime import run_agent as run_v2
        return run_v2(store,provider,model_ref,settings=settings,max_calls=max_calls,context_max_chars=context_max_chars)
    if context_max_chars!=60000:raise ValueError('custom FBA context policy requires recoverable=True')
    model=fba.load(store,model_ref);settings=fba.config(settings)
    if type(max_calls) is not int or not 1<=max_calls<=50:raise ValueError('invalid FBA Agent call budget')
    task=store.put('agent_task',dict(resume_version=1,mode='fba',model_ref=model_ref,settings=settings,max_calls=max_calls,
        provider={'class':type(provider).__module__+'.'+type(provider).__qualname__},
        recovery_support='not_available_for_fba; inspect immutable receipts; no automatic retry'),refs=(model_ref,))
    journal=InvocationJournal(store,task,initial_refs=(model_ref,))
    brief=dict(model_type='fba',model_ref=model_ref,formulation='steady_state',metabolites=len(model['metabolites']),
        reactions=len(model['reactions']),flux_unit=model['flux_unit'],objective=model['objective'],
        method=settings['method'],timeout_s=settings['timeout_s'],reference_requested=settings['reference'] is not None)
    messages=[dict(role='system',content='You operate a frozen steady-state FBA model. Return {"action":NAME,"args":{}} with an optional short reason. '
        'Allowed: inspect_fba, run_fba, finish. Run at most once. No model, bounds, objective, reference or settings changes. '
        'Report solver outcome, feasibility and comparison separately; scientific support stays unknown.'),
        dict(role='user',content=json.dumps(brief))]
    events=[];run_ref=None;state='incomplete'
    while journal.calls<max_calls:
        if sum(len(m['content']) for m in messages)>60000:
            events.append(dict(type='context_budget_exceeded',error='FBA context exceeds 60000 characters.'));break
        try:
            response=journal.invoke('respond',lambda:provider.respond(messages),
                summarize=lambda r:dict(response_json=json.dumps(r,allow_nan=False)))
        except ProviderError as exc:
            events.append(dict(type='provider_error',call=journal.calls,kind=exc.kind,retryable=False,
                               error='FBA provider failed; no automatic retry.'))
            break
        except Exception:
            events.append(dict(type='provider_error',call=journal.calls,kind='unknown',retryable=False,
                               error='Unknown provider failure; inspect journal, no automatic retry.'))
            break
        events.append(dict(type='llm_call',call=journal.calls,response=response))
        if (not isinstance(response,dict) or set(response)-{'action','args','reason'} or not {'action','args'}<=set(response)
            or response.get('args')!={} or ('reason' in response and (not isinstance(response['reason'],str) or len(response['reason'])>1000))):
            events.append(dict(type='tool_error',error='FBA actions require exactly action and empty args; no ODE or scientific edits.'));break
        action=response['action']
        messages.append(dict(role='assistant',content=json.dumps(response)))
        if action=='inspect_fba':
            out=journal.invoke('inspect_fba',lambda:dict(**brief,run_ref=run_ref,
                outcome=fba.summary(store,run_ref) if run_ref else None))
        elif action=='run_fba':
            if journal.runs:
                events.append(dict(type='tool_error',error='FBA scientific run budget is consumed.'));break
            try:
                run_ref=journal.invoke('run_fba',lambda:fba.run(store,model_ref,settings),details=dict(model_ref=model_ref,settings=settings),
                                       summarize=lambda r:dict(result_ref=r),result_refs=lambda r:[r])
            except Exception:
                events.append(dict(type='tool_error',tool='run_fba',error='FBA tool failed without a confirmed result; inspect invocation receipt, no retry.'));break
            out=fba.summary(store,run_ref)
            if not out['completion_eligible']:
                events.append(dict(type='tool_result',tool=action,call=journal.calls,outcome=out,artifact_refs=[run_ref]));break
        elif action=='finish':
            state='completed' if run_ref and fba.summary(store,run_ref)['completion_eligible'] else 'incomplete'
            break
        else:
            events.append(dict(type='tool_error',error='Unsupported model type/action for FBA; no tool executed.'));break
        events.append(dict(type='tool_result',tool=action,call=journal.calls,outcome=out,
                           artifact_refs=[run_ref] if run_ref else []))
        messages.append(dict(role='user',content=json.dumps(dict(tool=action,**out))))
    refs=[task,model_ref,journal.execution_ref,*journal.refs,*([run_ref] if run_ref else [])]
    return store.put('fba_agent_run',dict(version=1,model_type='fba',state=state,task_ref=task,model_ref=model_ref,run_ref=run_ref,
        call_count=journal.calls,run_count=journal.runs,execution_ref=journal.execution_ref,invocation_refs=journal.refs,
        events=events,scientific_support='unknown',limitations=['Frozen LP execution only; no automatic research-method selection or active model editing.',
        'FBA Agent resume/recovery is not implemented; interrupted/unknown effects are not retried.']),refs=tuple(refs))
