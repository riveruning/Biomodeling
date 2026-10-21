# 开发协作工作流验收记录

本记录评价开发协作工具，不评价 BioModel Agent 科学能力。

## 离线验证

命令：`node scripts/tests/dsh-supervisor.test.mjs`。

结果：15 项通过，0 项失败。覆盖低频退避、仅返回变化、限定文件的一次性审查、未知策略停止、越界修改、陈旧会话与重复审批、最终审批策略变化、无活动、无文件进展、无产物完成、取消失败、任务切换、路径逃逸，以及不自动执行测试命令。

任务卡预览通过：`node scripts/dsh-workflow.mjs --task tasks/workflow-smoke.json --dry-run`。两个监督脚本的 Node 语法检查通过。

## 真实协作验证

任务：仅创建 `artifacts/development/dsh-smoke.txt`，内容为一行 `DSH_WORKFLOW_READY`。

第一次启动在 Codex 沙箱中连接关闭。独立子进程探测确认 Node spawn 返回 EPERM。获准从宿主环境运行后，MCP 及 DSH 初始化成功，任务进入 generating。

最终结果：`no_initial_activity`；约 60 秒内查询 6 次，0 次权限审查，0 个文件改动。监督器确认取消后关闭连接，没有无限等待，也没有标记交付或验收通过。

本地证据目录：`.workflow/runs/workflow-smoke-1791983188231-a01542e2/`，包含任务、提示、审计和 summary。该目录不纳入版本库；任务配置保存在 `tasks/workflow-smoke.json`。

可以确认的结论：监督器能连接、低频查询、按预算停止和保存证据。无法确认的结论：DSH 能稳定响应并交付代码。现有结果不能确定无响应来自模型供应商延迟、网络、配置还是其他问题，不能归因于文件审批，因为本次未产生审批请求。

## 定位与修复

只读检查 DSH 既有会话记录，找到两次 `pi-ai stream idle timeout after 300000ms`，以及 maxRetries=5 的内部重试。外层状态只显示 generating，未把这些重试及时呈现为错误；这是长时间无可见产出的明确原因之一。

服务诊断：认证及模型列表查询 200；glm-5.3-flash 在列表中。其极短普通文本的 Chat Completions 和非流式 Responses 请求均在约 23–24 秒成功；本轮流式 Responses 和两种协议的强制工具请求在 45 秒预算内未完成。不能据此单独断言某个协议完全不支持或原模型永远不可用。

同一服务中，qwen3.8-chat 的 Chat Completions 自动工具请求约 1.3 秒返回正确 function_call。项目采用这一组合，并在临时运行覆盖层中限制超时与重试。原全局配置、凭证文件、原权限网关和 conda base 未修改。

## 修复后的真实验收

| 任务 | 结果与证据 |
|---|---|
| 一行文本交付 | 约16秒完成，1次写入经辅助审批；确实产生标记文件，但漏掉末尾LF，因此精确字节条件不通过 |
| 换行返修 | 约21秒，读写请求正常经过3次辅助审批，仍未补齐LF；保留失败，不改原验收条件 |
| 代码任务第一次 | DSH先读取不存在的文件，网关返回missing_target；监督器结束任务，没有绕过策略 |
| 代码任务准备后重试 | Codex仅创建空目标；DSH约36秒完成2次读取、2次写入，4次辅助审批均允许；实现文件和测试文件均有实际变更 |
| Codex代码验收 | 审查实现后执行 `.venv/bin/python -m pytest artifacts/development/test_dsh_grid.py -q`，8 passed；未由监督器自动执行 |
| 监督与运行覆盖层离线测试 | 15项监督测试、3项覆盖层测试通过 |

新增本地证据目录：

- `.workflow/runs/workflow-smoke-1791985518518-8ee12e2b/`
- `.workflow/runs/workflow-smoke-fix-1791985820415-5cbdc931/`
- `.workflow/runs/workflow-code-smoke-1791985904672-6e7cd4b7/`
- `.workflow/runs/workflow-code-smoke-1791986056589-323c69fc/`

代码任务的 Codex 验收另存于最后一个目录的 `acceptance.json`。协作测试产物位于 `artifacts/development/`，不作为业务实现或科学基准成果。

## 当前决策与限制

工作流工具：离线测试和真实读写、编码、独立测试链路通过。可以恢复项目业务切片开发。

限制：原模型/协议组合的上游迟滞没有在服务端修复；采用已验证的项目专用替代组合，不能保证其他模型或大任务同样稳定。字节精确换行案例仍不通过，表明指令执行质量需要实际验收，不能由正常结束状态推断。

业务实现：尚未完成，开发协作已经具备继续执行的条件。
