# BioModel Agent 运行版

本目录只包含运行程序、安装与启动脚本、依赖和使用说明。不附带测试、测试报告、评测脚本、演示项目、预期答案或历史结果。程序从你的资料和空白运行目录开始。

## 下载与环境

[下载 Windows / Linux 纯运行版 ZIP](https://github.com/FrostingMaple/Biomodeling/archive/refs/heads/codex/runtime-windows-20261031.zip)

需要 **64 位 Python 3.12**，无需 GPU。Windows 可以原生运行，不要求 WSL；Linux / WSL2 也可运行。请解压到本地磁盘上的普通目录，建议路径简短，不放在网络共享或云同步目录。

首次安装需要联网下载科学计算依赖。Python 可从 [Python 官方网站](https://www.python.org/downloads/windows/)安装，安装时保留 Python Launcher。运行版不是包含 Python 的独立 EXE。

## Windows

在解压目录双击 `setup.cmd`，安装成功后双击 `start.cmd`。如果安装了多个 Python，可在 PowerShell 中指定解释器：

```powershell
.\setup.ps1 -Python 'C:\Path\To\Python312\python.exe'
.\start.ps1
```

CMD 启动器仅在本次 PowerShell 进程中允许执行随包脚本，不改系统执行策略。运行窗口会显示本机地址，通常是 <http://127.0.0.1:8765/projects>。窗口保持打开，按 `Ctrl+C` 退出；以后只需启动，不必重复安装。

```powershell
# 更换端口或数据位置
.\start.ps1 -Port 8766 -Store 'D:\BioModelData'

# 只检查环境，不发送模型请求
.\.venv\Scripts\python.exe -m biomodel_agent workspace-doctor
```

## Linux / WSL2

```bash
bash setup.sh
bash start.sh
```

请先安装 Python 3.12 及系统的 venv 支持。以后只运行 `bash start.sh`；可用 `bash start.sh --port 8766` 更换端口。

## 配置自己的模型服务

默认模型服务关闭，可以打开界面、导入资料和检查输入。需要 Agent 规划时，在启动终端设置自己的 OpenAI 兼容服务：

```powershell
$env:BIOMODEL_API_KEY = '替换为自己的密钥'
$env:BIOMODEL_BASE_URL = 'https://YOUR_PROVIDER/v1'
$env:BIOMODEL_MODEL = 'YOUR_MODEL'
.\start.ps1 -AllowProvider
```

Linux / WSL2 使用 `export BIOMODEL_API_KEY=...` 等环境变量，然后执行 `bash start.sh --allow-provider`。服务需支持 `/chat/completions`。程序不自动读取 `.env`，包内没有预置凭证、服务账户或 API 地址。

每次推进前界面展示当前项目的资料范围和冻结额度；模型会收到选入材料、相关工具反馈和后续选取的公开文献。确认后，Agent 在该范围内决定建模细节，需要讨论时暂停。缺失 usage 保持未知，计算完成不代表机制已被生物学证实。

## 使用自己的资料

1. 在首页 `/` 导入 UTF-8 TXT / Markdown 或原生模型 JSON，单文件不超过 1 MB。没有预置文献与结果。
2. 在 `/projects` 描述应用场景或复现目标，选择资料，设置规划、文献和迭代额度。
3. Agent 提出对象、粒度、证据、假设和实验；查看具体讨论并回答后继续。
4. 查看版本、实际计算检查、来源和结论；可导出报告及 ZIP 研究包。

支持有限 ODE、稳态 FBA、SSA 随机反应、1D 线性 PDE、布尔网络与格点个体模型。完整数学输入直接计算。源码中的案例库命令需要另行提供自己的案例材料；本运行版没有这些材料。

`workspace-init --output 新目录` 创建空白外部 Agent 工作区，不预置合成资料。`workspace_assets/templates/` 中六份 JSON 是结构模板；ODE 数值与选择仍须按实际资料解决。模板中的示意数值不是用户任务证据或参考答案。

运行数据默认在 `runs/workbench/`，退出后保留。升级前备份整个存储；跨操作系统或实现版本继续旧科学任务需核对指纹，请为 Windows 新任务建立新存储。不要同时从 Windows 和 WSL/Linux 操作同一活动存储，也不通过复制目录假定中断请求没有发生。

可选 PDF 布局提取与图像依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install '.[pdf,figures]'
```

浏览器上传支持上述文本与 JSON；PDF 使用 `source-ingest` CLI。公开全文导入保留原响应和提取警告；任意图表、复杂公式与补充数据自动建模尚未覆盖。开发用 POSIX 进程组评测监督器未随本运行版分发。
