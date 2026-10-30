"""Budgeted Agent adapter over an operator-frozen native simulation.

This is explicit tool orchestration, not autonomous choice/inference of a
scientific method. Interrupted effects remain blocked; replay is a new run.
"""
import json
from . import typed_models as models
from .invocations import InvocationJournal
from .model_contracts import integer


def run_agent(store, provider, model_ref, *, model_type, settings=None, max_calls=6,
              recoverable=False, context_max_chars=60000):
    if type(recoverable) is not bool:
        raise ValueError('recoverable must be a bool')
    if recoverable:
        from .typed_agent_runtime import run_agent as run_v2
        return run_v2(store,provider,model_ref,model_type=model_type,settings=settings,
                      max_calls=max_calls,context_max_chars=context_max_chars)
    if context_max_chars!=60000:
        raise ValueError('custom typed context policy requires recoverable=True')
    model = models.load(store, model_ref, model_type=model_type)
    integer(max_calls, 1, 50, 'max_calls')
    input_ref = models.prepare(store, model_ref, settings)
    inp, _ = models.load_input(store, input_ref)
    brief = dict(model_type=model_type, profile=inp['profile'], model_ref=model_ref, input_ref=input_ref,
                 units=model['units'], settings=inp['settings'], scientific_support='unknown',
                 limitations=models.backend(model_type).LIMITATIONS)
    task_ref = store.put('agent_task', dict(resume_version=1, mode='native_typed', model_type=model_type,
        model_ref=model_ref, input_ref=input_ref, max_calls=max_calls, max_runs=1,
        provider={'class':type(provider).__module__+'.'+type(provider).__qualname__},
        recovery_support='not_supported; unknown effects block automatic retry'), refs=(model_ref, input_ref))
    journal = InvocationJournal(store, task_ref, initial_refs=(model_ref, input_ref))
    messages = [dict(role='system', content='Operate the supplied frozen native model only. '
        'Return {"action":NAME,"args":{}} with an optional short reason. Allowed: inspect_typed, run_typed, finish. '
        'Run at most once; no changes to type, model, units, settings, seed, data or budgets. '
        'Report numerical checks separately from scientific support, which stays unknown.'),
        dict(role='user', content=json.dumps(brief))]
    events = []; run_ref = None; state = 'incomplete'
    while journal.calls < max_calls:
        if sum(len(v['content']) for v in messages) > 60000:
            events.append(dict(type='context_budget_exceeded')); break
        try:
            response = journal.invoke('respond', lambda:provider.respond(messages),
                summarize=lambda r:dict(response_json=json.dumps(r, allow_nan=False)))
        except Exception:
            events.append(dict(type='provider_error', error='Provider failed; inspect receipts; no automatic retry.')); break
        events.append(dict(type='llm_call', response=response, call=journal.calls))
        if (not isinstance(response, dict) or not {'action','args'}<=set(response) or set(response)-{'action','args','reason'}
            or not isinstance(response['action'], str) or response['args']!={}
            or ('reason' in response and (not isinstance(response['reason'], str) or len(response['reason'])>1000))):
            events.append(dict(type='tool_error', error='Native typed actions require a known name and empty args.')); break
        action = response['action']; messages.append(dict(role='assistant', content=json.dumps(response)))
        if action=='inspect_typed':
            out = journal.invoke(action, lambda:dict(**brief, run_ref=run_ref, outcome=models.summary(store,run_ref) if run_ref else None))
        elif action=='run_typed':
            if journal.runs:
                events.append(dict(type='tool_error', error='Scientific run budget is consumed.')); break
            try:
                run_ref = journal.invoke(action, lambda:models.execute(store, input_ref),
                    details=dict(input_ref=input_ref), summarize=lambda r:dict(result_ref=r), result_refs=lambda r:[r])
            except Exception:
                events.append(dict(type='tool_error', error='No confirmed result; inspect receipts, no automatic retry.')); break
            out = models.summary(store, run_ref)
            if not out['completion_eligible']:
                events.append(dict(type='tool_result', tool=action, outcome=out)); break
        elif action=='finish':
            state = 'completed' if run_ref and models.summary(store,run_ref)['completion_eligible'] else 'incomplete'; break
        else:
            events.append(dict(type='tool_error', error='Unsupported native tool; no action executed.')); break
        events.append(dict(type='tool_result', tool=action, outcome=out))
        messages.append(dict(role='user', content=json.dumps(dict(tool=action, outcome=out))))
    refs = [task_ref, model_ref, input_ref, journal.execution_ref, *journal.refs, *([run_ref] if run_ref else [])]
    return store.put('typed_agent_run', dict(version=1, model_type=model_type, model_ref=model_ref,
        input_ref=input_ref, task_ref=task_ref, execution_ref=journal.execution_ref, invocation_refs=journal.refs,
        run_ref=run_ref, state=state, call_count=journal.calls, run_count=journal.runs, events=events,
        scientific_support='unknown', recovery_support='not_supported'), refs=tuple(refs))
