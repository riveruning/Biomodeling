# 六类建模的当前接口

后续增量（2026-10-30）：[统一任务入口](research-tasks.md)已接入六类工作流；新增 `agent-typed --recoverable` / typed v2，支持原任务暂停、回复和确认返回认领。旧默认 v1 保持不支持恢复，未知效果不重试。以下正文保留首版范围及其验收。

2026-10-27 用户核对六类清单后要求“全补了”，本次为剩余四类接上实际执行架构。原有 ODE 与稳态 FBA 保留各自协议；新增类型有原生 JSON、独立检查和真实计算，不转换成 ODE。以下是有明确边界的首版，不是任意生物论文的自动建模能力。

| 类型 | 可执行范围 | 尚未覆盖 |
|---|---|---|
| ODE | 原有小规模 SBML ODE、RoadRunner、来源与数值检查 | 任意方程/论文语义与科学适用性认证 |
| FBA | 原生 JSON / strict FBC v2 稳态 LP、HiGHS、原对偶及参考检查、显式 v2 恢复 | 动态 FBA、FVA、MILP、基因干预 |
| 随机反应 | 充分混合、整数分子数、0/1/2阶质量作用反应，Gillespie direct SSA，固定种子有限轨迹集合 | 任意倾向函数、时变驱动、延迟、空间随机反应；浓度常数到随机常数的推断转换 |
| PDE | 1D 单个非负标量场 `u_t = D*u_xx - decay*u`，均匀网格、常数系数，零 Dirichlet / 零通量 / 周期边界 | 多维、非线性、多场、复杂网格、对流与任意边界 |
| 布尔网络 | 显式规则、静态钳制，同步更新或每轮随机顺序逐节点更新 | 自动推断规则、概率布尔模型、通用吸引子枚举 |
| 个体模型 | 二维格点细胞，死亡、迁移、分裂、空间占位/接触抑制、亲子身份 | 连续位置、细胞力学、组织特定规则、与化学场耦合 |

## 使用

每类各有 [示例模型及设置](../examples/model-types/README.md)。它们是合成开发控制，不是四篇论文或真实科研成绩。以布尔网络为例，替换文件名即可运行其余三类：

```bash
.venv/bin/python -m biomodel_agent model-types
.venv/bin/python -m biomodel_agent typed-import examples/model-types/boolean.json --store runs/native
# 将输出 ref 填入 MODEL_REF
.venv/bin/python -m biomodel_agent typed-prepare MODEL_REF --config examples/model-types/boolean-settings.json --store runs/native
# 将输出 ref 填入 INPUT_REF
.venv/bin/python -m biomodel_agent typed-status INPUT_REF --store runs/native
.venv/bin/python -m biomodel_agent typed-execute INPUT_REF --store runs/native
# 将输出 ref 填入 RUN_REF
.venv/bin/python -m biomodel_agent report RUN_REF --output runs/native-report.md --store runs/native
.venv/bin/python -m biomodel_agent export RUN_REF --output runs/native-bundle --store runs/native
.venv/bin/python -m biomodel_agent typed-replay RUN_REF --store runs/native-bundle
```

`typed-run MODEL_REF --config ... --store ...` 合并准备和执行。每次该命令及 `typed-replay` 都是显式新计算；再次 `typed-execute INPUT_REF` 返回计划原结果。重跑使用原模型/设置/种子，生成新计划、冻结当前环境并记录原运行；环境变化时不宣称位级可重复。

Python 接口为 `typed_models.import_model/prepare/execute/summary/result/replay`。`result` 返回 `(frozen_input, native_model, returned_result)`，完整轨迹保存在返回收据。导出闭包可在独立 `ArtifactStore` 中读取、检查和显式重跑。

## 输入与计算语义

公共字段为整数 `schema_version: 1`、明确 `model_type`、`id`、`units`，可加有限单行 `note`。拒绝未知字段、JSON 重复字段、非有限值、用 Boolean 冒充整数、未知变量和超界设置。原始 JSON 字节另存 blob；读出时核对原文件与结构化载荷一致。不创建虚假的文献来源或科学 claim；单位是操作者声明，维度与科学适用性仍待核验。

