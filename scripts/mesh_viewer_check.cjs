// Numerical/input/lifecycle checks using recording canvas doubles. Not visual QA.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/mesh_viewer.js'), 'utf8');
const sandbox = {setTimeout, clearTimeout}; vm.createContext(sandbox); vm.runInContext(source, sandbox);
const Viewer = sandbox.CardioMeshViewer;

function surface(gl = null) {
  const listeners = new Map(), windowListeners = new Map(), lines = [], uploads = [], calls = [];
  let size = {width: 500, height: 500};
  const ctx = {setTransform() {}, fillRect() {}, beginPath() {lines.length = 0;}, moveTo(x,y) {lines.push([x,y]);}, lineTo(x,y) {lines.push([x,y]);}, stroke() {}};
  const win = {devicePixelRatio: 1, setTimeout, clearTimeout, addEventListener(name,fn) {windowListeners.set(name,fn);}, removeEventListener(name) {windowListeners.delete(name);}};
  const canvas = {style: {}, ownerDocument: {defaultView: win}, getContext(type) {return type === 'webgl' ? gl : ctx;}, getBoundingClientRect() {return size;}, addEventListener(name,fn) {listeners.set(name,fn);}, removeEventListener(name) {listeners.delete(name);}, setPointerCapture() {}, releasePointerCapture() {}};
  return {canvas, listeners, windowListeners, lines, uploads, calls, size(value) {size = value;}};
}
const tetra = {units: 'mm', vertices: [[0,0,0],[2,0,0],[0,2,0],[0,0,2]], faces: [[0,2,1],[0,1,3],[0,3,2],[1,2,3]]};
const checks = [];

const scene = surface(), status = [], viewer = new Viewer(scene.canvas,{onStatus: value => status.push(value)});
const original = JSON.stringify(tetra);
const meta = viewer.setMesh(tetra);
assert.equal(meta.renderer, 'cpu_wireframe'); assert.equal(meta.faces, 4); assert.equal(meta.units, 'mm');
assert.match(status.at(-1), /CPU.*WebGL/); assert.equal(JSON.stringify(tetra), original);
assert.equal(scene.lines.length, 16); assert.ok(scene.lines.flat().every(Number.isFinite));
checks.push('valid input draws bounded finite CPU projection and preserves source coordinates/units');

const scaled = viewer.setMesh(tetra,{displayScale:[1,1,14]});
assert.equal(scaled.display_scale[2],14);assert.equal(JSON.stringify(tetra),original);
for(const scale of [[0,1,1],[1,1,NaN],[1,1,1001],[1,1]])assert.throws(()=>viewer.setMesh(tetra,{displayScale:scale}));
viewer.setMesh(tetra);
checks.push('display-only sampling ratios preserve source vertices and reject invalid scale');

const edgeLength = () => Math.hypot(scene.lines[1][0]-scene.lines[0][0],scene.lines[1][1]-scene.lines[0][1]);
const before = edgeLength();
scene.size({width:1000,height:500}); viewer.resize();
assert.ok(Math.abs(edgeLength()-before)<1e-5,'a wider viewport must not stretch geometry');
scene.size({width:500,height:1000}); viewer.resize();
assert.ok(Math.abs(edgeLength()-before)<1e-5,'a taller viewport must not stretch geometry');
checks.push('orthographic geometry retains scale/aspect across wide and tall viewports');

for (const malformed of [null,{vertices:[[NaN,0,0]],faces:[]},{vertices:[[Infinity,0,0]],faces:[]},{vertices:[[0,0,0]],faces:[[0,0,1]]},{vertices:[[0,0,0]],faces:[[0,1,2]]},{vertices:[[0,0,0]],faces:Array(200001).fill([0,1,2])}]) {
  assert.throws(()=>viewer.setMesh(malformed));
}
assert.equal(JSON.stringify(tetra), original); viewer.reset(); assert.ok(scene.lines.flat().every(Number.isFinite));
checks.push('nonfinite coordinates, invalid indices, duplicate indices and oversized meshes fail before rendering');

