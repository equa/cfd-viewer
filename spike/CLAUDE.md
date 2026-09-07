# three.js spike — notes for future sessions

React + three.js client over the **unchanged** `foamviz` VTK pipeline. Read
`spike/README.md` first: it has how to run it, the wire format, and every
measured number. This file records what cost time to discover, why things are
the way they are, and what to do next.

## Status (2026-09-07)

Works and is verified: 22 headless-Chromium checks (`spike/check_browser.py`)
and a server-side bench (`spike/bench.py`), both green on VTK 9.7, three 0.185.1,
React 18.3.1. Screenshots land in `/tmp/spike-shots`.

Implemented: case + time step, colour field + component, colour map preset,
manual/auto range, **bands**, **colour-map-weighted opacity**, part visibility,
boundary opacity + near-wall culling, cut plane (axis + position), one
isosurface, streamlines as lines, Z-up turntable navigation, deferred sliders,
a measurement HUD and a banded legend.

The three numbers that matter: 588 k triangles cost **0.7 ms** of JavaScript
(decode + upload); s2's full scene is **3.9 MB gzipped** (16.2 MB raw); and
`vtkStreamTracer` is **2.6 s of the 3.1 s** server time. The client is not the
bottleneck, and never was.

## Reflections — what this does and does not settle

**Settled.** The CFD pipeline is renderer-agnostic. `case.py` and `pipeline.py`
were imported unchanged; the only addition to app code was `colors.rgb_table()`.
Whatever front end wins, that work survives — only `app.py` (the Trame UI) is
tied to Trame.

**Settled.** The browser side of this architecture is free. Any argument for or
against three.js has to be made on team, tooling and feature grounds.

**Genuinely better here than in the Trame path**, all measured, all
zero-round-trip: colour bands (823 → 21 distinct pixel colours, one uniform),
colour-map-weighted opacity (which FoamViz had to revert — the discretizable CTF
does not serialise to vtk.js), and Z-up turntable navigation (not available in
trame-vtk 2.11.15 local mode). The pattern: anything that is a *transfer
function* in VTK is a *uniform* in a shader, and uniforms are instant.

**Not settled.** Nothing here shows a performance reason to prefer this over the
vtk.js path already in production — that one also handles 12 M cells and brings
picking, volume rendering and a scalar bar for free. What this spike really buys
is control: the appearance pipeline is ours, and the UI is ordinary React, so
the cockpit and the viewer can share components and tokens instead of being an
iframe with its own look. That was the original motivation and it holds up.

**The cost of that control**, and it is not small: every appearance setting the
shader owns is a setting the server no longer knows. Bands and opacity are
already in that bucket, so a server-rendered report PNG would not match the
interactive view unless the settings travel with the render request. The
report-figures work sits exactly on this seam — decide it deliberately.

## The boundary that defines the design

This split *is* the architecture. Keep the `server`/`client` tags in the UI: they
make the cost visible while using it, and they caught more than one wrong
assumption already.

| Client (uniform or GPU state — instant, no fetch) | Server (re-extraction — a round trip) |
|---|---|
| colour map, colour range, bands, opacity mapping | colour **field** and component (scalars are baked per field) |
| per-part visibility, boundary opacity, near-wall culling | cut-plane position/axis, isovalue, seed count |
| camera, lighting, theme, legend | time step, patch selection, tube vs line |

`check_browser.py` asserts both halves — zero `/api/scene` requests for each
client control, exactly one for a slider release.

## Traps hit here

**htm + React is not htm + Preact.** `class` must be `className` (54 places) and
`style` must be an object, not a string. Both fail as a *blank page*, and the
minified React error ("#62") is useless — swap in `react.development.js` from
the CDN, read the real message, swap back. That cost the most time of anything
in this spike.

**three ≥ 0.17x ships a split build.** `three.module.js` re-exports
`./three.core.js`; vendor only the first and you get a silent 404 and a blank
page. Both are in `fetch_vendor.sh`.

**`OrbitControls` bakes `object.up` into a quaternion in its constructor**
(vendor file line ~406) and never re-reads it. `camera.up.set(0, 0, 1)` must come
*before* `new OrbitControls(...)`, and the start position must be off-axis or
azimuth is undefined at the pole. With that done it is a proper turntable: no
roll, target held. Mouse buttons are remapped to VTK order (left rotate, middle
pan, right dolly).

**React has no "user let go" event.** `onChange` *and* `onInput` both map to the
native `input` event, which a range input fires per pixel of a drag — one sweep
queued ~20 extractions. The native `change` event **is** the release event for a
range input, so `Slider` attaches it natively (`web/app.js`). An in-flight fetch
is also aborted when a newer one starts, so selects and number inputs cannot
stack up either.

**`THREE.RGBFormat` is gone.** `DataTexture` must be RGBA; the 256×3 LUT from
the server is expanded to 256×4 on upload.

**`renderer.outputColorSpace = LinearSRGBColorSpace`** on purpose. The LUT bytes
are the same matplotlib samples VTK writes into its transfer function, so any
output conversion would make the two renderers disagree on colour for no reason.

**A uniform is not enough for alpha.** A material only respects alpha when
`transparent` is set, and a half-transparent fragment that writes depth hides
what is behind it. `_applyBlending()` owns that rule for new meshes, opacity
changes and the opacity-map toggle alike. Blending is unsorted — no depth
peeling, no OIT — and the near-zero `discard` is what keeps it looking sane.

**Screenshot-based render checks lie.** The HUD, legend and busy overlay sit on
top of the canvas, so a page screenshot of an *empty* scene comes back
colourful. `Viewer.grab()` does a `readPixels` and counts distinct colours in
the browser (shipping the pixels out through `evaluate()` would be megabytes of
JSON per call).

