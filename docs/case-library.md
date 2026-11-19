# R03：固定案例目录、显式装载与知识候选

2026-10-26。本轮新增 `case_library.py`、`examples/case-library/catalog.json`、五项CLI及离线演练。它整理当前允许使用的已见开发资料，复用原不可变存储、来源/JATS、ODE/FBA工具和导出协议，不增加论文、外部请求、向量数据库或默认Agent检索。

## 实际目录与角色

[案例入口](../examples/case-library/README.md)提供四个v1.0.0入口：Goodwin探索运行、Goodwin原始初值未知、解析FBA链路和已保存公开教材FBA。前两个同属goodwin2013家庭；入口和重跑次数不能当作独立论文/评测样本。

每张固定卡包含模型类型、符号/方程或稳态约束、参数/初值来源、适用条件、原始资料归属、执行配方、参考类型、已知缺口、验证层级和逐文件SHA。Goodwin论文参数仍来自辅助视觉转录，独立审核pending；探索初值与原初值未知明确分开。FBA保留原网络/边界/活动目标，不推广到其他培养条件、动态模型或基因干预。上游许可按已有归属记录保留，不宣称新增再分发授权；教材许可未独立审计。

库用途固定为**显式模型复用**，不是盲目重建题的提示库。数值复算参考与正式评测答案是不同角色：这里只包含已见开发案例已有的SciPy/解析/教材标量参考，且参考文件不进入搜索正文或返回的source_refs；不包含gold、私有评分规则、独立评测或受限材料。使用这类含模型/参考的资料不能声称这些案例仍是未见评测。实际运行隔离和公平评价仍是R11工作。

## 查找与按需装载

```bash
.venv/bin/python -m biomodel_agent case-search --query goodwin
.venv/bin/python -m biomodel_agent case-inspect --id goodwin-exploratory --version 1.0.0
.venv/bin/python -m biomodel_agent case-load --id fba-textbook --version 1.0.0 --store runs/reuse-textbook
```

search只读固定目录的id/title/tags/summary，不读取模型、源正文、参考文件或候选。匹配仅是目录线索，不证明科学适用性。inspect显式查看所选卡及限制。没有“latest”、相似名称或未知版本回退。

目录文件摘要和逐卡摘要固定在代码中，装载前核对所选文件哈希/长度，拒绝越界、符号链接、缺失或换版。不是读取用户改写的JSON后相信其自报版本。目录更新需显式发布新数据/代码版本；摘要只保证受支持版本的完整性，不认证作者、操作者或科学正确性。

装载生成不可变case_material：只保存所选文件快照、卡片、模型句柄和允许作为来源的source_refs，最大16MB；不会执行模型、写入记忆、激活候选、修改当前任务或导入其他案例。当前目录版依赖checkout，普通wheel内置资料分发未做；默认安装说明使用editable项目。装载后的闭包可独立于原文件目录重跑。

加载返回的source_refs可以显式提供给**新**Agent任务，FBA返回的model_ref可用于已有FBA路由；没有注册自主case-search动作，也没有自动把库加入现有任务上下文。复用源必须沿用R01条件/来源检查，不能凭“有相似案例”认定参数适用于当前实验。现有study和恢复边界保持原样。

## 四种配方的行为

- Goodwin探索：逐字节导入旧来源绑定模型及其完整依赖，核对模型XML、实验和独立SciPy数值参考。固定分析者X=.1/Y=.2/Z=.3、未知/任意时钟、0..240网格、求解器rtol1e-7/atol1e-10/timeout30s/max_steps100000、尺度10及阈值0.001。参考不是作者原始数据，不比较原始瞬态。
- Goodwin原初值未知：只导入s2a、8张原始TIFF及先前已看图的候选，通过R08来源链另存派生源；不导入探索模型、分析者初值或SciPy参考。model_ref=null，三项初值None，case-run明确保存blocked_missing_initials、run_ref=null，不调用科学求解器。
- 两个FBA：读取固定原生JSON或FBC v2，沿用原显式平衡/边界/目标，highs-ds、15秒及已有标量参考；调用原残差/原对偶证书/目标比较，不自行重写LP算法，不宣称唯一通量或生物学有效。

