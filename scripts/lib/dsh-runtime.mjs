// Per-run provider overlay. The original launcher/config/gate remain untouched.
import { createRequire } from 'node:module';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

export function providerOverlay(original, { api, model, idleMs = 60000 }) {
  const rows = structuredClone(original);
  const llm = rows.find(row => row.id === 'llm-pi-ai');
  const acp = rows.find(row => row.id === 'acp');
  const route = llm?.config?.providers?.ustc;
  if (!route || acp?.config?.provider !== 'ustc') throw new Error('Unexpected trusted provider configuration');
  if (api && !['openai-completions', 'openai-responses'].includes(api)) throw new Error('Unsupported provider transport');
  const selected = model || acp.config.model;
  if (!route.models?.some(entry => entry.id === selected)) throw new Error('Model not present in trusted provider catalog');
  if (!Number.isInteger(idleMs) || idleMs < 10000 || idleMs > 180000) throw new Error('Invalid provider idle deadline');
  if (Object.keys(route).some(key => /^(apiKey|token|secret|password|headers)$/i.test(key))) throw new Error('Provider overlay must use a credential reference, not copied secret values');
  route.api = api || route.api;
  route.streamIdleTimeoutMs = idleMs;
  route.timeoutMs = idleMs + 15000;
  route.retryPolicy = { mode: 'normal', maxRetries: 0 };
  acp.config.model = selected;
  // Implementation delegates receive file tools only. Removing capabilities here
  // prevents avoidable requests; the original approval gate still reviews each file.
  const disabledTools = ['tool-bash', 'tool-pwsh', 'tool-jobs', 'tool-fs-search',
    'tool-skill', 'tool-todo', 'tool-goal', 'tool-workflow', 'tool-web',
    'tool-subagent', 'tool-subagent-fork', 'tool-subagent-control',
    'tool-subagent-list-agents', 'tool-plugin-manager'];
  return { patch: [{ id: llm.id, config: { providers: { ustc: route } } },
                   { id: acp.id, config: { provider: 'ustc', model: selected } },
                   ...disabledTools.map(id => ({ id, disabled: true }))],
           metadata: { provider: 'ustc', model: selected, api: route.api, idleMs, maxRetries: 0 } };
}

function shellQuote(value) { return "'" + value.replaceAll("'", "'\\''") + "'"; }

export async function prepareRuntime(bridgeRoot, options = {}) {
  const trustedPatch = '/home/riveruning/tools/dsh-acp-ustc.patch.yml';
  const pinnedDsh = '/home/riveruning/.npm/_npx/1e7f6d9597241db0/node_modules/@deepseek-ai/dsh/lib/bin.js';
  const requireFromDsh = createRequire(resolve(pinnedDsh, '../../package.json'));
  const { parse, stringify } = requireFromDsh('yaml');
  const configured = providerOverlay(parse(readFileSync(trustedPatch, 'utf8')), options);
  const trustedConfig = resolve(bridgeRoot, 'dist/config.js');
  const { PRECONFIGURED_AGENTS } = await import(pathToFileURL(trustedConfig).href);
  if (PRECONFIGURED_AGENTS.dsh_ustc?.permissionGateMarker !== 'CODEX_DSH_PERMISSION_GATE_V1_READY') throw new Error('Unexpected gate configuration');
  const gatePatch = resolve(bridgeRoot, 'runtime/permission-gate.patch.yml');
  if (!existsSync(pinnedDsh) || !existsSync(gatePatch)) throw new Error('Missing trusted DSH or gate');
  // Outside the DSH workspace: its file tools cannot modify this runtime overlay.
  const controlDir = mkdtempSync(resolve(tmpdir(), 'biomodel-dsh-control-'));
  const overlayPath = resolve(controlDir, 'provider.patch.yml');
  const launcher = resolve(controlDir, 'launch.sh');
  const entry = resolve(controlDir, 'bridge.mjs');
  try {
    writeFileSync(overlayPath, stringify(configured.patch), { mode: 0o600 });
    const launch = ['#!/usr/bin/env bash', 'set -euo pipefail',
      'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy',
      'export NO_PROXY="localhost,127.0.0.1,api.llm.ustc.edu.cn"', 'export no_proxy="$NO_PROXY"',
      `exec ${[process.execPath, pinnedDsh, '--profile', 'acp', '--patch', trustedPatch, '--patch', gatePatch, '--patch', overlayPath].map(shellQuote).join(' ')}`, ''].join('\n');
    writeFileSync(launcher, launch, { mode: 0o600 });
    const requireFromBridge = createRequire(resolve(bridgeRoot, 'package.json'));
    const serverTransport = requireFromBridge.resolve('@modelcontextprotocol/sdk/server/stdio.js');
    const bootstrap = `import { ACPServer } from ${JSON.stringify(pathToFileURL(resolve(bridgeRoot, 'dist/server.js')).href)};
import { PRECONFIGURED_AGENTS } from ${JSON.stringify(pathToFileURL(trustedConfig).href)};
import { StdioServerTransport } from ${JSON.stringify(pathToFileURL(serverTransport).href)};
const agents = { ...PRECONFIGURED_AGENTS, dsh_ustc: { ...PRECONFIGURED_AGENTS.dsh_ustc, command: 'bash', args: [${JSON.stringify(launcher)}] } };
const server = new ACPServer({ agents });
await server.server.connect(new StdioServerTransport());
`;
    writeFileSync(entry, bootstrap, { mode: 0o600 });
    return { entry, metadata: configured.metadata,
      cleanup: () => rmSync(controlDir, { recursive: true, force: true }) };
  } catch (error) {
    rmSync(controlDir, { recursive: true, force: true });
    throw error;
  }
}
