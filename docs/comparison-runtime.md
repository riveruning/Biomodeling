# R11b-1：受信任控制进程采集与轨迹侧车

2026-10-26。本轮在既有系统对照的“命令计划/事后导入”之间补充本地控制进程监督，并把哈希绑定的轨迹交付接到 R11a 数值参考。工具为 `benchmarks/development/comparison_runtime.py`。产品源码、原系统协议、评分器和历史结果均未修改。

**本轮不是 R11b 真实系统先导完成。** CLI只启动内置可信脚本控制和固定隔离探针，不启动模型服务、Codex CLI或提交程序。实际模型调用0、提交程序执行0；新采集不认证模型身份，也不代签来源/领域审核。

## 已实现的进程行为

`supervise` 为受信任操作者命令提供 argv 启动、显式工作目录、最小环境、stdin 写入与EOF、stdout/stderr分别保存及共享字节上限、单个进程采集墙钟期限、非零退出与启动失败记录。没有shell字符串拼接，不继承调用方凭证环境变量，不估算token或费用。

进程在新会话/进程组启动。超时或输出超限会杀死同组进程并保存已收到的字节；普通领进程退出后也清理同组后代，避免继承管道的子进程让采集无限等待。stdin背压与输出读取共用同一截止时钟；写入后关闭stdin。`stdin_bytes_written`是成功写入管道的数量，不证明子进程读过或理解了输入。

每个输出目录必须全新。执行前先保存 `reservation.json`，完成后原子保存 `receipt.json`，记录命令/输入摘要、采集源码摘要、实际耗时、退出码、输出字节数/哈希和终态。启动失败也留收据；已有目录或只有预约的目录不自动重试。这个保护只针对同一输出目录，**不是跨目录/整项研究的防重复认领账本**。父进程意外中断时若没有终态收据，结果保持unknown，不把未知执行当未执行。父进程硬崩溃/被SIGKILL后也不保证子进程停止，仍需后续外部监督和持久认领。

支持 succeeded、process_error、launch_error、timeout、output_limit、supervision_error。读取前重检预约/收据字段、终态、耗时、stdin计数及原始日志绑定。原始字节保存为 stdout.bin / stderr.bin，文本解析另做，不因截断或错误行宣称完整结果。

这只是受信任进程的操作监督，**不提供文件/网络隔离**，也不保证杀死故意脱离会话的恶意后代，不是cgroup、内存/磁盘总量配额或通用不可信代码沙箱。超时包含启动后的监控及清理时间；受阻的内核/文件I/O没有硬实时保证。采集耗时不含准备、评分和全部导入开销，不能拿它声称整任务总期限已由本工具实施。原系统导入器继续累计实际提供的采集耗时，未来真实runner仍须覆盖完整活动生命周期。

## 与旧导入器衔接

`fixture`检查原冻结任务、材料和剩余预算，仅接受codex_cli输入形式的 `scripted_control`。它启动明确写有“no model inference”的可信Python控制脚本，实际读取stdin至EOF、输出有限JSONL，并在正常控制中交付state=incomplete、execution_declared=false的空清单。没有生成“模型成功完成”的控制成绩。

进程原始stdout作为 events.jsonl，退出码/超时标志/采集墙钟直接来自新收据，再传给旧 `import_codex`。标准化记录的来源/数值审核仍unknown、recording_authenticated=false、execution_observed=null。输出超限在进程收据中单列；原协议没有该状态，归一化记录保留process_error，不能丢掉对应收据。

## 轨迹核验

`trajectory-check`先复核当前阶段任务，再用旧 `native_capture.read_delivery` 读取清单：路径必须是受支持相对路径、角色为trajectory、文件哈希一致，并受原文件/总量预算约束。它只读取JSON，不执行program、SBML或任何文件代码。

在可比较阶段复用 R11a `numeric_diagnostic`，严格比较源绑定闭式解、时间单位、网格、变量、有限值和固定尺度/阈值。translation/logistic未澄清初始阶段保持not_comparable，不套用澄清后的答案。记录当前准备包、交付清单、轨迹、核验器和数值协议的摘要。

结果无论吻合与否都保持科学支持unknown、独立审核pending、formal_task_score=not_assigned、execution_observed=null。轨迹吻合只说明文件数值与参考一致，不能证明提交程序被执行，更不能证明真实模型正确完成任务。没有回写旧assessment或改动评分规则。

## 隔离探针的实际失败

`probe-sandbox`只运行固定受信任Python载荷，要求bwrap创建独立命名空间、只读/usr和库、空临时目录、清空环境；检验网络命名空间变化和本地测试canary不可见。不连接外部地址，不读取真实私有材料。

当前执行环境实际返回：

```text
bwrap: loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted
```

最终原始stderr和收据保存于 `artifacts/r11b-runtime-20261026/sandbox-probe-final/`。这是当前环境拒绝命名空间设置的实测结果，不等于已定位到底层主机配置问题。没有去掉网络隔离重试、使用凭证、申请更高权限或退回普通进程执行提交程序。即使将来探针通过，也只是可用性检查，仍不能单凭它认证完整恶意代码隔离或真实模型运行环境。

## 使用

先按[现有系统协议](system-comparison.md)生成冻结清单和某个当前阶段任务目录，然后：

```bash
# 明确的可信控制，绝不启动模型：
.venv/bin/python benchmarks/development/comparison_runtime.py fixture --task TASK_DIR --scenario success --output /tmp/control-capture
.venv/bin/python benchmarks/development/comparison_runtime.py fixture --task TASK_DIR --scenario timeout --output /tmp/timeout-capture
# 固定命名空间探针；当前环境应保留失败，退出1：
.venv/bin/python benchmarks/development/comparison_runtime.py probe-sandbox --output /tmp/runtime-probe
# 只核验已授权交付目录中的结构化轨迹：
.venv/bin/python benchmarks/development/comparison_runtime.py trajectory-check --task TASK_DIR --capture CAPTURE_DIR --trajectory trajectory.json --output /tmp/trajectory-diagnostic
```

fixture还支持process_error/output_limit；结果正常返回0，已保存操作失败或数值不吻合/不可比较返回1，输入拒绝返回2。输出目录需全新。进程API是操作原语而非通用模型执行授权接口；CLI不接受任意外部命令。

## 本轮验证与未完成项

36项专项包括真实stdin/EOF、环境变量不继承、部分输出、非零退出、启动失败、实际超时、同组后代停止、stdin背压、共享stdout/stderr限额、预约/收据/字节篡改、已存在未知预约不重试、旧导入器使用实测值、脚本身份不可升级、任务/交付哈希、未澄清阶段和轨迹单位/数值等控制。另实际运行四种可信脚本场景、一次只读轨迹诊断及当前隔离探针；部分固定分母报告保留29条缺跑记录。它们全部不是LLM或独立领域效果证据。

完整回归与代码快照见 `artifacts/r11b-runtime-20261026/acceptance.json`。本轮只完成 R11b 的第一项采集/核验工程增量。仍待：实际模型与服务/材料范围配置、可用且经核查的运行隔离、完整活动预算监督、持久任务认领与崩溃恢复、可信采集来源、提交程序执行器，以及实际独立审核与真实系统先导。R11c同模型消融和R11d正式未见评价不由这些控制完成。

既有材料隔离与外发拒绝不变；不访问独立评测集或c13/c18，不启动网络/模型请求，不推送/发布，旧biomodel自动化保持暂停。
