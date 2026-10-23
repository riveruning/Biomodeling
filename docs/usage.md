# 使用指南

先完成[安装](../README.md)和 `biomodel-agent demo`。下列命令在项目根目录执行；`runs/` 保存本地输出，不进入 Git。

## 从材料运行 Agent

```bash
biomodel-agent agent --source examples/birth-death/source.txt --experiment examples/birth-death/experiment.json --store runs/my-agent
```

`.env` 填写接口地址、模型与密钥。`--source` 接受 TXT、MD、PDF，可重复传入多个来源；默认最多 8 次模型调用，使用 `--max-calls` 显式调整。`--json-mode` 仅用于明确支持 JSON 对象输出的服务。

最小实验 JSON：

```json
{"times": [0.0, 0.5, 1.0, 2.0, 5.0]}
```

`times` 必须是严格递增的有限数值，不能包含字符串或布尔值。可选字段有 `initial_overrides`、`parameter_overrides`、`segments` 和 `time_unit`；明确的实验与模型时间单位必须一致，不自动换算。格式定义见 [contracts.py](../src/biomodel_agent/domain/contracts.py)。

命令返回 `ref` 和 `state`。`ref` 是保存对象的哈希；**Agent 的 ref 与模拟 run 的 ref 不相同**，不要混用。`completed` 表示技术流程结束，仍需阅读报告里的科学状态；`awaiting_input` 表示等待补充；`incomplete` 表示预算耗尽或流程未能完成，应查阅报告了解原因。

## 从已有 SBML 运行

这条路径不调用模型接口：

```bash
biomodel-agent validate model.xml
biomodel-agent run model.xml --experiment experiment.json --store runs/my-simulation
```

如需对照，使用 `--reference reference.json`；参考可用 `{times, values, scales, threshold}` 格式，或完整 `SimulationResult`。`values` 是状态名到数值数组的映射，长度需与采样时间一致。尺度和阈值应在执行前确定；有时间单位时需与运行保持一致。

## 报告、研究包和重跑

把下列 `RUN_REF` 替换为 `run` 命令返回的 `ref`：

```bash
biomodel-agent report RUN_REF --store runs/my-simulation --output runs/my-simulation/report.md
biomodel-agent export RUN_REF --store runs/my-simulation --output runs/my-bundle
biomodel-agent replay RUN_REF --store runs/my-bundle
```

导出目标必须是空目录或尚不存在。研究包包含已校验的输入、来源和结果对象；接收者仍需安装本项目与依赖。`replay` 使用冻结输入重新计算。

安装演示返回的 `ref` 本身就是 run 引用，也可用以上命令处理，存储目录改为 `runs/demo`。

## 补充材料与恢复

当 Agent 状态为 `awaiting_input` 时，将补充说明写入 UTF-8 文本，并使用原 Agent 的 `ref`：

```bash
biomodel-agent agent-resume AGENT_REF --store runs/my-agent --reply-file clarification.txt --respondent researcher
```

保留原存储目录；恢复沿用冻结模型、来源历史和剩余预算。详细流程见[任务恢复](agent-resume.md)。进程意外中断后的执行诊断和受控恢复见[恢复协议](controlled-recovery.md)。

## 科学状态与退出码

| 输出 | 含义 |
|---|---|
| `succeeded` / `completed` | 计算执行成功 / Agent 技术流程结束 |
| `passed_structured_reference` | 冻结模型与协议下的数值参考比较通过，不代表论文语义已审核 |
| `not_verified` / `unknown` | 证据或验证尚不完整 |
| `failed` | 执行或明确的科学验证失败 |

`run/replay` 失败退出 1；`run --strict` 未验证退出 2。Agent 未完成退出 2，包括等待补充与预算结束。退出码 0 也需要检查任务的科学状态；一般模型不保证能从论文自动完整提取。

其他命令用 `biomodel-agent --help` 和 `biomodel-agent 子命令 --help` 查看。受约束候选研究见 [Agent 拟合与候选比较](agent-study-tool.md)。
