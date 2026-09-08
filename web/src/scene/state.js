/*
 * The state model, split exactly along the client/server boundary.
 *
 * `request`  — everything that changes what the server must EXTRACT. A change
 *              here costs a round trip, so every control bound to it is tagged
 *              `server` in the UI and either commits on release or sits behind
 *              an Apply button.
 * `appearance` — everything the shader or the GPU owns. Instant, no fetch. The
 *              controls bound to it are live, and this is where most of the
 *              Trame app's debouncing simply disappears: bands, colour range and
 *              the opacity ramp were deferred there because they rebuilt a VTK
 *              transfer function, and here they are uniforms.
 *
 * Keeping these two objects separate is not tidiness. It is the reason a colour
 * change cannot accidentally trigger an extraction, and it maps 1:1 onto the
 * table in CLAUDE.md.
 */

/* The six tools, in the Trame app's order. One entry drives both the tool-stack
 * row and the settings panel, so the two cannot drift -- the same reason its
 * TOOLS list existed. Icons are the nearest Tabler equivalents of the mdi ones
 * it used, so the two apps stay recognisably the same product. */
// `kind` is the panel's DOMINANT cost, shown as a badge on its heading;
// individual controls that differ carry their own badge (see tools.jsx).
export const TOOLS = [
  { key: 'cutplane', part: 'slice', title: 'Cut plane', icon: 'square', kind: 'server' },
  { key: 'boundary', part: 'boundary', title: 'Boundary', icon: 'cube', kind: 'client' },
  { key: 'contour', part: 'iso', title: 'Isosurfaces', icon: 'blur', kind: 'server' },
  { key: 'stream', part: 'stream', title: 'Streamlines', icon: 'stream', kind: 'server' },
  { key: 'glyph', part: 'glyph', title: 'Arrows', icon: 'arrow', kind: 'server' },
  { key: 'geometry', part: 'geometry', title: 'Geometry', icon: 'home', kind: 'server' },
]

/* Tool key -> the scene part its eye toggle shows. Same role as the Trame app's
 * TOOL_VISIBLE, and the same reason: selecting a tool must only change which
 * settings are on screen, never what is drawn. */
export const TOOL_PART = Object.fromEntries(TOOLS.map((t) => [t.key, t.part]))
export const PART_TOOL = Object.fromEntries(TOOLS.map((t) => [t.part, t.key]))

export const COMPONENTS = [
  { value: 'magnitude', label: 'Magnitude' },
  { value: 'x', label: 'X' },
  { value: 'y', label: 'Y' },
  { value: 'z', label: 'Z' },
]

export const VIEW_BUTTONS = [
  ['+X', '+x'], ['-X', '-x'], ['+Y', '+y'], ['-Y', '-y'],
  ['+Z', '+z'], ['-Z', '-z'], ['Iso', 'iso'],
]

/* Defaults carried over from the Trame app deliberately -- they are tuned, not
 * arbitrary. The shell starts opaque but front-face culled (so you see into the
 * room without transparency) and NOT field-coloured (so the slice reads against
 * a neutral grey); the heavy representations start off. */
export function initialRequest(meta) {
  const [lo, hi] = [0, 1]
  return {
    case: meta.case,
    time_index: Math.max(meta.times.length - 1, 0),
    patches: [],                    // empty = every patch
    field: meta.colorField,
    component: 'magnitude',
    cell_data: false,
    robust: false,
    // Source of truth for the cut: the world POINT plus the normal axis. Only
    // the active axis's coordinate positions the plane; the other two are
    // remembered, so switching the normal keeps them. No fractions anywhere --
    // the Trame app reworked its way to this and it is worth inheriting.
    plane_axis: 'z',
    plane: { x: meta.centre[0], y: meta.centre[1], z: meta.centre[2] },
    surface_clip: false,
    slice_edges: false,
    contour_count: 1,
    contour_value: (lo + hi) / 2,
    contour_min: lo,
    contour_max: hi,
    vector_field: meta.vectorField,
    stream_seeds: 60,
    stream_length: 4.0,
    stream_tubes: false,
    stream_radius: 1.4,
    glyph_source: 'slice',
    glyph_count: 400,
    glyph_scale: 1.0,
    glyph_scale_by: false,
    geometry_mode: 'features',
  }
}

export function initialAppearance(theme) {
  return {
    preset: 'coolwarm',
    autoRange: true,
    range: [0, 1],
    bands: 0,
    opacityMap: false,
    theme,
    visible: {
      boundary: true, slice: true, iso: false,
      stream: false, glyph: false, geometry: false,
    },
    styles: {
      // colored:false is the Trame default -- a neutral shell, culled at the
      // near walls, with the field colour carried by the slice inside it.
      boundary: { opacity: 1, cull: true, edges: false, colored: false },
      slice: { opacity: 1 },
      iso: { opacity: 0.35 },
      stream: { opacity: 1 },
      glyph: { opacity: 1 },
      geometry: { opacity: 1 },
    },
    lighting: { ambient: 0.3, diffuse: 0.7, lightKit: true },
    triad: true,
    // Streamline comets. In `appearance`, not `request`, because the whole
    // animation is a shader uniform -- it costs no extraction and no fetch.
    // Off by default: it is a reading aid you reach for, and a moving picture
    // is the wrong default for someone taking a measurement off the screen.
    comets: { on: false, speed: 0.35, period: 0.4 },
  }
}

/* The request, flattened to the query the server understands. The plane's
 * active coordinate is resolved here, so `plane_coord` is the single number the
 * server sees and the UI keeps its three-field memory. */
export function toQuery(request) {
  const { plane, patches, ...rest } = request
  return {
    ...Object.fromEntries(Object.entries(rest).map(([k, v]) => [
      k, typeof v === 'boolean' ? (v ? '1' : '0') : v,
    ])),
    patches: (patches || []).join(','),
    plane_coord: plane[request.plane_axis],
  }
}

/* Isovalues, mirrored from the server's _contour_values so the UI can show what
 * it is about to ask for without a round trip. */
export function contourValues({ contour_count, contour_value, contour_min, contour_max }) {
  const n = Math.max(Number(contour_count) || 1, 1)
  if (n <= 1) return [Number(contour_value)]
  const lo = Number(contour_min)
  const hi = Number(contour_max)
  return Array.from({ length: n }, (_, i) => lo + ((hi - lo) * (i + 1)) / (n + 1))
}
