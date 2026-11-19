"""SSE transport: a long reasoning trace assembles into the normal envelope."""
import json

import httpx

from biomodel_agent.llm import ChatProvider
from biomodel_agent.provider_identity import identity


def _sse(*events):
    lines = ["data: " + json.dumps(event) for event in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def _provider(monkeypatch, body, **kwargs):
    monkeypatch.setenv('BIOMODEL_TEST_PROVIDER_KEY', 'offline-dummy')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    return ChatProvider('https://offline.invalid/v1', 'offline-control',
                        api_key_env='BIOMODEL_TEST_PROVIDER_KEY',
                        transport=transport, **kwargs)


def test_streamed_deltas_become_one_action(monkeypatch):
    body = _sse(
        {"choices": [{"delta": {"content": '{"action": "inspect_'}}]},
        {"choices": [{"delta": {"content": 'progress"}'}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}},
    )
    provider = _provider(monkeypatch, body, stream=True, json_mode=True,
                         max_tokens=12000, reasoning_effort='max')
    result = provider.respond([{'role': 'user', 'content': 'go'}])
    assert result['action'] == 'inspect_progress'
    assert provider.last_usage['total_tokens'] == 12
    assert provider.last_response_diagnostic['finish_reason'] == 'stop'
    assert identity(provider)['stream'] is True


def test_streamed_usage_missing_stays_missing(monkeypatch):
    body = _sse({"choices": [{"delta": {"content": '{"action": "inspect_progress"}'},
                              "finish_reason": "stop"}]})
    provider = _provider(monkeypatch, body, stream=True, json_mode=True)
    result = provider.respond([])
    assert result['action'] == 'inspect_progress'
    assert provider.last_usage == {}
