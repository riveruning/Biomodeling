"""Render saved demo results; this script does not run or alter simulations."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def render(root):
    root = Path(root).resolve()
    summary = json.loads((root/'summary.json').read_text())
    run = json.loads((root/'05-run.json').read_text())
    reference = json.loads((root/'reference.json').read_text())
    task = json.loads((root/'task.json').read_text())
    time = np.asarray(run['simulation']['times'])
    colors = ['#0072B2', '#B64C00', '#007A58']
    with plt.rc_context({'font.size':10, 'axes.spines.top':False,
                         'axes.spines.right':False, 'svg.fonttype':'none',
                         'pdf.fonttype':42}):
        fig, axes = plt.subplots(2, 3, figsize=(12, 6.4), layout='constrained', sharex=True)
        for i, (name, color) in enumerate(zip(('X','Y','Z'), colors)):
            actual = np.asarray(run['simulation']['values'][name])
            expected = np.asarray(reference['values'][name])
            axes[0,i].plot(time, actual, color=color, lw=1.7, label='RoadRunner / SBML')
            axes[0,i].plot(time, expected, color='#252525', ls='--', lw=0.9, label='SciPy / DOP853')
            axes[0,i].set(title=name, ylabel=f'{name} (arbitrary units)', ylim=(0, None))
            axes[0,i].legend(fontsize=8, loc='upper right')
            axes[1,i].plot(time, np.abs(actual-expected)/10, color=color, lw=1.2)
            axes[1,i].set(xlabel='Time (arbitrary units)', ylabel='Absolute error / 10',
                          xlim=(0,240), ylim=(0,1.4e-5))
            axes[1,i].ticklabel_format(axis='y', style='sci', scilimits=(0,0))
            axes[1,i].grid(alpha=.18)
        fig.suptitle('Goodwin model: exploratory reconstruction and independent numerical check', fontsize=14)
        fig.supxlabel('Initials (0.1, 0.2, 0.3) are analyst choices. 481 samples; no smoothing.\n'
                      'Scaled-error threshold = 0.001 (outside residual axes). Original Figure 2B: not verified.', fontsize=10)
        for suffix in ('png','svg','pdf'):
            fig.savefig(root/f'trajectories.{suffix}', dpi=180, facecolor='white')
        plt.close(fig)
    caption = ('Three deterministic Goodwin trajectories X, Y, Z over 0–240 arbitrary time units, '
               'with RoadRunner solid lines and independently coded SciPy dashed lines. '
               'The lower panels show absolute solver differences divided by the fixed scale 10. '
               'All 481 samples are retained, without smoothing, filtering or statistical error bars. '
               'This is a single deterministic trajectory, not biological replicate data. '
               'Separate state axes have different ranges; residual axes share one range. '
               'Initial conditions are analyst supplied. Original-paper trajectory fidelity remains unverified.')
    (root/'figure-caption.txt').write_text(caption+'\n')
    provenance = dict(matplotlib=matplotlib.__version__, inputs={
        p:sha256((root/p).read_bytes()).hexdigest() for p in ('05-run.json','reference.json','trajectories.csv')},
        transformation='abs(RoadRunner - SciPy) / 10; no smoothing, filtering or aggregation',
        medium='local demonstration report, no journal submission specification',
        figure_inches=[12,6.4], png_dpi=180, caption=caption,
        guidance='/home/riveruning/.agents/skills/scientific-visualization/SKILL.md')
    (root/'figure-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
    repo = Path(__file__).resolve().parents[2]
    def file(label, path):
        return f'[{label}]({path})'
    links = lambda name: file(name, root/name)
    error = summary['comparison']['max_scaled_error']
    report = f'''# Goodwin 文献模型：完整工具流程演示

本轮实际执行了材料入库、缺失信息阻断、证据绑定、SBML 构建、模拟、独立数值对照、研究包导出和重跑。时间：2026-10-21（Asia/Shanghai）。

**数值对照通过，严格论文复现仍为 `not_verified`。** 本轮由 Codex 整理提案并编排工具；产品内部 `agent.run_agent` 与独立 LLM API 未调用。重跑脚本使用已冻结提案，不是通用文献提取器。

## 任务与结果

论文：Gonze & Abou-Jaoudé (2013), *The Goodwin Model: Behind the Hill Function*，DOI `10.1371/journal.pone.0069573`。使用仓库已保存的出版社 XML 与辅助转录，范围为方程 (1)–(3) 和 Figure 2B 参数，不覆盖整篇论文。

| 项目 | 本轮实际结果 |
|---|---|
| 缺初值的材料 | `{summary['missing_initial_state']}`，未生成可执行模型 |
| 完整探索协议 | `{summary['submission_state']}`，{summary['matched_bindings']}/{summary['evidence_targets']} 个目标绑定匹配 |
| 模拟 | `{summary['execution']}`，0–240 a.u.，481 个时间点 |
| 独立求解器对照 | `pass`，最大缩放误差 `{error:.8g}`，阈值 `0.001` |
| 研究包 | {summary['exported_objects_and_blobs']} 个对象及原文 blob，SHA-256 内容寻址 |
| 导出后重跑 | `{summary['replay_status']}`，轨迹最大绝对差 `{summary['replay_max_absolute_difference']}`，环境指纹一致 |
| 文献语义支持 | `{summary['semantic_support']}`，转录尚待独立人审 |
| 原论文图复现 | `not_verified`，初值为分析者选择，未与数字化原图对照 |

## 本次实际走过的流程

```mermaid
flowchart TD
    A[已有论文材料与辅助转录] --> B[原文入库及哈希]
    B --> C[缺初值提案]
    C --> D[awaiting_input：无可执行模型]
    D --> E[另建探索分支：沿用已声明的初值]
    E --> F[图片编号分行：保留原文与转换记录]
    F --> G[14 项提案与逐项证据]
    G --> H[绑定检查与静态验证]
    H --> I[SBML 模型]
    I --> J[RoadRunner 模拟与收敛检查]
    K[独立手写 SciPy DOP853] --> L[固定尺度与阈值比较]
    J --> L
    L --> M[报告与科学状态 not_verified]
    M --> N[研究包导出]
    N --> O[禁用缓存重跑：差异为 0]
```

这里的缺值分支和完整探索分支分别提交，不冒充研究者回复，也没有调用 `agent-resume`。沿用的三个初值记录为 `system_conjecture`，没有标为论文事实或新的人类确认。

## 模型

```text
dX/dt = k1*Ki^n / (Ki^n + Z^n) - k2*X
dY/dt = k3*X - k4*Y
dZ/dt = k5*Y - k6*Z

k1 = k3 = k5 = 1
k2 = k4 = k6 = 0.1
Ki = 1; n = 10
探索初值：X(0)=0.1, Y(0)=0.2, Z(0)=0.3
```

论文转录使用 arbitrary units；程序保留 `time_unit=unknown`，没有解释为秒或做物理单位转换。SBML 的单位大小区室是变量型 ODE 的编码约定，不是测量体积。

![Goodwin 三个状态的探索轨迹与独立求解误差]({root/'trajectories.png'})

上排实线为 RoadRunner，虚线为独立 SciPy；下排为各状态的绝对误差除以 10。没有平滑、筛选或重复实验误差条。后半段 X 的粗采样峰间隔均值约 {summary['late_peak_period_au']} a.u.，仅作探索诊断。

## 项目整体结构

```mermaid
flowchart TB
    U[用户任务：材料、协议、预算] --> CLI[cli.py / Python API]
    CLI --> AG[agent.py：单逻辑 Agent / 有限状态及预算]
    AG <--> LLM[llm.py：外部模型服务]
    AG --> TOOLS[受约束的科研工具]
    DEMO[本次 Codex 编排 / 冻结提案] --> TOOLS
    TOOLS --> EVID[evidence / retrieval / source_review]
    EVID --> BIND[agent_tools / bindings：提案与证据绑定]
    BIND --> MOD[modeling：ModelDraft → SBML]
    MOD --> APP[application：执行与科学状态裁决]
    APP --> SIM[simulation / validation：求解与验证]
    SIM --> BACK[libSBML / RoadRunner / SciPy]
    APP --> OUT[reporting / bundles：报告、导出、重跑]
    TOOLS <--> STORE[storage：不可变对象、原文 blob、SQLite 索引]
    OUT <--> STORE
    TOOLS -.其他研究任务.-> STUDY[studies / fitting / hypotheses]
```

产品是模块化单体、单逻辑 Agent。模型负责提出动作，确定性工具负责验证、求解与记录。Codex 属于外部编排/开发层。本轮走了图中“本次 Codex 编排”分支；产品 Agent、检索、记忆和候选拟合模块没有在本任务中执行。

| 层 | 关键源码 | 职责 |
|---|---|---|
| 入口与编排 | {file('agent.py', repo/'src/biomodel_agent/agent.py')}、{file('cli.py', repo/'src/biomodel_agent/cli.py')} | 任务状态、调用预算、工具调度、暂停恢复 |
| 证据与提案 | {file('agent_tools.py', repo/'src/biomodel_agent/agent_tools.py')}、{file('bindings.py', repo/'src/biomodel_agent/bindings.py')} | 逐项引用、值/方程绑定、缺失阻断 |
| 建模与计算 | {file('modeling.py', repo/'src/biomodel_agent/modeling.py')}、{file('application.py', repo/'src/biomodel_agent/application.py')} | SBML 构建、模拟、参考比较、科学裁决 |
| 持久化与交付 | {file('storage.py', repo/'src/biomodel_agent/storage.py')}、{file('bundles.py', repo/'src/biomodel_agent/bundles.py')} | 哈希对象、来源依赖闭包、导出与重跑 |

## 产物导航

| 产物 | 内容 |
|---|---|
| {links('task.json')} | 固定的输入哈希、初值来源、阈值、代码版本 |
| {links('02-missing-result.json')} | 缺初值时真实阻断记录 |
| {links('normalization.json')} | 仅将 4 个图片编号后的空格换成换行；没有修改值或方程 |
| {links('03-complete-proposal.json')} | 3 个状态、8 个参数、3 条方程及 14 处引用 |
| {links('04-submission-result.json')} | 14 项绑定诊断及静态检查 |
| {links('model.xml')} | 本次生成的 SBML |
| {links('05-run.json')}、{links('run-report.md')} | 模拟、收敛检查、来源覆盖与科学状态 |
| {links('trajectories.csv')} | 两个求解器的全部轨迹与逐点误差 |
| {links('summary.json')}、{links('06-replay.json')} | 汇总与导出后重新计算结果 |
| {links('bundle/bundle.json')} | 可验证的任务依赖闭包 |

## 重跑

在项目根目录运行，输出目录必须尚不存在：

```bash
.venv/bin/python benchmarks/development/demo_literature_workflow.py --output artifacts/my-goodwin-demo
```

该命令无需网络、API 密钥或绘图库，执行本次全部计算和断言。绘图是独立步骤，需要装有 Matplotlib 的 Python：

```bash
MPLCONFIGDIR=/tmp/biomodel-demo-mpl /home/riveruning/miniforge3/envs/bioinfo/bin/python benchmarks/development/render_literature_demo.py artifacts/my-goodwin-demo
```

只复算本次导出的输入：

```bash
.venv/bin/python -m biomodel_agent replay {summary['run_ref']} --store {root/'bundle'}
```

研究包包含模型、协议、参考与证据对象；代码及 Python 依赖仍需本项目环境。初次未分行的失败记录保存在相邻 `literature-workflow-demo-20261021` 目录。

## 本次暴露的限制

1. `e021:` 等图片编号与参数同行时，当前有界绑定器会返回 unknown。通过保留来源的排版派生文本解决，未修改产品解析器。
2. 初值来自探索协议；原图初值未知，也没有原图数字化轨迹，因此数值一致性不能升级为原图复现。
3. 转录、物理单位和论文语义仍待审核；绑定匹配只证明受支持语法下的声明和值/表达式相符。
4. 没有执行产品内部 LLM 决策循环，不能把这次工具流程当作自动提取能力、Agent 成功率或与其他系统的对比成绩。

图形设计参考本地 {file('scientific-visualization 技能', Path('/home/riveruning/.agents/skills/scientific-visualization/SKILL.md'))}；绘图来源及转换记录见 {links('figure-provenance.json')}。
'''
    (root/'README.md').write_text(report)
    print(json.dumps({'report':str(root/'README.md'), 'figure':str(root/'trajectories.png')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    render(parser.parse_args().output)
