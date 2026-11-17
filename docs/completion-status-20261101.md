# 问题与优化任务完成情况

## 2026-11-11 对照计划准备与暂停

本批 DSH 交付同模型对照及家族外/记忆、检索、指引消融两份计划，Codex 完成草案审查和结构验证。四个核心实验臂、三组单变量配对均采用 original_fulltext 轨道，候选与样本数等保持待冻结。新补齐本地 intake 和补充材料清单。源码、106 份测试、适配器与既有六文件准备包保持一致。

审查已纠正草案新增的独立审核者专属冻结/启动要求、每项目调用预算、记忆隔离和构建评分定义。操作方协议冻结与独立科学审核分别留存记录。草案仍为 draft_for_review，新流水线科学模型调用为 0。原交付和修订记录留档，详见 [本批验收](../artifacts/development/dsh-resume-20261110/real-paper-control-plans-acceptance-20261111.json)。

用户要求完成本批后暂停讨论，当前交付已验收。总体工程、科学状态、限制及讨论事项见 [阶段总结](work-summary-20261111.md)。

## 2026-11-11 PDF 覆盖与版本冻结核对

DSH 使用 GLM/max 完成默认 PDF 图像覆盖提示和开发适配器的提取引擎版本绑定。Codex 审查实际文件、运行验收并整理记录。请求档位已在配置与任务记录中保存，提供方内部执行档位无法独立观测。

默认 pypdf 摄取现会报告每页的图像对象内容覆盖缺口。图像枚举异常时保留文本并报告覆盖未知。完整 PDF 的 493914 字节原件、七页文本和 14652 个字符均保留，Linux 与 Windows 提取的逐页文本摘要一致。第六页提示已进入模型输入的来源目录。11 个图像对象条目按解析器资源计数登记。真实文章的科学模型调用为 0。详见 [SDK 验收](../artifacts/development/dsh-resume-20261110/pypdf-coverage-product-acceptance-20261111.json)。

| 独立验收批次 | 通过 | 跳过 | 失败 / 错误 |
|---|---|---|---|
| 当前适配器固定树 Linux 全量 | 2965 | 1 | 0 / 0 |
| SDK 修复冻结树 Linux 全量 | 2961 | 1 | 0 / 0 |
| Linux 安装包 29 模块 | 844 | 1 | 0 / 0 |
| Windows 安装包 29 模块 | 843 | 2 | 0 / 0 |
| 当前适配器 Linux 模块 | 65 | 0 | 0 / 0 |
| 当前适配器 Windows 已安装 SDK 模块 | 65 | 0 | 0 / 0 |
| 当前 SDK 的 Edge 浏览器检查 | 10 | 0 | 0 / 0 |

最终适配器固定树全量已通过，测试前后的实现、测试及适配器指纹一致。上述 SDK 与安装包验收使用修改适配器前的冻结测试副本。当前适配器另有两平台完整模块验收，Windows 暂存的全部 106 份测试与仓库字节一致。批次范围及完整结果见 [最终验收索引](../artifacts/development/dsh-resume-20261110/pdf-engine-binding-final-acceptance-20261111.json)。

当前 [运行包](../artifacts/development/dsh-resume-20261110/runtime-pdf-coverage-20261111.zip) SHA256 为 `d1739b6e6beb7bc6f1551e3e0815eee160a72fcb2bb8de0f696d349a17655521`，包含 99 份产品 Python 源码和 129 项分发清单。与前轮包相比仅 evidence.py 内容变化。Linux CPython 3.12.16 与原生 Windows CPython 3.12.14 的官方空 venv 安装入口均通过可选 PDF/OCR 安装和全文件字节核验。开发环境的实现与环境指纹为 `1debde5747f211de927213f184a8958e170861ac7f799081eaddd2b60063aa80`。该指纹包含 Python 补丁版本与四项数值依赖，各环境另按清单核对分发字节。

