// Live background: particle globe, rings with travelling arcs, perspective floor grid, dust.
// Plain WebGL 1, no libraries. Driven by real levels and phases from the house.

const GOLD = [0.949, 0.761, 0.106];

const GLOBE_VS = `
attribute vec3 aPos;
attribute float aSeed;
uniform vec2 uRes;
uniform vec2 uCenter;
uniform float uRadius;
uniform float uRot;
uniform float uTilt;
uniform float uDpr;
uniform float uEnergy;
uniform float uTime;
uniform float uPoint;
varying float vFront;
varying float vSeed;
void main() {
  float c = cos(uRot), s = sin(uRot);
  vec3 p = vec3(c * aPos.x + s * aPos.z, aPos.y, -s * aPos.x + c * aPos.z);
  float ct = cos(uTilt), st = sin(uTilt);
  p = vec3(p.x, ct * p.y - st * p.z, st * p.y + ct * p.z);
  float breathe = 1.0 + uEnergy * 0.035 * sin(uTime * 9.0 + aSeed * 6.28);
  float persp = 1.0 / (1.0 - p.z * 0.28);
  vec2 px = uCenter + vec2(p.x, -p.y) * uRadius * persp * breathe;
  vec2 ndc = px / uRes * 2.0 - 1.0;
  gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
  vFront = (p.z + 1.0) * 0.5;
  vSeed = aSeed;
  float scale = clamp(uRadius / 280.0, 0.7, 2.2);
  gl_PointSize = (0.85 + 1.7 * vFront) * uDpr * scale * uPoint * (1.0 + uEnergy * 0.45);
}`;

const GLOBE_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec3 uColor;
uniform float uAlpha;
uniform float uTime;
uniform float uEnergy;
varying float vFront;
varying float vSeed;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  float r = dot(d, d);
  if (r > 0.25) discard;
  float soft = smoothstep(0.25, 0.0, r);
  float core = smoothstep(0.07, 0.0, r);
  float glint = 0.72 + 0.28 * sin(uTime * (1.8 + vSeed * 4.0) + vSeed * 40.0);
  float a = mix(0.16, 1.0, vFront * vFront) * (soft * 0.65 + core * 0.9) * glint * uAlpha * (0.85 + uEnergy * 0.4);
  gl_FragColor = vec4(uColor * (0.75 + 0.7 * vFront), a);
}`;

const RING_VS = `
attribute float aT;
attribute float aSide;
uniform vec2 uRes;
uniform vec2 uCenter;
uniform float uRadius;
uniform float uWidth;
uniform float uStart;
uniform float uLen;
varying float vT;
void main() {
  float ang = uStart + aT * uLen;
  float r = uRadius + aSide * uWidth * 0.5;
  vec2 px = uCenter + vec2(cos(ang), sin(ang)) * r;
  vec2 ndc = px / uRes * 2.0 - 1.0;
  gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
  vT = aT;
}`;

const RING_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec4 uColor;
uniform float uFade;
varying float vT;
void main() {
  float edge = mix(1.0, smoothstep(0.0, 0.12, vT) * smoothstep(1.0, 0.88, vT), uFade);
  gl_FragColor = vec4(uColor.rgb, uColor.a * edge);
}`;

const TICK_VS = `
attribute float aAngle;
attribute float aR;
uniform vec2 uRes;
uniform vec2 uCenter;
uniform float uRadius;
uniform float uLen;
uniform float uRot;
void main() {
  float ang = aAngle + uRot;
  vec2 px = uCenter + vec2(cos(ang), sin(ang)) * (uRadius + aR * uLen);
  vec2 ndc = px / uRes * 2.0 - 1.0;
  gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
}`;

const FLAT_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec4 uColor;
void main() { gl_FragColor = uColor; }`;

const GRID_VS = `
attribute vec3 aPos;
uniform mat4 uMVP;
uniform float uScroll;
varying float vDepth;
void main() {
  vec3 p = aPos;
  p.z += aPos.y * uScroll;
  vDepth = -p.z;
  gl_Position = uMVP * vec4(p.x, 0.0, p.z, 1.0);
}`;

const GRID_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec4 uColor;
uniform float uFar;
varying float vDepth;
void main() {
  float fog = 1.0 - smoothstep(uFar * 0.15, uFar, vDepth);
  float near = smoothstep(0.0, 2.5, vDepth);
  gl_FragColor = vec4(uColor.rgb, uColor.a * fog * near);
}`;

