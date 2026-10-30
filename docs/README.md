# 文档索引

当前进展先读 [2026-10-30 整体进度](project-status-20261030.md)，具体能力以[支持矩阵](current-support.md)和对应专题为准。本文按用途分组，原文件路径保持不变。

历史文档中的测试数量、优先级和“下一步”只对应记录时版本；不能视为当前完整回归结果或实际科研成绩。可共享验证记录见[本次版本验证](validation/20261030/README.md)。部分证据链接指向不随源码分发的本地历史归档，目录与版本管理状态见[归档说明](repository-layout.md)。

## 先读这些

- [Agent 研究项目、迭代与论文开发队列（2026-10-30）](agent-research-projects.md)
- [有限来源到随机反应模型（2026-10-30）](source-stochastic.md)
- [本地研究工作台（2026-10-30）](research-workbench.md)
- [统一科研任务入口与六类续接（2026-10-30）](research-tasks.md)
- [模型类型建议、输入草稿与来源模型（2026-10-30）](task-proposals.md)

- [整体项目进度与下一阶段](project-status-20261030.md)
- [上一阶段盘点（2026-10-28）](project-status-20261028.md)
- [阶段汇报 PPT 的建议与实现可行性（2026-10-29）](ppt-ideas-review-20261029.md)
- [显式建模方案 IR、执行收据与外部工作区（2026-10-29）](modeling-decisions.md)
- [原生 Agent 建模方案、预算和恢复（2026-10-29 接续）](native-modeling-decisions.md)
- [原生方案真实模型先导与诊断（2026-10-29）](native-plan-pilot.md)
- [当前支持范围与证据入口](current-support.md)
- [六类建模的当前接口](model-types.md)
- [目录与归档说明](repository-layout.md)
- [架构审计后的优化任务队列](optimization-backlog.md)

## 模型、估参与可复算案例

- [v0.1 实际架构与使用](v0.1.md)
- [R09：稳态 FBA 产品接入](fba.md)
- [R09：FBA Agent 可恢复协议](fba-recovery.md)
- [R02：独立参数估计任务架构](parameter-estimation.md)
- [R02：Agent 估参请求、冻结执行与探索候选](agent-estimation.md)
- [R02：探索候选的显式执行与报告](exploratory-execution.md)
- [Agent 的受约束拟合与候选比较工具](agent-study-tool.md)
- [R03：固定案例目录、显式装载与知识候选](case-library.md)
- [R10：论文图像参考与定量诊断](paper-reference.md)

## 来源、检索与证据

- [BioModels 与 SABIO-RK 基础接入](public-knowledge.md)
- [T06：成熟本地 PDF 解析器适配](pdf-parser-adapter.md)
- [R08-1：JATS 图片缺口与视觉候选的来源链](jats-image-sources.md)
- [项目记忆、材料检索与操作指引 v1](memory-and-retrieval.md)
- [检索开发基线与可选片段排序](passage-retrieval.md)
- [T07a：检索材料与标注审核准备](retrieval-review.md)
- [T05：结构化证据目标绑定](evidence-binding.md)
- [参数声明与任务条件核对](parameter-applicability.md)
- [原生参数记录到模型采用：R01](parameter-source-adoption.md)
- [数值声明边界与验证状态](literal-assignment-validation.md)
- [来源赋值识别与引用自修](source-and-citation-recovery.md)
- [多处引文诊断与离线恢复演练](citation-diagnostics.md)

## 计划、记忆、预算与恢复

- [可修订任务计划与依赖管理：R04](task-plans.md)
- [R05：有预算的失败处理](failure-handling.md)
- [R07：有工具依赖与产物检查的科研流程](scientific-skills.md)
- [R06：记忆条件、依赖失效与运行后经验提议](memory-architecture.md)
- [工作上下文预算与完整历史留存](context-budget.md)
- [调用记录与预算持久化（T03a）](invocation-journal.md)
- [研究者澄清与任务恢复](agent-resume.md)
- [受控故障恢复与结果认领（T03b）](controlled-recovery.md)
- [统一研究入口与科学计算总期限（T04）](study-execution-deadline.md)

## 数值契约与结果判定

- [数据契约与执行协议](contracts.md)
- [Python 与 JSON 数值边界一致性（T02）](strict-numeric-contracts.md)
- [明确失败优先的运行裁决（T01）](run-verdict.md)
- [显式实验时间单位约束](experiment-time-units.md)
- [结果与参考轨迹时间单位](reference-time-units.md)
- [数值轨迹输入完整性（2026-10-18）](numeric-input-integrity.md)
- [参考结果输入验证（2026-10-18）](reference-input-validation.md)
- [固定评分尺度预检（2026-10-18）](fixed-scale-preflight.md)
- [缩放误差的中间溢出（2026-10-18）](scaled-error-overflow.md)

## 评价工具、协议与研究准备

- [R11a：开发材料准入盘点、参考与审核准备](evaluation-readiness.md)
- [R11b-1：受信任控制进程采集与轨迹侧车](comparison-runtime.md)
- [T08a：统一 Agent 评价清单与离线评分](agent-evaluation-protocol.md)
- [T08b：三种工作流执行适配器](workflow-adapters.md)
- [T08c：共同 HTTP 传输与离线外发准备](comparison-transport.md)
- [T08d-1：原生系统对照的离线接口](system-comparison.md)
- [T08d-0：待审核裁决与版本化评价报告](evaluation-reporting.md)
- [统一测试样例与收集计划](evaluation-data-plan.md)

## 历史设计、讨论与验收

- [基础架构与关键决策](architecture.md)
- [v0.1 验收记录](acceptance-v0.1.md)
- [实现交接与验收](implementation-guide.md)
- [Codex 与 DSH 开发工作流 v0.1](development-workflow.md)
- [开发协作工作流验收记录](workflow-validation.md)
- [七个 Agent 方向的实现与缺口](seven-point-status.md)
- [历次讨论的问题、具体任务与初步方案](discussion-task-plan-20261025.md)
- [项目短板修补：2026-10-25](shortfall-repair.md)
- [阶段审计与论文研究计划](research-readiness-audit.md)
- [真实论文复现评测：首个开发案例](literature-pilot.md)
- [缺失信息处理与模型可靠性：开发评测](reliability-evaluation.md)
- [Agent 可靠性改进：调用恢复与来源冲突阻断](agent-reliability-improvements.md)
- [冻结版本的独立材料小测与通用 Agent 对照](holdout-pilot-evaluation.md)
- [已发表系统的实际接入与开发评测](published-competitor-integration.md)

- [Windows 原生运行与纯运行包](windows-runtime.md)
