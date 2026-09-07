/*
 * The client half of the spike: React for the UI, three.js for the pixels, and
 * no CFD knowledge whatsoever. Everything geometric arrives from the server
 * already extracted (spike/wire.py); this file only turns typed arrays into
 * BufferGeometry and maps scalars through a LUT texture in a shader.
 *
 * The thing worth watching is which controls cost a round trip. Colour map,
 * colour range and visibility are pure GPU state -> instant, no fetch. Slice
 * position, isovalue, seed count, field and time step change what has to be
 * *extracted* -> refetch. Each control is tagged accordingly in the UI, and
 * carries a data-ctl name so spike/check_browser.py can assert exactly that.
 */
import * as THREE from 'three'
import { OrbitControls } from '/web/vendor/OrbitControls.js'

const html = htm.bind(React.createElement)
const { useState, useEffect, useRef, useCallback } = React

// ---------------------------------------------------------------- wire format

const MAGIC = 'FVS1'

function decodeScene(buffer) {
  const bytes = new Uint8Array(buffer)
  const magic = String.fromCharCode(...bytes.subarray(0, 4))
  if (magic !== MAGIC) throw new Error(`not a scene payload (${magic})`)
  const headerLen = new DataView(buffer).getUint32(4, true)
  const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + headerLen)))
  const base = 8 + headerLen
  const view = (e) => e.type === 'u32'
    ? new Uint32Array(buffer, base + e.offset, e.length / 4)
    : new Float32Array(buffer, base + e.offset, e.length / 4)
  const parts = header.parts.map((spec) => ({
    name: spec.name,
    mode: spec.mode,
    counts: spec.counts,
    index: view(spec.index),
    attributes: Object.fromEntries(
      Object.entries(spec.attributes).map(([k, e]) => [k, { array: view(e), components: e.components }]),
    ),
  }))
  return { header, parts }
}

// ------------------------------------------------------------------- shaders

