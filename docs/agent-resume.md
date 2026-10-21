# 研究者澄清与任务恢复

2026-10-17。本轮补全了“Agent 发现信息缺口 → 保存问题 → 研究者补充 → 从原任务继续”的流程。它是工作流能力建设，不是新的模型性能评测；未读取或接入 DSH 的独立测试集。

## 已实现的行为

- `run_agent` 在开始时冻结实验、求解器、观测、允许的机制空间、比较参考和总预算。比较参考保存在内部，不进入模型请求。
- 检查点保存可恢复的消息、事件、草稿、模型、运行状态，以及累计调用、修订和重试次数。
- `resume_agent` 只接受 `awaiting_input` 的任务。恢复沿用剩余预算、底层模型、服务地址和生成设置；不能通过补充文字扩大机制空间或改变实验配置。
- 研究者回答按原文保存为不可变记录及可引用的来源，记录回答者、待回答的问题、父任务和此前来源。回答者名称由操作者提供，系统不验证身份。
- 补充信息的证据类型固定为 `human_convention`，语义支持仍为 `unknown`。它代表研究者为当前分析提供的声明，不会自动成为原论文已经证实的事实。
- 补充后重新验证草稿。已明确声明的初值或参数必须引用对应补充来源，并与补充数值一致；不接受默默沿用冲突旧值。当前这条检查要求显式匹配声明，不自动推导补充值的单位换算。
- 来源先后按不可变的补充关系确定，不依赖引用表的遍历顺序。只解决被明确回答的目标；其他缺失或矛盾继续追问。
- 在已执行模拟之后恢复，仍禁止修改模型或再次执行同一冻结评估。旧结果保持原样。
- 报告显示父任务、检查点、剩余调用次数、问题、回答及其归属。导出的完整任务包可以在新的存储目录继续恢复。

## 使用方式

先按现有 `agent` 命令运行任务。状态为 `awaiting_input` 时，用 `report` 查看问题，再将回答保存为 UTF-8 文本。例如，材料缺初值时，回答可写成 `Initial x = 2.`。

```bash
.venv/bin/python -m biomodel_agent report AWAITING_RUN_REF --store artifacts/my-agent

.venv/bin/python -m biomodel_agent agent-resume AWAITING_RUN_REF \
  --store artifacts/my-agent \
  --reply-file researcher-reply.txt \
  --respondent 'Researcher A' \
  --base-url https://YOUR_PROVIDER/v1
```

需要原有的 API key 环境变量，默认名称为 `BIOMODEL_API_KEY`；可以通过 `--api-key-env` 指定变量名。恢复命令自动继承模型及 JSON 模式等设置，不需要重新输入。服务地址必须与原任务匹配。配置和产物不保存 key、HTTP headers 或 transport。

Python 接口：

```python
from biomodel_agent.agent import resume_agent

resumed_ref = resume_agent(
    store, provider, awaiting_run_ref,
    'Initial x = 2.',
    respondent='Researcher A',
)
```

输出是新的 `agent_run` 引用，旧任务和原始材料保持不可变。重复使用同一个父引用可创建明确记录的替代分支；每条分支继承父任务已经使用的预算。这不是跨分支的总费用限额，也不会保证重复运行免于再次调用服务。

## 已验证的结果

完整 Python 测试：**303 项通过**，其中新增 30 项恢复相关测试。覆盖：

- 重开存储后保留消息与历史，最终实际执行 ODE 求解；
- 模型主动追问及校验器阻断后的恢复；
- 调用、修订和重试预算跨恢复累计；
- 部分、无关和不确定回答不会清除其他缺口；
- 旧的可执行草稿必须重新检查，不能绕过补充信息；
- 实验、观测、求解器和内部比较参考保持冻结，参考不进入模型请求；
- 不允许借恢复扩大机制空间、重复执行或修改已评估模型；
- 导出包可恢复，替代分支保留各自来源；
- 命令行继承模型与 JSON 模式，key 不写入产物。

另外运行了两个独立 Python 进程的合成示例。第一个进程在缺初值时暂停并退出，第二个进程补充 `Initial x = 2.`，恢复并完成 `dx/dt = -0.4*x` 的实际模拟。累计 4 次脚本动作，原任务未改变；三个时间点与解析解的最大绝对误差约 `6.36e-7`。科学状态仍为 `not_verified`，语义支持仍为 `unknown`。

复现命令（输出目录必须未存在）：

```bash
.venv/bin/python benchmarks/development/demo_agent_resume.py \
  --phase start --output artifacts/my-resume-demo
.venv/bin/python benchmarks/development/demo_agent_resume.py \
  --phase resume --output artifacts/my-resume-demo
```

该示例使用预设动作验证存储、状态机、证据和模拟链路，不调用网络或 LLM，不代表自主理解、提取或追问能力的评测成绩。

## 范围与限制

当前恢复针对已正常保存的追问暂停点，不保证进程在任意指令处崩溃后的自动恢复。旧版本不完整的检查点会明确拒绝恢复，仍可读取旧报告。回答最多 8000 字符、回答者名称最多 200 字符，每条任务分支最多恢复 8 轮，并受原总调用预算限制。

缺失和冲突识别沿用有限的显式文本规则，主要面向英文数值声明、时间单位和 ODE 公式歧义；不能承诺理解任意语言或论文中的复杂论证。研究者声明和文献事实是否一致，仍需独立审核。数值模拟完成不等于论文复现成功。

下一步适合冻结本版，在独立测试上同时验证正确完成、错误继续、过度阻断和澄清后恢复；通用工具 agent 与同底层模型的对照应采用相同材料和明确预算。此处为后续建议，不表示已经执行这轮评测。
