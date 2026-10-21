# T08d-1：原生系统对照的离线接口

本轮搭建共同任务包、Codex JSONL/产品记录导入、原会话关系与统一报告。代码位于 `benchmarks/development/system_comparison.py` 和 `native_capture.py`。采用独立协议 `native_system_comparison_v1`，运行时明确为BioModel Agent、Codex-sol、Codex-astra；没有把Codex改名为旧协议的generic_tool_agent。

**当前是离线接口，不包含真实Codex推理启动器或提交程序的科学执行器。** 服务、认证、实际运行环境与材料外发仍待配置；`command-plan`只输出提议命令。实际产品比较与相同底层模型的框架归因继续分开。

## 共同任务和环境差异

当前只复用既有decay、translation、logistic三项已见合成任务，含5个阶段，一个同源家庭，每系统2次重复，共30条。原开发材料、标注和旧成绩保持不变，真实论文数0、领域审核pending。任务清单冻结实现哈希、科学工具指纹、材料、候选模型设置和共同活动墙钟预算，当前默认180秒。

三个系统ID为biomodel、codex-sol、codex-astra。Codex候选沿用用户的gpt-6.1-sol和暂按“Atra”理解的gpt-6-astra，均未测试可用性；产品底层模型暂未选定，版本字段保持null。它们是待配置候选，不是已运行身份。新协议允许显式记录外部回放，但不能把evidence_kind改成live_model或把未见材料/正式论文塞入本版开发协议。

| 层 | 当前处理 |
|---|---|
| Agent可见输入 | 每阶段仅source.txt、experiment.json、TASK.md，当前原文/实验对三系统相同；未来澄清只在对应阶段释放 |
| 操作者资料 | 完整清单含私有规则，parent与preparation文件放在runtime外；运行环境只应挂载当前runtime |
| 交付接口 | 最小文件清单、问题原文和自报状态；支持原生Python程序、SBML、轨迹、报告、引用文件，不要求Codex生成产品ModelDraft |
| 工具 | 产品为受控科学工具，Codex为原生编程环境；工具数语义不同，不能直接视为同等调用量 |
| 预算 | 同一案例初始与澄清累计活动墙钟；新会话也不重置。原生模型调用量不可从JSONL完整推得，保持null |
| 身份 | 配置和事件中的session是归属记录，不能认证提供方、实际模型、CLI版本或来源语义 |

包导出只是内容投影，本身不是OS沙箱。真实启动时还需冻结CLI二进制/版本、独立会话存储、模型服务和凭证接入、代码运行依赖、权限/联网范围与外部总期限监督；本轮没有为这些未选配置制造“已验证”声明。

## 原生事件与会话恢复

