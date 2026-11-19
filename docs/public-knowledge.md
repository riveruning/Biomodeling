# BioModels 与 SABIO-RK 基础接入

2026-10-23，`public_knowledge_v1`。新增公开数据库搜索、单条资料导入和 BioModels SBML 下载；复用现有不可变存储和模型检查。无需新增依赖或数据库账户。不是参数自动确认、开放式机制发现或自动经验写入。

## 使用

在仓库根目录执行；查询内容会发给所选公开数据库。只提交公开的查询词，不会自动上传论文、模型、记忆或实验数据。最多返回 10 条，无自动翻页或重试，单次响应最多 4 MiB；连接超时 5 秒、读取超时 20 秒，流式接收检查 30 秒总期限。总耗时可能因正在等待的一次读取而超过 30 秒。原始响应、URL、获取时间和内容哈希保存在指定 store；HTTP/连接失败保留失败记录，不伪装成零条结果。

```bash
# 搜索公开模型
.venv/bin/python -m biomodel_agent knowledge-search --database biomodels \
  --query Goodwin --limit 3 --store runs/knowledge

# 列出一个模型的文件
.venv/bin/python -m biomodel_agent knowledge-files BIOMD0000000012 \
  --store runs/knowledge

# 导入主 SBML，检查当前执行兼容性，并保存到新文件
.venv/bin/python -m biomodel_agent knowledge-fetch --database biomodels \
  BIOMD0000000012 --output runs/knowledge/repressilator.xml --store runs/knowledge

# 查询动力学记录；支持 SABIO-RK 字段查询，复杂值需引号
.venv/bin/python -m biomodel_agent knowledge-search --database sabiork \
  --query 'ECNumber:1.1.1.1' --limit 2 --store runs/knowledge

# 导入单条记录，返回 source_ref、参数与实验条件
.venv/bin/python -m biomodel_agent knowledge-fetch --database sabiork \
  4510 --store runs/knowledge
```

BioModels 只有一个主 XML/SBML 时自动选择，否则需 `--filename` 指定列表中的文件。服务器提供 SHA-256 时检查下载一致性。只导入 SBML，不下载或执行作者脚本，不自动仿真。`--output` 不覆盖已存在文件。`can_execute` 是当前结构检查结论，`scientific_status` 仍为 `not_verified`；有实验配置后可通过现有 `run --sbml ... --experiment ...` 入口运行。

SABIO-RK 使用当前官方网页调用的 `/api/ft/proxy-select`，不依赖已跳转的旧手册接口。保留完整响应与原始参数单位、服务器提供的归一化值和实验条件，不自行换算或选择参数。第三方服务字段以后可能变化，结构不匹配会报错。

## Agent 使用

新增 `search_knowledge` 动作，输入 `{"database":"biomodels","query":"Goodwin"}`。默认关闭，需为新任务显式启用：

```bash
.venv/bin/python -m biomodel_agent agent --source paper.txt \
  --experiment experiment.json --store runs/knowledge \
  --knowledge-database biomodels --knowledge-database sabiork
```

启用列表写入任务配置，恢复时沿用；搜索共享现有调用预算。外部请求结果未知的中断不自动重发。搜索结果仅是线索，不加入当前任务来源白名单，不允许凭搜索摘要提交参数或方程。需要使用时先 `knowledge-fetch`，把返回的 `source_ref` 明确提供给同一 store 中的新任务：

```bash
.venv/bin/python -m biomodel_agent agent --source-ref SOURCE_REF \
  --experiment experiment.json --store runs/knowledge
```

模型服务设置仍沿用原有配置。搜索/导入 CLI 不调用 LLM。冻结的 study 不开放外部查询；不修改候选、边界或训练/留出数据角色。新导入资料不自动进入历史任务或记忆。

资料导入后可用已有 `source-search` / `inspect_source` 查阅。外部 JSON/XML 目前作为原始表示保存，未实现全面的公式与参数声明转换；现有确定性绑定语法可能要求进一步明确声明，不能保证直接自动重建。接入不会把数据库条目自动标为领域审核通过，也没有全量镜像、许可重授权或自动知识审核机制。

## 实际验证入口

公开网络演练见 [live-summary.json](../artifacts/public-knowledge-20261023/live-summary.json)：BioModels 搜索 Goodwin、下载 BIOMD0000000012 并通过当前结构检查；SABIO-RK 查询 ECNumber:1.1.1.1，导入记录 4510。结果计数仅对应获取时刻。存储中保留原始响应和来源；这次没有调用线上 LLM，没有运行该下载模型或评价新生物学结论。

离线检查位于 `tests/test_public_knowledge.py`，覆盖来源保存、单位/条件保留、无结果、错误响应、限额、错误文件、校验和、Agent 默认关闭与显式启用。最终运行结果在同目录 `verification.json`。

接口参考：

- [BioModels 官方搜索与下载教程](https://www.ebi.ac.uk/training/online/courses/biomodels-quick-tour/searching-and-getting-data-from-biomodels/)
- [BioModels 实际公开搜索接口](https://www.biomodels.org/search?query=Goodwin&numResults=10&format=json)
- [SABIO-RK 官方界面](https://sabiork.h-its.org/ui/search)；当前前端声明 `/api/ft/proxy-select`，已实际请求核对。
- [SABIO-RK 官方旧客户端说明](https://www.sabio.h-its.org/layouts/content/docuRESTfulWeb/searchPython.gsp)仅作历史参考，未按其中旧地址实现。
