// Real installed Chromium/Edge browser; no third-party JS dependency or model API.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../..',import.meta.url));
const localPython=path.join(root,'.venv/bin/python');
const python=process.env.BIOMODEL_PYTHON||(process.platform==='win32'?'python':existsSync(localPython)?localPython:'python');
const candidates=process.platform==='win32'?[path.join(process.env['ProgramFiles(x86)']||'','Microsoft/Edge/Application/msedge.exe'),path.join(process.env.ProgramFiles||'','Google/Chrome/Application/chrome.exe')]:['/usr/bin/chromium','/usr/bin/chromium-browser','/usr/bin/google-chrome'];
const browser=process.env.BIOMODEL_BROWSER||candidates.find(existsSync);
assert.ok(browser,'Set BIOMODEL_BROWSER to an installed Chromium/Edge executable.');
const temporary=await mkdtemp(path.join(os.tmpdir(),'biomodel-browser-'));
const server=spawn(python,[path.join(root,'scripts/tests/workbench-browser-fixture.py'),path.join(temporary,'store')],{cwd:root,stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.setEncoding('utf8');server.stderr.on('data',text=>{stderr=(stderr+text).slice(-5000);});
const report=[];let profileLocal;
try{
 const info=await new Promise((resolve,reject)=>{
  let text='';const timeout=setTimeout(()=>reject(Error('Local fixture startup timeout: '+stderr)),45000);
  server.once('error',reject);server.once('exit',()=>reject(Error('Local fixture exited: '+stderr)));
  server.stdout.setEncoding('utf8');server.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n')){try{const data=JSON.parse(line);if(data.origin){clearTimeout(timeout);resolve(data);return;}}catch(_){}}});
 });
 for(const mode of ['projects','materials']){
  let profile;
  if(process.platform!=='win32'&&browser.toLowerCase().endsWith('.exe')){
   assert.ok(process.env.BIOMODEL_PROFILE_ROOT&&process.env.BIOMODEL_WINDOWS_PROFILE_ROOT,'WSL Windows browser requires explicit local and Windows profile roots.');
   profileLocal=await mkdtemp(path.join(process.env.BIOMODEL_PROFILE_ROOT,'browser-smoke-'));
   profile=path.win32.join(process.env.BIOMODEL_WINDOWS_PROFILE_ROOT,path.basename(profileLocal));
  }else{profileLocal=await mkdtemp(path.join(temporary,'profile-'));profile=profileLocal;}
  const url=mode==='projects'?info.origin+'/projects#'+info.paid:info.origin+'/';
  const args=['--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-extensions',
   '--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--dump-dom','--virtual-time-budget=60000',url];
  const child=spawn(browser,args,{stdio:['ignore','pipe','pipe']});let html='',errors='';
  child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
  child.stdout.on('data',chunk=>{html+=chunk;if(html.length>6_000_000)child.kill();});child.stderr.on('data',chunk=>{errors=(errors+chunk).slice(-1000);});
  const timeout=setTimeout(()=>child.kill(),90000);
  const [code]=await once(child,'exit');clearTimeout(timeout);
  assert.equal(code,0,'Browser did not exit successfully: '+errors);
  const match=html.match(/<pre id="browser-smoke-result">([\s\S]*?)<\/pre>/);
  assert.ok(match,'Browser did not produce a test result; stderr: '+errors);
  const result=JSON.parse(match[1].replaceAll('&amp;','&').replaceAll('&lt;','<').replaceAll('&gt;','>'));
  report.push({mode,...result});console.log(JSON.stringify({mode,...result}));
  assert.equal(result.status,'passed',result.error||'Browser checks failed');
  // Delete only this test's newly created, empty-at-start browser profile.
  await rm(profileLocal,{recursive:true,force:true,maxRetries:5,retryDelay:200});profileLocal=null;
 }
}finally{
 server.kill();await Promise.race([once(server,'exit'),new Promise(resolve=>setTimeout(resolve,2000))]);
 if(process.env.BIOMODEL_BROWSER_REPORT)await writeFile(process.env.BIOMODEL_BROWSER_REPORT,JSON.stringify(report,null,2)+'\n');
 await rm(temporary,{recursive:true,force:true,maxRetries:5,retryDelay:200}).catch(()=>{});
 if(profileLocal)await rm(profileLocal,{recursive:true,force:true,maxRetries:5,retryDelay:200}).catch(()=>{});
}
