// Development supervision only; never executes task-provided verification commands.
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { nightlyTaskValid } from './dsh-nightly.mjs';

export const DEFAULT_BUDGET = Object.freeze({
  first_activity_ms: 120000, idle_ms: 180000,
  first_write_ms: 300000, write_idle_ms: 300000, total_ms: 600000,
});
const FINAL_STATUSES = new Set(['complete', 'completed', 'cancelled', 'error', 'process_exited']);
const FORBIDDEN = new Set(['.git', '.aws', '.codex', '.agents', '.venv', '.workflow', 'node_modules']);
const DELEGATION_UNTIL = '2026-11-18T15:07:27Z';
const DELEGATION_PREFIXES = ['src/', 'tests/', 'docs/', 'examples/', 'scripts/',
  'artifacts/development/', 'benchmarks/development/'];

function delegatedTask(task, workspace) {
  if (task.delegation?.purpose === 'nightly_release') return nightlyTaskValid(task, workspace);
  return task.delegation?.until === DELEGATION_UNTIL
    && task.delegation.workspace === workspace
    && process.env.BIOMODEL_DSH_DELEGATION_UNTIL === DELEGATION_UNTIL
    && Date.now() < Date.parse(DELEGATION_UNTIL);
}

function protectedControl(path) {
  return path.startsWith('artifacts/development/dsh-delegation-48h/')
    || path === 'scripts/nightly_release.py'
    || /^scripts\/(?:dsh|lib\/dsh|tests\/dsh)/.test(path)
    || path === 'docs/development-workflow.md' || path === 'config/development-runtime.json';
}

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
  if (task.delegation && !delegatedTask(task, workspace)) throw new Error('Missing or expired delegated execution grant');
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
  const deliveryMode = task.delivery_mode === undefined ? 'files' : task.delivery_mode;
  if (!['files', 'proposal'].includes(deliveryMode)) throw new Error('Unknown delivery mode');
  if (deliveryMode === 'proposal') {
    if (task.write_files.length || task.expected_files.length) throw new Error('Proposal tasks have no writable or expected files');
  } else {
    if (!task.write_files.length || task.write_files.length > 6) throw new Error('Implementation task needs 1–6 explicit writable files');
    if (!task.expected_files.length) throw new Error('Implementation task needs expected files');
  }
  if (task.expected_files.some(path => !task.write_files.includes(path))) throw new Error('Expected files must be writable');
  for (const path of task.write_files) {
    const canonical = relative(workspace, scopedPath(workspace, path));
    if (canonical.startsWith('scripts/') || canonical.startsWith('tasks/') || canonical === 'docs/development-workflow.md' || canonical === 'config/development-runtime.json') throw new Error('DSH cannot change its supervisor or task controls');
  }
  if (!Array.isArray(task.acceptance) || !task.acceptance.length || task.acceptance.some(x => typeof x !== 'string')) throw new Error('Missing acceptance criteria');
  const budget = { ...DEFAULT_BUDGET, ...task.budget };
  for (const key of Object.keys(DEFAULT_BUDGET)) {
    // Leave one minute for the bridge watchdog within Node's timer range.
    if (!Number.isInteger(budget[key]) || budget[key] <= 0 || budget[key] > 2147423647) throw new Error(`Invalid budget ${key}`);
  }
  return { ...task, delivery_mode: deliveryMode, budget };
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
  const nightly = task.delegation?.purpose === 'nightly_release';
  return [
    '你是 DSH 实现者，Codex 负责指挥、独立审查和验收。',
    '用户要求DSH及全部新增推理请求只用DeepSeek或GLM，并请求实际支持的最高推理档。复用历史证据保留原模型标记，新增调用遵循当前模型要求。',
    `任务 ${task.id}；工作区 ${workspace}。只完成本次单一目标：${task.objective}`,
    `必要背景：${task.context}`,
    `允许读取：${[...new Set([...task.read_files, ...task.write_files])].map(p => resolve(workspace, p)).join(', ')}`,
    `${delegatedTask(task, workspace) ? '监督器监测文件' : '仅允许写入'}：${task.write_files.map(p => resolve(workspace, p)).join(', ')}`,
    `具体要求：${task.instructions}`,
    `验收条件：${task.acceptance.join('；')}`,
    nightly
      ? `用户已授权每日午夜整理、验证并提交推送riveruning/Biomodeling当前工作分支。本轮目的授权截止${task.delegation.until}。只可执行已审计scripts/nightly_release.py的inspect、verify、publish模式，日期必须为${process.env.NIGHTLY_DATE}。可读取任务明确文件与当日发布审计输出。`
      : delegatedTask(task, workspace)
      ? `用户48小时全权委派有效至${DELEGATION_UNTIL}。可读写任务相关路径前缀${DELEGATION_PREFIXES.join(', ')}，以及明确列出的只读文件。控制面、凭证、冻结评测与原始证据受保护。可通过bash运行授权Python/pytest/SDK/Node单命令，所有操作保留门控。write_files仅为监督器监测文件。`
      : task.delivery_mode === 'proposal'
      ? '必要代码片段已完整提供，只返回代码提案；本任务没有文件工具操作。'
      : '只使用明确路径的 read_file、write_file、edit_file 工具；不要使用 todo_write、规划工具、glob、目录扫描、shell、联网或安装工具。',
    nightly
      ? '由DSH按inspect→verify→publish执行，保留真实检查结果并核对远端SHA。只使用已审计助手，执行失败报告实际诊断。Codex审计。'
      : delegatedTask(task, workspace)
      ? '由DSH执行实现、测试、文献SDK和模型调用。Codex逐轮结束审计及每小时兜底。不要修改监督控制、任务卡、授权或凭证，不发布推送。'
      : '测试命令由 Codex 审查后执行。不要修改监督脚本、任务卡、权限配置或敏感路径。',
    task.delivery_mode === 'proposal'
      ? '仅返回可审查的实际实现代码，由Codex审查后应用。全部必要材料已在提示中；文件工具不在本任务范围。'
      : '先交付最小可审查文件，不输出长篇计划；若阻塞，报告具体原因，不无限换工具尝试。',
    task.delivery_mode === 'proposal'
      ? '代码提案由Codex审查、应用和测试。完成回复不代表验收通过。'
      : '最后简短列出实际文件、接口与限制。完成回复不代表验收通过。',
  ].join('\n');
}

