# v0.1 验收记录

日期：2026-10-15。结论：接受 **ODE 科学计算基础与受约束 A/B 原型**。不覆盖任意论文自动复现、开放式机制发现或相对通用 LLM 的优势。

DSH 分任务交付主体模块，Codex 独立审查并修正实际库接口、物种语义、训练/留出隔离、拟合器、证据范围和报告等问题。任务卡和监督记录在 tasks/ 与 .workflow/runs/。DSH 自报完成没有作为验收依据，后端实际运行与独立对照才是依据。

## 自动测试

最终结果：**151 项 Python 测试、18 项开发工作流测试全部通过**，见 [acceptance.json](../artifacts/v0.1/acceptance.json)、[pytest.log](../artifacts/v0.1/pytest.log) 与 [node-tests.log](../artifacts/v0.1/node-tests.log)。Python 测试覆盖不可变契约、未知/缺失值、SBML 真结构错误、单位未知、非单位体积量/浓度语义、精确采样、分段输入、子进程超时、存储损坏恢复、训练/留出隔离、已知参数拟合、引用范围、预算、失败状态、缓存/复算及导出原文闭包。

开发工具层 Node 测试覆盖审批范围、未知拒绝、退避、停滞、无文件交付及运行时覆盖；不计为产品科学能力证明。

## 公开策展模型执行对照

输入来源、原始 SHA-256、许可和修正版说明见 `benchmarks/manifests/public-models.json`。实际结果见 [evaluation.json](../artifacts/v0.1/evaluation.json)。

参考曲线由单独手写的策展 SBML 动力学转录使用 SciPy DOP853 / Radau 交叉生成，没有调用产品构建器或 RoadRunner。它提供跨实现数值证据，但人工转录仍可能存在共同理解错误，不等于独立论文金标准。

最大缩放误差为 `max(|simulation-reference| / scale)`；四例冻结阈值均为 1e-3，121 个采样点：

| 模型 | 系统 | 最大缩放误差 | 结果 |
|---|---|---:|---|
| BIOMD0000000010 | Kholodenko MAPK | 2.55e-6 | pass |
| BIOMD0000000012 | Repressilator | 4.42e-5 | pass |
| BIOMD0000000027 | Markevich | 1.94e-7 | pass |
| BIOMD0000000507 | Gardner toggle | 1.56e-7 | pass，显式序列化修正版 |

Toggle 原件缺少可选可视化包的 render:required 属性，不能通过原始结构检查。原件保留，修正版只添加 render:required=false，哈希和变换独立记录，没有改方程或参数；不能标为原件严格通过。

Markevich 原拟作留出候选，但本次已读取验证，所以四例都不作为盲测。静态/单位检查、粗细容差对照和环境保存在研究对象中。单位与来源未知没有转为 pass；守恒检查仍为 not_run。

## 合成 B：训练相容与干预区分

基准是线性化适应响应三个预定义候选：负反馈、非相干前馈、简单生成/降解。数据与协议在 benchmarks/inputs/adaptation/ 和 benchmarks/manifests/adaptation-protocol.json，固定 sigma=0.01 和种子。仅训练数据估参，然后冻结参数预测 b=0 留出干预。

| 候选 | 训练加权 RMSE | 留出缩放 RMSE | SciPy / RoadRunner 最大缩放差 |
|---|---:|---:|---:|
| 负反馈 | 0.7894 | 0.2906 | 1.10e-6 |
| 非相干前馈 | 0.7894 | 0.01036 | 9.68e-7 |
| 简单生成/降解 | 41.17 | 1.522 | 1.85e-7 |

仅训练数据时，两适应候选保留 indistinguishable_from_training；留出干预下，非相干前馈为候选集内最相容模型。阈值是预定义启发式协议，不是显著性检验。数据由该模板生成，结果证明有限协议正确运行，不能证明发现未知生物机制。局部诊断不等于全局可辨识性或置信区间。

## 真实 LLM 集成演示

[live-agent.json](../artifacts/v0.1/live-agent.json) 记录一次真实兼容服务调用：Claude Sonnet 4.6，人工整理的合成生成/降解材料，5 次模型调用、1 次草稿修复，服务返回总 token 9589。技术状态 completed；严格科学状态 not_verified，来源语义和部分单位未获证明。

此前真实尝试中的越界文本区间、错误声明类型和反应格式未通过检查，失败记录保留。该单例不是成功率估计，也不是论文提取基准。自动化 MockProvider 只验证策略与接口，不计为模型能力。

## 研究包复算

公开模型运行已导出到 artifacts/v0.1/portable-bundle 并由新进程在独立目录使用 replay 重跑；结果见 [portable-replay.json](../artifacts/v0.1/portable-replay.json)，执行和参考比较均通过，无 LLM 或缓存复用。

这验证对象闭包和单次模拟重跑，不等于新操作系统安装、容器封装或跨平台逐位复现。原文闭包另有自动测试。原运行与后续代码变化由环境指纹记录。

最终代码冻结后又导出 final-bundle 并启动独立进程 replay，结果见 [final-replay.json](../artifacts/v0.1/final-replay.json)。机器验收记录同时确认四个最终运行的代码指纹与当前源码一致，以及模型、修正版和许可文件的哈希匹配。

## 尚未验收

- 真实论文/补充材料端到端提取基准：not_run。
- 通用编程助手、固定流程、Agent 同预算对照：not_run，没有优势结论。
- 任意 PDF 公式识别、缺参恢复、无依据机制自动确认、开放机制发现：不可靠或未实现。
- 一般反应拟合、置信区间/后验、通用守恒、PDE、事件和延迟：未实现。
- 拟合研究一键 replay、Agent 崩溃续跑、严格论文语义人审闭环：未实现。

后续优先用人工审核论文金标准和同预算对照判断科研价值，再扩大模型范围。
