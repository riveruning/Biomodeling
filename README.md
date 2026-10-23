# BioModel Agent

从指定材料构建小规模生物动力学 ODE 模型，生成 SBML，执行模拟、数值对照并保存可审计报告。也支持预定义候选机制的拟合与留出比较。

**研究原型，Python 3.12，无需 GPU。** 自动建模需要提供 OpenAI 兼容的 Chat Completions 接口；离线计算和安装演示不需要 API。当前不保证任意论文自动复现或开放式机制发现，详见[支持范围](docs/current-support.md)。

## 1. 下载与安装

下载本仓库的源代码 ZIP 并解压，或克隆：

```bash
git clone https://github.com/riveruning/Biomodeling.git
cd Biomodeling
```

在解压/克隆后的项目根目录执行。Linux / macOS：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Windows 用户请在 **WSL2 的 Linux 环境**中按上面的步骤运行。当前 Agent 的任务锁依赖 Unix `fcntl`，完整 Agent 流程不支持原生 PowerShell；原生 Windows 的离线安装检查见[平台说明](docs/getting-started.md)。

想使用本项目验收的固定依赖版本，见[安装与常见问题](docs/getting-started.md)。本地验证环境为 Linux x86_64 / Python 3.12；其他系统需有对应的 libRoadRunner 二进制包。

## 2. 先跑通离线示例

```bash
biomodel-agent doctor
biomodel-agent demo
```

正常输出包含 `"ok": true`，以及 `"execution": "succeeded"`、`"comparison": "pass"`。演示对合成衰减模型与解析解进行比较，报告保存在 `runs/demo/report.md`；它用于检查安装，不代表论文复现成绩。

所有命令也可写成 `python -m biomodel_agent ...`。

## 3. 接上自己的模型接口

复制配置模板（Linux / macOS 用 `cp`，PowerShell 用 `Copy-Item`）：

```bash
cp .env.example .env
```

编辑 `.env`，填入服务商提供的三个值：

```dotenv
BIOMODEL_BASE_URL=https://你的服务地址/v1
BIOMODEL_MODEL=你的模型名称
BIOMODEL_API_KEY=你的密钥
```

地址应是版本化接口根路径，程序会追加 `/chat/completions`。运行命令时，Agent 读取**当前目录**的 `.env`；已有环境变量优先，也可以使用 `biomodel-agent --env-file /path/to/.env agent ...`。`.env` 已被 Git 忽略。

检查配置，然后运行仓库自带的合成材料示例：

```bash
biomodel-agent doctor --llm
biomodel-agent agent --source examples/birth-death/source.txt --experiment examples/birth-death/experiment.json --store runs/my-agent
```

`doctor --llm` 只做本地检查；第二条会调用你的模型服务并消耗其额度。支持 JSON 对象输出的服务可加 `--json-mode`。Agent 可能完成、请求补充信息或因预算耗尽停止，状态含义和恢复方法见[使用指南](docs/usage.md)。

## 4. 换成自己的材料

将 `--source` 换成本地 TXT、MD 或 PDF 路径，将 `--experiment` 换成你的采样协议 JSON；多个来源可以重复传入 `--source`。输入缺少必需方程、参数或初值时，系统会保留缺口并请求澄清。

- [使用指南](docs/usage.md)：实验格式、已有 SBML、报告、导出和恢复。
- [安装与常见问题](docs/getting-started.md)：固定依赖、Windows、接口错误与可选 PDF 解析器。
- [文档索引](docs/README.md)：当前能力、架构、开发与历史研究记录。
- [开发说明](CONTRIBUTING.md)：测试、打包和研究证据管理。

## 目录说明

| 路径 | 用途 |
|---|---|
| `src/biomodel_agent/` | 产品运行代码 |
| `examples/` | 首次接入的最小输入 |
| `.env.example` | 模型接口配置模板 |
| `docs/` | 使用说明与专题文档 |
| `tests/` | 测试和独立固定样本 |
| `benchmarks/` | 科学计算与研究评测输入、开发脚本 |
| `artifacts/` | 历史研究证据，初次使用无需下载 |
| `runs/` | 你生成的本地结果，自动忽略 |

源代码 ZIP 省略历史实验产物、内部任务和讨论记录。完整克隆仍保留它们，历史链接可在 [GitHub 完整仓库](https://github.com/riveruning/Biomodeling)查阅；整理前的进展记录见[历史首页](docs/history.md)。
