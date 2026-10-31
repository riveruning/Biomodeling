"use strict";
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="workbench-token"]').content;
const localKey="biomodel-projects:"+location.host;
let selected=/^[a-f0-9]{64}$/.test(location.hash.slice(1))?location.hash.slice(1):null;
let overview=null,current=null,refreshing=false,formSave=null,pending={};
try{pending=JSON.parse(localStorage.getItem(localKey)||"{}");if(!pending||typeof pending!=="object"||Array.isArray(pending))pending={};}catch(_){}
const names={ready:"可以推进",awaiting_alignment:"等待讨论与对齐",needs_recovery:"待核对已保存返回",completed_with_limits:"已形成有限结论",insufficient_evidence:"证据不足",unsupported:"当前工具不支持",stopped:"研究已停止",planner_budget_exhausted:"规划额度已用完",operation_limit_exhausted:"操作额度已用完",unavailable:"版本或记录不可用"};
function node(tag,attrs={},...children){const n=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k.startsWith("on"))n.addEventListener(k.slice(2),v);else if(k==="checked"||k==="disabled")n[k]=Boolean(v);else if(v!==undefined&&v!==null)n.setAttribute(k,v);}for(const x of children.flat())if(x!==null&&x!==undefined)n.append(x instanceof Node?x:document.createTextNode(String(x)));return n;}
function clear(id,...items){$(id).replaceChildren(...items);}
function note(message){$("notice").textContent=message;$("notice").hidden=!message;}
function safe(fn){return async e=>{try{await fn(e);}catch(err){note(err.message||"操作结果未确认，请核对原作业。");}};}
function button(label,fn,attrs={}){return node("button",{type:"button",...attrs,onclick:safe(fn)},label);}
function small(text){return node("p",{class:"muted"},text);}
function details(title,value){return node("details",{},node("summary",{},title),value instanceof Node?value:node("pre",{},typeof value==="string"?value:JSON.stringify(value,null,2)));}
async function api(path,body){const r=await fetch(path,{method:body===undefined?"GET":"POST",headers:{"X-Workbench-Token":token,...(body===undefined?{}:{"Content-Type":"application/json"})},...(body===undefined?{}:{body:JSON.stringify(body)})});const value=await r.json();if(!r.ok)throw Error(value.error||"请求未完成");return value;}
function persist(){try{localStorage.setItem(localKey,JSON.stringify(pending));}catch(_){}}
function choose(ref){selected=ref;location.hash=ref;return refresh();}
function show(title,...body){$("project-reader-title").textContent=title;clear("project-reader-body",...body);$("project-reader").showModal();}
function editor(title,body,save){$("project-editor-title").textContent=title;clear("project-editor-body",...body);formSave=save;$("project-editor").showModal();}
function field(label,input){return node("label",{},node("span",{},label),input);}
async function submit(operation,params,extra={}){
 const key=crypto.randomUUID().replaceAll("-","");pending[key]={operation,project_ref:params.project_ref||null,state:"submitting"};persist();
 let job;try{job=await api("/api/jobs",{key,operation,params,...extra});if(pending[key])pending[key].job_ref=job.job_ref;persist();}
 catch(e){if(pending[key])pending[key].state="unknown_effects";persist();note("提交结果未确认，已保存原提交标识；页面只查询，不自动重发。");throw e;}
 await follow(job);return job;
}
async function follow(job){
 const old=pending[job.key];if(!old)return;old.state=job.state;persist();
 if(job.state==="returned"){
  const result=job.result||{};delete pending[job.key];persist();
  if(result.project_ref){selected=result.project_ref;location.hash=selected;}
  if(old.operation==="snapshot_project"&&result.snapshot_ref)await submit("export_bundle",{ref:result.snapshot_ref});
  if(old.operation==="export_bundle"){
   const r=await fetch("/downloads/bundle/"+job.job_ref+".zip",{headers:{"X-Workbench-Token":token}});if(!r.ok)throw Error("结果包下载未完成");
   download(await r.blob(),"research-project.zip");
  }
 }else if(job.state==="failed"){delete pending[job.key];persist();note(job.error?.message||"操作未完成，请查看研究状态。");}
}
function download(blob,name){const url=URL.createObjectURL(blob);const a=node("a",{href:url,download:name});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function advance(){
 if(!selected)return;const params={project_ref:selected},preview=await api("/api/preview",{operation:"run_project",params});
 if(!preview.requires_provider){await submit("run_project",params);await refresh();return;}
 if(!preview.provider_enabled)throw Error("模型服务未启用。请在服务端配置服务后再推进研究。");
 const consent=node("input",{type:"checkbox"});const send=button("确认范围并推进研究",async()=>{
  if(!consent.checked){note("请确认本项目范围和预算。");return;}
  send.disabled=true;await submit("run_project",params,{provider_consent:true,scope_sha256:preview.scope_sha256});
  $("project-reader").close();await refresh();
 },{class:"primary"});
 show("Agent 研究 · 检查发送范围",small(preview.note),details("项目范围、服务与调用上限",preview.scope),
  field("允许 Agent 在这些上限内阅读资料、提出假设和迭代；需要对齐时暂停。",consent),send);
}
async function createEditor(){
 if(!overview)overview=await api("/api/overview");
 const scenario=node("textarea",{rows:5,required:true,placeholder:"描述应用场景、关注的问题以及你已经知道的限制。研究对象和粒度可以由 Agent 提出。"});
 const mode=node("select",{},node("option",{value:"application"},"应用场景建模"),node("option",{value:"reproduction"},"论文复现"));
 const calls=node("input",{type:"number",min:0,max:50,value:20,required:true});
 const literature=node("input",{type:"number",min:0,max:20,value:0,required:true});
 const choices=(overview.sources||[]).map(s=>({ref:s.source_ref,input:node("input",{type:"checkbox"}),name:s.filename}));
 const models=(overview.models||[]).map(m=>({ref:m.model_ref,input:node("input",{type:"checkbox"}),name:m.id+" · "+m.model_type}));
 editor("提出应用场景",[field("应用场景",scenario),field("任务类型",mode),small("Agent 负责提出研究目标、粒度、证据、假设和验证方案。创建项目不会发送模型请求。"),
  details("已有资料",choices.length?node("div",{},choices.map(x=>field(x.name,x.input))):"可先在原生任务页面导入资料。"),
  details("可选的已准备模型",models.length?node("div",{},models.map(x=>field(x.name,x.input))):"未提供模型时，Agent 可在支持范围内提出显式假设模型。"),
  details("调用额度",node("div",{},field("研究规划调用上限",calls),field("文献请求上限（0 表示关闭）",literature),small("最多 3 个模型/实验版本，每版原生调用最多 6 次，项目原生调用总分配上限 18。")))],async()=>{
   await submit("create_project",{config:{scenario:scenario.value,mode:mode.value,source_refs:choices.filter(x=>x.input.checked).map(x=>x.ref),
    model_refs:models.filter(x=>x.input.checked).map(x=>x.ref),policy:{planner_max_calls:Number(calls.value),max_literature_requests:Number(literature.value)}}});
   $("project-editor").close();await refresh();
 });
}
async function source(anchor){
 const q=new URLSearchParams({page:anchor.page,start:anchor.start,end:Math.min(anchor.end,anchor.start+4000)});
 const value=await api("/api/source/"+anchor.source_ref+"?"+q);
 show("证据原文",small("高亮切片来自原始来源；上下文与语义仍需核对。"),node("pre",{},value.before,node("mark",{},value.quote),value.after));
}
function render(data){
 current=data;$("project-welcome").hidden=true;$("project-view").hidden=false;
 $("project-scenario").textContent=data.scenario;$("project-state").textContent=names[data.state]||data.state;
 const b=data.budget;clear("project-metrics",...[["规划调用",b.planner_calls+" / "+b.planner_max_calls],["迭代版本",b.iterations+" / "+b.max_iterations],["原生额度分配",b.native_reserved+" / "+b.max_native_calls],["文献请求",b.literature_requests+" / "+b.max_literature_requests]].map(([n,v])=>node("div",{class:"metric"},small(n),node("strong",{},v))));
 const active=Object.values(pending).some(x=>x.project_ref===selected&&x.operation==="run_project");
 $("advance-project").disabled=active||data.state!=="ready"||b.planner_calls>=b.planner_max_calls;
 $("project-export").disabled=Object.values(pending).some(x=>(x.project_ref===selected||x.operation==="export_bundle")&&["submitting","queued","running"].includes(x.state));
 $("project-status-note").textContent=active?"Agent 正在推进研究。页面只读取进度。":data.state==="awaiting_alignment"?"回答下面的问题，再继续本项目。":data.state==="needs_recovery"?"操作结果尚未确认；请核对已保存返回，不要重新发起原请求。":b.planner_calls>=b.planner_max_calls?"研究规划额度已用完。已有证据和结果均保留。":"Agent 会在项目额度内选择下一步；新证据和新假设将产生新版本。";
 const frame=data.frame?.spec;
 clear("research-frame",...(frame?[node("h3",{},frame.objective),small("研究模式："+frame.mode),node("p",{},frame.granularity),node("p",{},frame.rationale),details("研究对象",frame.modeling_targets),details("证据需求与检索计划",frame.evidence_needs),details("本轮判据",frame.criteria),details("尚未解决",frame.unresolved)]:[small("尚未提出研究方案。你可以让 Agent 从应用场景开始推进。")]));
 clear("research-evidence",...(frame?[details("建模假设与采纳理由",frame.assumptions),...(frame.evidence||[]).map(a=>node("div",{},small(a.id+" · "+a.kind),node("p",{},a.quote||""),...(a.kind==="source"?[button("核对原文",()=>source(a),{class:"small"})]:[])))]:[small("来源与假设将在研究方案中记录。")]));
 clear("alignment",...(data.questions.length?data.questions.map(q=>node("div",{},node("h3",{},q.question),small("讨论类型："+q.category),details("影响范围",q.affected_fields),button("回答这个问题",()=>{
  const reply=node("textarea",{required:true,rows:4}),who=node("input",{required:true});
  editor("回答并记录对齐",[field("回答",reply),field("回答者",who)],async()=>{await submit("reply_project",{project_ref:selected,question_ref:q.question_ref,reply:reply.value,respondent:who.value});$("project-editor").close();await refresh();});
 }))):[small("当前没有等待研究者回答的问题。")]));
 clear("research-iterations",...Object.entries(data.iteration_facts||{}).map(([ref,f],i)=>node("div",{},node("h3",{},"实验版本 "+(i+1)),small(ref),details("实际执行与检查",f))));
 clear("research-assessments",...(data.assessments.length?data.assessments.map(a=>node("div",{},node("h3",{},a.interpretation),node("p",{},a.rationale),details("冻结判据与实际结果",{criteria:a.criteria,mandatory_checks:a.mandatory_checks,facts:a.facts}))):[small("运行后由 Agent 解释结果，检查状态由工具产生。")]));
 clear("research-usage",details("实际调用用量",data.usage),small("完整模型直接调用计算工具，不增加内层模型服务请求。缺失 usage 保持未知；有内层模型服务调用时，总 token 保持未知。"));
}
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
  overview=await api("/api/overview");$("provider-label").textContent=overview.provider.enabled?"模型服务已启用":"模型服务未启用";
  clear("project-list",...(overview.projects||[]).map(p=>button("",()=>choose(p.project_ref),{class:"task-card"+(p.project_ref===selected?" active":"")})));
  [...$("project-list").children].forEach((n,i)=>{const p=overview.projects[i];n.append(node("strong",{},p.scenario),small(names[p.state]||p.state));});
  for(const[key,item]of Object.entries({...pending})){
   try{await follow(await api(item.job_ref?"/api/job/"+item.job_ref:"/api/job-key/"+key));}catch(_){}
  }
  clear("project-jobs",...(overview.jobs||[]).filter(j=>j.operation.includes("project")||j.operation==="export_bundle").map(j=>node("div",{class:"job-row"},node("strong",{},j.operation),small(j.state),small(j.input_summary?.project_ref||""))));
  if(selected)render(await api("/api/project/"+selected));
 }finally{refreshing=false;}
}
$("new-project").onclick=$("start-project").onclick=safe(createEditor);
$("refresh-projects").onclick=safe(refresh);$("advance-project").onclick=safe(advance);
$("project-report").onclick=safe(async()=>show("研究报告",node("pre",{},(await api("/api/report/"+selected)).text)));
$("project-export").onclick=safe(async()=>{await submit("snapshot_project",{project_ref:selected});await refresh();});
$("close-project-reader").onclick=()=>$("project-reader").close();
$("cancel-project-editor").onclick=()=>$("project-editor").close();
$("project-form").onsubmit=safe(async e=>{e.preventDefault();if(formSave)await formSave();});
window.addEventListener("hashchange",safe(async()=>{const ref=location.hash.slice(1);if(/^[a-f0-9]{64}$/.test(ref)){selected=ref;await refresh();}}));
safe(refresh)();setInterval(()=>{if(Object.keys(pending).length)safe(refresh)();},2500);

