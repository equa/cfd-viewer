import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { FRAG, FRAG_LINE, VERT, VERT_LINE } from './shaders.js'

/*
 * The pixels. Everything above this file is React and knows no three.js;
 * everything in it is three.js and knows no CFD.
 *
 * That boundary is deliberate and worth keeping: it is what would let
 * react-three-fiber replace this class without touching a single control, and
 * it is what keeps the measured hot path (decode straight into BufferAttributes,
 * shared uniform objects mutated in place) out of a React reconciler. React
 * never owns a THREE object; it calls methods here.
 */

// Per-part draw order, low first. Transparency here is unsorted (no depth
// peeling, no OIT), so putting the big translucent shell last is what stops it
// erasing what is inside it.
const RENDER_ORDER = {
  geometry: 0, slice: 1, iso: 2, stream: 3, glyph: 4, boundary: 5,
}

const TRIAD_COLOURS = [0xe64d4d, 0x66d966, 0x598cf2]  // X red, Y green, Z blue
const AXES = ['x', 'y', 'z']

export class Viewer {
  constructor(container) {
    this.container = container
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      // Needed for toDataURL() to see anything: without it the colour buffer is
      // undefined after the frame is presented, and a screenshot comes back
      // blank on some drivers even when rendered in the same tick.
      preserveDrawingBuffer: true,
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    // Straight-through colour: the LUT bytes are the same matplotlib samples
    // VTK writes into its transfer function, so any output conversion would make
    // the two renderers disagree on colour for no reason.
    this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace
    container.appendChild(this.renderer.domElement)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1e6)
    // Z is up in CFD, and OrbitControls *is* a turntable: it orbits the target
    // while maintaining `object.up`, so azimuth spins about world +Z and the
    // horizon never rolls. Two traps. `camera.up` must be set BEFORE
    // constructing the controls -- the constructor bakes it into a quaternion
    // and never re-reads it -- and the start position must be off-axis, since
    // sitting on the pole leaves azimuth undefined.
    this.camera.up.set(0, 0, 1)
    this.camera.position.set(3, -3, 2)
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.12
    // three.js defaults to middle=dolly, right=pan; VTK (so ParaView, so the
    // Trame app) is middle=pan, right=dolly. Match what people already use.
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.DOLLY,
    }

    this.lut = new THREE.DataTexture(new Uint8Array(256 * 4), 256, 1, THREE.RGBAFormat)
    this.lut.minFilter = THREE.LinearFilter
    this.lut.magFilter = THREE.LinearFilter
    this.lut.wrapS = THREE.ClampToEdgeWrapping
    this.lut.wrapT = THREE.ClampToEdgeWrapping
    // uLut, uRange, uBands and uOpacityMap are shared *objects*, so a colour-map
    // or range change is one assignment for the whole scene. Opacity and
    // lighting are per material: the shell fades while the slice stays solid,
    // and the slice is drawn unlit.
    this.shared = {
      uLut: { value: this.lut },
      uRange: { value: new THREE.Vector2(0, 1) },
      uBands: { value: 0 },
      uOpacityMap: { value: 0 },
    }
    this.opacityMap = false
    this.lighting = { ambient: 0.35, diffuse: 0.65, lightKit: true }
    this.meshes = new Map()      // part name -> Mesh | LineSegments
    this.edges = new Map()       // part name -> LineSegments of mesh edges
    this.styles = new Map()      // part name -> { opacity, cull, edges }
    this.bounds = null
    this.light = false           // light theme?
    this.frames = 0
    this.fps = 0

    this._buildTriad()
    this._buildOutline()
    this.setTheme(false)
    this._resize()
    this._observer = new ResizeObserver(() => this._resize())
    this._observer.observe(container)
    this._tick()
  }

  dispose() {
    this._observer?.disconnect()
    this._disposeAll()
    this.controls.dispose()
    this.renderer.dispose()
    this.renderer.domElement.remove()
  }

  // -- frame loop -------------------------------------------------------

  _resize() {
    const { clientWidth: w, clientHeight: h } = this.container
    if (!w || !h) return
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }

  _tick() {
    this._raf = requestAnimationFrame(() => this._tick())
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    this.frames += 1
    const now = performance.now()
    if (!this._t0) this._t0 = now
    if (now - this._t0 >= 500) {
      this.fps = Math.round((this.frames * 1000) / (now - this._t0))
      this.frames = 0
      this._t0 = now
    }
  }

  stats() {
    const r = this.renderer.info.render
    return { fps: this.fps, triangles: r.triangles, calls: r.calls }
  }

  // -- materials --------------------------------------------------------

  _material(mode, { unlit = false } = {}) {
    const uniforms = {
      ...this.shared,
      uOpacity: { value: 1 },
      uAmbient: { value: unlit ? 1 : this.lighting.ambient },
      uDiffuse: { value: unlit ? 0 : this._diffuse() },
    }
    if (mode === 'lines') {
      return new THREE.ShaderMaterial({
        uniforms, vertexShader: VERT_LINE, fragmentShader: FRAG_LINE,
      })
    }
    return new THREE.ShaderMaterial({
      uniforms: {
        ...uniforms,
        uColored: { value: 1 },
        uFlat: { value: new THREE.Color(0.72, 0.75, 0.80) },
      },
      vertexShader: VERT,
      fragmentShader: FRAG,
      side: THREE.DoubleSide,
    })
  }

  /* A part with no scalars (the building geometry) has nothing to look up, so
   * it gets a plain neutral material -- light lines on a dark background and
   * vice versa, the same inversion pipeline.set_theme does server-side. */
  _neutralMaterial() {
    return new THREE.LineBasicMaterial({ color: this._neutralColour(), transparent: true })
  }

  _neutralColour() { return this.light ? 0x3a4048 : 0xd9dceb }

  _diffuse() { return this.lighting.lightKit ? this.lighting.diffuse : 0 }

  // -- parts ------------------------------------------------------------

  /* Replace only the parts in this payload, keep the rest.
   *
   * This is what makes the cut-plane slider cheap: a release refetches `slice`
   * (tens of kB) and the 15 MB boundary already on the GPU is untouched. The
   * Trame path re-executes and re-serialises the whole filter graph instead,
   * because its unit of work is the scene, not the part. */
  setParts({ header, parts }, { visible = {}, styles = {} } = {}) {
    for (const part of parts) {
      this._removePart(part.name)
      const geometry = new THREE.BufferGeometry()
      for (const [name, attr] of Object.entries(part.attributes)) {
        geometry.setAttribute(name, new THREE.BufferAttribute(attr.array, attr.components))
      }
      geometry.setIndex(new THREE.BufferAttribute(part.index, 1))

      const scalars = 'scalar' in part.attributes
      let object
      if (part.mode === 'lines') {
        object = new THREE.LineSegments(
          geometry, scalars ? this._material('lines') : this._neutralMaterial(),
        )
      } else {
        // A cut plane is read quantitatively against the colour bar, so shading
        // it only corrupts the reading -- ambient 1.0 is flat colour, the same
        // call the Trame app makes with slice_actor.LightingOff(). It matters
        // most with bands on, where a lit band is no longer one colour.
        object = new THREE.Mesh(geometry, this._material('triangles', {
          unlit: part.name === 'slice',
        }))
      }
      object.name = part.name
      object.renderOrder = RENDER_ORDER[part.name] ?? 1
      object.visible = visible[part.name] !== false
      this.meshes.set(part.name, object)
      this.scene.add(object)
      this.setStyle(part.name, styles[part.name] || this.styles.get(part.name) || {})
    }
    if (header.range) this.setRange(header.range[0], header.range[1])
    return header
  }

  /* Drop a part entirely -- what an eye toggle does when it goes off. The
   * geometry leaves the GPU, so hiding the boundary on a big case gives the
   * memory back rather than just skipping the draw. */
  dropPart(name) { this._removePart(name) }

  /* Drop every part. Used on a case switch: the incoming case may legitimately
   * return nothing for a part the outgoing one had (no building OBJ, an empty
   * isosurface), and without this that stale geometry would sit in the new
   * case's scene looking like real data. */
  dropAll() { this._disposeAll() }

  _removePart(name) {
    const mesh = this.meshes.get(name)
    if (mesh) {
      this.scene.remove(mesh)
      mesh.geometry.dispose()
      mesh.material.dispose()
      this.meshes.delete(name)
    }
    this._removeEdges(name)
  }

  _removeEdges(name) {
    const edge = this.edges.get(name)
    if (edge) {
      this.scene.remove(edge)
      edge.geometry.dispose()
      edge.material.dispose()
      this.edges.delete(name)
    }
  }

  _disposeAll() {
    for (const name of [...this.meshes.keys()]) this._removePart(name)
  }

  has(name) { return this.meshes.has(name) }

  setVisible(name, on) {
    const mesh = this.meshes.get(name)
    if (mesh) mesh.visible = on
    const edge = this.edges.get(name)
    if (edge) edge.visible = on && !!this.styles.get(name)?.edges
  }

  /* Per-part appearance: all GPU state, no refetch.
   *
   * `cull` renders back faces only -- the same trick as the Trame app's "cull
   * near walls" (VTK FrontfaceCulling), and the reason you can see into a room
   * without making its walls transparent. */
  setStyle(name, style) {
    const merged = {
      opacity: 1, cull: false, edges: false, colored: true,
      ...(this.styles.get(name) || {}), ...style,
    }
    this.styles.set(name, merged)
    const mesh = this.meshes.get(name)
    if (!mesh) return
    const u = mesh.material.uniforms
    if (u?.uOpacity) u.uOpacity.value = merged.opacity
    else mesh.material.opacity = merged.opacity
    // "Colour by field" off = a flat neutral shell, so the slice inside it
    // reads. A uniform, not a material swap: the Trame app turns the mapper's
    // ScalarVisibility off, which here would mean rebuilding the material.
    if (u?.uColored) u.uColored.value = merged.colored ? 1 : 0
    if (mesh.isMesh) mesh.material.side = merged.cull ? THREE.BackSide : THREE.DoubleSide
    this._applyBlending(mesh)
    this._applyEdges(name, merged.edges)
  }

  /* Mesh edges, client-side.
   *
   * `wireframe: true` would draw the *triangulation*, not the mesh -- every
   * quad crossed by a diagonal. EdgesGeometry suppresses edges between coplanar
   * faces, so a triangulated quad loses its diagonal and the real cell grid
   * comes back. The honest caveat: that only holds on flat cells. On a smoothly
   * curved surface genuine mesh edges are near-coplanar and vanish too, which
   * is why the *slice* gets its edges from the server's crinkle extraction
   * instead of from here. Room walls are flat, so for the shell this is right
   * and free -- no round trip, where the Trame path had none either (it was an
   * actor property, which three.js has no equivalent of). */
  _applyEdges(name, on) {
    const mesh = this.meshes.get(name)
    if (!on || !mesh?.isMesh) { this._removeEdges(name); return }
    if (this.edges.has(name)) { this.edges.get(name).visible = mesh.visible; return }
    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry, 1),
      new THREE.LineBasicMaterial({ color: this.light ? 0x9aa1ad : 0x40464f }),
    )
    edge.name = `${name}-edges`
    edge.renderOrder = (RENDER_ORDER[name] ?? 1) + 0.5
    edge.visible = mesh.visible
    this.edges.set(name, edge)
    this.scene.add(edge)
  }

  /* A material only respects alpha when `transparent` is set, and a
   * half-transparent fragment that writes depth hides what is behind it. Every
   * opacity change, every new mesh and the opacity-map toggle all have to run
   * through here, or transparency silently does nothing. */
  _applyBlending(mesh) {
    const opacity = mesh.material.uniforms?.uOpacity?.value ?? mesh.material.opacity ?? 1
    const solid = opacity >= 0.999 && !this.opacityMap
    mesh.material.transparent = !solid
    mesh.material.depthWrite = solid
    mesh.material.needsUpdate = true
  }

  // -- colour -----------------------------------------------------------

  setRange(lo, hi) { this.shared.uRange.value.set(lo, hi) }

  /* 0 (or 1) = smooth ramp, N = N discrete bands. One uniform for the whole
   * scene: instant, no geometry, no LUT rebuild, no round trip. */
  setBands(n) { this.shared.uBands.value = Number(n) || 0 }

  setOpacityMap(on) {
    this.opacityMap = !!on
    this.shared.uOpacityMap.value = on ? 1 : 0
    for (const mesh of this.meshes.values()) this._applyBlending(mesh)
  }

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

  setLighting({ ambient, diffuse, lightKit }) {
    this.lighting = {
      ambient: ambient ?? this.lighting.ambient,
      diffuse: diffuse ?? this.lighting.diffuse,
      lightKit: lightKit ?? this.lighting.lightKit,
    }
    for (const [name, mesh] of this.meshes) {
      const u = mesh.material.uniforms
      if (!u?.uAmbient) continue
      if (name === 'slice') continue  // stays unlit -- see _material()
      u.uAmbient.value = this.lighting.ambient
      u.uDiffuse.value = this._diffuse()
    }
  }

  /* The 3D viewport is not CSS, so the theme has to reach the renderer: the
   * background, and the neutral geometry line colour inverted (light lines on
   * dark, dark on light). Field-coloured parts need no change. */
  setTheme(light) {
    this.light = !!light
    this.scene.background = new THREE.Color(light ? 0xf2f3f5 : 0x16181d)
    for (const [name, mesh] of this.meshes) {
      if (!mesh.material.uniforms && mesh.material.color) {
        mesh.material.color.setHex(this._neutralColour())
      }
      const edge = this.edges.get(name)
      if (edge) edge.material.color.setHex(light ? 0x9aa1ad : 0x40464f)
    }
  }

  // -- the red cut-plane outline ----------------------------------------

  /*
   * The frame that previews the cut while the position slider is dragged.
   *
   * Here it is thirty lines of ordinary three.js: a rectangle spanning the two
   * non-normal axes, moved along the normal by setting `position`. Worth
   * recording what this replaces -- in the Trame path the same feature needed a
   * declarative vtk.js child injected into the view's context, mounted ONLY
   * during a drag because the client renderer is transiently null after a view
   * rebuild and the library's onMounted has no null guard. None of that exists
   * here: there is one renderer, it is ours, and the outline is just an object
   * in the scene.
   */
  _buildOutline() {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3))
    geometry.setIndex([0, 1, 1, 2, 2, 3, 3, 0])
    this.outline = new THREE.LineSegments(
      geometry,
      new THREE.LineBasicMaterial({ color: 0xe63333 }),
    )
    this.outline.name = 'plane-outline'
    this.outline.renderOrder = 10
    this.outline.visible = false
    this.outline.frustumCulled = false
    this.scene.add(this.outline)
  }

  /* Corners for the current normal: a rectangle over the full domain in the two
   * non-normal axes, at coordinate 0 on the normal -- `position` supplies the
   * coordinate as the slider moves, so a drag touches nothing but a Vector3. */
  setOutlineAxis(axis, bounds = this.bounds) {
    if (!bounds) return
    const ai = AXES.indexOf(axis)
    const others = [0, 1, 2].filter((i) => i !== ai)
    const span = others.map((i) => [bounds[2 * i], bounds[2 * i + 1]])
    const positions = this.outline.geometry.attributes.position.array
    const corners = [[0, 0], [1, 0], [1, 1], [0, 1]]
    corners.forEach(([u, v], k) => {
      const corner = [0, 0, 0]
      corner[others[0]] = span[0][u]
      corner[others[1]] = span[1][v]
      positions.set(corner, k * 3)
    })
    this.outline.geometry.attributes.position.needsUpdate = true
    this.outline.geometry.computeBoundingSphere()
    this._outlineAxis = axis
  }

  moveOutline(coord) {
    const ai = AXES.indexOf(this._outlineAxis || 'z')
    const at = [0, 0, 0]
    at[ai] = coord
    this.outline.position.set(...at)
  }

  showOutline(on) { this.outline.visible = !!on }

  // -- camera -----------------------------------------------------------

  frameAll(bounds) {
    this.bounds = bounds
    const [x0, x1, y0, y1, z0, z1] = bounds
    const centre = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    const diagonal = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 1
    this.controls.target.copy(centre)
    this.camera.near = diagonal / 1000
    this.camera.far = diagonal * 100
    // Front-right and slightly above, in a Z-up world: the standard CFD
    // three-quarter view, and well clear of the pole.
    this.camera.position.copy(centre).add(
      new THREE.Vector3(1, -1, 0.6).normalize().multiplyScalar(diagonal * 1.6),
    )
    this.camera.updateProjectionMatrix()
    this.controls.update()
    this._placeTriad(bounds, diagonal)
  }

  /* Look along a named axis, or the iso three-quarter view. Z is up -- these are
   * building rooms -- so every view keeps +Z up except the top/bottom pair,
   * where it cannot be and falls back to +Y. Camera only: no round trip, where
   * the Trame path had to set the server camera and then push it to the client
   * (and reset_camera alone silently did nothing, which is why its view buttons
   * appeared broken for a while). */
  setView(direction) {
    if (!this.bounds) return
    const [x0, x1, y0, y1, z0, z1] = this.bounds
    const centre = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    const diagonal = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 1
    let offset
    let up = new THREE.Vector3(0, 0, 1)
    if (direction === 'iso') {
      offset = new THREE.Vector3(1, -1, 0.6).normalize()
    } else {
      const sign = direction.startsWith('-') ? -1 : 1
      const axis = direction.slice(-1)
      const vec = [0, 0, 0]
      vec[AXES.indexOf(axis)] = sign
      offset = new THREE.Vector3(...vec)
      if (axis === 'z') up = new THREE.Vector3(0, 1, 0)
    }
    this.camera.up.copy(up)
    // OrbitControls caches `up` as a quaternion at construction, so a view that
    // changes it has to hand the controls the new one explicitly.
    this.controls.object.up.copy(up)
    this.controls.target.copy(centre)
    this.camera.position.copy(centre).add(offset.multiplyScalar(diagonal * 1.6))
    this.camera.updateProjectionMatrix()
    this.controls.update()
  }

  /* Centre of rotation from a point under the cursor (ParaView's F).
   *
   * A raycast against what is already on the GPU: no picker, no server, no
   * round trip. The Trame path had to ship display coordinates to Python, run a
   * vtkCellPicker against an offscreen window resized to match the client
   * canvas, reposition the camera and push it back -- because vtk.js orbits the
   * focal point and the geometry lived on the server. Here the geometry is
   * local, so this is four lines and instant. */
  pickCentre(clientX, clientY) {
    const rect = this.renderer.domElement.getBoundingClientRect()
    if (!rect.width || !rect.height) return false
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    )
    const raycaster = new THREE.Raycaster()
    raycaster.setFromCamera(ndc, this.camera)
    const targets = [...this.meshes.values()].filter((m) => m.visible)
    const hit = raycaster.intersectObjects(targets, false)[0]
    if (!hit) return false
    // Slide the camera so the picked point lands at screen centre, preserving
    // view direction and distance: the scene pans, it does not tilt or zoom.
    const shift = hit.point.clone().sub(this.controls.target)
    this.controls.target.add(shift)
    this.camera.position.add(shift)
    this.controls.update()
    return true
  }

  // -- orientation triad ------------------------------------------------

  /* Three RGB axis arrows, as ordinary geometry at the domain corner nearest
   * the default camera -- at the far corner an opaque slice hides two of the
   * three. Same colours and same anchor as the Trame app, so the legend's X/Y/Z
   * key reads the same in both. */
  _buildTriad() {
    this.triad = new THREE.Group()
    this.triad.name = 'triad'
    TRIAD_COLOURS.forEach((colour, i) => {
      const geometry = new THREE.CylinderGeometry(0.03, 0.03, 1, 12)
      geometry.translate(0, 0.5, 0)  // base at the origin, pointing +Y
      const cone = new THREE.ConeGeometry(0.09, 0.25, 12)
      cone.translate(0, 1.1, 0)
      const arrow = new THREE.Group()
      const material = new THREE.MeshBasicMaterial({ color: colour })
      arrow.add(new THREE.Mesh(geometry, material), new THREE.Mesh(cone, material))
      // Rotate the +Y-aligned arrow onto its own axis.
      if (AXES[i] === 'x') arrow.rotation.z = -Math.PI / 2
      if (AXES[i] === 'z') arrow.rotation.x = Math.PI / 2
      this.triad.add(arrow)
    })
    this.scene.add(this.triad)
  }

  _placeTriad(bounds, diagonal) {
    const [, x1, y0, , , z1] = bounds
    this.triad.position.set(x1, y0, z1)
    this.triad.scale.setScalar(0.1 * diagonal)
  }

  setTriadVisible(on) { this.triad.visible = !!on }

  // -- output -----------------------------------------------------------

  /* Render and read the canvas in the same tick. With preserveDrawingBuffer the
   * colour buffer survives the present, so this is reliable rather than
   * driver-dependent. */
  screenshot() {
    this.renderer.render(this.scene, this.camera)
    return this.renderer.domElement.toDataURL('image/png')
  }

  /* Test hook (tests/check_client.py): how many distinct colours are actually
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
    // Reference the background by READING A CORNER PIXEL, not by asking the
    // scene for its colour: Color.getHex() colour-manages its output, so it does
    // not match the framebuffer bytes -- a check built on it read 0% both ways
    // and looked green. A check that cannot fail is worse than no check.
    const clear = (pixels[0] << 16) | (pixels[1] << 8) | pixels[2]
    let background = 0
    for (let i = 0; i < pixels.length; i += 4) {
      const rgb = (pixels[i] << 16) | (pixels[i + 1] << 8) | pixels[i + 2]
      seen.add(rgb)
      if (rgb === clear) background += 1
    }
    return { colours: seen.size, background: background / (w * h), width: w, height: h }
  }

  camera_state() {
    return {
      position: this.camera.position.toArray(),
      target: this.controls.target.toArray(),
      up: this.camera.up.toArray(),
    }
  }
}
