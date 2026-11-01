# 目录与归档说明

更新于 2026-10-30。用途是帮助定位现有文件、安排新增文件，并解释本地归档与 Git 跟踪状态。

## 当前目录

| 路径 | 用途 | 新文件放置原则 |
|---|---|---|
| `src/biomodel_agent/` | Agent、领域契约、来源、计算、存储与报告 | 产品功能在这里实现，CLI 与 Agent 复用科学执行层 |
| `tests/`、`tests/fixtures/` | 回归检查及固定输入 | 放必要的小样本；大规模运行输出放运行目录 |
| `examples/` | 用户可阅读/复算的模型、设置与参考 | 保留来源、适用范围与缺口说明 |
| `benchmarks/inputs/`、`benchmarks/manifests/` | 既有开发输入及协议清单 | 区分开发材料、任务、阶段和重复运行 |
| `benchmarks/development/` | 评价、对照、诊断和历史演练脚本 | 运行前先读对应文档，部分脚本依赖历史产物或调用外部服务 |
| `docs/` | 当前支持、专题接口、历史验收和计划 | 先从[分类索引](README.md)进入；新文档登记在索引 |
| `artifacts/` | 历次实验与验收证据、对象存储、报告 | 新的开发输出可放独立日期/任务目录，不覆盖旧结果 |
| `runs/` | 用户新运行的推荐位置，可能尚未创建 | 使用任务子目录，避免不同任务混用 |
| `tasks/`、`discussion-notes/` | 本地开发任务卡与历史讨论 | 不是产品运行接口，也不是当前进展的唯一依据 |
| `scripts/`、`.workflow/` | 既有开发协作脚本与运行状态 | 与产品 Agent 区分；保留历史配置及状态 |
| `dist/`、`.venv/`、缓存目录 | 本地构建、依赖和缓存 | 不作为源代码或实验结论依据 |

## 源码阅读路线

| 主题 | 入口文件 |
|---|---|
| 命令与类型分派 | `cli.py`、`agent.py` |
| 显式建模方案与宿主 | `modeling_plan.py`、`modeling_workflow.py`、`native_modeling.py`、`workspace_tools.py`、`timeline.py` |
| ODE 构建与执行 | `agent_tools.py`、`modeling.py`、`application.py`、`validation.py` |
| FBA | `fba.py`、`fba_sbml.py`、`fba_agent.py`、`fba_agent_runtime.py` |
| 其他四类 | `typed_models.py`、`typed_agent.py`、`model_contracts.py`，以及 `stochastic.py`、`pde.py`、`boolean.py`、`individual.py` |
| 来源与参数 | `evidence.py`、`knowledge.py`、`parameter_sources.py`、`parameter_applicability.py`、`jats_sources.py` |
| 本地工作台 | `workbench.py`、`workbench_server.py`、`workbench_assets/` |
| 类型建议与来源构建 | `task_proposals.py`、`boolean_proposals.py`、`stochastic_proposals.py` |
| 统一任务与原生续接 | `research_tasks.py`、`research_cli.py`、`typed_agent_runtime.py` |
| 计划、失败与恢复 | `workstate.py`、`task_plans.py`、`failure_handling.py`、`invocations.py`、`recovery.py` |
| 估参与探索 | `fitting.py`、`estimation.py`、`estimation_workflow.py`、`exploration.py` |
| 记忆与科研指引 | `context.py`、`memory.py`、`memory_candidates.py`、`skills.py`、`scientific_skills.py` |
| 产物与复用 | `storage.py`、`bundles.py`、`reporting.py`、`case_library.py` |

上表文件均相对 `src/biomodel_agent/`；它是阅读分组，未移动 Python 模块或改变导入路径。

## 为什么先整理导航

旧报告、测试和脚本已经引用大量 `artifacts/` 路径；有的验证还绑定源码或文件摘要。直接搬移目录可能破坏证据入口或历史脚本。当前先保留路径，新增状态入口并标注历史用途。

2026-10-30 整理将 11798 个历史运行产物、内部任务、讨论记录及本地服务配置取消 Git 跟踪，约 80.8 MB。所有本地文件原位保留，取消跟踪前的 SHA-256 清单和索引备份位于本地 `artifacts/github-consolidation-20261030/`。此前 Git 历史不改写。

当前源码分发保留小型可复算示例、固定测试样本与 `docs/validation/` 中的可共享验收摘要。专题文档中的 `artifacts/`、`tasks/`、`discussion-notes/` 链接表示本地历史证据，不承诺存在于干净检出；历史脚本可能需要另行准备这些归档。普通产品运行及常规测试不依赖全部归档；原有三个归档/付费集成检查在材料缺失时跳过。

## 维护约定

- 当前结论从[最新进度盘点](project-status-20261030.md)和[支持矩阵](current-support.md)进入；历史专题中的测试数和“下一步”只对其记录版本有效。
- 新增能力同步登记支持范围、限制、命令和实际验证；“接口存在”“测试通过”“真实科学效果”分开记录。
- 新运行使用独立目录，保留失败与缺跑，不覆盖旧验收文件来制造最新成功状态。
- 可共享实例放 `examples/`，测试必需输入放 `tests/fixtures/`，实际运行产物放 `runs/` 或 `artifacts/`。
- README 保持安装、使用和导航简洁，逐轮实现细节留在专题文档。
