"use strict";
// Served only by the synthetic fixture, never by the production workbench.
const browserFixture=__FIXTURE__;
window.addEventListener("DOMContentLoaded",async()=>{
 const result=document.createElement("pre");result.id="browser-smoke-result";document.body.append(result);
 const checks=[];const check=(condition,name)=>{if(!condition)throw Error(name);checks.push(name);};
 const until=async test=>{for(let i=0;i<150;i++){if(test())return;await new Promise(r=>setTimeout(r,100));if(typeof refresh==="function")await refresh();}throw Error("Browser wait expired");};
 try{
  if(location.pathname==="/projects"){
   await until(()=>document.getElementById("project-scenario").textContent==="Browser saved decision fixture.");
   check(!document.getElementById("advance-project").disabled,"saved response button enabled at exhausted planner quota");
   document.getElementById("advance-project").click();
   await until(()=>document.getElementById("project-state").textContent==="规划额度已用完");
   check(document.getElementById("advance-project").disabled,"saved action consumed once and quota then disables planning");
   const data=await api("/api/project/"+browserFixture.paid);
   check(data.budget.planner_calls===1&&data.source_refs.length===1,"one saved planner response, one actual source action");
   const card=[...document.querySelectorAll(".task-card")].find(x=>x.textContent.includes("Browser chart fixture."));
   check(Boolean(card),"actual project sidebar card present");card.click();
   await until(()=>document.querySelector("svg.scientific-chart"));
   const chart=document.querySelector("svg.scientific-chart");
   check(chart.getAttribute("aria-label").includes("sweep")&&chart.getAttribute("aria-label").includes("Boolean"),"native chart has explicit units");
   const legend=document.querySelector("button.scientific-series");legend.click();
   check(legend.getAttribute("aria-pressed")==="false","chart legend toggles actual SVG visibility");
   document.getElementById("stop-project").click();
   await until(()=>document.getElementById("project-editor").open);
   const inputs=document.querySelectorAll("#project-editor-body input");inputs[0].value="browser-regression";inputs[1].value="Synthetic stop control.";
   document.getElementById("project-form").requestSubmit();
   await until(()=>document.getElementById("project-state").textContent==="研究已停止");
   check(document.getElementById("advance-project").disabled,"actual stop form blocks further planning");
  }else if(browserFixture.region){
   await showRegion(browserFixture.region);
   await until(()=>document.querySelector("img.material-region")?.naturalWidth>0);
   check(Boolean(document.querySelector("img.material-region").naturalWidth),"authenticated region blob renders under production CSP");
   const edit=[...document.querySelectorAll("#reader button")].find(x=>x.textContent==="审阅并保存新转录来源");
   check(Boolean(edit),"region review action present");edit.click();
   await until(()=>document.getElementById("editor").open);
   const fields=document.querySelectorAll("#editor-body input");fields[0].value="browser-regression";fields[1].value="Read the original region.";
   document.querySelector("#editor textarea").value="Initial x = 1.";
   document.getElementById("editor-form").requestSubmit();
   await until(()=>[...document.querySelectorAll("#jobs .job-row")].some(row=>row.textContent.includes("保存转录修订")&&row.textContent.includes("操作已完成")));
   const overview=await api("/api/overview");
   check(overview.sources.some(x=>x.authority==="attributed_human_transcription_not_independent_review"),"review creates an attributable new source");
  }else{checks.push("PDF region flow unavailable: optional PDF engine not installed");}
  result.textContent=JSON.stringify({status:"passed",checks,user_agent:navigator.userAgent,platform:navigator.platform});
 }catch(error){result.textContent=JSON.stringify({status:"failed",checks,error:error.message,user_agent:navigator.userAgent,platform:navigator.platform});}
});
