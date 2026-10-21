# 已发表系统的实际接入与开发评测

日期：2026-10-17。已接入官方固定源码的 Talk2Biomodels 和 KinModGPT；原论文模型在当前服务不可用，本轮运行的是明确标注的替代模型/兼容环境变体，不是原论文成绩复现。

## 源码与环境

| 系统 | 官方来源 | 固定提交 | 本轮适配 |
|---|---|---|---|
| Talk2Biomodels | [AIAgents4Pharma](https://github.com/VirtualPatientEngine/AIAgents4Pharma) | `4b9990b708102bd237939bcb668b476d70a1c79d` | 论文发表日期前最后一个提交，未核实为作者原始评测版本；执行官方 get_app 图、工具和提示。包命名空间引导跳过其他不相关应用的根导入；传入 qwen3.8-chat 的 ChatOpenAI 实例 |
| KinModGPT | [作者仓库](https://github.com/kmaeda16/KinModGPT) | `2b337a3d472e7d482ddb34bb25743d7618285430` | 加载 GPT-4 版本的原始函数与 Instruction.txt，仅在模块外替换 talk_to_GPT 的传输/模型；保留原始输出清理器和 Tellurium 转换 |

官方源码没有改写，选取文件、许可和逐文件 SHA-256 保存在 `artifacts/competitor-integration-20261017/source-manifest.json`。
Talk2Biomodels 的完整下载归档较大，提取所需模块并保留归档哈希回执后删除了传输包；模块源码与许可保留。

服务模型列表确认本次配置不提供 GPT-4、GPT-3.5-turbo、GPT-4o-mini；提供 qwen3.8-chat。
这个事实只指当前已配置的 USTC 服务，不表示这些模型在所有服务上不可用，也没有向其他账号或服务寻找凭证。

运行环境位于评测目录的 `runtime/`（KinModGPT）和 `runtime-t2b/`。没有修改产品 `.venv`、conda base、系统包或作者源文件。
依赖分别冻结于 `requirements-kinmod.lock` 和 `requirements-t2b.lock`。
KinModGPT 原始 macOS/Python 3.7/Tellurium 2.2.3 环境未重建；本轮使用 Python 3.12/Tellurium 2.2.11.2。
Talk2Biomodels 使用其模块所需的依赖，不安装单细胞、知识图谱等无关大型组件；实际解析版本完整保留。

## 轨道 A：文字到动力学模型

沿用此前冻结的 Goodwin 辅助转录：一组明确提供探索初值，一组没有初值。两次各调用一次替代 LLM，无输出修正。
原始 KinModGPT 指令、分段调用方式、项目符号清理及 Antimony→SBML 流程保留。

| 输入 | KinModGPT 变体的实际结果 | 评价范围 |
|---|---|---|
| 完整输入 | 生成 SBML 并模拟，初值 0.1/0.2/0.3 与输入相同；最大缩放误差 `2.6049e-5`，低于 `1e-3` | 完整输入下的数值行为通过；没有测试其人工协作、引用语义或一般文献提取能力 |
| 缺初值输入 | 生成的 Antimony 没有初值赋值，最终 SBML/模拟采用默认 0/0/0，仍执行 | 不提供严格来源模式的缺失初值阻断；不能将默认值当成论文初值。本例无可用的原文初值金标准，不计算轨迹复现通过率 |

原始方法允许若干未指定参数采用默认值。这是其方法约定，需要与我们的严格来源任务分开解释，不能仅凭该诊断就宣称我们整体更强。
它输出的参数名可以不同；本轮按可执行模型的数值行为评价，没有强迫它输出我们的内部草稿或引用格式。
此前 Codex、DSH、本项目 Agent 在同一完整输入上均通过；此处补充的是已发表方法变体的执行证据，而非重新建立同预算排行榜。

## 轨道 B：使用已有 SBML

任务一为合成降解模型，任务二为公开策展的 MAPK 模型。任务、源哈希、采样时刻和参考数据在 `sbml-protocol.json` 固定。
降解模型对照解析解；MAPK 对照此前独立手写 SciPy 方程参考。阈值为固定尺度下最大误差 `1e-3`。

| 入口 | 降解模型 | MAPK | 解读 |
|---|---|---|---|
| Talk2Biomodels 原图＋qwen，无额外接口指导 | 8 次模型调用预算用尽，无轨迹 | 8 次模型调用预算用尽，无轨迹 | 图与服务成功启动；失败发生在任务中的工具参数选择，不是未安装系统 |
| 同一官方图，额外澄清接口规则的独立诊断 | 数值通过，`1.5948e-7` | 数值通过，`9.1850e-7` | 有人工接口提示，不覆盖原始失败，不作为未辅助对照成绩 |
| 作者 BasicoModel 原始计算模块，确定性直接调用 | 数值通过 | 数值通过 | 仅计算模块控制，说明后端可执行，不是 Agent 成绩 |
| Codex CLI / gpt-6.1-sol，独立 Python 实现 | 数值通过 | 数值通过 | 新会话与相同上传模型；程序未由评测者修改，独立进程执行 |
| BioModel 计算核心 API | 数值通过 | 数值通过 | **只是数值控制**：当前产品 Agent 没有直接导入 SBML 的工具动作，不伪装为与 Talk2Biomodels 同任务的 Agent 成绩 |

未辅助运行暴露两个接口问题：显式 `biomodel_id=null` 被原校验器拒绝；改为虚构 MODEL 标识后工具尝试外部加载并失败。另一个字段 `interval` 的说明像时间步长，实际传给计算后端的含义是输出区间数量。
澄清诊断只补充“省略可选 biomodel_id；使用上传文件；interval 是区间数”的接口知识，没有提供参考轨迹或修改数值结果。
这不证明论文原用 GPT 模型也会失败，只反映本轮替代模型、源码快照与工具接口的组合。

第一次澄清诊断已执行模拟，但我们适配器受第三方库 locale 变化影响，记录非 ASCII 消息时落盘失败。已将日志移至 `talk2biomodels-contract-clarified-encoding-failed/`，修复写入为显式 UTF-8 后另行重跑；没有将适配器故障计入竞争系统表现。

## 重跑入口

需要已有的固定 vendor 快照和上述独立环境。runner 不读取凭证文件，只读调用者指定的环境变量。下载、安装、冻结和服务可用性记录均保存在本次评测目录。

```bash
# 无外部模型调用的官方图构建检查
artifacts/competitor-integration-20261017/runtime-t2b/bin/python \
  benchmarks/development/run_t2b_competitor.py --case decay --offline-smoke

# 付费运行：环境变量由调用者配置；不要将密钥写入代码或产物
artifacts/competitor-integration-20261017/runtime/bin/python \
  benchmarks/development/run_kinmodgpt_competitor.py \
  --case complete --key-env BIOMODEL_API_KEY

artifacts/competitor-integration-20261017/runtime-t2b/bin/python \
  benchmarks/development/run_t2b_competitor.py \
  --case decay --key-env BIOMODEL_API_KEY

# 独立标记的接口澄清诊断
artifacts/competitor-integration-20261017/runtime-t2b/bin/python \
  benchmarks/development/run_t2b_competitor.py \
  --case decay --key-env BIOMODEL_API_KEY --clarify-contract

# 不付费的结果重评分
.venv/bin/python benchmarks/development/score_published_competitors.py
```

这些是开发适配器，输出固定到本轮案例目录；原始结果已归档验收。重新运行前应另存已有产物，避免覆盖历史。
完整 JSON/CSV、模型输出、审计、程序复算、依赖锁定及验收见 `artifacts/competitor-integration-20261017/`。

## 结论边界

接入完成意味着已经实际执行作者代码和适用任务，不意味着原论文成绩得到复现，也不意味着已形成统计优势。
本次没有把不支持的论文重建任务强加给 Talk2Biomodels，没有把 KinModGPT 当成具备现代人工协作工具链的 Agent，没有把我们的 API 控制当作自然语言 Agent 成绩。
不同模型、不同提示辅助和单次开发案例不适合汇成一个总分或胜率。当前可以据此讨论严格来源模式、接口可用性与人工协作的价值，仍不能宣称本项目全面优于已发表系统。

## 验收

完整项目回归测试 230 项通过，无失败、错误或跳过；82 个保留的官方源码文件均通过哈希核对。产品源码指纹与接入前的小评测记录一致。本次新增内容为评测适配器、测试、输入、报告和隔离运行环境。验收记录见 `acceptance.json`。