OpenAI官方非交互说明使用`codex exec --json`输出JSONL，并支持`codex exec resume <SESSION_ID>`。`--ephemeral`不保存会话文件；本轮的命令计划保留持久会话，恢复只使用明确的ID，不使用`--last`。[官方说明](https://learn.chatgpt.com/docs/non-interactive-mode)、[CLI参考](https://learn.chatgpt.com/docs/cli/reference)

已只读核对本机codex-cli 0.163.0的version/exec help/resume help。未调用推理命令，未读取认证文件。计划通过stdin提供TASK.md并要求写完后关闭输入，避免旧脚本中继承stdin的历史问题。`--ignore-user-config`和`--ignore-rules`用于受控环境，但不能代替文件隔离或认证范围配置。

JSONL解析采用显式、有界的单会话单turn配置：

- 严格UTF-8和JSON，拒绝重复键、非有限数、非法顺序、多线程/turn混入、重复完成项及未知事件/工具类型；原始字节另存，不跳过错误行后宣称成功。
- 支持thread.started、turn.started/completed/failed、error和常用item生命周期；错误、未完成item及截断末行保留诊断。版本增加新类型时须显式更新配置并重新冻结，不能静默丢弃。
- 最多8MB事件、5000行、单行256KB。usage缺失保持null；已报告的输入/缓存/输出/推理token分别保留，缓存不重复加到输入量。
- 原生command_execution/MCP/web事件只计已完成原生工具项，不能据此证明执行了指定科学模拟；execution_declared和execution_observed分开，原生科学执行在独立检查前为null。

继续同一任务时，清单/实现/材料/系统/案例/重复号及父记录摘要必须一致，父记录须已完成交互并等待输入；原生恢复返回另一session会被拒绝。失败父记录不会自动改为fresh。显式fresh需要另一session，仍继承累计时间，在报告中不能得原会话恢复信用。不同案例、系统或重复共用同一原生session时，报告拒绝受影响记录，避免把上下文污染当独立运行。

产品适配器读取原domain_agent的v1归属记录，并核对共同材料、旧严格协议、证据类型、模型声明及父session引用。源记录原样保存，数值/来源审核不会因此提升为通过。现接口不是对任意伪造文件的身份认证；外部记录仍需可信采集器和独立复核。

## 交付文件和失败保存

Agent可在/delivery写入以下最小清单：

```json
{
  "state": "awaiting_input",
  "execution_declared": false,
  "question_texts": ["What is the initial P value?"],
  "artifacts": []
}
```

产物项字段为path、role、sha256，角色允许program/trajectory/sbml/report/citations。程序保持不透明字节，本接口不执行。路径拒绝绝对路径、上级跳转、非规范别名、符号链接和非普通文件，最多64份、总量8MB。原清单与事件流留存；部分清单失败时已验证的早先文件继续保存，不能假称完成。

没有delivery.json时，保留原生最终消息，标为not_submitted并等待输出审核，不自动判科学失败。进程非零退出、超时、服务错误、缺终态、事件非法和交付不完整分别留档。导入不产生请求，不重试、不续算、不覆盖目录；被拒绝的源capture文件也不会被删除。应导入已停止写入、由采集器持有的capture目录；路径检查不是对恶意并发文件系统变更的完整沙箱。

协议只接收scripted_control或external_recording。后者表示操作者提供的回放，**不等同已认证的真实模型证据**；所有标准化审核字段固定为unreviewed/unknown，不能通过字段改成“专家通过”。原生模型自报、命令文本和产物存在均不足以确认科学正确。

统一报告沿用T08d-0的固定分母与三值统计：正确完成、预算内完成、缺口识别、针对性追问、无依据执行/完成、过阻断、原会话恢复分开。未知不能算已确认失败或安全，描述范围不是统计置信区间。共同墙钟是按capture声明复算的预算，不是本离线导入器已经实施的运行时硬期限；缺usage和缺记录仍保留分母与未知量。

## 使用

先生成操作者清单：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python benchmarks/development/system_comparison.py \
  plan --output /tmp/new-system-plan
```

导出某个当前阶段并查看命令计划：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python benchmarks/development/system_comparison.py \
  prepare --manifest /tmp/new-system-plan/manifest.json \
  --trial codex-sol--translation--0--initial --output /tmp/new-system-task

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python benchmarks/development/system_comparison.py \
  command-plan /tmp/new-system-task
```

已有、来源范围明确的capture目录包含events.jsonl与可选delivery.json/产物后，可离线导入：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python benchmarks/development/system_comparison.py \
  import-codex --task /tmp/new-system-task --capture /path/to/capture \
  --exit-code 0 --elapsed-seconds 12 --output /tmp/new-system-import
```

超时用`--timed-out`，缺少进程退出结果时不填exit-code；已经产生的记录和文件仍保存。正常导入退出0，保存了操作失败的导入退出1，整批/身份/路径等拒绝退出2。score时已计划的非法记录保存invalid_record并退出1，重复或未计划ID拒绝整批；退出0只表示接口操作完成。

澄清任务需要`prepare --continuation resumed --parent /path/to/parent/record.json`；若有意开新会话则明确fresh，仍提供父记录供预算累计。`check TASK_DIR`可检查准备失效。产品可使用`import-product`或同名Python接口，输入原v1清单/记录、session-ref及恢复时的parent-session-ref。score读取本协议清单和标准化record列表，新建结果目录。

## 验证与当前限制

新增67项专项检查通过，覆盖会话/材料/配置绑定、跨案例session污染、预算继承、缺usage、错误事件、截断输出、部分文件、路径/FIFO/链接、CLI与分母。开发中的三项边界失败及修复原样保存。

完整确认1427项通过；首轮1426通过/1项V01失败及后续单独诊断、唯一旧测试改动详见下方记录。

[30阶段离线演练](../artifacts/audit-t08d-1-system-comparison-20261020/offline-replay/engineering.json)导入10份T08c历史产品记录和20份明确编造的Codex事件fixture。18项完成仍待审核，12项初始缺口另计；8条Codex“恢复”只是模拟ID关系。另只读核对4条历史产品原生父关系，未新增实际模型会话或科学比较执行；dummy程序从未运行。历史产品记录只用于接口映射，并非按新任务包重新执行后的同题成绩。

[绝对路径隔离探针](../artifacts/audit-t08d-1-system-comparison-20261020/isolation-probe-final/result.json)在无网络namespace中只运行固定Python检查：当前三份输入可读且只读，操作者canary/完整清单/项目源码不可读，凭证环境变量未继承。首次受当前沙箱限制无法创建网络namespace，按授权在宿主重跑；初次宿主探针有两条相对路径，已明确保留为不足证据，并用绝对路径及宿主正向存在检查修正。**该结果只证明这次离线探针的挂载视图，未证明真实Codex启动环境已安全隔离。**

完整回归、源码差异、工具帮助和所有失败见 [T08d-1归档目录](../artifacts/audit-t08d-1-system-comparison-20261020/)。产品、v1/v2评分器和执行传输不改。本轮唯一既有测试修改为V01：首轮完整1426通过、领域组旧0.6秒工具准入断言失败；原三项单独运行通过。随后将该测试改为工具确认进入后，以适配器局部时钟跳变模拟总期限耗尽，继续断言真实终止耗时、timeout、执行标记、工具计数及wall_deadline。三组确定性控制通过，不再依赖冷启动在0.6秒内完成；不表示生产期限放宽或性能提升，逻辑耗时也不能当性能数据。原始失败和前后真实产物均保留。

下一阶段仍需独立领域审核与具体服务/授权范围；再实现受监督的真实采集、独立科学执行/评分，进行先导对照。本轮不新增评测论文、不读取独立评测集或c13/c18材料，也不重试此前被拒绝的外发。
