# T08c：共同 HTTP 传输与离线外发准备

`benchmarks/development/comparison_transport.py` 提供三种工作流共用的 `ChatCompletionTransport`，实现既有 `request(payload, timeout_s)` 契约。产品源码、科学后端、T08a 评分器、旧测试及原材料不变；T08 整体仍待真实模型对照与独立审核。

## 请求与兼容范围

请求采用现有非流式 Chat Completions 子集：model、messages、temperature、top_p、max_tokens，以及清单非空时的 seed。传输层不补系统提示、不删除参数或自动换协议；请求值须与共同冻结配置一致。HTTP JSON 使用固定排序和 UTF-8 编码，传输收据的 request_body_sha256 可以从已审计请求重新计算，字符预算仍按 T08b 消息 JSON 口径计量。

这不是所有模型通用的接口。2026-10-19 核对的 [OpenAI 官方文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)将 max_tokens 列为旧参数、部分模型不支持，seed 也不保证确定性。本轮保留现有比较协议，未选择或迁移模型。服务不接受参数时如实失败；之后如需新协议，应对三组一起修改、重新冻结并验证，不能只给某一组删参或增加推理预算。

响应必须是一个 index=0、finish_reason=stop、assistant 字符串消息；拒绝截断、拒答、原生 tool_calls/function_call、多候选、重复键、非有限 JSON 和过深嵌套。具体动作继续由现有严格解析器与共同工具白名单校验。返回 model 与清单不符时停止，未返回 model 保留未核验状态。服务自报 model、声明 revision 和 backend fingerprint 均不能认证真实底层模型；fingerprint 仅保留摘要用于发现变化。

## 预算、错误与记录

工作流先预留预算、保存实际请求，再进入 HTTP。传输只做一次尝试；HTTP 错误、限流、连接异常、超时和无效响应均不自动重试、不按 Retry-After 睡眠。服务错误不读取原始错误体。请求失败仍保留调用费用计数，缺少 usage 保留 null，不估算 token 或金额。

服务地址仅支持 HTTPS，或显式本机 HTTP；拒绝 URL 凭证、查询串和片段。每次客户端禁用环境代理和自动跳转。密钥只在明确的网络 request 内从指定环境变量读取，不写入身份、请求、收据或异常。构造对象、准备和校验不读密钥、不创建 HTTP 客户端。单元测试的网络分支只使用专门的假环境变量，并把客户端替换为内存 HTTP。

每次交换另存 `evaluation_transport_exchange`：固定身份摘要、实际 HTTP 请求体摘要、HTTP 状态、已读响应字节、结果类别、Retry-After 数值及可选 backend fingerprint 摘要。收据链接至原请求与预留，最终会话平坦引用这些产物。原动作文本和合法 usage 仍保存在原 evaluation_response。连接未返回、进程终止或缺失元数据时不猜测远端结果；远端可能已消耗费用，不能由未知结果触发隐式重放。

响应按解码后的字节数限制，检查 UTF-8 和 JSON；这是读取/解析上限，不是完整进程内存上限。HTTP 单阶段等待采用较小的请求期限和剩余总预算，读取过程中也检查期限；总硬期限仍由 T08b POSIX 工作进程监督，包括不遵守 httpx 计时的测试回调。已被终止的进程可能来不及保存最后的 HTTP 收据，此时原请求和预留保留、响应未知。

## 恢复身份

旧通用注入接口仍可使用。新 HTTP 身份增加 profile、实际 network/mock 模式、服务地址、环境变量名称、请求期限、响应字节上限、模型配置摘要、httpx 版本及四个执行/评分模块的源码哈希。恢复比较其摘要；同一 Python 类换服务、改期限或换模式不能继续旧会话。密钥值不入身份，因此凭证轮换和提供方真实模型变化不受此摘要认证。

`httpx.MockTransport` 是唯一允许注入的内存后端，此时 evidence_kind 为只读的 scripted_control，且使用固定假令牌，不读取实际密钥。默认网络后端为 live_model。Python 回调仍是受信任代码；这些声明和哈希不是隔离恶意代码的系统沙箱。

## 准备与校验

CLI 只提供 prepare/check，不执行线上调用。由操作者另存新 live_model 清单，填写实际提供方、模型与版本、设置和预算；另存传输配置，例如：

