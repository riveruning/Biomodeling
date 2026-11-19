# 参数声明与任务条件核对

2026-10-25 的第二轮增量。任务现在可以冻结参数单位和条件要求，Agent 查证后取得 `parameter_assessment`，提交草稿前重算核对结果并匹配实际草稿。这补上了第一轮“只保存查证线索”的一部分缺口。

后续 R01 已新增[原生 SABIO-RK 参数整理与显式换算见证](parameter-source-adoption.md)，沿用本页的核对和提交流程。下述人工标签语法继续支持；原生记录可以通过准备命令生成可重算的派生来源。

这里的通过状态叫 **declared_fields_match**：表示原材料中明确声明的字段与调用者固定的要求一致。它不表示作者报告正确、实验适用性已经独立确认，或任意自然语言语义已经理解。所有结果继续保留 `semantic_support=unknown`。

## 固定需求与使用方式

调用者提供 requirements.json：

```json
{
  "k/value": {
    "unit": "second^-1",
    "conditions": {
      "organism": "synthetic-system",
      "temperature_c": 37,
      "pH": 7,
      "model_version": "v1"
    }
  }
}
```

通过 `agent --parameter-requirements requirements.json` 或 Python `run_agent(..., parameter_requirements=...)` 启用。要求属于任务冻结配置，模型不能在动作中修改；不允许用于改写已有 frozen study。

目前支持明确标签的完整原文行，例如：

```text
Parameter k = 0.4.
Parameter k unit: second^-1
Parameter k context.organism: synthetic-system
Parameter k context.temperature_c: 37
Parameter k context.pH: 7
Parameter k context.model_version: v1
```

这是有界输入格式，不保证任意论文已经包含这些标签。原文没有相应信息时，不能为了通过检查编造标签；需要有依据的材料整理或研究者署名澄清，且整理来源应当明确保留。原始数据库 JSON 不会被本标签解析器任意解释；SABIO-RK 已支持由 `parameter-source` 读取受支持原生字段，保存带出处和显式换算的结构化转录。

Agent 动作：

```json
{"action":"assess_parameter","args":{"target":"k/value","source_ref":"SOURCE_REF","page":1,"start":0,"end":280}}
```

`end` 必须替换成实际完整行边界。工具读取原材料，自行提取候选值、单位和条件，保存精确位置及逐字段结果。不能由模型传入候选值或一个自称已经通过的结果。标签也支持 `Initial x` 与 `x/initial`。

提交草稿时附加：

```json
{"parameter_assessments":{"k/value":"ASSESSMENT_REF"}}
```

该映射与原来的 draft/evidence 一起提交。提交前会重新读取原始来源、重算 assessment、核对冻结要求和来源范围；草稿值、单位必须一致，还必须引用被 assessment 核对的完整数值声明。字段完全一致也不替代原有引用绑定、模型和数值检查。

选择的片段也不能隐去授权材料其他位置的同目标条件冲突。工具会扫描授权范围内完整标签，记录相反的单位或条件声明。当前格式不解释多个条件块之间的复杂限定，出现冲突时保守追问，不自行选取有利的一段。

## 阻断与单位边界

- 字段缺失、候选值缺失或不支持的单位：`unknown`。
- 明确字段不一致：`mismatch`；重复声明相互冲突：`source_conflict`。
- 同维度但比例不同的单位：`conversion_required`，不自动改数值。
- 缺少 assessment、来源范围已变、伪造结果、草稿偏离或引用了另一处数值：技术性修正后重提。
- 增加研究者回复来源后，旧 assessment 不能直接沿用，需要对当前来源范围重查。

字段包括 organism、tissue、temperature_c、pH、model_version、reaction、compartment、measurement_method、substrate、quantity_kind。分类字段精确匹配，不推断物种别名；温度/pH 为明确数值，不解析区间或“37°C 左右”等语句。当前 pH 需求只接受受支持的 0–14 范围，不宣称这是所有化学体系的普适限制。

本轮也让 `second^-1`、`minute^-1`、`hour^-1` 及列明别名写入真实 SBML UnitDefinition，指数为 -1。要求使用反时间单位时，还检查其时间尺度与显式模型时钟一致；未知时钟或秒/分钟混用不会静默放行。浓度单位检查只比较注册的声明维度/比例；执行端不支持的单位仍拒绝采用，不以标签相同代替实际 SBML 表达。

## 验证

相关源码位于 parameter_applicability.py，新增 `parameter_applicability` 内置流程仅在带参数要求的任务中开放。任务进度台账显示提交时重新核对的声明状态；科学状态保持独立。

验收覆盖缺失/冲突、物种/温度/pH/版本不一致、需要换算、被改写的草稿、伪造检查状态、截断原文隐藏限定、另一来源同值引用、来源更新失效、SBML 单位和实际 ODE 求解。离线脚本行为检查不代表线上模型修复成功率或独立领域审核。

本轮完整回归 1516 项通过，随后新增的跨片段/跨来源条件冲突边界以 phase2-final-boundary-tests.log 为准。所有新检查均在开发材料上，未调用新的线上模型服务或代填科学审核。四种可重跑演示和机器收据保存在 artifacts/shortfall-repair-20261025/ 下。

后续仍需：更多原生字段/来源适配、复杂表格/正文解析、更广单位推导、数据驱动估参及不可辨识性判断、独立科学适用性审核。R01 的有界字段适配和换算不把 T10 整体标为完成。
