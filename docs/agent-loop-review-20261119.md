# 从可执行模型走向可调试研究循环

2026-11-19；开发诊断，不是独立评测或论文复现成功声明。原始用户目标仍包含真实文献检验、模型契约扩展和异质论文回归，不能仅凭本轮测试关闭。

## 已落实的定向修改

1. **明确区分已知输出耗尽与未知请求。** `llm.OutputBudgetExhausted` 只接收合法响应结构中明确的 `finish_reason=length`，且没有可执行动作的情况。缺失 `message`、非法内容类型、未知停止原因、网络未确认响应不因 usage 或 length 字段而被升级。有效 JSON 即使 finish 是 length 仍先接受正常解析结果。usage 缺失不填零。
2. **暂停与恢复成为正式状态。** `research_projects.inspect/snapshot` 投影 `output_budget_exhausted`，已知拒绝从未知操作集合移出，继续条件保持关闭。`research_output_budget` 校验项目、尝试、原始请求、提供方、prompt 作用域和引用闭包；历史记录重读也核验。旧版本未知失败不追认成新版本已知失败。
3. **显式新预算计划。** SDK `research_output_budget.plan` 不调用模型，不退款，不重置调用数/截止时间；新配置只能增加输出上限，可延长超时，其余提供方、模型、格式和推理配置保持一致。新请求前再次核对实际配置。相同归因和计划幂等，冲突拒绝；再次耗尽要另建计划。真正未知 POST 无此通道。计划与历史调用一同进入导出包。
4. **上下文遗漏可见。** `context.build_context` 将总消息、保留/遗漏数、保留比例及遗漏工具类别计数放入实际发给模型的通知。比例按消息计，**不是论文读过多少**；source coverage 明确 unknown。全历史保持原样，重要纠正、草案与科学结果仍保留；必要上下文超限仍停止，不偷偷删内容。
5. **平凡输出可见。** `agent._run_facts` 的轨迹诊断在所有观测列近乎恒定时返回 suspicious。少样本、无结果、无效数值保持 not_available；变化不等于振荡；恒定也可能是正确稳态。因此不改冻结阈值或科学通过状态，而将可操作提示交回模型，要求结合具体目标判断。

示例（运行者 SDK，不是向模型新增的工具）：

```python
from biomodel_agent import research_output_budget, research_projects, research_supervisor

view = research_projects.inspect(store, project_ref)
# higher_cap_provider 沿用相同提供方、模型、JSON和推理设置。
plan_ref = research_output_budget.plan(
    store, project_ref, view['output_budget_attempt_ref'], higher_cap_provider,
    actor='project_operator', reason='保留推理设置，为最终结构化动作提供更高输出上限。')
snapshot_ref = research_supervisor.run(store, higher_cap_provider, project_ref)
```

## 对照当前架构，而非沿用早期 demo 假设

| 用户指出的问题 | 当前代码中的机制 | 尚需改进 |
|---|---|---|
| 失败只返回字符串 | `diagnostics.py` 已有类别、route、suggestion；`failure_handling.py` 有确定性路由 | 各原生类型和项目层诊断字段尚未完全统一；能力反馈和可用例子需补齐 |
| 同坑重复、恢复失忆 | `workstate.py` 从不可变事件重建；失败请求指纹、任务计划、预算和未知效果保护已存在 | 失败策略是 opt-in；研究任务分派不会自动启用全部旧 ODE 策略，不能声称全入口已获得相同防停滞能力 |
| 无意义成功 | `application.py` 的科学结论来自实际数值/参考/来源检查；项目完成要求有效 assessment | 本轮增加平凡轨迹提示，目标相关的持续振荡、稳态、事件、空间模式验伪还需专门契约 |
| 一篇只允许一个模型 | 研究项目可开多个有父关系的 iteration，每次保留独立原生模型 | 仍缺论文层的多模型/多图目标清单与逐项完成状态；不应把多模型塞进一个 SBML 执行负载 |
| 时滞和事件 | `modeling.py` 明确拒绝 unsupported_delay / unsupported_events，能力目录有边界 | 没有 DDE 和一般事件后端；扫描不能冒充严格分岔分析 |
| 文献内容静默遗漏 | 原始 PDF、页文本、图像警告、区域审核及转录产物保留；已有全文分块阅读 | 本轮显示历史消息遗漏；不能代替页/公式/主题的读取覆盖台账，后者仍需实现 |
| 演示集无法代表真实文献 | 已有真实/辅助材料的开发流水线及独立评测边界 | 用户要求的 20–30 篇形态多样开发回归尚未完成；旧黄金案例与本轮 fixture 不能计成新增真实论文 |

