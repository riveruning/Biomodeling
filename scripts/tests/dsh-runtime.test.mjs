import test from 'node:test';
import assert from 'node:assert/strict';
import { providerOverlay } from '../lib/dsh-runtime.mjs';

const original = [{ id: 'llm-pi-ai', config: { providers: { ustc: { api: 'openai-responses', apiKeyEnv: 'USTC_API_KEY',
  baseURL: 'https://api.llm.ustc.edu.cn/v1', models: [{ id: 'original' }, { id: 'alternate' }] } } } },
  { id: 'acp', config: { provider: 'ustc', model: 'original' } }];

test('runtime overlay preserves original config and credential reference, bounds retries', () => {
  const before = structuredClone(original);
  const result = providerOverlay(original, { api: 'openai-completions', model: 'alternate' });
  assert.deepEqual(original, before);
  assert.equal(result.patch[0].config.providers.ustc.apiKeyEnv, 'USTC_API_KEY');
  assert.equal(result.patch[0].config.providers.ustc.api, 'openai-completions');
  assert.equal(result.patch[1].config.model, 'alternate');
  assert.equal(result.patch[0].config.providers.ustc.streamIdleTimeoutMs, 60000);
  assert.equal(result.patch[0].config.providers.ustc.retryPolicy.maxRetries, 0);
});

test('runtime overrides only configured models and supported wire protocols', () => {
  assert.throws(() => providerOverlay(original, { model: 'unknown' }), /catalog/);
  assert.throws(() => providerOverlay(original, { api: 'unreviewed-transport' }), /transport/);
});

test('runtime refuses to copy a plain credential into its overlay', () => {
  const bad = structuredClone(original);
  bad[0].config.providers.ustc.apiKey = 'placeholder';
  assert.throws(() => providerOverlay(bad, {}), /credential reference/);
});