适配器在准备和每次验证时查询实际 pypdf 版本，并将引擎、版本和源码绑定保存到 manifest 与 plan。引擎版本漂移、缺失旧绑定或版本查询失败均有拒绝调用与只读历史控制。Codex 将 DSH 的版本漂移测试改为直接模拟运行环境 metadata 变化，六个冻结文件和公共实现指纹保持一致，runner 调用为 0。见 [适配器审查](../artifacts/development/dsh-resume-20261110/paper-engine-binding-review-acceptance.json)。

[新的文章准备包](../artifacts/development/dsh-resume-20261110/real-paper-preparation-engine-bound-20261111/preparation-manifest.json)绑定实际 pypdf 6.20.1、当前源码和单一 DeepSeek Flash 2/max 配置。prepare、show 和未授权执行拒绝均已验收，六个文件保持逐字节一致。旧准备包仍可只读，原文件完整保留。见 [准备包验收](../artifacts/development/dsh-resume-20261110/real-paper-engine-bound-preparation-acceptance.json)。

审查中曾发现 DSH 的警告措辞与测试不一致，Codex 已对齐措辞。两个既有 Windows 文本夹具补充了明确换行设置。一次审查脚本局部替换检查中止后误启了依赖测试，留下 46 项通过与 1 项失败，完成定位修改后模块 47 项通过。原始失败和 DSH 原交付均留档。Windows 适配器复测的首次启动被沙箱内 WSL vsock 权限阻止，同一脚本通过已授权的宿主执行方式重试后通过。

完整目标继续按 [剩余任务审计](../artifacts/development/dsh-resume-20261110/remaining-audit.md)核对。实际文章仍为一篇已见开发材料、两个轨道，`execution_authorized=false`。具名人工转录、科学参考与评分冻结、科学试跑、家族外对照及独立审核待完成。后续顺序见 [无人值守计划](../artifacts/development/dsh-resume-20261110/overnight-plan.md)。2026-11-01 历史正文保留原字节。

## 2026-11-11 模型动作抽取验收记录

DSH 使用 GLM/max 完成本轮两个任务，Codex 审查实际差异并运行验收。`submit_draft`、`submit_plan` 和 `run` 已接入 `agent_model_tools.py`，连同前轮七个读取和五个科学/规划动作，共十五个工具动作进入独立处理模块。主循环管理守卫、局部状态、事件、预算、检查点和终止。AST 比对确认主文件的变化集中在模块导入及两处动作分派分支，详见 [当前验收索引](../artifacts/development/dsh-resume-20261110/model-tools-final-acceptance-20261111.json)。

新增真实执行后的单次读取故障控制，验证已确认的 run_ref、已消耗的 run_count、具体错误事件与最终 incomplete 状态。两处既有 Windows 工作流夹具改为明确保留 UTF-8 换行，并增加 LF/CRLF 修订、精确引文、双状态解析解与质量守恒控制。既有入口及恢复专项 273 项通过，两个修复测试模块 68 项通过。

| 当前独立验收批次 | 通过 | 平台跳过 | 失败 / 错误 |
|---|---|---|---|
| Linux 源码全量 | 2958 | 1 | 0 / 0 |
| Linux 已安装包 28 模块 | 797 | 1 | 0 / 0 |
| Windows 已安装包 28 模块 | 796 | 2 | 0 / 0 |
| Windows Edge 项目和材料检查 | 10 | 0 | 0 / 0 |

安装包回归从前轮 26 模块扩展到 28 模块，加入 native modeling 与 modeling workflow。各批次单独保留日志和 XML。Linux 跳过 Windows junction 语义，Windows 跳过缺少权限的 symlink 和 POSIX FIFO，junction 有独立控制。全量前后的实现、测试和适配器指纹一致，Windows 暂存的全部测试与仓库逐字节一致。见 [固定版本回归](../artifacts/development/dsh-resume-20261110/model-tools-final-full-regression/result.json)。

当前开发实现与环境指纹为 `041e1b376b6116c6303477ae2f733c7a4ff0f65167237a2fbfd48183f5e84259`。该指纹包含包内 Python 文件、Python 补丁版本及四项数值依赖版本。当前 [运行包](../artifacts/development/dsh-resume-20261110/runtime-model-tools-20261111.zip) SHA256 为 `f4382b331f4ad1a2f766b3f1133f78488ec5a1eeb714b93d954466d1b1d18aae`，129 个分发清单文件、99 份产品 Python 源码。仓库另有开发用评分模块，共 100 份。与上一运行包相比，分发内容仅修改 agent.py 并增加 agent_model_tools.py。

