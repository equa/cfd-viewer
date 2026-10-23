# cfd-viewer — OpenFOAM post-processing in a browser

Serves a solved OpenFOAM case as an interactive 3-D visualisation over HTTP,
aimed at what an IDA ICE CFD backend needs: open a case, pick a field, cut a
plane through the room, see where the air actually goes.

Built directly on VTK's native `vtkOpenFOAMReader` — no intermediate conversion,
no `foamToVTK`, no ParaView install.

![default view](docs/01-default.png)

## How it is put together

VTK does every CFD filter on the server; the browser draws the result. The
server extracts geometry per **part** — boundary, slice, isosurface,
streamlines, arrows, geometry — and ships plain triangles, line segments and
scalars (`src/equa/cfd_viewer/`: `foamviz/` + `server/`). The client turns those into `BufferGeometry`
and colours them in a shader (`web/`), so it knows no CFD at all.

That division is the point. Colour map, range, banding, opacity, per-part
visibility, the camera and the streamline animation are shader uniforms or GPU
state, so they cost **no round trip**; only what has to be re-*extracted* does.

> **The original Trame + vtk.js front end (FoamViz) was mothballed on
> 2026-09-12** and lives on the frozen **`trame` branch**. Nothing here depends
> on it. To look at it: `git show trame:foamviz/app.py`.

## Running it

