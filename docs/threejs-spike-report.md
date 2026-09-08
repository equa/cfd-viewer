# Spike report: VTK on the server, three.js in the browser

> **Historical, 2026-09-07.** This is the report that decided the front-end
> question; it describes `spike/`, which no longer exists — that code became
> `server/` and `web/` on `main`. The measurements and the reasoning stand and
> are the reason to keep it. For how the shipped client is built, read
> `CLAUDE.md` ("The three.js client"); the paths and run commands below are
> superseded by `README.md`.

The "SimScale split" — every CFD filter stays in Python/VTK, the browser gets
plain triangles and scalars and draws them with React + three.js. Built to
answer one question with numbers instead of opinion: **can we drop Trame and
vtk.js from the client without dropping VTK from the architecture?**

Short answer: yes, and the client side is nearly free. The costs that remain are
server-side extraction and transfer, and neither is caused by three.js.

## Run it

```sh
sh spike/fetch_vendor.sh          # once: vendors three, react, htm (pinned)
python spike/server.py --data data --port 8090
# http://localhost:8090/?case=hotRoom
```

No node, no npm, no build step: the client is ES modules plus React via `htm`
tagged templates. Deps are vendored under `spike/web/vendor/` so it also runs
with no network.

```sh
python spike/bench.py hotRoom s2     # server-side sizes and timings
python spike/check_browser.py --case s2 --out /tmp/shots   # headless browser, 22 checks
```

## What it reuses, unchanged

`foamviz/case.py` and `foamviz/pipeline.py` are imported as they are — same
reader, same decomposed-case handling, same cutter/contour/tracer, same baked
`FoamVizColor` array, same `colors.py` tables (a new `rgb_table()` serves the
LUT as 768 bytes, sampled from the same matplotlib data the VTK transfer
function uses, so both renderers show identical colours). Nothing in the CFD
pipeline had to change to serve three.js. That is the headline result: the VTK
investment is renderer-agnostic, and only `app.py` (the Trame UI) is not.

## Measured

Dev container, VTK 9.7, `data/` cases. Frame rates are headless Chromium on
**SwiftShader (software GL)** — a floor, not a prediction for real hardware.

| | hotRoom (32 k cells) | s2 (2.3 M cells) |
|---|---|---|
| wire, gzipped | **0.45 MB** | **3.89 MB** |
| wire, raw | 0.98 MB | 16.20 MB |
| server extraction | 330 ms | 3060 ms |
| fetch (localhost) | 371 ms | 3525 ms |
| JS decode | 0.5 ms | **0.3 ms** |
| GPU upload | 0.7 ms | **0.4 ms** |
| time to first frame | 0.6 s | 5.5 s |
| triangles / draw calls | 14.8 k / 4 | 609 k / 4 |
| fps, all parts (SwiftShader) | 53 | 8 |
| fps, boundary hidden | 60 | 43 |

Per-part, s2: boundary 588 k triangles / 170 ms / 15.0 MB raw · slice 10 k /
64 ms / 0.27 MB · isosurface 11 k / 35 ms / 0.29 MB · streamlines 43 k segments
/ **2649 ms** / 1.0 MB.

## Navigation and slider behaviour

**Turntable, +Z up.** `OrbitControls` *is* a turntable: it orbits the target
while maintaining `camera.up`, so azimuth spins about world +Z and the horizon
never rolls. Two gotchas: `camera.up` must be set **before** constructing the
controls (the constructor bakes it into a quaternion and never re-reads it), and
the start position must be off-axis, since sitting on the pole leaves azimuth
undefined. Mouse buttons are remapped to VTK order (left rotate, middle pan,
right dolly) so navigation matches FoamViz and ParaView rather than three.js
defaults.

Worth noting against the Trame path: turntable rotation is *not* available in
trame-vtk 2.11.15 local mode (see the main CLAUDE.md), and here it is three
lines.

**Sliders commit on release.** React's `onChange` for a range input is wired to
the native `input` event, which fires on every pixel of a drag -- so one sweep
of the cut-plane slider queued ~20 extractions, each of them seconds long on a
real case. The native `change` event *is* the release event for a range input,
so the draft value drives the UI live and only `change` triggers a refetch
(`Slider` in `web/app.js`; the listener is attached natively because React maps
both `onChange` and `onInput` to `input` and offers no "user let go" event).
On top of that, an in-flight request is aborted when a newer one starts, so the
selects and number inputs cannot stack up either and the newest request always
wins. `check_browser.py` asserts both halves: 20 drag events cause zero
requests, the release causes exactly one.

## Colour banding and opacity mapping

Not a problem -- it is *better* here, and it is worth being precise about why.
FoamViz has to bake bands into the transfer function's **nodes** (flat plateaus,
one per band) because `vtkDiscretizableColorTransferFunction` does not serialise
to vtk.js. In a shader, bands are a quantisation of the lookup coordinate:

```glsl
if (n >= 1.5) t = (min(floor(t * n), n - 1.0) + 0.5) / n;
```

Sampling the smooth 256-entry LUT at `(i+0.5)/N` returns exactly
`cmap((i+0.5)/N)` -- the same colour FoamViz bakes into plateau *i* -- so the
two renderers band identically rather than merely similarly. `uBands` is a
uniform, so the band count is a live client-side control: no re-extraction, no
LUT rebuild, no round trip. Measured on the unlit slice: 823 distinct pixel
colours smooth, 21 with 5 bands, zero `/api/scene` requests.

