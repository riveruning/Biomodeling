"""Durable stop requests and project deadlines, checked at safe boundaries.

A stop never interrupts a callback, loses its receipt or retries its effects.
The stop channel deliberately does not take the running project's owner lock.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import time

from . import model_contracts as c

STOP='research_project_stop'
_checkpoint=ContextVar('research_checkpoint',default=None)
_remaining=ContextVar('research_remaining',default=None)


class ResearchStopped(RuntimeError):
    """No new operation may start; any in-flight result must be preserved."""


def status(store,project_ref,project):
    from .research_tasks import records
    stops=records(store,STOP,project_ref=project_ref)
    for ref,payload in stops:
        c.fields(payload,{'version','project_ref','actor','reason','requested_at'})
        if payload['version']!=1 or set(store.get(ref)['refs'])!={project_ref}:
            raise ValueError('invalid research stop binding')
        c.label(payload['actor'],200);c.label(payload['reason'],1000)
        if type(payload['requested_at']) not in (float,int) or not math.isfinite(payload['requested_at']):
            raise ValueError('invalid stop timestamp')
    deadline=project.get('deadline_at')
    remaining=max(0.,deadline-time.time()) if deadline is not None else None
    return dict(stop_requested=bool(stops),stop_refs=[r for r,_ in stops],
        deadline_at=deadline,remaining_s=remaining,deadline_exhausted=remaining==0,
        semantics='Stops apply between operations and inner tool/provider calls. In-flight work finishes and retains its receipt. The wall-clock deadline includes human pauses.')


def request_stop(store,project_ref,*,actor,reason):
    from .research_projects import load
    from .research_tasks import records
    load(store,project_ref)
    c.label(actor,200);c.label(reason,1000)
    # Content-addressed requests with the same attribution are idempotent.
    for ref,payload in records(store,STOP,project_ref=project_ref):
        if payload['actor']==actor and payload['reason']==reason:return ref
    return store.put(STOP,dict(version=1,project_ref=project_ref,actor=actor,reason=reason,requested_at=time.time()),refs=(project_ref,))


@contextmanager
def scope(store,project_ref,project):
    initial=status(store,project_ref,project)
    end=time.monotonic()+initial['remaining_s'] if initial['remaining_s'] is not None else None
    def check():
        current=status(store,project_ref,project)
        if current['stop_requested'] or current['deadline_exhausted'] or end is not None and time.monotonic()>=end:
            raise ResearchStopped('Research stopped at a safe boundary; saved results remain available.')
    def remaining():
        check()
        if end is None:return None
        return max(0.,min(end-time.monotonic(),status(store,project_ref,project)['remaining_s']))
    token=_checkpoint.set(check)
    budget_token=_remaining.set(remaining)
    try:yield
    finally:_remaining.reset(budget_token);_checkpoint.reset(token)


def checkpoint():
    check=_checkpoint.get()
    if check is not None:check()


def remaining_s(cap):
    """Cap existing per-operation timeouts without changing frozen settings."""
    current=_remaining.get()
    if current is None:return cap
    remaining=current()
    return min(cap,remaining) if remaining is not None else cap
