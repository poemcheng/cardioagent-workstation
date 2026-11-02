'use strict';
// jsdom checks real HTTP and DOM behavior. It does not verify browser pixels.
const {JSDOM,VirtualConsole}=require('jsdom');
const assert=require('node:assert/strict');
const fs=require('fs');
const path=require('path');
const base=process.argv[2];
assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);
let cookie='',requests=0,dom;
async function request(url,options={}){
  const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);
  const headers=new Headers(options.headers);if(cookie)headers.set('Cookie',cookie);
  const response=await fetch(target,{...options,headers});requests++;
  for(const c of response.headers.getSetCookie())cookie=c.split(';')[0];
  return response;
}
async function data(url,options){const r=await request(url,options),d=await r.json();assert.ok(r.ok,JSON.stringify(d));return d;}
async function wait(predicate){const deadline=Date.now()+15000;while(!predicate()){if(Date.now()>deadline)throw Error('DOM wait timed out');await new Promise(resolve=>setTimeout(resolve,20));}}
(async()=>{
  const csrf=(await data('/api/session')).csrf;
  await data('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({username:'aipc-dom-admin',password:'Synthetic-DOM-password-9382'})});
  const errors=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
  dom=new JSDOM(await(await request('/aipc')).text(),{url:base+'/aipc',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const w=dom.window,$=id=>w.document.getElementById(id);w.fetch=request;w.HTMLElement.prototype.scrollIntoView=function(){};
  w.eval(fs.readFileSync(path.join(__dirname,'../static/aipc.js'),'utf-8'));
  await wait(()=>$('aipcCase').options.length===12&&!$('runBaseline').disabled&&$('aipcRuntime').textContent);
  let checks=0;const check=(v,label)=>{assert.ok(v,label);checks++;};
  check($('aipcTurns').children.length>0,'source rendered');
  check($('runLocalLLM').disabled,'missing LLM is explicit');
  await $('runBaseline').onclick();
  check($('aipcResultState').textContent==='草稿待人工覆核','draft state');
  check($('aipcMetrics').textContent.includes('lexical_section_baseline'),'baseline source');
  check($('aipcMetrics').textContent.includes('未執行語言模型'),'no fake model');
  check($('aipcMetrics').textContent.includes('待目標機驗證'),'NPU not presumed');
  w.document.querySelector('.evidence-card button').click();
  check(w.document.querySelectorAll('.source-turn.selected').length===1,'source jump');
  const record=await data($('exportAIPC').getAttribute('href'));
  check(record.evidence.every(e=>e.quote_verified),'verified full quotes');
  check(record.paired_imaging_case_id===null,'no invented pairing');
  check(record.actor==='aipc-dom-admin','authenticated actor');
  check(record.automatically_saved_to_medical_report===false,'human review retained');
  $('aipcCase').value='synthetic-02';await $('aipcCase').onchange();
  check($('exportAIPC').getAttribute('aria-disabled')==='true','switch invalidates export');
  await $('runBaseline').onclick();check($('aipcMissing').textContent==='待補：用藥核對','missing medication');
  $('aipcCase').value='synthetic-08';await $('aipcCase').onchange();await $('runBaseline').onclick();
  check($('aipcMissing').textContent==='待補：急性旗標','unknown acute');
  $('aipcCase').value='synthetic-07';await $('aipcCase').onchange();
  check(!$('runLocalLLM').disabled,'acute can bypass absent model');
  await $('runLocalLLM').onclick();check($('aipcResultState').textContent==='急性旗標優先','acute priority');
  check($('aipcMetrics').textContent.includes('explicit_acute_flag_rules'),'no acute LLM call');
  $('aipcSplit').value='test_1';await $('aipcSplit').onchange();
  check($('aipcCase').options.length===200,'official test split');
  check($('aipcMissing').textContent.includes('沒有結構交接欄位'),'unknown intake not invented');
  await $('runBaseline').onclick();check($('aipcEvidence').children.length>0,'real public data extraction');
  // A failing real request cannot leave an old export active.
  const nativeFetch=w.fetch;
  w.fetch=(url,options)=>request(url,options?.method==='POST'?{...options,body:JSON.stringify({case_id:'nonexistent',mode:'llm'})}:options);
  await $('runBaseline').onclick();w.fetch=nativeFetch;
  check($('exportAIPC').getAttribute('aria-disabled')==='true','failure clears stale result');
  check($('aipcMessage').textContent.includes('未產生新摘要'),'failure visible');
  check(errors.length===0,'no script errors');
  const result={passed:true,checks_count:checks,http_requests:requests,real_http:true,fetch_responses_mocked:false,browser_visual_qa:false,llm_executed:false,npu_execution_verified:false,errors};
  fs.writeFileSync(path.join(__dirname,'../evidence/aipc_dom_results.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));dom.window.close();
})().catch(error=>{dom?.window.close();console.error(error);process.exitCode=1;});
