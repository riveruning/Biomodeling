# R11a：开发材料准入盘点、参考与审核准备

2026-10-26。本轮新增离线工具 `benchmarks/development/evaluation_readiness.py`，复用 T07 审核包及 T08 v1/v2 评分器，未修改产品源码或旧评分规则。它只盘点明确列入范围的已见开发材料、生成逐案审核资料和阶段输入、补充手写闭式解数值参考；不运行 LLM、不签发独立审核，也不把开发材料改为未见评价。

## 实际核对范围

| 材料类别 | 本轮材料卡 | 实际用途和缺口 |
|---|---:|---|
| T07 检索合成控制 | 18 | 引用/候选片段检索诊断；不是完整建模题或真实论文；独立审核 pending |
| T08 分阶段 ODE 任务 | 3 | decay、translation、logistic；共5个阶段变体；三工作流×两重复共30条计划阶段记录，不是30道独立题 |
| Goodwin Figure 2B | 1 | 已见论文的回溯性图像特征诊断；原始初值/瞬态未知，来源为辅助转录，独立模型/标定审核 pending |
| FBA 数学/公开模型控制 | 2 | 解析链路和 COBRA textbook；LP残差/目标值数值诊断，不是两道独立论文重建题 |

24张卡的计量单位不同，**不能合并宣称24道独立 Benchmark 题**。材料家庭标签是保守元数据，不证明统计独立性或论文实体消歧。其他资料池、独立评测集、c13/c18不在此次盘点内；其数量与成熟度保持未核对。本轮没有访问它们，没有新增论文、网络请求、服务配置或凭证使用。

在这个固定子集内，正式未见评价可准入数为0，独立审核决定为0。即便未来人工审核开发材料，也不能改写其已见属性。本工具不接收审核表来升级正式资格；人工表单是待办工作表，实际决定需保留归属、证据和版本，并通过对应正式研究协议复核。

T07 的 `clarification_scope` 仍保留原 conflict 候选标签及“已有 AI 修订候选、尚未采用”的提示，不悄悄回写旧标注或旧成绩。独立审核应先处理该条件作用域问题。

## 冻结资料与评分参考

`acceptance-pack/private/readiness-packet.json` 保存49份文件的哈希/长度、24张材料卡的家庭/已见属性、来源、候选评分规则、允许的澄清及限制。材料、实现、参数规则、分组、审核字段和预算/阶段计划一旦变化，`check` 拒绝旧包。原图片/FBA文件与来源收据、Goodwin可执行模型与来源绑定模型另外交叉检查。

`private/review-cards/` 提供逐案可读文件，`private/review-forms.json` 的审核人、时间和检查项全部留空，绑定整体及逐卡摘要。检查项覆盖原文范围、方程/参数/初值、单位/条件、参考独立性、预期动作/澄清、指标适用性、家庭/已见属性、权限/交付。它不是审核人的身份、资质或签名认证。

三个 ODE 开发案例新增源文本 SHA-256 绑定、带精确 Unicode 字符位置的闭式解参考：

| 案例/阶段 | 闭式解 | 固定尺度 |
|---|---|---|
| decay / initial | A=2 exp(-0.4t) | A:2 |
| translation / clarified | M=2-exp(-0.4t)；P=6+3exp(-0.4t)-8.8exp(-0.2t) | M:2，P:6 |
| logistic / clarified | X=2/(1+9exp(-0.8t)) | X:2 |

时间均以来源声明的 minute 为准，沿用各实验网格，阈值固定为最大尺度绝对误差≤0.001。闭式解为分析者手写的数学参考，已经用独立编码的 SciPy DOP853 做数值互验，**尚未经过独立领域/数学审核**。translation 和 logistic 的 initial 阶段缺关键信息，没有轨迹答案，`numeric-check` 明确拒绝；不得拿澄清后的答案给初始阶段评分。

