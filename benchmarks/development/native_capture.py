"""Bounded offline parsing of native Codex JSONL and shared delivery files.

No commands, model requests or imported programs are executed by this module.
Raw input remains authoritative; parsed observations never certify science.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID

MAX_BYTES = 8_000_000
MAX_EVENTS = 5000
ITEM_TYPES = {'agent_message', 'reasoning', 'command_execution', 'file_change',
              'mcp_tool_call', 'web_search', 'todo_list', 'plan_update'}


class DeliveryError(ValueError):
    def __init__(self, partial_files):
        super().__init__('invalid or incomplete artifact inventory')
        self.partial_files = partial_files


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        value = json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        # Reject finite-looking overflow such as 1e999, including unknown fields.
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, RecursionError, OverflowError, ValueError, TypeError):
        raise ValueError('invalid bounded JSON') from None


def integer(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError('invalid nonnegative integer')
    return value


def session_id(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError('invalid native session UUID')
    try:
        if str(UUID(value)) != value:
            raise ValueError('noncanonical native session UUID')
    except (ValueError, AttributeError):
        raise ValueError('invalid native session UUID') from None
    return value


def read_file(root, relative, limit=MAX_BYTES):
    """Read a bounded regular file; reject traversal and symlink components."""
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('invalid relative capture path')
    path = Path(relative)
    if path.is_absolute() or str(path) != relative or any(p in ('.', '..') for p in path.parts):
        raise ValueError('capture path must stay inside its explicit root')
    root = Path(root).absolute()
    candidate = root/path
    if root.is_symlink() or any(p.is_symlink() for p in (candidate, *candidate.parents) if p == root or root in p.parents):
        raise ValueError('capture symlink refused')
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('capture path escaped its explicit root')
    descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('capture input must be a regular file')
        raw = stream.read(limit+1)
    if len(raw) > limit:
        raise ValueError('capture byte budget exceeded')
    return raw


def _usage(value):
    keys = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens')
    if value is None:
        return {k: None for k in keys}
    if not isinstance(value, dict):
        raise ValueError('invalid usage object')
    result = {k: integer(value[k]) if value.get(k) is not None else None for k in keys}
    if result['input_tokens'] is not None and result['cached_input_tokens'] is not None:
        if result['cached_input_tokens'] > result['input_tokens']:
            raise ValueError('cached tokens exceed total input tokens')
    return result


def parse_events(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError('native event byte budget exceeded')
    result = {'status': 'incomplete_events', 'session_id': None, 'events_seen': 0,
              'usage': _usage(None), 'agent_messages': [], 'native_tool_items': 0,
              'diagnostics': [], 'raw_sha256': hashlib.sha256(raw).hexdigest()}
    started, terminal, errors, active, completed, item_types = False, None, False, set(), set(), {}
    try:
        lines = raw.splitlines()
        if len(lines) > MAX_EVENTS:
            raise ValueError('native event count budget exceeded')
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if len(line) > 256_000:
                raise ValueError('native event line budget exceeded')
            try:
                event = decode(line)
            except ValueError:
                if index == len(lines)-1 and not raw.endswith(b'\n') and terminal is None:
                    result['diagnostics'].append('truncated_last_line')
                    return result
                raise
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('invalid event object')
            if terminal is not None:
                raise ValueError('events after terminal event refused')
            kind = event['type']
            if kind == 'thread.started':
                if result['session_id'] is not None or started:
                    raise ValueError('multiple or misplaced thread events')
                result['session_id'] = session_id(event.get('thread_id'))
            elif kind == 'turn.started':
                if started or result['session_id'] is None:
                    raise ValueError('misplaced turn start')
                started = True
            elif kind in ('turn.completed', 'turn.failed'):
                if not started:
                    raise ValueError('terminal event without turn start')
                terminal = kind
                if kind == 'turn.completed':
                    result['usage'] = _usage(event.get('usage'))
                    if isinstance(event.get('usage'), dict):
                        unknown = sorted(set(event['usage'])-set(result['usage']))
                        if unknown:
                            result['diagnostics'].append('unmapped_usage_fields:'+','.join(unknown))
                else:
                    errors = True
            elif kind == 'error':
                errors = True
                result['diagnostics'].append('native_error_event')
            elif kind in ('item.started', 'item.updated', 'item.completed'):
                if not started:
                    raise ValueError('item before turn start')
                item = event.get('item')
                if not isinstance(item, dict) or item.get('type') not in ITEM_TYPES:
                    raise ValueError('unsupported native item type')
                ident = item.get('id')
                if not isinstance(ident, str) or not 1 <= len(ident) <= 200 or ident in completed:
                    raise ValueError('invalid or repeated completed item')
                if ident in item_types and item_types[ident] != item['type']:
                    raise ValueError('native item type changed')
                item_types[ident] = item['type']
                if kind == 'item.started':
                    if ident in active:
                        raise ValueError('duplicate item start')
                    active.add(ident)
                elif kind == 'item.updated':
                    if ident not in active:
                        raise ValueError('item update without start')
                else:
                    active.discard(ident)
                    completed.add(ident)
                    if item['type'] == 'agent_message':
                        if not isinstance(item.get('text'), str):
                            raise ValueError('invalid native agent message')
                        result['agent_messages'].append(item['text'])
                    if item['type'] in ('command_execution', 'mcp_tool_call', 'web_search'):
                        result['native_tool_items'] += 1
            else:
                raise ValueError('unsupported native event type')
            result['events_seen'] += 1
        if errors:
            result['status'] = 'provider_error'
        elif terminal == 'turn.completed' and not active:
            result['status'] = 'finished'
        elif active:
            result['diagnostics'].append('unfinished_items')
    except ValueError as exc:
        result['status'] = 'invalid_events'
        result['diagnostics'].append(str(exc))
    return result


def read_delivery(root):
    """A common inventory; native programs/SBML are kept as opaque bytes."""
    root = Path(root)
    if not (root/'delivery.json').exists():
        return {'state': 'not_submitted', 'execution_declared': None,
                'question_texts': [], 'artifacts': []}, {}
    value = decode(read_file(root, 'delivery.json', 100_000))
    if not isinstance(value, dict) or set(value) != {'state', 'execution_declared', 'question_texts', 'artifacts'}:
        raise ValueError('invalid shared delivery inventory')
    if value['state'] not in ('completed', 'awaiting_input', 'incomplete'):
        raise ValueError('invalid delivery state')
    if value['execution_declared'] is not None and type(value['execution_declared']) is not bool:
        raise ValueError('invalid declared execution flag')
    questions = value['question_texts']
    if not isinstance(questions, list) or len(questions) > 20 or any(
            not isinstance(q, str) or not 1 <= len(q) <= 8000 for q in questions):
        raise ValueError('invalid shared question list')
    if not isinstance(value['artifacts'], list) or len(value['artifacts']) > 64:
        raise ValueError('delivery inventory count budget exceeded')
    blobs = {}
    total = 0
    for artifact in value['artifacts']:
        if not isinstance(artifact, dict) or set(artifact) != {'path', 'role', 'sha256'}:
            raise ValueError('invalid delivery artifact')
        if artifact['role'] not in ('program', 'trajectory', 'sbml', 'report', 'citations'):
            raise ValueError('unsupported delivery artifact role')
        name = artifact['path']
        try:
            raw = read_file(root, name)
            total += len(raw)
            if name in blobs or name == 'delivery.json' or total > MAX_BYTES:
                raise ValueError('duplicate or oversized delivery inventory')
            if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                raise ValueError('delivery artifact hash mismatch')
        except (ValueError, OSError):
            raise DeliveryError(blobs) from None
        blobs[name] = raw
    return value, blobs
