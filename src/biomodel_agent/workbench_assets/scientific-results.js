"use strict";
// Draw only saved numeric values, with explicit units and discrete step paths.
function scientificPlot(plot){
 const ns="http://www.w3.org/2000/svg",wrap=document.createElement("section"),legend=document.createElement("div");
 const make=(tag,attrs={},text)=>{const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;};
 const title=document.createElement("h4");title.textContent=plot.title;wrap.append(title);
 const svg=make("svg",{viewBox:"0 0 720 330",role:"img",class:"scientific-chart","aria-label":plot.title+"; "+plot.x_unit+" / "+plot.y_unit});
 svg.append(make("title",{},plot.title+"; saved numerical output; scientific support unknown"));
 const flat=plot.series.flatMap(s=>s.values),x=plot.x;
 if(!x.length||!flat.length||!flat.every(Number.isFinite)||!x.every(Number.isFinite))return wrap;
 let xmin=Math.min(...x),xmax=Math.max(...x),ymin=Math.min(...flat),ymax=Math.max(...flat);
 if(xmin===xmax)xmax=xmin+1;if(ymin===ymax){ymin-=.5;ymax+=.5;}
 const X=v=>70+(v-xmin)/(xmax-xmin)*620,Y=v=>270-(v-ymin)/(ymax-ymin)*230;
 svg.append(make("path",{d:"M70 35 V270 H690",fill:"none",stroke:"#667085"}));
 for(let i=0;i<=4;i++){
  const xv=xmin+(xmax-xmin)*i/4,yv=ymin+(ymax-ymin)*i/4;
  svg.append(make("text",{x:X(xv),y:290,"text-anchor":"middle","font-size":11,fill:"#172033"},Number(xv.toPrecision(4))));
  svg.append(make("text",{x:62,y:Y(yv)+4,"text-anchor":"end","font-size":11,fill:"#172033"},Number(yv.toPrecision(4))));
 }
 svg.append(make("text",{x:380,y:320,"text-anchor":"middle","font-size":12,fill:"#172033"},plot.x_label+" ["+plot.x_unit+"]"));
 svg.append(make("text",{x:70,y:20,"font-size":12,fill:"#172033"},plot.y_unit));
 const colors=["#0072B2","#D55E00","#009E73","#CC79A7","#6C4BA4","#865900"];
 plot.series.forEach((series,j)=>{
  const group=make("g"),color=colors[j%colors.length];
  const d=series.values.map((v,i)=>i===0?"M"+X(x[i])+" "+Y(v):plot.step?"H"+X(x[i])+"V"+Y(v):"L"+X(x[i])+" "+Y(v)).join(" ");
  group.append(make("path",{d,fill:"none",stroke:color,"stroke-width":2,"stroke-dasharray":j>=colors.length?"6 3":"none"}));
  if(x.length<=100)series.values.forEach((v,i)=>{const mark=make("circle",{cx:X(x[i]),cy:Y(v),r:2,fill:color});mark.append(make("title",{},series.name+": "+x[i]+" "+plot.x_unit+", "+v+" "+plot.y_unit));group.append(mark);});
  svg.append(group);const button=document.createElement("button");button.type="button";button.textContent=series.name;button.className="scientific-series series-"+(j%colors.length);button.setAttribute("aria-pressed","true");
  button.addEventListener("click",()=>{const hidden=button.getAttribute("aria-pressed")==="true";group.setAttribute("visibility",hidden?"hidden":"visible");button.setAttribute("aria-pressed",String(!hidden));});legend.append(button);
 });
 const note=document.createElement("p");note.className="muted";note.textContent=(plot.note||"")+" · 显示 "+plot.sampling.shown_points+" / "+plot.sampling.original_points+" 个点；未显示序列 "+plot.sampling.omitted_series+"。完整数据保留在收据中。";
 wrap.append(svg,legend,note);return wrap;
}
