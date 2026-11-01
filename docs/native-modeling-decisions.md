# 原生 Agent 的显式建模方案

2026-10-29 接续增量。把[方案 IR](modeling-decisions.md)接入已有 Agent 循环，新增显式启用的 `submit_plan`，复用既有 `run`、`finish`、澄清、修复预算和断点恢复。本页描述接续版本；上一阶段的验收包仍是当时源码快照。

## 入口

```bash
.venv/bin/python -m biomodel_agent agent --source path/to/source.txt \
  --experiment path/to/experiment.json --store runs/native-store \
  --modeling-decisions --max-calls 8
```

该命令使用原有 provider 配置，会实际调用配置的大模型。可选 `--modeling-context conditions.json` 冻结字符串条件，例如 `{"organism":"synthetic-system"}`；不传时为 `{}`。Python 对应 `run_agent(..., modeling_decisions=True, modeling_context={...})`。

未启用时保留原有提案协议和基础提示词。启用后，Agent 必须通过 `submit_plan` 提交完整方案，不能用 `submit_draft` 跳过决策检查。新的任务提示说明 schema；完整字段参见[计划格式](../src/biomodel_agent/workspace_assets/references/modeling-plan.md)。任务规划中的草稿提交阶段在此模式下由 `submit_plan` 满足。

`submit_plan` 参数为 `{"plan":完整方案,"reason":"修订理由"}`。首版不要求 reason；后续已保存版本必须保持方案 ID，并提供理由。原文引用可只给精确且唯一的 quote，由工具解析字符偏移；重复引文必须提供 start/end。无法从定性关系推导唯一方程时，保留候选与未解决决定。

## 运行与恢复

- 待选候选或缺参数可进入 `awaiting_input`。`agent-resume` 沿用原 task、调用/修复预算与来源，新增带署名的研究者答复；Agent 据此提交新版本。启用 failure handling 时继续沿用其调查、澄清与停止路线。
- 每个原任务最多一次科学执行；执行后不能改模型再运行。额外来源使旧提交失去当前来源资格，不能用旧结果直接 finish；已经计算的任务不会因此获得第二次计算额度。
- 提交保存方案、编译和提交收据。恢复时核对保留参数、任务、来源、版本链、编译器指纹与结果引用。确认返回后中断可认领已有结果；未确认效果阻断重试。
- 进度、报告、时间线和导出保留方案链。数值成功、参考比较与科学支持分别展示；明确要求参考比较时，比较实际通过才可完成。`run_count` 是科学调用的预算计数，不等价于成功积分次数。

## 当前边界

只接普通 `reproduction` 的有限 rate-rule ODE：1–16 状态、最多 32 参数、默认单位 compartment，无 reaction/derived。初始来源 1–8 个，为最多 8 次澄清预留来源空间；最多 16 次方案修订。条件是精确标签核对，机制解释仍未获得科学认证。

暂不与独立 study、参数采纳要求或估参协议组合，不允许 experiment overrides/segments 绕开方案决定。执行复用方案工作流的范围检查：2–501 采样点、时间 0–10000、求解器 timeout 0.1–60 秒；参考要求同网格/时间单位、完整输出列和预声明固定 scales。这些是局部执行约束，不是整个任务的硬期限。

支持已有任务规划、失败处理和科学流程 Skill 的相应阶段，不代表所有配置组合都已做真实任务验证。原有探索工作流仍走独立入口。源码或环境变化后拒绝旧编译输入，不回签旧结果。

## 可复跑验证

```bash
.venv/bin/python -m pytest tests/test_native_modeling.py -q
.venv/bin/python benchmarks/development/demo_native_modeling.py --output runs/native-modeling-demo
```

演练使用随包发布的已见合成材料与脚本化响应：完整方案、未选候选、缺参数及同任务澄清、解析参考通过、错误参考失败；实际运行求解器并核对导出后报告。合计 15 次脚本响应、4 次科学应用调用、0 次线上 provider 调用。不能据此推断真实模型能力或 token 节省。

完整回归 **2310 项通过**，其中本轮新增专项 **30 项通过**；两者均无失败、错误或跳过。源码版与独立安装版演练结果一致，公共 CLI 开关另行离线验证通过。

本轮记录见[交付入口](../artifacts/native-modeling-plan-20261029/README.md)。原有独立评价集未访问。DSH 仍未用于本轮实现，先前的写入接口故障没有被绕过；自动化保持暂停。

## 下一轮优化顺序

1. 用少量新的开发材料做真实模型先导，先固定输入、上下文限制、调用上限和完成标准，保留拒答、缺值和错误方案。分别统计决策/绑定失败、执行失败、请求澄清、实际 provider usage 与耗时。合成脚本控制只作回归。
2. 对重复失败做针对性修复：引用定位问题改检索/定位；候选选择问题改决策提示和示例；语法覆盖问题补有来源的最小案例；避免把所有失败都归因于模型不够强。
3. DSH 先单独验证最小文件写入与回执，再做相同任务/预算下的 GPT 单独实现和 GPT+DSH 协作对照。必须累计规划、实现、重试、审查与修补的全部 usage；缺失用量保持未知，不能只比较 GPT 调用次数。
4. 在上述证据基础上决定扩展方向：优先用户实际需要的模型类型或多实验复现；暂不同时扩展开放机制发现、估参和自主论文复现。

后续真实模型先导已完成两个开发控制：单变量复现经两次格式修复后成功，缺参数时主动澄清。使用精简提示、流式及请求 low 思考的实验变体，未更改产品默认配置；双变量线上未尝试。实际用量、失败与边界见[先导记录](native-plan-pilot.md)。
