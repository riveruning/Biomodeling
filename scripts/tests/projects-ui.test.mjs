// Render the shipped project UI against backend continuation states.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../src/biomodel_agent/workbench_assets/projects.js',import.meta.url),'utf8');
const elements={};
class Element {
  children=[];
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(){}
  addEventListener(){}
}
const project='a'.repeat(64);
const sandbox={Node:Element,location:{host:'offline',hash:'#'+project},
  localStorage:{getItem:()=>null,setItem:()=>{}},
  document:{querySelector:()=>({content:'dummy'}),
    getElementById:id=>elements[id]??=new Element(),createElement:()=>new Element(),createTextNode:x=>x}};
vm.createContext(sandbox);
vm.runInContext(source.slice(0,source.indexOf('$("new-project").onclick')),sandbox);
function render(state,continuation){
  sandbox.data={project_ref:project,scenario:'Offline control',state,continuation,
    budget:{planner_calls:1,planner_max_calls:1,iterations:0,max_iterations:3,native_reserved:0,max_native_calls:18,literature_requests:0,max_literature_requests:0},
    questions:[],iteration_facts:{},assessments:[],usage:{}};
  vm.runInContext('render(data)',sandbox);
}
render('ready',{can_advance:true,has_saved_response:true,requires_provider:false,reason:'saved_response'});
assert.equal(elements['advance-project'].disabled,false);
assert.match(elements['project-status-note'].textContent,/保存的研究决定/);
render('planner_budget_exhausted',{can_advance:false,has_saved_response:false,reason:'planner_budget_exhausted'});
assert.equal(elements['advance-project'].disabled,true);
assert.match(elements['project-status-note'].textContent,/额度已用完/);
for(const state of ['needs_recovery','awaiting_alignment','completed_with_limits','unsupported']){
  render(state,{can_advance:false,has_saved_response:true,reason:state});
  assert.equal(elements['advance-project'].disabled,true,state);
}
render('ready',{can_advance:true,has_saved_response:true,requires_provider:true,reason:'saved_response'});
assert.equal(elements['advance-project'].disabled,false); // Consent is handled by preview, not by a budget guess.
console.log('Project UI: saved decisions, exhausted budget and blocked/terminal states passed.');
