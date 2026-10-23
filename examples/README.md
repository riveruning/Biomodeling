# 首次接入示例

安装项目后，在仓库根目录先运行 `biomodel-agent demo`；它内置合成衰减模型和解析参考，不依赖示例文件或网络。

`birth-death/` 提供最小 Agent 输入，原样取自公开的仓库合成开发样本：

- `source.txt`：明确声明初值、参数、方程与时间单位。
- `experiment.json`：模拟采样时刻。

配置 `.env` 后运行：

```bash
biomodel-agent agent --source examples/birth-death/source.txt --experiment examples/birth-death/experiment.json --store runs/my-agent
```

这条命令会实际调用你配置的模型服务；不同服务、模型和返回内容可能导致澄清、格式错误或预算耗尽。该输入是合成示例，不是真实论文，也不是独立评测集。
