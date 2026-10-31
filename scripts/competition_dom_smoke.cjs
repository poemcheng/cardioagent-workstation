// Authenticated DOM + real HTTP competition flow. Canvas/Image are test doubles;
// this verifies UI/API behavior, not browser rendering or AMD hardware execution.
// Usage: NODE_PATH=/path/to/node_modules node scripts/competition_dom_smoke.cjs http://127.0.0.1:8877
// Run against a fresh, disposable server data directory.
"use strict";
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');

const base = process.argv[2];
if (!base || !/^http:\/\/127\.0\.0\.1:\d+\/?$/.test(base)) {
  throw Error('Provide a fresh local HTTP server URL, e.g. http://127.0.0.1:8877');
}
let cookie = '';
let activeDom = null;
const requests = [];
async function request(url, options = {}) {
  const target = new URL(url, base);
  assert.equal(target.origin, new URL(base).origin, 'test HTTP must remain same-origin');
  const headers = new Headers(options.headers);
  if (cookie) headers.set('Cookie', cookie);
  const response = await fetch(target, { ...options, headers });
  for (const value of response.headers.getSetCookie()) cookie = value.split(';')[0];
  const record = { method: options.method || 'GET', path: target.pathname, status: response.status };
  if ((response.headers.get('content-type') || '').includes('json')) {
    record.payload = await response.clone().json();
  }
  requests.push(record);
  return response;
}
async function json(url, options = {}) {
  const response = await request(url, options);
  const data = await response.json();
  assert.ok(response.ok, `${response.status}: ${JSON.stringify(data)}`);
  return data;
}
async function wait(predicate, label) {
  const deadline = Date.now() + 30000;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error('Timeout: ' + label);
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

(async () => {
  const auth = await json('/api/auth/status');
  assert.equal(auth.setup_required, true, 'use a fresh disposable server, not an existing workstation');
  const setup = await json('/api/session');
  await json('/api/auth/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': setup.csrf },
    body: JSON.stringify({ username: 'competition-dom-admin', password: 'Synthetic-Competition-DOM-7391' })
  });
  const html = await (await request('/')).text();
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(String(error)));
  const dom = new JSDOM(html, {
    url: base, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console
  });
  activeDom = dom;
  const w = dom.window;
  w.fetch = request;
  w.confirm = () => true;
  w.print = () => {};
  w.ResizeObserver = class { constructor(callback) { this.callback = callback; } observe() { this.callback(); } };
  w.Image = class {
    constructor() { this.complete = false; this.naturalWidth = 0; this.width = 512; this.height = 428; }
    set src(value) { this._src = value; this.complete = true; this.naturalWidth = 512; queueMicrotask(() => this.onload?.()); }
    get src() { return this._src; }
  };
  const ctx = {
    setTransform() {}, fillRect() {}, translate() {}, scale() {}, drawImage() {}, strokeRect() {},
    fillText() {}, beginPath() {}, arc() {}, fill() {}, moveTo() {}, lineTo() {}, stroke() {}
  };
  w.HTMLCanvasElement.prototype.getContext = () => ctx;
  w.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ width: 700, height: 470, left: 0, top: 0 });
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  const downloads = [];
  w.Blob = Blob;
  w.URL.createObjectURL = blob => { downloads.push({ blob }); return 'blob:competition-test-' + downloads.length; };
  w.URL.revokeObjectURL = () => {};
  const nativeAnchorClick = w.HTMLAnchorElement.prototype.click;
  w.HTMLAnchorElement.prototype.click = function () {
    if (this.href.startsWith('blob:competition-test-')) {
      downloads[downloads.length - 1].filename = this.download;
      return;
    }
    return nativeAnchorClick.call(this);
  };
  const scripts = ['hospital.js', 'competition.js'].map(name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8'));
  w.eval(scripts.join('\n;\n'));
  const $ = id => w.document.getElementById(id);
  const event = target => ({ target, preventDefault() {} });
  const checks = [];
  const last = (method, endpoint) => [...requests].reverse().find(r => r.method === method && r.path === endpoint);
  const apiPost = (endpoint, body) => request(endpoint, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': w.document.querySelector('meta[name="csrf-token"]').content },
    body: JSON.stringify(body)
  });
  async function scenario(name) {
    $('demoScenario').value = name;
    await $('loadScenario').onclick();
    assert.equal($('loadScenario').disabled, false);
  }
  async function execute(expected) {
    await $('agentForm').onsubmit(event($('agentForm')));
    await wait(() => $('agentStatus').textContent.includes(expected), expected);
    const created = last('POST', '/api/runs');
    assert.equal(created.status, 202);
    return json('/api/runs/' + created.payload.id);
  }
  async function activeStudy(caseId) {
    const list = await json('/api/hospital/studies');
    const entry = list.find(study => study.case_id === caseId);
    assert.ok(entry, 'study exists: ' + caseId);
    return json('/api/hospital/studies/' + entry.id);
  }

  await wait(() => $('patientId').textContent && $('worklistBody').children.length === 3, 'authenticated worklist');
  await wait(() => $('runtimeLabel').textContent.includes('NPU'), 'real readiness response');
  assert.match($('viewerTitle').textContent, /MRI/);
  assert.equal($('filmstrip').children.length, 8);
  assert.match($('viewerNotice').textContent, /未執行影像推論/);
  assert.equal($('transcribeAudio').disabled, true, 'research MRI cannot upload demonstration dictation');
  checks.push('authenticated MRI opens eight slices without CT inference');
  // Save an actual DOM-state preview with server-rendered MRI pixels. This is
  // explicitly read-only HTML, not a screenshot or a substitute for visual QA.
  {
    const preview = new JSDOM(w.document.documentElement.outerHTML);
    const doc = preview.window.document;
    doc.querySelectorAll('script,link[rel="stylesheet"],meta[name="csrf-token"],dialog').forEach(n => n.remove());
    const style = doc.createElement('style');
    style.textContent = ['hospital.css','competition.css'].map(name => fs.readFileSync(path.join(__dirname,'../static',name),'utf8')).join('\n');
    doc.head.append(style);
    async function asData(url) { return 'data:image/png;base64,' + Buffer.from(await (await request(url)).arrayBuffer()).toString('base64'); }
    for (const img of Array.from(doc.querySelectorAll('img'))) img.src = await asData(img.getAttribute('src'));
    const center = Number($('sliceSlider').value);
    const url = $('filmstrip').children[center].querySelector('img').getAttribute('src');
    const img = doc.createElement('img'); img.src = await asData(url); img.alt = 'ACDC 心臟 MRI 研究切片';
    img.style.cssText = 'width:100%;height:100%;object-fit:contain'; doc.getElementById('imageCanvas').replaceWith(img);
    doc.querySelectorAll('button,input,select,textarea').forEach(n => n.disabled = true);
    doc.querySelectorAll('a').forEach(n => n.removeAttribute('href'));
    doc.getElementById('userInfo').textContent = '影像覆核 / 介面預覽';
    doc.getElementById('runtimeLabel').textContent = '唯讀 HTML / 非執行中的系統';
    doc.getElementById('demoCaption').textContent = '唯讀介面預覽｜此檔不能執行推論或儲存報告。完整操作請解壓套件並執行 start_demo_windows.cmd。';
    doc.title = 'CardioAgent 競賽展示工作站｜唯讀介面預覽';
    fs.writeFileSync(path.join(__dirname,'../docs/Competition_Workstation_Preview.html'),'<!doctype html>\n'+doc.documentElement.outerHTML);
    preview.window.close();
  }


  $('guideBtn').onclick();
  assert.equal($('guideDialog').open, true);
  $('guidePrepare').onclick();
  assert.equal($('guideDialog').open, false);
  assert.equal($('prepareTab').hidden, false);
  checks.push('guide dialog routes to preparation tab');

  const runsBefore = requests.filter(r => r.method === 'POST' && r.path === '/api/runs').length;
  await scenario('complete');
  assert.match($('viewerScope').textContent, /SYNTHETIC/);
  assert.equal($('contextAge').value, '62');
  assert.equal($('contextAcute').value, 'false');
  assert.equal($('contextHistory').value, 'true');
  assert.equal($('contextConsent').checked, true);
  assert.equal($('visionMode').value, 'demo');
  assert.equal(requests.filter(r => r.method === 'POST' && r.path === '/api/runs').length, runsBefore, 'scenario loading must not auto-run');
  assert.match($('agentStatus').textContent, /尚未執行/);
  checks.push('scenario loading fills disclosed synthetic context without running tools');

  const complete = await execute('等待人工覆核');
  assert.equal(complete.status, 'pending_review');
  assert.equal(complete.completed.length, 6);
  assert.equal(complete.vision.candidates.length, 2);
  assert.ok(complete.vision.candidates.every(c => c.confidence === null));
  assert.equal(complete.tasks.length, 0);
  assert.equal($('agentTrace').children.length, 6);
  assert.match($('candidateCount').textContent, /2 個候選/);
  await $('approveAgent').onclick();
  await wait(() => $('agentTasks').textContent.includes('本機協作任務：2'), 'approved handoff');
  const approved = await json('/api/runs/' + complete.id);
  assert.equal(approved.status, 'approved');
  assert.equal(approved.tasks.length, 2);
  assert.ok(approved.tasks.every(task => task.demo === true && task.external_connected === false));
  checks.push('six synthetic tools produce two null-confidence markers and 0/2 human-gated tasks');

  $('sampleDictation').onclick();
  assert.match($('transcriptStatus').textContent, /未執行語音辨識/);
  assert.ok($('dictationText').value.length > 0);
  await $('prepareDraft').onclick();
  const prepared = last('POST', '/api/demo/prepare').payload;
  assert.equal(prepared.schema, 'cardio-demo-preparation/1');
  assert.equal(prepared.engine.source, 'rules');
  assert.equal(prepared.run_id, complete.id);
  assert.equal(prepared.requires_human_review, true);
  assert.equal(Object.keys(prepared.fields).length, 6);
  assert.ok(prepared.sources.length > 0);
  assert.equal($('preparationSources').children.length, prepared.sources.length);
  assert.equal($('preparationChecks').children.length, prepared.checks.length);
  assert.equal((await activeStudy('demo-ct-positive')).report, null, 'prepare does not save a report');
  const link = $('preparationSources').querySelector('a');
  link.onclick(event(link));
  await wait(() => $('sourceDialog').open, 'source document modal');
  assert.ok($('sourceText').textContent.length > 20);
  assert.match($('sourceVersion').textContent, /版本/);
  assert.match($('sourceVersion').textContent + $('sourceText').textContent, /示範|展示/);
  $('sourceDialog').close();
  checks.push('rule preparation returns six fields, visible checks and real source document modal without saving');

  assert.equal($('copyPrepared').disabled, false);
  $('copyPrepared').onclick();
  assert.equal($('dirtyNotice').hidden, false);
  assert.equal($('reportTab').hidden, false);
  assert.equal($('findings').value, prepared.fields.findings);
  assert.equal((await activeStudy('demo-ct-positive')).report, null, 'copy creates unsaved edits only');
  $('findings').value = '人工確認：本案例為合成流程測試，標記不是醫療檢出結果。';
  $('impression').value = '人工填寫：僅驗證影像協作與報告版本流程。';
  $('findings').dispatchEvent(new w.Event('input'));
  await $('saveDraft').onclick();
  assert.match($('reportVersion').textContent, /草稿/);
  $('reportForm').onsubmit(event($('reportForm')));
  await wait(() => $('reportVersion').textContent.includes('待簽核'), 'preliminary report');
  $('signBtn').onclick();
  $('signConfirmed').checked = true;
  await $('signForm').onsubmit(event($('signForm')));
  assert.match($('reportVersion').textContent, /已簽核/);
  assert.equal($('findings').disabled, true);
  checks.push('copy requires explicit saving and authenticated report signing locks original text');

  await $('prepareDraft').onclick();
  const signed = await activeStudy('demo-ct-positive');
  const lockedText = $('findings').value;
  assert.equal($('copyPrepared').disabled, true);
  $('copyPrepared').onclick();
  assert.equal($('findings').value, lockedText, 'handler also blocks locked-report replacement');
  const rejectedEdit = await apiPost('/api/hospital/studies/' + signed.id + '/report', {
    version: signed.report.version, status: 'draft', fields: prepared.fields
  });
  assert.equal(rejectedEdit.status, 409, 'backend must reject locked report overwrite');
  checks.push('signed reports reject prepared-copy replacement in both UI handler and backend');

  await $('exportDemo').onclick();
  assert.equal(downloads.length, 1);
  assert.match(downloads[0].filename, /^CardioAgent_Demo_demo-ct-positive_/);
  const evidence = JSON.parse(await downloads[0].blob.text());
  assert.equal(evidence.schema, 'cardio-competition-demo/1');
  assert.equal(evidence.study.case_id, 'demo-ct-positive');
  assert.equal(evidence.study.report.status, 'final');
  assert.equal(evidence.run.id, complete.id);
  assert.equal(evidence.run.tasks.length, 2);
  assert.equal(evidence.preparation.case_id, evidence.study.case_id);
  assert.ok(evidence.history.revisions.length >= 3);
  assert.equal(evidence.health.npu_verified, false);
  assert.equal(evidence.health.hardware_verified, false);
  assert.equal(evidence.runtime.npu_verified, false);
  const fhir = await json('/api/hospital/studies/' + signed.id + '/fhir');
  assert.equal(fhir.resourceType, 'Bundle');
  assert.ok(fhir.entry.some(entry => entry.resource.resourceType === 'DiagnosticReport' && entry.resource.status === 'final'));
  checks.push('download captures current study, signed report, run, sources, history and unverified hardware flags; FHIR exports final report');

  await scenario('mri');
  assert.equal($('dictationText').value, '');
  assert.equal($('preparationDraft').textContent, '');
  assert.equal($('preparationSources').children.length, 0);
  assert.equal($('copyPrepared').disabled, true);
  assert.equal($('transcribeAudio').disabled, true);
  checks.push('study change clears prior preparation, dictation and source state');

  await scenario('missing');
  assert.equal($('contextAge').value, '');
  assert.equal($('contextHistory').value, 'false');
  const missing = await execute('等待補件');
  assert.equal(missing.status, 'pending_data');
  assert.ok(missing.missing.includes('age'));
  assert.ok(missing.missing.includes('history_complete'));
  assert.equal(missing.tasks.length, 0);
  const originalVision = JSON.stringify(missing.vision);
  const originalHashes = JSON.stringify(missing.snapshot.slice_sha256);
  $('contextAge').value = '62';
  $('contextHistory').value = 'true';
  await $('agentForm').onsubmit(event($('agentForm')));
  await wait(() => $('agentStatus').textContent.includes('等待人工覆核'), 'missing data resumed');
  const resumed = await json('/api/runs/' + missing.id);
  assert.equal(resumed.id, missing.id);
  assert.equal(resumed.status, 'pending_review');
  assert.equal(JSON.stringify(resumed.vision), originalVision);
  assert.equal(JSON.stringify(resumed.snapshot.slice_sha256), originalHashes);
  assert.equal(resumed.settings.mode, 'demo');
  assert.equal(resumed.settings.planner, 'rules');
  assert.equal(resumed.tasks.length, 0);
  assert.equal(resumed.completed.length, 6);
  checks.push('missing context pauses with no tasks and resumes same run while preserving image evidence and settings');

  await scenario('zero');
  const zero = await execute('等待人工覆核');
  assert.equal(zero.vision.candidates.length, 0);
  assert.equal(zero.tasks.length, 0);
  assert.match($('candidateTable').textContent, /零候選不表示沒有鈣化/);
  assert.equal($('agentReview').hidden, false);
  checks.push('zero-candidate scenario preserves human review and does not claim absence of disease');

  // Hold genuine HTTP responses to exercise UI races without fabricated data.
  async function holdResponse(endpoint, action, mutate) {
    let release, held = false;
    const gate = new Promise(resolve => release = resolve);
    w.fetch = async (url, options) => {
      const response = await request(url, options);
      if (new URL(url,base).pathname === endpoint) { held = true; await gate; }
      return response;
    };
    try {
      const pending = action();
      await wait(() => held, 'real HTTP response held: ' + endpoint);
      await mutate();
      release();
      await pending;
    } finally { release(); w.fetch = request; }
  }
  await holdResponse('/api/demo/prepare', () => $('prepareDraft').onclick(), async () => {
    assert.equal($('dictationText').disabled, true);
    assert.equal($('sampleDictation').disabled, true);
    $('sourceQuery').value = '人工覆核';
    $('sourceQuery').dispatchEvent(new w.Event('input'));
  });
  assert.equal($('copyPrepared').disabled, true);
  assert.match($('preparationStatus').textContent, /輸入已變更/);
  checks.push('changed input invalidates a pending real preparation response and transcript stays locked while busy');

  await holdResponse('/api/demo/prepare', () => $('prepareDraft').onclick(), async () => {
    $('newAgent').onclick();
  });
  assert.equal($('copyPrepared').disabled, true);
  assert.match($('agentStatus').textContent, /尚未執行/);
  assert.equal($('preparationDraft').textContent, '');
  checks.push('same-case workflow reset rejects late preparation instead of reusing stale run evidence');

  await scenario('zero');
  await execute('等待人工覆核');
  const downloadCount = downloads.length;
  await holdResponse('/api/health', () => $('exportDemo').onclick(), async () => {
    $('newAgent').onclick();
  });
  assert.equal(downloads.length, downloadCount);
  assert.match($('toast').textContent, /工作已變更/);
  checks.push('same-case reset during real evidence collection cancels mixed-snapshot download');

  await scenario('acute');
  const acute = await execute('急性警訊優先');
  assert.equal(acute.status, 'emergency');
  assert.equal(acute.tool_log.length, 0);
  assert.equal(acute.completed.length, 0);
  assert.equal(acute.tasks.length, 0);
  assert.equal($('agentTrace').children.length, 0);
  assert.equal($('agentReview').hidden, true);
  checks.push('acute flag stops normal tools immediately and creates no handoff');

  await $('settingsBtn').onclick();
  assert.equal($('readinessDialog').open, true);
  assert.match($('readinessRows').textContent, /NPU.*待實機驗證/);
  assert.match($('runtimeLabel').textContent, /NPU：待實機驗證/);
  const readiness = last('GET', '/api/demo/status').payload;
  assert.equal(readiness.npu_verified, false);
  assert.equal(readiness.hardware_verified, false);
  assert.equal(readiness.capabilities.synthetic_workflow, true);
  assert.equal(typeof readiness.service.available, 'boolean');
  assert.ok(readiness.audit.valid);
  checks.push('readiness reports actual local service state and keeps AMD/NPU verification false');

  assert.deepEqual(errors, [], 'no jsdom script or navigation errors');
  const result = {
    passed: true, checks, checks_count: checks.length, jsdom: require('jsdom/package.json').version,
    real_http: true, authenticated_session: true, fetch_responses_mocked: false,
    canvas: 'stubbed', image_loading: 'stubbed', download: 'captured real generated Blob',
    browser_visual_qa: false, amd_hardware_verified: false, npu_execution_verified: false,
    http_requests: requests.length, completed_at: new Date().toISOString(), errors
  };
  fs.mkdirSync(path.join(__dirname, '../evidence'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, '../evidence/competition_dom.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  dom.window.close();
})().catch(error => { activeDom?.window.close(); process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
