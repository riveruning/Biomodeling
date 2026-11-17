import test from 'node:test';
import assert from 'node:assert/strict';
import { providerOverlay } from '../lib/dsh-runtime.mjs';

const original = [{ id: 'llm-pi-ai', config: { providers: { ustc: { api: 'openai-responses', apiKeyEnv: 'USTC_API_KEY',
  baseURL: 'https://api.llm.ustc.edu.cn/v1', models: [{ id: 'original' }, { id: 'alternate' }] } } } },
  { id: 'acp', config: { provider: 'ustc', model: 'original' } }];

test('bridge task deadline supports long max-effort tasks within a finite bound', () => {
  assert.equal(providerOverlay(original, {}).metadata.taskTimeoutMs, 1800000);
  assert.equal(providerOverlay(original, { taskTimeoutMs: 7260000 }).metadata.taskTimeoutMs, 7260000);
  assert.equal(providerOverlay(original, { taskTimeoutMs: 43260000 }).metadata.taskTimeoutMs, 43260000);
  for (const taskTimeoutMs of [0, 2147483648, NaN, '7200000']) {
    assert.throws(() => providerOverlay(original, { taskTimeoutMs }), /task deadline/);
  }
});

test('provider stream idle deadline supports explicit long tasks within timer bounds', () => {
  const result = providerOverlay(original, { idleMs: 600000 });
  assert.equal(result.metadata.idleMs, 600000);
  assert.equal(result.patch[0].config.providers.ustc.streamIdleTimeoutMs, 600000);
  assert.equal(result.patch[0].config.providers.ustc.timeoutMs, 615000);
  assert.equal(result.patch[0].config.providers.ustc.retryPolicy.maxRetries, 0);
  for (const idleMs of [0, 9999, 2147483647 - 14999, NaN, '600000']) {
    assert.throws(() => providerOverlay(original, { idleMs }), /idle deadline/);
  }
});

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

test('proposal mode removes file and directory capabilities from the runtime', () => {
  const result = providerOverlay(original, { proposalOnly: true });
  for (const id of ['tool-fs', 'tool-str-replace-editor', 'tool-working-directory']) {
    assert.equal(result.patch.find(row => row.id === id)?.disabled, true);
  }
  assert.equal(result.metadata.proposalOnly, true);
  assert.equal(providerOverlay(original, {}).patch.some(row => row.id === 'tool-fs'), false);
  assert.throws(() => providerOverlay(original, { proposalOnly: 'true' }), /proposal capability/);
});

test('runtime refuses to copy a plain credential into its overlay', () => {
  const bad = structuredClone(original);
  bad[0].config.providers.ustc.apiKey = 'placeholder';
  assert.throws(() => providerOverlay(bad, {}), /credential reference/);
});

test('reasoning requires explicit trusted model support and remains isolated', () => {
  assert.throws(() => providerOverlay(original, { reasoning: 'max' }), /not declared/);
  assert.throws(() => providerOverlay(original, { reasoning: 'invalid' }), /Unsupported reasoning/);
  const configured = structuredClone(original);
  configured[0].config.providers.ustc.models[1].reasoningEfforts = { high: 'high' };
  const result = providerOverlay(configured, { model: 'alternate', reasoning: 'high' });
  assert.equal(result.patch[0].config.providers.ustc.reasoning, 'high');
  assert.equal(result.metadata.reasoning, 'high');
  assert.equal(configured[0].config.providers.ustc.reasoning, undefined);
  assert.throws(() => providerOverlay(configured, { model: 'alternate', reasoning: 'max' }), /not declared/);
});

test('explicit non-reasoning mode clears inherited effort for a configured chat model', () => {
  const configured = structuredClone(original);
  configured[0].config.providers.ustc.reasoning = 'max';
  const result = providerOverlay(configured, { model: 'alternate', reasoning: 'none' });
  assert.equal(result.metadata.reasoning, null);
  assert.equal(Object.hasOwn(result.patch[0].config.providers.ustc, 'reasoning'), false);
  assert.equal(configured[0].config.providers.ustc.reasoning, 'max');
  assert.equal(result.metadata.reasoningEfforts, null);
});

test('per-run reasoning capabilities apply only to the selected model', () => {
  const result = providerOverlay(original, { model: 'alternate', reasoning: 'max', reasoningEfforts: { max: 'max' } });
  const models = result.patch[0].config.providers.ustc.models;
  assert.deepEqual(models[1].reasoningEfforts, { max: 'max' });
  assert.equal(models[0].reasoningEfforts, undefined);
  assert.equal(original[0].config.providers.ustc.models[1].reasoningEfforts, undefined);
  assert.equal(result.metadata.reasoning, 'max');
  for (const invalid of [{}, [], { max: 'high' }, { invalid: 'invalid' }, null]) {
    assert.throws(() => providerOverlay(original, { reasoningEfforts: invalid }), /capability declaration/);
  }
});
