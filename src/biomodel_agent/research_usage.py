"""Project-scoped inner provider accounting without credentials or replay."""
import math
import time
from uuid import uuid4

from .research_control import checkpoint

CALL='research_native_provider_call'
RETURN='research_native_provider_return'
OPERATION_ELAPSED_SEMANTICS=('Per-operation callback/perform handling measured by a monotonic clock. '
    'It already includes planner and nested native provider work, so do not add their separate elapsed '
    'summaries again. It excludes human alignment waits and whole-project wall-clock time. Empty groups '
    'are zero; old, interrupted, unreturned or invalid elapsed receipts remain missing, not zero.')


def monotonic():
    """Local monotonic source for operation timing; tests may replace it."""
    return time.monotonic()


def _valid_elapsed(value):
    if type(value) is int:return value>=0
    if type(value) is float:return math.isfinite(value) and value>=0
    return False


def elapsed_since(started):
    duration=monotonic()-started
    return duration if _valid_elapsed(duration) else None


def operation_elapsed(attempts,returns,operations):
    groups={name:dict(attempts=0,recorded_elapsed_s=0.,missing_elapsed_attempts=0,complete_elapsed_s=None)
            for name in operations}
    totals=dict(attempts=0,recorded_elapsed_s=0.,missing_elapsed_attempts=0)
    for ref,attempt in attempts:
        name=attempt['operation'];group=groups.get(name)
        if group is None:raise ValueError('unknown research operation')
        group['attempts']+=1;totals['attempts']+=1
        result=returns.get(ref);duration=result[1].get('elapsed_s') if result else None
        if _valid_elapsed(duration):
            group['recorded_elapsed_s']+=duration;totals['recorded_elapsed_s']+=duration
        else:
            group['missing_elapsed_attempts']+=1;totals['missing_elapsed_attempts']+=1
    for group in groups.values():
        if group['missing_elapsed_attempts']==0:group['complete_elapsed_s']=group['recorded_elapsed_s']
    totals['complete_elapsed_s']=totals['recorded_elapsed_s'] if totals['missing_elapsed_attempts']==0 else None
    return dict(semantics=OPERATION_ELAPSED_SEMANTICS,by_operation=groups,**totals)


def reported_usage(provider):
    value=getattr(provider,'last_usage',None)
    if not isinstance(value,dict):return {}
    return {k:v for k,v in value.items() if k in ('prompt_tokens','completion_tokens','total_tokens')
            and type(v) is int and 0<=v<=10**12}


class MeteredProvider:
    def __init__(self,provider,store,project_ref,iteration_ref):
        self.provider=provider;self.store=store;self.project_ref=project_ref;self.iteration_ref=iteration_ref

    def __getattr__(self,name):return getattr(self.provider,name)

    def respond(self,messages):
        checkpoint()
        from .provider_identity import identity
        attempt=self.store.put(CALL,dict(version=1,id=uuid4().hex,project_ref=self.project_ref,
            iteration_ref=self.iteration_ref,provider=identity(self.provider)),refs=(self.project_ref,self.iteration_ref))
        started=time.monotonic()
        try:response=self.provider.respond(messages)
        except BaseException as exc:
            self._save(attempt,started,'error',type(exc).__name__)
            raise
        self._save(attempt,started,'returned',None)
        return response

    def _save(self,attempt,started,state,error_type):
        self.store.put(RETURN,dict(version=1,project_ref=self.project_ref,iteration_ref=self.iteration_ref,
            attempt_ref=attempt,state=state,error_type=error_type,usage=reported_usage(self.provider),
            elapsed_s=time.monotonic()-started),refs=(self.project_ref,self.iteration_ref,attempt))


def summary(store,project_ref,expected_calls):
    from .research_tasks import records
    attempts=dict(records(store,CALL,project_ref=project_ref));returns={}
    for ref,ret in records(store,RETURN,project_ref=project_ref):
        attempt=ret['attempt_ref'];call=attempts.get(attempt)
        if (call is None or attempt in returns or ret['iteration_ref']!=call['iteration_ref']
                or set(store.get(ref)['refs'])!={project_ref,ret['iteration_ref'],attempt}):
            raise ValueError('inner provider usage binding mismatch')
        returns[attempt]=ret
    known=0;missing=0;elapsed=0.;missing_elapsed=0
    for attempt,call in attempts.items():
        if set(store.get(attempt)['refs'])!={project_ref,call['iteration_ref']}:
            raise ValueError('inner provider call binding mismatch')
        ret=returns.get(attempt,{})
        tokens=ret.get('usage',{}).get('total_tokens')
        if type(tokens) is int and 0<=tokens<=10**12:known+=tokens
        else:missing+=1
        duration=ret.get('elapsed_s')
        if type(duration) in (float,int) and 0<=duration<1e12:elapsed+=duration
        else:missing_elapsed+=1
    complete=expected_calls is not None and expected_calls==len(attempts)
    return dict(metered_calls=len(attempts),expected_calls=expected_calls,reported_total_tokens=known,
        missing_usage_calls=missing,recorded_elapsed_s=elapsed,missing_elapsed_calls=missing_elapsed,
        complete_total_tokens=known if complete and missing==0 else None,
        complete_elapsed_s=elapsed if complete and missing_elapsed==0 else None,
        call_refs=list(attempts),return_refs=[r for r,_ in records(store,RETURN,project_ref=project_ref)])
