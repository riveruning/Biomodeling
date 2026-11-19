# 有限来源到随机反应模型

2026-10-30。建议层新增 literal_stochastic_declarations_v1：从完整 UTF-8 声明核对整数初值、反应两侧、随机常数、逐反应单位及显式假设，经研究者选择后生成原生 SSA 输入。原有随机计算后端未改，仍用 mass_action_ssa_v1。本路径接入 [CLI 建议层](task-proposals.md)和[研究工作台](research-workbench.md)。

## 数学约定与范围

原生倾向函数为 \(a_r(n)=c_r\prod_i {n_i\choose\nu_{ir}}\)。c 是每一组特定反应物组合的随机常数；同种双分子反应按 \({n\choose2}\) 计数。这一约定见 [Gillespie 1977 原论文](https://www.cl.cam.ac.uk/teaching/2425/Bioinfo/papers/DanielGillespie1.pdf)。例如 **2 X -> 0** 在 X=4、c=0.5 时倾向函数为 3；不会把常数自动乘二或除二。

来源构建范围比原生后端窄：

- 1–8 种物质、1–16 个反应；初值为 0–10⁹ 的整数。每个反应一侧总阶数至多 2，零阶写作 0。
- 时间单位精确为 second / minute / hour；逐反应单位必须是 1/ 加同一时间标签。明确提供 molecule_count、stochastic_combinations、well_mixed、time_homogeneous。
- 单位检查只针对上述显式组合数约定的一致性。不会从浓度、体积或确定性常数推导随机常数，不做通用 SI 量纲推理，不换算秒/分钟/小时。
- 所有授权来源必须是与原始 blob 一致的完整 UTF-8 单页文本快照，总计至多 64000 字符。PDF/JATS 须先经过明确转录与核对。
- 检查识别到的声明及其条件限定，不理解任意自然语言冲突，不推断网络、反应或生物学适用性。

## 声明格式

[合成示例与模板](../examples/source-stochastic/README.md)：

~~~text
Mixing = well_mixed.
Kinetics = time_homogeneous.
Time unit = second.
State unit = molecule_count.
Rate convention = stochastic_combinations.
Initial X = 4.
Reaction birth: 0 -> X.
Stochastic rate birth = 5.
Stochastic rate unit birth = 1/second.
Reaction death: X -> 0.
Stochastic rate death = 0.5.
Stochastic rate unit death = 1/second.
~~~

物质/反应 ID 使用字母开头、字母数字下划线、最多 48 字符。每行末尾句点可省略。化学计量可写 **2 X** 或 **X + X**，有催化剂时保留两侧物质，例如 **X + E -> Y + E**。速率可为非负小数或指数记数法；按十进制原文比较，0.10000000000000001 不会被浮点舍入当成 0.1。零常数只有原文明确给出时才可接受，缺值不能默认补零。

建议候选使用 stochastic_draft 提供原生模型、stochastic_rate_units 提供每个反应的单位，evidence_map 对应：

- 全局：mixing / kinetics / time_unit / state_unit / rate_convention。
- 每个物质：X/initial。
- 每个反应：birth/reaction / birth/rate / birth/rate_unit。

每个目标须引用 1–4 个完整声明行的证据 ID，证据来源必须在该任务的授权范围内。研究目标或已有模型引用不能证明常数。整体范围内识别到的未引用冲突、额外物质/反应、不支持的声明或缺行同样会阻断；不允许只引用与候选一致的前缀。

## 采纳、执行与报告

建议保存不代表草稿通过检查。检查结果在 draft_check 中；draft_adoptable 为真才可构建该模型。缺数学载荷可以保存为待补建议；携带但来源检查失败的草稿不能强制采纳。

显式选择仍要求研究者、理由和逐项知悉未知项。选择本身不计算。生成的 typed_model 保留原生 JSON、建议与审核引用，以及 source_binding 中的单位、假设和检查 profile。执行前重新核对这些绑定；执行配置继续沿用原生采样、种子、事件上限和超时契约。

最终统一报告包含原生数值报告、选择依据、建议、原文引用、数学草稿与单位。脱离原 store 的结果包可重建同一报告。scientific_support 继续为 unknown；有限种子轨迹、来源字面一致和研究者签名均不构成分布检验或独立生物学审核。

## 开发证据

~~~bash
.venv/bin/python benchmarks/development/demo_stochastic_proposals.py --output artifacts/my-source-ssa-demo
.venv/bin/python benchmarks/development/demo_stochastic_proposals.py --serve --output artifacts/my-source-ssa-browser --port 8766
~~~

目录须不存在。演练使用自建合成材料和脚本模型响应，实际本机 HTTP 与 SSA 计算，不调用外部模型。包含完整构建/暂停续接、缺随机常数、未引用单位冲突三例。不能据此推断真实模型选型或建模成功率。

本批[交付与验收记录](validation/20261030/README.md)保留源码、安装包、专项/完整测试和真实浏览器证据；本批没有独立领域审核或线上效果实验。
