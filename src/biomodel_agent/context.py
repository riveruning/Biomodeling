"""Deterministic working-context selection with an immutable full audit trail.

No model summaries or truncation of scientific statements. The budget counts
serialized message characters, not provider tokens or the complete HTTP body.
"""
from __future__ import annotations

from copy import deepcopy
import json

NOTICE = {'role': 'user', 'content': json.dumps({
    'tool': 'context_notice',
    'notice': 'History is archived. Omitted facts remain unresolved; re-read authorized sources. Task scope and budgets remain unchanged.'
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
            if tool == 'load_skill' and data.get('scientific_skill_ref') and data.get('ok') is True:
                latest['scientific_skill:'+data['name']] = i
            if tool == 'researcher_reply':
                selected.add(i)
            elif tool in ('submit_draft', 'submit_plan', 'run', 'run_study', 'run_fba', 'inspect_fba', 'run_typed', 'inspect_typed', 'request_estimation', 'run_estimation', 'inspect_progress', 'investigate_parameter', 'assess_parameter', 'revise_plan', 'refresh_plan', 'route_failure'):
                latest[tool] = i
                if tool in ('run', 'run_study', 'run_estimation', 'run_fba', 'run_typed') and data.get('ok') is True:
                    latest[tool + '_result'] = i
        elif message['role'] == 'assistant' and data.get('action') in ('submit_draft', 'submit_plan'):
            latest['draft_action'] = i
    selected.update(latest.values())
    return selected


def _compact_projection(message):
    """Bounded projection for derived state messages (never scientific evidence).

    Only the ``inspect_progress`` projection is compacted: action schemas and
    the scenario copy are re-readable with ``inspect_research_guidance`` and the
    frozen task message. History is never modified; the packet marks exactly
    which fields were omitted.
    """
    data = _payload(message)
    if data.get('tool') != 'inspect_progress':
        return message
    out = deepcopy(data)
    omitted = []
    if 'action_schemas' in out:
        out['action_schemas'] = {'omitted_for_budget': True,
                                 'retrieve_with': 'inspect_research_guidance'}
        omitted.append('action_schemas')
    project = out.get('project')
    if isinstance(project, dict) and 'scenario' in project:
        project['scenario'] = {'omitted_for_budget': 'identical to the frozen task message'}
        omitted.append('project.scenario')
    out['projection_compacted'] = {'omitted_fields': omitted,
                                   'history_unchanged': True,
                                   'retrieve_with': 'inspect_progress / inspect_research_guidance'}
    return {**message, 'content': json.dumps(out, ensure_ascii=False)}


def build_context(messages, frozen_policy, *, required_indices=()):
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
    required_indices = tuple(required_indices)
    if any(type(index) is not int or not 0 <= index < len(messages) for index in required_indices):
        raise ValueError('required context index outside message history')
    original_chars = size(messages)
    maximum = settings['max_chars']
    if original_chars <= maximum:
        return deepcopy(messages), {'method': 'bounded_context_v1', 'compacted': False,
            'original_chars': original_chars, 'sent_chars': original_chars,
            'max_chars': maximum, 'omitted_messages': 0, 'retained_indices': list(range(len(messages)))}
    selected = essential_indices(messages) | set(required_indices)
    def packet(indices):
        ordered = sorted(indices)
        omitted_tools = {}
        for i, message in enumerate(messages):
            if i in indices or message['role'] != 'user':
                continue
            tool = _payload(message).get('tool')
            if isinstance(tool, str) and tool:
                name = tool[:64]
                omitted_tools[name] = omitted_tools.get(name, 0) + 1
        notice = json.loads(NOTICE['content'])
        notice['context_selection'] = dict(
            total_messages=len(messages), retained_messages=len(indices),
            omitted_messages=len(messages)-len(indices),
            retained_message_fraction=len(indices)/len(messages),
            omitted_tool_counts=dict(sorted(omitted_tools.items())[:16]),
            omitted_tool_categories_truncated=len(omitted_tools)>16,
            source_coverage='unknown')
        warning = dict(role='user', content=json.dumps(notice, ensure_ascii=False, sort_keys=True))
        return [deepcopy(messages[i]) for i in ordered[:2]] + [warning] + [deepcopy(messages[i]) for i in ordered[2:]]
    required = size(packet(selected))
    compacted_projection = False
    if required > maximum:
        # Derived state projections are re-readable; compact them explicitly
        # before failing the request. Scientific statements are never shortened.
        for index in sorted(selected, reverse=True):
            if _payload(messages[index]).get('tool') != 'inspect_progress':
                continue
            trial = set(selected)
            replaced = list(packet(trial))
            for position, message in enumerate(replaced):
                if message is not None and _payload(message).get('tool') == 'inspect_progress':
                    replaced[position] = _compact_projection(message)
            if size(replaced) <= maximum:
                request = replaced
                compacted_projection = True
                break
        if not compacted_projection:
            raise ContextBudgetExceeded(required, maximum)
    # Preserve whole recent messages while space remains. Never shorten quotes,
    # replies, draft values or validation diagnostics to make them fit.
    for i in range(len(messages) - 1, 1, -1):
        if i in selected:
            continue
        tentative = selected | {i}
        if size(packet(tentative)) <= maximum:
            selected = tentative
    if not compacted_projection:
        request = packet(selected)
    return request, {'method': 'bounded_context_v1', 'compacted': True,
        'projection_compacted': compacted_projection,
        'original_chars': original_chars, 'sent_chars': size(request), 'max_chars': maximum,
        'omitted_messages': len(messages) - len(selected), 'retained_indices': sorted(selected)}
