"""Round-51 output-budget contract tests (deterministic; no network, no paid calls).

Baseline reproduces the frozen production classification evidenced by the real
round-48 receipts (variant-gonze attempt 9705ad3827aa / variant-plasmid
e130d9a7a9f3): a received, billed, service-terminated 200 response with
``finish_reason=length`` and ``content=None`` surfaced as a generic
``response_envelope`` ProviderError, which the supervisor/project treated as an
UNKNOWN outcome and froze into needs_recovery.

The variant runs the REAL ChatProvider with the same desensitized response
shape and asserts the known output-budget outcome: usage/finish/attempt
attribution preserved, no replay, no same-cap retry, and empty content never
parsed into a success action.
"""
import json
import os

import httpx
import pytest

from biomodel_agent import research_projects as p, research_supervisor
from biomodel_agent.llm import (ChatProvider, ModelResponseError,
                                OutputBudgetExhausted, ProviderError)

# Real desensitized receipt shape (round-48 variant-gonze planner call 2):
# content None, finish_reason length, completion_tokens 6000 == max_tokens.
LENGTH_NONE_BODY = {
    "id": "chatcmpl-round51-fixture", "object": "chat.completion", "created": 0,
    "model": "deepseek-flash-2",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": None},
                 "finish_reason": "length"}],
    "usage": {"prompt_tokens": 8046, "completion_tokens": 6000, "total_tokens": 14046},
}
SUCCESS_BODY = {
    "id": "chatcmpl-round51-success", "object": "chat.completion", "created": 0,
    "model": "deepseek-flash-2",
    "choices": [{"index": 0,
                 "message": {"role": "assistant",
                             "content": json.dumps({"action": "finish", "args": {},
                                                    "reason": "deterministic control"})},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
}


class RecordingTransport:
    """Single scripted channel; counts every request (no hidden retries)."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return self.handler(request)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def close(self):
        pass


def body_response(body):
    return httpx.Response(200, text=json.dumps(body),
                          headers={"content-type": "application/json"})


def make_provider(transport, max_tokens=6000):
    return ChatProvider(base_url="https://provider.test/v1", model="deepseek-flash-2",
                        timeout_s=5, max_tokens=max_tokens, transport=transport,
                        json_mode=True, reasoning_effort="max")


def messages():
    return [{"role": "user", "content": "deterministic round-51 planner turn"}]


class PreFixChatProvider(ChatProvider):
    """Exactly what the frozen production ChatProvider raised (round-48 real
    receipts): the received-but-unusable response surfaced as a generic
    response_envelope ProviderError with usage/finish retained on the instance."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.last_usage = dict(LENGTH_NONE_BODY["usage"])
        self.last_response_diagnostic = {"finish_reason": "length",
                                         "content_characters": 0,
                                         "content_type": "NoneType"}
        self.last_model_content = None
        self.last_content_source = None

    def respond(self, messages=None, **overrides):
        self.calls += 1
        raise ProviderError("chat completion message content must be a string",
                            kind="response_envelope", retryable=True)


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("BIOMODEL_API_KEY", "round51-fixture-key-not-a-credential")


# ---------------------------------------------------------------- provider ---

def test_length_none_content_is_known_output_budget(api_key):
    transport = RecordingTransport(lambda request: body_response(LENGTH_NONE_BODY))
    provider = make_provider(transport)
    with pytest.raises(OutputBudgetExhausted) as excinfo:
        provider.respond(messages())
    exc = excinfo.value
    assert exc.finish_reason == "length"
    assert exc.usage == {"prompt_tokens": 8046, "completion_tokens": 6000,
                         "total_tokens": 14046}
    assert exc.max_tokens == 6000
    assert exc.kind == "output_budget" and exc.retryable is False
    # A known model-response outcome, not an unknown provider state.
    assert isinstance(exc, ModelResponseError)
    # Evidence preserved on the provider instance (as in the real receipts).
    assert provider.last_usage == exc.usage
    assert provider.last_response_diagnostic == {"finish_reason": "length",
                                                 "content_characters": 0,
                                                 "content_type": "NoneType"}
    assert len(transport.requests) == 1  # no implicit second call


def test_normal_json_success_unchanged(api_key):
    transport = RecordingTransport(lambda request: body_response(SUCCESS_BODY))
    provider = make_provider(transport)
    action = provider.respond(messages())
    assert action == {"action": "finish", "args": {}, "reason": "deterministic control"}
    assert provider.last_usage == {"prompt_tokens": 100, "completion_tokens": 20,
                                   "total_tokens": 120}
    assert provider.last_response_diagnostic == {"finish_reason": "stop",
                                                 "content_characters": len(action and
                                                                           json.dumps(action))}
    assert len(transport.requests) == 1


def test_transport_timeout_is_unknown_not_budget(api_key):
    def handler(request):
        raise httpx.ConnectTimeout("timed out")
    transport = RecordingTransport(handler)
    provider = make_provider(transport)
    with pytest.raises(ProviderError) as excinfo:
        provider.respond(messages())
    assert type(excinfo.value) is ProviderError
    assert excinfo.value.kind == "transport" and excinfo.value.retryable is True
    # Nothing was received: usage and diagnostics stay empty (unknown POST).
    assert provider.last_usage == {} and provider.last_response_diagnostic == {}
    assert len(transport.requests) == 1


@pytest.mark.parametrize("body,expected_finish", [
    ({"object": "chat.completion",
      "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}, None),
    ({"choices": [{"message": {"role": "assistant", "content": None},
                   "finish_reason": None}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}, "unknown"),
    ({"choices": [{"message": {"role": "assistant", "content": None},
                   "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}, "stop"),
    ({"choices": [{"message": {"role": "assistant", "content": None},
                   "finish_reason": "weird-unknown-state"}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
     "unknown"),
])
def test_unknown_provider_state_never_misjudged_as_budget(api_key, body, expected_finish):
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(ProviderError) as excinfo:
        provider.respond(messages())
    assert type(excinfo.value) is ProviderError
    assert excinfo.value.kind == "response_envelope"
    assert not isinstance(excinfo.value, OutputBudgetExhausted)
    assert excinfo.value.retryable is True
    if expected_finish is None:
        assert provider.last_response_diagnostic == {}
    else:
        assert provider.last_response_diagnostic["finish_reason"] == expected_finish
    # An error that still carries usage is not thereby a complete response.
    assert "usage" in body and provider.last_response_diagnostic.get("http_status") is None
    assert len(transport.requests) == 1


def test_truncated_nonempty_json_is_budget_not_format_retry(api_key):
    truncated = '{"action": "inspect_sourc'
    body = {"choices": [{"message": {"role": "assistant", "content": truncated},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 9000, "completion_tokens": 6000,
                      "total_tokens": 15000}}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(OutputBudgetExhausted) as excinfo:
        provider.respond(messages())
    assert excinfo.value.finish_reason == "length" and excinfo.value.retryable is False
    assert excinfo.value.kind == "output_budget"
    assert provider.last_model_content == truncated  # raw text retained untrusted
    assert not isinstance(excinfo.value, type(None))
    assert len(transport.requests) == 1  # never retried at the same cap


def test_plain_malformed_json_stays_bounded_format_error(api_key):
    body = {"choices": [{"message": {"role": "assistant", "content": "not json at all"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(ModelResponseError) as excinfo:
        provider.respond(messages())
    assert type(excinfo.value) is ModelResponseError
    assert excinfo.value.kind == "model_response" and excinfo.value.retryable is True
    assert not isinstance(excinfo.value, OutputBudgetExhausted)
    assert len(transport.requests) == 1


# ------------------------------------------------- supervisor -> project ----

def make_project(store):
    return p.create(store, {"scenario": "round-51 deterministic output-budget "
                                        "reproduction (no science calls)",
                            "policy": {"planner_max_calls": 3,
                                       "context_max_chars": 60000}})


def provider_failure_receipts(store, project_ref):
    return [p.own(store, ref, p.PROVIDER_FAILURE, project_ref)
            for ref, _ in p.rows(store, p.PROVIDER_FAILURE, project_ref)]


def test_variant_known_output_budget_through_real_entry(api_key, tmp_path):
    from biomodel_agent.storage import ArtifactStore
    transport = RecordingTransport(lambda request: body_response(LENGTH_NONE_BODY))
    provider = make_provider(transport)
    store = ArtifactStore(tmp_path / "store")
    project_ref = make_project(store)
    research_supervisor.run(store, provider, project_ref)

    view=p.inspect(store,project_ref)
    assert view['state']=='output_budget_exhausted'
    assert view['unresolved_operation_refs']==[]
    assert view['continuation']['can_advance'] is False
    assert view['continuation']['requires_provider'] is False

    # Exactly one provider request: no replay, no same-cap retry.
    assert len(transport.requests) == 1
    # Explicit, interpretable budget-exhaustion status with attribution.
    status = research_supervisor.output_budget_status(store, project_ref)
    assert status is not None and status["version"] == 1
    row = status["exhausted"][0]
    assert row["finish_reason"] == "length"
    assert row["usage"] == {"prompt_tokens": 8046, "completion_tokens": 6000,
                            "total_tokens": 14046}
    assert row["requested_max_tokens"] == 6000
    assert row["request_ref"] and row["failure_ref"] and row["attempt_ref"]
    assert status["project_state"] == p.inspect(store, project_ref)["state"]
    assert "operator explicitly chooses" in status["next_step"]

    # The failure receipt keeps usage, finish reason and attempt attribution.
    attempts, returns, _ = p.history(store, project_ref)
    respond_attempts = [(ref, a) for ref, a in attempts if a["operation"] == "respond"]
    assert len(respond_attempts) == 1
    attempt_ref, attempt = respond_attempts[0]
    assert attempt_ref not in returns or returns[attempt_ref][1]["state"] != "returned"
    receipts = provider_failure_receipts(store, project_ref)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["attempt_ref"] == attempt_ref
    assert receipt["request_ref"] == attempt["args"]["request_ref"]
    assert receipt["error_kind"] == "output_budget"
    assert receipt['output_budget_verified'] is True
    assert receipt["usage"] == {"prompt_tokens": 8046, "completion_tokens": 6000,
                                "total_tokens": 14046}
    assert receipt["response_diagnostic"]["finish_reason"] == "length"
    assert receipt["response_diagnostic"]["content_type"] == "NoneType"

    # Empty content never became an action; billed usage stays in accounting.
    assert research_supervisor._pending_response(store, project_ref) is None
    usage = research_supervisor.usage(store, project_ref)
    assert usage["planner_calls"] == 1
    assert usage["reported_total_tokens"] == 14046
    assert usage["missing_usage_calls"] == 0


def test_baseline_prefix_classification_freezes_as_unknown(api_key, tmp_path):
    """Baseline: the frozen production classification reproduces the real
    round-48 outcome - one paid call, unknown attempt state, needs_recovery."""
    from biomodel_agent.storage import ArtifactStore
    transport = RecordingTransport(lambda request: body_response(LENGTH_NONE_BODY))
    provider = PreFixChatProvider(base_url="https://provider.test/v1",
                                  model="deepseek-flash-2", timeout_s=5,
                                  max_tokens=6000, transport=transport,
                                  json_mode=True, reasoning_effort="max")
    store = ArtifactStore(tmp_path / "store")
    project_ref = make_project(store)
    snapshot = research_supervisor.run(store, provider, project_ref)

    assert provider.calls == 1 and len(transport.requests) == 0  # provider-level only
    assert p.inspect(store, project_ref)["state"] == "needs_recovery"  # the freeze
    assert research_supervisor.output_budget_status(store, project_ref) is None
    attempts, returns, _ = p.history(store, project_ref)
    respond_attempts = [(ref, a) for ref, a in attempts if a["operation"] == "respond"]
    assert len(respond_attempts) == 1
    assert returns[respond_attempts[0][0]][1]["state"] == "unknown"
    receipt = provider_failure_receipts(store, project_ref)[0]
    assert receipt["error_kind"] == "response_envelope"
    assert receipt["usage"]["completion_tokens"] == 6000
    assert receipt["response_diagnostic"]["finish_reason"] == "length"


def test_success_control_normal_json_through_real_entry(api_key, tmp_path):
    from biomodel_agent.storage import ArtifactStore
    body = dict(SUCCESS_BODY)
    body = {"choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(
                                         {"action": "ask_alignment",
                                          "args": {"category": "objective",
                                                   "question": "Proceed within budget?",
                                                   "affected_fields": ["scenario"]},
                                          "reason": "deterministic success control"})},
                         "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    store = ArtifactStore(tmp_path / "store")
    project_ref = make_project(store)
    snapshot = research_supervisor.run(store, provider, project_ref)

    assert len(transport.requests) == 1
    usage = research_supervisor.usage(store, project_ref)
    assert usage["planner_calls"] == 1
    assert usage["reported_total_tokens"] == 120
    assert usage["missing_usage_calls"] == 0
    # The returned response was consumed exactly once.
    assert research_supervisor._pending_response(store, project_ref) is None
    assert "output_budget_exhausted" not in snapshot


# ------------------------------------------- round-58 boundary guards (58) ---

def test_second_run_after_output_budget_does_not_recontact_same_cap(api_key, tmp_path):
    """Round-58 guard 1: after a proven length+None output-budget exhaustion,
    a second research_supervisor.run on the same project / same provider /
    same profile / same task, with no explicit new plan and no output-budget
    adjustment, must not silently re-contact the same-cap provider. A second
    contact is recorded as a contract gap (expectations are not weakened)."""
    from biomodel_agent.storage import ArtifactStore
    transport = RecordingTransport(lambda request: body_response(LENGTH_NONE_BODY))
    provider = make_provider(transport)
    store = ArtifactStore(tmp_path / "store")
    project_ref = make_project(store)
    research_supervisor.run(store, provider, project_ref)
    assert len(transport.requests) == 1  # proven first (only) contact

    second_error = None
    try:
        research_supervisor.run(store, provider, project_ref)
    except Exception as exc:  # a refusal/stop is acceptable; a hidden call is not
        second_error = exc
    assert len(transport.requests) == 1, (
        "CONTRACT GAP: second run re-contacted the same-cap provider "
        "(requests=%d; second run error=%r)" % (len(transport.requests), second_error))
    # Old failure receipt / usage evidence is retained, never overwritten.
    receipts = provider_failure_receipts(store, project_ref)
    assert len(receipts) == 1 and receipts[0]["error_kind"] == "output_budget"
    assert receipts[0]["usage"] == {"prompt_tokens": 8046, "completion_tokens": 6000,
                                    "total_tokens": 14046}
    usage = research_supervisor.usage(store, project_ref)
    assert usage["planner_calls"] == 1
    assert usage["reported_total_tokens"] == 14046
    status = research_supervisor.output_budget_status(store, project_ref)
    assert status is not None and len(status["exhausted"]) == 1


def test_length_without_message_object_is_bad_envelope_not_budget(api_key):
    """Round-58 guard 2: HTTP 200 with choices[0].finish_reason == "length" and
    usage but NO message object is not a valid chat-completion envelope. It
    must stay the conservative bad-envelope ProviderError and must never be
    promoted to OutputBudgetExhausted merely because length+usage are present."""
    body = {"id": "chatcmpl-round58-no-message", "object": "chat.completion",
            "created": 0, "model": "deepseek-flash-2",
            "choices": [{"index": 0, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 8046, "completion_tokens": 6000,
                      "total_tokens": 14046}}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(ProviderError) as excinfo:
        provider.respond(messages())
    assert type(excinfo.value) is ProviderError
    assert excinfo.value.kind == "response_envelope"
    assert not isinstance(excinfo.value, OutputBudgetExhausted)
    assert excinfo.value.retryable is True
    assert len(transport.requests) == 1  # no same-cap retry on a bad envelope


def exhausted_project(tmp_path, *, calls=3, body=None):
    from biomodel_agent.storage import ArtifactStore
    store=ArtifactStore(tmp_path/'store')
    project=p.create(store,dict(scenario='Budget recovery contract.',policy=dict(planner_max_calls=calls)))
    transport=RecordingTransport(lambda request:body_response(body or LENGTH_NONE_BODY))
    provider=make_provider(transport)
    research_supervisor.run(store,provider,project)
    return store,project,transport,provider,p.inspect(store,project)['output_budget_attempt_ref']


def test_explicit_plan_continues_once_without_refunding_usage(api_key,tmp_path):
    from biomodel_agent import research_output_budget as budget,bundles
    from biomodel_agent.storage import ArtifactStore
    store,project,old_transport,old,attempt=exhausted_project(tmp_path)
    answer={'action':'ask_alignment','args':{'category':'objective','question':'Which figure?',
        'affected_fields':['scenario']},'reason':'Explicit target needed.'}
    body=json.loads(json.dumps(SUCCESS_BODY));body['choices'][0]['message']['content']=json.dumps(answer)
    transport=RecordingTransport(lambda request:body_response(body))
    larger=make_provider(transport,max_tokens=12000)
    plan=budget.plan(store,project,attempt,larger,actor='operator',reason='Allow final JSON after reasoning.')
    assert len(transport.requests)==0 and p.inspect(store,project)['state']=='ready'
    assert budget.plan(store,project,attempt,larger,actor='operator',reason='Allow final JSON after reasoning.')==plan
    before=len(p.history(store,project)[0])
    with pytest.raises(ValueError,match='does not match'):
        research_supervisor.run(store,old,project)
    assert len(old_transport.requests)==1 and len(p.history(store,project)[0])==before
    snapshot=research_supervisor.run(store,larger,project)
    assert len(transport.requests)==1 and p.inspect(store,project)['state']=='awaiting_alignment'
    usage=research_supervisor.usage(store,project)
    assert usage['planner_calls']==2 and usage['reported_total_tokens']==14166
    assert p.inspect(store,project)['output_budget_attempt_ref'] is None
    assert p.rows(store,p.PROVIDER_FAILURE,project)[0][1]['usage']['total_tokens']==14046
    bundles.export_bundle(store,snapshot,tmp_path/'export')
    copied=ArtifactStore(tmp_path/'export')
    assert p.inspect(copied,project)['state']=='awaiting_alignment'
    assert p.rows(copied,budget.PLAN,project)[0][0]==plan


@pytest.mark.parametrize('field,value',[
    ('max_tokens',6000),('max_tokens',5000),('model','different-model'),
    ('base_url','https://other.test/v1'),('reasoning_effort','low'),('timeout_s',1),
])
def test_plan_rejects_same_cap_or_changed_contract(api_key,tmp_path,field,value):
    from biomodel_agent import research_output_budget as budget
    store,project,transport,_,attempt=exhausted_project(tmp_path)
    candidate=make_provider(transport,max_tokens=12000)
    setattr(candidate,field,value)
    with pytest.raises(ValueError,match='higher output cap'):
        budget.plan(store,project,attempt,candidate,actor='operator',reason='A new cap.')
    assert len(transport.requests)==1 and p.inspect(store,project)['state']=='output_budget_exhausted'


def test_plan_cannot_refund_exhausted_quota_or_override_stop(api_key,tmp_path):
    from biomodel_agent import research_output_budget as budget,research_control
    store,project,transport,_,attempt=exhausted_project(tmp_path,calls=1)
    larger=make_provider(transport,max_tokens=12000)
    with pytest.raises(ValueError,match='remaining policy'):
        budget.plan(store,project,attempt,larger,actor='operator',reason='No budget remains.')
    research_control.request_stop(store,project,actor='operator',reason='Stop.')
    with pytest.raises(ValueError,match='remaining policy'):
        budget.plan(store,project,attempt,larger,actor='operator',reason='Cannot override stop.')
    assert len(transport.requests)==1


def test_plan_conflicts_and_second_length_require_new_plan(api_key,tmp_path):
    from biomodel_agent import research_output_budget as budget
    store,project,transport,_,attempt=exhausted_project(tmp_path)
    larger=make_provider(transport,max_tokens=12000)
    for reason in ('','   '):
        with pytest.raises(ValueError):budget.plan(store,project,attempt,larger,actor='operator',reason=reason)
    budget.plan(store,project,attempt,larger,actor='operator',reason='More output.')
    with pytest.raises(ValueError,match='conflicting'):
        budget.plan(store,project,attempt,larger,actor='operator',reason='Changed plan.')
    research_supervisor.run(store,larger,project)
    view=p.inspect(store,project)
    assert view['state']=='output_budget_exhausted' and view['output_budget_attempt_ref']!=attempt
    research_supervisor.run(store,larger,project)
    assert len(transport.requests)==2 and research_supervisor.usage(store,project)['planner_calls']==2


def test_unknown_post_is_not_eligible_for_output_budget_plan(api_key,tmp_path):
    from biomodel_agent import research_output_budget as budget
    from biomodel_agent.storage import ArtifactStore
    def timeout(request):raise httpx.ReadTimeout('Response not confirmed.')
    transport=RecordingTransport(timeout);provider=make_provider(transport)
    store=ArtifactStore(tmp_path/'store');project=make_project(store)
    research_supervisor.run(store,provider,project)
    attempts,_,unknown=p.history(store,project)
    assert unknown==[attempts[0][0]]
    with pytest.raises(ValueError,match='current known'):
        budget.plan(store,project,attempts[0][0],make_provider(transport,12000),actor='operator',reason='Unsafe retry.')
    research_supervisor.run(store,provider,project)
    assert len(transport.requests)==1 and p.inspect(store,project)['state']=='needs_recovery'


@pytest.mark.parametrize('field,value',[
    ('attempt_ref','0'*64),('project_ref','0'*64),('request_ref','0'*64),
    ('provider',{}),('output_budget_verified',False),
    ('response_diagnostic',{'finish_reason':'length','requested_max_tokens':True}),
])
def test_failure_closure_rejects_foreign_or_unverified_receipt(api_key,tmp_path,field,value):
    from biomodel_agent import research_output_budget as budget
    store,project,_,_,attempt=exhausted_project(tmp_path)
    args=dict(p.history(store,project)[0])[attempt]['args']
    original=store.get(p.inspect(store,project)['output_budget_failure_ref'])
    payload=dict(original['payload']);payload[field]=value
    forged=store.put(p.PROVIDER_FAILURE,payload,refs=original['refs'])
    with pytest.raises(ValueError):budget.failure_closure(store,project,args,attempt,forged)


def test_missing_usage_stays_unknown_cost_even_for_known_terminal(api_key,tmp_path):
    body=json.loads(json.dumps(LENGTH_NONE_BODY));body.pop('usage')
    store,project,transport,_,_=exhausted_project(tmp_path,body=body)
    assert p.inspect(store,project)['state']=='output_budget_exhausted'
    usage=research_supervisor.usage(store,project)
    assert usage['missing_usage_calls']==1 and usage['planner_complete_total_tokens'] is None
    assert len(transport.requests)==1


def test_plain_provider_error_kind_cannot_assert_verified_terminal(api_key,tmp_path):
    from biomodel_agent.storage import ArtifactStore
    class Unverified:
        max_tokens=6000
        last_usage={'total_tokens':7}
        last_response_diagnostic={'finish_reason':'length','content_type':'NoneType'}
        def respond(self,messages):raise ProviderError('Unverified.',kind='output_budget')
    store=ArtifactStore(tmp_path/'store');project=make_project(store)
    research_supervisor.run(store,Unverified(),project)
    assert p.inspect(store,project)['state']=='needs_recovery'
    failure=provider_failure_receipts(store,project)[0]
    assert 'output_budget_verified' not in failure
    assert 'requested_max_tokens' not in failure['response_diagnostic']
    assert research_supervisor.output_budget_status(store,project) is None


@pytest.mark.parametrize("bad_choice", [
    pytest.param({"index": 0, "message": "assistant", "finish_reason": "length"},
                 id="message-not-a-dict"),
    pytest.param({"index": 0, "message": {"role": "assistant"},
                  "finish_reason": "length"}, id="content-field-absent"),
    pytest.param({"index": 0, "message": {"role": "assistant", "content": []},
                  "finish_reason": "length"}, id="content-illegal-list"),
    pytest.param({"index": 0, "message": {"role": "assistant", "content": 0},
                  "finish_reason": "length"}, id="content-illegal-int"),
])
def test_malformed_length_envelopes_stay_bad_envelope_not_budget(api_key, bad_choice):
    """Round-58 guard 2 boundaries: with ``finish_reason == "length"`` and
    billed usage present, every structurally malformed choice stays the
    conservative response_envelope ProviderError.  Only an explicit assistant
    message whose ``content`` field exists and is ``None`` is interpretable as
    a known budget exhaustion; nothing else may be promoted on length+usage
    alone."""
    body = {"id": "chatcmpl-round58-malformed", "object": "chat.completion",
            "created": 0, "model": "deepseek-flash-2",
            "choices": [bad_choice],
            "usage": {"prompt_tokens": 8046, "completion_tokens": 6000,
                      "total_tokens": 14046}}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(ProviderError) as excinfo:
        provider.respond(messages())
    assert type(excinfo.value) is ProviderError
    assert excinfo.value.kind == "response_envelope"
    assert not isinstance(excinfo.value, OutputBudgetExhausted)
    assert excinfo.value.retryable is True
    # The received-but-unusable response still keeps its billed usage.
    assert provider.last_usage == {"prompt_tokens": 8046, "completion_tokens": 6000,
                                   "total_tokens": 14046}
    assert len(transport.requests) == 1  # no same-cap retry on a bad envelope


def test_length_none_without_usage_is_budget_with_unknown_usage(api_key):
    """Round-58 boundary: a known ``length`` + explicit ``content=None``
    exhaustion with NO usage block stays a known output-budget outcome, and
    the missing usage is reported as unknown (empty dict), never zero-filled."""
    body = {"id": "chatcmpl-round58-no-usage", "object": "chat.completion",
            "created": 0, "model": "deepseek-flash-2",
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": None},
                         "finish_reason": "length"}]}
    transport = RecordingTransport(lambda request: body_response(body))
    provider = make_provider(transport)
    with pytest.raises(OutputBudgetExhausted) as excinfo:
        provider.respond(messages())
    assert excinfo.value.finish_reason == "length"
    assert excinfo.value.usage == {}  # unknown stays unknown, not zeros
    assert excinfo.value.max_tokens == 6000
    assert excinfo.value.kind == "output_budget" and excinfo.value.retryable is False
    assert len(transport.requests) == 1
