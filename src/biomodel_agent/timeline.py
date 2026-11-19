"""Read-only, bounded rendering of saved Agent and modeling-workflow events.

Row order is event order, not inferred wall-clock timing or scientific proof.
"""
from __future__ import annotations

from html import escape
import re

# Generic saved-event records accept the historical minimal projection shape.
KINDS = {'agent_run', 'agent_checkpoint', 'fba_agent_run', 'typed_agent_run', 'modeling_plan_run'}
# These roots additionally require their authoritative dependency bindings.
VERIFIED_KINDS = {'research_task_run'}
REF = re.compile(r'^[0-9a-f]{64}$')


def _text(value, limit=500):
    if not isinstance(value, str):
        return ''
    return ''.join(c if ord(c) >= 32 or c in '\n\t' else ' ' for c in value)[:limit]


def _signals(event):
    """Collect only explicit check/outcome facts, with bounded traversal."""
    statuses, refs = [], []
    pending = [(event, 0)]; visited = 0
    while pending:
        item, depth = pending.pop(); visited += 1
        if visited > 2000:
            statuses.append('unknown'); break
        if depth > 8:
            statuses.append('unknown'); continue
        if isinstance(item, dict):
            for key, value in item.items():
                if key in ('status', 'state', 'scientific_status', 'numeric_status', 'comparison_status') and isinstance(value, str):
                    statuses.append(value)
                elif key == 'ok' and value is False:
                    statuses.append('failed')
                elif key == 'completion_eligible' and value is False:
                    statuses.append('not_eligible')
                elif key in ('checks', 'outcome', 'validation_feedback', 'items', 'summary', 'numeric_validation', 'comparison'):
                    pending.append((value, depth + 1))
                if key in ('artifact_refs', 'model_ref', 'run_ref', 'draft_ref', 'plan_ref',
                           'compilation_ref', 'application_run_ref', 'receipt_ref', 'task_ref', 'result_ref'):
                    for ref in value if isinstance(value, (list, tuple)) else (value,):
                        if isinstance(ref, str) and REF.fullmatch(ref) and ref not in refs:
                            refs.append(ref)
        elif isinstance(item, (list, tuple)):
            pending.extend((value, depth + 1) for value in item[:2000])
            if len(item) > 2000:
                statuses.append('unknown')
    return list(dict.fromkeys(statuses)), refs


def _row(index, event):
    if not isinstance(event, dict):
        return dict(index=index, call=None, event_type='invalid_event', tool=None,
                    status='unknown', summary='Saved event is not an object.', artifact_refs=[], check_states=[])
    typ = _text(event.get('type'), 100) or 'unknown_event'
    response = event.get('response') if isinstance(event.get('response'), dict) else {}
    tool = event.get('tool') or event.get('action') or response.get('action')
    tool = _text(tool, 100) or None
    signals, refs = _signals(event)
    failed = any(s in ('fail', 'failed', 'invalid', 'error', 'timed_out', 'unsupported') for s in signals)
    if typ in ('tool_error', 'provider_error') or failed:
        status = 'failed'
    elif typ == 'awaiting_input' or event.get('state') == 'awaiting_input':
        status = 'waiting'
    elif typ in ('plan_blocked', 'tool_refused') or 'not_eligible' in signals:
        status = 'blocked'
    elif any(s in ('unknown', 'not_verified', 'not_run') for s in signals):
        status = 'unknown'
    elif typ == 'llm_call':
        status = 'proposed'
    elif typ in ('tool_result', 'compilation', 'execution_returned'):
        status = 'returned'
    else:
        status = 'unknown'
    pieces = []
    for key in ('question', 'error', 'message', 'reason', 'next_action'):
        if text := _text(event.get(key)):
            pieces.append(text)
    for problem in event.get('problems', []) if isinstance(event.get('problems'), list) else []:
        if text := _text(problem):
            pieces.append(text)
    if not pieces and typ == 'llm_call':
        pieces.append(_text(response.get('reason')) or f'Proposed action: {tool or "unknown"}')
    call = event.get('call')
    return dict(index=index, call=call if type(call) is int and call >= 0 else None,
        event_type=typ, tool=tool, status=status, summary='; '.join(pieces)[:500] or 'See saved artifact/checks.',
        artifact_refs=refs, check_states=signals)


def project(payload, *, root_ref, kind):
    if kind not in KINDS | VERIFIED_KINDS:
        raise ValueError('timeline requires a supported Agent/checkpoint/modeling-plan run')
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list) or len(payload['events']) > 10000:
        raise ValueError('timeline requires an object with at most 10000 saved events')
    def count(key):
        value = payload.get(key)
        return value if type(value) is int and value >= 0 else None
    # Report an explicit saved support field, never infer it from completion.
    support = payload.get('scientific_support')
    return dict(version=1, root_ref=root_ref, kind=kind, state=_text(payload.get('state'), 100) or 'unknown',
        call_count=count('call_count'), run_count=count('run_count'),
        scientific_support=_text(support, 100) or 'unknown',
        support_origin='saved_label_only_not_independent_verification',
        rows=[_row(i, event) for i, event in enumerate(payload['events'])])


def from_run(store, ref):
    record = store.get(ref)
    if record['kind']=='research_task_run':
        from .research_tasks import result
        result(store,ref)
    return project(record['payload'], root_ref=ref, kind=record['kind'])


def cell(value):
    if value is None:
        return 'unknown'
    raw = str(value)
    text = _text(raw, 3000)
    if len(raw) > 3000:
        text += ' … [truncated; inspect saved artifact]'
    text = escape(text, quote=False).replace('\\', '\\\\')
    for symbol in ('|', '`', '*', '_', '[', ']'):
        text = text.replace(symbol, '\\' + symbol)
    return text.replace('\n', '<br>').replace('\t', ' ')


def render_markdown(projection):
    lines = ['# Saved task timeline', '', f'Artifact: {cell(projection["root_ref"])}',
        f'Kind: {cell(projection["kind"])}; state: **{cell(projection["state"])}**',
        f'Model calls: {cell(projection["call_count"])}; scientific run attempts/reservations: {cell(projection["run_count"])}',
        f'Saved scientific-support label: {cell(projection["scientific_support"])} (not independent verification).', '',
        'Indices are saved event order, not timestamps. Returned tools do not establish scientific correctness.', '',
        '| Event | Call | Tool/type | Recorded outcome | Detail | Artifact references |',
        '|---|---|---|---|---|---|']
    for row in projection['rows']:
        detail = row['summary']
        if row['check_states']:
            detail += ' Checks: ' + ', '.join(row['check_states'])
        values = [row['index'], row['call'], row['tool'] or row['event_type'], row['status'],
                  detail, ', '.join(row['artifact_refs'])]
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    return '\n'.join(lines) + '\n'
