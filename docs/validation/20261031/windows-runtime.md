# 原生 Windows 纯运行版交付

本次在既有研究项目里程碑之上完成 Windows 本地所有权、UTF-8 读取、空白工作区及安装/启动支持。用户使用说明见[运行包说明](../../../scripts/runtime/README.md)，实现与限制见 [Windows 范围](../../windows-runtime.md)。

目标为 `FrostingMaple/Biomodeling`；纯运行分支包含 109 个文件，不含测试、评测脚本、开发评分模块、示例材料、历史结果或凭证。主项目更新只涉及 `biomodel-agent-standalone/` 和入口 README。运行包保留六份结构模板，模板值不作为证据；初始化 0 来源、0 运行。

Windows 3.12.14 的实际安装、空白工作区、默认关闭模型的启动脚本和真实 Edge 验证通过。核心专项 38 通过、2 跳过：本机无符号链接创建权限，以及 POSIX FIFO 专属检查；Windows 目录 junction 拒绝、进程间独占和进程退出释放实际通过。原生六类模型计算、证据比较、讨论续接、确认结果恢复与工作台接口均有专项覆盖。

Edge 在最终安装版完成中文讨论、两轮模型计算、报告、180661 字节 ZIP 下载、只读刷新与 390px 布局；12 次脚本规划响应、0 次内层模型请求、2 次实际科学计算、0 外部页面请求、0 页面错误。下载研究包能独立重建相同报告。Linux 源码、Linux 纯运行安装版和 Windows 安装版的数学模型、设置、数值输出逐项一致；两个系统安装后的 98 个程序及资源文件逐一相同。

开发源码目录包含不随运行版发布的评分模块，因此开发版与运行版指纹不同；系统和 Python 版本也参与指纹。分别使用新存储验证，没有为旧对象改签指纹。最终运行 ZIP SHA256 为 `239188a2cfd0ac689aa826eef70d75f0f200cf035674b388f5452afa066ecffe`。

验证过程保留了 LF/CRLF 固定来源偏移不一致、Windows 默认 GBK 读取 UTF-8 对象、WSL 启动 PowerShell 的 PATHEXT 不完整，以及并行 Windows 演练等待超时等记录。旧来源固定控制显式写入 LF，产品原文不归一化；内部 JSON 显式 UTF-8，产品证据校验和额度不放宽。私有浏览器等待窗口按实测耗时调整，产品预算保持原值。

所有模型响应均为脚本，未新增外部模型请求，未使用独立评价集；真实论文复现数仍为 0。原生兼容性不证明真实 LLM 规划质量、生物学有效性或 token 收益。Windows 文件扫描在本机并行验证下较慢；大型项目性能仍需后续优化。开发用 POSIX 进程组监督器不随运行版发布。

本地原始记录保留在 `artifacts/windows-runtime-20261031/`，原始失败与最终通过分开保存。最终完整 Linux 回归 **2674 通过、1 跳过**（控制台 1471.08 秒）；Windows 专项 **38 通过、2 跳过**（控制台 960.63 秒）。源码、测试和分发拷贝均通过冻结哈希核对。详细接受摘要见 [机器可读记录](windows-runtime-summary.json)。已上传并核对远端：纯运行分支 [`codex/runtime-windows-20261031`](https://github.com/FrostingMaple/Biomodeling/tree/codex/runtime-windows-20261031)，提交 `c468f7516abb97626debaf77ddd1d23e13b5515f`；整合目录更新见 [PR #3](https://github.com/FrostingMaple/Biomodeling/pull/3)，提交 `8f67e1a73265225b8bb0f134ce75608bc2916440`。PR 尚未合入 main；运行分支可立即下载。
