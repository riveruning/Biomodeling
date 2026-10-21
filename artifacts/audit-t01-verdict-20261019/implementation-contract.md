# T01：明确失败优先于未知状态

本任务只统一 reproduction/structured execution 的结果裁决；研究计划、证据语义、恢复机制和 RAG 不在本次修改范围。

已复现：声明非负的反应状态 x(0)=1，恒定消耗 k=.4，到 t=4 得 x=-.6。numeric_validation 正确给出 blocking/nonnegative/fail，但 strict=True 时 scientific_status 被归为 not_verified，Agent finish 返回 completed。现有 mock 测试直接设置 scientific_status=failed，漏掉聚合链路。

实现步骤与边界：

1. 在 application.py 建立小型共享纯裁决 helper，以保存的运行事实为输入；接口名称可自行选择并清楚说明。Agent finish 和 CLI _print_run_result 使用同一裁决，普通研究计划 completion_eligible 分支保持原行为。不引入框架或新依赖。
2. 模拟执行非 succeeded、静态或数值报告中 severity=blocking/status=fail、参考 comparison.status=fail、保存的 scientific_status=failed，均为明确失败。该失败优先于任何 unknown/not_run 或来源缺失；单个失败不能被其他通过检查覆盖。
3. 对没有明确失败但仍缺少来源支持、比较或有效验证的情况保持 not_verified，不能把 unknown/not_run 改为 pass，也不能自动当作 fail。只有冻结参考确实 pass、所需数值检查确实 pass（非空）且严格模式的来源条件满足时，才保留 passed_structured_reference。普通数值模式仍按原声明区分来源忠实性。
4. 新生成 run 的 scientific_status 使用该裁决。存储的 status 仍表示求解器执行状态，失败产物照常保存。CLI 失败返回1，严格且未核验返回2，合法普通未核验保留原退出行为；Agent 只有执行 succeeded 且无明确失败时可技术完成，来源未知依然不可宣称科学验证通过。
5. 对已保存旧记录也检查其实际 numeric/static/comparison 事实，不可仅信旧 scientific_status=not_verified。必要时正常使用 helper 给出保守结果，不重写旧不可变对象。缓存复用路径也不能重新产出把明确失败掩盖的状态。不要降低现有缓存数值预检。
6. 新增 tests/test_run_verdict.py，写有区分力的测试：真实严格模式非负性失败、真实严格参考不匹配、受控收敛失败、未知检查负控制、合法来源未知控制、旧报告状态与失败事实冲突、CLI 与 Agent 完成判断。实际求解路径使用项目已有 SBML/科学工具，脚本 Provider 不联网。Codex 独立执行测试，DSH 不运行 shell。

只改 task.write_files；不要重写已有测试、放宽科学门槛、读独立评测材料、运行网络模型或修改任务卡/监督程序。优先小步编辑保留其他行为。最后报告实际改动、helper 接口和未覆盖限制。
