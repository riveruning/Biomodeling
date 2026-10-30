"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="workbench-token"]').content;
const storageKey = "biomodel-workbench:" + location.host;
const state = {overview: null, current: /^[a-f0-9]{64}$/.test(location.hash.slice(1)) ? location.hash.slice(1) : null, task: null, refreshing: false, pending: {}, saving: false};
const names = {ode: "ODE · 常微分方程", fba: "FBA · 稳态通量", stochastic: "随机反应", pde: "PDE · 一维扩散衰减", boolean: "布尔网络", individual: "二维格点个体"};
const states = {awaiting_type: "待选择模型", awaiting_input: "待补充 / 待回答", ready_to_run: "可以执行", completed: "流程已完成", incomplete: "流程未完成", failed: "执行失败", needs_recovery: "待核对恢复", needs_proposal_recovery: "建议请求待核对", unknown_effects: "结果未确认", another_revision_dispatched: "其他修订已执行", unavailable: "版本或记录不可用", proposed: "已有推荐候选", awaiting_selection: "待选择候选", invalid: "建议无效", queued: "已排队", running: "执行中", returned: "操作已完成"};
const operationNames = {import_source: "导入资料", import_model: "导入原生模型", create_task: "保存任务", prepare_proposal: "导入建议", select_proposal: "采纳候选", recover_proposal: "认领建议返回", export_bundle: "导出结果包", propose_task: "请求模型建议", run_task: "执行任务", resume_task: "回答并继续", recover_task: "恢复原生执行"};
try {const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");if (saved && typeof saved === "object" && !Array.isArray(saved)) state.pending = saved;} catch (_) {}
function persist() {try {localStorage.setItem(storageKey, JSON.stringify(state.pending));} catch (_) {}}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "checked" || key === "disabled") node[key] = Boolean(value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function clear(id, ...children) {$(id).replaceChildren(...children);}
function notice(message) {
  $("notice").textContent = message;$("notice").hidden = !message;
  for (const dialog of document.querySelectorAll("dialog")) {
    let target = dialog.querySelector(".dialog-notice");
    if (!target && dialog.open) {target = el("div", {class: "dialog-notice", role: "alert"});dialog.querySelector(".dialog-heading").after(target);}
    if (target) {target.textContent = message;target.hidden = !message;}
  }
}
function guard(fn) {return async event => {try {await fn(event);} catch (error) {notice(error.message || "操作未确认，请查看已有记录。");}};}
function short(ref) {return ref ? ref.slice(0, 10) + "…" : "—";}
function badge(value) {return el("span", {class: "tag " + (value === "completed" || value === "returned" ? "good" : /fail|invalid|blocked|unknown|unavailable/.test(value) ? "blocked" : /awaiting|recovery|queued|running/.test(value) ? "waiting" : "")}, states[value] || value);}
function button(text, fn, attrs = {}) {return el("button", {...attrs, type: "button", onclick: guard(fn)}, text);}
function muted(text) {return el("p", {class: "muted"}, text);}
function detail(title, content) {return el("details", {}, el("summary", {}, title), typeof content === "string" ? el("pre", {}, content) : content);}
async function api(path, body) {
  let response;
  try {response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: {"X-Workbench-Token": token, ...(body === undefined ? {} : {"Content-Type": "application/json"})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});}
  catch (_) {throw new Error("连接中断，操作结果尚未确认。请刷新任务和操作记录，按原作业标识核对；不要重新提交。");}
  let data;
  try {data = await response.json();} catch (_) {throw new Error("响应未完整接收，请按原作业标识核对。");}
  if (!response.ok) throw new Error(data.error || "本地请求未完成。");
  return data;
}
async function parseJson(text) {return (await api("/api/parse-json", {text})).value;}
async function download(path, filename) {
  const response = await fetch(path, {headers: {"X-Workbench-Token": token}});
  if (!response.ok) throw new Error("下载尚不可用，请查看操作状态。");
  const objectUrl = URL.createObjectURL(await response.blob());
  const a = el("a", {href: objectUrl, download: filename});document.body.append(a);a.click();a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
}
function choose(ref) {state.current = ref; state.task = null;location.hash = ref;return refresh();}
function field(label, input) {return el("label", {class: "field"}, el("span", {}, label), input);}
function checkbox(label, checked = false) {const input = el("input", {type: "checkbox", checked});return {input, node: el("label", {class: "checkbox-label"}, input, el("span", {}, label))};}
function editor(title, content, submit, label = "保存") {
  $("editor-title").textContent = title;clear("editor-body", ...content);$("editor-submit").textContent = label;$("editor-submit").disabled = false;
  $("editor-form").onsubmit = guard(async event => {
    event.preventDefault();if (state.saving) return;state.saving = true;$("editor-submit").disabled = true;
    try {const close = await submit();if (close !== false) $("editor").close();} finally {state.saving = false;$("editor-submit").disabled = false;}
  });
  $("editor").showModal();
}
function reader(title, ...content) {$("reader-title").textContent = title;clear("reader-body", ...content);if (!$("reader").open) $("reader").showModal();}
async function submit(operation, params, extra = {}) {
  const key = crypto.randomUUID();state.pending[key] = {operation};persist();notice("");
  try {
    const job = await api("/api/jobs", {key, operation, params, ...extra});state.pending[key].job_ref = job.job_ref;persist();
    await refresh();return job;
  } catch (error) {notice(error.message + " 作业标识：" + key);throw error;}
}
async function service(operation, params) {
  const preview = await api("/api/preview", {operation, params});
  if (!preview.requires_provider) {await submit(operation, params);return true;}
  if (!preview.provider_enabled) throw new Error("模型服务未启用。可继续整理材料、导入已有建议和查看结果；需要服务时请在启动工作台时明确启用。");
  const consent = checkbox("我已检查发送范围，允许此次向上述服务发送本任务的材料和相关工具反馈。");
  const send = button("确认发送并开始", async () => {
    if (!consent.input.checked) throw new Error("请先检查并勾选此次发送范围。");
    send.disabled = true;
    try {await submit(operation, params, {provider_consent: true, scope_sha256: preview.scope_sha256});$("reader").close();if ($("editor").open) $("editor").close();}
    finally {send.disabled = false;}
  }, {class: "primary"});
  reader(operationNames[operation] + " · 检查发送范围", muted("服务：" + preview.scope.provider.base_url + " · " + preview.scope.provider.model),
    el("ul", {}, preview.scope.data_categories.map(text => el("li", {}, text))), muted(preview.note),
    detail("查看此次任务与发送范围", JSON.stringify(preview.scope, null, 2)), consent.node, el("div", {class: "dialog-actions"}, send));
  return false;
}
async function report(ref) {
  const data = await api("/api/report/" + ref);
  reader("研究报告", button("下载 Markdown 报告", () => download("/downloads/report/" + ref + ".md", "biomodel-report.md")), el("pre", {}, data.text));
}
async function source(anchor) {
  const query = new URLSearchParams({page: anchor.page || 1, start: anchor.start || 0});
  if (anchor.end !== undefined) query.set("end", anchor.end);
  const data = await api("/api/source/" + anchor.source_ref + "?" + query);
  const pages = el("select", {"aria-label": "来源页"}, data.metadata.pages.map(p => el("option", {value: p.page}, "第 " + p.page + " 页")));pages.value = String(data.page);
  pages.addEventListener("change", guard(() => source({source_ref: data.source_ref, page: Number(pages.value)})));
  const navigation = el("div", {class: "inline-actions"},
    button("前一段", () => source({source_ref: data.source_ref, page: data.page, start: Math.max(0, data.start - 1600)}), {class: "small", disabled: data.start === 0}),
    button("后一段", () => source({source_ref: data.source_ref, page: data.page, start: data.end}), {class: "small", disabled: data.end >= data.page_characters}));
  reader("核对来源原文", muted("来源 " + short(data.source_ref) + " · 第 " + data.page + " 页 · 字符 " + data.start + "–" + data.end), pages,
    el("div", {class: "quote-context"}, data.before, el("mark", {}, data.quote), data.after),
    navigation, muted("高亮来自服务端核对的原文切片；未显示部分不代表不存在其他内容。"), detail("来源信息", JSON.stringify(data.metadata, null, 2)));
}
async function taskEditor(revision = false) {
  const original = revision && state.task ? structuredClone(state.task.config) : {schema_version: 1, goal: "", model_type: null, source_refs: [], model_ref: null, execution: {}, max_calls: 6, proposal_max_calls: 0};
  const goal = el("textarea", {required: true, maxlength: 4000, rows: 3});goal.value = original.goal;
  const type = el("select", {}, el("option", {value: ""}, "暂不选择，先整理问题与材料"), Object.entries(names).map(([value, title]) => el("option", {value}, title)));type.value = original.model_type || "";
  const items = [...state.overview.sources];
  for (const r of original.source_refs || []) if (!items.some(s => s.source_ref === r)) items.push({source_ref: r, filename: "已绑定来源 " + short(r)});
  const checks = items.map(s => {const check = checkbox(s.filename + " · " + short(s.source_ref), (original.source_refs || []).includes(s.source_ref));return {...check, ref: s.source_ref};});
  const models = [...state.overview.models];
  if (original.model_ref && !models.some(m => m.model_ref === original.model_ref)) models.push({model_ref: original.model_ref, id: "当前原生模型", model_type: original.model_type});
  const model = el("select", {}, el("option", {value: ""}, "未提供原生模型"), models.map(m => el("option", {value: m.model_ref}, (names[m.model_type] || m.model_type) + " · " + m.id)));model.value = original.model_ref || "";
  const execution = el("textarea", {class: "code", rows: 7});execution.value = JSON.stringify(original.execution || {}, null, 2);
  const nativeCalls = el("input", {type: "number", min: 1, max: 50, required: true, value: original.max_calls});
  const proposalCalls = el("input", {type: "number", min: 0, max: 3, required: true, value: original.proposal_max_calls || 0});
  const frozen = revision && state.task.status.proposal_call_count > 0;nativeCalls.disabled = proposalCalls.disabled = frozen;
  const reason = el("input", {type: "text", maxlength: 2000, required: revision, placeholder: "说明本次补充或修订的依据"});
  const body = [field("研究目标", goal), field("模型类型", type), field("已导入的原生模型（可选）", model),
    el("div", {class: "field"}, el("span", {}, "授权来源"), el("div", {class: "checklist"}, ...(checks.length ? checks.map(c => c.node) : [muted("尚无来源，请先导入资料。")]))),
    el("div", {class: "form-row"}, field("原生执行调用上限", nativeCalls), field("建议调用上限（0 表示不调用）", proposalCalls)),
    muted(frozen ? "已有建议预留，两类调用上限已冻结。" : "建议额度与执行额度分别计算。没有服务请求时也可导入已有建议。"),
    detail("执行配置（已有类型的 JSON 契约）", field("execution", execution))];
  if (revision) body.push(field("修订说明", reason));
  editor(revision ? "补充输入 / 创建任务修订" : "新建研究任务", body, async () => {
    const config = {...original, goal: goal.value, model_type: type.value || null, source_refs: checks.filter(c => c.input.checked).map(c => c.ref), model_ref: model.value || null,
      max_calls: Number(nativeCalls.value), proposal_max_calls: Number(proposalCalls.value), execution: await parseJson(execution.value)};
    await submit("create_task", {config, ...(revision ? {parent_ref: state.current, reason: reason.value} : {})});
  });
}
function importEditor() {
  const type = el("select", {}, el("option", {value: "source"}, "资料 · UTF-8 TXT / Markdown"), Object.entries(names).filter(([kind]) => kind !== "ode").map(([value, title]) => el("option", {value}, "原生模型 · " + title)));
  const file = el("input", {type: "file", accept: ".txt,.md,.json", required: true, class: "file-input"});
  editor("导入资料或原生模型", [muted("选择本地文件，最多 1 MB。导入后在任务配置中明确选择，不自动扩大已有任务范围。"), field("内容类型", type), field("文件", file)], async () => {
    const selected = file.files[0];if (!selected || selected.size > 1000000) throw new Error("请选择最多 1 MB 的文件。");
    const content = new TextDecoder("utf-8", {fatal: true}).decode(await selected.arrayBuffer());
    await submit(type.value === "source" ? "import_source" : "import_model", {filename: selected.name, content, ...(type.value === "source" ? {} : {model_type: type.value})});
  }, "导入到工作区");
}
function proposalEditor() {
  const input = el("textarea", {class: "code", rows: 16, required: true, placeholder: "粘贴 proposal JSON。它只生成建议，不启动科学计算。"});
  const file = el("input", {type: "file", accept: ".json"});file.addEventListener("change", guard(async () => {
    const f = file.files[0];if (f.size > 1000000) throw new Error("建议文件超过 1 MB。");input.value = new TextDecoder("utf-8", {fatal: true}).decode(await f.arrayBuffer());
  }));
  editor("导入已有模型建议", [muted("保留原文证据、理由、未知项与备选。产品内不发起模型请求，外部准备的用量未知。"), file, field("建议 JSON", input)], async () => {
    await submit("prepare_proposal", {request_ref: state.current, proposal: await parseJson(input.value)});
  });
}
function selectEditor(proposal, candidate) {
  const reviewer = el("input", {required: true, maxlength: 200, placeholder: "研究者姓名或标识"});
  const reason = el("textarea", {required: true, maxlength: 2000, placeholder: "说明为何选择该候选"});
  const unknowns = candidate.unknowns.map(text => checkbox(text));
  editor("选择候选 · " + candidate.id, [muted("此操作保存选择和输入，不运行模型。确认未知项表示已知悉，不表示已经解决或通过领域审核。"),
    field("研究者", reviewer), field("选择依据", reason), ...unknowns.map(c => c.node)], async () => {
    if (unknowns.some(c => !c.input.checked)) throw new Error("请逐项确认已知悉候选中的未知项。");
    await submit("select_proposal", {proposal_ref: proposal.proposal_ref, candidate_id: candidate.id, reviewer: reviewer.value, reason: reason.value, acknowledged_unknowns: candidate.unknowns});
  }, "保存选择，准备任务输入");
}
function renderProposals(view) {
  clear("proposals");
  if (!view.proposals.length) {$("proposals").append(el("p", {class: "empty"}, "还没有建议。可导入已有建议，或在启用模型服务和建议额度后请求建议。"));return;}
  for (const proposal of view.proposals) {
    const wrapper = el("article", {class: "proposal"}, el("div", {class: "candidate-header"}, el("h3", {}, proposal.summary || "建议记录"), badge(proposal.state)));
    if (!proposal.candidates) {wrapper.append(...(proposal.issues || []).map(muted));$("proposals").append(wrapper);continue;}
    wrapper.append(muted("来源：" + (proposal.origin === "provider" ? "模型服务建议" : "外部 / 本地已准备建议") + (view.selection?.proposal_ref === proposal.proposal_ref ? " · 已由研究者选择" : " · 需显式选择")));
    if (view.selection?.proposal_ref === proposal.proposal_ref) wrapper.append(muted("当前修订已采纳候选 " + view.selection.candidate_id + " · 研究者：" + view.selection.reviewer + " · " + view.selection.reason));
    for (const question of proposal.questions) wrapper.append(el("p", {class: "question"}, question));
    for (const candidate of proposal.candidates) {
      const node = el("section", {class: "proposal"}, el("div", {class: "candidate-header"}, el("h3", {}, names[candidate.model_type] + " · " + candidate.id), ...(candidate.id === proposal.recommendation ? [el("span", {class: "tag"}, "推荐候选")] : [])),
        el("p", {}, candidate.rationale));
      if (candidate.unknowns.length) node.append(el("strong", {class: "muted"}, "尚未解决"), el("ul", {}, candidate.unknowns.map(text => el("li", {}, text))));
      if (candidate.missing_inputs.length) node.append(muted("待补输入：" + candidate.missing_inputs.join("、")));
      for (const note of candidate.input_notes) node.append(muted(note));
      if (candidate.draft_check) node.append(candidate.draft_adoptable ? el("span", {class: "tag good"}, "草稿来源检查通过") : badge("blocked"),
        muted(candidate.draft_adoptable ? view.selection?.proposal_ref === proposal.proposal_ref ? "当前模型已按采纳的草稿生成；来源检查不表示生物学有效。" : "模型草稿已匹配有限文本声明，仍需研究者选择。" : "草稿存在阻断项，不能采纳。"), ...candidate.draft_check.issues.map(i => muted(i.target + "：" + i.message)));
      if (candidate.model_draft) node.append(detail("原生模型数学草稿", el("pre", {}, JSON.stringify(candidate.model_draft, null, 2))));
      if (candidate.stochastic_rate_units) node.append(muted("SSA 倾向函数：aᵣ(n) = cᵣ ∏ comb(nᵢ, νᵣᵢ)。不把浓度速率直接转成随机常数。"),
        detail("随机常数单位与来源假设", el("pre", {}, JSON.stringify({rate_units: candidate.stochastic_rate_units, source_binding: candidate.draft_check?.binding}, null, 2))));
      const anchors = new Set([...candidate.evidence_ids, ...Object.values(candidate.evidence_map || {}).flat()]);
      const evidenceList = el("div", {});
      for (const anchor of proposal.evidence.filter(e => anchors.has(e.id))) {
        evidenceList.append(el("p", {}, anchor.id + " · " + (anchor.kind === "goal" ? "研究目标（仅说明意图）" : anchor.kind === "model" ? "已提供模型的结构引用" : "来源第 " + anchor.page + " 页"),
          ...(anchor.quote ? [el("p", {class: "muted"}, anchor.quote)] : []),
          ...(anchor.kind === "source" ? [button("核对原文", () => source(anchor), {class: "small"})] : [])));
      }
      node.append(detail("证据与原文", evidenceList), detail("输入草稿与所需材料", el("div", {}, el("ul", {}, candidate.required_inputs.map(text => el("li", {}, text))), el("pre", {}, JSON.stringify(candidate.input_draft, null, 2)), muted(candidate.execution_defaults))),
        el("div", {class: "inline-actions"}, button(view.selection?.proposal_ref === proposal.proposal_ref && view.selection.candidate_id === candidate.id ? "当前已采纳" : "选择此候选", () => selectEditor(proposal, candidate),
          {class: "primary small", disabled: Boolean(proposal.request_ref !== state.current || view.status.dispatch_ref || view.status.state === "another_revision_dispatched" || candidate.draft_check && !candidate.draft_adoptable)})));
      wrapper.append(node);
    }
    wrapper.append(button("查看建议报告", () => report(proposal.proposal_ref), {class: "small"}));$("proposals").append(wrapper);
  }
}
function renderTask(view) {
  state.task = view;const s = view.status;$("welcome").hidden = true;$("task-view").hidden = false;
  $("breadcrumb").textContent = "研究任务";$("task-goal").textContent = s.goal;$("task-meta").textContent = "修订 " + s.revision + " · " + (names[s.model_type] || "模型类型待选择");
  $("task-status").replaceWith(Object.assign(badge(s.state), {id: "task-status"}));
  const stageNames = {intake: "问题与材料", type_selection: "模型选择", input_preparation: "输入检查", execution: "原生执行", report: "结果与报告"};
  clear("stages", ...s.stages.map((item, i) => el("div", {class: "stage " + item.status}, "0" + (i + 1) + "  " + stageNames[item.id])));
  clear("metrics", ...[["建议调用", s.proposal_call_count + " / " + s.proposal_max_calls], ["原生调用", s.native_call_count ?? "未知"], ["累计调用", (s.total_call_count ?? "未知") + " / " + s.max_total_calls], ["科学运行", s.run_count ?? (s.dispatch_ref ? "未知" : 0)]].map(([name, value]) => el("div", {class: "metric"}, el("small", {}, name), el("strong", {}, value))));
  const next = {awaiting_type: "先整理材料，再检查候选并显式选择模型类型。", awaiting_input: s.pending_question ? "请回答下方的研究者追问，沿用原任务继续。" : "请补齐所选工作流的输入；可通过修订保存。", ready_to_run: "输入已通过前置检查，核对发送范围后可以执行。", completed: "检查过程、数值结果和完整报告。", needs_recovery: "核对原生执行账本，只恢复已确认的返回。", needs_proposal_recovery: "建议返回尚未确认，请核对请求记录，不重新请求。", another_revision_dispatched: "同一任务的其他修订已执行，请切换到对应修订。", unknown_effects: "操作效果未确认，先核对已有记录，不重新执行。"};
  const gaps = {type_required: "需要显式选择模型类型。", sources_required: "需要含方程、初值和参数的授权来源。", experiment_required: "需要明确的采样网格与时间单位。", model_required: "需要准备所选类型的原生模型。", wrong_workflow: "ODE 来源构建与已提供 SBML 的直接运行使用不同入口。", unsupported_source_construction: "此原生路由需要已准备模型；来源布尔或随机反应草稿请先检查建议再采纳。", unsupported_conditions: "当前原生路由不解释 ODE 条件标签。", implementation_changed: "产品版本已变化，需要明确创建修订再继续。", invalid_execution: "执行配置未通过检查。", invalid_native_input: "原生模型或设置未通过检查。"};
  clear("next-action", el("strong", {}, "下一步："), next[s.state] || s.next_action, ...(s.gaps.length ? [el("ul", {class: "gap-list"}, s.gaps.map(g => el("li", {}, gaps[g.code] || g.question)))] : []), detail("检查细节", JSON.stringify({next_action: s.next_action, gaps: s.gaps}, null, 2)));
  $("revise-task").disabled = Boolean(s.dispatch_ref || s.state === "another_revision_dispatched");
  $("upload-proposal").disabled = Boolean(s.dispatch_ref || s.state === "another_revision_dispatched");
  $("make-proposal").disabled = !state.overview.provider.enabled || s.proposal_call_count >= s.proposal_max_calls || Boolean(s.dispatch_ref) || s.state === "needs_proposal_recovery" || s.state === "another_revision_dispatched";
  renderProposals(view);clear("execution");$("execution-status").textContent = states[s.state] || s.state;
  if (s.pending_question) {
    $("execution").append(el("div", {class: "question"}, s.pending_question), button("回答并继续原任务", () => {
      const reply = el("textarea", {required: true, maxlength: 12000, rows: 5});const respondent = el("input", {required: true, maxlength: 200});
      editor("回答研究者追问", [field("当前问题", el("p", {class: "question"}, s.pending_question)), field("回答", reply), field("回答者", respondent)], () => service("resume_task", {request_ref: state.current, reply: reply.value, respondent: respondent.value}), "检查并发送回答");
    }, {class: "primary", disabled: !state.overview.provider.enabled}));
  } else if (s.state === "ready_to_run") $("execution").append(muted("输入符合所选工作流。执行会使用冻结输入与既有预算。"), button("检查发送范围并执行", () => service("run_task", {request_ref: state.current}), {class: "primary", disabled: !state.overview.provider.enabled}));
  else if (s.state === "needs_recovery" || s.native_run_ref && !s.result_ref) $("execution").append(muted("先核对已保存的返回。未知效果不能通过重新运行来恢复。"), button("检查并恢复原生执行", () => service("recover_task", {request_ref: state.current})));
  else $("execution").append(muted(s.state === "completed" ? "流程已结束。请继续检查数值结果、来源和模型适用性。" : "补齐输入、选择候选或检查现有操作记录后继续。"));
  if (!state.overview.provider.enabled) $("execution").append(muted("模型服务未启用，页面加载和刷新不会调用服务。"));
  clear("result", ...(s.result_ref ? [button("查看完整结果报告", () => report(s.result_ref), {class: "primary"}), muted("完成状态来自原生流程，不代表生物学正确性已验证。")] : [muted("尚无已确认的统一结果报告。已有过程保留在下方。")])) ;
  clear("timeline");
  for (const row of view.timeline?.rows || []) $("timeline").append(el("div", {class: "timeline-row"}, row.index, badge(row.status), el("span", {}, row.tool || row.event_type, " · ", row.summary || "")));
  clear("sources");
  const sourceRefs = new Set([...view.config.source_refs, ...view.proposals.flatMap(p => (p.evidence || []).filter(e => e.kind === "source").map(e => e.source_ref))]);
  for (const r of sourceRefs) {
    const info = state.overview.sources.find(item => item.source_ref === r);
    $("sources").append(el("div", {class: "source-row"}, el("strong", {}, info?.filename || short(r)), muted((view.config.source_refs.includes(r) ? "任务输入 · " : "建议 / 采纳来源 · ") + short(r)), button("查看原文", () => source({source_ref: r}), {class: "small"})));
  }
  if (view.config.model_ref) $("sources").append(el("div", {class: "source-row"}, "已绑定原生模型", muted(short(view.config.model_ref))));
  if (!sourceRefs.size && !view.config.model_ref) $("sources").append(muted("尚未绑定资料或模型。先导入，再通过修订选择。"));
  clear("revisions", ...view.revisions.map(r => el("div", {class: "revision-row"}, "修订 " + r.revision + " · " + short(r.request_ref), button(r.request_ref === state.current ? "当前修订" : "查看此修订", () => choose(r.request_ref), {class: "small", disabled: r.request_ref === state.current}))));
  clear("attempts", muted("已报告 token：" + view.proposal_usage.known_total_tokens + "；缺少完整用量的调用：" + view.proposal_usage.missing_total_usage_calls + "。"));
  for (const attempt of view.proposal_attempts) $("attempts").append(el("div", {class: "source-row"}, muted(short(attempt.attempt_ref)),
    muted(attempt.confirmed_return ? "已保存返回" : "返回未确认，不重新请求"),
    ...(attempt.confirmed_return && !attempt.proposal_refs.length ? [button("认领已保存的建议返回", () => submit("recover_proposal", {attempt_ref: attempt.attempt_ref}), {class: "small"})] : [])));
}
async function handleJob(job) {
  if (!state.pending[job.key]) return;
  if (job.state === "returned" || job.state === "failed" || job.state === "unknown_effects") {
    delete state.pending[job.key];persist();
    if (job.state === "returned" && job.result?.request_ref) {state.current = job.result.request_ref;location.hash = state.current;}
    if (job.state === "failed") notice(job.error?.message || "操作失败，请查看已保存状态。");
    if (job.state === "unknown_effects") notice("操作结果未确认，不会自动重新提交。请查看任务和账本。");
  }
}
function renderJobs(jobs) {
  clear("jobs");if (!jobs.length) $("jobs").append(el("p", {class: "empty"}, "这里会记录材料导入、任务修订、建议、执行和导出的状态。"));
  for (const job of jobs) {
    const actions = el("div", {class: "job-actions"});
    if (job.result?.request_ref || job.request_ref) actions.append(button("查看任务", () => choose(job.result?.request_ref || job.request_ref), {class: "small"}));
    if (job.state === "returned" && job.operation === "export_bundle") actions.append(button("下载结果包", () => download("/downloads/bundle/" + job.job_ref + ".zip", "biomodel-bundle.zip"), {class: "small"}));
    actions.append(button("记录详情", () => reader("操作记录", el("pre", {}, JSON.stringify(job, null, 2))), {class: "small"}));
    $("jobs").append(el("div", {class: "job-row"}, el("div", {class: "job-label"}, el("strong", {}, operationNames[job.operation] || job.operation), muted(short(job.job_ref)), ...(job.error ? [muted(job.error.message)] : [])), badge(job.state), actions));
  }
  for (const [key, pending] of Object.entries(state.pending)) if (!jobs.some(j => j.key === key)) {
    $("jobs").append(el("div", {class: "job-row"}, el("div", {class: "job-label"}, el("strong", {}, operationNames[pending.operation] || "提交记录"), muted("提交标识：" + key), muted("尚未确认对应作业。先核对任务；页面不会重新提交。")),
      badge("unknown_effects"), button("停止本页查询", () => {delete state.pending[key];persist();return refresh();}, {class: "small"})));
  }
}
async function refresh() {
  if (state.refreshing) return;state.refreshing = true;
  try {
    const overview = await api("/api/overview");state.overview = overview;
    for (const key of Object.keys(state.pending)) {
      const known = overview.jobs.find(j => j.key === key);
      if (known) await handleJob(known);
      else {try {await handleJob(await api("/api/job-key/" + encodeURIComponent(key)));} catch (_) {}}
    }
    $("provider-state").textContent = overview.provider.enabled ? "模型服务已启用 · 每次发送需确认" : "模型服务未启用";
    clear("task-list", ...overview.requests.map(t => button("", () => choose(t.request_ref), {class: "task-card" + (t.request_ref === state.current ? " active" : "")})));
    Array.from($("task-list").children).forEach((node, i) => {const t = overview.requests[i];node.append(el("strong", {}, t.goal), el("small", {}, (names[t.model_type] || "类型待选择") + " · " + (states[t.state] || t.state) + " · v" + t.revision));});
    if (!overview.requests.length) $("task-list").append(el("p", {class: "muted"}, "还没有任务。"));
    renderJobs(overview.jobs);
    if (state.current) {
      try {renderTask(await api("/api/task/" + state.current));}
      catch (error) {state.task = null;$("task-view").hidden = true;$("welcome").hidden = false;notice(error.message);}
    }
  } finally {state.refreshing = false;}
}
$("new-task").onclick = $("start-task").onclick = guard(() => taskEditor());
$("import-material").onclick = $("add-material").onclick = guard(importEditor);
$("refresh").onclick = guard(refresh);$("revise-task").onclick = guard(() => taskEditor(true));
$("upload-proposal").onclick = guard(proposalEditor);$("make-proposal").onclick = guard(() => service("propose_task", {request_ref: state.current}));
$("view-task-report").onclick = guard(() => report(state.current));
$("export-task").onclick = guard(() => submit("export_bundle", {ref: state.task.status.result_ref || state.current}));
document.querySelectorAll("[data-close]").forEach(node => node.addEventListener("click", () => $(node.dataset.close).close()));
window.addEventListener("hashchange", guard(async () => {const r = location.hash.slice(1);if (/^[a-f0-9]{64}$/.test(r)) {state.current = r;await refresh();}}));
guard(refresh)();
setInterval(() => {if (Object.keys(state.pending).length || state.overview?.jobs.some(j => j.state === "queued" || j.state === "running")) guard(refresh)();}, 2500);