const DUST_VS = `
attribute vec3 aPos;
uniform vec2 uRes;
uniform float uTime;
uniform float uDpr;
varying float vTw;
void main() {
  vec2 p = aPos.xy + vec2(sin(uTime * 0.11 + aPos.z * 20.0), cos(uTime * 0.09 + aPos.z * 13.0)) * 0.045;
  gl_Position = vec4(p, 0.0, 1.0);
  vTw = 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * (0.8 + aPos.z * 2.2) + aPos.z * 50.0));
  gl_PointSize = (1.1 + aPos.z * 2.4) * uDpr;
}`;

const DUST_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec3 uColor;
varying float vTw;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  if (dot(d, d) > 0.25) discard;
  float soft = smoothstep(0.25, 0.02, dot(d, d));
  gl_FragColor = vec4(uColor, 0.55 * vTw * soft);
}`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
  return sh;
}

function program(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const uniforms = {};
  const count = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < count; i++) {
    const info = gl.getActiveUniform(p, i);
    uniforms[info.name] = gl.getUniformLocation(p, info.name);
  }
  const attribs = {};
  const acount = gl.getProgramParameter(p, gl.ACTIVE_ATTRIBUTES);
  for (let i = 0; i < acount; i++) {
    const info = gl.getActiveAttrib(p, i);
    attribs[info.name] = gl.getAttribLocation(p, info.name);
  }
  return { p, u: uniforms, a: attribs };
}

function buffer(gl, data) {
  const b = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  return b;
}

function globePoints(fine) {
  const pts = [];
  const push = (x, y, z) => pts.push(x, y, z, Math.random());
  const latStep = fine ? 3 : 5;
  const latDensity = fine ? 260 : 150;
  for (let lat = -88; lat <= 88; lat += latStep) {
    const r = Math.cos((lat * Math.PI) / 180);
    const y = Math.sin((lat * Math.PI) / 180);
    const n = Math.max(28, Math.round(latDensity * r));
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      push(Math.cos(a) * r, y, Math.sin(a) * r);
    }
  }
  const meridians = fine ? 56 : 32;
  const steps = fine ? 160 : 96;
  for (let m = 0; m < meridians; m++) {
    const a = (m / meridians) * Math.PI * 2;
    for (let k = 0; k <= steps; k++) {
      const lat = -Math.PI / 2 + (k / steps) * Math.PI;
      const r = Math.cos(lat);
      push(Math.cos(a) * r, Math.sin(lat), Math.sin(a) * r);
    }
  }
  const fill = fine ? 4200 : 1800;
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < fill; i++) {
    const y = 1 - (i / (fill - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const t = golden * i;
    push(Math.cos(t) * r, y, Math.sin(t) * r);
  }
  return new Float32Array(pts);
}

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}

function multiply(a, b) {
  const o = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return o;
}

function view(pitch, height) {
  const c = Math.cos(pitch), s = Math.sin(pitch);
  const rot = [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1];
  const trans = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -height, 0, 1];
  return multiply(rot, trans);
}

// Global tempo of the background motion; globe placement and level response stay unscaled.
const TEMPO = 0.55;

const RESTING = { listening: 0.62, collecting: 0.62, followup: 0.4, thinking: 0.48, speaking: 0.9 };

const gold = (a) => `rgba(242, 194, 27, ${a})`;

export class Scene {
  constructor(canvas) {
    this.canvas = canvas;
    this.globe = { x: 0, y: 0, r: 0, alpha: 0 };
    this.target = { x: 0, y: 0, r: 0, alpha: 0 };
    this.energy = 0;
    this.energyTarget = 0;
    this.phase = "idle";
    this.time = 0;
    this.rot = 0;
    this.pulse = 0;
    this.last = 0;
    this.frame = 0;
    this.mode = null;
    this.gl = null;
    this.software = false;
    this.lite = false;
    this.dprCap = 3;
    this.avg = 1 / 60;
    this.frames = 0;
    if (!this.initGl()) this.init2d();
    if (!this.mode) return;
    this.resize();
    window.addEventListener("resize", () => {
      this.resize();
      this.kick();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.stop();
      else this.kick();
    });
    this.kick();
  }

  initGl() {
    const gl = this.canvas.getContext("webgl", { antialias: true, alpha: false, powerPreference: "high-performance" });
    if (!gl) return false;
    this.gl = gl;
    try {
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      const renderer = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "";
      this.software = /swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer);
      if (this.software) {
        this.dprCap = 1;
        this.lite = true;
      }
      this.setup();
    } catch (err) {
      console.error("scene", err);
      this.gl = null;
      // A canvas that already handed out a WebGL context cannot give a 2D one.
      const fresh = this.canvas.cloneNode(false);
      this.canvas.replaceWith(fresh);
      this.canvas = fresh;
      return false;
    }
    this.mode = "gl";
    return true;
  }

  init2d() {
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return;
    this.ctx = ctx;
    this.dprCap = 1.5;
    const all = globePoints(false);
    const pts = [];
    for (let i = 0; i < all.length; i += 12) pts.push(all[i], all[i + 1], all[i + 2]);
    this.pts2d = new Float32Array(pts);
    this.dust2d = Array.from({ length: 160 }, () => [Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random()]);
    this.initRings();
    this.mode = "2d";
  }

  initRings() {
    this.arcs = [
      { r: 1.08, w: 3.2, start: 0.4, len: 1.15, speed: 0.22, a: 0.95 },
      { r: 1.08, w: 3.2, start: 3.5, len: 0.55, speed: 0.22, a: 0.8 },
      { r: 1.28, w: 2.8, start: 0.15, len: 0.9, speed: 0.16, a: 1 },
      { r: 1.28, w: 2.8, start: 2.4, len: 0.45, speed: 0.16, a: 0.85 },
      { r: 1.46, w: 2.4, start: 1.05, len: 1.35, speed: -0.11, a: 0.8 },
      { r: 1.46, w: 2.4, start: 4.2, len: 0.7, speed: -0.11, a: 0.7 },
      { r: 1.68, w: 1.8, start: 2.2, len: 0.5, speed: 0.07, a: 0.65 },
      { r: 1.68, w: 1.8, start: 5.1, len: 0.32, speed: 0.07, a: 0.5 },
      { r: 1.92, w: 1.5, start: 0.8, len: 0.85, speed: -0.05, a: 0.45 },
      { r: 2.12, w: 1.2, start: 3.3, len: 0.4, speed: 0.09, a: 0.4 },
      { r: 2.2, w: 2.2, start: 0, len: 1.7, speed: 0.38, a: 0.55 },
    ];
    this.baseRings = [
      { r: 1.02, a: 0.28, w: 1.6 },
      { r: 1.08, a: 0.14, w: 1.1 },
      { r: 1.28, a: 0.22, w: 1.4 },
      { r: 1.46, a: 0.16, w: 1.2 },
      { r: 1.68, a: 0.12, w: 1.0 },
      { r: 1.92, a: 0.1, w: 0.9 },
      { r: 2.12, a: 0.07, w: 0.8 },
    ];
  }

  setup() {
    const gl = this.gl;
    const pixels = window.innerWidth * window.innerHeight * (window.devicePixelRatio || 1);
    this.fine = !this.software && pixels > 900000;
    this.progGlobe = program(gl, GLOBE_VS, GLOBE_FS);
    this.progRing = program(gl, RING_VS, RING_FS);
    this.progTick = program(gl, TICK_VS, FLAT_FS);
    this.progGrid = program(gl, GRID_VS, GRID_FS);
    this.progDust = program(gl, DUST_VS, DUST_FS);

    const g = globePoints(this.fine);
    this.globeCount = g.length / 4;
    this.globeBuf = buffer(gl, g);

    const seg = 720;
    const ring = [];
    for (let i = 0; i <= seg; i++) ring.push(i / seg, -1, i / seg, 1);
    this.ringCount = (seg + 1) * 2;
    this.ringBuf = buffer(gl, new Float32Array(ring));

    const ticks = [];
    const n = 240;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const long = i % 10 === 0 ? 1 : 0.45;
      ticks.push(a, 0, a, long);
    }
    this.tickCount = n * 2;
    this.tickBuf = buffer(gl, new Float32Array(ticks));

    const grid = [];
    const far = 90;
    for (let x = -48; x <= 48; x += 1) grid.push(x, 0, 0, x, 0, -far);
    for (let z = 0; z <= far; z += 1) grid.push(-72, 1, -z, 72, 1, -z);
    this.gridCount = grid.length / 3;
    this.gridBuf = buffer(gl, new Float32Array(grid));
    this.gridFar = far;

    const dust = [];
    for (let i = 0; i < 520; i++) dust.push(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random());
    this.dustCount = 520;
    this.dustBuf = buffer(gl, new Float32Array(dust));

    this.initRings();
  }

  resize() {
    if (!this.mode) return;
    this.dpr = Math.min(window.devicePixelRatio || 1, this.dprCap);
    const w = Math.round(window.innerWidth * this.dpr);
    const h = Math.round(window.innerHeight * this.dpr);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    if (this.gl) this.gl.viewport(0, 0, w, h);
  }

  // Globe placement in CSS pixels, eased towards the target.
  setGlobe(x, y, r, alpha, instant = false) {
    this.target = { x, y, r, alpha };
    if (instant || this.globe.r === 0) this.globe = { ...this.target };
    this.kick();
  }

  setPhase(phase) {
    this.phase = phase;
    this.energyTarget = RESTING[phase] ?? 0.22;
    this.kick();
  }

  level(value) {
    this.energyTarget = Math.max(this.energyTarget, Math.min(1, value * 1.6));
    this.kick();
  }

  kick() {
    if (!this.mode || document.hidden) return;
    if (!this.frame) {
      this.last = performance.now();
      this.frame = requestAnimationFrame((t) => this.loop(t));
    }
  }

  stop() {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }

  loop(now) {
    const raw = (now - this.last) / 1000;
    const dt = Math.min(0.05, Math.max(0, raw));
    this.last = now;
    this.frame = requestAnimationFrame((t) => this.loop(t));
    this.time += dt * TEMPO;
    const k = 1 - Math.exp(-dt * 2.6);
    for (const key of ["x", "y", "r", "alpha"]) this.globe[key] += (this.target[key] - this.globe[key]) * k;
    this.energy += (this.energyTarget - this.energy) * (1 - Math.exp(-dt * 4));
    const resting = RESTING[this.phase] ?? 0.22;
    this.energyTarget += (resting - this.energyTarget) * (1 - Math.exp(-dt * 1.5));
    this.rot += dt * TEMPO * (0.28 + this.energy * 0.55);
    this.pulse += dt * TEMPO * (0.16 + this.energy * 0.24);
    this.adapt(raw);
    if (this.mode === "gl") this.draw();
    else this.draw2d();
  }

  // Trades resolution for frame rate on weak or software GPUs so the motion stays fluid.
  adapt(raw) {
    if (raw <= 0 || raw > 0.5) return;
    this.avg += (raw - this.avg) * 0.05;
    if (++this.frames < 90) return;
    this.frames = 0;
    if (this.avg < 1 / 34) return;
    const current = Math.min(window.devicePixelRatio || 1, this.dprCap);
    if (current > 1) this.dprCap = Math.max(1, current - 0.5);
    else if (!this.lite) this.lite = true;
    else if (current > 0.6) this.dprCap = Math.max(0.6, current - 0.2);
    else return;
    this.resize();
    this.avg = 1 / 60;
  }

  draw() {
    const gl = this.gl;
    const W = this.canvas.width;
    const H = this.canvas.height;
    const dpr = this.dpr;
    gl.clearColor(0.024, 0.031, 0.039, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE);

    // floor grid
    {
      const pr = this.progGrid;
      gl.useProgram(pr.p);
      const mvp = multiply(perspective(0.95, W / H, 0.1, 120), view(-0.08, 1.7));
      gl.uniformMatrix4fv(pr.u.uMVP, false, new Float32Array(mvp));
      gl.uniform1f(pr.u.uScroll, (this.time * 0.7) % 2);
      gl.uniform1f(pr.u.uFar, this.gridFar);
      gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], 0.16 + this.energy * 0.08);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.gridBuf);
      gl.enableVertexAttribArray(pr.a.aPos);
      gl.vertexAttribPointer(pr.a.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, this.gridCount);
      gl.disableVertexAttribArray(pr.a.aPos);
    }

    // dust
    {
      const pr = this.progDust;
      gl.useProgram(pr.p);
      gl.uniform1f(pr.u.uTime, this.time);
      gl.uniform1f(pr.u.uDpr, dpr);
      gl.uniform3f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2]);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.dustBuf);
      gl.enableVertexAttribArray(pr.a.aPos);
      gl.vertexAttribPointer(pr.a.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.POINTS, 0, this.dustCount);
      gl.disableVertexAttribArray(pr.a.aPos);
    }

    const g = this.globe;
    if (g.alpha < 0.01 || g.r < 2) return;
    const cx = g.x * dpr;
    const cy = g.y * dpr;
    const R = g.r * dpr;

    // rings, travelling arcs and energy pulses
    {
      const pr = this.progRing;
      gl.useProgram(pr.p);
      gl.uniform2f(pr.u.uRes, W, H);
      gl.uniform2f(pr.u.uCenter, cx, cy);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.ringBuf);
      gl.enableVertexAttribArray(pr.a.aT);
      gl.enableVertexAttribArray(pr.a.aSide);
      gl.vertexAttribPointer(pr.a.aT, 1, gl.FLOAT, false, 8, 0);
      gl.vertexAttribPointer(pr.a.aSide, 1, gl.FLOAT, false, 8, 4);
      gl.uniform1f(pr.u.uFade, 0);
      gl.uniform1f(pr.u.uStart, 0);
      gl.uniform1f(pr.u.uLen, Math.PI * 2);
      const breathe = 1 + 0.018 * Math.sin(this.time * 1.6);
      for (const ring of this.baseRings) {
        gl.uniform1f(pr.u.uRadius, R * ring.r * breathe);
        if (!this.lite) {
          gl.uniform1f(pr.u.uWidth, ring.w * 5.5 * dpr);
          gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], ring.a * 0.35 * g.alpha);
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.ringCount);
        }
        gl.uniform1f(pr.u.uWidth, ring.w * dpr);
        gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], ring.a * g.alpha);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.ringCount);
      }
      for (let i = 0; i < 3; i++) {
        const p = (this.pulse + i / 3) % 1;
        const fade = (1 - p) * (1 - p) * Math.min(1, p * 8);
        gl.uniform1f(pr.u.uRadius, R * (1.0 + p * 1.3));
        gl.uniform1f(pr.u.uWidth, (1.2 + p * 3) * dpr);
        gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], 0.45 * fade * g.alpha * (0.7 + this.energy));
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.ringCount);
      }
      gl.uniform1f(pr.u.uFade, 1);
      const speedUp = 1 + this.energy * 4.2;
      for (const arc of this.arcs) {
        const start = arc.start + this.time * arc.speed * speedUp;
        gl.uniform1f(pr.u.uRadius, R * arc.r * breathe);
        gl.uniform1f(pr.u.uWidth, arc.w * dpr * (1 + this.energy * 0.55));
        gl.uniform1f(pr.u.uStart, start);
        gl.uniform1f(pr.u.uLen, arc.len * (1 + this.energy * 0.3));
        gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], arc.a * g.alpha * (0.75 + this.energy * 0.5));
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.ringCount);
      }
      gl.disableVertexAttribArray(pr.a.aT);
      gl.disableVertexAttribArray(pr.a.aSide);
    }

    // ticks
    {
      const pr = this.progTick;
      gl.useProgram(pr.p);
      gl.uniform2f(pr.u.uRes, W, H);
      gl.uniform2f(pr.u.uCenter, cx, cy);
      gl.uniform1f(pr.u.uRadius, R * 2.28);
      gl.uniform1f(pr.u.uLen, R * 0.045);
      gl.uniform1f(pr.u.uRot, this.time * 0.08);
      gl.uniform4f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2], 0.34 * g.alpha);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.tickBuf);
      gl.enableVertexAttribArray(pr.a.aAngle);
      gl.enableVertexAttribArray(pr.a.aR);
      gl.vertexAttribPointer(pr.a.aAngle, 1, gl.FLOAT, false, 8, 0);
      gl.vertexAttribPointer(pr.a.aR, 1, gl.FLOAT, false, 8, 4);
      gl.drawArrays(gl.LINES, 0, this.tickCount);
      gl.disableVertexAttribArray(pr.a.aAngle);
      gl.disableVertexAttribArray(pr.a.aR);
    }

    // globe
    {
      const pr = this.progGlobe;
      gl.useProgram(pr.p);
      gl.uniform2f(pr.u.uRes, W, H);
      gl.uniform2f(pr.u.uCenter, cx, cy);
      gl.uniform1f(pr.u.uRadius, R);
      gl.uniform1f(pr.u.uTilt, 0.38 + 0.06 * Math.sin(this.time * 0.35));
      gl.uniform1f(pr.u.uDpr, dpr);
      gl.uniform1f(pr.u.uEnergy, this.energy);
      gl.uniform1f(pr.u.uTime, this.time);
      gl.uniform3f(pr.u.uColor, GOLD[0], GOLD[1], GOLD[2]);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.globeBuf);
      gl.enableVertexAttribArray(pr.a.aPos);
      gl.enableVertexAttribArray(pr.a.aSeed);
      gl.vertexAttribPointer(pr.a.aPos, 3, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(pr.a.aSeed, 1, gl.FLOAT, false, 16, 12);
      if (!this.lite) {
        gl.uniform1f(pr.u.uRot, -this.rot * 0.42);
        gl.uniform1f(pr.u.uPoint, 3.4);
        gl.uniform1f(pr.u.uAlpha, g.alpha * 0.28);
        gl.drawArrays(gl.POINTS, 0, this.globeCount);
      }
      gl.uniform1f(pr.u.uRot, this.rot);
      gl.uniform1f(pr.u.uPoint, 1.15);
      gl.uniform1f(pr.u.uAlpha, g.alpha);
      gl.drawArrays(gl.POINTS, 0, this.globeCount);
      gl.disableVertexAttribArray(pr.a.aPos);
      gl.disableVertexAttribArray(pr.a.aSeed);
    }
  }

  // Same scene drawn with Canvas 2D for browsers without WebGL.
  draw2d() {
    const c = this.ctx;
    const dpr = this.dpr;
    const w = this.canvas.width / dpr;
    const h = this.canvas.height / dpr;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.fillStyle = "#06080a";
    c.fillRect(0, 0, w, h);
    c.globalCompositeOperation = "lighter";

    // floor grid
    const horizon = h * 0.52;
    const depth = h - horizon;
    const focal = depth * 1.2;
    const far = 40;
    const fan = c.createLinearGradient(0, horizon, 0, h);
    fan.addColorStop(0, gold(0));
    fan.addColorStop(1, gold(0.14 + this.energy * 0.06));
    c.strokeStyle = fan;
    c.lineWidth = 1;
    c.beginPath();
    for (let x = -30; x <= 30; x++) {
      c.moveTo(w / 2 + (focal * x * 0.6) / 1.2, h);
      c.lineTo(w / 2 + (focal * x * 0.6) / far, horizon + focal / far);
    }
    c.stroke();
    const scroll = (this.time * 0.7) % 1;
    for (let i = 0; i < far; i++) {
      const z = i + 1.2 - scroll;
      const y = horizon + focal / z;
      if (y > h) continue;
      c.strokeStyle = gold((0.16 + this.energy * 0.06) * (1 - z / far) * Math.min(1, (z - 0.2) * 1.5));
      c.beginPath();
      c.moveTo(0, y);
      c.lineTo(w, y);
      c.stroke();
    }

    // dust
    for (const d of this.dust2d) {
      const x = (d[0] + Math.sin(this.time * 0.11 + d[2] * 20) * 0.045 + 1) * 0.5 * w;
      const y = (1 - (d[1] + Math.cos(this.time * 0.09 + d[2] * 13) * 0.045)) * 0.5 * h;
      const tw = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(this.time * (0.8 + d[2] * 2.2) + d[2] * 50));
      const s = 1 + d[2] * 2;
      c.fillStyle = gold(0.5 * tw);
      c.fillRect(x - s / 2, y - s / 2, s, s);
    }

    const g = this.globe;
    if (g.alpha < 0.01 || g.r < 2) return;
    const { x: cx, y: cy } = g;
    const R = g.r;
    const breathe = 1 + 0.018 * Math.sin(this.time * 1.6);
    const circle = (r, from = 0, to = Math.PI * 2) => {
      c.beginPath();
      c.arc(cx, cy, r, from, to);
      c.stroke();
    };

    c.lineCap = "round";
    for (const ring of this.baseRings) {
      c.strokeStyle = gold(ring.a * g.alpha);
      c.lineWidth = ring.w;
      circle(R * ring.r * breathe);
    }
    for (let i = 0; i < 3; i++) {
      const p = (this.pulse + i / 3) % 1;
      const fade = (1 - p) * (1 - p) * Math.min(1, p * 8);
      c.strokeStyle = gold(0.45 * fade * g.alpha * (0.7 + this.energy));
      c.lineWidth = 1.2 + p * 3;
      circle(R * (1 + p * 1.3));
    }
    const speedUp = 1 + this.energy * 4.2;
    for (const arc of this.arcs) {
      const start = arc.start + this.time * arc.speed * speedUp;
      c.strokeStyle = gold(arc.a * g.alpha * (0.75 + this.energy * 0.5));
      c.lineWidth = arc.w * (1 + this.energy * 0.55);
      circle(R * arc.r * breathe, start, start + arc.len * (1 + this.energy * 0.3));
    }

    c.strokeStyle = gold(0.34 * g.alpha);
    c.lineWidth = 1;
    c.beginPath();
    const tickRot = this.time * 0.08;
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * Math.PI * 2 + tickRot;
      const len = R * 0.045 * (i % 5 === 0 ? 1 : 0.45);
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      c.moveTo(cx + ca * R * 2.28, cy + sa * R * 2.28);
      c.lineTo(cx + ca * (R * 2.28 + len), cy + sa * (R * 2.28 + len));
    }
    c.stroke();

    const cr = Math.cos(this.rot);
    const sr = Math.sin(this.rot);
    const tilt = 0.38 + 0.06 * Math.sin(this.time * 0.35);
    const ct = Math.cos(tilt);
    const st = Math.sin(tilt);
    const scale = Math.min(2.2, Math.max(0.7, R / 280));
    const buckets = [[], [], [], []];
    const p = this.pts2d;
    for (let i = 0; i < p.length; i += 3) {
      const x0 = cr * p[i] + sr * p[i + 2];
      const z0 = -sr * p[i] + cr * p[i + 2];
      const y = ct * p[i + 1] - st * z0;
      const z = st * p[i + 1] + ct * z0;
      const persp = 1 / (1 - z * 0.28);
      buckets[Math.min(3, Math.floor((z + 1) * 2))].push(cx + x0 * R * persp, cy - y * R * persp);
    }
    for (let b = 0; b < 4; b++) {
      const front = (b + 0.5) / 4;
      const alpha = (0.16 + 0.84 * front * front) * g.alpha * (0.85 + this.energy * 0.4);
      const s = (0.85 + 1.7 * front) * scale * (1 + this.energy * 0.45) * 1.2;
      c.fillStyle = gold(Math.min(1, alpha));
      const list = buckets[b];
      for (let i = 0; i < list.length; i += 2) c.fillRect(list[i] - s / 2, list[i + 1] - s / 2, s, s);
    }
  }
}
