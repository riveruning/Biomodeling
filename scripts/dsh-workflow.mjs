#!/usr/bin/env node
import { createRequire } from 'node:module';
import { createHash, randomUUID } from 'node:crypto';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildPrompt, scopedPath, supervise, unwrap, validateTask } from './lib/dsh-supervisor.mjs';
import { prepareRuntime } from './lib/dsh-runtime.mjs';

const workspace = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
function argsOf(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--dry-run') args.dryRun = true;
    else if (['--task', '--bridge-root', '--provider-api', '--model'].includes(flag)) {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error(`Missing value for ${flag}`);
      args[{ '--task': 'task', '--bridge-root': 'bridgeRoot', '--provider-api': 'providerApi', '--model': 'model' }[flag]] = argv[++i];
    } else throw new Error(`Unknown argument ${flag}`);
  }
  if (!args.task) throw new Error('Usage: node scripts/dsh-workflow.mjs --task tasks/example.json [--bridge-root /trusted/bridge] [--dry-run]');
  return args;
}
function redact(value) {
  if (typeof value === 'string') return value.replace(/(api[_-]?key\s*[:=]\s*)[A-Za-z0-9_-]{16,}/gi, '$1[REDACTED]').replace(/(Bearer\s+)[A-Za-z0-9._-]+/gi, '$1[REDACTED]');
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /^(api_?key|authorization|password|secret|access_token|refresh_token)$/i.test(key) ? '[REDACTED]' : redact(item)]));
  return value;
}
async function main() {
  const args = argsOf(process.argv.slice(2));
  const task = validateTask(JSON.parse(readFileSync(scopedPath(workspace, args.task), 'utf8')), workspace);
  if (task.id === 'replace-with-task-id') throw new Error('Task template must be filled before dispatch');
  const prompt = buildPrompt(task, workspace);
  if (args.dryRun) { console.log(JSON.stringify({ task: task.id, prompt, budget: task.budget }, null, 2)); return; }

  const bridgeRoot = realpathSync(args.bridgeRoot || '/home/riveruning/tools/mcp-acp-bridge');
  const entry = resolve(bridgeRoot, 'dist/index.js');
  const relativeBridge = bridgeRoot.startsWith(`${workspace}/`) || bridgeRoot === workspace;
  if (relativeBridge || !existsSync(entry)) throw new Error('Use a built trusted bridge outside the DSH-writable workspace');
  const requireFromBridge = createRequire(resolve(bridgeRoot, 'package.json'));
  const { Client } = await import(pathToFileURL(requireFromBridge.resolve('@modelcontextprotocol/sdk/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(requireFromBridge.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href);
  const runId = `${task.id}-${Date.now()}-${randomUUID().slice(0, 8)}`;
  const output = resolve(workspace, '.workflow/runs', runId);
  mkdirSync(output, { recursive: true });
  let auditBytes = 0;
  function record(event) {
    const line = JSON.stringify({ time: new Date().toISOString(), ...redact(event) }) + '\n';
    auditBytes += Buffer.byteLength(line);
    if (auditBytes > 20 * 1024 * 1024) throw new Error('Audit size budget exceeded');
    appendFileSync(resolve(output, 'audit.jsonl'), line);
  }
  writeFileSync(resolve(output, 'task.json'), JSON.stringify(task, null, 2));
  writeFileSync(resolve(output, 'prompt.txt'), prompt);
  record({ event: 'configured', taskId: task.id, bridgeRoot, bridgeEntrypointHash: createHash('sha256').update(readFileSync(entry)).digest('hex') });
  const runtimeConfig = JSON.parse(readFileSync(resolve(workspace, 'config/development-runtime.json'), 'utf8'));
  if (runtimeConfig.schema_version !== '1') throw new Error('Unsupported development runtime config');
  const runtime = await prepareRuntime(bridgeRoot, { api: args.providerApi || runtimeConfig.provider_api,
    model: args.model || runtimeConfig.model, idleMs: runtimeConfig.stream_idle_ms });
  record({ event: 'provider_runtime', ...runtime.metadata });
  console.log(JSON.stringify({ event: 'started', task: task.id, output }));
  const client = new Client({ name: 'biomodel-development-supervisor', version: '0.1' }, { capabilities: {} });
  const transport = new StdioClientTransport({ command: process.execPath, args: [runtime.entry], cwd: bridgeRoot, stderr: 'pipe' });
  let stderrBytes = 0;
  transport.stderr?.on('data', chunk => {
    stderrBytes += chunk.length;
    if (stderrBytes <= 1024 * 1024) appendFileSync(resolve(output, 'bridge-stderr.log'), chunk);
  });
  const connectionId = `biomodel-${runId}`;
  let initialized = false, stopping = false;
  const interrupt = () => { stopping = true; };
  process.on('SIGINT', interrupt); process.on('SIGTERM', interrupt);
  const call = async (name, args) => unwrap(await client.callTool({ name, arguments: args }));
  try {
    await client.connect(transport);
    const initializedState = await call('initialize_client', { agent: 'dsh_ustc', connectionId, cwd: workspace, toolApprovalMode: 'manual' });
    initialized = true;
    if (initializedState.permissionGate !== 'verified') throw new Error('DSH permission gate not verified');
    const submitted = await call('send_prompt', { connectionId, prompt, mode: 'poll' });
    if (!submitted.task?.taskId) throw new Error('Missing bridge task ID');
    record({ event: 'submitted', task: submitted.task });
    const summary = await supervise({ task, workspace, connectionId, taskId: submitted.task.taskId, call, record,
      notify: event => console.log(JSON.stringify(redact(event))), signal: () => stopping,
      saveResponse: text => appendFileSync(resolve(output, 'response.txt'), text) });
    summary.outputDirectory = output;
    writeFileSync(resolve(output, 'summary.json'), JSON.stringify(redact(summary), null, 2));
    console.log(JSON.stringify(redact({ event: 'summary', ...summary })));
    process.exitCode = summary.outcome === 'ready_for_review' ? 0 : summary.outcome === 'needs_review' ? 3 : 1;
  } catch (error) {
    record({ event: 'error', message: error.message });
    writeFileSync(resolve(output, 'summary.json'), JSON.stringify({ taskId: task.id,
      outcome: 'supervisor_error', error: error.message, acceptance: 'not_run', outputDirectory: output }, null, 2));
    console.error(JSON.stringify({ event: 'error', message: error.message, output }));
    process.exitCode = 1;
  } finally {
    if (initialized) try { await call('close_connection', { connectionId }); }
    catch (error) { console.error(JSON.stringify({ event: 'close_failed', message: error.message })); process.exitCode = 1; }
    try { await client.close(); }
    finally {
      runtime.cleanup();
      process.off('SIGINT', interrupt); process.off('SIGTERM', interrupt);
    }
  }
}
main().catch(error => { console.error(JSON.stringify({ event: 'error', message: error.message })); process.exitCode = 1; });
