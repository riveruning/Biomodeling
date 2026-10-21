"""T08c HTTP controls: fixtures never represent a real model invocation."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import time

import httpx
import pytest

from biomodel_agent.llm import ProviderError
from biomodel_agent.storage import ArtifactStore
from test_workflow_adapters import action, projection, translation_proposal

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def transport_module():
    spec = importlib.util.spec_from_file_location('comparison_transport_acceptance',
        ROOT/'benchmarks/development/comparison_transport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return {'base_url': 'https://comparison.invalid/v1', 'api_key_env': 'BIOMODEL_API_KEY',
            'request_timeout_s': 10., 'max_response_bytes': 2_000_000}


def model(module):
    return module.driver.protocol.development_manifest(ROOT)['model_config']


def packet(settings):
    value = {'model': settings['model'], 'messages': [{'role': 'system', 'content': '中文 HTTP control'}],
             'temperature': settings['temperature'], 'top_p': settings['top_p'],
             'max_tokens': settings['max_output_tokens']}
    if settings['seed'] is not None:
        value['seed'] = settings['seed']
    return value


def envelope(settings, content=None, usage=None):
    return {'model': settings['model'], 'system_fingerprint': 'fixture-backend',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant',
                'content': content or json.dumps({'action': 'ask_researcher', 'args': {'question': 'Clarify the task.'}})}}],
            'usage': usage}


def test_http_request_preserves_the_frozen_payload_and_uses_no_real_key(transport_module, monkeypatch):
    module = transport_module
    settings = model(module)
    payload = packet(settings)
    seen = []
    original = module.os.environ.get
    def forbid_key(name, *args):
        assert name != 'BIOMODEL_API_KEY', 'Mock HTTP read a real credential'
        return original(name, *args)
    monkeypatch.setattr(module.os.environ, 'get', forbid_key)
    def handler(request):
        seen.append(json.loads(request.content))
        assert str(request.url) == config()['base_url']+'/chat/completions'
        assert request.headers['authorization'] == 'Bearer offline-http-control'
        assert request.extensions['timeout']['read'] == pytest.approx(.25)
        return httpx.Response(200, json=envelope(settings))
    transport = module.ChatCompletionTransport(settings, config(), http_transport=httpx.MockTransport(handler))
    result = transport.request(payload, .25)
    assert result['model'] == settings['model'] and seen == [payload]
    assert transport.evidence_kind == 'scripted_control'
    assert transport.exchange_audit()['http_status'] == 200
    assert transport.exchange_audit()['request_body_sha256'] == module.protocol.digest(payload)


def test_preparation_is_offline_frozen_and_never_authorizes_egress(transport_module, tmp_path, monkeypatch):
    module = transport_module
    manifest = module.driver.protocol.development_manifest(ROOT)
    manifest['evidence_kind'] = 'live_model'
    manifest['model_config'].update(provider='example-not-selected', model='example-not-selected', revision='example-not-selected')
    def forbidden(*args, **kwargs):
        pytest.fail('offline preparation tried to create an HTTP client')
    monkeypatch.setattr(module.httpx, 'Client', forbidden)
    report = module.prepare_comparison(manifest, config(), tmp_path/'prepared', root=ROOT)
    assert report['status'] == 'prepared_only_no_authorization'
    assert report['planned_trials'] == 30 and report['online_inference_calls'] == 0
    assert len(report['materials']) == 10
    assert module.validate_preparation(tmp_path/'prepared', root=ROOT) == report
    assert report['manifest_sha256'] == module.driver.protocol.digest(manifest)


@pytest.mark.parametrize('endpoint', [
    'http://public.invalid/v1', 'https://name:secret@comparison.invalid/v1',
    'https://comparison.invalid/v1?key=secret', 'https://comparison.invalid/v1#secret',
    'https://comparison.invalid/v1?', 'https://comparison.invalid:bad/v1',
    'https://comparison.invalid:0/v1', 'https://comparison.invalid/ v1',
    'https://comparison.invalid/v1/chat/completions', 'file:///tmp/endpoint',
])
def test_endpoint_validation_precedes_clients_or_credentials(transport_module, endpoint, monkeypatch):
    module = transport_module
    settings = model(module)
    monkeypatch.setattr(module.httpx, 'Client', lambda *a, **kw: pytest.fail('invalid endpoint created a client'))
    with pytest.raises(ValueError):
        module.ChatCompletionTransport(settings, {**config(), 'base_url': endpoint})


@pytest.mark.parametrize('field,value', [('model', 'other'), ('seed', 7), ('temperature', True),
                                       ('max_tokens', 1001), ('max_tokens', 1000.0), ('stream', True)])
def test_wire_settings_cannot_change_or_gain_unreviewed_fields(transport_module, field, value):
    module = transport_module
    settings = model(module)
    payload = packet(settings)
    payload[field] = value
    transport = module.ChatCompletionTransport(settings, config(),
        http_transport=httpx.MockTransport(lambda _: pytest.fail('invalid payload reached HTTP')))
    with pytest.raises(ValueError):
        transport.request(payload, 1)


def test_no_seed_is_injected_and_configuration_is_copied(transport_module):
    module = transport_module
    settings = model(module)
    settings['seed'] = None
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(transport.model_config))
    transport = module.ChatCompletionTransport(settings, config(), http_transport=httpx.MockTransport(handler))
    original = packet(settings)
    settings['model'] = 'changed outside constructor'
    transport.model_config['model'] = 'changed returned copy'
    transport.request(original, 1)
    assert seen == [original] and 'seed' not in seen[0]


@pytest.mark.parametrize('status', [302, 401, 408, 429, 500, 503])
def test_http_failures_do_not_redirect_read_error_body_or_retry(transport_module, status):
    module = transport_module
    settings = model(module)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': '2', 'Location': 'https://other.invalid'},
                              content=b'SECRET-HTTP-error-body')
    transport = module.ChatCompletionTransport(settings, config(), http_transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error:
        transport.request(packet(settings), 1)
    assert len(calls) == 1 and error.value.retryable is False
    audit = transport.exchange_audit()
    assert audit['http_status'] == status and audit['retry_after_s'] == 2
    assert audit['response_bytes'] == 0 and audit['outcome'] == 'http_error'
    assert 'SECRET' not in str(error.value)+json.dumps(audit)


@pytest.mark.parametrize('defect', ['truncated', 'refusal', 'tool_calls', 'wrong_role',
                                  'choices', 'null_message', 'not_json', 'duplicate', 'nonfinite', 'overflow',
                                  'wrong_index', 'no_index', 'invalid_refusal', 'invalid_tool_calls', 'function_call'])
def test_invalid_http_envelopes_never_become_actions(transport_module, defect):
    module = transport_module
    settings = model(module)
    body = envelope(settings)
    choice = body['choices'][0]
    if defect == 'truncated': choice['finish_reason'] = 'length'
    elif defect == 'refusal': choice['message']['refusal'] = 'declined'
    elif defect == 'tool_calls': choice['message']['tool_calls'] = [{'type': 'function'}]
    elif defect == 'wrong_role': choice['message']['role'] = 'user'
    elif defect == 'choices': body['choices'].append(deepcopy(choice))
    elif defect == 'null_message': choice['message'] = None
    elif defect == 'wrong_index': choice['index'] = True
    elif defect == 'no_index': del choice['index']
    elif defect == 'invalid_refusal': choice['message']['refusal'] = False
    elif defect == 'invalid_tool_calls': choice['message']['tool_calls'] = False
    elif defect == 'function_call': choice['message']['function_call'] = {}
    raw = json.dumps(body).encode()
    if defect == 'not_json': raw = b'<html>SECRET error</html>'
    elif defect == 'duplicate': raw = b'{"model":"x","model":"y","choices":[]}'
    elif defect == 'nonfinite': raw = b'{"unused":NaN}'
    elif defect == 'overflow': raw = b'{"unused":1e99999}'
    transport = module.ChatCompletionTransport(settings, config(),
        http_transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw)))
    with pytest.raises(ProviderError) as error:
        transport.request(packet(settings), 1)
    assert error.value.kind == 'response_envelope'
    assert transport.exchange_audit()['outcome'] == 'invalid_envelope'
    assert 'SECRET' not in str(error.value)


def test_response_bytes_are_bounded_before_json_decoding(transport_module):
    module = transport_module
    settings = model(module)
    transport = module.ChatCompletionTransport(settings, {**config(), 'max_response_bytes': 1024},
        http_transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x'*5000)))
    with pytest.raises(ProviderError):
        transport.request(packet(settings), 1)
    assert transport.exchange_audit()['outcome'] == 'response_too_large'


def test_timeout_and_connection_error_are_sanitized_without_retry(transport_module):
    module = transport_module
    settings = model(module)
    for exception, expected, outcome in [(httpx.ReadTimeout('SECRET timeout'), TimeoutError, 'timeout'),
                                         (httpx.ConnectError('SECRET connection'), ProviderError, 'transport_error')]:
        calls = []
        def handler(request):
            calls.append(request)
            raise exception
        transport = module.ChatCompletionTransport(settings, config(), http_transport=httpx.MockTransport(handler))
        with pytest.raises(expected) as error:
            transport.request(packet(settings), 1)
        assert len(calls) == 1 and transport.exchange_audit()['outcome'] == outcome
        assert 'SECRET' not in str(error.value)+json.dumps(transport.exchange_audit())


def test_network_path_uses_only_explicit_fake_key_and_disables_proxies_and_redirects(transport_module, monkeypatch):
    module = transport_module
    settings = model(module)
    monkeypatch.setenv('T08C_FAKE_KEY', 'fake-key-used-only-in-unit-control')
    original = module.httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers['Authorization'] == 'Bearer fake-key-used-only-in-unit-control'
        return httpx.Response(200, json=envelope(settings))
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        assert kwargs['transport'] is None
        return original(**{**kwargs, 'transport': httpx.MockTransport(handler)})
    monkeypatch.setattr(module.httpx, 'Client', client)
    transport = module.ChatCompletionTransport(settings, {**config(), 'api_key_env': 'T08C_FAKE_KEY'})
    transport.request(packet(settings), 1)
    assert len(seen) == 1 and transport.evidence_kind == 'live_model'
    assert 'fake-key-used-only' not in json.dumps(transport.comparison_identity)+json.dumps(transport.exchange_audit())


def fixture_transport(module, p, actions, *, endpoint=None, usage=None, status=200, error=None):
    scripted = module.driver.ScriptedTransport(p, actions)
    def handler(request):
        if error is not None: raise error
        if status != 200:
            return httpx.Response(status, content=b'SECRET provider body', headers={'Retry-After': '60'})
        result = scripted.request(json.loads(request.content), 1)
        result['choices'][0].update(index=0, finish_reason='stop')
        result['choices'][0]['message']['role'] = 'assistant'
        result['usage'] = usage
        return httpx.Response(200, json=result)
    return module.ChatCompletionTransport(p['model_config'], {**config(), **({'base_url': endpoint} if endpoint else {})},
        http_transport=httpx.MockTransport(handler))


def literal_decay(module, workflow):
    # These literal actions use only request-visible source placeholders.
    from biomodel_agent.domain.contracts import ModelDraft, Parameter, RateRule, State
    from biomodel_agent.domain.serialization import to_dict
    draft = ModelDraft('decay', (State('A', 2.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('A', '-k*A'),), time_unit='minute')
    proposal = action('submit_draft', {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': '$main', 'page': 1, 'quote': quote} for target, quote in [
            ('A/initial', 'Initial A = 2.0.'), ('k/value', 'Parameter k = 0.4.'),
            ('rule:A/expression', 'Equation dA/dt = -k*A.')]]})
    return [proposal] if workflow == 'fixed_workflow' else [proposal, action('run'), action('finish')]


@pytest.mark.parametrize('workflow', ['fixed_workflow', 'generic_tool_agent', 'domain_agent'])
@pytest.mark.parametrize('usage', [None, {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}])
def test_real_shared_tools_through_http_keep_usage_and_linked_receipts(transport_module, tmp_path, workflow, usage):
    module = transport_module
    adapters = module.driver.adapters
    p = projection(adapters, workflow)
    transport = fixture_transport(module, p, literal_decay(module, workflow), usage=usage)
    out = adapters.run_trial(p, transport, tmp_path/'phase')
    assert out['record']['status'] == 'finished' and out['record']['observation']['state'] == 'completed'
    calls = out['record']['telemetry']['model_calls']
    assert calls == (1 if workflow == 'fixed_workflow' else 3)
    assert out['record']['telemetry']['output_tokens'] == (None if usage is None else 7*calls)
    assert len(out['audit']['transport_exchange_refs']) == calls
    store = ArtifactStore(out['store_root'])
    for ref in out['audit']['transport_exchange_refs']:
        exchange = store.get(ref)
        assert exchange['kind'] == 'evaluation_transport_exchange'
        assert exchange['payload']['exchange']['outcome'] == 'ok'
        assert exchange['payload']['exchange']['http_status'] == 200
        assert set(exchange['refs']) <= set(out['audit']['request_refs'])
    run = store.get(out['audit']['run_ref'])['payload']
    assert run['source_validation']['semantic_support'] == 'unknown'
    assert out['record']['evidence_kind'] == 'scripted_control'


@pytest.mark.parametrize('workflow', ['fixed_workflow', 'generic_tool_agent', 'domain_agent'])
@pytest.mark.parametrize('fault', ['http_error', 'timeout', 'invalid_envelope'])
def test_http_failures_are_charged_and_persisted_once_per_workflow(transport_module, tmp_path, workflow, fault):
    module = transport_module
    adapters = module.driver.adapters
    p = projection(adapters, workflow)
    if fault == 'invalid_envelope':
        transport = module.ChatCompletionTransport(p['model_config'], config(),
            http_transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'not JSON SECRET')))
    else:
        transport = fixture_transport(module, p, [], status=429 if fault == 'http_error' else 200,
            error=httpx.ReadTimeout('SECRET timeout') if fault == 'timeout' else None)
    out = adapters.run_trial(p, transport, tmp_path/'phase')
    expected = {'http_error': 'provider_error', 'timeout': 'timeout', 'invalid_envelope': 'invalid_output'}[fault]
    assert out['record']['status'] == expected
    assert out['record']['telemetry']['model_calls'] == 1 and not out['record']['observation']['executed']
    assert len(out['audit']['transport_exchange_refs']) == 1
    assert out['record']['telemetry']['output_tokens'] is None
    for path in Path(out['store_root']).rglob('*.json'):
        assert 'SECRET' not in path.read_text()


@pytest.mark.parametrize('defect', ['endpoint', 'timeout', 'response_limit', 'mode'])
def test_http_identity_changes_cannot_resume_a_parent(transport_module, tmp_path, defect):
    module = transport_module
    adapters = module.driver.adapters
    first = projection(adapters, 'domain_agent', 'translation')
    before = adapters.run_trial(first, fixture_transport(module, first,
        [action('ask_researcher', {'question': 'What is initial P?'})]), tmp_path/'parent')
    second = projection(adapters, 'domain_agent', 'translation', 'clarified')
    options = config()
    if defect == 'endpoint': options['base_url'] = 'https://other.invalid/v1'
    elif defect == 'timeout': options['request_timeout_s'] = 9
    elif defect == 'response_limit': options['max_response_bytes'] = 1024
    transport = module.ChatCompletionTransport(second['model_config'], options,
        http_transport=None if defect == 'mode' else httpx.MockTransport(lambda _: pytest.fail('changed identity reached HTTP')))
    if defect == 'mode':
        with pytest.raises(AttributeError):
            transport.evidence_kind = 'scripted_control'
    with pytest.raises(ValueError, match='evidence kind' if defect == 'mode' else 'provider/settings/budget'):
        adapters.run_trial(second, transport, tmp_path/'child', store_root=before['store_root'],
                           parent_session_ref=before['session_ref'])
    assert not (tmp_path/'child').exists()


@pytest.mark.parametrize('defect', ['endpoint', 'budget', 'source_hash', 'approval', 'implementation'])
def test_preparation_changes_require_new_freeze(transport_module, tmp_path, monkeypatch, defect):
    module = transport_module
    manifest = module.driver.protocol.development_manifest(ROOT)
    manifest['evidence_kind'] = 'live_model'
    directory = tmp_path/'prepared'
    module.prepare_comparison(manifest, config(), directory, root=ROOT)
    filename = {'endpoint': 'transport.json', 'budget': 'manifest.json', 'source_hash': 'manifest.json',
                'approval': 'preflight.json'}.get(defect)
    if filename:
        path = directory/filename
        value = json.loads(path.read_text())
        if defect == 'endpoint': value['base_url'] = 'https://other.invalid/v1'
        elif defect == 'budget': value['budget']['max_model_calls'] += 1
        elif defect == 'source_hash': value['cases'][0]['phases'][0]['files'][0]['sha256'] = '0'*64
        else: value['status'] = 'approved'
        path.write_text(json.dumps(value))
    else:
        original = module._implementation()
        monkeypatch.setattr(module, '_implementation', lambda: {**original, 'httpx_version': 'changed'})
    with pytest.raises(ValueError):
        module.validate_preparation(directory, root=ROOT)


def test_preparation_rejects_scripted_manifest_before_output(transport_module, tmp_path):
    module = transport_module
    with pytest.raises(ValueError, match='live-model'):
        module.prepare_comparison(module.driver.protocol.development_manifest(ROOT), config(), tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('workflow', ['fixed_workflow', 'generic_tool_agent', 'domain_agent'])
def test_same_http_identity_restores_actual_original_task_and_keeps_cumulative_budget(transport_module, tmp_path, workflow):
    module = transport_module
    adapters = module.driver.adapters
    first = projection(adapters, workflow, 'translation')
    before = adapters.run_trial(first, fixture_transport(module, first,
        [action('ask_researcher', {'question': 'What is initial P?'})]), tmp_path/'parent')
    assert before['record']['observation']['state'] == 'awaiting_input'
    second = projection(adapters, workflow, 'translation', 'clarified')
    proposal = translation_proposal([{'content': json.dumps({'materials': {'$main': {'pages': [{'chars': 1}]}}})},
                                    {'content': json.dumps({'tool': 'researcher_reply', 'source_ref': '$reply'})}])
    actions = [proposal] if workflow == 'fixed_workflow' else [proposal, action('run'), action('finish')]
    after = adapters.run_trial(second, fixture_transport(module, second, actions), tmp_path/'child',
        store_root=before['store_root'], parent_session_ref=before['session_ref'])
    assert after['record']['continuation'] == 'resumed'
    assert after['record']['observation']['state'] == 'completed'
    store = ArtifactStore(after['store_root'])
    parent = store.get(before['session_ref'])['payload']
    child = store.get(after['session_ref'])['payload']
    assert child['transport_identity'] == parent['transport_identity']
    assert child['meter_totals']['model_calls'] == before['record']['telemetry']['model_calls']+after['record']['telemetry']['model_calls']
    if workflow == 'domain_agent':
        native = store.get(after['audit']['native_agent_run_ref'])['payload']
        assert native['parent_run_ref'] == before['audit']['native_agent_run_ref']


def test_http_mock_stall_is_still_bounded_by_the_outer_hard_deadline(transport_module, tmp_path):
    module = transport_module
    adapters = module.driver.adapters
    p = projection(adapters)
    p['budget']['max_elapsed_seconds'] = .8
    def stall(request):
        time.sleep(5)
        return httpx.Response(200, json=envelope(p['model_config']))
    transport = module.ChatCompletionTransport(p['model_config'], config(), http_transport=httpx.MockTransport(stall))
    started = time.monotonic()
    out = adapters.run_trial(p, transport, tmp_path/'timeout')
    assert time.monotonic()-started < 4
    assert out['record']['status'] == 'timeout' and not out['record']['observation']['executed']
    assert out['record']['telemetry']['model_calls'] <= 1


def test_missing_explicit_test_credential_never_creates_an_http_client(transport_module, monkeypatch):
    module = transport_module
    monkeypatch.delenv('T08C_INTENTIONALLY_UNSET_KEY', raising=False)
    monkeypatch.setattr(module.httpx, 'Client', lambda *a, **kw: pytest.fail('missing credential opened a client'))
    settings = model(module)
    transport = module.ChatCompletionTransport(settings, {**config(), 'api_key_env': 'T08C_INTENTIONALLY_UNSET_KEY'})
    with pytest.raises(ProviderError, match='credential unavailable'):
        transport.request(packet(settings), 1)
    assert transport.exchange_audit()['outcome'] == 'missing_credential'


def test_preparation_cli_has_no_execution_path(transport_module, tmp_path, monkeypatch, capsys):
    module = transport_module
    manifest = module.protocol.development_manifest(ROOT)
    manifest['evidence_kind'] = 'live_model'
    manifest_path, transport_path = tmp_path/'manifest.json', tmp_path/'transport.json'
    manifest_path.write_text(json.dumps(manifest))
    transport_path.write_text(json.dumps(config()))
    monkeypatch.setattr(module.httpx, 'Client', lambda *a, **kw: pytest.fail('CLI opened HTTP'))
    assert module.main(['prepare', '--manifest', str(manifest_path), '--transport', str(transport_path),
                        '--output', str(tmp_path/'ready')]) == 0
    assert module.main(['check', str(tmp_path/'ready')]) == 0
    with pytest.raises(SystemExit):
        module.main(['execute', str(tmp_path/'ready')])
    assert 'prepared_only_no_authorization' in capsys.readouterr().out
