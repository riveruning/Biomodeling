# 来源布尔模型建议模板

三个文件都是合成开发材料：完整文本声明、研究任务配置模板、建议模板。它们不需要线上服务即可准备和检查建议。

1. `source-ingest boolean-source.txt --store STORE` 导入文本。
2. 将两个 JSON 模板中的 `SOURCE_REF` 替换为导入结果的 `ref`（来源引用）。
3. `research-task-create --config boolean-request.template.json --store STORE` 保存任务，未选型返回 2。
4. `research-proposal-create REQUEST_REF --config boolean-proposal.template.json --store STORE` 保存建议。
5. `research-proposal-show PROPOSAL_REF --store STORE` 检查原文引用与草稿。
6. `research-proposal-select PROPOSAL_REF --candidate direct --reviewer '研究者' --reason '核对原文后选择' --store STORE` 生成新任务修订和原生模型；这一步不运行模型。

命令前加 `.venv/bin/python -m biomodel_agent`。模板中的 proposal_max_calls=1 只允许后续显式发起一次建议请求；上述本地 create 路径不会调用服务。实际执行及凭证设置见 [完整说明](../../docs/task-proposals.md)。

一条命令运行脚本化建议、显式采纳、实际 Boolean 计算及失败控制：

```bash
.venv/bin/python benchmarks/development/demo_task_proposals.py --output artifacts/my-proposal-demo
```

输出目录须尚不存在。脚本拦截 provider 构造，不发送 HTTP 请求；报告继续标记 scientific_support=unknown。
