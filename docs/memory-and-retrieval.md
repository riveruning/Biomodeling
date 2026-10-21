# 项目记忆、材料检索与操作指引 v1

本轮将此前 Memory / RAG / Tool Use / Skill 建议落为一个可运行的增量。仍是单 Agent 与确定性科学工具；不增加外部服务或 Python 依赖，不用项目记忆替代证据。

## 可以做什么

- **项目记忆**：由操作者明确写入偏好、经验或决定，跨进程保留。每条记录含项目、可选模型范围、作者、来源、理由和前一版本。作者字段是操作者声明，不是身份认证。
- **更正与撤回**：追加不可变记录，不覆盖原文。检索只返回有效条目。并发更正产生同源的多个有效分支时先阻断，操作者可撤回不采用的分支解除冲突。
- **受范围限制的读取**：Agent 必须显式启用一个 memory_project；模型范围不匹配、来源不在当前任务白名单中的记忆不进入可读取快照。默认关闭记忆。decision 类记录必须同时绑定模型和来源。
- **材料检索**：Agent 可以用 search_sources 查找当前任务材料的相关页，获得可直接引用的原文和精确位置。采用确定性关键词匹配，不搜索宿主文件、其他项目或参考答案；这只是第一版检索增强，不是语义检索或开放文献搜索。
- **操作指引**：load_skill 提供两个固定、版本化的流程：ode_reproduction 与 clarification_review。它们说明步骤，不执行代码，也不更改权限、预算或科学判据。

## 记忆不会变成科学事实

所有记忆都标为 operator_assertion / semantic_support=unknown。recall_memory 返回的内容作为不可信提示，不是 source，不能引用为参数、方程或单位的科学依据。科学草稿仍须引用当前授权材料，并通过现有来源、单位、模型和数值检查。

例如，“曾经使用分钟制”可以帮助系统优先查找时间单位，但不能替当前论文决定时间单位。模型范围只限定旧模型经验的读取，不会把旧模型决定自动迁移到新草稿。

Agent 只获得记忆读取工具，不能自行写入、覆盖或撤回记忆。v1 不自动从模型回答中提炼“事实”，避免把未经核验的回答变成后续任务前提。

## 任务快照与恢复

开始任务时冻结有效记忆引用和技能目录，作为 agent_task 的依赖保存；新增的其他记忆不会悄悄进入正在执行的任务。每次模型调用前、返回动作后以及恢复保存研究者回复前，都检查快照中的条目是否已被更正或撤回；失效则停止，不继续使用旧记忆。恢复前失效不会写入新回复或调用模型，需以新快照建立新任务。

记忆引用进入研究包依赖闭包，导出、重新打开后可按同一快照恢复。独立导出包仅知道包内历史，不能感知导出后原存储的新撤回；这不是跨副本同步系统。没有记忆字段的旧检查点继续沿用原恢复流程。

新工具和旧工具共享原 LLM 调用预算；多一次检索也占一次动作调用，不增加修订或重试额度。读工具结果即刻保存检查点，但没有新增“任意崩溃点恢复”保证。

## 命令行

先把待保留经验写入 UTF-8 文件，如 note.txt。使用同一个产物存储目录：

```bash
.venv/bin/python -m biomodel_agent memory-add --store artifacts/my-study \
  --project study-a --category experience --author Researcher \
  --content-file note.txt

.venv/bin/python -m biomodel_agent memory-search --store artifacts/my-study \
  --project study-a --query tolerance

.venv/bin/python -m biomodel_agent memory-retract MEMORY_REF --store artifacts/my-study \
  --author Researcher --reason 'This advice no longer applies.'
```

更正时使用 memory-add 的 `--supersedes OLD_MEMORY_REF --reason 'Correction reason'`，并保持项目、模型和类别不变。`--source-ref SOURCE_REF` 可重复提供；decision 还须提供 `--model-ref MODEL_REF`。检索带来源/模型范围的记忆也应提供相应 `--source-ref` / `--model-ref`。

Agent 启用记忆：

```bash
.venv/bin/python -m biomodel_agent agent --store artifacts/my-study \
  --source paper.txt --experiment experiment.json --memory-project study-a
```

仍使用原有 BIOMODEL_BASE_URL、BIOMODEL_MODEL 和 BIOMODEL_API_KEY 配置。可选 `--memory-model-ref` 限定模型。此示例会调用配置的模型；离线单元与集成测试没有调用外部模型。

也可单独检查检索结果，无需模型：

```bash
.venv/bin/python -m biomodel_agent source-search --store artifacts/my-study \
  --source-ref SOURCE_REF --query 'Parameter k'
```

Python API 位于 memory.py：remember、retract、snapshot、recall；检索 API 为 retrieval.search_sources。技能目录由 skills.catalog 提供。

## 范围与预算

每次材料检索最多 64 个明确来源、2000 页、200 万原文字符；超过扫描预算报错，不静默截断语料。最多 10 个片段、每段最多 600 字符，总片段文本最多 8000 字符（默认 4000），包含精确的原页字符偏移。排序按页上命中的不同查询词数，再按来源顺序及页码。matched_terms 描述整页匹配，不保证所有词都在单个片段内；每个返回片段至少包含一个完整实际命中。

关键词包含数字和下划线，支持 Unicode 原文定位；没有同义词或完整语言学匹配能力。分词/排序不是语义支持判断。

每个任务快照最多 100 条记忆，每条内容最多 2000 字符；召回默认最多 5 条、3000 字符，整条返回或省略，避免截断决定而改变含义。当前按项目扫描已有记忆，未建立大规模索引。项目范围是本地应用隔离规则，不是多租户身份授权系统。撤回不是物理删除。

## 验证与开发分工

验收材料在 artifacts/context-memory-development/。新增回归覆盖持久化、项目/模型/来源隔离、更正与撤回、快照失效、并发冲突解除、预算、CLI、检索边界、原文偏移和技能版本。

集成测试用脚本动作验证“记忆→技能→检索→草稿→真实 ODE→解析解核对”，并验证导出研究包后的恢复、累计预算、参考答案不进入请求、记忆不能作为科学引用。脚本不是实际模型推理，不据此宣称提高了自主 Agent 成功率。

DSH 交付了检索模块与测试初版；Codex 独立审查后修正语法/导入、数字符号分词、Unicode 定位、排序测试口径和短预算片段缺失命中的问题。原交付与监督记录保留。记忆、Agent/CLI 接入、技能和集成验收由 Codex 实现。没有读取新独立测试案例作本轮调试，也没有发起此前等待授权的 USTC 案例回归。

后续可用专家审查的跨会话任务衡量重复追问、过期信息使用、检索命中、完成率和调用成本，再决定是否接入 Mem0、LangMem 等外部实现。当前没有安装或复制这些项目代码；借鉴的是明确范围、持久化、按需召回与历史更新的设计思路。
