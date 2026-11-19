"""Over-budget requests compact only re-readable state projections."""
import json

import pytest

from biomodel_agent import context


def _messages(schemas_chars=20000, scenario_chars=10000):
    return [
        {'role': 'system', 'content': 'system instructions'},
        {'role': 'user', 'content': '{"task":"frozen"}'},
        {'role': 'user', 'content': json.dumps({
            'tool': 'inspect_progress',
            'project': {'project_ref': 'p', 'scenario': 'S' * scenario_chars},
            'action_schemas': {'x': 'A' * schemas_chars},
            'budget': {'planner_calls': 3},
        })},
    ]


def test_oversized_state_projection_is_compacted_explicitly():
    messages = _messages()
    assert context.size(messages) > 8000
    packet, audit = context.build_context(messages, context.policy(8000))
    assert audit['projection_compacted'] is True
    state = json.loads(packet[-1]['content'])
    assert state['projection_compacted']['history_unchanged'] is True
    assert state['action_schemas']['omitted_for_budget'] is True
    assert state['action_schemas']['retrieve_with'] == 'inspect_research_guidance'
    assert state['project']['scenario']['omitted_for_budget']
    assert state['budget'] == {'planner_calls': 3}
    # History is never modified by packet construction.
    assert messages[-1]['content'].count('A' * 100) == 200


def test_scientific_message_without_projection_still_fails_closed():
    messages = [
        {'role': 'system', 'content': 'system instructions'},
        {'role': 'user', 'content': '{"task":"frozen"}'},
        {'role': 'user', 'content': json.dumps({'tool': 'inspect_source',
                                                'result': {'text': 'Q' * 9000}})},
    ]
    with pytest.raises(context.ContextBudgetExceeded):
        context.build_context(messages, context.policy(8000))