Linux 临时独立 CPython 3.12.16 和原生 Windows CPython 3.12.14 均通过官方空 venv 安装入口，包含可选 PDF/OCR。两平台安装目录含中文与空格，99 份已安装源码和 129 个清单文件全部核对。Linux 由 setup.sh --ocr 自行创建 venv。安装过程复用依赖和 OCR 缓存，安装目标均为临时独立 venv。安装包字节按分发清单验证，各环境的版本指纹有其对应范围。

[当前真实文章准备包](../artifacts/development/dsh-resume-20261110/real-paper-preparation-model-tools-20261111/preparation-manifest.json)使用本地开发环境 Python 3.12.3，绑定上述开发指纹及现有 DSH 适配器。prepare、show 和 execution_authorized=false 的执行拒绝已复验，六个文件保持一致。完整 PDF 和两个轨道的一篇已见开发文章口径沿用冻结任务包。实际论文模型调用为 0，具名人工转录、科学准入与参考评分冻结、实际试跑、家族外对照和独立科学审核待完成。见 [准备验收](../artifacts/development/dsh-resume-20261110/real-paper-model-tools-preparation-acceptance.json)。

新增故障控制最初从 provider 的请求快照读取错误反馈，DSH 已改为从最终 checkpoint 读取并复验通过。Codex 的准备脚本两次前置检查分别选错生成后的投影文件、误用了原文件摘要口径，均在适配器调用前停止，已按适配器的原始输入与规范化摘要契约纠正并复验。原失败证据留档。

完整目标仍有 [原任务剩余项](../artifacts/development/dsh-resume-20261110/remaining-audit.md)，后续顺序见 [计划](../artifacts/development/dsh-resume-20261110/overnight-plan-20261110.json)。R02 延续原计划暂缓，当前未提交源码的远端 CI 待核验。内部完成文档外发的较早拒绝记录保留，本节由 Codex 本地整理，2026-11-01 历史正文保持原样。

## 2026-11-11 上一批工程核对

本轮工程交付已验收，原完整目标仍有下述待办。DSH 使用 DeepSeek Flash2/max 和 GLM/max 编写实现及必要测试，Codex 调度、审查和运行验收。请求档位已在配置与运行覆盖层记录，提供方内部档位执行无法独立观测。当前 [验收索引](../artifacts/development/dsh-resume-20261110/orchestration-final-acceptance-20261111.json)和 [状态记录](../artifacts/development/dsh-resume-20261110/status.json)为权威入口，较早状态已单独归档。

本轮新增五个科学/规划动作处理器，连同前轮七个读取处理器保留原 journal、guard 顺序、事件、检查点和恢复契约；项目操作增加持久 elapsed_s 与按操作耗时聚合，旧收据或中断的缺失完整耗时保持 unknown。研究项目、流水线、监督器及工作台的 33 处调用迁移到公开 records/owner。GLM 修复新增耗时测试的装配问题，以及三个测试夹具的 Windows 隐式换行问题；精确来源范围与引文规则保持严格，并增加 LF/CRLF 控制。

| 当前独立验收批次 | 通过 | 跳过 | 失败 / 错误 |
|---|---|---|---|
| Linux 源码全量 | 2955 | 1 | 0 / 0 |
| Linux 已安装包 26 模块 | 734 | 1 | 0 / 0 |
| Windows 已安装包 26 模块 | 733 | 2 | 0 / 0 |
| Linux 独立 Python 干净安装功能冒烟 | 9 | 0 | 0 / 0 |
| Windows Edge 项目及材料检查 | 10 | 0 | 0 / 0 |

各批次分别保留日志与 XML，不累加。Linux 跳过 Windows junction 语义；Windows 跳过无权限的 symlink 与 POSIX FIFO，junction 有单独测试。两平台新修复的可靠性模块各 35 项通过。Windows 暂存的全部测试文件与仓库逐字节一致。全量运行前后的源码、测试与适配器指纹一致，见 [固定版本回归](../artifacts/development/dsh-resume-20261110/orchestration-accepted-full-regression/result.json)。