`docs/architecture.md` 自称原始 v0.1 设计提案，不能据其推断当前所有实现。当前是六类原生数学执行器 + 统一任务/研究项目编排；应共享证据、诊断和持久化循环，保持各数学执行器语义独立。

## 下一批完整实现方案，可直接拆给 DSH

每轮限定一个行为、明确源码与测试文件、先交付局部修改。Codex 执行独立验证；DSH 连续无文件产出时停止，转 Codex 实现，不循环消费相同上下文。仅 DeepSeek/GLM 最高已支持推理档；未知 POST 不重发。冻结评测集、凭证和旧证据不变。

### A. 统一失败契约与策略切换

- 在 `diagnostics.py` 定义版本化结果：`error_type/code/target/what_engine_supports/suggested_fix/example`，从能力目录提供事实，不让错误字符串猜测创造新能力。接入 ODE、FBA、typed 执行器、原生任务和研究项目，不复制一套科学逻辑。
- 新策略版本使用两次同类且同根因失败触发换策略或明确暂停。保留旧冻结策略版本和旧任务兼容；不直接把旧 POLICY 常量 3 改 2。根因键包含结构/目标/材料版本，不能因改 reason 或 JSON 排序清空失败记录。
- 策略改变必须是有实质差异的草案/方法或新增证据；相同模型另开 iteration 仍计停滞。成功的只读查询不能无限清零。恢复从相同事件谱系计算，不退还预算。
- 验收：原始 `Y_delay` 未声明案例；换参数但相同不支持语义；连续两次；合法方法变化；新增材料；进程中断/恢复；跨项目隔离；停滞后的未知请求不自动重发。多个入口都通过才声明统一。

### B. 结果验伪与多目标台账

- 在论文目标层登记 `target_id/model_id/figure/panel/conditions/reference_kind`；目标可以是稳态、阻尼响应、持续振荡、分岔候选或定性拓扑，不能仅按标题推断。每个模型仍有独立参数、初值、单位、证据和计算收据。
- 平凡诊断后续接入目标相关检查：有明确振荡目标时报告采样长度、振幅变化、周期证据和瞬态范围；没有足够周期只报不足。允许真实稳定稳态通过对应目标，避免反向“见平就拒”。
- 模型数组作为论文任务清单和模型引用集合；执行器仍一次执行一个权威负载。完成按目标逐项聚合，不让一图成功代表全文。没有数据时区分定性支持和定量复现。
- 验收：综述含三个模型；同符号不同模型；缺一张图；恒定稳态正控制；固定点冒充振荡负控制；瞬态振荡误报；所有数值检查失败而模型声称完成；导出和恢复保持各目标状态。

### C. 数学能力扩展（分批交付，不能只放开解析器）

1. **有界常时滞 DDE 配置**：显式状态历史函数/历史数据区间、初始时间、时滞单位、延迟表达式依赖、求解容差、样本区间和计算预算。新增独立适配器和能力声明，保持 ODE/SBML 原语义；不支持的变时滞、中立型、隐式时滞明确拒绝。固定历史与已知解正控制、跨延迟边界和步长收敛、零延迟极限、缺历史/负延迟/单位不符负控制都通过后再接 agent。中间物链是近似，不能规定“20级就等价”，必须独立保存近似依据与收敛证据。
2. **事件配置**：冻结支持的触发、赋值、优先级、持续性和延迟语义，明确触发值与执行时值的区分。连续跨越、同时事件、重复触发、状态重置、步长/采样边界与重放一致性检查全部通过；当前不支持的 SBML 事件不得因解析成功而执行。
3. **参数扫描到分岔分析**：先做有界扫描及稳态/周期描述，明确不能仅凭有限扫描点认证分岔。若声明分岔定位，另加平衡分支、稳定性指标、数值容差和独立已知系统控制；分支跳跃、瞬态长度、迟滞和参数方向分别记录。
4. 上述每项均接项目目标、来源契约、失败路由和预算。不得独立写脚本绕过 agent 后宣称主系统支持。

