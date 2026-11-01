# 来源随机模型合成示例

本目录是开发材料，不是文献复现。

- [birth-death.txt](birth-death.txt)：反应、整数初值、随机常数、逐反应单位及混合/动力学假设。
- [request.template.json](request.template.json)：来源任务和显式 SSA 执行设置；默认不指定原生模型。
- [proposal.template.json](proposal.template.json)：建议、原生数学草稿、单位表、完整行引用及逐项映射。

先通过工作台或已有 source-import 命令导入文本，把两个模板中的 SOURCE_REF 替换成返回的来源引用。新建任务后导入建议、展开数学草稿与单位/假设、核对原文，确认未知项并显式采纳。采纳不会计算。若没有启用模型服务，只能进行离线导入/检查/采纳；计算可用下面的脚本化演练验证。

~~~bash
.venv/bin/python benchmarks/development/demo_stochastic_proposals.py --output artifacts/my-source-ssa-demo
~~~

演练实际运行本机 HTTP、来源检查、原任务追问续接、SSA 与导出，模型回答由脚本提供，另有缺速率和单位冲突负控。输出目录须不存在，不调用外部模型。

契约与边界见[有限来源到随机反应模型](../../docs/source-stochastic.md)。随机常数按每组反应物组合定义；不从浓度速率换算。有限轨迹不证明真实生物学适用性。