产品源码指纹为 `af89924aac9682c35cf7e2da32858562544b4f9b0068f5fbee871a4bff2a22b7`，测试指纹为 `5d0eefb3b0512e305962a9f2c493973f9ddb78aca6490550aa8269892687fe52`，适配器 SHA256 为 `ba64ae1a1f914300ab4929a1fdd7b98f504fe9643c38a5e86c7185721a65d947`。

当前运行包为 [runtime-orchestration-20261111.zip](../artifacts/development/dsh-resume-20261110/runtime-orchestration-20261111.zip)，SHA256 为 `f6407bb7d1f96ff6765c59d19338cd5a27028ea367037902f08db80e1b40c37b`，128 个清单文件、98 份产品 Python 源码，仓库另有开发用评分模块。Windows 中文及空格路径的官方空 venv 安装通过。Linux 临时独立 CPython 3.12.16 使用官方 setup.sh --ocr 自行创建空 venv、安装锁定依赖，并通过六类计算/导出和真实 OCR 冒烟；未修改系统 Python。来源和哈希见 [独立安装验收](../artifacts/development/dsh-resume-20261110/linux-standalone-install-acceptance.json)。

真实文章 [当前准备包](../artifacts/development/dsh-resume-20261110/real-paper-preparation-orchestration-20261111/preparation-manifest.json)绑定当前源码、适配器和完整 PDF，6 个文件经 show 与未授权 run 拒绝后保持不变，最后交接复核仍一致。PDF 为 493914 字节，SHA256 为 `65325d788518f4f84b58d3a9deef2a62432bc840eb9c18062d9aab2d4f573ef3`。两个轨道合计一篇已见开发文章，实际科学模型调用为 0，execution_authorized=false。双解析器的七页摄取与第六页三个图面板的 AI 布局预检留档；具名人工转录、科学准入、参考与评分冻结、实际试跑及独立科学审核待完成。DOI 仍未外部核验。

[原任务范围审计](../artifacts/development/dsh-resume-20261110/remaining-audit.md)保留通用复杂材料、严格 reported 的覆盖缺口、后续编排职责拆分、家族外验证、同模型对照与消融。R02 延续原计划暂缓，当前未提交源码的远端 CI 尚未核验。后续顺序见 [计划](../artifacts/development/dsh-resume-20261110/overnight-plan-20261110.json)：冻结科学准入、参考、评分及预算，完成具名转录和独立审核，取得实际执行授权后开展文章试跑与对照，再按失败分布安排扩展。

验收包装曾出现安装调度早于暂存完成、独立 Linux 冒烟缺多进程 main guard、Windows 启动路径格式错误，以及 Linux 回归缺本地端口权限的问题。失败和中断记录均保留，修正环境或包装后已复验通过；本次正式结果采用获准环境的最终日志与 XML。内部完成文档和验收汇总外发被自动审批拒绝，该文档任务未派发；本节及范围审计由 Codex 本地整理。2026-11-01 历史正文保持原样。

## 2026-11-11 早前核对

本轮实现与回归测试由 DSH 编写，Codex 负责调度、复核和运行验收。当前工程交付已通过以下检查，完整目标仍有待办。最终证据与版本见 [验收索引](../artifacts/development/dsh-resume-20261110/overnight-final-acceptance.json)。

| 范围 | 本轮交付与证据 |
|---|---|
| 编排接口 | 五个模块迁移到公开 provider identity，保留 agent 兼容入口。七个读取动作抽入 `agent_read_tools.py`，保留反馈、事件、调用编号、检查点及回调时机契约 |
| 推理参数 | 公开项目 CLI 接入 reasoning effort、max output tokens、provider timeout，并随 provider identity 冻结。旧配置保留默认行为。实际 HTTP 请求体有专项测试 |
| 中文路径 OCR | 在隔离 worker 内使用经校验数据目录与相对 tessdata 路径，恢复 worker cwd，保留原始数据哈希与出处链。真实 Windows 中文及空格路径识别通过 |
| 文章适配器 | 公开 CLI 组合、源码与原 PDF 版本绑定、累计预算、预留与收据、完整诊断字节绑定、未知效果停止与恢复不重放均已接入。修复 JSON 转义字符 canary 漏检和负面产品状态分类，并覆盖 Windows 原诊断字节保留 |

