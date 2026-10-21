# T03a：持久调用边界，不实现自动恢复

用户要求继续T03a，由DSH实现。基线811通过且源码未变。Codex已准备独立故障注入测试；先读本文件、agent.py、storage.py和新空invocations.py，只改这两个生产文件，不改测试、不探索其他材料。

## 1. 独立模块 invocations.py 的固定接口

`InvocationJournal(store, task_ref, *, parent_run_ref=None, calls=0, runs=0)`：读取已存在的agent_task及其max_calls（1..50），严格检查继承calls为0..max_calls、runs为0或1且均不能是bool。校验通过后保存kind=`agent_execution`，payload至少有version=1、唯一id（uuid）、task_ref、parent_run_ref、base_call_count、base_run_count、max_calls、max_runs=1。refs明确包含task_ref及存在的parent_run_ref。每次执行/恢复/显式替代分支均新建execution。

实例属性：`execution_ref`、`refs`（本次execution的所有成功保存调用记录ref的平坦列表）、`calls`、`runs`。

`invoke(name, callback, *, details=None, summarize=None, result_refs=None)`：

- 名称白名单及replay_class：respond→external_unknown；submit_draft→artifact_write；run/run_study→scientific_execution；search_sources/inspect_source/recall_memory/load_skill→read_only。未知名称拒绝且不执行callback。类别仅供审计，不自动重试。
- respond预留一个LLM调用；run/run_study共用至多一次科学执行；其他操作不新增这两种预算。预算在callback前检查并先持久化，错误和中断不退回预算。
- 保存kind=`agent_invocation`的reserved记录，然后才更新本地计数并执行callback。payload固定字段：version=1、execution_ref、operation_id（本次操作一次生成的唯一id）、name、phase、call_count、run_count、replay_class、details（提供的JSON数据或空dict）。
- callback正常返回后，保存returned记录，operation_id与reserved相同、计数相同，details为summarize(result)或result（为None时用空dict）。result_refs(result)明确提供真实产物refs，结果引用只来自可信工具返回，不从模型字符串里猜refs。returned技术返回不代表科学成功。
- callback抛Exception：保存error；抛SystemExit/KeyboardInterrupt等其他BaseException：保存interrupted；details只含error_type，不保存原始异常文本或provider对象，然后重新抛原异常。错误不允许自动重试或再次调用callback。
- callback返回后的摘要/结果记录写入失败必须向上传播，不能伪造returned、不能重新执行；该写入不放在callback的异常捕获范围内。reserved写入失败时callback绝不能执行。
- 存储引用保持有界深度：每条reserved只refs执行记录；每条terminal只refs执行记录、本次reserved和明确result_refs。不要链接前一操作，不做无限链。storage引用深度上限64，最多50次模型调用可能产生约200条记录，必须用平坦refs。
- 不保存key/header/transport。details来自调用方明确数据；不检查provider任意属性。JSON不合法时正常拒绝，不能把存储失败当作成功。

## 2. agent.py 的有限集成

在_execute_agent恢复本地calls/run_count后创建Journal，继承它们且传入task_ref/parent_run_ref；不要更改LLM消息、events内容/顺序或原预算、科学门槛、重试策略。

在调用点加invoke（保持原参数验证位置，不让被拒绝的请求执行）：

1. provider.respond：放在原try/except ProviderError内部；调用前details=`{'request_messages':request_trace,'context':context_audit}`，返回summary=`{'response_json':_json(response),'usage':仅非bool整数usage字段}`。必须在_assistant/动作处理之前保存returned。保留现有一次调用一次计数与ProviderError重试预算，不添加新的重试。
2. agent_tools.submit_draft：details含args；摘要只取state/pro problems/checks及draft_ref/model_ref/hypothesis_ref/source_review_ref等JSON字段，不保存artifact对象；result_refs只取这些实际生成的非空引用。
3. application.run_reproduction：details含model_ref/draft_ref；summary=`{'result_ref':ref}`，result_refs=[ref]，必须在后续store.get/_run_facts之前保存returned。原run_count更新保持与journal一致。
4. studies.execute_study：同样summary与refs，details含plan_ref。原run_count和现有checkpoint可保留，但journal的科学预算reserved必须在真正进入execute_study之前。发生异常仍沿用原内部诊断/incomplete行为。
5. search_sources、recall_memory、skills.load_skill：包装实际函数调用；details含args，结果本身为JSON可作summary；不扩大来源/权限。
6. _source_text：仅在inspect_source实际读页面位置包装，summary只取`{'page_found':text is not None}`，不要重复存整页。检查参数越界行为不变。
7. finish/ask_researcher是流程控制，仍由现有events/checkpoint记录，不作为新的科学工具执行。

checkpoint和最终agent_run新增execution_ref、invocation_refs；refs同时明确包含执行记录和所有当前调用记录，以便正常export保留闭包。_final新增可选kwargs（旧调用兼容）。resume的run/checkpoint一致性预检增加这两个可选字段（旧记录两边都缺失仍接受）；每次恢复新建execution，只继承已有计数与parent_run，不伪造旧历史，不在当前execution里重新登记旧调用。

新字段不进入模型上下文，不改变原始事件索引/历史；日志自身不是一项模型动作、不消耗额外模型调用。现有任意崩溃恢复入口、锁、全局跨分支费用限制均不在本轮。

## 3. 验收与范围

故障注入检查：provider入口、返回后动作处理前、真实模拟结果落盘后反馈前；预留写失败不调用服务；结果记录写失败不重复计算；工具部分产物后中断不谎称returned；重试每次计数、暂停/恢复及替代分支计数、平坦引用、导出闭包、异常文本不泄露凭据。研究执行也验证调用前预留，真实拟合仍由原测试覆盖。

只改invocations.py与agent.py。已有测试、storage、CLI、科学工具实现保持不变；不安装，不执行shell/测试/网络/子Agent，不读独立评测/文献材料。新文件已经由Codex建立为空文件，可以读取后写入。优先完成模块，再小步改调用点；不重写整个Agent。
