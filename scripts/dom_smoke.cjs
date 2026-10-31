// DOM + real HTTP integration check; canvas rendering is stubbed, not visual QA.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const base=process.argv[2];let cookie='';
async function request(url,options={}){const headers=new Headers(options.headers);if(cookie)headers.set('Cookie',cookie);const result=await fetch(new URL(url,base),{...options,headers});for(const value of result.headers.getSetCookie())cookie=value.split(';')[0];return result;}
async function json(url,options={}){const response=await request(url,options);const data=await response.json();assert.ok(response.ok,JSON.stringify(data));return data;}
const wait=async(predicate,label)=>{const deadline=Date.now()+10000;while(!predicate()){if(Date.now()>deadline)throw Error('Timeout: '+label);await new Promise(r=>setTimeout(r,25));}};
(async()=>{
 const setup=await json('/api/session');
 await json('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':setup.csrf},body:JSON.stringify({username:'dom-research-admin',password:'Synthetic-DOM-test-7391'})});
 const html=await (await request('/')).text(),errors=[];
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
 const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc}),w=dom.window;
 w.fetch=request;w.confirm=()=>false;w.print=()=>{};
 w.ResizeObserver=class{constructor(callback){this.callback=callback;}observe(){this.callback();}};
 w.Image=class{constructor(){this.complete=false;this.naturalWidth=0;this.width=512;this.height=428;}set src(value){this._src=value;this.complete=true;this.naturalWidth=512;queueMicrotask(()=>this.onload?.());}get src(){return this._src;}};
 const ctx={setTransform(){},fillRect(){},translate(){},scale(){},drawImage(){},strokeRect(){},fillText(){},beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){}};
 w.HTMLCanvasElement.prototype.getContext=()=>ctx;
 w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({width:700,height:470,left:0,top:0});
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 w.eval(fs.readFileSync(path.join(__dirname,'../static/hospital.js'),'utf8'));
 const $=id=>w.document.getElementById(id),event=target=>({target,preventDefault(){}}),checks=[];
 await wait(()=>$('patientId').textContent&&$('worklistBody').children.length===3,'initial hospital worklist');
 assert.equal($('worklistBody').children.length,3);assert.match($('viewerTitle').textContent,/MRI/);checks.push('authenticated worklist and MRI selection');
 // Self-contained read-only UI preview, not a screenshot or a deployed app.
 const preview=new JSDOM(w.document.documentElement.outerHTML),doc=preview.window.document;
 doc.querySelectorAll('script,link[rel="stylesheet"],meta[name="csrf-token"],dialog').forEach(n=>n.remove());
 const style=doc.createElement('style');style.textContent=fs.readFileSync(path.join(__dirname,'../static/hospital.css'),'utf8');doc.head.append(style);
 const asData=async url=>'data:image/png;base64,'+Buffer.from(await (await request(url)).arrayBuffer()).toString('base64');
 for(const img of Array.from(doc.querySelectorAll('img')))img.src=await asData(img.getAttribute('src'));
 const center=Number($('sliceSlider').value),centerUrl=$('filmstrip').children[center].querySelector('img').getAttribute('src');
 const display=doc.createElement('img');display.src=await asData(centerUrl);display.alt='ACDC 心臟 MRI 研究影像';display.style.cssText='width:100%;height:100%;object-fit:contain';doc.getElementById('imageCanvas').replaceWith(display);
 doc.querySelectorAll('button,input,select,textarea').forEach(n=>n.disabled=true);doc.querySelectorAll('a').forEach(n=>n.removeAttribute('href'));
 doc.getElementById('userInfo').textContent='影像覆核 / 介面預覽';doc.querySelector('.environment-strip').textContent='唯讀介面預覽｜研究影像｜未連接醫院系統';doc.title='CardioAgent 醫院式工作站｜唯讀介面預覽';
 fs.writeFileSync(path.join(__dirname,'../docs/Hospital_Workstation_Preview.html'),'<!doctype html>\n'+doc.documentElement.outerHTML);preview.window.close();
 $('findings').value='人工填寫的研究觀察，不作為臨床診斷。';$('impression').value='影像流程研究測試。';$('findings').dispatchEvent(new w.Event('input'));
 assert.equal($('dirtyNotice').hidden,false);await $('saveDraft').onclick();assert.match($('reportVersion').textContent,/草稿/);checks.push('draft saved through real backend');
 $('reportForm').onsubmit(event());await wait(()=>$('reportVersion').textContent.includes('待簽核'),'preliminary report');
 assert.equal($('signBtn').disabled,false);$('signBtn').onclick();$('signConfirmed').checked=true;await $('signForm').onsubmit(event());
 assert.match($('reportVersion').textContent,/已簽核/);assert.equal($('findings').disabled,true);checks.push('human confirmation and locked signed report');
 $('addendumBtn').onclick();$('addendumReason').value='工作流程驗證';$('addendumText').value='此為補充研究說明，保留原報告。';await $('addendumForm').onsubmit(event($('addendumForm')));
 assert.match($('reportVersion').textContent,/已補充/);assert.equal($('addendaList').children.length,1);checks.push('addendum without replacing signed content');
 $('compare').onclick();assert.equal($('compareCell').hidden,false);checks.push('adjacent-slice layout toggles');
 const row=Array.from($('worklistBody').children).find(r=>r.textContent.includes('協作流程測試'));await row.onclick();
 assert.equal($('findings').value,'');assert.equal($('referenceOverlay').checked,false);checks.push('study switch clears report and reference overlays');
 $('contextAge').value='62';$('contextAcute').value='false';$('contextHistory').value='true';$('contextConsent').checked=true;
 await $('agentForm').onsubmit(event());await wait(()=>$('agentStatus').textContent.includes('等待人工覆核'),'agent completion');
 await $('approveAgent').onclick();await wait(()=>$('agentTasks').textContent.includes('本機協作任務：2'),'approved tasks');checks.push('agent handoff gated by authenticated approval');
 $('search').value='ACDC';$('search').oninput();assert.equal($('worklistBody').children.length,1);checks.push('worklist search');
 $('worklistToggle').onclick();assert.equal(w.document.body.classList.contains('worklist-collapsed'),true);$('worklistToggle').onclick();assert.equal(w.document.body.classList.contains('worklist-collapsed'),false);checks.push('worklist collapse preserves study context');
 assert.deepEqual(errors,[]);const result={passed:true,checks,jsdom:'26.1.0',real_http:true,canvas:'stubbed',browser_visual_qa:false,errors};
 fs.writeFileSync(path.join(__dirname,'../evidence/hospital_dom.json'),JSON.stringify(result,null,2));process.stdout.write(JSON.stringify(result,null,2));dom.window.close();
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
