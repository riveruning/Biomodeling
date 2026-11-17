#!/usr/bin/env node
// Construct one date-bound DSH release run. All Git work belongs to the reviewed helper.
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const workspace = '/home/riveruning/biologymodel_agent';
const helper = `${workspace}/scripts/nightly_release.py`;
const manifest = JSON.parse(readFileSync('/home/riveruning/tools/mcp-acp-bridge/runtime/biomodel-nightly-release-approved.json', 'utf8'));
if (manifest.repository !== 'riveruning/Biomodeling' || manifest.script_path !== helper
    || manifest.sha256 !== createHash('sha256').update(readFileSync(helper)).digest('hex')) {
  throw new Error('Nightly helper requires an updated independent review');
}
if (process.argv.length !== 2) throw new Error('This nightly launcher takes no arguments');
const date = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date());
const id = `dsh-nightly-release-${date.replaceAll('-', '')}`;
const until = new Date(Date.now() + 170 * 60000).toISOString();
const state = `artifacts/runs/nightly-release/${date}/publish.json`;
const task = {
  schema_version: '1', id,
  objective: '整理、验证并将当前工作分支最新版提交推送用户自己的GitHub仓库riveruning/Biomodeling。',
  context: '用户明确授权每日北京时间00:00执行。夜间发布目的授权独立于旧48小时科研窗口。由DSH具体执行，Codex审计。只用DeepSeek或GLM并请求最高支持推理。',
  read_files: [], write_files: [state], expected_files: [state],
  delegation: { purpose: 'nightly_release', workspace, until },
  instructions: [
    `发布日期${date}。第一操作执行inspect，然后依次verify和publish。三个精确单命令为 .venv/bin/python scripts/nightly_release.py inspect --date ${date}、.venv/bin/python scripts/nightly_release.py verify --date ${date}、.venv/bin/python scripts/nightly_release.py publish --date ${date}。`,
    'verify执行完整pytest通常约17分钟，bash工具必须显式设置timeoutMs=3600000并前台等待结束。inspect和publish设置timeoutMs=300000。工具参数只给command、workdir、description和timeoutMs。',
    '不要使用shell组合、重定向、临时脚本或其它命令。只通过已审计助手操作Git，保护无关暂存和工作区，凭证、冻结评测和原始数据不发布。',
    '检查输出的真实身份、版本选择、验证日志/exit和SHA，只有远端实际匹配才能报告推送成功。',
    '验证失败如实保留，按助手允许的规则保存带真实失败标签的工作分支版本。版本漂移先重新verify。网络故障可有限重试publish，同日已有成功提交不要重复创建。',
    '身份/内容链不符时报告实际阻塞。不要改权限、控制、helper、远端或强制推送。最后简短列出实际branch、commit、远端SHA及验证结果。',
  ].join('\n'),
  acceptance: ['真实远端SHA核验或准确可恢复失败证据', '版本验证结果可追溯', '同日重试幂等'],
  verification_commands: [],
  budget: { first_activity_ms: 1800000, idle_ms: 7200000, first_write_ms: 10000000,
    write_idle_ms: 10000000, total_ms: 10000000 },
};
mkdirSync(`${workspace}/tasks`, { recursive: true });
const taskPath = `tasks/${id}.json`;
writeFileSync(`${workspace}/${taskPath}`, JSON.stringify(task, null, 2) + '\n');
const env = { ...process.env, NIGHTLY_REPOSITORY: 'riveruning/Biomodeling',
  NIGHTLY_DATE: date, NIGHTLY_UNTIL: until };
delete env.BIOMODEL_DSH_DELEGATION_UNTIL;
const child = spawnSync(process.execPath, ['scripts/dsh-workflow.mjs', '--task', taskPath,
  '--stream-idle-ms', '600000'], { cwd: workspace, env, stdio: 'inherit', timeout: 10100000 });
if (child.error) throw child.error;
process.exitCode = child.status ?? 1;
