# 开发说明

产品安装和接口配置从 [README](README.md)开始。日常使用无需开发监督器、任务清单或历史研究目录。

## 开发环境与检查

在 Python 3.12 虚拟环境中运行：

```bash
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps --editable .
biomodel-agent doctor
biomodel-agent demo --store runs/development-demo
python -m pytest -q
```

数值测试需要允许创建子进程。主要回归样本在 `tests/fixtures/`，不依赖整套历史产物；少量历史付费/研究集成检查仅在完整证据存在时执行，精简包会明确跳过它们。

修改 DSH 开发监督脚本时，额外使用本地 Node.js 运行：

```bash
node scripts/tests/dsh-supervisor.test.mjs
node scripts/tests/dsh-runtime.test.mjs
```

DSH 只用于外部开发，不是产品运行依赖。专题说明见 [development-workflow.md](docs/development-workflow.md)。

## 精简交付包

```bash
python scripts/package_starter.py
```

输出 `dist/biomodel-agent-starter.zip`。打包器使用明确的源码目录与文件清单，不包含 `.venv`、真实 `.env`、缓存、内部任务、讨论记录、机器运行配置或历史实验子目录。输出已存在时拒绝覆盖；换一个 `--output` 或先移走旧包。

`.gitattributes` 的 `export-ignore` 同时使后续 Git 源码归档省略历史产物、内部任务、讨论和运行配置；完整 Git 克隆仍保留原始证据。需要复算历史研究时，从完整仓库获取相应路径。Python 安装包只包含运行代码，不包含研究产物。

## 文件管理

- 产品代码放 `src/biomodel_agent/`，首次使用输入放 `examples/`，使用文档放 `docs/`。
- 新运行结果放 `runs/`；`runs/` 与新增的 `artifacts/*` 默认忽略。已经版本化的历史证据仍保留。
- 测试依赖的小型固定输入放 `tests/fixtures/`，注明来源和用途；不靠下载旧实验目录才能运行回归。
- 密钥只保存在本地环境或已忽略的 `.env`。配置示例使用占位值。
- 研究记录保留失败、负结果、科学状态和来源边界；开发演练不能改称论文复现或独立评测成绩。

历史首页移至 [docs/history.md](docs/history.md)。已有独立评测集不属于日常安装或冒烟测试输入。
