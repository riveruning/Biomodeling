import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, symlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { buildPrompt, DEFAULT_BUDGET, deliverySummary, permissionScope, scopedPath, supervise, unwrap, validateTask } from '../lib/dsh-supervisor.mjs';

const workspace = resolve(import.meta.dirname, '../..');
const task = { schema_version: '1', id: 'test-task', objective: 'one behavior', context: 'frozen interface', instructions: 'write one file',
  read_files: ['README.md'], write_files: ['src/example.py'], expected_files: ['src/example.py'], acceptance: ['actual behavior'], budget: { ...DEFAULT_BUDGET } };
const state = (status = 'generating', extra = {}) => ({ status, sessionId: 'session', task: { taskId: 'task' }, response: '', toolCallCount: 0, pendingPermissions: [], ...extra });
const request = (overrides = {}) => ({ requestId: 'permission', taskId: 'task', sessionId: 'session',
  toolCall: { rawInput: { file_path: resolve(workspace, 'src/example.py'), content: '# code\n' } },
  policy: { decision: 'allow', operation: 'create', paths: [{ input: resolve(workspace, 'src/example.py'), resolved: resolve(workspace, 'src/example.py'), withinWorkspace: true, sensitive: false }] }, ...overrides });

function harness(snapshots, options = {}) {
  let time = 0, index = 0;
  let files = { 'src/example.py': null };
  const calls = [], records = [], notifications = [], sleeps = [], responses = [];
  const call = async (name, args) => {
    calls.push({ name, args });
    if (name === 'read_response') {
      const snapshot = snapshots[Math.min(index++, snapshots.length - 1)];
      if (snapshot.write) files = { 'src/example.py': snapshot.write };
      return snapshot;
    }
    if (name === 'approve_tool_call') return options.review || { outcome: 'allowed_once' };
    if (name === 'cancel_task') {
      if (options.cancelFails) throw new Error('cancel failed');
      return { status: 'cancelled', task: { status: 'cancelled' } };
    }
    if (name === 'close_connection') return {};
    throw new Error(`Unexpected call ${name}`);
  };
  return {
    calls, records, notifications, sleeps, responses,
    run: () => supervise({ task: { ...task, budget: { ...task.budget, ...options.budget } }, workspace,
      connectionId: 'connection', taskId: 'task', call, snapshotFiles: () => ({ ...files }), now: () => time,
      sleep: async ms => { sleeps.push(ms); time += ms; }, record: e => records.push(e), notify: e => notifications.push(e), saveResponse: r => responses.push(r) }),
  };
}

test('quiet polling backs off, requests no full histories, prints only changes', async () => {
  const h = harness([state(), state(), state(), state(), state('complete', { write: 'sha', response: 'done' })]);
  const result = await h.run();
  assert.deepEqual(h.sleeps, [2000, 5000, 10000, 15000]);
  assert.equal(result.outcome, 'ready_for_review');
  assert.equal(result.acceptance, 'not_run');
  assert.deepEqual(result.changedFiles, ['src/example.py']);
  assert.equal(h.notifications.filter(e => e.event === 'status').length, 2);
  assert.equal(h.calls.filter(c => c.name === 'read_response').every(c => c.args.includeToolDetails === false && c.args.sinceLastRead === true), true);
  assert.deepEqual(h.responses, ['done']);
});

test('verified in-scope file request gets review once, not blanket approve', async () => {
  const h = harness([state('awaiting_permission', { pendingPermissions: [request()] }), state('complete', { write: 'sha' })]);
  const result = await h.run();
  assert.equal(result.outcome, 'ready_for_review');
  const reviews = h.calls.filter(c => c.name === 'approve_tool_call');
  assert.equal(reviews.length, 1);
  assert.equal(reviews[0].args.action, 'review');
  assert.equal(result.reviews, 1);
  assert.equal(h.records.some(e => e.event === 'permission_evidence' && e.request.toolCall.rawInput.content), true);
});

test('waiting/unknown policy stops and preserves evidence without override', async () => {
  const h = harness([state('awaiting_permission', { pendingPermissions: [request({ policy: { decision: 'wait', reason: 'unreviewed execution' } })] })]);
  const result = await h.run();
  assert.equal(result.outcome, 'needs_review');
  assert.equal(result.reason, 'unreviewed execution');
  assert.equal(h.calls.some(c => c.name === 'approve_tool_call'), false);
  assert.equal(h.calls.some(c => c.name === 'cancel_task'), true);
  assert.equal(h.records.some(e => e.event === 'permission_evidence'), true);
});

