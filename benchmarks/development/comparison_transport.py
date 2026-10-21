"""Shared bounded Chat Completions HTTP transport and offline preparation.

Importing, constructing, preparing and checking do not read credentials or
open a connection. Only an explicit network transport.request() can do so.
The CLI deliberately supports preparation/checking only, never execution.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import httpx

from biomodel_agent.llm import ProviderError, _retry_after

_spec = importlib.util.spec_from_file_location('http_comparison_driver', Path(__file__).with_name('run_agent_comparison.py'))
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)
protocol = driver.protocol
PROFILE = 'chat_completions_comparison_v1'


def _model_config(value):
    protocol._fields(value, 'provider model revision temperature top_p max_output_tokens seed')
    for key in ('provider', 'model', 'revision'):
        protocol._text(value[key], 200)
    protocol._number(value['temperature'], 0, 2)
    protocol._number(value['top_p'], .000001, 1)
    protocol._integer(value['max_output_tokens'], 1, 32000)
    if value['seed'] is not None:
        protocol._integer(value['seed'], 0, 2**32-1)
    return deepcopy(value)


def _config(value):
    protocol._fields(value, 'base_url api_key_env request_timeout_s max_response_bytes')
    base = value['base_url']
    if not isinstance(base, str) or not 1 <= len(base) <= 2000 or any(c.isspace() or ord(c) < 32 for c in base):
        raise ValueError('invalid comparison endpoint')
    try:
        parsed = urlparse(base)
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        valid = (parsed.scheme == 'https' or parsed.scheme == 'http' and local)
        if (not valid or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or '?' in base or '#' in base or '\\' in base
                or '%' in parsed.netloc or parsed.port == 0 or parsed.path.rstrip('/').endswith('/chat/completions')):
            raise ValueError('invalid comparison endpoint')
        base = str(httpx.URL(base)).rstrip('/')
    except (ValueError, httpx.InvalidURL):
        raise ValueError('endpoint requires HTTPS (or local HTTP), without credentials, query or fragment') from None
    name = value['api_key_env']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', name):
        raise ValueError('invalid credential environment variable name')
    protocol._number(value['request_timeout_s'], .01, 3600)
    protocol._integer(value['max_response_bytes'], 1024, 8_000_000)
    return {**deepcopy(value), 'base_url': base}


def _implementation():
    folder = Path(__file__).parent
    return {'toolchain_sha256': protocol.implementation_fingerprint(), 'httpx_version': httpx.__version__,
            **{name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in (
                'comparison_transport.py', 'workflow_adapters.py', 'run_agent_comparison.py', 'agent_evaluation.py')}}


def _envelope(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate envelope key')
            out[key] = value
        return out
    def reject(_):
        raise ValueError('nonfinite envelope number')
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            reject(value)
        return number
    try:
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                          parse_constant=reject, parse_float=finite)
        pending = [(body, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise ValueError('envelope nesting limit')
            if isinstance(value, dict):
                pending.extend((v, depth+1) for v in value.values())
            elif isinstance(value, list):
                pending.extend((v, depth+1) for v in value)
        if not isinstance(body, dict) or not isinstance(body.get('choices'), list) or len(body['choices']) != 1:
            raise ValueError('one completion required')
        choice = body['choices'][0]
        if (not isinstance(choice, dict) or choice.get('finish_reason') != 'stop'
                or type(choice.get('index')) is not int or choice['index'] != 0):
            raise ValueError('completion did not stop normally')
        message = choice.get('message')
        if (not isinstance(message, dict) or message.get('role') != 'assistant'
                or not isinstance(message.get('content'), str) or message.get('refusal') not in (None, '')
                or message.get('tool_calls') not in (None, []) or message.get('function_call') is not None):
            raise ValueError('unsupported completion message')
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ProviderError('invalid comparison response envelope', kind='response_envelope', retryable=False) from None


class ChatCompletionTransport:
    """One HTTP attempt, no retries/fallbacks. A MockTransport stays scripted."""

    def __init__(self, model_config, config, *, http_transport=None):
        self._model = _model_config(model_config)
        self._config = _config(config)
        if http_transport is not None and type(http_transport) is not httpx.MockTransport:
            raise ValueError('only an explicit in-memory MockTransport may replace network HTTP')
        self._http_transport = http_transport
        self._last_exchange = None

    @property
    def evidence_kind(self):
        return 'scripted_control' if self._http_transport is not None else 'live_model'

    @property
    def model_config(self):
        return deepcopy(self._model)

    @property
    def comparison_identity(self):
        return {'profile': PROFILE, 'mode': 'mock_http' if self._http_transport is not None else 'network_http',
                'config': deepcopy(self._config), 'model_config_sha256': protocol.digest(self._model),
                'implementation': _implementation()}

    def exchange_audit(self):
        return deepcopy(self._last_exchange)

    def _payload(self, payload):
        expected = {'model': self._model['model'], 'temperature': self._model['temperature'],
                    'top_p': self._model['top_p'], 'max_tokens': self._model['max_output_tokens']}
        if self._model['seed'] is not None:
            expected['seed'] = self._model['seed']
        protocol._fields(payload, ' '.join([*expected, 'messages']))
        if any(type(payload[k]) is bool or payload[k] != v for k, v in expected.items()):
            raise ValueError('HTTP payload differs from frozen model settings')
        protocol._integer(payload['max_tokens'], 1, 32000)
        if 'seed' in payload:
            protocol._integer(payload['seed'], 0, 2**32-1)
        messages = payload['messages']
        if not isinstance(messages, list) or not messages or len(messages) > 1000:
            raise ValueError('bounded message list required')
        for message in messages:
            protocol._fields(message, 'role content')
            if message['role'] not in ('system', 'user', 'assistant') or not isinstance(message['content'], str):
                raise ValueError('unsupported HTTP message')
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > 8_000_000:
            raise ValueError('HTTP request byte limit exceeded')
        return raw

    def request(self, payload, timeout_s):
        self._last_exchange = None
        protocol._number(timeout_s, .000001, 3600)
        raw = self._payload(payload)
        limit = min(timeout_s, self._config['request_timeout_s'])
        deadline = time.monotonic()+limit
        self._last_exchange = {'schema_version': 1, 'identity_sha256': protocol.digest(self.comparison_identity),
            'request_body_sha256': hashlib.sha256(raw).hexdigest(), 'http_status': None,
            'response_bytes': 0, 'outcome': 'pending', 'retry_after_s': None, 'system_fingerprint_sha256': None}
        audit = self._last_exchange
        def remaining():
            if time.monotonic() >= deadline:
                raise TimeoutError('comparison HTTP request deadline')
        try:
            if self._http_transport is not None:
                key = 'offline-http-control'
            else:
                key = os.environ.get(self._config['api_key_env'])
                if not isinstance(key, str) or not key.strip() or len(key) > 16000 or '\r' in key or '\n' in key:
                    audit['outcome'] = 'missing_credential'
                    raise ProviderError('comparison credential unavailable', kind='configuration', retryable=False)
                key = key.strip()
            remaining()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=limit, transport=self._http_transport) as client:
                with client.stream('POST', self._config['base_url']+'/chat/completions', content=raw,
                        headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json',
                                 'Accept': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                    audit['http_status'] = response.status_code
                    remaining()
                    if not 200 <= response.status_code < 300:
                        audit['outcome'] = 'http_error'
                        audit['retry_after_s'] = _retry_after(response.headers.get('Retry-After'))
                        # Do not read, persist or interpolate an HTTP error body.
                        raise ProviderError('comparison HTTP status '+str(response.status_code),
                                            kind='http', retryable=False, retry_after_s=audit['retry_after_s'])
                    chunks = []
                    for chunk in response.iter_bytes(chunk_size=8192):
                        remaining()
                        audit['response_bytes'] += len(chunk)
                        if audit['response_bytes'] > self._config['max_response_bytes']:
                            audit['outcome'] = 'response_too_large'
                            raise ProviderError('comparison response byte limit exceeded', kind='response_envelope', retryable=False)
                        chunks.append(chunk)
                    body = _envelope(b''.join(chunks))
                    remaining()
                    fingerprint = body.get('system_fingerprint')
                    if isinstance(fingerprint, str) and len(fingerprint) <= 1000:
                        audit['system_fingerprint_sha256'] = hashlib.sha256(fingerprint.encode()).hexdigest()
                    audit['outcome'] = 'ok'
                    return body
        except (TimeoutError, httpx.TimeoutException):
            audit['outcome'] = 'timeout'
            raise TimeoutError('comparison HTTP timeout') from None
        except ProviderError as exc:
            if audit['outcome'] == 'pending':
                audit['outcome'] = 'invalid_envelope' if exc.kind == 'response_envelope' else 'provider_error'
            raise
        except Exception:
            audit['outcome'] = 'transport_error'
            raise ProviderError('comparison HTTP transport failure', kind='transport', retryable=False) from None


def _description(manifest, config, root):
    audit = protocol.validate_manifest(manifest, root)
    if manifest['evidence_kind'] != 'live_model':
        raise ValueError('preparation requires a new explicit live-model manifest')
    transport = ChatCompletionTransport(manifest['model_config'], config)
    materials = []
    for case in manifest['cases']:
        for phase in case['phases']:
            for entry in phase['files']:
                text = protocol._source(entry, root)
                materials.append({'case_id': case['id'], 'phase': phase['id'], **entry,
                                  'utf8_bytes': len(text.encode('utf-8')), 'chars': len(text)})
    return {'schema_version': 1, 'status': 'prepared_only_no_authorization',
            'manifest_sha256': protocol.digest(manifest), 'planned_trials': audit['planned_trials'],
            'transport_identity': transport.comparison_identity, 'materials': materials,
            'runtime_data_categories': ['task and tool instructions', 'authorized source excerpts and prerecorded replies',
                                        'model outputs and conversation history', 'scientific tool feedback'],
            'private_rubric_in_workflow_projection': False, 'online_inference_calls': 0,
            'actual_model_revision_authenticated': False,
            'note': 'This package freezes a proposed scope, not a human authorization or a model capability result.'}


def prepare_comparison(manifest, config, output, *, root=protocol.ROOT):
    config = _config(config)
    description = _description(manifest, config, root)
    output = Path(output)
    if output.exists():
        raise ValueError('use a new preparation output directory')
    output.mkdir(parents=True, exist_ok=False)
    driver.adapters._write(output/'manifest.json', manifest)
    driver.adapters._write(output/'transport.json', config)
    driver.adapters._write(output/'preflight.json', description)
    return description


def validate_preparation(directory, *, root=protocol.ROOT):
    directory = Path(directory)
    manifest = protocol._read(directory/'manifest.json')
    config = protocol._read(directory/'transport.json')
    report = protocol._read(directory/'preflight.json')
    expected = _description(manifest, _config(config), root)
    if report != expected:
        raise ValueError('preparation is stale or was changed; freeze a new package')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--manifest', required=True, type=Path)
    prepare.add_argument('--transport', required=True, type=Path)
    prepare.add_argument('--output', required=True, type=Path)
    check = commands.add_parser('check')
    check.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            report = prepare_comparison(protocol._read(args.manifest), protocol._read(args.transport), args.output)
        else:
            report = validate_preparation(args.directory)
        print(json.dumps({'status': report['status'], 'manifest_sha256': report['manifest_sha256'],
                          'planned_trials': report['planned_trials'], 'online_inference_calls': 0}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ProviderError):
        print(json.dumps({'status': 'refused', 'online_inference_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
