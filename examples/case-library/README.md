# 已见开发案例库 v1.0.0

固定目录面向**显式模型复用**，不是独立评测集或默认Agent检索源。前两个入口属于同一Goodwin家庭，四入口不能当作四篇独立论文。

| ID | 类型 | 配置/参考 | 已知缺口 |
|---|---|---|---|
| goodwin-exploratory | ODE | 原始来源绑定模型；分析者初值X=.1/Y=.2/Z=.3；固定0..240网格；SciPy数值参考 | 原论文初值未知，参考不是作者数据，物理单位与来源语义未确认 |
| goodwin-original-initials-unknown | ODE来源/阻断 | s2a JATS、8张原图和已见视觉候选；不加载探索模型或SciPy答案 | 三项初值None，执行明确阻断；视觉候选独立审核pending |
| fba-analytic-chain | 稳态LP | 固定化学计量、摄入上界和目标；解析标量参考 | 不是论文重建或生物学验证，无动态/唯一通量认证 |
| fba-textbook | 稳态FBC | 已保存公开教材原模型、来源收据、固定标量参考 | 已见开发模型；标量吻合不证明唯一通量或生物适用性，上游许可未独立审核 |

`catalog.json`保存版本、来源、方程/符号、参数/初值、适用范围、参考类型、缺口、配方及逐文件SHA。目录字节摘要和逐案摘要由产品代码固定；不是读取用户JSON后相信其自报版本。`goodwin-visual-candidates.json`复用先前已逐图查看的候选，不是新增OCR结果或独立审核。

```bash
.venv/bin/python -m biomodel_agent case-search --query goodwin
.venv/bin/python -m biomodel_agent case-inspect --id goodwin-original-initials-unknown --version 1.0.0
.venv/bin/python -m biomodel_agent case-load --id fba-analytic-chain --version 1.0.0 --store runs/reuse-chain
# 使用load返回的ref，另行明确运行：
.venv/bin/python -m biomodel_agent case-run --material-ref MATERIAL_REF --store runs/reuse-chain
```

搜索只读目录元数据，不扫描/索引模型、轨迹参考、gold或候选。装载只导入所选资料，保存不可变case_material、新model/source句柄和配方，不执行模型、不修改已有任务。参考文件单独用于数值检查，不进入source_refs。可按需把返回source_refs明确传给**新**Agent任务；FBA使用其model_ref与既有FBA路由。库没有注册Agent默认自动检索/选择动作，不自动判断科学适用性。

`case-run`创建新的确定性数值运行，不是旧Agent/study恢复，不绕过原任务的累计预算/版本边界。缺初值入口返回blocked_missing_initials；可执行入口也仅可能获得numeric_diagnostic_pass，科学支持始终unknown、独立审核pending。底层报告使用返回的run_ref读取。失败保留、不自动重试或调参。

当前目录版依赖项目checkout中的固定资料；wheel内嵌分发尚未实现。装载后可导出case_material/ case_run闭包，包中REPLAY.md给出正确的case-run命令，不依赖原资料路径再运行；仍须相同项目/科学依赖。使用CLI或带`if __name__ == '__main__'`保护的脚本，stdin入口可能不兼容现有spawn科学工作进程。

新增资料先用已有导入工具显式保存，再提交候选：

```bash
.venv/bin/python -m biomodel_agent case-propose --store STORE --material-ref MATERIAL_REF --source-ref SOURCE_REF --proposed-version 1.0.1 --summary '具体的新增资料或修订' --applicability '适用条件与限制' --author '归属记录'
```

候选保留源引用/原始字节摘要、基版本、建议版本、适用条件、归属、重复与差异线索；只进待审区，不能搜索、激活或替换目录。同一XML的不同段落不因原始blob相同而合并。差异提示不是科学矛盾判决，作者名称和材料角色也未经认证。本版本没有审批/发布API，未来审核通过仍须显式制作新版本。

详细接口、数据边界和验收见[案例库说明](../../docs/case-library.md)。项目provider调用、独立领域审核均为0；不包含独立评测或受限材料。