| 独立验收批次 | 通过 | 跳过 | 范围 |
|---|---|---|---|
| Linux 最终全量 | 2945 | 1 | 源码、测试及适配器前后指纹一致，跳过 Windows junction 语义 |
| Windows 实际安装包 | 352 | 2 | 15 个模块，跳过 symlink 权限和 POSIX FIFO，包含真实 OCR |
| 全新 Linux 安装包 | 292 | 1 | 14 个核心模块，包含真实 OCR |
| 隔离适配器 | 61 | 0 | 仅复制适配器与测试文件，使用全新 Linux 安装包 |
| Node 与浏览器 | 控制测试 22 项、项目 UI 检查、Edge 检查 10 项通过 | — | 实际 Windows Edge 154，浏览器后端为 Linux 当前源码的本地合成夹具 |

各批次分别保留原始日志与 XML。最终全量记录见 [固定版本回归](../artifacts/development/dsh-resume-20261110/final-full-regression/result.json)。工程审查由 DSH 提出具体反例，再经 Codex 离线复现、DSH 修复及运行回归，独立科学审核保持 pending。

补充的读取动作与 provider 兼容性审查提出一项 timeout 身份差异。新旧实现的相同本地 run/resume 操作均拒绝运行中新增的 timeout 设置，按既有冻结契约处置，源码保持已验收版本。原报告和 [对照复现处置](../artifacts/development/dsh-resume-20261110/agent-extraction-review-disposition.json)一并保留。

最终运行包为 [runtime-overnight-final-20261111.zip](../artifacts/development/dsh-resume-20261110/runtime-overnight-final-20261111.zip)，SHA256 为 `8e1b4d07bcf9792bd1184ac17c61c9dd7ad8e2a6234642c24bb4304baf411ba8`。127 个清单文件与 97 份已安装 Python 源码核对通过，仓库另有开发用 `literature_scoring.py`，共 98 份。Windows 空 venv 的 `setup.ps1 -Ocr` 通过。Linux 主机缺少 ensurepip，使用隔离空 venv 引导 pip 后运行 `setup.sh --ocr`，全部锁定依赖、安装包和功能验收通过，官方脚本自行创建空 venv 的路径仍待具备该前置条件的环境验证。当前未提交源码对应的远端 CI 尚未核验。

真实文章已通过新适配器生成 [最终准备包](../artifacts/development/dsh-resume-20261110/real-paper-preparation-final-20261111/preparation-manifest.json)，`show` 可读取，未授权 `run` 在调用公共命令前拒绝，文件保持原样，模型调用为 0。完整 PDF 为 493914 字节，SHA256 为 `65325d788518f4f84b58d3a9deef2a62432bc840eb9c18062d9aab2d4f573ef3`。两个轨道合计一篇已见开发文章，保留未见留出禁用标记。两种解析器的七页本地摄取已记录，第六页图像与公式仍待视觉核对。具名转录、科学准入及协议冻结、实际评测与独立审核待完成，DOI 保持未验证。max 请求转发可由代码与请求体核验，服务内部档位执行情况无法独立观测。

通用复杂材料与 strict reported 的覆盖、读取动作以外的大型编排系统拆分、家族外验证、同模型对照与消融仍待完成，R02 延续原计划暂缓安排。

历史验收包装曾修正 draft/ready 断言、进程 PATHEXT 和 OCR 环境变量。Windows 验收目录一度误拷开发模块，已按正式清单重装并重验，正式 ZIP 始终采用排除开发材料的构建规则。一次专项运行因本地端口权限报错，最终获准的完整回归已覆盖同批测试并通过。日志保留供复核。

