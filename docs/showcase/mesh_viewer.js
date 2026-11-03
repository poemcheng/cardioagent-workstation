/* Local mesh preview only. Rendering does not prove GPU/NPU inference. */
(function (root) {
  'use strict';
  const MAX_FACES = 200000, MAX_VERTICES = 600000, CPU_FACE_LIMIT = 8000;
  const VERTEX = 'attribute vec3 aPosition; attribute vec3 aNormal; uniform mat3 uRotation; uniform vec2 uScale; varying vec3 vNormal; void main(){vec3 p=uRotation*aPosition; gl_Position=vec4(p.x*uScale.x,p.y*uScale.y,p.z*0.5,1.0); vNormal=uRotation*aNormal;}';
  const FRAGMENT = 'precision mediump float; varying vec3 vNormal; void main(){vec3 n=normalize(vNormal); if(n.z>0.0)n=-n; vec3 l=normalize(vec3(-0.4,0.65,-1.0)); float key=max(dot(n,l),0.0); float fill=max(dot(n,normalize(vec3(0.7,-0.25,-0.3))),0.0); float spec=pow(max(dot(reflect(-l,n),vec3(0.0,0.0,-1.0)),0.0),28.0); gl_FragColor=vec4(vec3(0.72,0.49,0.45)*(0.34+0.52*key+0.18*fill)+vec3(0.13*spec),1.0);}';
  function finite(value) { return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1e9; }
  function validate(mesh) {
    if (!mesh || !Array.isArray(mesh.vertices) || !Array.isArray(mesh.faces)) throw Error('3D 網格必須包含 vertices 與 faces 陣列。');
    if (mesh.vertices.length > MAX_VERTICES || mesh.faces.length > MAX_FACES) throw Error('3D 網格超過預覽上限（600,000 頂點／200,000 三角面）。');
    for (const vertex of mesh.vertices) if (!Array.isArray(vertex) || vertex.length !== 3 || !vertex.every(finite)) throw Error('3D 頂點須為有限的三維座標，範圍不得超過 ±1e9。');
    for (const face of mesh.faces) {
      if (!Array.isArray(face) || face.length !== 3 || !face.every(i => Number.isInteger(i) && i >= 0 && i < mesh.vertices.length) || new Set(face).size !== 3) throw Error('3D 三角面索引無效。');
    }
    if (mesh.faces.length && !mesh.vertices.length) throw Error('3D 三角面缺少頂點。');
  }
  function rotation(pitch, yaw) {
    const cx = Math.cos(pitch), sx = Math.sin(pitch), cy = Math.cos(yaw), sy = Math.sin(yaw);
    // Column-major Rx * Ry. CPU and WebGL use the same world-to-view rotation.
    return new Float32Array([cy, sx * sy, -cx * sy, 0, cx, sx, sy, -sx * cy, cx * cy]);
  }

  class CardioMeshViewer {
    constructor(canvas, options = {}) {
      if (!canvas || typeof canvas.getContext !== 'function') throw Error('請提供 3D 預覽 Canvas。');
      this.canvas = canvas;
      this.win = canvas.ownerDocument?.defaultView || root;
      this.onStatus = typeof options.onStatus === 'function' ? options.onStatus : function () {};
      this.destroyed = false; this.points = new Float32Array(); this.faces = new Uint32Array();
      this.cpuFaces = new Uint32Array(); this.units = ''; this.pitch = -1.12; this.yaw = .42; this.zoom = 1;
      this.width = 1; this.height = 1; this.pointer = null; this.frame = null; this.buffer = null;
      this.gl = null; this.ctx = null; this.program = null; this.contextLost = false;
      this.handlers = []; this.originalTouchAction = canvas.style?.touchAction;
      if (canvas.style) canvas.style.touchAction = 'none';
      let gl = null;
      try { gl = canvas.getContext('webgl', {alpha: false, antialias: true, powerPreference: 'low-power'}); } catch (_) {}
      if (gl && typeof gl.createShader === 'function') {
        this.gl = gl;
        try { this._initGL(); } catch (_) { this._fallbackCanvas(); }
      } else {
        this.ctx = canvas.getContext('2d');
      }
      if (!this.gl && !this.ctx) throw Error('此瀏覽器無法建立 WebGL 或 Canvas 2D 預覽；仍可下載 OBJ／STL。');
      this._listen(canvas, 'pointerdown', e => {
        if (this.destroyed || (e.button !== undefined && e.button !== 0)) return;
        this.pointer = {id: e.pointerId, x: e.clientX, y: e.clientY};
        try { canvas.setPointerCapture?.(e.pointerId); } catch (_) {}
      });
      this._listen(canvas, 'pointermove', e => {
        if (!this.pointer || this.pointer.id !== e.pointerId || this.destroyed) return;
        this.yaw = (this.yaw + (e.clientX - this.pointer.x) * .012) % (Math.PI * 2);
        this.pitch = (this.pitch + (e.clientY - this.pointer.y) * .012) % (Math.PI * 2);
        this.pointer.x = e.clientX; this.pointer.y = e.clientY; this._schedule();
      });
      const end = e => { if (this.pointer && (e.pointerId === undefined || e.pointerId === this.pointer.id)) { this.pointer = null; try { canvas.releasePointerCapture?.(e.pointerId); } catch (_) {} } };
      this._listen(canvas, 'pointerup', end); this._listen(canvas, 'pointercancel', end);
      this._listen(canvas, 'wheel', e => {
        if (this.destroyed || !Number.isFinite(e.deltaY)) return;
        e.preventDefault(); this.zoom = Math.max(.25, Math.min(4, this.zoom * Math.exp(-e.deltaY * .001))); this._schedule();
      }, {passive: false});
      this._listen(canvas, 'webglcontextlost', e => { e.preventDefault(); this.contextLost = true; this.onStatus('WebGL 繪圖內容已失效；可重新載入預覽或下載 OBJ／STL。'); });
      this._listen(canvas, 'webglcontextrestored', () => {
        if (!this.gl || this.destroyed) return;
        try { this.contextLost = false; this._initGL(); this._upload(); this._draw(); this._status(); }
        catch (_) { this.contextLost = true; this.onStatus('WebGL 內容無法恢復；可重新載入預覽或下載 OBJ／STL。'); }
      });
      if (this.win.addEventListener) this._listen(this.win, 'resize', () => this.resize());
      this.resize(); this._status();
    }
    _listen(target, name, fn, options) { target.addEventListener(name, fn, options); this.handlers.push([target, name, fn, options]); }
    _initGL() {
      const gl = this.gl, shaders = [];
      let program = null;
      try {
        for (const [type, source] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]]) {
          const shader = gl.createShader(type); shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader);
          if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw Error('Shader unavailable');
        }
        program = gl.createProgram(); shaders.forEach(s => gl.attachShader(program, s)); gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error('Program unavailable');
        this.program = program; this.positionLocation = gl.getAttribLocation(program, 'aPosition');
        this.normalLocation = gl.getAttribLocation(program, 'aNormal'); this.rotationLocation = gl.getUniformLocation(program, 'uRotation'); this.scaleLocation = gl.getUniformLocation(program, 'uScale');
        gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE);
      } catch (error) { if (program) gl.deleteProgram(program); throw error; }
      finally { shaders.forEach(shader => gl.deleteShader(shader)); }
    }
    _fallbackCanvas() {
      // A canvas with an allocated WebGL context cannot subsequently become 2D.
      // Use a local child canvas only if initialization failed after allocation.
      if (this.buffer) this.gl?.deleteBuffer(this.buffer);
      if (this.program) this.gl?.deleteProgram(this.program);
      this.gl = null; this.program = null; this.buffer = null;
      const doc = this.canvas.ownerDocument;
      if (doc?.createElement && this.canvas.parentElement) {
        const fallback = doc.createElement('canvas');
        fallback.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
        fallback.setAttribute('aria-hidden', 'true');
        const parent = this.canvas.parentElement;
        this.originalParentPosition = parent.style.position;
        if (!this.originalParentPosition) parent.style.position = 'relative';
        parent.append(fallback); this.fallback = fallback; this.ctx = fallback.getContext('2d');
      } else { try { this.ctx = this.canvas.getContext('2d'); } catch (_) {} }
    }
    setMesh(mesh, options = {}) {
      if (this.destroyed) throw Error('3D 預覽已關閉。');
      validate(mesh);
      const displayScale = options.displayScale || [1, 1, 1];
      if (!Array.isArray(displayScale) || displayScale.length !== 3 || !displayScale.every(v => finite(v) && v >= .001 && v <= 1000)) throw Error('顯示比例必須為三個有限正數。');
      const displayVertices = mesh.vertices.map(v => v.map((x,k) => x * displayScale[k]));
      this.displayScale = displayScale.slice();
      const n = mesh.vertices.length, low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
      for (const v of displayVertices) for (let k = 0; k < 3; k++) { low[k] = Math.min(low[k], v[k]); high[k] = Math.max(high[k], v[k]); }
      const center = n ? low.map((v, k) => v + (high[k] - v) / 2) : [0, 0, 0];
      let radius = 0;
      for (const v of displayVertices) radius = Math.max(radius, Math.hypot(v[0] - center[0], v[1] - center[1], v[2] - center[2]));
      if (mesh.faces.length && radius === 0) throw Error('3D 網格沒有可顯示的空間範圍。');
      const points = new Float32Array(n * 3), faces = new Uint32Array(mesh.faces.length * 3);
      for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) points[i * 3 + k] = (displayVertices[i][k] - center[k]) / (radius || 1);
      mesh.faces.forEach((f, i) => faces.set(f, i * 3));
      const step = Math.max(1, Math.ceil(mesh.faces.length / CPU_FACE_LIMIT));
      const sampled = [];
      for (let i = 0; i < mesh.faces.length; i += step) sampled.push(...mesh.faces[i]);
      this.points = points; this.faces = faces; this.cpuFaces = Uint32Array.from(sampled);
      this.units = typeof mesh.units === 'string' ? mesh.units : '未提供';
      if (this.gl && !this.contextLost) this._upload();
      this.reset(); this._status();
      return {vertices: n, faces: mesh.faces.length, preview_faces: this.gl ? mesh.faces.length : this.cpuFaces.length / 3, renderer: this.gl ? 'webgl' : 'cpu_wireframe', units: this.units, display_scale: this.displayScale.slice()};
    }
    _upload() {
      const gl = this.gl;
      if (!gl || this.contextLost) return;
      const data = new Float32Array(this.faces.length * 6), p = this.points;
      const normals = new Float64Array(p.length);
      // Area-weighted shared-vertex normals retain the original surface shape.
      for (let i = 0; i < this.faces.length; i += 3) {
        const a=this.faces[i]*3,b=this.faces[i+1]*3,c=this.faces[i+2]*3;
        const ux=p[b]-p[a],uy=p[b+1]-p[a+1],uz=p[b+2]-p[a+2];
        const vx=p[c]-p[a],vy=p[c+1]-p[a+1],vz=p[c+2]-p[a+2];
        const nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx;
        for (const k of [a,b,c]) {normals[k]+=nx;normals[k+1]+=ny;normals[k+2]+=nz;}
      }
      for (let i=0;i<normals.length;i+=3) {
        const length=Math.hypot(normals[i],normals[i+1],normals[i+2]);
        if(length>1e-12){normals[i]/=length;normals[i+1]/=length;normals[i+2]/=length;}
        else {normals[i]=0;normals[i+1]=0;normals[i+2]=1;}
      }
      for(let i=0;i<this.faces.length;i++) {
        const k=this.faces[i]*3;data.set([p[k],p[k+1],p[k+2],normals[k],normals[k+1],normals[k+2]],i*6);
      }
      if (this.buffer) gl.deleteBuffer(this.buffer);
      this.buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    }
    _status() {
      if (!this.faces.length) { this.onStatus('尚無可顯示的 3D 表面。'); return; }
      if (this.contextLost) return;
      this.onStatus(this.gl ? 'WebGL 表面預覽（繪圖 API，非 GPU／NPU 推論證明）' : `CPU 線框預覽（WebGL 不可用；顯示 ${this.cpuFaces.length / 3}／${this.faces.length / 3} 個三角面）`);
    }
    resize() {
      if (this.destroyed) return;
      const rect = this.canvas.getBoundingClientRect();
      this.width = Math.max(1, Math.min(2400, Math.round(rect.width || 640)));
      this.height = Math.max(1, Math.min(1800, Math.round(rect.height || 420)));
      const dpr = Math.max(1, Math.min(2, Number(this.win.devicePixelRatio) || 1)); this.dpr = dpr;
      for (const canvas of [this.canvas, this.fallback].filter(Boolean)) { canvas.width = Math.round(this.width * dpr); canvas.height = Math.round(this.height * dpr); }
      if (this.gl) this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      this._draw();
    }
    reset() { if (this.destroyed) return; this.pitch = -1.12; this.yaw = .42; this.zoom = 1; this._draw(); }
    _schedule() {
      if (this.frame !== null || this.destroyed) return;
      const draw = () => { this.frame = null; if (!this.destroyed) this._draw(); };
      this.frame = this.win.requestAnimationFrame ? this.win.requestAnimationFrame(draw) : this.win.setTimeout(draw, 16);
    }
    _draw() {
      if (this.destroyed || this.contextLost) return;
      const r = rotation(this.pitch, this.yaw), scale = .85 * this.zoom;
      if (this.gl) {
        const gl = this.gl; gl.clearColor(.025, .035, .045, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        if (!this.faces.length || !this.buffer) return;
        gl.useProgram(this.program); gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
        gl.enableVertexAttribArray(this.positionLocation); gl.vertexAttribPointer(this.positionLocation, 3, gl.FLOAT, false, 24, 0);
        gl.enableVertexAttribArray(this.normalLocation); gl.vertexAttribPointer(this.normalLocation, 3, gl.FLOAT, false, 24, 12);
        gl.uniformMatrix3fv(this.rotationLocation, false, r);
        gl.uniform2f(this.scaleLocation, scale * Math.min(1, this.height / this.width), scale * Math.min(1, this.width / this.height));
        gl.drawArrays(gl.TRIANGLES, 0, this.faces.length); return;
      }
      const ctx = this.ctx; if (!ctx) return;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); ctx.fillStyle = '#080b0e'; ctx.fillRect(0, 0, this.width, this.height);
      if (!this.cpuFaces.length) return;
      const factor = Math.min(this.width, this.height) * scale / 2, p = this.points;
      ctx.strokeStyle = '#9aaebd'; ctx.globalAlpha = .55; ctx.lineWidth = .6; ctx.beginPath();
      for (let i = 0; i < this.cpuFaces.length; i += 3) {
        for (let j = 0; j < 4; j++) {
          const k = this.cpuFaces[i + j % 3] * 3;
          const x = p[k], y = p[k + 1], z = p[k + 2];
          const px = this.width / 2 + (r[0] * x + r[3] * y + r[6] * z) * factor;
          const py = this.height / 2 - (r[1] * x + r[4] * y + r[7] * z) * factor;
          if (j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
      }
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      if (this.frame !== null) { if (this.win.cancelAnimationFrame) this.win.cancelAnimationFrame(this.frame); else this.win.clearTimeout(this.frame); }
      this.handlers.forEach(([target, name, fn, options]) => target.removeEventListener(name, fn, options)); this.handlers = [];
      if (this.buffer) this.gl?.deleteBuffer(this.buffer); if (this.program) this.gl?.deleteProgram(this.program);
      this.pointer = null; this.points = new Float32Array(); this.faces = new Uint32Array(); this.cpuFaces = new Uint32Array();
      if (this.canvas.style) this.canvas.style.touchAction = this.originalTouchAction || '';
      if (this.fallback) { const parent = this.fallback.parentElement; this.fallback.remove(); if (parent) parent.style.position = this.originalParentPosition; }
    }
  }
  root.CardioMeshViewer = CardioMeshViewer;
})(typeof window === 'undefined' ? globalThis : window);
