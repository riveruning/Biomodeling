"""Same F1 decisions over in-memory HTTP; no network service or real model."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import httpx

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PRIOR = ROOT/'artifacts/audit-t08b-f1-equation-prose-20261019'
spec = importlib.util.spec_from_file_location('t08c_http_controls', ROOT/'benchmarks/development/comparison_transport.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver


def main():
    manifest = driver.protocol.development_manifest(ROOT)
    old_manifest = json.loads((PRIOR/'control-inputs/manifest.json').read_text())
    assert manifest == old_manifest  # No product source/inputs/settings changed.
    controls = OUT/'control-inputs'
    controls.mkdir(exist_ok=False)
    shutil.copyfile(PRIOR/'control-inputs/manifest.json', controls/'manifest.json')
    shutil.copyfile(PRIOR/'control-inputs/scripts.json', controls/'scripts.json')
    scripts = json.loads((controls/'scripts.json').read_text())
    driver.validate_scripts(manifest, scripts)
    config = {'base_url': 'https://comparison.invalid/v1', 'api_key_env': 'BIOMODEL_API_KEY',
              'request_timeout_s': 30., 'max_response_bytes': 2_000_000}
    driver.adapters._write(controls/'transport.json', config)
    identity = module.ChatCompletionTransport(manifest['model_config'], config,
        http_transport=httpx.MockTransport(lambda _: None)).comparison_identity
    driver.adapters._write(OUT/'control-freeze.json', {
        'manifest_identical_to_F1': True, 'scripts_identical_to_F1': True,
        'scripts_sha256': hashlib.sha256((controls/'scripts.json').read_bytes()).hexdigest(),
        'product_toolchain_sha256': manifest['toolchain_sha256'], 'transport_identity': identity,
        'mock_endpoint': config['base_url'], 'online_inference_calls': 0})
    def factory(p):
        fixture = driver.ScriptedTransport(p, scripts['trials'][p['trial']['id']])
        def handler(request):
            assert str(request.url) == config['base_url']+'/chat/completions'
            response = fixture.request(json.loads(request.content), 30)
            response['choices'][0].update(index=0, finish_reason='stop')
            response['choices'][0]['message']['role'] = 'assistant'
            response['system_fingerprint'] = 'offline-http-fixture'
            return httpx.Response(200, json=response)
        return module.ChatCompletionTransport(p['model_config'], config, http_transport=httpx.MockTransport(handler))
    result = driver.run_comparison(manifest, factory, OUT/'http-mock-execution')
    # An explicit example for review, not a selected provider/model or approval.
    example = deepcopy(manifest)
    example['evidence_kind'] = 'live_model'
    example['model_config'].update(provider='example-not-selected', model='example-not-selected', revision='example-not-selected')
    preflight = module.prepare_comparison(example, config, OUT/'preparation-example')
    assert module.validate_preparation(OUT/'preparation-example') == preflight
    print(json.dumps(result['engineering'], ensure_ascii=False))
    print(json.dumps({'preparation_example': preflight['status'], 'selected_real_model': False,
                      'online_inference_calls': 0}))


if __name__ == '__main__':
    main()