### D. 原文读取覆盖

- 建立来源页/块/公式/图/表/SI 的覆盖清单；记录未读取、仅文本、图像未转录、已转录未审核、已审核及所需目标。以原始定位和版本为键，不用摘要长度代表读过。
- 每次上下文选择显示已给出/未覆盖的定位范围和缺口；主题只能由可追溯的分节标签或有归因的人工/模型标签产生，不靠关键词遗漏静默补全。保留检索接口，让 agent 请求未覆盖材料。
- 验收：标题/正文/图注跨页、图像公式、SI 缺失、多个模型、改版文献，以及仅占 39.5% 块的案例；实际提供给模型的范围与台账逐项一致。

### E. 20–30 篇真实文献回归

- 先完成本篇与既有 Gonze 的开发诊断，再冻结一个分层开发清单，目标 24 篇，包含综述/多模型、图片公式、缺 SI、只有图、时滞/事件、单位/初值冲突、稳态与振荡正控制。记录 DOI/原件 SHA、许可/材料来源、目标、家族、已见状态；不把重复图或辅助转录计作不同论文。
- 一次冻结首轮配置、各层预算、模型与 max 设置和成功定义。逐篇记录提取/覆盖、建模/证据、数值执行、目标比较、基础设施、待审核。缺跑和未知结果保留在分母，修后复跑另开版本，不覆盖原失败。
- 报告正确完成、有效识别材料不足、错误完成、过度阻断、费用/未知 usage、人工补证、各层失败率。家族/论文是统计单位，不能把多图和重复调用当独立样本。开发集不充当未见正式评测。

## 本篇文献的证据与限制

Novak & Tyson 原 PDF：SHA256 `cbdd88e9caf1de838449679f225b313c4c2905c9672704478ae3ae155f74314f`。默认项目入口提取 27 页、62652 字符；多个页面存在未转录图像。图 1c 对应时滞 Eq.2，图 2 含多个子模型和 S1 依赖，图 3c 对应 Eq.4；不应混用参数和初值。

已有 `artifacts/novak-tyson-literature-20261119` 的任务场景实际是 MM→Expt 背景文献提取，不是模型复现；失败记录包括未知 provider 请求。某个旧临时启动脚本使用 low，不能算本轮 max 设置的对照，不能沿用。原始记录保留，不以新结果覆盖。

本轮另建 `novak-figure3-triage`，使用真实项目入口、明确 Figure 3c 目标、最多三次规划调用、GLM/max/12000 输出上限；其结果是开发诊断而非定量复现认证。缺公式或初值时应保留缺口并追问，不能靠猜补参数获得成功。


本次真实 GLM/max 诊断已完成：三次规划调用均收到合法动作，reported total tokens 为 39504（其中输入 token 占大部分，不能等同新增付费 token）。操作依次是页20越界读取被拒、修正后读取页20、读取页19；项目最终 planner_budget_exhausted。没有模型、仿真、目标比较或追问产物，故**未复现**，也不能用它证明框架性能提高。完整收据见 `artifacts/development/agent-renewal-20261119/novak-figure3-triage/result.json` 和独立 store。

这次实际运行改变下一步优先级：先将读取范围错误转换为带可用页长/合法示例的反馈，并改善有明确模型目标时的定位；当前每轮规划 prompt 约1.1万 token，还应审查阶段相关 schema/指引的必要性，避免以重复长提示弥补状态缺口。此诊断只选择一个已见综述，不是24篇异质文献回归，更不是与基线的配对效果证据。