test('gateway allows workspace path but task scope forbids unrelated edits', async () => {
  const outside = request({ policy: { decision: 'allow', operation: 'edit', paths: [{ input: resolve(workspace, 'README.md'), resolved: resolve(workspace, 'README.md'), withinWorkspace: true, sensitive: false }] } });
  const h = harness([state('awaiting_permission', { pendingPermissions: [outside] })]);
  assert.equal((await h.run()).outcome, 'needs_review');
  assert.equal(h.calls.some(c => c.name === 'approve_tool_call'), false);
});

test('stale permission session and duplicate requests fail closed', async () => {
  const stale = harness([state('awaiting_permission', { pendingPermissions: [request({ sessionId: 'old' })] })]);
  assert.equal((await stale.run()).outcome, 'permission_mismatch');
  const duplicate = harness([state('awaiting_permission', { pendingPermissions: [request()] }), state('awaiting_permission', { pendingPermissions: [request()] })]);
  assert.equal((await duplicate.run()).outcome, 'permission_mismatch');
  assert.equal(duplicate.calls.filter(c => c.name === 'approve_tool_call').length, 1);
});

test('policy changes at final review do not become approvals', async () => {
  const h = harness([state('awaiting_permission', { pendingPermissions: [request()] })], { review: { outcome: 'waiting', policy: { reason: 'changed inputs' } } });
  const result = await h.run();
  assert.equal(result.outcome, 'needs_review');
  assert.equal(result.reason, 'changed inputs');
});

test('no initial activity expires early rather than polling full task deadline', async () => {
  const h = harness([state()], { budget: { first_activity_ms: 30000 } });
  const result = await h.run();
  assert.equal(result.outcome, 'no_initial_activity');
  assert.equal(result.elapsedMs, 30000);
  assert.ok(result.polls < 10);
});

test('repeated text without files cannot indefinitely extend implementation task', async () => {
  const h = harness([state('generating', { response: 'still planning' })], { budget: { first_write_ms: 7000 } });
  const result = await h.run();
  assert.equal(result.outcome, 'no_initial_file');
  assert.equal(result.elapsedMs, 7000);
});

test('DSH completion without actual files is not delivery', async () => {
  const h = harness([state('complete', { response: 'I implemented everything' })]);
  const result = await h.run();
  assert.equal(result.outcome, 'no_delivery');
  assert.deepEqual(result.missingFiles, ['src/example.py']);
});

test('unchanged existing files do not count as implementation', () => {
  const result = deliverySummary(task, { 'src/example.py': 'same' }, { 'src/example.py': 'same' });
  assert.equal(result.delivered, false);
  assert.deepEqual(result.missingFiles, []);
});

test('cancellation failure is explicit and closes the connection', async () => {
  const h = harness([state()], { budget: { first_activity_ms: 1000 }, cancelFails: true });
  const result = await h.run();
  assert.equal(result.outcome, 'cancellation_failed');
  assert.equal(h.calls.some(c => c.name === 'close_connection'), true);
});

test('task switch cannot silently reuse another task permission scope', async () => {
  const h = harness([state('generating', { task: { taskId: 'another-task' } })]);
  assert.equal((await h.run()).outcome, 'task_mismatch');
});

test('task paths protect supervisor, reject traversal and symlink escape', () => {
  assert.throws(() => validateTask({ ...task, write_files: ['scripts/dsh-workflow.mjs'], expected_files: ['scripts/dsh-workflow.mjs'] }, workspace), /supervisor/);
  assert.throws(() => validateTask({ ...task, read_files: ['src/../README.md'] }, workspace), /traversal/);
  assert.throws(() => scopedPath(workspace, '../outside.py'), /outside/);
  assert.throws(() => scopedPath(workspace, '.aws/config'), /Protected/);
  const temp = mkdtempSync(resolve(tmpdir(), 'biomodel-workflow-test-'));
  try {
    mkdirSync(resolve(temp, 'workspace'));
    mkdirSync(resolve(temp, 'outside'));
    symlinkSync(resolve(temp, 'outside'), resolve(temp, 'workspace/link'));
    assert.throws(() => scopedPath(resolve(temp, 'workspace'), 'link/file.py'), /Symlink/);
  } finally { rmSync(temp, { recursive: true, force: true }); }
});

test('missing structured tool evidence refuses review', () => {
  assert.throws(() => unwrap({ content: [{ type: 'text', text: '{}' }] }), /structured/);
  assert.equal(permissionScope(request({ toolCall: {} }), task, workspace).allowed, false);
});

test('task prompt is specific and never includes executable test command', () => {
  const prompt = buildPrompt({ ...task, verification_commands: [['dangerous-test-command']] }, workspace);
  assert.ok(prompt.includes(resolve(workspace, 'src/example.py')));
  assert.ok(prompt.includes('Codex'));
  assert.equal(prompt.includes('dangerous-test-command'), false);
});