```bash
.venv/bin/python -m biomodel_agent case-run --material-ref MATERIAL_REF --store STORE
# 底层详细报告使用case-run返回的run_ref：
.venv/bin/python -m biomodel_agent report RUN_REF --store STORE
```

case-run是明确请求的**新数值诊断**，不是Agent/study恢复，也不改变旧任务的累计预算。运行前保存case_invocation，底层仍保存原科学输入/预留/收据/结果；不因失败自动重试、改参考、换参数或松阈值。可执行入口只有numeric_diagnostic_pass/failed；未知初值入口阻断。科学支持始终unknown，独立审核pending，不能把数字通过当作论文或模型能力认证。

CLI返回0表示本命令技术正常，case-run返回2表示阻断/数值失败，非法输入返回1。使用CLI或带`if __name__ == '__main__'`保护的脚本；现有科学工作进程用spawn，stdin/某些交互入口并不支持。首次stdin探针因此失败，实际失败记录保留；后续用独立脚本验收，没有改为fork或假称缓存成功。

## 导出与冷启动

case_material和case_run可用现有export命令导出，包中REPLAY.md给出case-run及正确material_ref。该命令启动新的数值诊断，不是恢复旧Agent或冻结研究。材料快照在脱离原目录后仍核对文件、模型/来源范围、候选原图和配方，参考不变；需要相应项目版本及科学依赖。

本轮实际发现并修复一个既有打包边界：某些原始文件字节本身就是存储对象JSON，两种表示共享同一SHA。旧导出器只复制对象，文件快照的get_blob随后失败。现在保留同SHA的对象和原始blob两份表示，内容/引用ID不变；case_run的重跑材料还必须确实包含在闭包中。先失败后修复的检查和独立Goodwin重跑均留档，不把打包缺失解释成科学模型失败。

## 知识候选与版本差异

已在允许范围内明确保存的新资料，可通过原来源导入器入库后再提议：

```bash
.venv/bin/python -m biomodel_agent case-propose --store STORE --material-ref MATERIAL_REF --source-ref SOURCE_REF --proposed-version 1.0.1 --summary '具体建议' --applicability '条件和限制' --author '归属标识'
```

候选保存基案例/版本、新源句柄和原始blob摘要、提议版本、说明、适用范围和归属。相同请求幂等；同一来源投影/说明/适用条件、不同归属记录可标记重复；同一案例和建议版本下的不同投影或内容保留差异引用。同一XML不同段落不会因raw blob相同而合并。原版本下换材料给出same_version_different_material提示。

这些是版本/资料差异线索，不是科学矛盾判决或权限认证。候选只接收已显式提供的source句柄，不接收model/result等非source对象；工具不自动抓取新文件、访问独立材料，也不能识别另被包装为source的资料角色。原始响应的保存沿用既有knowledge/evidence导入链。

所有候选保持pending_operator_and_domain_review、searchable=false、scientific_support=unknown；没有审批/发布/激活API。即使手工在存储中伪造approved字段，也不会进入固定目录搜索或替换案例。未来仍须实际审核来源、权限、条件和冲突，制作显式新版本。不是“使用一次便自动学成事实”。

## 实际验收与剩余项

36项专项覆盖：元数据按需搜索、四案装载、未知/改变版本/哈希/符号链接拒绝、来源/模型/参考角色隔离、严格初值阻断、三个实际数值求解、断开原文件路径的闭包重跑、同SHA对象/blob、重跑命令/依赖闭包、候选幂等/重复/差异、不同XML段落及伪造审批不入索引、CLI。

离线演练 `benchmarks/development/reuse_case_library.py` 在各自全新存储中实际运行四入口：三例数值诊断通过、一例初值阻断；每案导出后再重跑，结论相同。另记录四项明确候选控制，目录仍为四入口。它们是已有开发数据的数值/工程演练，重复不是独立任务，项目provider调用0、实际独立领域审核0。最终状态见 `artifacts/r03-case-library-20261026/acceptance.json`。

仍未完成：通用可扩充案例的实际审核/发布机制、源码版本迁移、wheel资料分发、Agent自主查找/选择与受控资料加入、科学适用性及真实任务收益评价。R08自动OCR/数学识别、R11真实服务/隔离/独立评价仍保留。旧冻结任务不回签或覆盖；原有留出隔离与外发限制保持，旧biomodel自动化保持暂停，没有推送或发布。