提示开销已有逐字段实测（原始首次规划请求，不按字符伪称 token）：系统指令19229字符，冻结任务消息15890字符，其中能力目录7560、全部模型语法例子3166、科研过程指引2044、ODE方案例子1516。`research_guidance.frozen_model_guidance` 的瘦身门只在有 background index/reading 后触发，普通复现的读取阶段不会触发，因此不相关的多后端材料被重复嵌入。下一批应依据当前任务阶段提供可达的按需指引和必要能力摘要，而不是要求模型在长提示中自己筛选，亦不能静默删科学证据或不可用能力的限制。实测索引为 `novak-figure3-triage/prompt-overhead.json`。


另外定位到[官方 PMC 正文及补充材料入口](https://pmc.ncbi.nlm.nih.gov/articles/PMC2796343/)，列有 `NIHMS161063-supplement-Supplementary_Material.pdf`。当前 web 读取该附件遇到浏览器校验，尚未下载或审阅；Nature 页跳转到身份服务，亦未获取。不能因此声称补充材料不存在。官方网页呈现数学记号，下一批应检验项目 HTML/JATS 解析路径能否保留可用公式，再决定是否需要图像转录。发现记录保留在 `supplement-discovery.json`，并未把未读附件作为模型证据。


上一轮工程验收（当轮源码指纹）：四个互斥模块批次覆盖全部3509项收集结果，共3506 passed、3 skipped、2 subtests passed；源码/测试采用指纹不变。原单进程运行收到SIGTERM退出143，不能算完整通过。各批数值库线程限制为1。详见 `artifacts/development/agent-renewal-20261119/full-regression-review.json`。此结果不等于论文复现或整体性能提升；后续全目标仍保持active。


## 后续定向修改：读取与指引可达性

公共 `diagnostics.validate_source_span` 为拒绝的越界读取提供页长、上限、合法新请求示例和空页处理；接入项目与原生 agent，不自动裁剪或执行示例。失败和新读取仍分别留痕，调用预算不重置，源范围授权仍先校验。

普通项目的能力目录、语法示例及大动作 schema 改由正常 `inspect_research_guidance` 获取；不再将完整副本塞进初始任务，也不等到超限才瘦身。验证发现旧 schema 仅在背景索引/分块阅读后暴露查询入口，现已为普通早期阶段补齐只读入口。未就绪的执行动作仍不能查到或执行，真实后端验证不变。

与上一轮 Novak 相同原件、目标和策略的初始请求对比，序列化字符由47447降至33547（约29.3%）；冻结任务消息由15890降至2150。此数字不是 token 或费用节省率，也不是模型效果对照。已有三条真实决策在独立离线 store 中通过正式 ChatProvider mock 重放，确认新范围诊断进入下一次请求、失败记录未被覆盖、三次预算仍准确耗尽。重放不产生新模型决策，不复制真实 usage 冒充模拟费用，不构成论文复现。

本批无新增付费模型调用。当前验证范围与源码指纹见 `artifacts/development/agent-renewal-20261119/read-guidance-02/review.json`；上一轮全量结果不冒称本批全量。下一步仍是目标模型定位、公式/补充材料与初值证据闭环，再进行新的真实运行；其余失败策略、模型契约和24篇开发回归目标不变。

本批最终定向验收：10个相关模块275项通过，涵盖 ChatProvider/研究循环、原生读取与恢复、预算失败、上下文和完整指引→原生模型运行；期间源文件与测试指纹不变。旧断言已从“内嵌副本存在”改为“通过正式查询取得相同目录/模板，且保留人类回复与科学权限”。本批未再次全量运行，上一轮3506项通过仅适用于上一轮源码。


## 第三批：DSH小型代码提案与两次停滞规则

本次DSH采用无文件/无shell的极小纯代码提案，约147秒交付。新默认策略v2阈值为两次，v1仍保持三次；Codex额外接入统一ODE任务、原生结果策略绑定和真实阈值提示。缺信息的二次停滞切换到追问而非继续查证或泛化停止；不退预算、不选择参数、不重复未知效果。189项相关回归及2项专门公共分派/篡改负控制通过。独立SDK仍opt-in，其他类型/外层项目尚未统一。记录见 `strategy-03/review.json`。

本批科研模型调用为0，另有一次DSH代码提案。通过现有环境网络配置已获取235822字节官方PMC HTML并校验PMCID，原始SHA为 `bc0c60cc78900dc7890124e450f7d3becf41f6ac08ad17fa03b3ebf6331c64cd`。正式SDK的直接GET曾超时，两个网络路径的证据独立保留，不冒称SDK网络已修。补充链接仍返回HTML而非PDF，未采用为材料，未执行其中任何指令或程序。

新的实际根因：原始HTML含10个MathML元素，其中6个显示公式未形成投影块；现有提示只报告未转录图像，没有揭示这项公式丢失。下一批应修 `background_html` 的公式块与结构保留、定位和未审核标记，避免分数/上下标静默展平，再通过项目真实读入口验收。不得以原文抓取成功或未审核数学序列化宣称论文已复现。详见 `materials-03/formula-gap.json`。

## 第四批：官方 HTML 显示公式投影修复（2026-11-19）

`background_html` 1.0.0 完全跳过 MathML：官方 PMC HTML 的 10 个 `<math>` 没有一个进入文本投影，其中 6 个显示公式（M1/M3/M4/M5/M6/M9）没有对应块，此前的提示只报“图片未转录”，掩盖了公式丢失。本批修复为 1.1.0：

- 新增 `_serialize_math`：保留 `mfrac/mrow/msup/msub/mo/mtext` 结构，剔除 `script/style` 与 `on*/href/style` 等可执行或无关属性，文本与属性按 XML 转义（`&lt;`、`&amp;`），只保留 `id/class/display/alttext`。
- 投影规则：显示公式（`display=block`、`table.disp-formula`、section 直接子节点）各自成块并带原 `id`/路径；行内公式以独立 token 进入段落，不与前后文本拼接成假方程；每个 token 形如 `[unreviewed-mathml] <math ...>`，状态固定 `unreviewed`，`representation=parsed_mathml_serialization`。
- 定位与覆盖：`formula_sources[*].source_spans` 精确指向页文本，逐条经 `inspect_source` 读回一致；被忽略子树（如 `<svg>`）里的 `<math>` 记 `not_projected` 并报 `mathml_not_projected`，不伪造投影。
- 元数据上限 `MAX_FORMULA_RECORDS=100`：仅截断记录，页文本中的公式 token 不截断；`formula_sources_truncated` 显式暴露。
- 来源状态：有未审核公式或有未投影公式时整体 `needs_review`，不宣称公式已审核或可执行。

证据：`artifacts/development/agent-renewal-20261119/materials-04/review.json`（修复前后对比）与 `formula-projection.json`（真实 HTML 逐条读回）。真实材料：官方 HTML sha256 `bc0c60c…64cd`，10 个 MathML → 10 个投影、6 个显示块（FD1–FD6），span 回读全部精确，planner 调用 0。

测试：`test_html_mathml.py` 7 项与 `test_background_html.py` 5 项全过（12 passed）；HTML/证据/项目相关 8 个文件 321 passed；轨迹/引用/单位/恢复/失败策略负控制 225 passed。以上是工程回归，不是论文复现声明。

## 第五批：两篇真实文献的首轮诊断（进行中，未完成）

已用同一真实材料、同一模型/推理档（glm-5.3-flash, reasoning=max）、预先冻结预算启动两个独立项目：

- Novak Fig3c/Eq4：官方 HTML，`planner_max_calls=12, max_native_calls=10, max_iterations=2, max_tokens=12000, timeout=2400s`，收据 `novak-eq4-04/`。
- Gonze Fig2B：辅助转录为摄取源，原始 `article.xml`/`article.html` 仅保留哈希未解析；`6/4/1, 12000, 1800s`，收据 `gonze-fig2b-04a/`。

两次首轮调用都在冻结时限处被截断，未返回任何动作：

- Novak：`respond` 2399.9 s（timeout 2400），1 次 planner 调用，reported tokens 0、`missing_usage_calls=1`，state `needs_recovery`。
- Gonze：`respond` 1799.7 s（timeout 1800），同样 1 次调用、usage 缺失、state `needs_recovery`。

按“未知 POST 不重发”保留原样，未自动重试、未重置预算；**没有模型、仿真、比较或追问产物，不能计为复现结果**。这是基础设施/提供方延迟失败（`glm-5.3-flash` 在网关模型列表中，但该 POST 挂起至超时），不是论文失败，也不是架构死循环。另记录负控制：PLOS HTML 因非 PMC `<article>` 结构被明确拒绝（`gonze-fig2b-04a/ingest-rejection.json`，0 调用）。

下一轮应先做一次独立的、显式记账的最小提供方探针（同模型/同档位、短 timeout、no-json-mode 对照），区分“网关排队”与“json_mode/reasoning 参数导致挂起”，再决定是否用新预算重跑；不得把本次未知 POST 算作已消耗 token，也不得据此宣称模型能力结论。

仍缺：PDF 图像公式转录、页/块/公式覆盖台账、多目标（多模型/多图）登记、20–30 篇异质文献回归；Gonze 的初始值缺口必须由研究者答复后才能做严格轨迹重放。

## 第六批：长推理请求的非流式挂起（基础设施根因）

两轮真实尝试都指向同一个提供方行为，而非论文或解析器：

| 尝试 | 模型 | 冻结预算 | 结果 |
|---|---|---|---|
| `novak-eq4-04` | glm-5.3-flash | 12/10/2, 2400 s | 第 1 次 POST 挂到 2399.9 s，usage 缺失 |
| `gonze-fig2b-04a` | glm-5.3-flash | 6/4/1, 1800 s | 第 1 次 POST 挂到 1799.7 s |
| `novak-eq4-05` | glm-5.3-flash | 10/8/2, 600 s | 第 1 次 POST 挂到 599.7 s |
| `novak-eq4-06` | deepseek-v4-flash-ascend | 10/8/2, 600 s | 前 3 次 `respond` 正常，第 4 次挂到 591 s；3 次 `inspect_source` 已读页 |
| `gonze-fig2b-05` | deepseek-v4-flash-ascend | 6/4/1, 600 s | 第 2 次 POST 挂到 596 s |

小请求探针（`provider-probe-04*`）：GLM 与 DeepSeek 的 20-token 请求 3–8 s；31k 字符合成 prompt 在 GLM 55 s、DeepSeek 5 s；**同一条真实冻结 planner prompt（33,274 字符）在 DeepSeek 非流式 23.7 s 返回**。真正挂起的是带长推理轨迹的那次调用：流式重放同一 prompt 得到 `first_event=42.3 s`、`first_content=123.7 s`、`total=125.6 s`，其中 `reasoning` 增量约 10,194 字符，最终返回 317 字符动作。

结论：提供方的**非流式**路径在 `reasoning_effort=max` 的长推理上会把连接挂到客户端 deadline；流式正常。修复：

- `ChatProvider(stream=False)` 新增可选 SSE 传输 `_post_stream`：累积 `delta.content`，合成常规 chat-completion envelope（usage/finish_reason 保持原语义），未知/超时仍是 unknown，不重试。
- `provider_identity` 在 `stream=True` 时记录该字段，收据可区分两种传输。
- `run_live_target_20261119.py --stream` 把该传输用于真实运行；默认关闭，旧行为与全部旧测试不变。
- 新增 `tests/test_llm_streaming.py` 2 项；provider/预算契约 75 项通过。

这不是“把超时调大”或放宽预算：预算仍冻结、未知 POST 仍不重发；只是把同一请求换成可观测的流式传输。`novak-eq4-07` / `gonze-fig2b-06` 用该传输重新冻结预算（10/8/2, 1800 s；6/4/1, 1200 s）继续首轮诊断，结果另记。

## 第七批：真实首轮结果与两个新的模型层根因

流式传输启用后，两篇论文都获得了真实结局（不是超时）：

**Gonze Fig2B（`gonze-fig2b-06`，deepseek-v4-flash-ascend, stream, 6/4/1, 1200 s）**
3 次 planner、28,962 tokens、148.6 s，状态 `awaiting_alignment`。动作序列：respond → scenario_source → respond → invalid_response（1 次，格式纠正）→ respond → ask_alignment。问题精确定位到缺失的 `X0,Y0,Z0` 与模拟窗口，给出 4 个选项（提供数值 / 授权任意初值并只做定性 / 只复现方程与参数 / 视为阻塞），明确拒绝从先验知识导入初值。**这是缺参数正控制的正确行为**：没有模型、没有求解、没有猜补。

**Novak Fig3c/Eq4（`novak-eq4-07`，10/8/2, 1800 s, 60k context）**
7 次 planner、107,233 tokens、741 s 后 `ContextBudgetExceeded`：essential context 需要 61,515 JSON 字符 > 60,000。序列为 4 次 `inspect_source` + `revise_frame` + guidance；**未产生模型或仿真**。根因：旧的读取结果没有压缩成“定位 + 有界摘录”，长综述的多次读取把上下文顶满，系统是硬失败而不是有损压缩。

**Novak 重跑（`novak-eq4-08`，120k context）**
6 次 planner、89,799 tokens、413 s 后 `output_budget_exhausted`（5 次 `inspect_source`，finish_reason=length 且无可解析动作）。这是**已知**输出预算耗尽，进入 `output_budget_attempt_ref`/暂停通道，未重发、未重置旧预算；说明放宽上下文只把失败点从输入预算推到输出预算，真正的浪费是读取策略：agent 在重复读页而没有形成“方程/参数/初值/单位”的目标清单。

由此得到下一批的两个明确修复方向（尚未实现）：

1. **读取压缩**：`inspect_source` 的历史结果在上下文中改为“locator + 有界摘录 + 未覆盖范围”，而不是整段重放；读取台账记录已给/未给定位，避免以摘要长度冒充覆盖率。
2. **读取计划/停止条件**：在 reproduction 场景下限制“无新增定位的重复读取”，并要求每次读取前后更新目标清单（方程、参数、初值、单位、SI）。这与“两次停滞换策略”同源，但作用在读阶段。

本批真实调用（均为 DeepSeek/GLM max，全新冻结预算，未重发未知 POST）：Gonze 28,962 tokens；Novak 07 107,233；Novak 08 89,799；探针合计约 1.5k 输出 token 级。收据见 `gonze-fig2b-06/`、`novak-eq4-07/`、`novak-eq4-08/`、`provider-probe-04*/`。仍未完成：Novak 的模型/仿真/比较，Gonze 需研究者回答初值；MM→Expt 与多模型登记、20–30 篇回归均未开始。

## 第八批：上下文投影压缩与同预算对照

上一批 Novak 在 60k 上下文中失败，根因不是论文内容，而是每轮请求都携带两份可重读的派生投影：`inspect_progress` 里的 `action_schemas`（7,708 字符）与 `project.scenario`（约 3.2k，和冻结任务重复）。17 条消息共 103,606 字符，其中 essential 包就需要 61,516 > 60,000。

修复（`context._compact_projection`）：当 essential 包超预算时，**只**压缩 `inspect_progress` 派生投影——`action_schemas` 替换为 `{omitted_for_budget, retrieve_with: inspect_research_guidance}`，`project.scenario` 标注为与冻结任务相同，并写入 `projection_compacted.history_unchanged=true`；历史原样保留，科学陈述、引用、草案与诊断不缩短。若压缩后仍超预算，仍以 `ContextBudgetExceeded` 失败关闭（负控制测试）。

对照（同材料、同模型 deepseek-v4-flash-ascend/max、同预算 10/8/2、60k、1800 s、stream）：

| 运行 | 代码 | 结局 | planner | tokens | 读页 |
|---|---|---|---|---|---|
| `novak-eq4-07` | 无压缩 | `ContextBudgetExceeded` 61,516 > 60,000 | 7 | 107,233 | 4 |
| `novak-eq4-09` | 有压缩 | 无输入预算失败，转为 `output_budget_exhausted` | 6 | 83,575 | 5 |

即输入预算死路已消除（少 1 次调用、少 23,658 tokens），但 agent 仍在 5 次读页后把 12k 输出预算耗在推理上、没有产出可解析动作。这是**已知**输出预算耗尽，按设计进入显式新上限计划通道；用 `resume_output_budget_20261119.py` 对同一 attempt 申请 20,000 上限（只允许提高输出上限，调用数不重置）继续，结果另记。

测试：`tests/test_context_projection_compaction.py` 2 项（压缩成功 / 无投影时仍失败关闭）；项目、读取、上下文相关 5 文件 137 项通过。

## 第九批：读取停滞的确定性提示（Novak 首次得到正确对齐问题）

读取压缩只解决了“请求装不下”，没有解决“模型持续读同一段”。实际读跨度显示 `0-8000` 被重复读取四次，历史在 `respond ↔ inspect_source` 之间交替，因此“连续读”不能用相邻操作判断。

修复分两层：

1. **重复读取台账**（`research_projects.inspect_source`）：对同 source/page/span 的重复读取返回 `read_ledger{repeat_read, prior_span_occurrences, new_characters}` 与 `diagnostic`，明确“不要再读这一段，用已保留文本、提交目标 frame 或提出精确对齐问题”。文本仍返回，历史不改。测试：`tests/test_source_read_dedup.py` 2 项。
2. **停滞指令**（`research_supervisor.read_stall_directive`）：从历史尾部统计“自上次非读操作以来的读次数”，把 `respond` 视为透明，达 3 次时在下一请求追加一条 `planner_directive`（不增加证据、不改历史、不重置预算），要求停止读页并提交 frame 或对齐问题。测试：`tests/test_read_stall_directive.py` 3 项。

同材料、同模型/预算的连续对照（deepseek-v4-flash-ascend, max, stream, 10/8/2, 60k, 1800 s）：

| 运行 | 代码状态 | 结局 | planner | tokens | 读页 |
|---|---|---|---|---|---|
| `novak-eq4-07` | 无压缩 | ContextBudgetExceeded | 7 | 107,233 | 4 |
| `novak-eq4-09` | 投影压缩 | output_budget_exhausted | 6 | 83,575 | 5 |
| `novak-eq4-09`+20k 计划 | 显式提高输出上限 | planner_budget_exhausted（仍只读） | 10 | 138,797 | 9 |
| `novak-eq4-10` | +重复读取诊断 | output_budget_exhausted | 5 | 71,592 | 4 |
| `novak-eq4-11` | 停滞指令（阈值逻辑错，未触发） | output_budget_exhausted | 5 | 71,473 | 4 |
| `novak-eq4-12` | 停滞指令（跨 respond 计数） | **awaiting_alignment** | 4 | **57,386** | 3 |

`novak-eq4-12` 的对齐问题直接引用了已投影的 Eq.(4) 与 Fig.3 图注：列出全部参数（p=4, Km/Kd=0.1, Kd·KI=2, S=1, k1=kdx=kdy=0.05 min⁻¹, ksy=k2ET/Kd=1 min⁻¹），指出正文未给 X(0),Y(0) 且 Fig.3c 为未转录图像，并额外指出 **S=1 与 S/Kd=1 的图注不一致、Kd 无数值**这一单位/尺度歧义，给出三条明确选项（补初值与约定 / 明确标注任意扰动只比较吸引子 / 只报告稳态与信号响应）。这是两篇文献都完成“首轮诊断”的标志：Gonze 缺初值、Novak 缺初值+单位约定，均未猜补。

注意：`novak-eq4-09` 的 20k 计划把调用用到 10 次仍只读，说明单纯提高输出上限无用；真正的杠杆是读取阶段的停止条件。测试：读取/项目/停滞相关 49 项通过；上下文 36 项、项目读取上下文 137 项通过。
