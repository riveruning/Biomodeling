# BioModel Agent

面向生物动力学模型的可审计科研助手：从文献重建模型，执行和验证模拟，并在受约束的机制空间中构建、比较新模型。

**v0.1 已实现并运行验收，当前不承诺任意论文自动复现或开放式机制发现。** 产品采用模块化单体和一个受约束的 Agent；Codex / DSH 属于外部开发工具层。交付日期：2026-10-15。

2026-10-17 后续进展：[真实论文评测首例](docs/literature-pilot.md)已完成两组真实 LLM 调用。原始文本组识别图片公式缺口；辅助转录组 11 项方程/参数及引用匹配、3 个初值保留未知，但仍有格式与模型约束问题，没有执行论文模拟。参考答案待人审，不计为完整论文复现成功。

2026-10-17 竞争系统评测：[Talk2Biomodels 与 KinModGPT 实际接入报告](docs/published-competitor-integration.md)。已执行官方源码的替代模型变体，并保留未辅助失败与接口澄清诊断；不视为原论文成绩复现。

2026-10-17 可靠性诊断：[缺失信息处理与模型可靠性评测](docs/reliability-evaluation.md)，覆盖 10 个合成机制的缺初值、缺参数、公式歧义与单位冲突；原始服务失败与另存的串行诊断分别报告。

2026-10-17 Agent 优化：[调用恢复与来源冲突阻断](docs/agent-reliability-improvements.md)。格式和临时服务错误可在总预算内恢复；明确来源矛盾在严格模式中阻断执行，独立留出集保持隔离。

2026-10-17 研究者交互：[澄清记录与任务恢复](docs/agent-resume.md)。暂停后可通过 `agent-resume` 继续原任务，保留证据历史和剩余预算；新增跨进程演示，未读取独立测试集。

2026-10-18 冻结版外部诊断：[独立材料审核与五类小测对照](docs/holdout-pilot-evaluation.md)。复算 20 例参考，单列 6 例评分边界；完成五类材料的原生 Codex 对照和实际任务恢复。属于待领域专家审核的小样本诊断，未建立领先结论。

2026-10-18 开发修复：[来源赋值识别与引用自修](docs/source-and-citation-recovery.md)。修复复合表达式尾项的虚假赋值、识别追加的研究者澄清标题，并把可自修引用错误交还模型。原始回答离线重放与完整回归分别留档，不更新冻结版评测成绩。

2026-10-18 后续修复：[数值声明边界与验证状态](docs/literal-assignment-validation.md)。避免把分数、算式或残缺数值截为参数字面量；完整回归 350 项通过。真实模型验证因网络限制及外发授权审批而未完成，未产生新的性能结论。

2026-10-18 引用诊断：[多处引文诊断与离线恢复演练](docs/citation-diagnostics.md)。一次反馈多处重复/缺失引文，给出有限候选位置；引用预检失败不创建部分 claim。完整回归 364 项通过，另用明确标注的脚本修订演练旧任务恢复，不计为新的模型成绩。

2026-10-18 实验时间：[显式实验时间单位约束](docs/experiment-time-units.md)。实验可声明秒、分或小时；模型时间单位不一致时，在缓存、求解或拟合前阻断，Agent 可在原预算内修正。旧输入保持 unknown，不自动补单位或换算。

## 阅读顺序

1. [实际架构、支持范围与使用说明](docs/v0.1.md)。
2. [验收结果与科学局限](docs/acceptance-v0.1.md)。
3. [Codex / DSH 开发工作流](docs/development-workflow.md)。
4. 原始设计依据：[架构提案](docs/architecture.md)、[契约提案](docs/contracts.md)、[开发阶段提案](docs/implementation-guide.md)。长期设计能力不等于已经实现。

## 使用当前项目环境

依赖位于项目 `.venv`，不需要 GPU，不修改 conda base。

```bash
.venv/bin/python -m biomodel_agent --help
.venv/bin/python benchmarks/development/evaluate.py --output artifacts/my-evaluation
```

第二条不调用 LLM：运行四个公开策展 SBML 的定量对照，以及合成适应响应的拟合、留出干预比较和数据不足判断。它是科学计算基准，不是论文提取基准。

受约束 B 示例：

```bash
.venv/bin/python -m biomodel_agent hypothesis \
  --training benchmarks/inputs/adaptation/training.json \
  --holdout benchmarks/inputs/adaptation/holdout.json \
  --store artifacts/my-study
```

文本提取需要配置 OpenAI 兼容服务地址、模型和 `BIOMODEL_API_KEY` 环境变量：

```bash
.venv/bin/python -m biomodel_agent agent \
  --source benchmarks/inputs/synthetic-birth-death.txt \
  --experiment benchmarks/inputs/synthetic-birth-death-experiment.json \
  --store artifacts/my-agent \
  --base-url https://YOUR_PROVIDER/v1 --model YOUR_MODEL
```

服务明确兼容 JSON 对象输出时，可以追加可选 `--json-mode`。该选项默认关闭，开启后仍进行本地严格校验；重试始终计入总调用预算。

此材料是人工整理的合成示例。Agent 完成技术流程时，论文语义支持仍可为 `unknown`、科学状态仍可为 `not_verified`。

## 在其他目录安装

推荐 Python 3.12，在该项目目录内创建独立环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps --editable .
```

当前宿主缺少 `ensurepip` 时，实际采用以下方式；已有 `.venv` 无需重复创建：

```bash
/usr/bin/python3.12 -m venv --without-pip .venv
/usr/bin/python3 -m pip --python .venv install --no-cache-dir -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps --editable .
```

`requirements.lock` 保存已验收版本，不是跨操作系统二进制环境镜像。

## 验证

```bash
.venv/bin/python -m pytest -q
node scripts/tests/dsh-supervisor.test.mjs
node scripts/tests/dsh-runtime.test.mjs
```

数值测试需要允许创建子进程。真实结果见 [evaluation.json](artifacts/v0.1/evaluation.json)，真实 LLM 单例演示见 [live-agent.json](artifacts/v0.1/live-agent.json)。

## 推荐基线

- A 为主线：公开材料中的小规模确定性 ODE 文献模型复现。
- B 保留完整闭环：现象与数据 → 候选机制 → 新模型 → 参数估计 → 留出预测与比较。
- 模块化单体；一个受工作流约束的 Agent；成熟科学计算后端。
- 每份可执行模型、证据、实验协议和运行结果均有版本及来源。
- 首期以 SBML 为可执行模型格式，应用层保留其他模型类型的接入边界。
- 先验证科学计算核心，再加入 LLM；不要求 GPU，不训练基础模型。

Codex、GPT-6 Sol、DSH 等属于开发工具层。它们可以协作实现本项目，但不因此成为产品运行时组件，也不规定产品内部采用多 Agent 架构。
