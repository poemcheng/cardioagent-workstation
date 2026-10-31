// Authenticated real HTTP + DOM flow. Canvas/Image doubles are NOT visual QA.
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const base=process.argv[2];
if(!base||!/^http:\/\/127\.0\.0\.1:\d+\/?$/.test(base))throw Error('Use a fresh disposable loopback HTTP server.');
let cookie='';const requests=[],doms=[],errors=[],checks=[];
async function request(url,options={}){
 const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);
 const headers=new Headers(options.headers);if(cookie)headers.set('Cookie',cookie);
 const result=await fetch(target,{...options,headers});for(const value of result.headers.getSetCookie())cookie=value.split(';')[0];
 const record={method:options.method||'GET',path:target.pathname,status:result.status};
 if((result.headers.get('content-type')||'').includes('json'))record.payload=await result.clone().json();requests.push(record);return result;
}
async function json(url,options={}){const r=await request(url,options),p=await r.json();assert.ok(r.ok,`${r.status}: ${JSON.stringify(p)}`);return p;}
async function wait(predicate,label){const deadline=Date.now()+45000;while(!predicate()){if(Date.now()>deadline)throw Error('Timeout: '+label);await new Promise(r=>setTimeout(r,25));}}
const last=(method,url)=>[...requests].reverse().find(r=>r.method===method&&r.path===url);
function wireDOM(html,url,scripts){
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
 const dom=new JSDOM(html,{url:new URL(url,base).href,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});doms.push(dom);const w=dom.window;
 w.fetch=request;w.confirm=()=>true;w.print=()=>{};
 w.ResizeObserver=class{constructor(fn){this.fn=fn;}observe(){this.fn();}disconnect(){}};
 w.Image=class{constructor(){this.complete=false;this.naturalWidth=0;this.width=96;this.height=96;}set src(v){this._src=v;this.complete=true;this.naturalWidth=96;queueMicrotask(()=>this.onload?.());}get src(){return this._src;}};
 const ctx={setTransform(){},fillRect(){},translate(){},scale(){},drawImage(){},strokeRect(){},fillText(){},beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){}};
 w.HTMLCanvasElement.prototype.getContext=function(type){return type==='webgl'?null:ctx;};
 w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({width:700,height:470,left:0,top:0});
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 w.eval(scripts.map(name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8')).join('\n;\n'));
 return {dom,w,$:id=>w.document.getElementById(id)};
}
function event(target){return{target,preventDefault(){}};}
async function png(url){const r=await request(url);assert.equal(r.status,200);assert.match(r.headers.get('content-type'),/image\/png/);const bytes=new Uint8Array(await r.arrayBuffer());assert.deepEqual(Array.from(bytes.slice(0,8)),[137,80,78,71,13,10,26,10]);return bytes.length;}

(async()=>{
 const status=await json('/api/auth/status');assert.equal(status.setup_required,true,'fresh disposable data required');const session=await json('/api/session');
 await json('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({username:'integrated-dom-admin',password:'Synthetic-Integrated-DOM-7391'})});
 const page=wireDOM(await(await request('/analysis')).text(),'/analysis',['mesh_viewer.js','analysis.js']);const {w,$}=page;
 await wait(()=>$('analysisConnection').textContent.includes('已連線')&&$('analysisCase').options.length===3,'initial MRI worklist');
 await wait(()=>$('analysisUseLLM').disabled,'unconfigured local model remains disclosed');
 assert.equal($('analysisCase').value,'research-mri-01');assert.equal($('recognitionMode').value,'inspect');assert.equal($('metricVolume').textContent,'—');
 assert.match($('analysisIdentity').textContent,/RESEARCH/);assert.equal($('reportDraftLink').getAttribute('aria-disabled'),'true');
 checks.push('authenticated analysis workspace defaults to real MRI inspection without invented physical volume or report transfer');

 const analysesBefore=requests.filter(r=>r.method==='POST'&&r.path==='/api/analyses').length;
 await $('loadPhantom').onclick();
 assert.equal($('analysisCase').value,'demo-volume-phantom');assert.equal($('analysisCenter').max,'32');assert.equal($('analysisThreshold').value,'120');
 assert.equal($('recognitionMode').value,'inspect');assert.match($('analysisSource').textContent,/合成/);
 assert.equal(requests.filter(r=>r.method==='POST'&&r.path==='/api/analyses').length,analysesBefore,'loading phantom must not execute analysis');
 checks.push('explicit phantom loading adds synthetic 32-slice study with threshold120 and does not auto-run');

 await $('analysisForm').onsubmit(event($('analysisForm')));
 const created=last('POST','/api/analyses');assert.equal(created.status,201,JSON.stringify(created.payload));const phantom=created.payload;
 assert.equal(phantom.status,'pending_review');assert.equal(phantom.settings.segmentation_mode,'threshold');
 assert.equal(phantom.source.slice_count,32);assert.deepEqual(phantom.source.shape_zyx,[32,96,96]);
 assert.equal(phantom.reconstruction.statistics.foreground_voxels,28736);assert.equal(phantom.reconstruction.statistics.physical_volume_mm3,89800);
 assert.equal(phantom.comparison.segmentation.dice,1);assert.equal(phantom.comparison.segmentation.iou,1);
 assert.equal(phantom.reconstruction.mesh.units,'mm');assert.ok(phantom.reconstruction.mesh.vertices.length>1000);assert.ok(phantom.reconstruction.mesh.faces.length>1000);
 assert.equal(phantom.recognition.inference_executed,false);assert.match($('metricCandidates').textContent,/未辨識/);
 assert.equal($('metricVoxels').textContent,'28,736');assert.equal($('metricVolume').textContent,'89,800');assert.equal($('metricDice').textContent,'1');
 assert.ok($('pipelineStages').children.length>=7);assert.match($('languageSource').textContent,/rules/);
 assert.equal($('reportDraftLink').getAttribute('aria-disabled'),'true');
 checks.push('full real pipeline returns independently matched phantom mask28736, design-volume89800mm3, Dice1 and actual surface without claiming learned detection');

 assert.ok(await png($('analysisOriginal').getAttribute('src'))>100);const overlayBytes=await png($('analysisOverlay').getAttribute('src'));assert.ok(overlayBytes>100);
 const maskBytes=await png(`/api/analyses/${phantom.id}/slices/${phantom.settings.center}?view=mask`);assert.ok(maskBytes>100);
 $('showOverlay').checked=false;$('showOverlay').onchange();assert.match($('analysisOverlay').src,/view=original/);
 checks.push('original, overlay and saved mask are actual authenticated PNG responses; overlay toggle preserves original source');

 w.document.querySelector('[data-analysis-tab="mesh"]').click();assert.equal($('meshPane').hidden,false);assert.match($('meshRenderer').textContent,/CPU.*WebGL/);
 assert.match($('meshDetails').textContent,/mm/);assert.match($('meshDetails').textContent,/32 × 96 × 96/);
 for(const [id,kind]of[['objExport','obj'],['stlExport','stl']]){const r=await request($(id).getAttribute('href'));assert.equal(r.status,200);const body=await r.text();assert.ok(body.length>1000);if(kind==='obj')assert.match(body,/\nv /);else assert.match(body,/facet normal/);}
 checks.push('actual mesh reaches disclosed CPU wireframe fallback and OBJ/STL downloads contain real geometry');

 w.document.querySelector('[data-analysis-tab="graph"]').click();const graph=$('provenanceGraph').querySelector('svg');assert.ok(graph);const nodes=graph.querySelectorAll('[role=button]');assert.equal(nodes.length,phantom.graph.nodes.length);nodes[0].onclick();const detail=JSON.parse($('graphDetail').textContent);assert.equal(detail.node,phantom.graph.nodes[0].id);assert.ok(Array.isArray(detail.relationships));
 assert.ok($('languageSummary').textContent);assert.ok(!/Grad-CAM/.test($('languageSource').textContent));
 checks.push('source graph contains actual analysis nodes and interactive evidence details; language source is disclosed separately');

 w.document.querySelector('[data-analysis-tab="discussion"]').click();assert.equal($('discussionState').textContent,'仍有待補項目');const links=Array.from($('clinicalSources').querySelectorAll('a'));assert.ok(links.length>=2);assert.ok(links.every(a=>/^https:\/\/(www\.)?(jacc\.org|acc\.org|escardio\.org|professional\.heart\.org|ahajournals\.org|academic\.oup\.com|pmc\.ncbi\.nlm\.nih\.gov)/.test(a.href)));
 const flags=Array.from($('discussionChecks').querySelectorAll('select'));assert.equal(flags.length,7);for(const flag of flags){flag.value=flag.id==='discussion-acute'?'false':'true';flag.dispatchEvent(new w.Event('change'));}$('clinicianNote').value='流程測試：由人員填寫之討論備註，非病人處置決定。';$('clinicianNote').dispatchEvent(new w.Event('input'));
 assert.equal($('discussionDirtyNotice').hidden,false);assert.equal($('approveAnalysis').disabled,true);assert.equal($('rejectAnalysis').disabled,true);
 const dirtyNote=$('clinicianNote').value,beforeCancel=requests.length;let discardPrompts=0;w.confirm=()=>{discardPrompts++;return false;};
 $('analysisCase').value='research-mri-01';await $('analysisCase').onchange();assert.equal($('analysisCase').value,'demo-volume-phantom');assert.equal($('clinicianNote').value,dirtyNote);
 await $('analysisForm').onsubmit(event($('analysisForm')));await $('loadPhantom').onclick();await $('analysisHistory').querySelector('button').onclick();assert.equal(discardPrompts,4);assert.equal(requests.length,beforeCancel);assert.equal($('discussionDirtyNotice').hidden,false);w.confirm=()=>true;
 checks.push('unsaved discussion edits block review; cancelling study change, rerun, phantom load and history preserves edits without HTTP mutations');
 await $('discussionForm').onsubmit(event($('discussionForm')));
 assert.equal($('discussionDirtyNotice').hidden,true);assert.equal($('approveAnalysis').disabled,false);
 const saved=last('POST',`/api/analyses/${phantom.id}/discussion`).payload;assert.equal(saved.discussion.status,'ready_for_mdt');assert.equal(saved.discussion.recommendation.procedure,null);assert.equal(saved.discussion.surgery_planning_ready,false);assert.equal(saved.discussion.automatic_orders_created,false);assert.equal(saved.discussion.clinical_note.origin,'clinician_entered');
 assert.match($('discussionState').textContent,/未核准手術/);assert.equal($('discussionOptions').children.length,3);
 checks.push('all manually completed checklist flags permit MDT discussion only, with official sources, clinician-note origin and no procedure/order');

 $('approveAnalysis').onclick();assert.equal($('analysisReviewDialog').open,true);assert.equal($('analysisReviewConfirmed').checked,false);
 const reviewCount=requests.filter(r=>r.method==='POST'&&r.path.endsWith('/review')).length;await $('confirmAnalysisReview').onclick();assert.equal(requests.filter(r=>r.method==='POST'&&r.path.endsWith('/review')).length,reviewCount);assert.match($('analysisToast').textContent,/勾選/);
 $('analysisReviewConfirmed').checked=true;await $('confirmAnalysisReview').onclick();const approved=last('POST',`/api/analyses/${phantom.id}/review`).payload;
 assert.equal(approved.status,'approved');assert.equal(approved.review.actor,'integrated-dom-admin');assert.equal(approved.review.review_confirmed,true);assert.equal(approved.review.source_integrity_checked.source_current,true);assert.equal($('analysisReviewDialog').open,false);assert.equal($('saveDiscussion').disabled,true);assert.equal($('approveAnalysis').disabled,true);
 assert.equal($('reportDraftLink').getAttribute('aria-disabled'),'false');const reportURL=$('reportDraftLink').getAttribute('href');assert.match(reportURL,new RegExp('analysis='+phantom.id));
 const exported=await json($('analysisExport').getAttribute('href'));assert.equal(exported.analysis.id,phantom.id);assert.equal(exported.analysis.status,'approved');assert.equal(exported.analysis.discussion.recommendation.procedure,null);
 checks.push('explicit checked confirmation records analysis approval, locks discussion and enables only an unsaved report transfer; full export preserves evidence');

 const report=wireDOM(await(await request(reportURL)).text(),reportURL,['hospital.js','competition.js']),R=report.$;
 await wait(()=>R('patientId').textContent==='PHANTOM-3D'&&R('preparationStatus').textContent.includes('尚未寫入'),'reviewed analysis draft on corresponding hospital study');
 assert.equal(R('prepareTab').hidden,false);assert.equal(R('copyPrepared').disabled,false);assert.match(R('preparationEngine').textContent,/reviewed_analysis_facts/);
 const studyId=new URL(reportURL,base).searchParams.get('study');assert.equal((await json('/api/hospital/studies/'+studyId)).report,null);
 R('copyPrepared').onclick();assert.equal(R('dirtyNotice').hidden,false);assert.equal(R('reportTab').hidden,false);assert.match(R('findings').value,/28736/);assert.equal((await json('/api/hospital/studies/'+studyId)).report,null);
 R('findings').value+='\n人工註記：本次為合成流程研究。';R('findings').dispatchEvent(new report.w.Event('input'));await R('saveDraft').onclick();assert.equal((await json('/api/hospital/studies/'+studyId)).report.status,'draft');
 checks.push('reviewed analysis opens matching hospital study, copy creates dirty edits without autosave, and explicit save creates a report draft');

 $('analysisCase').value='research-mri-01';await $('analysisCase').onchange();assert.equal($('metricVoxels').textContent,'—');assert.equal($('metricDice').textContent,'—');assert.equal($('metricVolume').textContent,'—');assert.equal($('reportDraftLink').getAttribute('aria-disabled'),'true');assert.equal($('provenanceGraph').children.length,0);assert.equal($('recognitionMode').value,'inspect');
 await $('analysisForm').onsubmit(event($('analysisForm')));const mri=last('POST','/api/analyses').payload;assert.equal(mri.source.modality,'MRI');assert.equal(mri.recognition.inference_executed,false);assert.equal(mri.comparison.segmentation.available,true);assert.ok(Math.abs(mri.comparison.segmentation.dice-0.1814946331797905)<1e-12);assert.equal(mri.comparison.segmentation.reference_voxels,75846);assert.equal(mri.reconstruction.statistics.physical_volume_mm3,null);assert.equal(mri.reconstruction.mesh.units,'voxel');assert.equal($('metricVolume').textContent,'—');assert.equal($('metricDice').textContent,'0.1815');assert.match($('metricCandidates').textContent,/未辨識/);
 checks.push('switching study clears prior results; MRI threshold baseline reports measured0.1815agreement with supplied labels, unknown physical spacing, and no CT inference');

 const providedOption=$('segmentationMode').querySelector('[value=provided_reference]');assert.ok(providedOption);assert.equal(providedOption.disabled,false);
 $('segmentationMode').value='provided_reference';$('segmentationMode').onchange();assert.equal($('analysisThreshold').disabled,true);assert.equal($('referenceClassLabel').hidden,false);$('referenceClass').value='0';
 await $('analysisForm').onsubmit(event($('analysisForm')));const labelled=last('POST','/api/analyses').payload;assert.equal(labelled.settings.segmentation_mode,'provided_reference');assert.equal(labelled.reconstruction.statistics.foreground_voxels,75846);assert.equal(labelled.reconstruction.statistics.physical_volume_mm3,null);assert.equal(labelled.reconstruction.mesh.units,'voxel');assert.equal(labelled.recognition.inference_executed,false);assert.equal(labelled.comparison.segmentation.available,false);assert.equal(labelled.comparison.segmentation.dice,null);assert.equal($('metricVoxels').textContent,'75,846');assert.equal($('metricDice').textContent,'—');assert.equal($('metricVolume').textContent,'—');assert.match($('meshDetails').textContent,/voxel/);assert.ok(await png(`/api/analyses/${labelled.id}/slices/${labelled.settings.center}?view=mask`)>100);
 checks.push('provided MRI labels reconstruct75846-voxel union without AI inference, physical units, or self-reference Dice');

 $('segmentationMode').value='registered_model';$('segmentationMode').onchange();assert.equal($('analysisThreshold').disabled,true);await $('analysisForm').onsubmit(event($('analysisForm')));const blocked=last('POST','/api/analyses').payload;assert.equal(blocked.settings.segmentation_mode,'registered_model');assert.equal(blocked.segmentation,null);assert.equal(blocked.reconstruction,null);assert.ok(blocked.stages.some(s=>s.key==='segmentation'&&s.status==='blocked'));assert.equal(blocked.integrity.approvable,false);assert.equal($('metricVoxels').textContent,'—');assert.equal($('objExport').getAttribute('aria-disabled'),'true');assert.equal($('approveAnalysis').disabled,true);assert.equal($('analysisIntegrity').hidden,false);assert.match($('analysisIntegrity').textContent,/segmentation|分割/);
 checks.push('missing registered segmentation is visibly blocked, with no threshold substitution, false mask, physical volume or mesh download');

 const unavailable=w.document.createElement('option');unavailable.value='missing-case';unavailable.textContent='Removed study';$('analysisCase').append(unavailable);
 let releaseCase;const caseGate=new Promise(resolve=>releaseCase=resolve);w.fetch=async(url,options)=>{const response=await request(url,options);if(url==='/api/cases/missing-case')await caseGate;return response;};
 $('analysisCase').value='missing-case';const changing=$('analysisCase').onchange();assert.equal($('executeAnalysis').disabled,true);assert.equal($('analysisCase').disabled,true);
 const beforeLoadingSubmit=requests.filter(r=>r.method==='POST'&&r.path==='/api/analyses').length;await $('analysisForm').onsubmit(event($('analysisForm')));assert.equal(requests.filter(r=>r.method==='POST'&&r.path==='/api/analyses').length,beforeLoadingSubmit);
 await wait(()=>last('GET','/api/cases/missing-case'),'actual unavailable case response');assert.equal(last('GET','/api/cases/missing-case').status,404);releaseCase();await changing;w.fetch=request;unavailable.remove();
 assert.equal($('analysisCase').value,'research-mri-01');assert.match($('analysisBadge').textContent,new RegExp(blocked.id.slice(0,8)));assert.equal($('executeAnalysis').disabled,false);assert.equal($('analysisIntegrity').hidden,false);
 checks.push('case load locks execution; a delayed genuine404 restores the previous case and analysis without creating a mismatched analysis');

 await $('analysisStatusBtn').onclick();assert.equal($('analysisModels').open,true);const local=JSON.parse($('analysisModelStatus').textContent);assert.equal(local.segmentation_and_3d.npu_execution_verified,false);assert.equal(local.segmentation_and_3d.amd_hardware_verified,false);assert.equal(local.text_and_speech.npu_verified,false);
 checks.push('model readiness comes from actual local APIs and preserves AMD/NPU unverified flags');
 assert.deepEqual(errors,[]);
 const result={passed:true,checks,checks_count:checks.length,real_http:true,authenticated_session:true,fetch_responses_mocked:false,http_requests:requests.length,canvas:'2D recording double; WebGL unavailable',image_loading:'stubbed; actual PNG endpoints independently fetched',browser_visual_qa:false,amd_hardware_verified:false,npu_execution_verified:false,completed_at:new Date().toISOString(),errors};
 fs.mkdirSync(path.join(__dirname,'../evidence'),{recursive:true});fs.writeFileSync(path.join(__dirname,'../evidence/integrated_dom.json'),JSON.stringify(result,null,2));process.stdout.write(JSON.stringify(result,null,2)+'\n');
 for(const dom of doms)dom.window.close();
})().catch(error=>{for(const dom of doms)dom.window.close();process.stderr.write(error.stack+'\n');process.exitCode=1;});
