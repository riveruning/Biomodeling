"""Execution and feedback normalization for the agent's read/query actions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = ('READ_ACTIONS', 'ReadToolOutcome', 'execute_read_tool')

READ_ACTIONS = frozenset({
    'inspect_progress', 'investigate_parameter', 'search_knowledge',
    'search_sources', 'recall_memory', 'load_skill', 'inspect_source',
})


@dataclass(frozen=True)
class ReadToolOutcome:
    event: dict
    message: dict
    checkpoint: bool
    stop: bool = False
    state: str | None = None


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _is_int(value: Any) -> bool:
    return type(value) is int


def execute_read_tool(action, args, *, store, source_refs, retrieval_method,
                      knowledge_databases, memory_scope, skill_catalog,
                      skill_protocol, read_limit, invoke, inspect_progress,
                      source_text, call_count=None) -> ReadToolOutcome:
    """Execute one read/query action and return its normalized feedback.

    The caller owns event/message ordering, event call-count stamping and
    checkpoint persistence.  Callbacks preserve runtime state: inspect_progress
    reads the live loop snapshot, source_text remains bound to the agent helper,
    and call_count keeps the invalid-span feedback's original call stamp.
    """
    if action in ('inspect_progress', 'investigate_parameter'):
        try:
            if action == 'inspect_progress':
                if args:
                    raise ValueError('inspect_progress takes no arguments')
                out = invoke(action, inspect_progress)
            else:
                from .parameter_investigation import investigate
                if set(args) != {'target', 'query'}:
                    raise ValueError('investigate_parameter requires exactly target and query')
                out = invoke(action, lambda: investigate(store, source_refs, args['target'],
                                                         args['query'], method=retrieval_method),
                             details={'args': args},
                             result_refs=lambda r: [r['investigation_ref']])
            feedback = {'tool': action, 'ok': True, **out}
            event = {'type': 'tool_result', 'tool': action,
                     'artifact_refs': ([out['investigation_ref']]
                                       if action == 'investigate_parameter' else [])}
        except (ValueError, TypeError) as exc:
            feedback = {'tool': action, 'ok': False, 'error': str(exc)}
            event = {'type': 'tool_error', **feedback}
        return ReadToolOutcome(event=event, message=_user(_json(feedback)), checkpoint=True)

    if action == 'search_knowledge':
        try:
            from . import knowledge
            if set(args) != {'database', 'query'} or args['database'] not in knowledge_databases:
                raise ValueError('search_knowledge requires an enabled database and query')
            out = invoke(action, lambda: knowledge.search(store, args['database'], args['query']),
                         details={'args': args},
                         result_refs=lambda result: (result['search_ref'],))
            feedback = {'tool': action, 'ok': True, **out}
            event = {'type': 'tool_result', 'tool': action,
                     'search_ref': out['search_ref'], 'artifact_refs': [out['search_ref']]}
        except (ValueError, TypeError) as exc:
            feedback = {'tool': action, 'ok': False, 'error': str(exc)}
            event = {'type': 'tool_error', **feedback}
        return ReadToolOutcome(event=event, message=_user(_json(feedback)), checkpoint=True)

    if action in ('search_sources', 'recall_memory', 'load_skill'):
        try:
            expected = {'name'} if action == 'load_skill' else {'query'}
            if set(args) != expected:
                raise ValueError(f'{action} requires exactly {sorted(expected)}')
            if action == 'search_sources':
                from .retrieval import search_sources
                out = invoke(action, lambda: search_sources(store, source_refs, args['query'],
                                                            method=retrieval_method),
                             details={'args': args})
            elif action == 'recall_memory':
                from . import memory
                if memory_scope is None:
                    raise ValueError('memory is not enabled for this task')
                out = invoke(action, lambda: memory.recall(store, memory_scope, args['query'],
                                                           source_refs=source_refs),
                             details={'args': args})
            else:
                from . import skills
                advertised = {item['name']: item['version'] for item in skill_catalog}
                if args['name'] not in advertised:
                    raise ValueError('skill is not in the frozen task catalog; start a new task')

                def load_procedure():
                    if skill_protocol and args['name'] in skill_protocol['entries']:
                        from . import scientific_skills as procedures
                        return procedures.load(store, skill_protocol, args['name'])
                    return skills.load_skill(args['name'])

                out = invoke('load_skill', load_procedure, details={'args': args},
                             result_refs=lambda r: [r['scientific_skill_ref']]
                             if r.get('scientific_skill_ref') else [])
                if advertised.get(out['name']) != out['version']:
                    raise ValueError('skill is not in the frozen task catalog; start a new task')
            feedback = {'tool': action, 'ok': True, **out}
            event = {key: value for key, value in feedback.items()
                     if key not in ('results', 'steps')}
            if action in ('search_sources', 'recall_memory'):
                event['results'] = [{key: value for key, value in hit.items()
                                     if key not in ('text', 'content')}
                                    for hit in out['results']]
            if out.get('scientific_skill_ref'):
                event['artifact_refs'] = [out['scientific_skill_ref']]
            event = {'type': 'tool_result', **event}
        except (ValueError, TypeError) as exc:
            feedback = {'tool': action, 'ok': False, 'error': str(exc)}
            event = {'type': 'tool_error', **feedback}
        return ReadToolOutcome(event=event, message=_user(_json(feedback)), checkpoint=True)

    if action == 'inspect_source':
        if set(args) != {'source_ref', 'page', 'start', 'end'}:
            error = 'inspect_source args must be exactly source_ref,page,start,end'
            return ReadToolOutcome(
                event={'type': 'tool_error', 'tool': action, 'error': error},
                message=_user(_json({'tool': action, 'ok': False, 'error': error})),
                checkpoint=False, stop=True, state='failed')
        ref, page, start, end = (args['source_ref'], args['page'],
                                 args['start'], args['end'])
        if (ref not in source_refs or not _is_int(page) or not _is_int(start)
                or not _is_int(end) or start < 0 or end <= start
                or end - start > read_limit):
            error = 'source/page/start/end outside task bounds'
            return ReadToolOutcome(
                event={'type': 'tool_error', 'tool': action, 'error': error},
                message=_user(_json({'tool': action, 'ok': False, 'error': error})),
                checkpoint=False, stop=True, state='failed')
        text = invoke('inspect_source', lambda: source_text(ref, page),
                      details={'args': args},
                      summarize=lambda t: {'page_found': t is not None})
        if text is None or page < 1 or end > len(text):
            # The old branch reused one dict for the event and model feedback;
            # the caller stamps the event, while the feedback keeps its call.
            error = {'type': 'tool_error', 'tool': action, 'error': 'invalid source span',
                     'available_pages': [{'page': p['number'], 'chars': len(p['text'])}
                                         for p in store.get(ref)['payload']['pages']]}
            feedback = dict(error)
            if call_count is not None:
                feedback['call'] = call_count()
            return ReadToolOutcome(event=error, message=_user(_json(feedback)),
                                   checkpoint=False)
        excerpt = text[start:end]
        out = {'tool': action, 'ok': bool(excerpt), 'source_ref': ref, 'page': page,
               'start': start, 'end': end, 'length': len(excerpt), 'text': excerpt}
        from .pdf_adapter import locate
        locator = locate(store, ref, page, start, end)
        if locator is not None:
            out['locator'] = locator
        event = {'type': 'tool_result',
                 **{key: value for key, value in out.items() if key != 'text'}}
        return ReadToolOutcome(event=event, message=_user(_json(out)), checkpoint=False)

    raise ValueError(f'unsupported read action: {action}')
