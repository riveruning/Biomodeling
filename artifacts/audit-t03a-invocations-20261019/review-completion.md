# T03a 有界修订：只补齐 agent.py

上一DSH连接异常terminated，未完成集成，已关闭。记录模块已交付，独立35项23通过12失败。Codex已修正模块的interrupted缺少error_type（2行），本次不得修改invocations.py，只读其invoke接口。不要重新设计Journal，不改测试。

agent.py当前的明确问题与修订步骤：

1. finish_session调用_final时遗漏execution_ref和invocation_refs，导致正常run与checkpoint历史不一致。增加`execution_ref=journal.execution_ref, invocation_refs=journal.refs`两个kwargs。
2. 当前provider在正常返回后才`calls=journal.calls`，抛ProviderError时calls没有同步；当前_study_once把run_count/calls赋给内部局部变量，同样没有同步外层。建议在journal初始化后添加一个小包装`invoke(name, callback, **kwargs)`，使用`nonlocal calls, run_count`，try返回journal.invoke，finally同步`calls, run_count = journal.calls, journal.runs`。所有实际调用点用此包装，消除异常分支遗漏；该finally不重试、不修改Journal。
3. provider调用改用invoke，删除重复/过晚的本地计数更新。保留原except ProviderError及events/重试逻辑。
4. run_study移除有作用域bug的_study_once；在原try里`run_ref = invoke('run_study', lambda:studies.execute_study(...), details={'plan_ref':...}, summarize=lambda r:{'result_ref':r}, result_refs=lambda r:[r])`，然后原result_summary。让Journal负责预留科学次数，不再自行run_count+=1。
5. 普通run目前完全没接：删除旧run_count+=1，保留draft_data/nonnegative读取，在调用application.run_reproduction的准确位置用invoke包装。details含model_ref/draft_ref，summary和refs与study相同。必须在后续store.get/_run_facts之前存returned。
6. submit_draft目前没接：原函数调用位置用invoke，details={'args':args}，摘要挑state/problems/checks及draft_ref/model_ref/hypothesis_ref/source_review_ref（不含artifact dataclass），result_refs只取四个可信非空ref。
7. search_sources、recall_memory、skills.load_skill当前没接：在各实际函数调用位置用invoke，对应名字，details={'args':args}，返回JSON直接用默认summary即可。保留所有参数/白名单/冻结版本检查。
8. inspect_source当前没接：在`text = _source_text(...)`处用invoke，名字inspect_source，details={'args':args}，summarize=lambda text:{'page_found':text is not None}，不要存整页。

保留所有旧events顺序、模型messages内容、调用/修订/重试上限与科学门槛。finish和ask是流程控制不登记工具执行。checkpoint字段/refs已经接好，不要撤销。旧run和checkpoint两边都缺execution_ref/invocation_refs的情况仍应恢复；新执行继承旧计数但不补造旧历史。

直接按上面8点编辑这一个文件，不重复探索模块或旧任务卡，不跑shell/测试/网络/子Agent。Codex会独立检查故障点、真实模拟、读工具、重试、恢复/分支、平坦导出和子进程直接退出。
