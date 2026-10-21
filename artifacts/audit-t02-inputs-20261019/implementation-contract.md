# T02：统一领域数值输入边界

Codex 已确认源码与上一轮748通过的验收版一致，独立测试已有63例（覆盖矩阵，不是63个独立缺陷）。当前37失败、26通过。DSH只实现这两个生产文件，不新增测试，不读取其他材料。Codex会独立执行现有与新增回归。

写入：src/biomodel_agent/domain/contracts.py、src/biomodel_agent/domain/serialization.py。先读本契约及两个文件，再直接小步编辑，不探索其他模块。

## 实现步骤

1. 统一原始数值类型检查：接受int/float（isinstance可保持numpy.float64等兼容），明确排除bool，拒绝str、Decimal和仅实现__float__的对象。不要先float()再检查类型，也不要调用任意对象的转换协议。float溢出的整数（如10**400）须抛ValueError，不泄露OverflowError。公共finite(value,what,allow_none)接口保留，参数/初值的None仍为显式缺失；其他原有标量字段仍须有限且满足原正数/排序规则。
2. 可提取一个小型严格数值转换helper供finite和输出列复用，避免重复的类型判据。copy_finite_map的数值叶检查也使用同一规则，但不改变合法整数叶的存储类型、不改变其既有递归能力。不修改通用FrozenMap，它仍须支持bool/string/None等元数据。
3. SolverConfig.max_steps仍是正整数，补拒绝bool；不把计数转float，不新增数值上限，因此10**400这类正整数计数依然允许构造（不表示实际执行这样的大预算）。
4. SimulationResult.values当前float(x)把bool/数字字符串转换成正常轨迹。改为严格类型转换，拒绝bool/string/无法表示为float的巨大整数；长度等已有检查保持。重要例外：结果列原生float NaN/Inf需要原样保留用于validate_numeric报告失败，不能在构造时全部禁止，也不能裁剪/修复成有限值。to_dict已有非有限拒绝逻辑不放宽。SimulationResult.times/elapsed_s仍需有限，不能用诊断例外放宽这些字段。
5. serialization._coerce_primitive的float分支调用上述公共有限检查，而不是复制另一套规则。SchemaError仍是JSON入口的对外错误；不要改变其他JSON类型、字段、元组、可选值、computed hash协议，不迁移schema。

## 验收边界

- Parameter.value、State.initial、Compartment.size、InputSegment.time、ExperimentSpec.times、SolverConfig.rtol/atol/timeout_s、SimulationResult.times/elapsed_s/values以及数值映射，Python/JSON均不能通过bool、数字字符串或巨大浮点溢出整数。
- max_steps在两个入口拒绝bool/float/string/None，合法正整数保持。
- 合法整数/浮点、None缺失值、pickle和JSON往返不变；通用元数据的bool不受影响。
- 结果列NaN/Inf的诊断链保留；数值验证仍返回blocking fail，不能序列化成成功证据。
- T01真实产物的合法实验/求解器/结果仍可读取；测试有从此前产物摘取的小型兼容fixture。
- 不改旧测试、不加依赖、不改科学表达式/门控/单位规则、不运行shell/测试/网络/子Agent。只用明确路径的read/edit/write工具。

交付简述实际helper接口和限制。不要大量重写原模块或生成长篇分析，Codex负责审查与验收。
