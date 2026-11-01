# Windows 原生运行与纯运行包

面向使用者的安装说明及 Windows / Linux 脚本见 [运行包说明](../scripts/runtime/README.md)。用 `scripts/build_runtime_distribution.py --output 新目录` 从当前产品生成运行目录和 ZIP；生成器按白名单复制运行代码、界面、操作说明与六份结构模板；同目录中的 `literature_scoring.py` 仅属开发评分，不进入运行包，不复制开发输入、固定测试、报告或运行记录。

## 运行与验证范围

目标环境为 64 位 Python 3.12、Windows 本地磁盘及独立 venv。ODE 使用 libSBML / RoadRunner，FBA 使用 SciPy，其余有限原生模型沿用原执行器。Windows 的进程所有权使用禁止共享的 `CreateFileW` 句柄，POSIX 使用 `flock`；关闭句柄或进程结束时释放，锁标记文件本身可以保留。目录和最终文件的符号链接 / 重解析点保持拒绝。

原生 Windows 与 Linux / WSL2 不同时操作同一活动存储；不同系统、Python 版本或实现指纹的旧任务不自动认领为可恢复。工作台默认关闭模型服务。开发评测中的 POSIX 进程组监督器仍在 Linux / WSL2 使用，不随运行包发布。

## 分发范围

运行包的 `workspace-init` 创建空白工作区；开发仓库保留既有合成控制。运行模板是输入结构说明，不提供任务证据或预期答案。自己的资料、密钥和运行结果都保留在运行者的本地目录。

同学仓库为 `FrostingMaple/Biomodeling`。独立运行分支以运行包为根目录；主分支更新只涉及 `biomodel-agent-standalone/` 和其入口说明，保留既有整合工程。

实际验证结果与版本摘要见本地 `artifacts/windows-runtime-20261031/`；冻结接受摘要见 [跨平台交付记录](validation/20261031/windows-runtime.md)。所有模型响应使用脚本，本次没有新增外部模型请求。平台兼容不能证明真实 LLM 规划质量或真实文献复现率。

Windows 验证发现并处理两项实际差异：固定来源控制显式写入 LF 以保持精确偏移；内部 UTF-8 对象和模板显式按 UTF-8 读取，避免系统默认 GBK。并行运行多个验证存储时，Windows 对象扫描明显慢于当前 Linux 环境，私有演练的等待上限需与实际耗时匹配；这不改变产品请求额度、证据要求或恢复规则。原始超时和失败单独保留，不当成科学计算失败或已通过结果。
