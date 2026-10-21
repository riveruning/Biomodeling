# 明确失败优先的运行裁决（T01）

2026-10-19，已验收。任务来源：[架构审计与优化队列](optimization-backlog.md)。

## 修复的问题

严格复现原先把“数值验证明确失败”和“来源尚未支持”统一归为 not_verified；Agent finish 只看求解 succeeded 且科学状态不为 failed，因此可能在非负性检查已经阻断时仍 completed。

真实反例为 x(0)=1、恒定消耗 k=.4，t=4 时 x=-.6000000000000008。修复前 Agent completed/scientific_status=not_verified；现在求解器状态仍 succeeded，数值失败被保留，scientific_status=failed，Agent incomplete，CLI 返回1。失败结果继续保存以供诊断，不改变轨迹来满足检查。

## 当前裁决

application 中 derive_scientific_status、initial_scientific_status 共用一个纯裁决内核；新运行、缓存、Agent 工具摘要与 finish、CLI 都使用它。blocking_verdict_basis 返回实际失败依据。

- 求解非 succeeded、静态/数值 blocking 检查 fail、参考比较 fail，或已有科学状态 failed，均为明确失败，优先于其他 unknown/not_run。
- 缺失或未知的验证保持 not_verified，不自动当作失败，也不升级为通过。
- passed_structured_reference 要求已知任务模式、实际参考比较通过、finite/convergence 全部出现且每条通过、其余阻断检查没有未知状态。严格复现还要求明确完整且支持的来源和明确没有未知单位。
- 有效但来源未核验的运行仍可技术 completed；它的科学状态保持 not_verified。普通数值参考通过仍不证明论文忠实性。
- 对旧记录按保存的实际事实重新裁决，不改写旧对象。旧记录模式缺失时保留未核验；旧标签不能遮盖已保存的失败。CLI 输出同时保留 stored_scientific_status 与有效裁决，严格未核验返回2。

未增加外部工具、依赖或新的科研范围。研究计划 completion_eligible 分支沿用原协议；证据绑定、崩溃恢复和输入契约继续由后续任务处理。

## 实现与独立验收

DSH 按任务卡实现3个生产文件及新增测试，Codex监督文件范围并独立审查。首次宿主派发因 storage.py 不在只读范围而暂停，未改源码；补全范围后，DSH交付文件但达到600秒时限。一次限定两文件的审查修订随后因60000ms流式空闲超时结束，部分修订已保存，没有将服务终态当作验收通过。

Codex最后修正已定位的边界：旧模式、缺失收敛检查、重复检查、额外阻断未知、缺失单位审核，以及CLI显示、一次错误导入和未实际触达run/finish的测试构造；移除未使用的无效SBML测试常量。DSH两批快照和Codex修正差异分别保留。未继续循环重派。

实际结果：

- 初始独立负控制：8项中7失败、1通过，生产代码哈希与审计基线一致。
- 第一批DSH交付：独立11项8通过、3失败；DSH新增15项12通过、3失败，明确未验收。
- DSH修订：先前11项通过，额外6个旧记录边界未通过；新增测试16项中还有1项导入错误，继续保留失败记录。
- 最终定向：45项通过，包括独立用例、DSH测试与研究流程回归。
- 最终完整Python回归：748项通过，122.14秒，无失败、错误或跳过。原有测试文件没有修改。
- 开发监督脚本离线测试：15项通过；运行时覆盖层测试：3项通过。

独立验收已保留为 tests/test_run_verdict_acceptance.py，后续完整回归会继续检查这些边界。

源码差异、源码快照、原始失败、实际反例产物与验收JSON位于 [audit-t01-verdict-20261019](../artifacts/audit-t01-verdict-20261019/acceptance.json)。实际负值模拟见 [verified-negative-result.json](../artifacts/audit-t01-verdict-20261019/verified-negative-result.json)。

这是确定性代码与科学计算链路修复，不是新的真实模型能力评测。DSH开发调用与产品模型评测分开；本轮没有发送c13/c18材料，没有重跑此前审批拒绝的在线回归。
