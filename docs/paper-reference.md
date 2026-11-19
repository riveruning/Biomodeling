# R10：论文图像参考与定量诊断

Goodwin 已有案例新增独立的图像参考路径：保存原出版图、像素坐标标定、颜色/图例排除规则、逐列原始提取值和误差区间；冻结模型及运行配置后执行，再比较 Y/Z 的周期、最小值和最大值。CLI 为 `paper-reference`、`paper-protocol`、`paper-compare`。它不会更改原有 ODE run 的科学状态，也没有接入 Agent 的自动科学认证。

`examples/goodwin/reference.json` 是 SciPy 数值参考，**不是作者数据或原图数字化数据**。两种参考具有不同产物类型，不能互换。本轮图像参考来自 [Gonze & Abou-Jaoudé (2013), Figure 2B](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0069573)。原 PNG 逐字节保存并绑定 SHA-256，来源依赖闭包随案例保存；无需联网或访问独立评测集。

## 当前结果及边界

保存于 `artifacts/r10-paper-reference-20261025/acceptance-demo/comparison-report.md`：六项 Y/Z 特征均在声明的图像测量区间内，模型周期约 39.7 a.u.。这只是同一已见开发案例的回溯性诊断；先前已看过视觉结果、周期与幅值，本次不能称为预注册或盲测。新运行之前冻结了当前提取规则和比较配置；没有调整参数、拟合相位或事后改阈值。

本次检查的原文段落及图注未给出初值；没有穷尽搜索全部补充材料或作者代码。模型沿用分析者设定 X=0.1、Y=0.2、Z=0.3，原始瞬态和逐点原图误差 **not_run**。X 的幅值接近线宽/坐标标定分辨率，明确排除其幅值验收。Z 被图例遮挡的部分保持缺失，不插值、不跨缺口计算周期。

## 方法

- 原图 2050×872，PNG 调色板按原像素转换 RGB；不缩放、不应用色彩增强或 ICC 重映射。Figure 2B 的 x=1231.5..1991.5 对应 t=0..200，y=86.5..740.5 对应数值 3..0。轴中心位置为开发者人工识别，尚无独立校准审核。
- 按已保存通道阈值和差值筛选每列颜色像素，记录中位 y 及最上/下像素。图例矩形排除；红色 ROI 排除 y=1 虚线。未经平滑或插值。每点区间为线宽包络加声明的 1.5 px 标定/采样余量；这是测量假设，**不是统计置信区间、真实误差保证或重复实验**。
- 特征为局部峰/谷的中位数及同一连续片段内相邻谷值间距的中位数。prominence 和最小间距写入标定文件；保留全部峰谷和计数。平台宽度纳入时间区间。
- 模型沿用旧稳定段选择，比较 [400,600] 与 [600,800]。三类特征相对漂移须 ≤1%，各窗和图像各至少两项特征；不满足则 not_comparable。此门限是开发诊断，不能证明极限环存在。最后窗口的特征与图像区间逐项比较，不做相位对齐。
- 比较前，从保存的原始图片重提取像素和特征；重检模型哈希、输入、时间网格、求解器、严格模式、非负目标、数值有限性和收敛记录。改模型/配置后的运行不能套用同一协议。内容寻址可追溯不等于认证来源语义或领域正确性。
- 单独以手写 RHS 的 SciPy DOP853 复算同一分析初值，报告固定尺度 10、阈值 0.001 的数值误差。这只检查两种实现/求解结果，不替代论文数据或独立模型审核。

## 离线重建

ODE 依赖按根目录安装；图像准备、比较和绘图另需 `.[figures]` 中记录的 Pillow 12.3.0 / Matplotlib 3.11.1。本机使用已有 bioinfo Python 3.14 环境执行图像步骤、项目 Python 3.12 环境执行 RoadRunner，不混用二进制库。也可在同时装有上述依赖的单一受支持环境运行三个阶段。以下命令的输出目录需全新：

```bash
PYTHONPATH=src /home/riveruning/miniforge3/envs/bioinfo/bin/python benchmarks/development/quantify_goodwin_figure2.py prepare --output /tmp/goodwin-r10
.venv/bin/python benchmarks/development/quantify_goodwin_figure2.py simulate --output /tmp/goodwin-r10
MPLCONFIGDIR=/tmp/biomodel-r10-mpl PYTHONPATH=src /home/riveruning/miniforge3/envs/bioinfo/bin/python benchmarks/development/quantify_goodwin_figure2.py compare --output /tmp/goodwin-r10
```

也可用 CLI 对已有存储逐步处理：

```bash
python -m biomodel_agent paper-reference --store STORE --image examples/goodwin/figure2-reference/publisher-figure-2.png --calibration examples/goodwin/figure2-reference/calibration.json
python -m biomodel_agent paper-protocol --store STORE --reference-ref FIGURE_REF --model-ref MODEL_REF --protocol examples/goodwin/figure2-reference/protocol.json
python -m biomodel_agent paper-compare --store STORE --protocol-ref PROTOCOL_REF --run-ref RUN_REF
```

比较报告、原始数字化 CSV、全部特征、独立数值互验、PNG/SVG、图像元数据与替代文本、可移植比较闭包分别保存。通用 bundle 自动生成的 `replay` 提示针对执行产物；本类诊断应按其 `protocol_ref`/`run_ref` 使用 `paper-compare` 重算。

## 审核和后续

本次仅开发者检查，不伪装独立领域审核。未知原始初值、模型实现的独立语义核对、像素标定和排除规则审核、原始数据/补充材料进一步查证，以及未见案例的事前协议仍未完成。R11 的真实 LLM 对照、领域审核和消融不由这张图证明。

图示为内部临时审核图，无目标期刊或合规声明。使用 scientific-visualization 技能组织数据完整性、图形和审核记录；软件方法引用为 Kassis et al. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*, [arXiv:2609.00065](https://doi.org/10.48550/arXiv.2609.00065)，不是本项目科学有效性的证据。
