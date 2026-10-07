/* Offline stock-geometry replay. No third-party libraries or network requests. */
(() => {
  "use strict";

  const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const multiply = (a, b) => {
    const out = new Array(16).fill(0);
    for (let c = 0; c < 4; ++c) for (let r = 0; r < 4; ++r)
      for (let k = 0; k < 4; ++k) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
    return out;
  };
  const translation = (v) => {
    const m = identity();
    m[12] = v[0]; m[13] = v[1]; m[14] = v[2];
    return m;
  };
  const scaling = (v) => {
    const m = identity();
    m[0] = v[0]; m[5] = v[1]; m[10] = v[2];
    return m;
  };
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const subtract = (a, b) => a.map((v, i) => v - b[i]);
  const normalized = (v) => {
    const n = Math.hypot(...v);
    return n > 1e-12 ? v.map(x => x / n) : [0, 0, 1];
  };
  const rotation = (axis, angle) => {
    const [x, y, z] = normalized(axis), c = Math.cos(angle), s = Math.sin(angle), t = 1 - c;
    return [t*x*x+c, t*x*y+s*z, t*x*z-s*y, 0,
      t*x*y-s*z, t*y*y+c, t*y*z+s*x, 0,
      t*x*z+s*y, t*y*z-s*x, t*z*z+c, 0, 0, 0, 0, 1];
  };
  const originMatrix = (origin) => {
    const rpy = origin?.rpy || [0, 0, 0];
    return multiply(translation(origin?.xyz || [0, 0, 0]),
      multiply(rotation([0, 0, 1], rpy[2]), multiply(rotation([0, 1, 0], rpy[1]), rotation([1, 0, 0], rpy[0]))));
  };
  const perspective = (aspect) => {
    const f = 1 / Math.tan(Math.PI / 8), near = 0.025, far = 200;
    return [f/aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far+near)/(near-far), -1, 0, 0, 2*far*near/(near-far), 0];
  };
  const lookAt = (eye, target) => {
    const z = normalized(subtract(eye, target)), x = normalized(cross([0, 0, 1], z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
  };
  const normalMatrix = (m) => {
    const a = [m[0], m[1], m[2]], b = [m[4], m[5], m[6]], c = [m[8], m[9], m[10]];
    const u = cross(b, c), v = cross(c, a), w = cross(a, b), determinant = dot(a, u);
    return Math.abs(determinant) > 1e-14 ? [...u, ...v, ...w].map(x => x / determinant) : [1, 0, 0, 0, 1, 0, 0, 0, 1];
  };

  function box(size) {
    const [x, y, z] = size.map(v => v / 2);
    const vertices = [[-x,-y,-z], [x,-y,-z], [x,y,-z], [-x,y,-z], [-x,-y,z], [x,-y,z], [x,y,z], [-x,y,z]];
    const triangles = [];
    for (const [a, b, c, d] of [[0,3,2,1], [4,5,6,7], [0,1,5,4], [1,2,6,5], [2,3,7,6], [3,0,4,7]])
      triangles.push([a,b,c], [a,c,d]);
    return {vertices, triangles};
  }

  function sphere(radius, rows = 18, columns = 32) {
    const vertices = [], triangles = [];
    for (let i = 0; i <= rows; ++i) for (let j = 0; j < columns; ++j) {
      const theta = i * Math.PI / rows, phi = j * 2 * Math.PI / columns;
      vertices.push([radius*Math.sin(theta)*Math.cos(phi), radius*Math.sin(theta)*Math.sin(phi), radius*Math.cos(theta)]);
    }
    for (let i = 0; i < rows; ++i) for (let j = 0; j < columns; ++j) {
      const a = i * columns + j, b = i * columns + (j + 1) % columns;
      if (i > 0) triangles.push([a, a+columns, b]);
      if (i < rows-1) triangles.push([b, a+columns, b+columns]);
    }
    return {vertices, triangles};
  }

  class ReplayViewer {
    constructor(canvas, robotAssets) {
      this.canvas = canvas;
      this.assets = robotAssets || {links: [], joints: [], root_links: []};
      this.gl = canvas.getContext("webgl", {alpha: false, antialias: true});
      if (!this.gl) throw new Error("3D replay requires WebGL in this browser.");
      this.destroyed = false;
      this.buffers = [];
      this.frameRequest = null;
      this.jointPositions = {};
      this.ballPosition = null;
      this.ballLocatorEnabled = true;
      this.courtEnabled = true;
      this._createProgram();
      this.robotDrawables = [];
      this.children = new Map();
      this.jointsByName = new Map();
      for (const joint of this.assets.joints || []) {
        this.jointsByName.set(joint.name, joint);
        if (!this.children.has(joint.parent)) this.children.set(joint.parent, []);
        this.children.get(joint.parent).push({...joint, matrix: originMatrix(joint.origin)});
      }
      for (const link of this.assets.links || []) for (const visual of link.visuals || []) {
        const geometry = visual.geometry;
        const matrix = multiply(originMatrix(visual.origin), scaling(geometry.scale || [1,1,1]));
        for (const part of geometry.parts || []) this.robotDrawables.push({link: link.name, matrix, mesh: this._mesh(part)});
      }
      this.ballMesh = this._mesh({...sphere(0.0335), color: [0.78, 0.93, 0.05, 1]});
      const locatorRing = Array.from({length: 48}, (_, i) => [Math.cos(i*2*Math.PI/48), Math.sin(i*2*Math.PI/48), 0]);
      this.locatorMesh = this._lineMesh(locatorRing, [0.25, 0.94, 1, 1], this.gl.LINE_LOOP);
      this.trailMesh = this._lineMesh([], [1, 0.76, 0.25, 1], this.gl.LINES);
      this.floorMesh = this._mesh({...box([32, 16, 0.06]), color: [0.09, 0.12, 0.16, 1]});
      this.courtDrawables = [];
      this._createCourt();
      this.setView("robot");
      this._bindControls();
      if (typeof ResizeObserver !== "undefined") {
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(canvas);
      }
      this.resize();
    }

    _createProgram() {
      const gl = this.gl;
      const compile = (type, source) => {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          const message = gl.getShaderInfoLog(shader);
          gl.deleteShader(shader);
          throw new Error(`Replay shader: ${message}`);
        }
        return shader;
      };
      const vertex = compile(gl.VERTEX_SHADER, `
        attribute vec3 aPosition;
        attribute vec3 aNormal;
        uniform mat4 uModel;
        uniform mat4 uViewProjection;
        uniform mat3 uNormal;
        varying vec3 vNormal;
        varying vec3 vPosition;
        void main() {
          vec4 position = uModel * vec4(aPosition, 1.0);
          vPosition = position.xyz;
          vNormal = uNormal * aNormal;
          gl_Position = uViewProjection * position;
        }`);
      const fragment = compile(gl.FRAGMENT_SHADER, `
        precision mediump float;
        uniform vec4 uColor;
        uniform vec3 uEye;
        uniform float uUnlit;
        varying vec3 vNormal;
        varying vec3 vPosition;
        void main() {
          if (uUnlit > 0.5) {
            gl_FragColor = uColor;
            return;
          }
          vec3 normal = normalize(vNormal);
          if (!gl_FrontFacing) normal = -normal;
          vec3 light = normalize(vec3(-0.35, -0.55, 1.0));
          float diffuse = max(dot(normal, light), 0.0);
          vec3 halfDirection = normalize(light + normalize(uEye - vPosition));
          float specular = pow(max(dot(normal, halfDirection), 0.0), 28.0) * 0.10;
          vec3 linearColor = uColor.rgb * (0.46 + diffuse * 0.54) + vec3(specular);
          gl_FragColor = vec4(pow(max(linearColor, vec3(0.0)), vec3(1.0/2.2)), uColor.a);
        }`);
      this.program = gl.createProgram();
      gl.attachShader(this.program, vertex);
      gl.attachShader(this.program, fragment);
      gl.linkProgram(this.program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(this.program));
      this.locations = {};
      for (const name of ["uModel", "uViewProjection", "uNormal", "uColor", "uEye", "uUnlit"])
        this.locations[name] = gl.getUniformLocation(this.program, name);
      this.locations.aPosition = gl.getAttribLocation(this.program, "aPosition");
      this.locations.aNormal = gl.getAttribLocation(this.program, "aNormal");
    }

    _mesh(part) {
      const gl = this.gl, data = [];
      for (const triangle of part.triangles) {
        const [a, b, c] = triangle.map(i => part.vertices[i]);
        const normal = normalized(cross(subtract(b, a), subtract(c, a)));
        for (const position of [a, b, c]) data.push(...position, ...normal);
      }
      const buffer = gl.createBuffer();
      this.buffers.push(buffer);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      return {buffer, count: data.length / 6, color: part.color || [0.75, 0.78, 0.82, 1]};
    }

    _lineMesh(vertices, color, mode) {
      const buffer = this.gl.createBuffer();
      this.buffers.push(buffer);
      const mesh = {buffer, count: 0, color, mode, unlit: true};
      this._setLineVertices(mesh, vertices);
      return mesh;
    }

    _setLineVertices(mesh, vertices) {
      const data = new Float32Array(vertices.length*6);
      vertices.forEach((vertex, i) => {
        data.set(vertex, i*6);
        data[i*6+5] = 1;
      });
      this.gl.bindBuffer(this.gl.ARRAY_BUFFER, mesh.buffer);
      this.gl.bufferData(this.gl.ARRAY_BUFFER, data, this.gl.STATIC_DRAW);
      mesh.count = vertices.length;
    }

    _createCourt() {
      const addBox = (xyz, size, color) => this.courtDrawables.push({matrix: translation(xyz), mesh: this._mesh({...box(size), color})});
      const green = [0.045, 0.21, 0.15, 1], white = [0.75, 0.79, 0.76, 1], net = [0.055, 0.065, 0.075, 1];
      addBox([11.885, 0, -0.01], [23.77, 8.23, 0.02], green);
      for (const y of [-4.115, 4.115]) addBox([11.885, y, 0.004], [23.82, 0.05, 0.008], white);
      for (const x of [0, 23.77]) addBox([x, 0, 0.004], [0.05, 8.23, 0.008], white);
      for (const x of [5.485, 18.285]) addBox([x, 0, 0.004], [0.05, 8.23, 0.008], white);
      addBox([11.885, 0, 0.004], [12.8, 0.05, 0.008], white);
      // Net tape follows the real centre dip (0.914 m) and 1.07 m end height.
      const sections = 40, width = 9.6;
      for (let i = 0; i <= sections; ++i) {
        const y = -width/2 + i*width/sections;
        const height = 0.914 + 0.156 * Math.pow(Math.abs(y)/(width/2), 2);
        addBox([11.885, y, height/2], [0.014, 0.012, height], net);
        if (i < sections) {
          const centerY = y + width/(2*sections);
          const centerHeight = 0.914 + 0.156 * Math.pow(Math.abs(centerY)/(width/2), 2);
          addBox([11.885, centerY, centerHeight], [0.035, width/sections+0.01, 0.04], white);
        }
      }
      for (let z = 0.16; z < 0.91; z += 0.15) addBox([11.885, 0, z], [0.012, width, 0.01], net);
      for (const y of [-width/2, width/2]) addBox([11.885, y, 0.55], [0.065, 0.065, 1.1], net);
    }

    _bindControls() {
      let pointer = null;
      this.onPointerDown = (event) => {
        if (event.button !== 0) return;
        pointer = {id: event.pointerId, x: event.clientX, y: event.clientY};
        this.canvas.setPointerCapture?.(event.pointerId);
      };
      this.onPointerMove = (event) => {
        if (!pointer || pointer.id !== event.pointerId) return;
        this.azimuth -= (event.clientX-pointer.x)*0.008;
        this.elevation = Math.max(0.04, Math.min(1.45, this.elevation+(event.clientY-pointer.y)*0.007));
        pointer.x = event.clientX; pointer.y = event.clientY;
        this._requestDraw();
      };
      this.onPointerUp = () => { pointer = null; };
      this.onWheel = (event) => {
        event.preventDefault();
        this.distance = Math.max(0.35, Math.min(80, this.distance*Math.exp(Math.max(-100, Math.min(100, event.deltaY))*0.002)));
        this._requestDraw();
      };
      this.canvas.style.touchAction = "none";
      this.canvas.addEventListener("pointerdown", this.onPointerDown);
      this.canvas.addEventListener("pointermove", this.onPointerMove);
      this.canvas.addEventListener("pointerup", this.onPointerUp);
      this.canvas.addEventListener("pointercancel", this.onPointerUp);
      this.canvas.addEventListener("wheel", this.onWheel, {passive: false});
      this.onWindowResize = () => this.resize();
      window.addEventListener("resize", this.onWindowResize);
    }

    setFrame(jointNameToPosition, ballXYZ) {
      this.jointPositions = {...jointNameToPosition};
      this.ballPosition = Array.isArray(ballXYZ) && ballXYZ.length === 3 && ballXYZ.every(Number.isFinite) ? [...ballXYZ] : null;
      this._requestDraw();
    }

    setCourt(enabled) {
      this.courtEnabled = Boolean(enabled);
      this._requestDraw();
    }

    setBallLocator(enabled) {
      this.ballLocatorEnabled = Boolean(enabled);
      this._requestDraw();
    }

    setTrail(arrayXYZ) {
      if (this.destroyed) return;
      const points = Array.isArray(arrayXYZ) ? arrayXYZ : [], segments = [];
      const valid = point => Array.isArray(point) && point.length === 3 && point.every(Number.isFinite);
      for (let i = 1; i < points.length; ++i) {
        // Missing samples leave a gap rather than an invented trajectory segment.
        if (valid(points[i-1]) && valid(points[i])) segments.push(points[i-1], points[i]);
      }
      this._setLineVertices(this.trailMesh, segments);
      this._requestDraw();
    }

    setView(view) {
      if (view !== "robot" && view !== "court") throw new Error(`Unknown replay view: ${view}`);
      this.view = view;
      this.target = view === "court" ? [11.0, 0, 0.4] : [0.12, 0, 0.62];
      this.distance = view === "court" ? 29 : 2.25;
      this.azimuth = view === "court" ? -1.07 : -0.85;
      this.elevation = view === "court" ? 0.76 : 0.40;
      this._requestDraw();
    }

    resize() {
      if (this.destroyed) return;
      const rectangle = this.canvas.getBoundingClientRect();
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(rectangle.width*pixelRatio));
      const height = Math.max(1, Math.round(rectangle.height*pixelRatio));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width; this.canvas.height = height;
      }
      this._requestDraw();
    }

    _requestDraw() {
      if (this.destroyed || this.frameRequest !== null) return;
      this.frameRequest = requestAnimationFrame(() => {
        this.frameRequest = null;
        if (!this.destroyed) this._draw();
      });
    }

    _jointPosition(name, visited = new Set()) {
      const explicit = this.jointPositions[name];
      if (Number.isFinite(explicit)) return explicit;
      if (visited.has(name)) return 0;
      visited.add(name);
      const mimic = this.jointsByName.get(name)?.mimic;
      return mimic ? (mimic.multiplier ?? 1)*this._jointPosition(mimic.joint, visited)+(mimic.offset ?? 0) : 0;
    }

    _linkTransforms() {
      const transforms = new Map();
      const visit = (name, matrix) => {
        if (transforms.has(name)) return;
        transforms.set(name, matrix);
        for (const joint of this.children.get(name) || []) {
          const position = this._jointPosition(joint.name);
          const motion = joint.type === "revolute" || joint.type === "continuous" ? rotation(joint.axis || [1,0,0], position)
            : joint.type === "prismatic" ? translation((joint.axis || [1,0,0]).map(v => v*position)) : identity();
          visit(joint.child, multiply(matrix, multiply(joint.matrix, motion)));
        }
      };
      const children = new Set((this.assets.joints || []).map(joint => joint.child));
      const roots = this.assets.root_links?.length ? this.assets.root_links : (this.assets.links || []).filter(link => !children.has(link.name)).map(link => link.name);
      for (const root of roots) visit(root, identity());
      return transforms;
    }

    _drawMesh(mesh, matrix) {
      const gl = this.gl, loc = this.locations;
      gl.bindBuffer(gl.ARRAY_BUFFER, mesh.buffer);
      gl.vertexAttribPointer(loc.aPosition, 3, gl.FLOAT, false, 24, 0);
      gl.vertexAttribPointer(loc.aNormal, 3, gl.FLOAT, false, 24, 12);
      gl.uniformMatrix4fv(loc.uModel, false, matrix);
      gl.uniformMatrix3fv(loc.uNormal, false, normalMatrix(matrix));
      gl.uniform4fv(loc.uColor, mesh.color);
      gl.uniform1f(loc.uUnlit, mesh.unlit ? 1 : 0);
      gl.drawArrays(mesh.mode ?? gl.TRIANGLES, 0, mesh.count);
    }

    _drawBallLocator(eye) {
      const forward = normalized(subtract(eye, this.target));
      const right = normalized(cross([0, 0, 1], forward)), up = cross(forward, right);
      const depth = dot(subtract(eye, this.ballPosition), forward);
      if (depth <= 0.025) return;
      const cssHeight = Math.max(1, this.canvas.getBoundingClientRect().height);
      // A cyan annotation ring is about 14 CSS px across in the court view.
      // It grows outside the actual 67 mm ball in close-up; sphere geometry stays fixed.
      const radius = Math.max(0.0335*1.4, 14*depth*Math.tan(Math.PI/8)/cssHeight);
      const matrix = [...right.map(v => v*radius), 0, ...up.map(v => v*radius), 0,
        ...forward.map(v => v*radius), 0, ...this.ballPosition, 1];
      // This screen-facing locator is an overlay, including when the sphere is occluded.
      this.gl.disable(this.gl.DEPTH_TEST);
      this._drawMesh(this.locatorMesh, matrix);
      this.gl.enable(this.gl.DEPTH_TEST);
    }

    _draw() {
      const gl = this.gl, loc = this.locations;
      const planar = this.distance*Math.cos(this.elevation);
      const eye = [this.target[0]+planar*Math.cos(this.azimuth), this.target[1]+planar*Math.sin(this.azimuth), this.target[2]+this.distance*Math.sin(this.elevation)];
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      gl.clearColor(0.035, 0.047, 0.065, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.useProgram(this.program);
      gl.enableVertexAttribArray(loc.aPosition);
      gl.enableVertexAttribArray(loc.aNormal);
      gl.uniformMatrix4fv(loc.uViewProjection, false, multiply(perspective(this.canvas.width/this.canvas.height), lookAt(eye, this.target)));
      gl.uniform3fv(loc.uEye, eye);
      this._drawMesh(this.floorMesh, translation([11, 0, -0.05]));
      if (this.courtEnabled) for (const drawable of this.courtDrawables) this._drawMesh(drawable.mesh, drawable.matrix);
      const transforms = this._linkTransforms();
      for (const drawable of this.robotDrawables) {
        const link = transforms.get(drawable.link);
        if (link) this._drawMesh(drawable.mesh, multiply(link, drawable.matrix));
      }
      if (this.trailMesh.count) {
        gl.depthMask(false);
        this._drawMesh(this.trailMesh, identity());
        gl.depthMask(true);
      }
      if (this.ballPosition) this._drawMesh(this.ballMesh, translation(this.ballPosition));
      if (this.ballPosition && this.ballLocatorEnabled) this._drawBallLocator(eye);
    }

    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      if (this.frameRequest !== null) cancelAnimationFrame(this.frameRequest);
      this.resizeObserver?.disconnect();
      this.canvas.removeEventListener("pointerdown", this.onPointerDown);
      this.canvas.removeEventListener("pointermove", this.onPointerMove);
      this.canvas.removeEventListener("pointerup", this.onPointerUp);
      this.canvas.removeEventListener("pointercancel", this.onPointerUp);
      this.canvas.removeEventListener("wheel", this.onWheel);
      window.removeEventListener("resize", this.onWindowResize);
      for (const buffer of this.buffers) this.gl.deleteBuffer(buffer);
      this.gl.deleteProgram(this.program);
    }
  }

  window.ReplayViewer = ReplayViewer;
})();