源文本变更要求重新检查闭式解；非零初始时间、参数/初值覆盖、分段或与来源不兼容的时钟均拒绝。数值比较直接调用产品 `validation.compare_series`，精确核对网格、变量集、有限值、状态及显式时间单位。返回候选/参考摘要、数值诊断，来源支持保持unknown、正式任务成绩保持not_assigned，不自动导入或填写v1 assessment。轨迹文件本身不证明它由真实模型/程序产生，还需运行收据、来源与行为审核。

## 固定分母与输入边界

沿用原协议的三工作流及两重复。每组10条计划记录；正确完成/预算内完成/过度阻断的适用分母为6；缺口识别/针对性追问/澄清后恢复为4；无依据执行和完成为10。缺跑、服务错误和未审核不会从分母移除。`private/empty-records-report-v2.json` 实际调用旧报告器，所有未提供记录保持 missing/unknown，不能读作已确认失败或“安全无错误”。

每条 `stage-projections/` 文件复用现有 `trial_input`，只包含当前材料和冻结运行配置，没有私有评分规则、数值参考或未来澄清内容。**这些文件是投影，不是操作系统沙箱。** 真正运行时只能挂载一条当前阶段；操作者包、其他案例/重复/阶段必须在运行时可见范围外。同一任务恢复须保留原会话关联及累计预算；新会话另列，不能领取原会话恢复信用。

## 使用

```bash
.venv/bin/python benchmarks/development/evaluation_readiness.py prepare --output /tmp/r11a-review
.venv/bin/python benchmarks/development/evaluation_readiness.py check --packet /tmp/r11a-review/private/readiness-packet.json
# 对本轮开发材料应明确拒绝，退出码2：
.venv/bin/python benchmarks/development/evaluation_readiness.py check --packet /tmp/r11a-review/private/readiness-packet.json --require-formal
# 对某个已收集的结构化轨迹，仅作数学诊断：
.venv/bin/python benchmarks/development/evaluation_readiness.py numeric-check --case decay --phase initial --trajectory /path/to/trajectory.json
```

轨迹字段为times、values、time_unit，可含status；已执行数值比较返回0/1表示吻合/不吻合，非法输入、未解决阶段或正式准入拒绝返回2。prepare输出目录必须全新；校验在写目录之前完成。工具读取固定白名单路径，拒绝符号链接、越界与过大输入，不跟随包中注入的任意材料路径。

## 本轮验证与后续计划

41项专项覆盖计量单位区分、空记录分母、改包/来源/实现失效、原始文件收据、符号链接、无效输入不写目录、审核空表、阶段投影、三例闭式解数值互验、未澄清阶段和单位/网格/变量/非有限值等负控制。实际演练另保存3例DOP853数值吻合、3例错误轨迹不吻合、2例初始缺口拒绝和1例正式准入拒绝；这些都是明确数值/工程控制，不是模型成绩。最终完整回归状态见 `artifacts/r11a-evaluation-readiness-20261026/acceptance.json`。

下一步分开推进，不能靠测试数量代替科研证据：

1. 独立审核人检查来源、闭式解、澄清与行为评分规则；T07标签修订单独留版本，记录不同意见，不能由本 Agent 自签“独立通过”。
2. R11b 实际系统先导：先明确并冻结服务/模型版本、资料外发范围、会话和文件隔离、工具/依赖、墙钟监督、预算、澄清脚本和可信采集。保留每一次失败、缺跑、产物和使用量。此轮不启动这些服务。
3. R11c 同底层模型归因：与原生系统比较分开，冻结同模型/工具权限和每种条件；Memory/RAG/Skill/计划/失败路由的消融需另有任务适用性，当前三个老任务不证明新增能力收益。
4. R11d 正式评价：在已有访问约束下另行准备未见、按论文/机制家庭划分的材料和事前协议。样本量由先导与研究目标决定，不能通过改development字段凑数。

真实先导配置与待办机器清单另存本轮 `pilot-plan.json`。R11a本轮交付的是固定范围内的盘点、参考和审核准备；独立审核及R11b–d仍未完成。旧biomodel自动化保持暂停，没有推送或发布。