```json
{
  "base_url": "https://YOUR_PROVIDER/v1",
  "api_key_env": "BIOMODEL_API_KEY",
  "request_timeout_s": 60.0,
  "max_response_bytes": 2000000
}
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/comparison_transport.py prepare \
  --manifest path/to/new-live-manifest.json \
  --transport path/to/transport.json --output /tmp/comparison-preparation

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/comparison_transport.py check /tmp/comparison-preparation
```

输出目录须不存在。包保存 manifest.json、transport.json 和 preflight.json；逐项列出案例/阶段、来源/实验路径、SHA256、字符与 UTF-8 字节数，并冻结服务、实现、模型/预算及计划摘要。预期外发还包含任务/工具指引、允许来源片段与预录澄清、对话历史及科学工具反馈；动态请求仍须实际审计。private_rubric 保留在控制器，不进入工作流投影。

状态始终为 prepared_only_no_authorization。包不是授权凭据，修改成 approved 会在校验中拒绝；材料、服务、预算或实现变化也须重新冻结。真实执行还需依据用户对具体材料/服务的授权，把新传输注入现有 run_comparison；通用库是受信任执行接口，准备包本身不充当拦截任意 Python 网络调用的安全边界。

[本轮准备示例](../artifacts/audit-t08c-http-transport-20261019/preparation-example/preflight.json)使用 `.invalid` 地址和 example-not-selected 模型，不能当成已选择服务或已获批准的运行计划。旧四次 c13/c18 USTC 外发拒绝仍有效；没有读取其材料、换路或重试。

## 实测与限制

修改前两项检查得到缺模块错误，说明缺少 HTTP/准备包能力，不作为已有产品行为缺陷。首轮 2 项、扩展 62 项通过；再补原任务恢复、期限、配置完整性、拒答/截断/原生工具与错误类型检查，最终专项 **74 项通过**。共同适配与评分定向 165 项通过，最终完整 **1309 项通过**（330.81 秒）；原测试和产品源码不改。源码差异、快照与原始日志见 [T08c 工程验收](../artifacts/audit-t08c-http-transport-20261019/acceptance.json)。

HTTP 模拟中的决策仍是 F1 原有字面脚本，材料、模型占位配置、预算与清单不改。30 阶段均保存：66 次内存 HTTP 请求和逐条收据，18 个阶段技术完成，12 个初始缺口阶段仍追问；实际严格模拟与收敛加严积分各 18 次。三组/重复的轨迹相同，全部 18 次数值输出与 F1 一致，4 次产品领域 Agent 原生恢复的父任务关系已核对。实际请求体摘要逐条从已保存请求复算一致，收据链接至调用前预留；详见 [HTTP 与数值诊断](../artifacts/audit-t08c-http-transport-20261019/http-scientific-results.json)。

全部 assessment 仍 unreviewed，semantic_support 为 unknown，线上推理调用及独立人工审核决定均为 0；缺失 usage 保持 null。准备示例的纯 CLI check 也已实际运行。旧 2425 个 T08a/T08b/F1 文件逐项哈希校验保留。它能验证传输与工作流连接，不能提供真实模型成绩、真实服务兼容性、费用或稳定性结论。

用户在本轮末明确后续主要与 **Codex 产品执行**比较，候选先记为 gpt-6.1-sol 和 gpt-6-astra（后者暂按用户原文“6 Atra”理解），具体服务配置讨论按用户要求暂缓。[意图记录](../artifacts/audit-t08c-http-transport-20261019/comparison-intent.json)单列候选、未定地址与授权状态。

下一项 T08d 先定义 Codex 对照协议、真实执行/记录接口及共同材料与预算，明确工具/权限/环境差异；确定配置和材料授权后再实跑。产品对 Codex 的系统比较用于评价实际表现，另保留同底层模型的三种工作流比较来判断框架贡献，模型差异与框架贡献分开归因。当前 T08a/T08b 三种内部工作流仍按原协议保留，Codex 需有自己的真实执行证据。

独立领域审核、未见任务和 Memory/RAG/Skill 消融仍待补。已见合成材料保持开发属性，未增加论文或读取独立评测集。