The slice is drawn **unlit** (ambient 1.0), the same call FoamViz makes with
`slice_actor.LightingOff()`. That matters more with bands on: a shaded band is
no longer one colour, which defeats the point of banding.

### Colour-map-weighted opacity

Reverted in FoamViz as not feasible in vtk.js local mode (the discretizable CTF
does not serialise); here it is a shader line and a checkbox, linear as
requested:

```glsl
float alpha = uOpacity * mix(1.0, t, uOpacityMap);
if (alpha < 0.01) discard;
```

Alpha follows the **unbanded** `t`, so the opacity ramp is a function of the
value the way ParaView's own opacity transfer function is, independent of how
the colours are banded. Measured on the boundary alone -- no-slip walls, so
`|U|` is ~0 across them -- the view goes from 75% empty to 96% empty, with zero
`/api/scene` requests. The result is the familiar ParaView look: the plume glows
and everything slow fades out.

The uniform is one assignment, but each material's *blending* state has to
follow it (`_applyBlending`): a material only respects alpha when `transparent`
is set, and a half-transparent fragment that writes depth hides what is behind
it.

Two honest limitations:

- **Transparency is unsorted** -- no depth peeling, no OIT -- so overlapping
  transparent surfaces can composite in the wrong order. The near-zero discard
  removes the worst of it, because the fragments that would look most obviously
  wrong are the ones that vanish. ParaView solves this properly with depth
  peeling; doing the same here is real work.
- **The legend does not show the ramp.** It bands correctly but is drawn fully
  opaque, so it currently over-promises when opacity mapping is on.

### The caveat that applies to both

Banding and opacity now live in the client, so anything rendered *server-side*
(a report PNG) does not know about them -- the band count and the opacity flag
would have to travel with the render request. In the VTK path the transfer
function carries them for free. This generalises: every appearance setting the
shader owns is a setting the server no longer knows.

## Findings

1. **The client is not the bottleneck — it is a rounding error.** 588 k
   triangles decode and upload in **0.7 ms** of JavaScript. Any argument for or
   against three.js has to be made on other grounds, because the browser side of
   this architecture costs nothing.
2. **The streamline tracer is the bottleneck**: 2.6 s of the 3.1 s server time on
   s2, and 309 ms of 330 ms on hotRoom. That is `vtkStreamTracer`, and FoamViz
   pays exactly the same price today — it is not a cost of this architecture.
   Optimising it (fewer seeds, coarser integration, or a cached seed set) helps
   both paths equally.
3. **Compress the transport and the wire problem mostly goes away.** s2 drops
   16.2 → 3.9 MB with plain gzip (24%). Normals compress to nothing on
   axis-aligned room walls (3.5 MB → 8 kB), wall scalars likewise (no-slip, so
   `|U|` is 0 nearly everywhere: 1.2 MB → 3 kB). What is left is indices
   (6.9 → 2.3 MB) and positions (3.5 → 0.8 MB).
4. **The interaction model splits cleanly, and the split is the design.**
   Colour map, colour range, per-part visibility, boundary opacity and near-wall
   culling are pure GPU state — no request, instant, verified by asserting zero
   `/api/scene` calls. Cut-plane position, isovalue, seed count, colour *field*
   and time step change what must be extracted, so they cost a round trip. The
   UI tags every control `server` or `client` so the cost is visible while you
   use it.
5. **Boundary geometry dominates the wire, and it is the obvious next lever.**
   15 of s2's 16.2 MB is the boundary surface at full mesh resolution — mostly
   flat walls. Decimation (`vtkQuadricDecimation`) or shipping only the patches
   in view should cut that by an order of magnitude. Not done here: out of scope
   for a spike, and the gzip result made it non-urgent.
6. **No GL needed on the server.** This path never renders; it only extracts. A
   production build could drop OSMesa, `libgl1` and the render window entirely
   (`FoamPipeline._build_scene`), which shrinks the image and removes the whole
   headless-GL failure class. The spike still constructs a render window because
   it reuses `FoamPipeline` as-is.
7. **Deliberately not solved:** picking (three.js gives you a triangle, not a
   VTK cell id — a server round trip with the world coordinate would be needed),
   volume rendering, and glyphs (they come free through the same triangle path —
   `vtkGlyph3D` output is polydata — just not wired into the UI).

## Wire format

Not glTF, on purpose. glTF's material model has no concept of a scalar field,
so carrying `|U|` per vertex means either baking vertex colours server-side —
which kills client-side re-colouring, the one interaction that must never cost a
round trip — or smuggling values through a custom accessor. Instead:

```
"FVS1" | uint32 header length | header JSON | buffer blobs
```

The header names every buffer by byte offset, type and component count, so the
client does one fetch, one `arrayBuffer()` and zero parsing (`decodeScene` in
`web/app.js`, `pack`/`unpack` in `wire.py`). If glTF interop matters later, add
it as a second endpoint for export; it should not be the interactive path.

## Verdict

The architecture works and the client cost is negligible. What this spike does
**not** show is any performance reason to prefer it over the vtk.js path already
in production — that one also renders 12 M-cell cases and comes with picking,
volume rendering and a scalar bar for free. Choose between them on team and
tooling grounds, not on these numbers.
