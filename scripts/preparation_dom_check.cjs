// Actual Python/Flask transport plus jsdom interactions. No browser pixel QA.
'use strict';
const fs=require('fs'),path=require('path'),{spawn}=require('child_process'),readline=require('readline'),assert=require('assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..');
const python=spawn(process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python',['-u','-c',`import sys,tempfile
from pathlib import Path
from cardio.browser_runtime import BrowserRuntime
with tempfile.TemporaryDirectory() as d:
 r=BrowserRuntime(Path(d)/'session')
 for line in sys.stdin:
  try: print(r.request(line),flush=True)
  except Exception as e: print(__import__('json').dumps({'transport_error':str(e)}),flush=True)
 r.close()
`],{cwd:root,stdio:['pipe','pipe','inherit']});
let pending=[],dom;const checks=[],errors=[];
readline.createInterface({input:python.stdout}).on('line',line=>{const p=pending.shift();if(p){try{const r=JSON.parse(line);r.transport_error?p.reject(Error(r.transport_error)):p.resolve(r);}catch(e){p.reject(e);}}});
function transport(payload){return new Promise((resolve,reject)=>{pending.push({resolve,reject});python.stdin.write(JSON.stringify(payload)+'\n');});}
async function request(url,opt={}){const u=new URL(url,'http://workstation.invalid');const r=await transport({path:u.pathname+u.search,method:opt.method||'GET',headers:opt.headers||{},body:opt.body});return new Response(Buffer.from(r.base64,'base64'),{status:r.status,headers:r.headers});}
async function until(fn){const end=Date.now()+15000;while(!fn()){if(Date.now()>end)throw Error('DOM condition timeout');await new Promise(r=>setTimeout(r,10));}}
(async()=>{try{
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 dom=new JSDOM(await(await request('/workspace')).text(),{url:'http://workstation.invalid/workspace',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
 const w=dom.window,$=id=>w.document.getElementById(id);w.fetch=request;w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.eval(fs.readFileSync(path.join(root,'static/workspace.js'),'utf8'));
 await until(()=>$('prepUser').textContent.includes('browser-visitor')&&$('prepMessage').textContent.includes('核對'));
 assert.equal($('countOrders').textContent,'0');checks.push('Initial board has zero actual orders and authenticated demo scope');
 $('newOrder').click();$('exampleBrief').click();$('briefForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 await until(()=>$('countOrders').textContent==='1');assert.equal($('orderDetail').querySelector('button').disabled,true);assert.match($('orderDetail').textContent,/尚未確認/);checks.push('Unknown clinical/context/authorization fields prevent execution');
 [...$('orderDetail').querySelectorAll('button')].find(x=>x.textContent==='另存調整規格').click();
 $('briefAcute').value='false';$('briefHistory').value='true';$('briefConsent').value='true';$('briefForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 await until(()=>$('countOrders').textContent==='2');const run=$('orderDetail').querySelector('button');assert.equal(run.disabled,false);run.click();
 await until(()=>$('countReview').textContent==='1');assert.match($('orderDetail').textContent,/75,846/);assert.match($('orderDetail').textContent,/未執行病灶辨識/);assert.equal($('orderDetail').querySelectorAll('table tr').length,9);checks.push('New immutable brief executes actual MRI reconstruction and displays all eight tool stages');
 const link=[...$('orderDetail').querySelectorAll('a')].find(x=>x.textContent==='開啟影像與覆核');assert.match(link.href,/analysis\?case=research-mri-01&analysis=[a-f0-9]{32}/);checks.push('Prepared evidence links to the existing source-bound human review workspace');
 const token=w.document.querySelector('meta[name="csrf-token"]').content;const post=async(url,body)=>{const r=await request(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)});assert.ok(r.ok,await r.clone().text());return r.json();};
 const b=await(await request('/api/workspace')).json(),a=b.orders.find(o=>o.analysis_id).analysis;
 await post('/api/analyses/'+a.id+'/review',{version:a.version,decision:'approve',review_confirmed:true});$('refreshBoard').click();await until(()=>$('countReviewed').textContent==='1');checks.push('Board derives approved state from actual analysis review instead of auto-approving jobs');
 for(const [id,value] of Object.entries({costCases:100,costManual:20,costAssisted:10,costHourly:600,costMonthly:5000}))$(id).value=value;
 $('costForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await until(()=>$('costResult').textContent.includes('5,000'));assert.match($('costResult').textContent,/非已驗證/);checks.push('Cost inputs persist and calculate the explicitly labelled scenario without claiming observed savings');
 $('newOrder').click();$('briefGoal').value='<img src=x onerror=alert(1)>'; $('briefQuestion').value='DOM safety check';$('briefForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await until(()=>$('countOrders').textContent==='3');assert.equal($('orderList').querySelectorAll('img').length,0);assert.match($('orderList').textContent,/<img src=x/);checks.push('Untrusted brief text renders as text and does not create HTML elements');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks,checks_count:checks.length,transport:'actual BrowserRuntime Flask client',browser_visual_qa:false,errors},null,2));
 }finally{dom?.window.close();python.stdin.end();}})().catch(e=>{console.error(e);python.kill();process.exitCode=1;});
