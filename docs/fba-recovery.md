# R09：FBA Agent 可恢复协议

2026-10-27。用户指定优先推进 R09，R02 后续暂缓。本轮补齐有界稳态 FBA 主流程的暂停、回复、检查点和中断恢复，复用原调用账本、执行所有权、上下文选择和恢复审计。LP 求解器、数值检查和 FBC 导入器保持原实现。

## 入口与兼容

新任务显式指定 `recoverable=True`，使用 `fba_agent_v2`。默认入口保留旧行为，旧任务缺少完整状态时不会被回签为可恢复记录。旧恢复诊断仍明确拒绝，未知计数仍为 null。

```python
agent.run_model_agent(store, provider, model_type='fba', model_ref=model_ref,
                      settings=settings, recoverable=True, max_calls=6,
                      context_max_chars=60000)
```

```bash
# 服务配置按原有方式显式提供；本轮未启动线上服务。
.venv/bin/python -m biomodel_agent agent-fba MODEL_REF --store STORE \
  --config settings.json --recoverable --max-calls 6

# awaiting_input 运行的回复；不接受模型/LP配置修改
.venv/bin/python -m biomodel_agent agent-resume PAUSED_RUN_REF --store STORE \
  --reply-file reply.txt --respondent OPERATOR

# 中断后的只读诊断及明确恢复，使用 execution 引用
.venv/bin/python -m biomodel_agent agent-recovery-status EXECUTION_REF --store STORE
.venv/bin/python -m biomodel_agent agent-recover EXECUTION_REF --store STORE
.venv/bin/python -m biomodel_agent report RUN_REF --store STORE --output report.md
```

`--context-max-chars` 是序列化消息字符预算，范围 8000–500000，默认60000，自定义值要求启用新协议。字符预算不等于 provider token、HTTP 请求大小或整项研究的总费用。Provider 参数的恢复遵守原 endpoint/class/model/generation identity 检查，不保存凭证。

## 冻结状态

```mermaid
flowchart LR
    M[原生/FBC模型与固定设置] --> T[FBA v2任务与环境快照]
    T --> Q[ask_researcher 暂停]
    Q --> A[有归属的执行回复]
    A --> L[run_fba 一次LP执行]
    L --> R[原始结果与返回收据]
    R --> F[finish / 报告]
    R --> C[中断后核对并认领原返回]
    C --> F
```

动作仅有 `inspect_fba`、`run_fba`、`finish` 和新协议的 `ask_researcher`。前三者空参数；追问只接受不超过1000字符的 `question`。不能编辑模型、通量边界、目标、方法、参考值、时限或预算，不能切到 ODE/研究拟合工具。

检查点保存完整可见消息、事件、当前结果、调用/科学执行计数、clarification 和 recovery 引用。模型/配置/来源 blob、当前源码和依赖环境保持冻结。回复不超过8000字符，保存操作者归属与未验证状态，不创建科学来源或模型更正。要改 LP 须显式准备新的模型/任务。

每条接续分支继承已有调用与一次科学执行计数；LP执行后暂停再回复不能再次求解。模型/配置、provider或环境不符、预算耗尽、检查点不匹配以及必要上下文超限时，在写回复和调用模型之前阻断。

与已有 ODE 接续语义一致，操作者再次使用较早暂停记录会显式创建另一分支；这不是跨分支/跨store全局配额或“任意旧记录只可消费一次”的保证。`fba-replay` 仍是新计算，不能冒充旧 Agent 恢复。当前没有整个研究或跨机器的硬总期限。

## 中断恢复

共享审计器按新协议识别 `fba_agent_run` 和 FBA 初态，验证任务、来源模型、计数、预留/终态及检查点；不把 FBA 模型当 ODE 来源。读取原始对象文件，不修复 SQLite 索引。真正接续前仍需取得同一任务的本地文件所有权。

可认领的科学返回必须同时匹配任务模型、设置、环境和 `run_fba` 预留，且经过原 `fba.summary` 对源模型、输入/返回收据、原对偶数值与参考比较的重检。已确认的 provider 响应也直接回放，不重复发请求。

只有未返回的只读 `inspect_fba` 可以重做。未知 provider/科学效果、错误或中断终态、冲突收据、错误模型/配置、旧版本状态均阻断。即使目录已有一个底层 LP 结果，缺少确认的 Agent 返回也不能静默认领或重算。恢复中再次中断仍保留同一执行和原预算。

暂停恢复与故障恢复分开：前者使用 awaiting_input 的运行引用，后者使用未完成执行引用；已有完整运行的执行报告 finished，不重新运行。

## 验证与报告

原解析小网络和已有公开教材 FBC 模型均保留。教材流程演练包含暂停、LP已返回后中断、原预算恢复、完成及导出；底层只有一次 LP 求解，目标与已有独立标量参考一致。Provider为固定脚本，不是实际大模型成绩；科学支持保持 unknown，目标一致不证明唯一通量或生物学正确。

报告显示任务/检查点/恢复收据、累计调用与剩余预算、有归属的执行回复，以及原 LP 的结果/参考/数值检查。回复署名不是身份认证或独立领域审核。示例见[教材恢复报告](../artifacts/r09-fba-recovery-20261027/textbook-recovery-report.md)，验收见[本轮记录](../artifacts/r09-fba-recovery-20261027/acceptance.json)。

R09当前工程范围为有界稳态 FBA：导入、显式配置、求解/数值检查、类型路由、可选暂停/恢复、报告和打包。动态FBA、FVA/MILP、基因操作、任意论文方法推断、真实模型效果及独立领域审核继续不在已交付范围；更广模型类型属于后续 T15 扩展。