export function permissionScope(request, task, workspace) {
  const policy = request.policy;
  if (request.evidenceChanged) return { allowed: false, reason: 'Permission evidence changed' };
  if (policy?.decision !== 'allow') return { allowed: false, reason: policy?.reason || 'Bridge policy did not allow' };
  if (task.delegation?.purpose === 'nightly_release') {
    if (!nightlyTaskValid(task, workspace)) return { allowed: false, reason: 'Nightly purpose grant expired or missing' };
    if (policy.operation === 'execute' && policy.rule === 'biomodel_nightly_release'
        && policy.cwd === workspace) return { allowed: true };
    const allowedReads = new Set(task.read_files.map(file => scopedPath(workspace, file)));
    const evidenceDirectory = `artifacts/runs/nightly-release/${process.env.NIGHTLY_DATE}/`;
    return { allowed: policy.operation === 'read' && Array.isArray(policy.paths) && policy.paths.length > 0
      && policy.paths.every(file => {
        if (!file.withinWorkspace || file.sensitive) return false;
        try {
          const target = scopedPath(workspace, file.resolved || file.input);
          return allowedReads.has(target) || relative(workspace, target).startsWith(evidenceDirectory);
        } catch { return false; }
      }) };
  }
  if (task.delegation) {
    if (!delegatedTask(task, workspace)) return { allowed: false, reason: 'Delegation grant expired or missing' };
    const cwdAllowed = typeof policy.cwd === 'string'
      && (policy.cwd === workspace || policy.cwd.startsWith(workspace + '/'));
    if (policy.operation === 'pwd') return { allowed: cwdAllowed };
    if (policy.operation === 'execute_read' && policy.rule === 'biomodel_workspace_inventory') return { allowed: cwdAllowed };
    if (policy.operation === 'execute' && policy.rule === 'biomodel_delegated_execution') {
      if (!cwdAllowed) return { allowed: false, reason: 'Execution outside delegated workspace' };
      for (const item of policy.paths || []) {
        if (!item.withinWorkspace || item.sensitive || protectedControl(relative(workspace, item.resolved))) {
          return { allowed: false, reason: 'Execution targets protected control path' };
        }
      }
      return { allowed: true };
    }
    if (!request.toolCall?.rawInput || !['read', 'write', 'create', 'edit', 'execute_read'].includes(policy.operation)
        || !Array.isArray(policy.paths) || !policy.paths.length) return { allowed: false, reason: 'Unverified delegated operation' };
    for (const item of policy.paths) {
      if (!item.withinWorkspace || item.sensitive) return { allowed: false, reason: 'Unsafe delegated path' };
      if (policy.operation === 'execute_read' && policy.rule === 'biomodel_directory_listing'
          && item.resolved === workspace) continue;
      let path;
      try { path = relative(workspace, scopedPath(workspace, item.resolved || item.input)); }
      catch (error) { return { allowed: false, reason: error.message }; }
      const explicitRead = ['read', 'execute_read'].includes(policy.operation) && task.read_files.includes(path);
      if (protectedControl(path) || (!explicitRead && !DELEGATION_PREFIXES.some(prefix => path === prefix.slice(0, -1) || path.startsWith(prefix)))) {
        return { allowed: false, reason: `Path outside delegated task scope: ${path}` };
      }
    }
    return { allowed: true };
  }
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

export function deliverySummary(task, before, after, responseChars = 0) {
  if (task.delivery_mode === 'proposal') {
    return { changedFiles: [], missingFiles: [], delivered: responseChars > 0, deliveryMode: 'proposal' };
  }
  const changedFiles = task.write_files.filter(path => after[path] !== null && after[path] !== before[path]);
  const missingFiles = task.expected_files.filter(path => !after[path]);
  if (task.delegation?.purpose === 'nightly_release') {
    return { changedFiles, missingFiles, delivered: missingFiles.length === 0 && responseChars > 0 };
  }
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
  let tools = 0, previousStatus = null, backoff = 0, polls = 0, reviews = 0, responseChars = 0;
  const proposal = task.delivery_mode === 'proposal';
  const intervals = [2000, 5000, 10000, 15000];
  const reviewed = new Set();
  const finish = (outcome, extra = {}) => {
    const summary = { taskId: task.id, bridgeTaskId: taskId, outcome,
      elapsedMs: now() - start, polls, reviews,
      ...deliverySummary(task, baseline, files, responseChars), acceptance: 'not_run', ...extra };
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
      if (task.delegation && now() >= Date.parse(task.delegation.until)) return await stop('delegation_expired');
      if (signal()) return await stop('interrupted');
      const elapsed = now() - start;
      if (elapsed >= task.budget.total_ms) return await stop('deadline');
      if (!seenActivity && elapsed >= task.budget.first_activity_ms) return await stop('no_initial_activity');
      if (seenActivity && now() - activityAt >= task.budget.idle_ms) return await stop('stalled');
      if (!proposal && !seenWrite && elapsed >= task.budget.first_write_ms) return await stop('no_initial_file');
      if (!proposal && seenWrite && now() - writeAt >= task.budget.write_idle_ms) return await stop('no_file_progress');

      const snapshot = await call('read_response', { connectionId, mode: 'poll', sinceLastRead: true, includeToolDetails: false });
      polls++;
      if (snapshot.task?.taskId !== taskId) return await stop('task_mismatch', { observedTask: snapshot.task?.taskId });
      let changed = false;
      if (snapshot.response) { saveResponse(snapshot.response); responseChars += snapshot.response.length;
        seenActivity = true; activityAt = now(); changed = true; }
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
        if (!scope.allowed) {
          if (!task.delegation) return await stop('needs_review', { reason: scope.reason, requestId: request.requestId, policy: request.policy });
          const denial = await call('approve_tool_call', { connectionId, requestId: request.requestId, action: 'deny' });
          record({ event: 'delegated_operation_denied', reason: scope.reason, requestId: request.requestId, policy: request.policy, denial });
          seenActivity = true; activityAt = now(); changed = true;
          continue;
        }
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
          return finish(deliverySummary(task, baseline, files, responseChars).delivered ? 'ready_for_review' : 'no_delivery');
        }
        return finish(snapshot.status, { error: snapshot.currentError || snapshot.task?.error });
      }
      backoff = changed ? 0 : Math.min(backoff + 1, intervals.length - 1);
      const remaining = [task.budget.total_ms - (now() - start),
        (seenActivity ? activityAt + task.budget.idle_ms : start + task.budget.first_activity_ms) - now(),
        (proposal ? start + task.budget.total_ms : seenWrite ? writeAt + task.budget.write_idle_ms
          : start + task.budget.first_write_ms) - now()];
      await sleep(Math.max(1, Math.min(intervals[backoff], ...remaining)));
    }
  } catch (error) {
    return await stop('supervisor_error', { error: error.message });
  }
}