本节由 Codex 本地整理。自动审批拒绝将内部完成文档与验收汇总发送给 USTC 的文档任务，该任务未派发。DSH 已交付的实现与测试及其验收结果保留。

## 2026-11-10 历史核对

本轮在已验收实现上更新当前状态。以下计数分别来自对应固定源码、测试和运行包。文末 2026-11-01 清单保留为历史记录，其计数不与本轮相加。完整目标仍未全部完成。

| 原任务 | 当前实现与验证 | 剩余范围 |
|---|---|---|
| GUIDE-01 / 来源构建 | 完整 ODE 方案 schema、唯一引文定位和有限逐字 ODE/Boolean/SSA 构建路径已接入。已修复可选父引用与参考引用的 null 契约，以及 Synthetic/# 前缀吞没科学条件的问题 | 一般论文与中文材料的语义提取、严格 reported 绑定及真实任务收益仍待验证 |
| DATA-01 | PDF 区域候选、具名转录修订、CSV/TSV 单位和仿射转换账本已实现。转录来源绑定校验覆盖修订链、区域、原 PDF 页、引用与公开 evidence 入口，普通 JATS/非转录来源保持兼容。来源归属修复由 DSH 实现于 `material_review.py`/`evidence.py`，DSH 补充边界测试，相关 224 项通过 | 通用公式识别、复杂跨页表格与补充数据自动理解尚未实现。转录属于明确人工介入 |
| UI-01 | PDF 区域、保存新转录、带单位结果图、父版本差异与停止界面已接入。实际 Windows Edge 的项目和材料两条流程检查通过 | 通用复杂材料自动建模能力仍受 DATA-01 限制 |
| RUN-01 | 总墙钟期限、安全边界停止、当前调用收据保留、内层已报告 usage 汇总已接入，相关功能包含在固定源码回归中 | 缺失 usage 保持 unknown。完整调用与各阶段耗时是否齐全仍需真实长任务记录核验 |
| SCI-01 | SSA 描述性矩、PDE 场、Boolean 序列和个体种群外部参考协议，及三网格收敛协议均已实现并有专项控制 | 独立参考审核、适用性与真实任务效果仍待验证。描述性矩比较不提供显著性检验 |
| 经验与记忆 | 六类范围指引、显式选择的条件化经验及失效过滤已接入并通过专项控制 | 家族外验证、同模型消融和可重复收益尚未完成 |
| REL-01 | 固定源码与测试的完整 Linux 回归 2845 项通过、1 项跳过，实现指纹前后一致，均为 `4b3cc9d72c3482c95271bb8773a3551bbf631968a6af43100a5187f09286c293`，测试指纹为 `2393f3a19a9d03731b1e69734efbe977b2cc6c05f7e5e07d9fe7fe2ccaf25760`。原生 Windows 的 13 个 CI 模块 252 项通过、2 项跳过，0 项失败，跳过仅涉及 Windows symlink 权限与 POSIX FIFO 平台限制。Windows 的可选 PDF 与锁定 OCR 数据在全新基础 venv 上通过可选安装入口增量安装，真实光栅 PDF 的 OCR 识别通过，`setup.ps1 -Pdf` 与 `setup.ps1 -Ocr` 均以退出码 0 完成 | 仅 Linux 干净安装及当前远端 CI 尚未验证 |
| 编排维护性 | 本轮沿用已抽出的能力、schema、控制、usage、材料、结果与参考模块 | 原大型编排函数的系统拆分及剩余跨模块私有调用替换仍未完成 |
| 科研评价 | 前轮已见开发项目的失败与用量记录保留。真实文章准备由 DSH 实际交付 schema2 八阶段 JSON，按 Codex 三项反馈修正，两个任务均成功，外部验收为 accepted_preparation_only。PDF SHA256 为 `65325d788518f4f84b58d3a9deef2a62432bc840eb9c18062d9aab2d4f573ef3`、493914 字节。原文自动提取轨与具名转录辅助轨合计为 1 篇已见开发文章，不作未见留出 | 预算仍待冻结，尚未开展实际科学评测。真实文章 runner 与实际评测均未启动。新材料、同底层模型对照、独立领域审核和家族外评价仍未完成 |
| R02 | 保留既有受限估参 | 原计划暂缓的通用不确定性、复杂观测和跨后端估参尚未扩展 |

