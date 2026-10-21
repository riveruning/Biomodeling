"""Read-only diagnostic for the configured DSH provider; never prints credentials.

Uses the host's existing PyYAML, without installing packages or modifying config.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['metadata', 'models', 'chat', 'chat-tool', 'stream', 'responses', 'responses-stream', 'responses-tool'], default='metadata')
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--model')
    parser.add_argument('--auto-tool', action='store_true')
    args = parser.parse_args()
    patches = yaml.safe_load(Path('/home/riveruning/tools/dsh-acp-ustc.patch.yml').read_text())
    provider = None
    model = None
    for item in patches:
        config = item.get('config', {})
        if 'providers' in config:
            provider = config['providers']['ustc']
        if config.get('provider') == 'ustc' and 'model' in config:
            model = config['model']
    if provider is None or model is None:
        raise RuntimeError('Expected configured USTC route and selected model')
    if args.model:
        if args.model not in [entry['id'] for entry in provider.get('models', [])]:
            raise RuntimeError('Model override must exist in configured route')
        model = args.model
    # Match the DSH credential-store fallback without copying the secret anywhere.
    import os
    reference = provider['apiKeyEnv']
    store = yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())
    key = os.environ.get(reference) or store.get('refs', {}).get(reference)
    if not isinstance(key, str) or not key:
        print(json.dumps({'credential_configured': False, 'model': model}))
        return 1
    base = provider['baseURL'].rstrip('/')
    if base != 'https://api.llm.ustc.edu.cn/v1':
        raise RuntimeError('Unexpected credential destination; diagnostic refuses transfer')
    if args.mode == 'metadata':
        print(json.dumps({'credential_configured': True, 'credential_source': 'launch_env' if os.environ.get(reference) else 'existing_store',
                          'provider': 'ustc', 'base_url': base, 'api': provider.get('api'), 'model': model}))
        return 0

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    payload = None
    endpoint = '/models'
    if args.mode in ['chat', 'chat-tool', 'stream']:
        endpoint = '/chat/completions'
        body = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply exactly READY.'}],
                'max_tokens': 256, 'stream': args.mode == 'stream'}
        if args.mode == 'chat-tool':
            body.update(messages=[{'role': 'user', 'content': 'Call emit_ready with text READY.'}],
                        tools=[{'type': 'function', 'function': {'name': 'emit_ready', 'description': 'Return readiness',
                                'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                                               'required': ['text'], 'additionalProperties': False}}}],
                        tool_choice={'type': 'function', 'function': {'name': 'emit_ready'}})
            if args.auto_tool:
                body.pop('tool_choice')
        payload = json.dumps(body).encode()
    elif args.mode in ['responses', 'responses-stream', 'responses-tool']:
        endpoint = '/responses'
        body = {'model': model, 'input': 'Reply exactly READY.',
                'max_output_tokens': 256, 'stream': args.mode == 'responses-stream'}
        if args.mode == 'responses-tool':
            body.update(input='Call emit_ready with text READY.',
                        tools=[{'type': 'function', 'name': 'emit_ready', 'description': 'Return readiness',
                                'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                                               'required': ['text'], 'additionalProperties': False}}],
                        tool_choice={'type': 'function', 'name': 'emit_ready'})
            if args.auto_tool:
                body.pop('tool_choice')
        payload = json.dumps(body).encode()
    request = urllib.request.Request(base + endpoint, data=payload, headers=headers)
    started = time.monotonic()
    try:
        with opener.open(request, timeout=args.timeout) as response:
            if args.mode in ['stream', 'responses-stream']:
                events = 0
                content = ''
                reasoning_chars = 0
                for line in response:
                    if time.monotonic() - started > args.timeout:
                        raise TimeoutError('Total streaming probe deadline exceeded')
                    if not line.startswith(b'data:'):
                        continue
                    raw = line[5:].strip()
                    events += 1
                    if raw == b'[DONE]':
                        break
                    item = json.loads(raw)
                    if events == 1:
                        print(json.dumps({'event': 'first_stream_event', 'type': item.get('type'),
                                          'elapsed_s': round(time.monotonic() - started, 2)}), flush=True)
                    if item.get('type') == 'response.output_text.delta':
                        content += item.get('delta') or ''
                    for choice in item.get('choices', []):
                        delta = choice.get('delta', {})
                        content += delta.get('content') or ''
                        reasoning_chars += len(delta.get('reasoning_content') or '')
                    if content:
                        break
                print(json.dumps({'status': response.status, 'elapsed_s': round(time.monotonic() - started, 2),
                                  'events': events, 'content': content.replace(key, '[REDACTED]')[:80], 'reasoning_chars': reasoning_chars}))
                return 0 if content else 2
            result = json.loads(response.read(2 * 1024 * 1024))
            output = {'status': response.status, 'elapsed_s': round(time.monotonic() - started, 2)}
            if args.mode == 'models':
                ids = [item.get('id') for item in result.get('data', []) if isinstance(item, dict)]
                output.update(model_count=len(ids), selected_model_available=model in ids, models=ids[:30])
            elif args.mode in ['responses', 'responses-tool']:
                output.update(response_status=result.get('status'),
                              output_types=[item.get('type') for item in result.get('output', [])],
                              content=[str(content.get('text') or '').replace(key, '[REDACTED]')[:80]
                                       for item in result.get('output', []) for content in item.get('content', []) if content.get('type') == 'output_text'],
                              usage=result.get('usage'), error=result.get('error'))
                if args.mode == 'responses-tool':
                    output['tool_calls'] = [{'name': item.get('name'), 'arguments': str(item.get('arguments', '')).replace(key, '[REDACTED]')[:100]}
                                            for item in result.get('output', []) if item.get('type') == 'function_call']
            else:
                choices = result.get('choices', [])
                output.update(choices=[{'finish_reason': c.get('finish_reason'),
                                       'content': str(c.get('message', {}).get('content') or '').replace(key, '[REDACTED]')[:80],
                                       'reasoning_chars': len(c.get('message', {}).get('reasoning_content') or ''),
                                       'tool_calls': [{'name': t.get('function', {}).get('name'),
                                                      'arguments': str(t.get('function', {}).get('arguments', '')).replace(key, '[REDACTED]')[:100]}
                                                      for t in c.get('message', {}).get('tool_calls', [])]} for c in choices],
                              usage=result.get('usage'))
            print(json.dumps(output, ensure_ascii=False))
            return 0
    except urllib.error.HTTPError as error:
        # Provider error bodies are filtered and bounded; request headers are never logged.
        body = error.read(4096).decode(errors='replace').replace(key, '[REDACTED]')
        print(json.dumps({'status': error.code, 'elapsed_s': round(time.monotonic() - started, 2), 'error': body[:300]}))
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'message': str(error).replace(key, '[REDACTED]')[:200],
                          'elapsed_s': round(time.monotonic() - started, 2)}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