const big = {vertices: tetra.vertices, faces:Array(8001).fill([0,1,2]),units:'voxel'};
const limited = viewer.setMesh(big); assert.ok(limited.preview_faces <= 8000); assert.match(status.at(-1), /4001.*8001/);
assert.ok(scene.lines.length <= 32000); checks.push('CPU fallback caps sampled triangles and exposes sampling instead of claiming full surface rendering');

let prevented = 0;
scene.listeners.get('wheel')({deltaY:1e9,preventDefault(){prevented++;}});
assert.equal(viewer.zoom,.25); scene.listeners.get('wheel')({deltaY:-1e9,preventDefault(){prevented++;}}); assert.equal(viewer.zoom,4);
assert.equal(prevented,2);
scene.listeners.get('pointerdown')({pointerId:1,clientX:0,clientY:0,button:0});
scene.listeners.get('pointermove')({pointerId:1,clientX:50,clientY:20});
scene.listeners.get('pointerup')({pointerId:1}); assert.equal(viewer.pointer,null);
viewer.destroy(); assert.equal(scene.listeners.size,0); assert.equal(scene.windowListeners.size,0); assert.equal(scene.canvas.style.touchAction,'');
assert.throws(()=>viewer.setMesh(tetra)); viewer.destroy();
checks.push('interaction zoom is bounded; destroy removes handlers, pending draws and render data');

function glDouble() {
  const gl = {uploads:[],draws:[],deletes:0,VERTEX_SHADER:1,FRAGMENT_SHADER:2,COMPILE_STATUS:3,LINK_STATUS:4,DEPTH_TEST:5,LEQUAL:6,CULL_FACE:7,ARRAY_BUFFER:8,STATIC_DRAW:9,COLOR_BUFFER_BIT:16,DEPTH_BUFFER_BIT:32,FLOAT:33,TRIANGLES:34};
  for(const name of ['shaderSource','compileShader','attachShader','linkProgram','deleteShader','enable','depthFunc','disable','bindBuffer','viewport','clearColor','clear','useProgram','enableVertexAttribArray','vertexAttribPointer','uniformMatrix3fv','uniform2f']) gl[name]=()=>{};
  gl.createShader=()=>({});gl.getShaderParameter=()=>true;gl.createProgram=()=>({});gl.getProgramParameter=()=>true;gl.getAttribLocation=()=>1;gl.getUniformLocation=()=>({});gl.createBuffer=()=>({});
  gl.deleteBuffer=()=>gl.deletes++;gl.deleteProgram=()=>gl.deletes++;
  gl.bufferData=(_,data)=>gl.uploads.push(Array.from(data));gl.drawArrays=(_,first,count)=>gl.draws.push(count);
  return gl;
}
const gl = glDouble(), gscene=surface(gl), gstatus=[], gpu = new Viewer(gscene.canvas,{onStatus: s=>gstatus.push(s)});
assert.equal(gpu.setMesh(tetra).renderer,'webgl');assert.equal(gl.draws.at(-1),12);
const uploaded=gl.uploads.at(-1);assert.ok(uploaded.every(Number.isFinite));
for(let i=0;i<uploaded.length;i+=6)assert.ok(Math.abs(Math.hypot(uploaded[i+3],uploaded[i+4],uploaded[i+5])-1)<1e-5);
const sameVertexNormals=[];
for(let i=0;i<uploaded.length;i+=6){if(Math.abs(uploaded[i]-uploaded[0])<1e-6&&Math.abs(uploaded[i+1]-uploaded[1])<1e-6&&Math.abs(uploaded[i+2]-uploaded[2])<1e-6)sameVertexNormals.push(uploaded.slice(i+3,i+6));}
assert.ok(sameVertexNormals.length>1);for(const n of sameVertexNormals)assert.deepEqual(n,sameVertexNormals[0]);
assert.match(gstatus.at(-1), /非 GPU／NPU/);gpu.setMesh({vertices:[],faces:[],units:'voxel'});assert.match(gstatus.at(-1),/尚無/);
gpu.destroy();assert.ok(gl.deletes>=2);checks.push('WebGL upload has finite geometry and unit shared vertex normals; renderer status never claims hardware inference');

process.stdout.write(JSON.stringify({passed:true,checks,checks_count:checks.length,visual_qa:false,contexts:'recording doubles'},null,2)+'\n');
