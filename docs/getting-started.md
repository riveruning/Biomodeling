# 安装与常见问题

首次使用按[项目首页](../README.md)操作。所有相对路径以项目根目录为起点；使用示例需要源代码 ZIP 或克隆仓库，单独安装 wheel 不包含仓库的示例和研究文档。

## Python 与依赖

使用独立的 Python 3.12 虚拟环境。激活后 `python -m pip install .` 安装产品及声明的依赖；当前已实际验证 Linux x86_64 / Python 3.12，其他平台需验证原生科学计算依赖是否提供兼容 wheel。

想使用项目验收时的固定版本：

```bash
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps .
biomodel-agent doctor
biomodel-agent demo
```

锁文件包含测试和构建工具，是 Python 3.12 的验收环境记录，不是跨平台二进制镜像。`pip install .` 使用声明的版本约束，可能解析到较新的依赖；遇到差异时优先使用锁文件。

如果 Ubuntu / Debian 创建环境时提示缺少 `ensurepip`，安装与解释器对应的 `python3.12-venv` 系统包，再重新创建环境。没有系统安装权限时，可使用已有的 Python 3.12 虚拟环境。

完整 Agent 流程使用 Unix `fcntl` 文件锁，**不支持原生 Windows**。Windows 用户应在 WSL2 的 Linux 环境中按项目首页安装与运行。若只检查原生 Windows 的依赖和离线计算，可在 PowerShell 直接指定解释器，无需改执行策略：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m biomodel_agent doctor
.\.venv\Scripts\python.exe -m biomodel_agent demo
```

这组 PowerShell 命令仅检查离线功能；`doctor --llm` 还会检查 Agent 所需的平台能力。如果原生依赖没有对应平台/架构的 wheel，可使用 Linux x86_64 环境或 WSL2；本项目不要求 GPU。

## 配置读取规则

- `agent`、`agent-resume`、`agent-recover` 和 `doctor --llm` 默认读取**当前目录**的 `.env`，不搜索父目录。
- `--env-file` 是全局参数，放在子命令前，例如 `biomodel-agent --env-file settings.env doctor --llm`。显式指定的文件不存在时会报错。
- 优先级为：命令行 `--base-url` / `--model` > 已有进程环境变量 > 文件值。恢复任务沿用冻结的模型配置。
- dotenv 值按字面读取，不执行 shell，也不展开 `${VARIABLE}`。含 `#`、空格等特殊字符的值请用引号包裹。
- 默认密钥变量为 `BIOMODEL_API_KEY`；可用 `--api-key-env MY_KEY` 指定其他变量名。`doctor` 仅显示配置是否完整，不输出密钥，不发网络请求。

## 模型接口排查

| 现象 | 排查方法 |
|---|---|
| `missing LLM settings` | 在运行目录放置 `.env`，填写全部三个值，执行 `doctor --llm` |
| `replace_template_value` | 模板里的 `YOUR_*` 尚未替换 |
| HTTP 401 / 403 | 检查密钥、模型访问权限及服务地址 |
| HTTP 404 | 确认根地址，例如 `https://provider.example/v1`；不要填写完整 `/chat/completions` 地址 |
| HTTP 429 | 检查服务商额度与并发限制；重试也计入 Agent 总预算 |
| TLS / 连接失败 | 产品适配器不读取系统代理环境变量；确认该地址能直接访问，或使用本地兼容网关 |
| 返回格式错误 | 服务需支持 Chat Completions；先不加 `--json-mode`，确认兼容后再启用 |
| `awaiting_input` / 退出码 2 | 查阅保存的任务和报告，可能需要补材料或增加预算，不一定是安装问题 |

公开接口要求 HTTPS；本地接口可使用 `http://localhost:端口/v1` 或 `http://127.0.0.1:端口/v1`。本地网关仍需设置密钥变量，填写该网关接受的值。

## PDF

默认使用 pypdf，只读取可提取文本；扫描件、图片公式和复杂表格可能需要人工补充转录。可选解析器安装方法：

```bash
python -m pip install '.[pdf]'
biomodel-agent source-ingest paper.pdf --store runs/pdf --pdf-parser pymupdf
```

可选 PDF 依赖单独锁定在 `requirements-pdf.lock`。能力边界见[PDF 适配器说明](pdf-parser-adapter.md)。
