# 固定回归样本

这些文件从已有历史产物原样复制，使常规测试不依赖整套研究归档：

- `baseline-plan-v1.json`：原 `artifacts/audit-t04-study-execution-20261019/baseline-plan-v1.json`，验证旧计划兼容。
- `http-mock-execution/*.json`：原 `artifacts/audit-t08c-http-transport-20261019/http-mock-execution/` 中的同名文件，验证旧报告可复算和记录导入。

HTTP 样本属于脚本控制，不是真实模型能力证据。收据里的原机器路径属于历史元数据，测试不访问这些路径。不要重新生成或更改这些冻结样本来迎合测试结果。

旧报告/记录导入测试使用样本中冻结的工具链指纹做确定性输入；不声称当前安装环境与历史环境相同。生产评分与恢复流程仍会拒绝不匹配的真实工具链指纹。