// `position` and `normal` are three.js built-ins; `scalar` is ours, straight
// off the wire. The LUT lookup is the whole colour pipeline -- change uRange or
// swap the texture and the picture re-colours with no geometry touched.
const VERT = /* glsl */`
  attribute float scalar;
  varying float vScalar;
  varying vec3 vNormal;
  void main() {
    vScalar = scalar;
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

const VERT_LINE = /* glsl */`
  attribute float scalar;
  varying float vScalar;
  void main() {
    vScalar = scalar;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

// Two-sided key+fill with an ambient floor, so no face reads as black -- the
// same rule FoamViz's vtkLightKit setup follows, hence abs() on the dots.
const FRAG = /* glsl */`
  uniform sampler2D uLut;
  uniform vec2 uRange;
  uniform float uOpacity;
  uniform float uAmbient;
  varying float vScalar;
  varying vec3 vNormal;
  void main() {
    float t = clamp((vScalar - uRange.x) / max(uRange.y - uRange.x, 1e-12), 0.0, 1.0);
    vec3 base = texture2D(uLut, vec2(t, 0.5)).rgb;
    vec3 n = normalize(vNormal);
    float key = abs(dot(n, normalize(vec3(0.35, 0.65, 0.55))));
    float fill = abs(dot(n, normalize(vec3(-0.6, -0.25, 0.5)))) * 0.35;
    gl_FragColor = vec4(base * (uAmbient + (1.0 - uAmbient) * (key + fill)), uOpacity);
  }`

const FRAG_LINE = /* glsl */`
  uniform sampler2D uLut;
  uniform vec2 uRange;
  varying float vScalar;
  void main() {
    float t = clamp((vScalar - uRange.x) / max(uRange.y - uRange.x, 1e-12), 0.0, 1.0);
    gl_FragColor = vec4(texture2D(uLut, vec2(t, 0.5)).rgb, 1.0);
  }`

// ------------------------------------------------------------------- viewer

class Viewer {
  constructor(container) {
    this.container = container
    this.renderer = new THREE.WebGLRenderer({ antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    // Straight-through colour: the LUT bytes come from the same matplotlib
    // samples VTK uses, so any output conversion would make the two renderers
    // disagree on colour for no reason.
    this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace
    container.appendChild(this.renderer.domElement)

    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(0x16181d)
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1e6)
    // Z is up in CFD, and OrbitControls *is* a turntable: it orbits the target
    // while maintaining `object.up`, so azimuth spins about the world Z axis and
    // the horizon never rolls. Two things matter here. The up vector must be set
    // BEFORE constructing the controls -- the constructor bakes it into a
    // quaternion (`_quat`) and never re-reads it -- and the start position must
    // be off-axis, since sitting exactly on the pole leaves azimuth undefined.
    this.camera.up.set(0, 0, 1)
    this.camera.position.set(3, -3, 2)
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.12
    // three.js defaults to middle=dolly, right=pan; VTK (so ParaView, so
    // FoamViz) is middle=pan, right=dolly. Match the app people already use --
    // easy to flip back here if the other order is preferred.
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.DOLLY,
    }

    this.lut = new THREE.DataTexture(new Uint8Array(256 * 4), 256, 1, THREE.RGBAFormat)
    this.lut.minFilter = this.lut.magFilter = THREE.LinearFilter
    this.lut.wrapS = this.lut.wrapT = THREE.ClampToEdgeWrapping
    // uLut and uRange are shared *objects*, so a colour-map or range change is
    // one assignment for the whole scene. Opacity is per material: the boundary
    // needs to fade while the slice inside it stays solid.
    this.shared = { uLut: { value: this.lut }, uRange: { value: new THREE.Vector2(0, 1) } }
    this.meshes = new Map()
    this.frames = 0
    this.fps = 0
    this._resize()
    window.addEventListener('resize', () => this._resize())
    this._tick()
  }

  _resize() {
    const { clientWidth: w, clientHeight: h } = this.container
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / Math.max(h, 1)
    this.camera.updateProjectionMatrix()
  }

  _tick() {
    requestAnimationFrame(() => this._tick())
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    this.frames += 1
    const now = performance.now()
    if (!this._t0) this._t0 = now
    if (now - this._t0 >= 500) {
      this.fps = Math.round((this.frames * 1000) / (now - this._t0))
      this.frames = 0
      this._t0 = now
      window.__spikeStats = this.stats()
    }
  }

  material(mode) {
    const uniforms = { ...this.shared, uOpacity: { value: 1 }, uAmbient: { value: 0.35 } }
    return mode === 'lines'
      ? new THREE.ShaderMaterial({ uniforms, vertexShader: VERT_LINE, fragmentShader: FRAG_LINE })
      : new THREE.ShaderMaterial({
        uniforms, vertexShader: VERT, fragmentShader: FRAG, side: THREE.DoubleSide,
      })
  }

  /* Boundary appearance -- both of these are why you can see into the room, and
   * both are pure GPU state: no refetch, no re-extraction. `cull` renders back
   * faces only, the same trick as FoamViz's "cull near walls" (VTK's
   * FrontfaceCulling). */
  setSurfaceStyle({ opacity, cull }) {
    this.surfaceStyle = { opacity, cull }
    const mesh = this.meshes.get('boundary')
    if (!mesh) return
    mesh.material.uniforms.uOpacity.value = opacity
    mesh.material.transparent = opacity < 0.999
    mesh.material.depthWrite = opacity >= 0.999
    mesh.material.side = cull ? THREE.BackSide : THREE.DoubleSide
    mesh.material.needsUpdate = true
  }

  setScene({ header, parts }, visible) {
    for (const mesh of this.meshes.values()) {
      mesh.geometry.dispose()
      mesh.material.dispose()
      this.scene.remove(mesh)
    }
    this.meshes.clear()

    for (const part of parts) {
      const geometry = new THREE.BufferGeometry()
      for (const [name, attr] of Object.entries(part.attributes)) {
        geometry.setAttribute(name, new THREE.BufferAttribute(attr.array, attr.components))
      }
      geometry.setIndex(new THREE.BufferAttribute(part.index, 1))
      const material = this.material(part.mode)
      const mesh = part.mode === 'lines'
        ? new THREE.LineSegments(geometry, material)
        : new THREE.Mesh(geometry, material)
      mesh.name = part.name
      mesh.visible = visible[part.name] !== false
      this.meshes.set(part.name, mesh)
      this.scene.add(mesh)
    }
    this.setRange(header.range[0], header.range[1])
    if (this.surfaceStyle) this.setSurfaceStyle(this.surfaceStyle)
    return header
  }

  setVisible(name, on) {
    const mesh = this.meshes.get(name)
    if (mesh) mesh.visible = on
  }

  setRange(lo, hi) { this.shared.uRange.value.set(lo, hi) }

  setLut(rgb) {
    const data = this.lut.image.data
    for (let i = 0; i < 256; i += 1) {
      data[i * 4 + 0] = rgb[i * 3 + 0]
      data[i * 4 + 1] = rgb[i * 3 + 1]
      data[i * 4 + 2] = rgb[i * 3 + 2]
      data[i * 4 + 3] = 255
    }
    this.lut.needsUpdate = true
  }

  frameAll(bounds) {
    const [x0, x1, y0, y1, z0, z1] = bounds
    const centre = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    const diagonal = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 1
    this.controls.target.copy(centre)
    this.camera.near = diagonal / 1000
    this.camera.far = diagonal * 100
    // Front-right and slightly above, in a Z-up world -- the standard CFD
    // three-quarter view, and well clear of the pole.
    this.camera.position.copy(centre).add(
      new THREE.Vector3(1, -1, 0.6).normalize().multiplyScalar(diagonal * 1.6),
    )
    this.camera.updateProjectionMatrix()
    this.controls.update()
  }

  stats() {
    const r = this.renderer.info.render
    return { fps: this.fps, triangles: r.triangles, calls: r.calls }
  }

  /* Test hook (spike/check_browser.py): how many distinct colours are actually
   * in the GL back buffer. A page screenshot cannot answer that -- the HUD, the
   * legend and the busy overlay all sit on top of the canvas, so an empty scene
   * screenshots as a colourful one. Counted here rather than shipping the pixels
   * out through evaluate(), which would be megabytes of JSON per call. */
  grab() {
    const gl = this.renderer.getContext()
    this.renderer.render(this.scene, this.camera)
    const w = gl.drawingBufferWidth
    const h = gl.drawingBufferHeight
    const pixels = new Uint8Array(w * h * 4)
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const seen = new Set()
    for (let i = 0; i < pixels.length; i += 4) {
      seen.add((pixels[i] << 16) | (pixels[i + 1] << 8) | pixels[i + 2])
    }
    return { colours: seen.size, width: w, height: h }
  }
}

// ---------------------------------------------------------------------- app

/* Sliders commit on release, not while dragging.
 *
 * React's onChange for a range input is wired to the native `input` event,
 * which fires on every pixel of a drag -- so a single slider sweep queued ~20
 * extractions, each of them seconds long on a real case. The native `change`
 * event is the release event for a range input (mouse up, or key up when
 * arrowing), so the draft value drives the UI live and only `change` commits.
 *
 * The listener is attached natively rather than through React because React
 * maps both onChange and onInput to `input`; there is no synthetic event for
 * "the user let go".
 */
function Slider({ value, onCommit, ...rest }) {
  const ref = useRef(null)
  const [draft, setDraft] = useState(value)
  const commit = useRef(onCommit)
  commit.current = onCommit

  useEffect(() => { setDraft(value) }, [value])
  useEffect(() => {
    const el = ref.current
    const handler = (e) => commit.current(+e.target.value)
    el.addEventListener('change', handler)
    return () => el.removeEventListener('change', handler)
  }, [])

  return html`<input ...${rest} ref=${ref} type="range" value=${draft}
                     onInput=${(e) => setDraft(+e.target.value)} />`
}

const PARTS = [
  ['boundary', 'Boundary'],
  ['slice', 'Cut plane'],
  ['iso', 'Isosurface'],
  ['streamlines', 'Streamlines'],
]

function Panel({ meta, q, set, visible, toggle, preset, setPreset, range, setRange, onRescale,
  surface, setSurface }) {
  const fields = meta ? Object.keys(meta.fields).sort() : []
  const times = meta ? meta.times : []
  return html`
    <div className="panel">
      <h1>VTK server · three.js client</h1>
      <div className="sub">${meta ? `${meta.case} — ${meta.cells.toLocaleString()} cells` : 'loading…'}</div>

      <div className="group">
        <label className="head">Case <span className="tag server">server</span></label>
        <div className="row">
          <select data-ctl="case" value=${q.case}
                  onChange=${(e) => set({ case: e.target.value, time_index: undefined })}>
            ${(meta ? meta.cases : []).map((c) => html`<option key=${c} value=${c}>${c}</option>`)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">Time</span>
          <${Slider} data-ctl="time" min="0" max=${Math.max(times.length - 1, 0)} step="1"
                 value=${q.time_index ?? Math.max(times.length - 1, 0)}
                 onCommit=${(v) => set({ time_index: v })} />
          <span>${times.length ? (times[q.time_index ?? times.length - 1] ?? 0) : 0}</span>
        </div>
      </div>

      <div className="group">
        <label className="head">Field <span className="tag server">server</span></label>
        <div className="row">
          <span className="lbl">Colour by</span>
          <select data-ctl="field" value=${q.field || ''} onChange=${(e) => set({ field: e.target.value })}>
            ${fields.map((f) => html`<option key=${f} value=${f}>${f}</option>`)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">Component</span>
          <select data-ctl="component" value=${q.component} onChange=${(e) => set({ component: e.target.value })}
                  disabled=${!meta || meta.fields[q.field] !== 3}>
            ${['magnitude', 'x', 'y', 'z'].map((c) => html`<option key=${c} value=${c}>${c}</option>`)}
          </select>
        </div>
      </div>

      <div className="group">
        <label className="head">Colour map <span className="tag client">client</span></label>
        <div className="row">
          <select data-ctl="preset" value=${preset} onChange=${(e) => setPreset(e.target.value)}>
            ${(meta ? meta.presets : []).map((p) => html`<option key=${p} value=${p}>${p}</option>`)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">Min</span>
          <input data-ctl="range-min" type="number" step="any" value=${range[0]}
                 onChange=${(e) => setRange([+e.target.value, range[1]])} />
        </div>
        <div className="row">
          <span className="lbl">Max</span>
          <input data-ctl="range-max" type="number" step="any" value=${range[1]}
                 onChange=${(e) => setRange([range[0], +e.target.value])} />
        </div>
        <div className="row"><button data-ctl="rescale" onClick=${onRescale}>Rescale to data</button></div>
      </div>

      <div className="group">
        <label className="head">Parts <span className="tag client">client</span></label>
        ${PARTS.map(([key, label]) => html`
          <div className="row" key=${key}>
            <label className="chk">
              <input data-ctl=${`part-${key}`} type="checkbox" checked=${visible[key] !== false}
                     onChange=${(e) => toggle(key, e.target.checked)} />
              ${label}
            </label>
          </div>`)}
        <div className="row">
          <span className="lbl">Bnd. opacity</span>
          <input data-ctl="opacity" type="range" min="0.05" max="1" step="0.05" value=${surface.opacity}
                 onInput=${(e) => setSurface({ ...surface, opacity: +e.target.value })} />
          <span>${surface.opacity.toFixed(2)}</span>
        </div>
        <div className="row">
          <label className="chk">
            <input data-ctl="cull" type="checkbox" checked=${surface.cull}
                   onChange=${(e) => setSurface({ ...surface, cull: e.target.checked })} />
            Cull near walls
          </label>
        </div>
        <div className="sub tight">Unchecking hides; the geometry stays on the GPU.
          Parts are only re-extracted when a <span className="tag server">server</span> control changes.</div>
      </div>

      <div className="group">
        <label className="head">Cut plane <span className="tag server">server</span></label>
        <div className="row">
          <span className="lbl">Axis</span>
          <select data-ctl="slice-axis" value=${q.slice_axis} onChange=${(e) => set({ slice_axis: e.target.value })}>
            ${['x', 'y', 'z'].map((a) => html`<option key=${a} value=${a}>${a}</option>`)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">Position</span>
          <${Slider} data-ctl="slice-frac" min="0" max="1" step="0.02" value=${q.slice_frac}
                 onCommit=${(v) => set({ slice_frac: v })} />
          <span>${(+q.slice_frac).toFixed(2)}</span>
        </div>
      </div>

      <div className="group">
        <label className="head">Isosurface / streamlines <span className="tag server">server</span></label>
        <div className="row">
          <span className="lbl">Isovalue</span>
          <${Slider} data-ctl="iso-frac" min="0.02" max="0.98" step="0.02" value=${q.iso_frac}
                 onCommit=${(v) => set({ iso_frac: v })} />
          <span>${(+q.iso_frac).toFixed(2)}</span>
        </div>
        <div className="row">
          <span className="lbl">Seeds</span>
          <input data-ctl="seeds" type="number" min="10" max="2000" step="10" value=${q.seeds}
                 onChange=${(e) => set({ seeds: +e.target.value })} />
        </div>
      </div>
    </div>`
}

function Hud({ info, stats }) {
  if (!info) return html`<div className="hud">loading…</div>`
  const { bytes, wireBytes, fetchMs, decodeMs, buildMs, header } = info
  const mb = (n) => (n / 1024 / 1024).toFixed(2)
  return html`
    <div className="hud">
      <div><b>${wireBytes ? mb(wireBytes) : mb(bytes)} MB</b> on the wire
        ${wireBytes ? html`<span> (${mb(bytes)} MB raw)</span>` : ''}
        · server <b>${header.serverMs.total} ms</b>
        · fetch <b>${fetchMs.toFixed(0)} ms</b>
        · decode <b>${decodeMs.toFixed(1)} ms</b>
        · upload <b>${buildMs.toFixed(1)} ms</b></div>
      <div>${header.parts.map((p) => `${p.name} ${p.counts.primitives.toLocaleString()}`).join(' · ')}</div>
      <div><b>${stats.fps} fps</b> · ${stats.triangles.toLocaleString()} tris ·
        ${stats.calls} draw call${stats.calls === 1 ? '' : 's'}
        · ${header.field}/${header.component}</div>
    </div>`
}

function Legend({ preset, range }) {
  // The bar is a CSS gradient built from the same LUT bytes the shader samples.
  return html`
    <div className="legend">
      <div className="ticks">
        <span>${(+range[1]).toPrecision(3)}</span>
        <span>${((+range[0] + +range[1]) / 2).toPrecision(3)}</span>
        <span>${(+range[0]).toPrecision(3)}</span>
      </div>
      <div className="bar" id="legend-bar"></div>
    </div>`
}

function App() {
  const stageRef = useRef(null)
  const viewerRef = useRef(null)
  const lutCache = useRef(new Map())
  const inflight = useRef(null)
  const [meta, setMeta] = useState(null)
  const [q, setQ] = useState({
    case: new URLSearchParams(location.search).get('case') || '',
    component: 'magnitude',
    slice_axis: 'z',
    slice_frac: 0.5,
    iso_frac: 0.5,
    seeds: 200,
  })
  const [visible, setVisible] = useState({})
  const [preset, setPreset] = useState('coolwarm')
  const [range, setRangeState] = useState([0, 1])
  const [surface, setSurfaceState] = useState({ opacity: 1, cull: true })
  const [info, setInfo] = useState(null)
  const [stats, setStats] = useState({ fps: 0, triangles: 0, calls: 0 })
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    viewerRef.current = new Viewer(stageRef.current)
    viewerRef.current.setSurfaceStyle(surface)
    window.__spikeGrab = () => viewerRef.current.grab()  // see Viewer.grab
    // Test hook: camera/target/up, so check_browser.py can assert the turntable
    // behaviour (azimuth about +Z, no roll, target held).
    window.__spikeCamera = () => {
      const { camera, controls } = viewerRef.current
      return {
        position: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
        target: { x: controls.target.x, y: controls.target.y, z: controls.target.z },
        up: { x: camera.up.x, y: camera.up.y, z: camera.up.z },
      }
    }
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      if (viewerRef.current) setStats(viewerRef.current.stats())
    }, 500)
    return () => clearInterval(id)
  }, [])

  // Colour map: fetched once per preset (768 bytes), then cached. Switching is
  // a texture upload -- no geometry request.
  useEffect(() => {
    let cancelled = false
    const apply = (rgb) => { if (!cancelled) viewerRef.current?.setLut(rgb) }
    const cached = lutCache.current.get(preset)
    if (cached) { apply(cached); return undefined }
    fetch(`/api/lut?name=${preset}`).then((r) => r.arrayBuffer()).then((b) => {
      const rgb = new Uint8Array(b)
      lutCache.current.set(preset, rgb)
      apply(rgb)
    })
    return () => { cancelled = true }
  }, [preset])

  useEffect(() => {
    const bar = document.getElementById('legend-bar')
    const rgb = lutCache.current.get(preset)
    if (!bar || !rgb) return
    const stops = []
    for (let i = 0; i < 256; i += 16) {
      stops.push(`rgb(${rgb[i * 3]},${rgb[i * 3 + 1]},${rgb[i * 3 + 2]}) ${(i / 255) * 100}%`)
    }
    bar.style.background = `linear-gradient(to top, ${stops.join(',')})`
  }, [preset, info])

  // Metadata (fields, times, bounds) -- one request per case.
  useEffect(() => {
    fetch('/api/cases').then((r) => r.json()).then(({ cases }) => {
      const name = q.case && cases.includes(q.case) ? q.case : cases[0]
      return fetch(`/api/meta?case=${encodeURIComponent(name)}`)
        .then((r) => r.json())
        .then((m) => {
          setMeta({ ...m, cases })
          setQ((s) => ({ ...s, case: name, field: s.field || m.colorField }))
        })
    }).catch((e) => setError(String(e)))
  }, [q.case])

  // The geometry request. Every dependency here is a "server" control.
  const fetchScene = useCallback(async () => {
    if (!meta || !q.field) return
    // One extraction at a time: abandon whatever is still in flight rather than
    // letting requests stack up behind it. Deferred sliders stop a drag from
    // queueing 20 of these; this stops the selects and number inputs from
    // queueing the rest, and means the newest request always wins.
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    setBusy(true)
    const params = new URLSearchParams({
      case: q.case,
      field: q.field,
      component: q.component,
      parts: PARTS.map(([k]) => k).join(','),
      slice_axis: q.slice_axis,
      slice_frac: q.slice_frac,
      iso_frac: q.iso_frac,
      seeds: q.seeds,
    })
    if (q.time_index !== undefined) params.set('time_index', q.time_index)
    try {
      const t0 = performance.now()
      const response = await fetch(`/api/scene?${params}`, { signal: controller.signal })
      if (!response.ok) throw new Error(await response.text())
      const buffer = await response.arrayBuffer()
      const t1 = performance.now()
      // What actually crossed the wire, not what we unpacked: the response is
      // gzipped (see server.py), and on a big case the compressed size is ~1/4
      // of the byte length. Reporting byteLength alone slanders the format.
      const entry = performance.getEntriesByType('resource')
        .reverse().find((e) => e.name.includes('/api/scene'))
      const wireBytes = entry?.encodedBodySize || 0
      const scene = decodeScene(buffer)
      const t2 = performance.now()
      const header = viewerRef.current.setScene(scene, visible)
      const t3 = performance.now()
      if (!info) viewerRef.current.frameAll(header.bounds)
      setRangeState(header.range)
      setInfo({
        bytes: buffer.byteLength,
        wireBytes,
        fetchMs: t1 - t0,
        decodeMs: t2 - t1,
        buildMs: t3 - t2,
        header,
      })
      setError(null)
    } catch (e) {
      if (e.name === 'AbortError') return  // superseded by a newer request
      setError(String(e))
    } finally {
      if (inflight.current === controller) {
        inflight.current = null
        setBusy(false)
      }
    }
  }, [meta, q.case, q.field, q.component, q.slice_axis, q.slice_frac, q.iso_frac, q.seeds, q.time_index])

  useEffect(() => { fetchScene() }, [fetchScene])

  const setRange = (r) => { setRangeState(r); viewerRef.current?.setRange(r[0], r[1]) }
  const setSurface = (v) => { setSurfaceState(v); viewerRef.current?.setSurfaceStyle(v) }

  return html`
    <div className="app">
      <${Panel} meta=${meta} q=${q} set=${(patch) => setQ((s) => ({ ...s, ...patch }))}
        visible=${visible}
        toggle=${(k, on) => { setVisible((v) => ({ ...v, [k]: on })); viewerRef.current?.setVisible(k, on) }}
        preset=${preset} setPreset=${setPreset}
        range=${range} setRange=${setRange}
        surface=${surface} setSurface=${setSurface}
        onRescale=${() => info && setRange(info.header.range)} />
      <div className="stage" ref=${stageRef}>
        <${Hud} info=${info} stats=${stats} />
        <${Legend} preset=${preset} range=${range} />
        ${busy && html`<div className="busy">extracting…</div>`}
        ${error && html`<div className="busy error">${error}</div>`}
      </div>
    </div>`
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`)
window.__spikeReady = true
