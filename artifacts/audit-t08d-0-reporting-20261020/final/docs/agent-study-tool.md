# Agent 的受约束拟合与候选比较工具

本轮把已有的参数拟合与候选比较计算接入 Agent，而不是仅能从独立 Python/CLI 调用。首版采用明确、有限的 adaptation_study_v1 场景：反馈、前馈及基线三个候选模板，可选其中 1–3 个；固定观测为有符号偏差 response=x。模板是系统猜想，不是论文事实，也不代表开放式机制发现。

当前新计划已升级为 `adaptation_study_v2`，研究入口与总期限见 [T04说明](study-execution-deadline.md)；旧v1计划保留兼容。本页的首版验收记录仍作为历史证据，最新能力入口见 [当前支持矩阵](current-support.md)。

## 流程

1. 操作者提供 training/holdout 数据，选择允许的候选和计算预算，生成不可变 study plan。
2. 创建 hypothesis 模式的 Agent 任务，绑定该 plan。技能目录仅展示相容的 constrained_study 指引；普通任务不展示此指引。模型只看到候选名称、条件数量和预算，不收到数据数组或留出条件 ID。
3. Agent 可加载 constrained_study 指引并调用 `run_study {}`。输入不接受候选、边界、数据、阈值或新的 plan_ref。
4. 工具调用现有研究内核：只把训练条件交给拟合器，多起点估计；通过 libRoadRunner 交叉核对；冻结拟合参数后评分留出条件。
5. 返回结果摘要并保存研究产物、数据、拟合和环境记录。Agent 可以结束并生成报告，不能在同一执行分支中再拟合或换模型。

新工具与原动作共享 LLM 调用预算，不添加隐式重试。计算预算独立冻结在 plan 中：每候选拟合 1–8 起点、每起点最多 1000 次优化函数评估、每候选拟合超时不超过 90 秒，默认 30 秒。当前v2计划另冻结科学总期限 `total_timeout_s`，默认300秒、最多600秒，覆盖建模、拟合、交叉核对、训练/留出与干预预测及比较；阶段共享剩余时间。旧v1按明确披露的300秒执行默认兼容，不改写原计划。LLM讨论、人工等待和最终回复不计入该科学期限。

## 数据与协议校验

plan 在任何模型或拟合调用之前检查两个数据分组的显式角色、互不重复的条件 ID、时间网格、列长度、有限值、正噪声标准差和固定尺度。留出数据允许在准备阶段作格式校验，但不会作为优化器输入。操作者仍需保证数据划分在科学上独立；仅检查 role/ID 不能证明没有人为重复使用数据。

每组最多 16 个条件、2000 个采样点，每条件 2–500 点，时间范围 0–10000。实验时钟为 second 或保持 unknown；后者不构成单位验证。首版只支持常量 u/b 干预（0–10），初值覆盖须对全部选定候选有定义，不支持分段刺激。k/d/h 的拟合边界来自版本化候选配置，不能通过本工具临时扩大。更一般的模型和观测需新增并验证相应 profile。

plan 保存完整候选草稿、边界、观测、数据和评分协议。读取时重新核对 profile；模板或协议被改变则拒绝执行，而不是悄悄替换旧计划。数值后端版本和代码环境仍随结果记录，不承诺跨版本逐位复现。

## 完成与失败

- 真实拟合和交叉核对有效，但没有候选适合数据，或候选无法区分：可正常交付负结果；winner 不伪造。
- 所有拟合失败、超时或交叉核对失败：usable_candidates=0，Agent 不得标记 completed。
- 研究执行抛异常：保存内部诊断及 plan 依赖，任务 incomplete，不把诊断原文送给模型。
- 执行后即使又暂停、收到研究者回复并恢复，也沿用已消耗的研究执行次数，不能再次拟合。

至多一次是每条 Agent 执行分支的约束，不是整个存储的全局调用锁。操作者显式创建新任务、重复调用 study-run 或从较早暂停点建立替代分支，仍可能再次计算；相同冻结协议不会因此获得修改候选或用留出值重拟合的权限。任意崩溃点的自动恢复仍未实现。

所有研究结果保留 scientific_status=not_verified。拟合好、留出误差小或排名第一，都不证明机制真实、参数全局可辨识或具有置信区间。

## 使用

数据 JSON 为条件列表，每项包含 id、role、experiment、values、sigma，可选 scales；当前观测列为 response。可使用已有开发输入检查命令：

```bash
.venv/bin/python -m biomodel_agent study-prepare --store artifacts/my-study \
  --training benchmarks/inputs/adaptation/training.json \
  --holdout benchmarks/inputs/adaptation/holdout.json --starts 2
```

记下返回的 plan_ref。直接运行冻结研究，不调用 LLM：

```bash
.venv/bin/python -m biomodel_agent study-run PLAN_REF --store artifacts/my-study
```

或者由 Agent 决定何时执行这条已授权研究流程（沿用原模型服务配置）：

```bash
.venv/bin/python -m biomodel_agent agent --store artifacts/my-study \
  --source protocol.txt --mode hypothesis --study-plan-ref PLAN_REF
```

study plan 已提供实验，故不必另给 --experiment；若显式给出，必须与首个训练条件一致。此类任务不接受另行指定 solver、observations、reference、scales 或 allowed_mechanisms，以免绕过或混淆冻结协议。普通复现任务继续使用原 --experiment 流程。

返回 agent_run 的 run_ref 指向 hypothesis_study。两者均支持现有 report/export；研究报告包含逐候选拟合状态、训练与留出误差、条件实验建议及限制。Agent 报告也显示冻结计划、排名、候选选择与未核验科学状态。

Python API：studies.prepare_study、execute_study、plan_summary、result_summary，以及 run_agent(..., mode='hypothesis', study_plan_ref=...)。

## 验证与演示

新增 46 项回归覆盖协议拒绝、数据角色隔离、模板/边界篡改、真实拟合与交叉核对、留出失败负结果、重复执行、恢复、超时、内部错误、CLI 和报告。测试与演示使用脚本动作，不调用模型，不代表自主推理成绩。

解析案例的反馈模型 x'=2−1.2x−z、z'=.2x，与前馈模型 x'=2(1−z)−x、z'=.2(1−z)，从零状态出发均有训练解 x(t)=2.5(exp(−.2t)−exp(−t))。因此仅训练集时应报告无法区分。将抑制臂 b 冻结为零后，两者产生不同预测；解析反馈参考为 (5/3)(1−exp(−1.2t))。回归核对系统只在加入该预设干预后区分候选。

可重跑完整离线演示，输出目录须不存在：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/demo_agent_study.py --output /tmp/new-agent-study-demo
```

本轮实际演示保存在 artifacts/agent-study-development/offline-demo-final/，包含解析数据、冻结 plan、脚本请求、真实拟合/比较、研究报告和结果摘要。反馈/前馈在训练数据上都近乎精确匹配，留出干预才支持反馈候选；科学状态保持 not_verified。数值详情以 results.json 和完整验收记录为准。

本轮未增加依赖、未进行新的产品模型调用，也未修改或重新评分独立测试集。该功能补齐了工具调用和研究编排；模型能否自主正确选择它仍需另行评测。
