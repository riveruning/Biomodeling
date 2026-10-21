# Python 与 JSON 数值边界一致性（T02）

2026-10-19。任务来源：[优化任务队列](optimization-backlog.md)。

## 问题与行为

此前 Python 的 Parameter('k', True) 会变成1.0，数字字符串会被float()隐式转换，相同JSON标量却被拒绝。时间、求解器、步数和结果列也存在类似差异；结果列经FrozenMap重建时，JSON入口同样可能接受布尔值/数字字符串并将其归一化成正常轨迹。

现在统一领域数值类型边界：

| 场景 | 当前规则 |
|---|---|
| 参数、初值、区室大小、时间、求解器浮点配置、耗时、数值映射 | 接受有限int/float，拒绝bool、数字字符串、Decimal、仅实现__float__的对象及无法表示为float的整数 |
| 显式缺失参数与初值 | None保持缺失，不补值；其他标量的None限制沿用原契约 |
| max_steps | 仍为正整数，拒绝bool、float、字符串；不转float，不新增计数上限 |
| SimulationResult结果列 | 严格数值类型；保留原生float NaN/Inf供数值验证诊断，不修复/裁剪成有限值 |
| 通用FrozenMap元数据 | bool、字符串、None仍可保留，未套用数值字段的规则 |

异常结果列保留不等于数值证据通过：validate_numeric仍报告blocking fail，to_dict仍拒绝非有限值。结果时间与elapsed_s没有获得该诊断例外。结果时间网格的顺序检查仍由原有验证器处理，未改变科学门槛或单位规则。

Python领域输入对非法值返回ValueError；JSON codec仍返回SchemaError。contracts.strict_float为有限数值转换的共享入口，finite保留原签名和None语义；数值映射和JSON浮点分支复用它。输出列使用允许非有限诊断值的独立转换路径。

合法int/float、pickle、JSON往返保持原行为，合法数值映射中的整数叶仍按原类型保存。isinstance兼容原本符合int/float判断的类型，不承诺接收所有NumPy或第三方数值类型。此前依赖隐式字符串/Decimal转换的调用者需要先显式整理输入。

## 实施与验证记录

DSH在任务卡限定的两个文件中完成主体实现：domain/contracts.py、domain/serialization.py。监督结果为ready_for_review，未作为自动验收；Codex独立审查及测试，未修改这次DSH生产代码。原有测试文件保持不变。

独立验收包含63个检查（是字段/类型覆盖矩阵，不是63个不同缺陷）：实施前37失败、26通过，覆盖隐式转换、整数溢出、合法值、None、元数据、异常输出及历史读取。定向回归189项通过；最终完整Python回归811项通过，125.81秒，无失败、错误或跳过。

历史兼容样本来自T01已保存的真实负值模拟，实验、求解器、结果与原产物逐字段一致，原对象和输入哈希校验通过。小型固定样本位于benchmarks/inputs/contracts-compatibility/t01-negative-run.json，测试只读该样本，不打开或改写历史存储。

源码快照、DSH交付、差异、失败基线、实际行为探针、兼容性校验及最终完整回归记录见 [T02验收记录](../artifacts/audit-t02-inputs-20261019/acceptance.json)。

本次不迁移旧产物，不推断已经被归一化为数值的旧输入原本是否来自bool/string，不实现语义审核或崩溃恢复。没有增加依赖或运行新的产品模型评测；DSH使用现有开发通道，未发送c13/c18材料或重试此前被拒绝的回归。