The server is the Python package `equa-cfd-viewer`, managed with
[uv](https://docs.astral.sh/uv/):

```bash
uv sync                                          # Python env from uv.lock

# the client, once (and after changes to web/)
cd web && npm ci && npm run build && cd ..

# the server (serves web/dist at /)
uv run cfd-viewer --data ./data
```

Then open <http://localhost:5003/>. With no `?case=` in the address, the viewer
opens on a **start screen** listing every case it found (label, path, cell
count, decomposed or not), with a filter; one case only opens directly.

For UI work, run Vite's dev server instead — it hot-reloads and proxies `/api`
to the Python process:

```bash
uv run cfd-viewer --data ./data              # terminal 1
cd web && npm run dev                        # terminal 2 -> http://localhost:5173/
```

The options:

| Option | Variable | Meaning |
|---|---|---|
| `--data DIR` | `CASE_ROOTS` | A directory of cases, or one case. Repeat for several roots (in the variable, separate them with `:`). Default `./data` |
| `--scan-depth N` | `SCAN_DEPTH` | Directory levels below each root to search for cases. Default 4. Cases are found at any level up to it, never inside another case, as cfd-backend finds them (e.g. its test cases in `CFD_HOME/tests/<name>`) |
| `--host`, `--port` | `PORT` | Default `0.0.0.0`, `5003` |
| `--root-path PATH` | `ROOT_PATH` | Also serve under `PATH` (`/viewer`), for a proxy that does not strip it, as equa-deployment's Traefik does. Every route then answers under it *and* at the root; `PATH` redirects to `PATH/`. Default none |
| `--mode server` | | The only mode so far, and the default: an empty `--data` is not fatal, cases are picked up as they appear. `--server` is an alias |

An option on the command line wins over its variable. The image sets
`CASE_ROOTS=/data` and `PORT=5003` and passes no options, so a platform's
settings take effect.

`python main.py ...` with `pip install -r requirements.txt` still works the old
way; that is how cfd-backend's `cfd-viz` image runs it (see
[the cockpit contract](#the-cockpit-contract)). Endpoints for tooling:
`/health`, and `/api/config` (mode, version).

`--data DIR` points at either a single case directory or a directory of them; a
case is anything containing `system/controlDict`. The bundled demo lives in
`data/hotRoom`.

### In a container

The repository's own `Dockerfile` builds the server with the client inside it,
from the lock file, on the same base and uid as cfd-backend's `cfd-viz`:

```bash
podman build --format docker --build-arg SERVICE_VERSION=0.0.0-dev -t cfd-viewer:dev .
podman run --rm -p 5003:5003 -v ./data/hotRoom:/data/hotRoom:ro,Z cfd-viewer:dev
```

`--format docker` keeps the image's `HEALTHCHECK`: Podman's default OCI format
has no health checks and drops it with a warning. `,Z` is for SELinux hosts such
as Fedora; it relabels the mounted directory.

Cases are read in place and never written to, so `/data` can be read-only.
cfd-backend still builds `cfd-viz` its own way, from a clone of this repository;
[docs/cfd-backend-switch.md](docs/cfd-backend-switch.md) describes moving it to
this image, and what can be removed here once it has.

### Deep-linking a case, and the theme

`?case=<id or name>` opens a specific case directly, e.g.
<http://localhost:5003/?case=hotRoom>. Every case has a stable 12-character
**id** (from its root and path, shown in the case switcher); its directory
**name** works too as long as exactly one case has it. An unknown or ambiguous
`?case=` is a 404, and the page falls back to the start screen with a notice,
never to a different case. `?theme=light|dark` sets the colour scheme (default
dark). Both are what let a host application embed the viewer for one case in
an `<iframe>` and have it match the surrounding app.

Cases are read **in place and never written to** (no `.foam` marker is created),
so case directories can be read-only: a network share, or a `:ro` mount.

### Which VTK

Plain `vtk` from PyPI, **9.4 or newer**, and **no GL library at all** -- not
for the server, not for the tests. The server never renders: it only extracts
geometry, and the browser draws it. `FoamPipeline` builds no render window
(VTK needs a GL backend present just to *construct* one), and since
2026-10-23 there is nothing left that renders server-side: `screenshot()`,
`write_vtkjs()` and `pick_cor()` were the Trame front end's and are gone.
`tests/test_pipeline.py` replaces `vtk.vtkRenderWindow` with a stub that
raises, so a render window cannot creep back unnoticed; cfd-backend's smoke
test checks the same from its side. Neither image (cfd-backend's `cfd-viz`,
nor this repository's) installs `libosmesa6` or `libgl1`.

If you ever do want VTK to render -- an ad-hoc script, say -- the stock wheels
carry EGL and OSMesa render windows and `dlopen` the libraries, so install
`libosmesa6` (or `mesa-libOSMesa`) first. `vtk-osmesa` is a dead end: not on
PyPI, frozen at 9.3.1, CPython 3.12 at most.

## What it does

| Tool | What you get |
|---|---|
| **Colour** (top bar) | Any field the case contains; magnitude or X/Y/Z for vectors; nine colour maps; automatic, percentile (1–99 %) or manual range; discrete bands; opacity weighted by value |
| **Cut plane** | An X/Y/Z-aligned plane positioned by world coordinate — the anchor for everything below. Optionally a *crinkle* slice: the true cell layer rather than a flat cut |
| **Boundary** | The room shell, neutral or field-coloured, with near-wall culling so you can see in, mesh edges, opacity, cut-away-at-plane, and per-patch read selection |
| **Isosurfaces** | 1 / 3 / 5 nested isosurfaces of the coloured field, or **locked** to a field of their own — so you can contour speed and colour by temperature |
| **Streamlines** | RK45 integration seeded from the cut plane, as lines or **tubes built in the browser** (so the representation and the width are instant). **Animate flow** sends comets along them at the local flow speed — which is also the only thing that shows a streamline's *direction* |
| **Arrows** | Glyphs on the plane (an even grid) or on the isosurface, uniform length or scaled by magnitude |
| **Geometry** | The building outline from `constant/triSurface/building*.obj`, as feature edges or full wireframe |
| Bottom bar | Time-step player, six axis-aligned camera presets plus iso, time re-scan |
| Keyboard | `x`/`y`/`z` look down an axis (shift for the far side), `r` reframes, `f` sets the centre of rotation under the cursor |

The cut plane deliberately drives the slice, the stream seeds *and* the arrows.
For room airflow that matches how a result actually gets read: choose a plane,
then ask what the air is doing on it.

Streamline **tubes are built in the browser** from the line data, not shipped.
`vtkTubeFilter`'s output was 9x the wire for no saving in server time (measured
on a 2.3 M-cell case: 10.30 MB gzipped against 1.13 MB) since the tracer
dominates either way. Inflating locally takes a few milliseconds, makes the
lines/tubes switch instant, and turns the width into a shader uniform — it
displaces the tube's centreline along the ring normal, so dragging it rebuilds
nothing.

The streamline animation is an animated dash pattern rather than particles — no
particle buffer, no per-frame CPU work, one uniform. It rides the transport time
`vtkStreamTracer` already computes (`IntegrationTime`), so the comets move at
the **local flow speed** and visibly rip through a plume while crawling in the
corners. Try it with **Tubes** on: on 1-px lines a comet is a short bright
segment that gets lost in a coiled tangle, on tubes it is a discrete object.
Tubes cost nothing now, so there is no reason not to.

### Which controls cost a round trip

The viewer labels every control `server` or `client`, because the difference is
worth seeing while you use it:

- **client** — colour map, range, bands, opacity-by-value, per-part visibility,
  opacity, colour-by-field and its solid-colour fallback, near-wall culling,
  shell mesh edges, the streamline animation, camera, lighting, theme. These are
  shader uniforms or GPU state: instant, no request.
- **server** — colour *field* and component, cut-plane position, isovalues, the
  isosurface's locked field, seed and glyph counts, time step, patch selection,
  crinkle slice, true cell values. These change what has to be extracted.
  Robust range and **Rescale** are the exception: they change no geometry, so
  they come from a cheap `/api/range` rather than re-extracting anything.

Server-side controls commit **on release**, and the heavy groups (streamline
tuning, the plane's numeric X/Y/Z fields, patch selection) sit behind an
**Apply** button, so a drag or a half-typed number never queues an extraction.

Extraction is also **per part**: moving the cut plane re-extracts the slice
(tens of kB) and leaves the boundary surface (15 MB on a 2.3 M-cell case)
untouched on the GPU. Decoded parts are cached by request signature, so going
back to a time step or slice position you have already visited is free.

## Demo cases

Both are versioned **with their results**, ASCII and tiny, so a fresh checkout
runs every suite without OpenFOAM. They are test fixtures, not showcases: point
`--data` at a real case to see what the viewer does at scale.

`data/hotRoom` is OpenFOAM 13's `fluid/hotRoomBoussinesqSteady` tutorial on a
coarse 10×5×10 mesh (500 cells of 1 m) -- a 10 × 5 × 10 m room with 4 m² of
floor held at 600 K. It converges at iteration 740; `purgeWrite 2` keeps the
time steps `0`, `700` and `740`, enough for the time player to step through.
About 400 kB. `constant/triSurface/building.obj` is a small added fixture (a
box at the room bounds) that exercises the Geometry tool. Chosen as the closest
tutorial analogue to an IDA ICE `HEATING` case: buoyancy-driven room airflow
with a thermal plume, steady state, and genuinely 3-D.

`data/cavity` is the lid-driven cavity for the `fluid` solver: 20×20×1 (400
cells, 2-D), times `0`, `0.5`, `1`, about 330 kB. It makes the start screen
list more than one case.

To regenerate either (OpenFOAM 13 sourced):

```bash
cd data/hotRoom && ./Allclean && ./Allrun
```

Then re-record the HTTP contract (`uv run python tools/record_contract.py`)
and review its diff: the replay compares values only while the case files
match the recording.

## Screenshots

| Streamlines seeded on the plane | Isosurfaces of speed |
|---|---|
| ![streamlines](docs/02-streamlines.png) | ![isosurfaces](docs/05-isosurfaces.png) |

## Layout

```
src/equa/cfd_viewer/
  __main__.py          the CLI: `cfd-viewer`, `python -m equa.cfd_viewer`
  foamviz/case.py      vtkOpenFOAMReader wrapper: times, fields, patches, snapshots
  foamviz/pipeline.py  the VTK filter graph: cutter, contour, tracer, glyphs, colouring
  foamviz/colors.py    colour map presets, shared by the shader LUT and the legend
  server/sources.py    where cases come from: discovery, ids, labels, cell counts
  server/scene.py      per-part extraction; PART_INPUTS declares what makes a part stale
  server/wire.py       vtkPolyData -> typed arrays ("FVS1" binary format)
  server/app.py        aiohttp routes; extraction runs off the event loop

web/src/viewer/        three.js: the Viewer class and the shaders (no React in here)
web/src/scene/         the state model and per-part fetching + cache (no three.js in here)
web/src/ui/            React + Mantine: start screen, top bar, side pane, tools, overlays

main.py, foamviz/,     shims kept for cfd-backend's cfd-viz build (see below)
requirements.txt
pyproject.toml, uv.lock, hatch_build.py   the package; the hook puts web/dist in the wheel
dodo.py                doit tasks, named as in equa-ci
Dockerfile             this repository's own image
openspec/              specs and changes (the standalone-viewer change lives here)
tests/                 see below
docs/                  screenshots, the three.js spike report, the cfd-backend switch note
```

### The cockpit contract

cfd-backend's `cfd-viz` image is built from a **clone of this repository's
`main`**: it needs `web/package.json`, `web/package-lock.json`,
`requirements.txt` and `main.py` at the root, installs with
`pip install -r requirements.txt` on Python 3.14, runs
`python main.py --server --data "$CFD_HOME" ...`, and its smoke test imports
`foamviz.pipeline` from the checkout root. So that layout is a contract, kept by
three shims (`main.py`, the top-level `foamviz/` alias and a generated
`requirements.txt`) and checked on every `doit ci` by
`tests/check_cockpit_contract.py`. Its HTTP behaviour is a contract too:
`tests/test_contract.py` replays recorded responses: the API may only grow
(fields, types and statuses are always checked), and while the demo data and
library versions match the recording, values and geometry bytes must match too. [docs/cfd-backend-switch.md](docs/cfd-backend-switch.md)
is the way out of the layout half.

### Design decisions worth knowing

**Colour scalars are baked into an array.** Instead of asking a mapper for "the
magnitude of U" at render time, `pipeline.py` computes a plain scalar array
(`FoamVizColor`) and colours by that. Vector modes on a lookup table are a
render-side concept that does not survive being shipped to a browser, so baking
them means the client receives a number per vertex and makes the data range
trivially correct. It also means isosurfaces contour whatever you are
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
uv run doit ci                     # everything a change must pass, ~3 min
uv run doit list                   # the separate tasks: lint_py, test_py, test_ui, package, ...
```

`doit ci` runs ruff, pytest, the browser suite, the client build, a check that
the wheel carries the client, a check that `requirements.txt` matches the lock,
and the cockpit contract. The suites also run directly:

```bash
uv run python tests/test_pipeline.py            # 64 checks, no browser, no GL: the VTK pipeline
uv run python tests/check_client.py             # 115 checks: the client in real Chromium
uv run python tests/test_contract.py            # 56 recorded HTTP exchanges, replayed
uv run python tests/check_cockpit_contract.py   # cfd-backend's cfd-viz build, mirrored
uv run pytest tests/test_sources.py tests/test_endpoints.py
uv run python tests/bench.py hotRoom s2         # server-side extraction sizes and timings
```

Nothing needs a system GL library (see "Which VTK"). The browser suite needs
Playwright's Chromium, which brings its own software GL (SwiftShader), plus
Chromium's usual system libraries: `doit` installs the browser itself (task
`chromium`), and as root also the libraries, as in CI. The cockpit check runs
cfd-backend's `viz.sh` from a checkout next to this one when there is one, and
otherwise the copy in `tests/cockpit/` -- which must match the checkout's
whenever both exist.

Commit messages follow Conventional Commits. The checks run as git hooks once
installed: `uv run pre-commit install --hook-type commit-msg --hook-type pre-commit`.

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

The browser suite reads rendered pixels through a `readPixels` hook in the page
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
- **Report figures.** "Add to case report" is not built here yet, and needs a
  decision first: does the image come from the client canvas (matching what the
  user sees, bands and opacity included) or from a server render (which would
  need every appearance setting to travel with the request)? The mothballed
  Trame app wrote them, so the format exists — see `CLAUDE.md`.
- **Comfort metrics.** The IDA ICE side cares about draught rate, PMV/PPD and
  operative temperature, none of which are OpenFOAM fields. They would be
  derived arrays computed on load — a natural extension of the same baked-array
  mechanism used for colouring.

Scale is *not* on this list: tested to 12 M cells, and measured on a 15.8 M-cell
IDA ICE case: 13 s to open, a 25 MB gzipped boundary, and **7.3 GB peak RAM**
(6.6 GB settled). The server's memory settles near the largest case opened, so
plan for about 0.45 GB per million cells. Note that an IDA ICE "zone"
is a room selected for analysis, not an OpenFOAM `cellZone` — cases are treated
as single-region, single-zone. Decomposed (`processor*`) cases are read via
`vtkPOpenFOAMReader` and are supported.