Windows 侧，全新运行包 `setup.ps1` 基础安装通过。全新空 venv 安装全部 15 项锁定依赖，96 份已安装的 Python 源码与当前源码一致。doctor 锁定依赖就绪，公开 CLI 离线创建空项目通过，pip 缓存可复用。运行包 wheel 新目标安装也通过，但该轮复用旧依赖，须与全新依赖安装区分。在该全新基础 venv 上，`setup.ps1 -Pdf` 与 `setup.ps1 -Ocr` 两个可选安装入口均以退出码 0 完成，增量安装可选 PDF 与锁定 OCR 数据，真实光栅 PDF 的 OCR 识别通过。本次没有另建第二个全新 OCR 环境。本轮恢复 PowerShell 的机器 PATHEXT，解决了 WSL 传入空值的问题。测试包装的 draft 状态断言修正为真实 ready 且 frame/iterations 为空，产品未修改。

更早轮次记录过 Linux 2810 项通过和原生 Windows 210 项通过，也记录过原生 Windows 251 项通过、3 项跳过，以及 245 项通过、9 项跳过。这些结果都属于历史批次，不与本轮 2845/252 计数相加。

本轮完成情况文档的底稿作者为 Codex，修订作者为 DSH。真实文章准备过程中的旧取消任务仅为历史，不能再据此称 DSH 从未交付。

当前 DSH 配置限定为用户指定的 deepseek-flash-2 与 glm-5.3-flash，运行覆盖层请求 max。DeepSeek Flash2/max 成功。GLM 有成功记录，也出现 stream idle 失败。先前短窗口会中断实际持续思考，当前 2 小时任务预算仅为监督运行窗口，无用户时间要求。服务内部是否严格执行 max 档位无法独立观测。

本轮详细证据位于本地 `artifacts/development/dsh-resume-20261110/` 及 `.workflow/runs/`。

通用复杂材料、严格 reported、编排维护、家族外与消融、独立审核及 R02 仍未全部完成，本文不构成全目标验收。

## 2026-11-01 历史记录

核对日期：2026-11-01。依据本轮源码、[任务计划](execution-plan-20261101.md)、[交付记录](validation/20261101/improvements.md)及[验证摘要](validation/20261101/improvement-summary.json)。

**尚未全部完成。** 已完成已确认 bug 的代码修复、第一批工程改进，以及根据真实试用失败补充的 schema 和错误反馈；原规划中的材料、界面、执行控制和科学评价仍有未完成项。

## 已完成的具体范围

| 项目 | 完成情况 | 证据与限制 |
|---|---|---|
| FIX-01：正式 provider 的 reason 字段被拒绝 | 已修复 | 统一动作归一化和保存回答绑定；原始回答保留，篡改动作/参数仍拒绝 |
| FIX-02：经验提取返回协议冲突 | 已修复 | 使用 propose_lessons 信封，兼容旧收据；无效返回保存诊断，候选保持不激活 |
| FIX-03：已保存回答被前端额度判断挡住 | 代码修复完成 | 后端统一 continuation，CLI/HTTP/实际 JS 渲染检查通过；真实浏览器演练待补 |
| INT-01：只测试 Scripted provider 的覆盖缺口 | 已补充 | 正式 ChatProvider 配合模拟 HTTP，贯通 CLI、工作台、追问/续接、批次、科学执行与导出 |
| CAP-01：能力描述分散 | 最小目录已完成 | CLI、建议层、研究提示、工作台共用六类能力目录；后端仍决定实际执行语义 |
| GUIDE-01：研究阶段字段与引文反馈 | 已完成本轮范围 | frame/open_iteration schema、共享上限、有限候选引文位置、原生输入写入前预检；完整 ODE decision-plan schema 尚缺 |
| GUIDE-02：来源绑定错误细节丢失 | 已修复 | Boolean/SSA 返回缺失目标、有限额外目标和逐项问题；未放宽来源校验；最新反馈的在线收益未验证 |
| EVAL-01：冻结开发试用输入 | 已完成首批 | 8 个已见案例、4 个家族、1 篇论文标识；包含合成与辅助转录控制 |
| EVAL-02：第一轮真实服务试用 | 首轮及有界诊断已完成 | 学校服务 44 次规划调用、1 次已准备合成 FBA 的实际数值执行；完整论文复现仍为 0 |

