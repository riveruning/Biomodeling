# R08-1：JATS 图片缺口与视觉候选的来源链

2026-10-26。本轮针对已有 Goodwin 开发材料接通 `jats_sources.py` 和两项显式 CLI，不更改默认文本/PDF导入方式。实际输入是出版者 JATS/XML，旧脚本有意把图片公式保留为占位符，**不是扫描PDF解析失败**。本轮没有接通OCR或数学识别，不能据此宣称R08整体完成。

## 原始投影

`source-jats`读取明确指定的单个`sec id`，保存原XML字节、所选逻辑段落的文本投影、XML相对索引位置、原生节点结构及缺口。图片、MathML、表格和上下标保留可定位的未解释标记；不把MathML扁平化成已识别方程，不把上下标连成普通字符，不自动解释表格配对或修正Unicode负号。

图片映射由操作者显式提供，只能对应所选范围内的href。路径必须在XML目录下，拒绝绝对路径、上级跳转、符号链接与过大文件；仅做PNG/TIFF签名初筛并保存字节，**不以签名验证图像真实性或识别内容**。没有映射或未保存文件的图片仍保持local_asset_unavailable，不自动联网下载。

外部DTD不加载；内部DTD/实体声明和XInclude拒绝。输入UTF-8 XML最多2MB、树最多2万个节点/64层，文本最多20万字符、缺口最多128个，映射最多64个，单图2MB、总图16MB。表格/MathML内部节点原样保存在结构中，不另做嵌套图片资产解释。它不是完整JATS标准验证器，也不是PDF阅读顺序/OCR适配。

来源页码1表示一个逻辑JATS段落，**不是PDF第1页**；XML索引路径与字符范围也不是PDF坐标或文件字节偏移。XML文本顺序不证明印刷版式顺序正确。

## 带来源链的视觉候选

`source-transcript`仅向原始JATS投影派生新的来源，不覆盖原来源。每项明确绑定缺口ID、已保存公式图片的blob和有界候选文字；必须记录分析者及转录/规范化说明。

新来源保留全部原始文本前缀，再独立追加“ANALYST VISUAL CANDIDATE；independent review pending”区块。只支持直接从原始投影派生，不能把多轮派生链当作原文。转录记录和来源始终标为analyst_checked_pending_independent / scientific_support=unknown，不存在自动“领域通过”开关。分析者名称是归属文本，不是身份、独立性或资质认证。

这条路径用于显式开发预审和探索输入，**候选文字不是作者原文的逐字引文**。录入者可以误读图片；即使文本绑定matched，也只验证声明/值/表达式在候选文字中一致，不能证明图片被正确阅读。现有通用claim类型、review_status和语义未知规则保持；引用中新的locator明确标记视觉候选层。

新增的JATS来源校验接入现有claim创建及重新验证：从不可变原XML重建投影，核对资产依赖、候选文字、原来源、派生文本blob及能力/科学状态；重新计算定位。视觉区块的引用包含原来源、转录记录、原始公式图blob、XML路径与候选字符范围。删掉图片锚点、换文本/资产/能力、假称OCR完成或科学通过，不能作为该契约下有效的新引用。

这保证受支持来源链的完整性与可追溯性，不认证作者或操作者身份，不阻止另行提交一份人为编造的普通文本；来源科学语义仍须审核。新增来源必须显式加入新任务，不能改写冻结研究的来源范围或绕过现有适用性与恢复检查。

## Goodwin 实际诊断

只使用已有 `benchmarks/inputs/literature/gonze2013` 的s2a段落和已保存图片，未增加论文或读取独立评测材料。

| 项目 | 实际记录 |
|---|---:|
| 所选XML内图片缺口 | 24（22公式/符号图片、2幅图） |
| 已保存并绑定的目标公式图片 | 8 |
| 视觉候选区块 | 8 |
| 受检查的参数/速率规则声明 | 11 |
| 原始投影的上述文本声明matched | 0 |
| 视觉候选的上述文本声明matched | 11 |
| 保留未知的原始初值 | X、Y、Z共3项 |

8张图为e004/e005/e006/e010与e021/e022/e023/e024。本轮Codex逐图视觉自查，使用既有PNG查看副本；Pillow逐像素比较确认PNG与对应TIFF在相同尺寸、RGB模式展开后相等。TIFF原字节保持，查看副本及哈希/等同性记录另存。该检查只证明本地两份图像对应，不认证出版者下载链或代替独立领域审核。

视觉规范化明确记录：Ki表示原K_i，`**`表示幂，连等式拆成单独Parameter声明，1/10写为0.1。原始初值不从曲线、既有探索协议或先验知识补齐。已有辅助转录文本不被覆盖，当前候选仍是已见开发案例的AI自查。

0→11是**已指定候选文本的机械可绑定性诊断**，不是LLM抽取分数或自动OCR准确率；预期值与候选由同一开发者准备，独立审核pending。保留全部24处原始缺口，不能说剩余16处或作者全部内容已完成转录。

实际构建了初值全为None的草稿，现有`validate_draft`返回三项initial_missing并阻止执行。没有建立虚假的严格论文模型或运行新轨迹；项目provider/额外模型试验调用0，本聊天的Codex视觉预审不计作产品抽取评测。科学支持unknown，原图/模型/转录的独立审核仍待做。

## 使用与复核

```bash
# 用当前已有材料重建全部诊断；目录需全新：
.venv/bin/python benchmarks/development/trace_goodwin_images.py --output /tmp/goodwin-image-trace
# 显式导入所选段落，assets.json是href到XML目录相对路径的映射：
.venv/bin/python -m biomodel_agent source-jats --store STORE --path benchmarks/inputs/literature/gonze2013/article.xml --section s2a --assets assets.json
# 显式新增视觉候选；candidates.json包含gap_id、asset_ref和text：
.venv/bin/python -m biomodel_agent source-transcript --store STORE --source-ref ORIGINAL_REF --candidates candidates.json --actor 'attributed analyst' --notes 'exact visual transformations and remaining limitations'
```

完整演练保存原始/候选文本、缺口/图片依赖、11项claim定位与绑定、原始图片映射、候选说明、缺初值草稿检查和可移植来源闭包。两份全新存储得到相同的原来源/派生来源/草稿引用。闭包携带XML、原始TIFF、候选和claim，不要求原项目文件留在原路径。

代码新增会改变原有实现指纹；旧R11a/系统对照的冻结包因此按设计拒绝，不能重新签名伪装旧任务可恢复。它们的历史验证只对应当时版本，需要新实验时按原协议重新prepare新任务，旧记录保持不动。

39项新增检查与130项既有来源/PDF相关检查共169项通过。完整回归、最终源码与实际演练以 `artifacts/r08-jats-images-20261026/acceptance.json` 为准。一次相关集命令误写了不存在的test_evidence.py，日志保留；该次未运行任何测试，不计作验收。

## 未完成的R08部分

当前没有可用Tesseract，OCR与公式识别明确not_run，没有安装模型或调用云服务。成熟OCR/数学识别组件的选择、有限PDF页型/裁剪/坐标、置信或矛盾处理、公式/表格配对的独立审核，以及同材料raw与真实审核转录的端到端效果仍待做。解析缺口不能解释成作者没有提供信息；源文本匹配不能代替这些验证。

R11b真实服务和隔离条件仍待解决。本轮不重试其失败隔离探针，不访问独立评测集或c13/c18、不联网/使用凭证、不推送/发布，旧biomodel自动化保持暂停。
