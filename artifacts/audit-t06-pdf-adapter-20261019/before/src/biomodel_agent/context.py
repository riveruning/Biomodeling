"""Deterministic working-context selection with an immutable full audit trail.

No model summaries or truncation of scientific statements. The budget counts
serialized message characters, not provider tokens or the complete HTTP body.
"""
from __future__ import annotations

from copy import deepcopy
import json

NOTICE = {'role': 'user', 'content': json.dumps({
    'tool': 'context_notice',
    'notice': 'Some older messages are omitted from this request; the full history remains archived. '
              'Omission does not mean a fact was resolved or evidence was verified. '
              'Re-read authorized sources when needed. Task scope, tool and execution budgets remain unchanged.'
}, ensure_ascii=False, sort_keys=True)}


class ContextBudgetExceeded(ValueError):
    def __init__(self, required_chars, max_chars):
        super().__init__(f'Essential context requires {required_chars} message-JSON characters; '
                         f'budget is {max_chars}. Start a new task with an appropriate context budget.')
        self.required_chars = required_chars
        self.max_chars = max_chars


def policy(max_chars=60000):
    if type(max_chars) is not int or not 8000 <= max_chars <= 500000:
        raise ValueError('context_max_chars must be an integer in 8000..500000')
    return {'version': 1, 'max_chars': max_chars}


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != {'version', 'max_chars'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('unsupported context policy')
    return policy(value['max_chars'])


def size(messages):
    """JSON character length of the messages array, with consistent encoding."""
    return len(json.dumps(messages, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def _payload(message):
    try:
        data = json.loads(message['content'])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def essential_indices(messages):
    """Keep original instructions/task, every attributed reply and latest state."""
    selected = {0, 1, len(messages) - 1}
    latest = {}
    for i, message in enumerate(messages):
        data = _payload(message)
        if message['role'] == 'user':
            tool = data.get('tool')
            if tool == 'researcher_reply':
                selected.add(i)
            elif tool in ('submit_draft', 'run', 'run_study'):
                latest[tool] = i
                if tool in ('run', 'run_study') and data.get('ok') is True:
                    latest[tool + '_result'] = i
        elif message['role'] == 'assistant' and data.get('action') == 'submit_draft':
            latest['draft_action'] = i
    selected.update(latest.values())
    return selected


def build_context(messages, frozen_policy):
    """Return a fresh request packet and selection audit; never modify history."""
    settings = validate_policy(frozen_policy)
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError('context requires system instructions and a task message')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('system', 'user', 'assistant')
                or not isinstance(message['content'], str)):
            raise ValueError('invalid context message')
    if messages[0]['role'] != 'system' or messages[1]['role'] != 'user':
        raise ValueError('context must start with system instructions and the frozen task')
    if any(m['role'] == 'system' for m in messages[1:]):
        raise ValueError('additional system messages are not part of the task protocol')
    original_chars = size(messages)
    maximum = settings['max_chars']
    if original_chars <= maximum:
        return deepcopy(messages), {'method': 'bounded_context_v1', 'compacted': False,
            'original_chars': original_chars, 'sent_chars': original_chars,
            'max_chars': maximum, 'omitted_messages': 0, 'retained_indices': list(range(len(messages)))}
    selected = essential_indices(messages)
    def packet(indices):
        ordered = sorted(indices)
        return [deepcopy(messages[i]) for i in ordered[:2]] + [deepcopy(NOTICE)] + [deepcopy(messages[i]) for i in ordered[2:]]
    required = size(packet(selected))
    if required > maximum:
        raise ContextBudgetExceeded(required, maximum)
    # Preserve whole recent messages while space remains. Never shorten quotes,
    # replies, draft values or validation diagnostics to make them fit.
    for i in range(len(messages) - 1, 1, -1):
        if i in selected:
            continue
        tentative = selected | {i}
        if size(packet(tentative)) <= maximum:
            selected = tentative
    request = packet(selected)
    return request, {'method': 'bounded_context_v1', 'compacted': True,
        'original_chars': original_chars, 'sent_chars': size(request), 'max_chars': maximum,
        'omitted_messages': len(messages) - len(selected), 'retained_indices': sorted(selected)}
