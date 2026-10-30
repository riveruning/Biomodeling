# BioModel Agent

面向生物动力学模型的科研原型：从指定材料构建小规模确定性 ODE 模型，执行 SBML 模拟、参考比较，并保存证据和报告。也支持预定义候选机制的拟合与留出比较，以及有界稳态 FBA 的 JSON/FBC 导入、LP 求解、数值检查与独立 Agent 路由。另有随机反应、1D线性PDE、布尔网络与格点细胞模型的[原生执行架构](docs/model-types.md)。当前不保证任意论文自动复现或开放式机制发现。

可启动[本地研究工作台](docs/research-workbench.md)，在浏览器里整理任务、核对原文与建议、采纳输入、回答追问和导出结果。模型服务默认关闭：

```bash
.venv/bin/python -m biomodel_agent workbench --store runs/research-workbench --port 8765
```

打开终端显示的本机地址。当前仍需显式选择与科研核验，界面不会把输入完整或计算完成当作生物学证实。

## 目录

| 目录 | 内容 |
|---|---|
| `src/biomodel_agent/` | 产品源码 |
| `tests/` | 回归测试与必要固定样本 |
| `examples/goodwin/` | 已有 Goodwin 演示的模型、协议、参考和图 |
| `examples/fba-v1/` | 稳态 FBA 解析网络及冻结参考配置 |
| `examples/model-types/` | 随机、PDE、布尔与个体四类原生输入/设置 |
| `examples/task-proposals/`、`examples/source-stochastic/` | 来源布尔/SSA 声明与建议模板 |
| `benchmarks/` | 科学计算基准、输入与研究评测脚本 |
| `docs/` | 功能说明、科学边界与证据索引 |
| `docs/validation/` | 可共享的版本验收摘要 |

源码分发包含程序、文档、示例和固定测试样本。`artifacts/`、`runs/`、`tasks/`、`discussion-notes/` 及本地服务配置不再跟踪；此次整理保留了本地历史文件。新运行放入 `runs/`，可共享验收摘要见[版本验证](docs/validation/20261030/README.md)。目录用途与归档状态见[目录说明](docs/repository-layout.md)；早期记录也可在[历史版本](https://github.com/riveruning/Biomodeling/tree/5627aa0a382c6f4d3970119a1249c0650173327d)查阅。

## 安装

使用 Python 3.12，在项目根目录创建独立环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps --editable .
.venv/bin/python -m biomodel_agent --help
```

不需要 GPU。上述为 Linux 使用方式；Windows 完整 Agent 流程请使用 WSL2，当前任务锁依赖 Unix 文件锁。

## 使用

不调用模型接口的科学计算基准：

```bash
.venv/bin/python benchmarks/development/evaluate.py --output runs/evaluation
```

接入自己的 OpenAI 兼容模型服务（先在环境中设置 `BIOMODEL_API_KEY`）：

```bash
.venv/bin/python -m biomodel_agent agent \
  --source benchmarks/inputs/synthetic-birth-death.txt \
  --experiment benchmarks/inputs/synthetic-birth-death-experiment.json \
  --store runs/my-agent \
  --base-url https://YOUR_PROVIDER/v1 --model YOUR_MODEL
```

服务根地址会追加 `/chat/completions`。这条命令使用人工整理的合成输入并会调用模型服务；技术流程完成不代表论文语义已经验证。

## 文档与测试

- [整体项目进度与下一阶段（2026-10-30）](docs/project-status-20261030.md)
- [本次整理与验证记录](docs/validation/20261030/README.md)
- [文档分类索引](docs/README.md)
- [目录与归档说明](docs/repository-layout.md)
- [建模方案 IR、执行收据与外部 Agent 工作区](docs/modeling-decisions.md)
- [原生 Agent 显式建模方案与同任务恢复](docs/native-modeling-decisions.md)
- [本地研究工作台](docs/research-workbench.md)
- [统一科研任务入口与六类续接](docs/research-tasks.md)
- [类型建议、输入草稿与来源模型](docs/task-proposals.md)
- [当前支持范围](docs/current-support.md)
- [稳态 FBA 输入、执行与限制](docs/fba.md)
- [FBA Agent 可选暂停与受控恢复](docs/fba-recovery.md)
- [实现说明与命令](docs/v0.1.md)
- [Goodwin 示例](examples/goodwin/README.md)

```bash
.venv/bin/python -m pytest -q
```

新的运行结果请放在 `runs/` 或 `artifacts/` 的新任务子目录中，保留旧验收记录。部分历史研究脚本需要归档产物；产品安装和常规测试不依赖这些实验归档。干净检出中的历史归档/付费集成检查会按已有规则跳过；详见[版本验证](docs/validation/20261030/README.md)。

## 当前阶段

截至 2026-10-30，六类模型已有各自受限的执行路径。ODE 主流程最完整，FBA 支持稳态计算与显式恢复；随机反应、1D 线性 PDE、布尔网络和格点细胞支持冻结原生输入的计算、检查、报告与重跑，通用论文自动建模尚未接通；显式 typed v2 已补暂停与受控恢复。

新增[统一科研任务入口](docs/research-tasks.md)：输入准备、六类分派、状态/追问、累计预算和受控续接使用共同入口。其前置[建议层](docs/task-proposals.md)可提出六类选型和输入草稿；新增来源构建支持有限完整 UTF-8 布尔声明和[随机反应 SSA 声明](docs/source-stochastic.md)，逐项核对原文、单位和假设并显式选择后才形成原生模型。FBA、PDE、个体仍需准备原生模型；线上建议效果尚未验证。

新增[显式建模方案与工作区工具包](docs/modeling-decisions.md)：保存证据、机制候选、数学选择与假设，通过既有 ODE 检查编译，显式执行并生成报告/导出。可用 `workspace-doctor`、`workspace-init` 准备外部 Agent 工作区；可通过 CLI/Python 使用，也已接入显式启用的原生 `submit_plan`；真实宿主建模效果仍待验证。

参数查证、计划、失败处理、记忆、估参和案例复用已有工程增量，真实任务收益仍待评价。后续重点是检验新工作区的真实使用效果，验证原生方案流程并按任务需要扩展其他模型类型。阶段背景见[项目盘点](docs/project-status-20261028.md)；全部专题入口见[文档索引](docs/README.md)。
