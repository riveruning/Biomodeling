# 四类原生模型示例

各有 `<type>.json` 和 `<type>-settings.json`：

| type | 合成控制 | 独立可核对性质 |
|---|---|---|
| stochastic | 常数移入/单分子死亡 | 解析均值、方差 |
| pde | 零边界扩散/衰减 | 衰减正弦连续解与空间误差 |
| boolean | 两节点负反馈 | 00→10→11→01→00，周期4 |
| individual | 死亡/迁移/分裂细胞 | 一格一个细胞，人口与亲子事件一致 |

以上不是论文复现，参数不来自文献。以个体模型为例：

```bash
.venv/bin/python -m biomodel_agent typed-import examples/model-types/individual.json --store runs/native
# 返回 ref 填入 MODEL_REF
.venv/bin/python -m biomodel_agent typed-run MODEL_REF --config examples/model-types/individual-settings.json --store runs/native
# 返回 ref 填入 RUN_REF
.venv/bin/python -m biomodel_agent report RUN_REF --output runs/individual-report.md --store runs/native
```

其他类型相同。契约、预算、Agent入口和局限见[六类支持说明](../../docs/model-types.md)。旧 ODE/FBA 案例入口保持原样。