**`Color.getHex()` colour-manages its output**, so it does not match the
framebuffer bytes — a background-share check built on it silently read 0% both
ways and looked green. `grab()` reads a corner pixel as the reference instead.
Worth remembering as a class of bug: a check that cannot fail is worse than no
check.

**`byteLength` is not the wire size.** The response is gzipped
(`response.enable_compression()`), and on s2 the compressed size is a quarter of
the raw. The HUD reads `encodedBodySize` off the resource-timing entry.

**Playwright needs a readiness poll.** The server builds the VTK pipeline and
scans the case root before it listens — several seconds. `wait_for_server()`
polls `/api/cases` and surfaces the server's own output if it exited early.

**OpenFOAM patches are polygons, not triangles** — `vtkTriangleFilter` is
mandatory before the wire. Normals are computed server-side (`SplittingOff`, so
the point count stays 1:1 with the scalars), and skipped when the input already
has them (the isosurface arrives via `vtkPolyDataNormals`).

**Streamlines have no polyline primitive in three.js** that survives an index
buffer, so an N-point line becomes N-1 `LineSegments` index pairs: one extra
index per point, whole scene in one draw call.

## Porting the Trame UX (next session)

The Trame app is **6 tools, 77 state vars and 9 controllers**. The spike covers
maybe a third. Inventory, with where each piece has to live:

| Trame feature | Where | Notes |
|---|---|---|
| Arrows / glyphs | server | Free through the triangle path — `vtkGlyph3D` output is polydata. Just wire `parts=glyphs` and the seed source (plane grid vs isosurface). |
| Building geometry (OBJ) | server | A LINES part from `vtkFeatureEdges`; the reader is already configured by `set_case`. |
| Multiple isosurfaces | server | `contour_count` + values; the endpoint takes one `iso_frac` today. |
| Patch selection | server | `case.load(time, patches)` already supports it — pass the list. |
| Streamline tubes | server | `vtkTubeFilter` behind a `tubes=1` param; ships as triangles. |
| Mesh edges / crinkle slice | either | `wireframe: true` draws the *triangulation*, not the mesh — never use it. `THREE.EdgesGeometry(geom, smallAngle)` is better than it sounds: it suppresses edges between coplanar faces, so a triangulated quad loses its diagonal and the real quad grid comes back — but only on flat cells; on smoothly curved surfaces genuine mesh edges are near-coplanar and vanish too. Exact needs the crinkle filter / `vtkExtractEdges` as a LINES part. |
| Cell-data (flat) colouring | server | Needs non-indexed triangles with the cell scalar replicated per vertex (3× the vertex data) — decide before building it. |
| Time player | both | Each step is a refetch; see the caching note below. |
| View buttons (±X/±Y/±Z/iso) | client | Camera only. Trivial. |
| Centre-of-rotation pick (F) | client | `THREE.Raycaster` gives the world point — *easier* than FoamViz's server `pick_cor` round trip. |
| Lighting sliders, theme | client | Uniforms + CSS vars; `scene.background` for the viewport. |
| Legend title/units, opacity ramp | client | The legend bands correctly but is drawn opaque, so it over-promises with opacity mapping on. |
| Fat streamlines | client | WebGL caps `lineWidth` at 1 — the same wall FoamViz hit — but three.js `Line2`/`LineMaterial` does shader-based thick lines. A real improvement, at the cost of one more vendored module. |
| Screenshot | client | `render()` then `toDataURL()` in the same frame (or `preserveDrawingBuffer`). |
| Add to report | server | Writes into `<case>/report/`. **Decide first:** does the report PNG come from the client canvas (matches what the user sees, including bands/opacity) or from a server render (needs every appearance setting passed along)? |

Suggested order: glyphs → multiple isosurfaces → patch selection → view buttons
→ F-key pick → tubes → geometry → mesh edges → cell data → report. The first
five are cheap and cover most of what makes the Trame app feel complete.

**Decide before writing UI code:**

1. **One scene request object, and cache on it.** The payload is immutable for a
   given request signature, so an LRU of decoded parts makes going back and
   forth between time steps and slice positions *instant* — something the Trame
   path cannot do, since it re-extracts every time. This is the biggest UX win
   available and it shapes the state model, so do it first, not later.
2. **Where the UI state lives.** 77 flat state vars is what a Trame app looks
   like; React wants the server-affecting subset as one object (the request) and
   the client-affecting subset as another (appearance). The `server`/`client`
   table above is that split — use it as the state shape.
3. **The build.** This spike is deliberately build-free (vendored ESM + `htm`)
   so it measures the architecture and not a toolchain. Porting into
   `cfd-backend/frontend` means normal Vite + JSX and `npm i three`; `htm` and
   `spike/web/vendor/` go away. Do not carry the vendoring across.
4. **Boundary geometry is the wire cost** — 15 of s2's 16.2 MB raw, mostly flat
   walls. Patch selection helps immediately; `vtkQuadricDecimation` is the real
   fix. gzip made this non-urgent, not solved.

## Known gaps

- **Picking a cell** (as opposed to a point): three.js hands back a triangle
  index, not a VTK cell id. Needs a server round trip with the world coordinate.
- **Volume rendering**: nothing. vtk.js has a volume mapper; here it is a
  project.
- **Unsorted transparency**: see `_applyBlending`. ParaView uses depth peeling.
- **One shared case per process**, like FoamViz, and extraction runs on the
  aiohttp event loop — a second user blocks the first. Fine for a spike, not for
  deployment.
- **No GL is needed on this server** (it only extracts, never renders), so a
  production build could drop OSMesa, `libgl1` and the render window entirely.
  The spike still builds one because it reuses `FoamPipeline` as-is.