- **随机：** `species: [{id,initial}]`、`reactions: [{id,reactants,products,rate}]`、`rate_convention: stochastic_combinations`。状态单位必须为 `molecule_count`。倾向为 `rate * product(comb(n_i, reactant_i))`，例如双分子同种反应使用 `n*(n-1)/2`。`rate` 是随机常数，零阶是每时间事件率，不能直接套用确定性浓度模型常数。从0到 duration 均匀采样，取最后事件后的右连续状态；保存采样轨迹、各轨迹反应总数、均值和 `ddof=0` 描述方差。所有轨迹共享 `max_events`。
- **PDE：** `length,diffusion,decay,boundary,initial`。D 解释为声明的空间单位平方/时间，decay 为时间倒数，不自动换算。`dirichlet_zero` 使用包含端点的节点网格，初值端点必须为零；`no_flux/periodic` 使用守恒的单元中心网格，中心位置不代表边界。SciPy 稀疏矩阵指数推进空间离散系统；空间误差仍需独立检查。保存位置、时间、场值与积分质量，不裁剪负值来伪造通过。
- **布尔：** `nodes: [{id,initial,rule}], update, clamps`；时间为 `sweep`。只解释变量、`True/False`、`and/or/not` 和括号，不执行 eval、函数、属性或下标。同步规则读同一旧状态；`random_sequential` 每轮洗牌自由节点、逐节点读最新状态，每节点更新一次。钳制必须与初值一致。保存全部状态和顺序；仅同步模式识别观察到的周期，异步重复状态不当作吸引子证据。
- **个体：** `width,height,boundary,initial,probabilities`，单位为 `sweep/lattice_site`。初值为位置数组，ID 按输入顺序从0分配。每轮洗牌开始时存活的细胞；先死亡判定，存活者再判定迁移，最后判定分裂。在空的上下左右邻格中均匀选择，周期窄网格的同一邻格不重复加权；封闭边界不越界。新生细胞下一轮激活。三种概率均为每次激活的概率，不能当连续时间速率。保存每步位置、激活顺序、事件及亲子身份；空间占用阻断分裂。

## 预算与持久记录

公共 `timeout_s` 为0.1–60秒，默认15秒，限制计算子进程启动、导入和计算的墙钟时间；结束后最多另用两次各一秒清理。它不是 provider 或全研究期限。默认和组合限制如下：

| 类型 | 其余默认设置 | 上限 |
|---|---|---|
| 随机 | seed=0, duration=10, samples=51, trajectories=16, max_events=100000 | 32种、64反应；512轨迹、1001采样、共100万事件；输出状态元素≤50万、分子数≤10亿 |
| PDE | duration=1, samples=21 | 3–256网格值、1001采样、输出≤25万元素；D*T/dx²与decay*T均≤100万 |
| 布尔 | seed=0, steps=20 | 64节点、1万轮、状态总元素≤25万；每条规则AST≤100节点 |
| 个体 | seed=0, steps=20, max_activations=100000 | 每边2–32格、256轮、共20万激活；格点数×帧数≤10万 |

输入、源码/环境指纹、预留执行、返回收据和运行分别保存。每计划一次预留，同 store 文件所有权阻止并发；预留后没有完整运行记录时保守阻断，不因删除索引记录而重算。这里只保证当前本地 store 历史，旧导出副本不是跨 store 全局幂等账本。读取结果重新计算类型检查，不信任保存的“pass”；导出含原始文件和全部依赖。

## Agent 与验收边界

`run_model_agent(..., model_type='stochastic'/'pde'/'boolean'/'individual', model_ref=..., settings=...)` 及 `agent-typed --model-type ...` 提供明确路由。动作仅 `inspect_typed/run_typed/finish`；一次科学计算纳入共享持久调用账本，模型/设置/种子由操作者冻结。本次没有真实 provider 调用。新增四类尚未支持 Agent 暂停恢复，诊断明确拒绝且不伪造零计数。原有 ODE/FBA 恢复保持原样。论文到原生模型的自动整理、类型自主选择、活跃模型修改与估参未接通这四类。

类型检查分别覆盖：整数/初值/时序/描述矩/最终化学计量平衡；空间网格/边界/非负与质量收支；逐步规则/顺序/钳制和周期；逐帧身份/占位/事件/亲子与人口。它们不能代替科学真实性或参数来源审核。

开发检查使用不同类型的独立参考：移入-死亡过程解析均值/方差、化学计量守恒；三种边界的 PDE 连续解析模态与二阶空间收敛；手工四状态布尔周期；细胞确定死亡、纯迁移、饱和抑制及新生细胞延后激活。有限随机样本只按固定种子和预设误差界核对，不证明整个分布。没有用 Goodwin 代替整体验证。

工程证据及报告见 [本轮验收](../artifacts/multitype-modeling-20261027/acceptance.json)。独立领域审核、真实大模型成绩和新独立论文评价均未开展。运行 `scientific_support=unknown`、`comparison=not_requested`；解析/手工控制不冒充普通运行的文献参考。T15 广义目标和 R11 科学评价仍未结项。

方法依据：[Gillespie原始算法](https://pubs.acs.org/doi/10.1021/j100540a008)、[SciPy expm_multiply](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.expm_multiply.html)。[Mesa激活文档](https://mesa.readthedocs.io/latest/tutorials/2_agent_activation.html)仅作为更新顺序的设计参考；个体规则由本项目明确规定。实际依赖现有 NumPy/SciPy，自实现上述有限随机/离散规则，没有安装或假称执行 GillesPy2、Mesa 或通用 PDE 软件。
