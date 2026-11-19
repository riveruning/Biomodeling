# 显式建模方案 IR、执行收据与外部工作区

2026-10-29。由阶段汇报第 6–8 页和第 16–17 页的建议发展而来。本次覆盖已有小规模 rate-rule ODE 主线，将证据、定性机制、数学选择、编译和计算连接起来。

## 可以实际做什么

计划保存原文定位、语义实体、机制解释、多个数学候选与逐项决策。每个初值、参数和方程都声明 `reported`、`assumed` 或 `unresolved`。选择候选、检查来源、编译模型、冻结执行、运行、报告与导出是明确动作。

“A 促进 B”只支持一个待核实的定性解释，不唯一确定线性项或 Hill 函数。未选候选返回 `awaiting_selection`；缺值或决定未解决返回具体问题；严格复现中的假设阻断编译。探索模式可保留显式假设，生成单独的候选，不把假设写成原文事实。

现有 `ModelDraft`、`submit_draft`、绑定检查和 SBML 构建继续负责数学与来源检查。新层增加选择记录，不替换现有科学工具。本页首阶段交付通过 CLI/Python 提供给外部 Agent。当时尚无原生 `submit_plan`；随后接续已新增显式启用的[原生 Agent 集成](native-modeling-decisions.md)，MCP 服务仍未实现。

## 范围与科学限制

- 首版支持 1–16 个状态、最多 32 个参数、默认单位 compartment 和明确 rate rules；不接反应式、derived 或非默认 compartment。既有更广 ODE/FBA/其他类型入口继续可用。
- 证据保存原文版本和 Unicode 字符偏移；解释与机制仍为未验证候选。已声明条件只做精确字段匹配，不进行通用生物学适用性推理。
- `reported` 的值或公式经过已有有界声明语法检查。科学上合理但超出识别语法的表达仍可能被阻断；不会将未知自动标为支持。
- 数值、参考比较和科学支持分开。工作流可数值完成且无参考，科学状态仍为 `not_verified`；所有新计划/工作流的 `scientific_support` 保持 `unknown`。
- 不做自动 OCR、任意论文重建、自然语言到计划的效果认证或开放机制发现。外部 Agent 的实际建模决策和成本尚待真实先导。

## 安装与工作区包

使用既有环境即可，本轮无新增运行时依赖。workspace 资源随 wheel 分发。

```bash
.venv/bin/python -m biomodel_agent workspace-doctor
mkdir -p runs
.venv/bin/python -m biomodel_agent workspace-init --output runs/ode-workspace
```

输出路径必须不存在，父目录需存在。工作区包含项目说明、本地 `AGENTS.md`、`.agents/skills/biomodel-ode/SKILL.md`、按需参考、依赖锁与三类合成示例。初始化仅导入示例来源和配置，不启动 provider 或科学计算。

`workspace.json` 保存当前解释器、来源引用、源代码摘要和资源摘要。环境检查报告依赖可发现性与锁版本匹配情况，不执行安装，也不证明 provider/求解器健康。宿主需要文件访问和命令执行能力；支持该 Skill 目录约定的宿主可发现指引，其他宿主可显式读取。当前不是所有宿主均已验证的通用插件。

## 操作流程

以下替换 `SOURCE_REF`、`PLAN_REF` 等为实际输出；示例来源在 `workspace.json` 的 `example_source_refs.complete`。

```bash
.venv/bin/python -m biomodel_agent model-plan-prepare --config runs/ode-workspace/examples/complete-plan.json --source-ref SOURCE_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent model-plan-show PLAN_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent model-plan-compile PLAN_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent model-plan-execution-prepare COMPILATION_REF --config runs/ode-workspace/examples/execution.json --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent model-plan-execute TASK_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent model-plan-summary RUN_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent agent-timeline RUN_REF --store runs/ode-workspace/store
.venv/bin/python -m biomodel_agent report RUN_REF --store runs/ode-workspace/store --output runs/ode-report.md
.venv/bin/python -m biomodel_agent export RUN_REF --store runs/ode-workspace/store --output runs/ode-bundle
```

准备/编译的缺口返回码为 2，同时保存并返回记录引用；非法输入返回 1。数值执行符合完成条件返回 0，失败返回 1 并保留结果。读取命令返回 0 只表示记录可读，不能据退出码判定科学正确。