## 部分完成或未完成

| 原优化方向 / 任务 | 当前完成部分 | 仍需完成 |
|---|---|---|
| 真实材料到可信数学模型 | 来源绑定、方案和错误反馈已有工程路径；已取得实际失败分布 | 完整 ODE decision-plan 字段指引、稳定的数学构建和引用修复；ODE/SSA 真实试用阻塞尚未全部解决 |
| DATA-01：材料与观测数据 | 保留原有 PDF/JATS/UTF-8 解析与有限字面数据选择 | OCR/公式、复杂/跨页表格、补充数据、观测映射和转换审阅；本轮未新增通用解析能力 |
| UI-01：研究工作台闭环 | 修复续接按钮，展示支持范围和既有诊断 | PDF 导入接线、便捷修订、模型版本差异、单位明确的科学结果图；真实浏览器回归 |
| RUN-01：执行控制与成本 | 规划耗时、失败调用的已报告 usage 和安全响应诊断 | 研究总期限、安全停止/取消协议、内层完整 token 汇总；缺失用量仍未知 |
| SCI-01：类型专属验证 | 既有数值/不变量检查，ODE 序列和 FBA 目标参考协议 | SSA 统计比较、PDE 网格收敛/外部参考、Boolean/个体观测与干预比较等按需协议 |
| 编排模块维护性 | 抽出动作契约、能力目录和阶段 schema | agent.py 等较大编排函数的系统拆分、公开接口替代部分跨模块私有调用 |
| 经验、记忆、科研指引的收益 | 原有条件化记忆/审核机制；经验提取协议已修 | 多类型接入、家族外验证、有/无记忆或指引的对照；没有自动激活本轮经验 |
| REL-01：跨系统发布 | Linux 本地回归、Node 检查、运行包检查；Windows CI 已配置 | 本轮原生 Windows/浏览器验收、CI 远端结果确认及必要安装验证 |
| 科研评价结论 | 首轮开发控制、实际失败保留、合成 FBA 数值执行 | 新材料与家族外评价、独立领域审核、同模型对照/消融；尚无完整论文自主复现成绩 |
| R02 估参后续扩展 | 保留既有受限估参功能 | 仍按此前安排暂缓，未扩展通用不确定性、复杂观测或跨后端估参 |

不以测试数量、可调用接口或一次数值成功替代科研效果结论。本轮没有确认新的数值求解器错误，但这不代表整个项目不存在其他 bug。

## 后续优先顺序

1. **ODE decision-plan 与来源绑定修复。** 基于已观察到的字段、条件/context、缺失目标和引用错误补完整契约及逐项修复路径，先验证是否减少无进展调用。
2. **用新开发材料验证收益。** 原失败及重跑保留分列，遵守单独冻结的预算；独立评价集继续隔离，不能把已见案例当作泛化成绩。
3. **研究总期限/停止和工作台审阅。** 先补安全停止与完整用量，再接材料修订和类型适配结果图。
4. **按需求扩展科学验证和经验复用。** 以有来源的参考与统计协议验收，再评价家族外收益。

多 Agent、微服务、向量数据库、新模型大类等继续以实际需求和对照收益为前提，未作为本轮已交付功能。

## 验证与版本口径

- schema 阶段全量离线回归：**2723 passed、1 skipped**。
- 最后追加的来源绑定诊断增量：**204 项相关回归通过**。全量与增量分别保存实现指纹，未把两轮数量相加。
- 最终三个原始 bug 的[离线回归](validation/20261101/bug-regressions.json)均通过；[原始失败](validation/20261101/bug-reproductions.json)保留。
- 原始运行 store、模型服务记录、运行包和源码快照保留在本地 `runs/`；仓库发布源码、测试、小型示例及可共享验证摘要。
