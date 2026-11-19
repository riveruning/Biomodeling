# R09：稳态 FBA 产品接入

2026-10-27更新。原生 FBA JSON、受支持的 SBML FBC v2 导入、SciPy/HiGHS LP、数值检查、收据、报告与类型路由已有；现新增[可选的FBA Agent暂停与受控恢复](fba-recovery.md)。现有 ODE 路径保留。当前是有界稳态线性模型支持，不是任意代谢建模或从论文自动推断方法。

## 使用

```bash
python -m biomodel_agent fba-import examples/fba-v1/chain.json --store STORE
python -m biomodel_agent fba-run MODEL_REF --config examples/fba-v1/analytic-reference.json --store STORE
python -m biomodel_agent report RUN_REF --store STORE
python -m biomodel_agent fba-replay RUN_REF --store STORE
python -m biomodel_agent export RUN_REF --output BUNDLE_DIRECTORY --store STORE
```

`MODEL_REF`、`RUN_REF` 使用前一个命令返回的完整引用。公开教材模型可用 `fba-import tests/fixtures/fba/textbook.xml --format fbc` 导入，再用 `examples/fba-v1/textbook-reference.json` 比较原始活动目标。

Python：`fba.import_model(store, path, format='json'|'fbc')`，`fba.run(store, model_ref, settings)`，`fba.summary(store, run_ref)`。`fba.replay` 创建独立新执行，保留原输入配置及 replay_of；不会覆写原结果，也不属于原 Agent 的追加运行。

模型类型路由：`agent.run_model_agent(store, provider, model_type='fba', model_ref=ref, settings=...)`，或 `model_type='ode'` 转交既有 ODE 参数。未知类型明确拒绝，不把 FBA 编译成 ODE。服务配置准备好后可用独立命令 `agent-fba MODEL_REF --store STORE --config CONFIG`；本轮只有脚本 Provider 演练，没有调用真实模型服务。

## 输入和支持范围

原生格式示例见 [chain.json](../examples/fba-v1/chain.json)。schema_version=1、kind=fba、formulation=steady_state；代谢物显式声明 id、boundary_condition、compartment；反应显式提供固定化学计量及上下界；目标明确 maximize/minimize 和反应系数。`null` 上下界表示显式无界，不能省略后让求解器默认填0。通量单位标签必填，可明确写 unknown；未知单位不被提升为科学确认。

约束为非 boundary 物种的 S·v=0，保留所有显式上下界和活动线性目标。不会根据物种名称或胞外 compartment 自动跳过平衡。最大1000个物种、2000个反应、100000个非零项、8MB输入；拒绝布尔数值、NaN/Inf（原生JSON需用null表示无界）、重复JSON键、未知物种/反应、反向边界和多余字段。标识符与数值幅度也有明确上限。

FBC 导入仅接受 Level 3、FBC v2、strict=true、常数边界参数和固定显式正化学计量，先做 libSBML 一致性检查。保存原始 XML、库版本、活动目标、反应到边界参数的映射、单位定义和警告；每次加载从原始文件重算规范模型以核对来源。原始有向无穷边界会转为显式null；不同或部分缺失的通量单位不隐式换算。

拒绝动态规则、事件、初始赋值、函数、SBML额外约束、动力学律/修饰因子、可变化学计量、其他扩展包及过大的元数据。净化学计量为空的反应也不在本子集内。基因关联、化学式与注释保留在原始文件中，当前不解释为基因干预或化学有效性检查。FBC 包用于稳态约束模型，版本范围见 [SBML 官方说明](https://sbml.org/documents/specifications/level-3/version-1/fbc/)。

## 执行、检查与收据

方法仅开放 highs-ds/highs-ipm，超时0.1–60秒、默认15秒。子进程包含启动及求解的父进程墙钟限额，同时向HiGHS传time_limit；到时终止子进程并留失败记录。导入、来源重检与报告受大小/维度门控，不宣称包含在该LP子进程总时限内。LP选项与对偶返回值参考 [SciPy 官方接口](https://docs.scipy.org/doc/scipy/reference/optimize.linprog-highs.html)。

最优结果必须同时通过有限性、绝对与尺度化稳态残差、绝对与尺度化边界误差、目标一致性，以及对偶可行性/驻点与原对偶目标差检查。可行通量本身不等于最优；保存的 status 或 completion 字符串不能代替实际检查。可选标量参考在任务前冻结，比较阈值固定为1e-8×(1+|参考|)。只验证目标值，不宣称唯一通量向量或生物学正确。

求解前保存 fba_input 和 reserved fba_execution，返回后保存包含原始解/证书的 fba_execution_receipt 与 fba_run。读取结果核对输入、预留、返回收据和原始结果的绑定，再重算检查。数值环境和方法保留在输入/结果中。报告区分求解状态、完成资格、参考比较和 scientific_support=unknown，展示最大40项通量，完整向量在产物中。

不可行、无界、超时、求解器错误或证书/数值失败都保留为失败，目标或通量未知时保持null；CLI返回2。输入错误返回1，技术合格运行返回0。参考失败不会改模型、阈值或重跑。

旧FBA Agent入口保留inspect_fba、run_fba、finish及原日志。新任务显式 `recoverable=True` / `--recoverable` 后开放 `ask_researcher`、完整检查点与共享受控恢复；每条接续分支保留累计调用和一次科学执行预算。模型/配置不能更改，未知效果不自动重试。旧记录不补造快照，恢复诊断仍拒绝并显示不支持的计数null；独立LP重跑不是旧任务恢复。细节和跨分支/上下文预算边界见[新协议](fba-recovery.md)。

## 开发验收和限制

复用先前已保存的公开 COBRApy textbook 模型，原XML SHA-256为 `0abebb806ce94922d7c7eb6cadca8c2fa5c4500d0db8e119d3c6224ae59e212d`。测试夹具逐字节复制原开发文件，来源收据保留，没有读取独立评测集或新增评测论文。72物种、95反应的活动目标参考为0.8739215069684307，来自 [COBRApy 官方教材示例](https://cobrapy.readthedocs.io/en/latest/simulating.html)。两个HiGHS方法共享同一引擎，不作为两个独立引擎的证据。

解析解小网络、最大/最小目标、负向通量、boundary语义、不可行/无界、错误参考、超时、原对偶证书、来源和收据篡改、模型类型错误、科学预算以及打包重跑均有控制。最终完整回归1756项通过，其中新增FBA专项74项；10例开发演练、原配置独立重跑和19对象闭包重算通过。详细验证见 [R09 交付记录](../artifacts/r09-fba-20261025/README.md)。脚本 Agent 成功只证明工程接通；真实模型方法选择、论文提取、动态FBA/FVA/MILP、基因操作、通用单位推导、独立领域审核和真实性能对照均未完成。
