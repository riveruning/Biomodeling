// Development supervision only; never executes task-provided verification commands.
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';

export const DEFAULT_BUDGET = Object.freeze({
  first_activity_ms: 120000, idle_ms: 180000,
  first_write_ms: 300000, write_idle_ms: 300000, total_ms: 600000,
});
const FINAL_STATUSES = new Set(['complete', 'completed', 'cancelled', 'error', 'process_exited']);
const FORBIDDEN = new Set(['.git', '.aws', '.codex', '.agents', '.venv', '.workflow', 'node_modules']);

export function unwrap(result) {
  if (result?.isError) throw new Error((result.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n'));
  if (result?.structuredContent) return result.structuredContent;
  throw new Error('Bridge returned no structured evidence; refusing blind supervision');
}

export function scopedPath(workspace, path) {
  if (typeof path !== 'string' || !path || path.includes('\0')) throw new Error('Invalid task path');
  const target = resolve(workspace, path);
  const rel = relative(workspace, target);
  if (!rel || rel === '..' || rel.startsWith('../') || isAbsolute(rel)) throw new Error(`Path outside narrow workspace: ${path}`);
  const segments = rel.split('/');
  if (segments.some(part => FORBIDDEN.has(part)) || segments.at(-1) === 'AGENTS.md') throw new Error(`Protected task path: ${path}`);
  let ancestor = target;
  while (!existsSync(ancestor)) {
    if (lstatSafe(ancestor)?.isSymbolicLink()) throw new Error(`Broken symlink: ${path}`);
    ancestor = dirname(ancestor);
  }
  let current = workspace;
  for (const segment of segments) {
    current = resolve(current, segment);
    if (lstatSafe(current)?.isSymbolicLink()) throw new Error(`Symlink task path: ${path}`);
  }
  const realAncestor = realpathSync(ancestor);
  const ancestorRel = relative(workspace, realAncestor);
  if (ancestorRel === '..' || ancestorRel.startsWith('../') || isAbsolute(ancestorRel)) throw new Error(`Resolved path outside workspace: ${path}`);
  return target;
}

function lstatSafe(path) {
  try { return lstatSync(path); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

export function validateTask(task, workspace) {
  if (task.schema_version !== '1') throw new Error('Unsupported task schema');
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(task.id || '')) throw new Error('Invalid task ID');
  for (const key of ['objective', 'context', 'instructions']) {
    if (typeof task[key] !== 'string' || !task[key].trim()) throw new Error(`Missing task ${key}`);
  }
  for (const key of ['read_files', 'write_files', 'expected_files']) {
    if (!Array.isArray(task[key])) throw new Error(`Task ${key} must be an array`);
    if (new Set(task[key]).size !== task[key].length) throw new Error(`Duplicate task ${key}`);
    for (const path of task[key]) {
      if (isAbsolute(path) || path.split('/').includes('..')) throw new Error('Task paths must be explicit relative paths without traversal');
      scopedPath(workspace, path);
    }
  }
  if (!task.write_files.length || task.write_files.length > 6) throw new Error('Implementation task needs 1–6 explicit writable files');
  if (!task.expected_files.length) throw new Error('Implementation task needs expected files');
  if (task.expected_files.some(path => !task.write_files.includes(path))) throw new Error('Expected files must be writable');
  for (const path of task.write_files) {
    const canonical = relative(workspace, scopedPath(workspace, path));
    if (canonical.startsWith('scripts/') || canonical.startsWith('tasks/') || canonical === 'docs/development-workflow.md' || canonical === 'config/development-runtime.json') throw new Error('DSH cannot change its supervisor or task controls');
  }
  if (!Array.isArray(task.acceptance) || !task.acceptance.length || task.acceptance.some(x => typeof x !== 'string')) throw new Error('Missing acceptance criteria');
  const budget = { ...DEFAULT_BUDGET, ...task.budget };
  for (const key of Object.keys(DEFAULT_BUDGET)) {
    if (!Number.isInteger(budget[key]) || budget[key] <= 0 || budget[key] > 1800000) throw new Error(`Invalid budget ${key}`);
  }
  return { ...task, budget };
}

export function fileSnapshot(task, workspace) {
  const snapshot = {};
  for (const path of task.write_files) {
    const resolved = scopedPath(workspace, path);
    const stat = lstatSafe(resolved);
    if (!stat) { snapshot[path] = null; continue; }
    if (!stat.isFile() || stat.size > 1024 * 1024) throw new Error(`Invalid or oversized output file: ${path}`);
    snapshot[path] = createHash('sha256').update(readFileSync(resolved)).digest('hex');
  }
  return snapshot;
}

export function buildPrompt(task, workspace) {
  return [
    '你是 DSH 实现者，Codex 负责指挥、独立审查和验收。',
    `任务 ${task.id}；工作区 ${workspace}。只完成本次单一目标：${task.objective}`,
    `必要背景：${task.context}`,
    `允许读取：${[...new Set([...task.read_files, ...task.write_files])].map(p => resolve(workspace, p)).join(', ')}`,
    `仅允许写入：${task.write_files.map(p => resolve(workspace, p)).join(', ')}`,
    `具体要求：${task.instructions}`,
    `验收条件：${task.acceptance.join('；')}`,
    '只使用明确路径的 read_file、write_file、edit_file 工具；不要使用 todo_write、规划工具、glob、目录扫描、shell、联网或安装工具。',
    '测试命令由 Codex 审查后执行。不要修改监督脚本、任务卡、权限配置或敏感路径。',
    '先交付最小可审查文件，不输出长篇计划；若阻塞，报告具体原因，不无限换工具尝试。',
    '最后简短列出实际文件、接口与限制。完成回复不代表验收通过。',
  ].join('\n');
}

export function permissionScope(request, task, workspace) {
  const policy = request.policy;
  if (request.evidenceChanged) return { allowed: false, reason: 'Permission evidence changed' };
  if (policy?.decision !== 'allow') return { allowed: false, reason: policy?.reason || 'Bridge policy did not allow' };
  if (!request.toolCall?.rawInput || !Array.isArray(policy.paths) || !policy.paths.length) return { allowed: false, reason: 'Missing structured file evidence' };
  if (!['read', 'write', 'create', 'edit'].includes(policy.operation)) return { allowed: false, reason: 'Only explicit file operations are supervised' };
  const allowedFiles = policy.operation === 'read' ? [...task.read_files, ...task.write_files] : task.write_files;
  const allowed = new Set(allowedFiles.map(path => scopedPath(workspace, path)));
  for (const path of policy.paths) {
    if (!path.withinWorkspace || path.sensitive) return { allowed: false, reason: 'Unsafe resolved file scope' };
    let resolved;
    try { resolved = scopedPath(workspace, path.resolved || path.input); }
    catch (error) { return { allowed: false, reason: error.message }; }
    if (!allowed.has(resolved)) return { allowed: false, reason: `File not in task scope: ${resolved}` };
  }
  return { allowed: true };
}

export function deliverySummary(task, before, after) {
  const changedFiles = task.write_files.filter(path => after[path] !== null && after[path] !== before[path]);
  const missingFiles = task.expected_files.filter(path => !after[path]);
  return { changedFiles, missingFiles, delivered: changedFiles.length > 0 && missingFiles.length === 0 };
}

// Injected call/clock/IO make supervision independently testable without DSH or model calls.
export async function supervise({ task, workspace, connectionId, taskId, call,
  snapshotFiles = () => fileSnapshot(task, workspace),
  now = Date.now, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  record = () => {}, notify = () => {}, saveResponse = () => {}, signal = () => false,
}) {
  const start = now();
  const baseline = snapshotFiles();
  let files = baseline, activityAt = start, writeAt = start, seenActivity = false, seenWrite = false;
  let tools = 0, previousStatus = null, backoff = 0, polls = 0, reviews = 0;
  const intervals = [2000, 5000, 10000, 15000];
  const reviewed = new Set();
  const finish = (outcome, extra = {}) => {
    const summary = { taskId: task.id, bridgeTaskId: taskId, outcome,
      elapsedMs: now() - start, polls, reviews,
      ...deliverySummary(task, baseline, files), acceptance: 'not_run', ...extra };
    record({ event: 'finished', ...summary });
    return summary;
  };
  const stop = async (reason, extra = {}) => {
    record({ event: 'stopping', reason, ...extra });
    try {
      const cancelled = await call('cancel_task', { connectionId, taskId });
      if (cancelled.task?.status !== 'cancelled' && cancelled.status !== 'cancelled') throw new Error('Cancellation not confirmed');
      record({ event: 'cancelled', reason });
    } catch (error) {
      record({ event: 'cancellation_failed', message: error.message });
      try { await call('close_connection', { connectionId }); } catch (closeError) { record({ event: 'close_failed', message: closeError.message }); }
      return finish('cancellation_failed', { reason, error: error.message, ...extra });
    }
    return finish(reason, extra);
  };
  try {
    while (true) {
      if (signal()) return await stop('interrupted');
      const elapsed = now() - start;
      if (elapsed >= task.budget.total_ms) return await stop('deadline');
      if (!seenActivity && elapsed >= task.budget.first_activity_ms) return await stop('no_initial_activity');
      if (seenActivity && now() - activityAt >= task.budget.idle_ms) return await stop('stalled');
      if (!seenWrite && elapsed >= task.budget.first_write_ms) return await stop('no_initial_file');
      if (seenWrite && now() - writeAt >= task.budget.write_idle_ms) return await stop('no_file_progress');

      const snapshot = await call('read_response', { connectionId, mode: 'poll', sinceLastRead: true, includeToolDetails: false });
      polls++;
      if (snapshot.task?.taskId !== taskId) return await stop('task_mismatch', { observedTask: snapshot.task?.taskId });
      let changed = false;
      if (snapshot.response) { saveResponse(snapshot.response); seenActivity = true; activityAt = now(); changed = true; }
      if ((snapshot.toolCallCount || 0) !== tools) { tools = snapshot.toolCallCount || 0; seenActivity = true; activityAt = now(); changed = true; }
      const currentFiles = snapshotFiles();
      if (JSON.stringify(currentFiles) !== JSON.stringify(files)) {
        files = currentFiles; seenWrite = true; writeAt = now(); activityAt = now(); seenActivity = true; changed = true;
        const event = { event: 'files_changed', files: task.write_files.filter(p => currentFiles[p] !== baseline[p]) };
        record(event); notify(event);
      }
      if (snapshot.status !== previousStatus) {
        previousStatus = snapshot.status;
        changed = true;
        const event = { event: 'status', status: snapshot.status, tools };
        record(event); notify(event);
      }

      const pending = snapshot.pendingPermissions || [];
      if (FINAL_STATUSES.has(snapshot.status) && pending.length) return await stop('inconsistent_terminal_state');
      for (const request of pending) {
        record({ event: 'permission_evidence', request });
        const scope = permissionScope(request, task, workspace);
        if (!scope.allowed) return await stop('needs_review', { reason: scope.reason, requestId: request.requestId, policy: request.policy });
        if (request.taskId !== taskId || request.sessionId !== snapshot.sessionId || reviewed.has(request.requestId)) return await stop('permission_mismatch');
        const review = await call('approve_tool_call', { connectionId, requestId: request.requestId, action: 'review' });
        record({ event: 'permission_result', requestId: request.requestId, review });
        reviews++;
        if (review.outcome !== 'allowed_once') return await stop('needs_review', { reason: review.policy?.reason || review.outcome, requestId: request.requestId });
        reviewed.add(request.requestId);
        notify({ event: 'reviewed', operation: request.policy.operation, files: request.policy.paths.map(p => relative(workspace, p.resolved || p.input)) });
        seenActivity = true; activityAt = now(); changed = true;
      }
      if (FINAL_STATUSES.has(snapshot.status)) {
        record({ event: 'terminal', status: snapshot.status, task: snapshot.task, error: snapshot.currentError });
        files = snapshotFiles();
        if (snapshot.status === 'complete' || snapshot.status === 'completed') {
          return finish(deliverySummary(task, baseline, files).delivered ? 'ready_for_review' : 'no_delivery');
        }
        return finish(snapshot.status, { error: snapshot.currentError || snapshot.task?.error });
      }
      backoff = changed ? 0 : Math.min(backoff + 1, intervals.length - 1);
      const remaining = [task.budget.total_ms - (now() - start),
        (seenActivity ? activityAt + task.budget.idle_ms : start + task.budget.first_activity_ms) - now(),
        (seenWrite ? writeAt + task.budget.write_idle_ms : start + task.budget.first_write_ms) - now()];
      await sleep(Math.max(1, Math.min(intervals[backoff], ...remaining)));
    }
  } catch (error) {
    return await stop('supervisor_error', { error: error.message });
  }
}
