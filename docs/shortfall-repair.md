# 项目短板修补：2026-10-25

本轮对应组会准备时记录的 T09–T15。先补现有 ODE Agent 的任务管理、参数查证和验证反馈链路；现有单 Agent 架构与科学执行判据继续使用。下面区分产品功能、离线工程验证与尚未完成的真实效果评价。

后续第二轮已新增[冻结参数条件与提交前重检](parameter-applicability.md)，下表保留首轮交付范围；最新状态见优化队列与对应专题文档。

| 原问题 | 本轮实现 | 仍需补齐 |
|---|---|---|
| T09 经验缺少候选审核入口 | 从实际任务记录生成候选，保存适用条件；审核与启用分开，批准后才写入原有范围化记忆；引用审核记录，支持原有更正/撤回与快照失效 | 仍需操作者发起候选与审核；不是自主学习；长期任务、规模性能、Skill 扩展与收益消融未完成 |
| T10 不确定参数缺少查证链路 | `investigate_parameter` 在当前任务授权材料中检索，复用来源冲突检查，保存目标、查询、原文位置与问题；草稿参数台账记录值、声明单位、原始来源类别及引用 | 不自动采用参数；没有新增通用联网检索、完整条件/单位适用性判断、可辨识性或通用估参。已有两库搜索仍只提供线索 |
| T11 长任务难以说明进度和缺口 | `inspect_progress`、`agent-progress`、检查点及最终报告，按实际事件/产物派生步骤、依赖、修订次数、问题、剩余调用和下一步 | 是固定 ODE/冻结 study 路线的进度投影；尚无任意任务分解、可编辑计划或计划调整策略比较 |
| T12 错误缺少统一处理说明 | 分类引用、绑定、资料缺失、来源冲突、单位、模型、数值、服务与未知错误，给出修复/检索追问/澄清/诊断等路径 | 处理建议沿用既有工具循环、预算和重试策略；不是自动诊断所有异常，也不新增未知结果重试 |
| T13 验证失败不便针对性修正 | 草稿结果增加 `validation_feedback`：目标、来源位置、原始错误、是否阻塞、处理路径及建议；返给模型并持久保存 | 仍需真实模型评价修复率、误阻断、引入错误与调用成本；不以反馈字段代替科学审核 |
| T14 长上下文容易遗漏当前状态 | 压缩时保留最近一次进度和参数查证结果，保留原有最新草稿/执行结果/署名回复规则；按需读取进度与参数来源 | 仍受上下文硬预算约束；未做真实长任务或消融，不宣称 token/耗时或完成率改善 |
| T15 ODE 之外建模 | 本轮没有新增模型类型 | 随机模型、PDE、FBA、布尔网络或细胞个体模型，需要选定问题、专业求解路径、参考案例和类型专属验收 |

## 参数查证与任务进度

Agent 新动作：

```json
{"action":"investigate_parameter","args":{"target":"k/value","query":"Parameter k"}}
{"action":"inspect_progress","args":{}}
```

查证也支持 `<state>/initial`。每次仍消耗一次原有模型动作调用，并经过调用预留/返回记录。只读取当前任务 `source_refs`，不扫描其他项目、模型答案或宿主目录。查询会先通过已有来源数量、页数和字符预算检查，然后复用保守的来源检查器。没有匹配或未检测到冲突都不代表参数已经确认。

查证结果保存为 `parameter_investigation`，始终 `selected_value=null`、`semantic_support=unknown`。检索命中包含原文与位置，条件和单位适用性仍未验证；不能把资料中出现的值直接认定适合当前问题。冻结研究任务不开放此动作，继续使用已冻结候选和参数边界。

最终 Agent 记录现在直接保存 `draft_ref` 和派生 `progress`。参数台账只读取草稿与真正的 claim 引用；`declared_origin` 是草稿声明，`claim_type` 是原证据分类，都不自动升级为确认事实。旧记录可通过原检查点恢复草稿引用。

```bash
.venv/bin/python -m biomodel_agent agent-progress AGENT_RUN_REF --store artifacts/my-task
.venv/bin/python -m biomodel_agent report AGENT_RUN_REF --store artifacts/my-task
```

`inspect_progress` 不允许模型传入“完成”状态。步骤是否完成来自已保存的工具结果；科学支持仍显示 `not_verified`。最新草稿通过后，旧提案的问题仍保留在历史事件中，但不继续列为当前未解决项。新查证操作已写入返回收据时，可以沿用原有恢复机制认领；只有写入预留而没有确定结果时，仍按未知部分写入阻断，不重新查证。

## 经验候选审核

```bash
.venv/bin/python -m biomodel_agent memory-candidate-propose AGENT_RUN_REF \
  --store artifacts/my-task --project study-a --author Researcher \
  --applicability '适用于需要确认初值的确定性衰减模型任务'

.venv/bin/python -m biomodel_agent memory-candidate-inspect CANDIDATE_REF --store artifacts/my-task

.venv/bin/python -m biomodel_agent memory-candidate-review CANDIDATE_REF \
  --store artifacts/my-task --decision approved --reviewer Researcher \
  --reason '已核对任务记录、处理路径与适用范围'

.venv/bin/python -m biomodel_agent memory-candidate-activate REVIEW_REF --store artifacts/my-task
```

候选内容由工具记录派生，包含实际任务状态、问题类别和处理建议；不提炼新的生物事实。候选没有进入 `research_memory`，批准但未启用也不进入召回。拒绝后不可启用；同一审核不可重复启用，也不能通过重复启用复活已撤回经验。适用条件作为署名建议保留，不是程序已经证明条件成立。操作者身份是声明，未新增身份认证。

启用后沿用项目、模型、来源白名单及快照限制。带模型范围的经验，需要检索任务显式传入相应 `memory_model_ref`。经验始终是建议，不能作为参数或方程的科学引用。更新与撤回继续使用原 `memory-add --supersedes` 与 `memory-retract`。

## 验证与剩余工作

验收日志、初次失败、修正后的回归、可重跑演示及真实数值产物位于 [本轮记录](../artifacts/shortfall-repair-20261025/README.md)。离线脚本测试动作和工程行为，求解器实际运行；没有新的线上模型成功率或独立领域审核结论。

原始需求中的“尽量全部补齐”仍有这些未完成项：真实模型对照/消融与独立领域审核，通用参数条件核验及估计，任意长任务计划修订策略，更多科研 Skill，成熟 OCR/公式识别与新的建模类型。这些需要相应数据、案例与效果验收，不能凭本轮接口接通标为完成。
