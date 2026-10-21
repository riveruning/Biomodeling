# 数值轨迹输入完整性（2026-10-18）

## 已复现缺陷

直接调用比较接口时，布尔值或数字字符串会被 float 转成普通数字；两份同样错误的数据可被判为 pass。两份同样倒序或包含重复时间点的轨迹也可通过。另外，validate_numeric 对成功状态但含 NaN/Infinity 的 SimulationResult 调用内部列解析时会抛 ValueError，原有构造失败报告的分支无法执行。

修复前后的可复现输入与结果保存在 artifacts/numeric-input-integrity-20261018/before.json、after.json。

## 改动

比较接口要求结果为 SimulationResult 或包含 times/values 的映射；时间点为非空、有限、严格递增的数字序列，拒绝布尔值与字符串。列容器必须为映射，每个列名须为非空字符串，列值须为长度匹配的有限数字序列。不再把列表键值对转换成映射，或把非字符串列名转成字符串。保留 FrozenMap 支持、单采样点及负起点。

validate_numeric 将不可用轨迹转换为 blocking 的 finite/fail 检查报告，不因 NaN/Infinity 或倒序时间点抛出原有解析异常；不修改原始结果，不裁剪、填充或修复数据。超出浮点表示范围的整数被视为非法输入，避免转换溢出。固定尺度和误差阈值公式未变。

## 验证

新增 38 项回归，分别覆盖实际/参考两侧的类型、时间网格、列名、长度、容器和有限性问题，非有限输出的失败报告，解析衰减轨迹的误差阈值、不可变映射及边界网格。完整测试 526 项，以 artifacts/numeric-input-integrity-20261018/acceptance.json 与 installed-pytest.xml 为最终证据。另做三个原检查点的脚本化离线兼容演练，仍不代表模型自主完成。

此次只修改数值验证模块及测试/说明，不调用外部模型，不改变独立测试集或历史评测成绩。严格检查无法恢复进入 SimulationResult 构造前已被其他调用者转换的信息；这不是对所有输入链路的形式化证明。