完整 schema 与字段用途见[工作区参考](../src/biomodel_agent/workspace_assets/references/modeling-plan.md)；具体命令语义见[命令参考](../src/biomodel_agent/workspace_assets/references/commands.md)。JSON 拒绝重复字段与非有限常量。

## 补充资料、版本与执行预算

新证据或数学修改使用完整 config、明确的 source refs，以及 `model-plan-prepare --parent-ref OLD_PLAN_REF --reason '具体修改'`。最多 16 次修订，旧提案仍可读；原已冻结任务不被更改。此为计划版本接续，不冒称原生 Agent 的同会话恢复。

编译前和执行前均重检，实际模型必须与选定草稿及证据引用一致。源码指纹或环境改变后拒绝旧编译/执行输入，需显式产生新版本，旧记录不回签。

每个冻结执行任务最多一次科学调用，共用现有本地任务所有权。预算依据不可变文件收据，不依赖可删除的 SQLite 索引。反复 `model-plan-execute TASK_REF` 返回原结果；若仅最终运行记录写入中断，可认领已确认返回的收据。没有确认收据时保留未知效果并阻断重算；错误不自动重试。

执行禁止通过 experiment overrides 或 segments 改写编译后的数学选择。采样最多 501 点，时间 0–10000；单次积分时限 0.1–60 秒，最多粗/加严两次积分，不表示整个科研任务的统一硬期限。参考比较需要同网格、时间单位、全部状态/观测列和事先声明的固定尺度。任务若明确请求参考比较，必须实际比较通过才能完成；比较未运行时，即使求解器检查通过也保留未完成。

`model-plan-replay RUN_REF` 是显式的新任务与新计算，保留原运行关联。导出包保存计划、来源、编译、配置、收据和原始运行；同版本环境可离线读取报告并显式重跑。旧 bundle 不是跨 store 全局执行账本。

## 可读时间线

`agent-timeline` 支持 ODE Agent、检查点、FBA Agent、新四类 Agent 和本轮工作流运行。读取既有事件，保留失败、未知、阻塞与建议动作；事件索引不是时间戳，不编造缺失耗时。一般 Agent 的科学支持字段仅显示其保存标签，不构成独立核验。本轮工作流的事件另经实际任务/收据重检。

候选报告直接展示每个拟采用数值/表达式及其出处状态；长引用或单元格会带截断提示，完整材料仍按引用与偏移读取。Markdown 特殊字符按原文显示，避免公式中的乘号或源文本被误当作排版标记。

CLI 科学流程本身没有 provider 调用，其调用数为 0；外部宿主 Agent 的用量不会因此变成 0，需在真实运行采集中另外记录。`run_count` 表示科学调用尝试/预留，异常路径不能据此推出数值计算确已完成。

## 实际验证与复算

专项控制覆盖来源切片、条件/数值错配、公式歧义、候选选择、假设隔离、缺值与显式资料修订、收据绑定、并发所有权、未知效果、返回结果认领、索引删除、失败参考、包导出与重跑，以及公共 CLI 的三类示例。

首阶段快照完整回归 **2280 项通过**，本轮新增专项 **85 项通过**，均无失败、错误或跳过。源码与独立安装版各完成一套公共 CLI 演练，结果一致；导出报告与原报告字节相同，同环境显式重跑的轨迹最大绝对差为 0。详见[验收记录](../artifacts/workspace-ir-20261029/acceptance.json)与[交付入口](../artifacts/workspace-ir-20261029/README.md)。这些结果验证当前工程和合成数值控制，不是实际宿主 Agent 的能力成绩。

```bash
.venv/bin/python benchmarks/development/demo_modeling_workspace.py --output runs/ode-workspace-demo
```

演练只使用三个已见合成开发控制及预先规定的解析参考/错误参考；不是 LLM 自主建模成绩、独立论文题或领域审核。完整数量、日志、wheel 与实际交付见[本轮验收目录](../artifacts/workspace-ir-20261029/continuation.md)。首轮执行测试因复用的观测预检要求非空而失败，已修正为空观测走原应用层状态输出，失败日志保留。另一次扩大测试的命令误写了不存在的文件名，未执行测试，亦保留原日志。

DSH 曾尝试实现时间线；两次会话的写入申请均缺路径/正文，被既有网关拦截，零源码交付。一次缩小重试后由 Codex 接手。没有修改外部网关或使用替代通道绕过拒绝；缺完整 usage，未宣称节省 GPT token。
