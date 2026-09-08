# cfd-viewer — OpenFOAM post-processing in a browser

Serves a solved OpenFOAM case as an interactive 3-D visualisation over HTTP,
aimed at what an IDA ICE CFD backend needs: open a case, pick a field, cut a
plane through the room, see where the air actually goes.

Built directly on VTK's native `vtkOpenFOAMReader` — no intermediate conversion,
no `foamToVTK`, no ParaView install.

![default view](docs/01-default.png)

## Two front ends, one VTK pipeline

The CFD work is renderer-agnostic, and that turned out to be the durable part of
this project. `foamviz/case.py`, `pipeline.py` and `colors.py` are shared and
maintained; two front ends sit on top of them.

**The React + three.js client** (`server/` + `web/`) is where development
happens. VTK extracts geometry on the server and ships plain triangles and
scalars; the browser draws them with three.js and knows no CFD. Colouring,
banding, opacity and the camera are shader uniforms and GPU state, so they cost
no round trip at all.

**FoamViz** (`foamviz/app.py`), the original [Trame](https://kitware.github.io/trame/)
+ vtk.js app, is **resting**: kept working and kept honest about pipeline
changes, but no longer where new UX work goes. The `trame` branch is a frozen
snapshot of it, and is what the `cfd-viz` container in the `cfd-backend` stack
currently builds.

Both are kept in one tree on purpose — the moment `pipeline.py` exists in two
places it forks, and the pipeline is the asset. See `CLAUDE.md`.

## Running it

```bash
# the client, once
cd web && npm install && npm run build && cd ..

# the server (serves web/dist at /)
python main.py --data ./data --port 5003
```

Then open <http://localhost:5003/>.

For UI work, run Vite's dev server instead — it hot-reloads and proxies `/api`
to the Python process:

```bash
python main.py --data ./data --port 5003     # terminal 1
cd web && npm run dev                        # terminal 2 -> http://localhost:5173/
```

The resting Trame app:

```bash
python main.py --data ./data --trame         # :8080, opens a browser
python main.py --data ./data --trame --server # :8080, headless
```

`--data DIR` points at either a single case directory or a directory of them; a
case is anything containing `system/controlDict`. The bundled demo lives in
`data/hotRoom`.

### Deep-linking a case, and the theme

`?case=<name>` opens a specific case directly, e.g.
<http://localhost:5003/?case=hotRoom>, where `<name>` is the case directory
name. `?theme=light|dark` sets the colour scheme (default dark). Both are what
let a host application embed the viewer for one case in an `<iframe>` and have
it match the surrounding app.

### Which VTK

Plain `vtk` from PyPI, **9.4 or newer**. Since 9.4 the standard wheels carry
both an EGL and an OSMesa render window and choose one at runtime — X11 first,
then EGL, then OSMesa. VTK `dlopen`s those libraries rather than bundling them,
so a slim container needs:

```bash
apt-get install -y libosmesa6      # or: dnf install -y mesa-libOSMesa
```

A normal Linux workstation already has it. Check with:

```bash
python -c "import vtk; w=vtk.vtkRenderWindow(); w.SetOffScreenRendering(1); w.Render(); print(w.GetClassName())"
# vtkOSOpenGLRenderWindow  (or vtkEGLRenderWindow on a GPU machine)
```

**`vtk-osmesa` is a fallback, not the recommendation.** It statically bundles
OSMesa so it needs no system library, but it is not on PyPI (it lives only on
`https://wheels.vtk.org`), it stopped at **9.3.1**, and it publishes wheels only
for CPython 3.6–3.12 on Linux x86_64 and Windows — which is the usual reason
`pip install vtk-osmesa` fails. Reach for it only where you cannot install
`libosmesa6`.

Note that the three.js client **never renders on the server**; it only extracts.
A deployment that serves only that client needs no GL at all — no OSMesa, no
`libgl1`, and none of the headless-GL failure class. The render window is still
constructed today because both front ends share one `FoamPipeline`.

## What it does

| Tool | What you get |
|---|---|
| **Colour** (top bar) | Any field the case contains; magnitude or X/Y/Z for vectors; nine colour maps; automatic, percentile (1–99 %) or manual range; discrete bands; opacity weighted by value |
| **Cut plane** | An X/Y/Z-aligned plane positioned by world coordinate — the anchor for everything below. Optionally a *crinkle* slice: the true cell layer rather than a flat cut |
| **Boundary** | The room shell, neutral or field-coloured, with near-wall culling so you can see in, mesh edges, opacity, cut-away-at-plane, and per-patch read selection |
| **Isosurfaces** | 1 / 3 / 5 nested isosurfaces of the coloured field |
| **Streamlines** | RK45 integration seeded from the cut plane, as lines or tubes, coloured by the field |
| **Arrows** | Glyphs on the plane (an even grid) or on the isosurface, uniform length or scaled by magnitude |
| **Geometry** | The building outline from `constant/triSurface/building*.obj`, as feature edges or full wireframe |
| Bottom bar | Time-step player, six axis-aligned camera presets plus iso, time re-scan |
| Keyboard | `x`/`y`/`z` look down an axis (shift for the far side), `r` reframes, `f` sets the centre of rotation under the cursor |

The cut plane deliberately drives the slice, the stream seeds *and* the arrows.
For room airflow that matches how a result actually gets read: choose a plane,
then ask what the air is doing on it.

### Which controls cost a round trip

The viewer labels every control `server` or `client`, because the difference is
worth seeing while you use it:

- **client** — colour map, range, bands, opacity-by-value, per-part visibility
  and opacity, near-wall culling, shell mesh edges, camera, lighting, theme.
  These are shader uniforms or GPU state: instant, no request.
- **server** — colour *field* and component, cut-plane position, isovalues, seed
  and glyph counts, time step, patch selection, crinkle slice, tubes. These
  change what has to be extracted.

Server-side controls commit **on release**, and the heavy groups (streamline
tuning, the plane's numeric X/Y/Z fields, patch selection) sit behind an
**Apply** button, so a drag or a half-typed number never queues an extraction.

Extraction is also **per part**: moving the cut plane re-extracts the slice
(tens of kB) and leaves the boundary surface (15 MB on a 2.3 M-cell case)
untouched on the GPU. Decoded parts are cached by request signature, so going
back to a time step or slice position you have already visited is free.

## Demo case

`data/hotRoom` is OpenFOAM 13's `fluid/hotRoomBoussinesqSteady` tutorial,
refined to 40×20×40 (32 000 cells) — a 10 × 5 × 10 m room with a 1 m² patch of
floor held at 600 K. It converged in 1730 iterations and wrote 19 time
directories, so the time player has something to animate.
`constant/triSurface/building.obj` is a small added fixture (a box at the room
bounds) that exercises the Geometry tool.

Chosen as the closest tutorial analogue to an IDA ICE `HEATING` case:
buoyancy-driven room airflow with a thermal plume, steady state. To regenerate
the solution (not the OBJ):

```bash
source /opt/cfd/OpenFOAM-13/etc/bashrc
cd data/hotRoom && ./Allclean && ./Allrun
```

## Screenshots

| Streamlines seeded on the plane | Isosurfaces of speed |
|---|---|
| ![streamlines](docs/02-streamlines.png) | ![isosurfaces](docs/05-isosurfaces.png) |

## Layout

```
main.py                CLI entry point for both front ends

foamviz/case.py        vtkOpenFOAMReader wrapper: times, fields, patches, snapshots
foamviz/pipeline.py    the VTK filter graph: representations, colouring, render window
foamviz/colors.py      colour map presets, shared by the 3D view and the legend
foamviz/app.py         the RESTING Trame UI

server/scene.py        per-part extraction; PART_INPUTS declares what makes a part stale
server/wire.py         vtkPolyData -> typed arrays ("FVS1" binary format)
server/app.py          aiohttp routes; extraction runs off the event loop

web/src/viewer/        three.js: the Viewer class and the shaders (no React in here)
web/src/scene/         the state model and per-part fetching + cache (no three.js in here)
web/src/ui/            React + Mantine: top bar, side pane, tools, bottom bar, overlays

tests/                 see below
docs/                  screenshots, and the three.js spike report with its measurements
```

### Design decisions worth knowing

**Colour scalars are baked into an array.** Instead of asking the mapper for
"the magnitude of U" at render time, `pipeline.py` computes a plain scalar array
(`FoamVizColor`) and colours by that. Vector modes on a lookup table are a
render-side concept that does not survive serialisation to a browser renderer,
so baking them keeps every front end showing the same picture and makes the data
range trivially correct. It also means isosurfaces contour whatever you are
currently colouring by, which turns out to be the intuitive behaviour.

**The reader is pulled, not connected.** `FoamCase.load()` requests a time step
and hands downstream filters a concrete snapshot via `SetInputData`. Requesting
time through a connected pipeline means every downstream `Update()`
re-negotiates the time request, and it is easy to silently fall back to `t=0`.

**Patch selection is a read-time filter.** Deselecting patches stops the reader
from loading them at all rather than hiding actors, which is the part that
matters once cases get large.

**The wire format is not glTF, on purpose.** glTF's material model has no
concept of a scalar field, so carrying `|U|` per vertex would mean either baking
vertex colours server-side — which kills client-side re-colouring, the one
interaction that must never cost a round trip — or smuggling values through a
custom accessor. `server/wire.py` ships raw typed arrays that map 1:1 onto
`THREE.BufferAttribute` instead: one fetch, one `arrayBuffer()`, zero parsing.

## Tests

```bash
python tests/test_pipeline.py      # 63 checks, ~30 s, no browser — the shared pipeline
python tests/check_client.py       # 64 checks — the three.js client in real Chromium
python tests/browser_check.py      # the resting Trame app
python tests/bench.py hotRoom s2   # server-side extraction sizes and timings
```

`test_pipeline.py` checks the reader and every representation by **output
size**, not by exit status: an empty VTK filter raises nothing and renders as a
perfectly plausible blank image, so the only useful question is whether geometry
came out the other end.

`check_client.py` mostly does not ask "did it render". It asks **which controls
cause a refetch**, because that boundary is the architecture and it is the kind
of thing that decays silently — add one dependency to a hook and changing the
colour map re-extracts the mesh, with no symptom beyond the app feeling slow on
a big case. So it counts `/api/scene` requests around nearly every interaction:
zero for a colour-map switch, zero for a whole cut-plane drag, exactly one for
the release, and zero for returning to a cached time step. It also asserts the
red plane outline actually appears mid-drag by counting red pixels.

Both browser suites read rendered pixels through a `readPixels` hook in the page
rather than from a screenshot: the HUD, legend and busy overlay sit on top of
the canvas, so a page screenshot of an *empty* scene comes back colourful.

## Where this would need work for production

- **Multiple users.** One process holds one VTK pipeline and one open case.
  Extraction now runs in a worker thread behind a lock, so a long
  `vtkStreamTracer` no longer freezes the whole server, but two users still
  share one case. Concurrency needs a process per session.
- **Boundary decimation.** 15 of a 2.3 M-cell case's 16.2 MB raw payload is the
  boundary at full mesh resolution, mostly flat walls. gzip takes the whole
  payload to 24 %, which made this non-urgent rather than solved; patch
  selection helps immediately, `vtkQuadricDecimation` is the real fix.
- **Report figures.** The Trame app can write a figure into `<case>/report/`.
  Not ported yet, and it needs a decision first: does the image come from the
  client canvas (matching what the user sees, bands and opacity included) or
  from a server render (which would need every appearance setting to travel with
  the request)? See `CLAUDE.md`.
- **Comfort metrics.** The IDA ICE side cares about draught rate, PMV/PPD and
  operative temperature, none of which are OpenFOAM fields. They would be
  derived arrays computed on load — a natural extension of the same baked-array
  mechanism used for colouring.

Scale is *not* on this list: tested to 12 M cells. Note that an IDA ICE "zone"
is a room selected for analysis, not an OpenFOAM `cellZone` — cases are treated
as single-region, single-zone. Decomposed (`processor*`) cases are read via
`vtkPOpenFOAMReader` and are supported.
