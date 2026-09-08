# cfd-viewer — notes for future sessions

Browser-based post-processing for OpenFOAM cases, aimed at an IDA ICE CFD
backend. See `README.md` for what it does and how to run it; this file records
what cost time to discover.

## Two front ends, one pipeline (2026-09-08)

The repo holds **two** front ends over **one** VTK pipeline:

| | where | status |
|---|---|---|
| **VTK pipeline** | `foamviz/case.py`, `pipeline.py`, `colors.py` | shared, maintained |
| **three.js client** | `server/` (extraction over HTTP) + `web/` (React + Mantine + three.js) | **where new work goes** |
| **FoamViz (Trame + vtk.js)** | `foamviz/app.py` | resting |

```
python main.py --data ./data            # three.js client  (default, :5003)
python main.py --data ./data --trame    # FoamViz          (:8080)
```

**Why both live on `main`.** The Trame UI is not being developed any more, but
it is not on a branch of its own either, and that is deliberate: the moment
`pipeline.py` lives in two places it forks, and the CFD pipeline is the asset
here — it is renderer-agnostic and it is what the three.js spike proved
survives a front-end change. So `app.py` stays in the tree as a second,
unmaintained entry point, and a change to the shared pipeline updates its call
sites **in the same commit**. It must keep constructing and keep passing
`tests/test_pipeline.py`; if it ever becomes genuinely expensive to keep, delete
it rather than let it rot in place and lie about being current.

**The `trame` branch** is a frozen snapshot of the Trame app as it stood at
`4a58f11`. Frozen means frozen: if the shared pipeline on `main` gains a fix
worth having there, cherry-pick it deliberately.

**The deployed `cfd-viz` service builds the three.js client** from `main`
(switched 2026-09-08). `cfd-backend/Containerfile` gained a `viz-build` node
stage that clones this repo once and runs the Vite build, and the runtime stage
copies the whole tree from it — so the Python source and the built client always
come from one commit, and the runtime image needs no git.
`cfd-backend/Containerfile.trame` still builds the Trame app from the `trame`
branch as a **manual** fallback (CI does not build it), to be deleted once the
new client has run in the deployment long enough to trust.

Two things about that image not to "tidy":

- **`--server` must stay in its CMD.** It is not only "do not open a browser":
  it also makes an empty `--data` root non-fatal, so the service starts on a
  fresh or not-yet-mounted `CFD_HOME` and picks cases up as they appear. Without
  it an empty volume kills the process and the container restart-loops — a bug
  this stack has already had once (`6a3e688`).
- **It installs `requirements-core.txt`, not `requirements.txt`.** The core file
  is exactly what the three.js service needs (vtk, aiohttp, matplotlib, numpy);
  the full file adds it plus the trame packages. `main.py` imports `foamviz.app`
  lazily *because of this* — a top-level import would make the service refuse to
  start over a dependency it never uses — and `--trame` without trame installed
  fails with a message naming the fix rather than a traceback.

`libosmesa6`/`libgl1` are still installed in that image even though this client
never renders server-side (nothing in `server/` calls `Render()`). What is not
established is whether VTK can still *construct* a `vtkRenderWindow` with no GL
backend present at all, and both front ends share one `FoamPipeline`, which
builds one at import. Dropping them is a real size win; verify construction
survives first.

**Renamed** from `cfd-trame-vtk-viewer` to `cfd-viewer`, since `main` is no
longer a Trame app. References in `cfd-backend` (docs, Containerfile,
`frontend/vite.config.js`) were updated with it.

## Resuming in a new workspace

The project lives at `/workspace/cfd-viewer` and is under version control
(`origin/main` on GitHub). What actually needs to travel is about **1 MB**:

```
CLAUDE.md README.md requirements.txt .gitignore main.py
foamviz/ server/ web/ tests/ docs/
data/hotRoom/{Allrun,Allclean,system,constant,0}     # the case definition only
```

Everything else rebuilds:

- `web/node_modules/` — `cd web && npm install`
- `web/dist/` — `cd web && npm run build` (the Python server serves it from there;
  without it, `/` shows a short "no client build" page instead)
- the Python env — see "Environment" below; in this container it is already at
  `/opt/venv` and on `PATH`
- `data/hotRoom/` (74 MB) — regenerate, see "Demo data". Or point `--data` at a
  real case; nothing depends on the demo except the tests.

`tests/test_pipeline.py` hardcodes the demo case (32 000 cells, 19 time steps,
3 patches, T range 300–600 K). Point it elsewhere and those specific assertions
need updating — the checks are deliberately concrete.

## Environment

- **Container venv at `/opt/venv`, on `PATH`** (2026-09-04): baked into the image
  from `requirements.txt` with **plain `vtk` 9.7** + system **`libosmesa6`** and
  the Playwright/Chromium apt libs. `python`/`pip`/`playwright` all resolve to it.
  Run things directly: `python tests/test_pipeline.py`, `python tests/browser_check.py`.
  **No `LD_LIBRARY_PATH` staging any more** — apt puts OSMesa on the default
  linker path. This is the primary env now.
- Legacy: a `vtk-osmesa` 9.3.1 venv still exists at `/home/node/.venvs/foamviz`
  (`/home/node` is a persistent volume, `/workspace` is not) — self-contained
  OSMesa, kept as a fallback; no longer the one to use.
- Python 3.12, Ubuntu 24.04 container. **No root at runtime** (image built with root).
- `render_window.GetClassName()` should report `vtkOSOpenGLRenderWindow`.

### VTK packaging — checked 2026-08-11, don't re-derive

- `requirements.txt` asks for plain **`vtk>=9.4`**. Since 9.4 the stock PyPI
  wheels contain EGL *and* OSMesa render windows and fall back X11 → EGL →
  OSMesa at runtime. They **dlopen** libEGL/libOSMesa instead of bundling, so a
  slim container still needs `apt-get install libosmesa6`.
- **The image now installs `libosmesa6` (apt)**, so `/opt/venv` runs stock `vtk`
  9.7 straight from `requirements.txt` — no `vtk-osmesa`, no `LD_LIBRARY_PATH`.
  Verified 2026-09-04: the 9-step browser suite is green on it. (Older containers
  had no system Mesa and used `vtk-osmesa` 9.3.1 as a self-contained fallback —
  see the legacy venv note above.)
- `vtk-osmesa` is a dead end and should not be the default: not on PyPI
  (`--extra-index-url https://wheels.vtk.org`, which 301s to a GitLab package
  index), frozen at **9.3.1**, wheels only for cp36–cp312 on linux x86_64 and
  win amd64. Python 3.13+/macOS/arm64 cannot resolve it at all.
- **trame does not lag VTK.** `trame-vtk` 2.11.15 requires only `trame-client`
  — no VTK pin anywhere. VTK 9.6.2 was verified end-to-end including vtk.js
  client-side serialisation.
- OpenFOAM 13 at `/opt/cfd/OpenFOAM-13`; `source /opt/cfd/OpenFOAM-13/etc/bashrc`.
- **Playwright + Chromium are fully installed in the image (2026-09-04):**
  browser under `$PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`, all Chromium system
  libs apt-installed (`ldd` on the shell is clean). `browser_check.py` launches
  headless Chromium (WebGL 2.0 via SwiftShader) with **no `LD_LIBRARY_PATH`**.
  The old "libs can't be installed, stage on LD_LIBRARY_PATH" recipe is obsolete;
  the Containerfile apt list (libosmesa6 + ~22 Chromium libs) replaces it.

## VTK-Python traps hit here

- **Default lighting is a single camera headlight** → faces angled away go
  black (the boundary shell especially). Fixed with a `vtkLightKit` (key + fill
  + back + head, like ParaView) + `TwoSidedLightingOn` on the renderer, plus an
  **ambient floor** on the lit actors. Controls live in a **collapsible
  "Lighting" panel** at the bottom of the side pane (hidden by default): a **Light kit**
  toggle (default on; off → `RemoveAllLights` → VTK's default headlight) and
  Ambient/Diffuse sliders (default 0.3/0.7) → `set_light_kit` / `set_lighting`.
  The ambient floor is the part that reliably survives to the **vtk.js client**
  (local mode does its own lighting; server-side lights may not serialise) — it
  guarantees no face is pure black in either mode, and it lights the
  server-rendered report PNGs. The slice stays `LightingOff` (flat) and is left
  out. Lighting prefs are **persisted globally** to a JSON settings file
  (`_save_settings`/`_load_settings`, default `<case_root>/.foamviz-settings.json`
  — persistent on the CFD_HOME volume — override with `$FOAMVIZ_SETTINGS`), so
  they survive a server restart. (The shared-session server already keeps state
  across page reloads; the file adds cross-restart survival. Not cookies —
  global, server-side, and verifiable.)
- **Never pass a freshly constructed source inline.**
  `glyph.SetSourceConnection(vtk.vtkArrowSource().GetOutputPort())` **segfaults**
  — Python collects the temporary while the pipeline still references it. Keep an
  attribute for every source. This cost the most time of anything in the build.
- **Actor-level transforms do not survive serialisation to vtk.js.** The
  orientation triad only rendered correctly in one mode until its
  position/scale/rotation were baked into the geometry with
  `vtkTransformPolyDataFilter`.
- **Do not colour by vector mode on a lookup table.** Same reason. `pipeline.py`
  bakes a derived scalar array (`FoamVizColor`) instead, so both render modes
  agree and the data range is trivially correct.
- **Time stepping**: pull with `reader.UpdateTimeStep(t)` and hand downstream
  filters the result via `SetInputData`. A connected pipeline re-negotiates the
  time request on every `Update()` and silently falls back to `t=0`.
- `vtkOpenFOAMReader` needs an empty `*.foam` marker file in the case dir
  (`ensure_foam_stub` creates it). It advertises `patch/*` and `group/*` arrays;
  groups duplicate their member patches, so only `patch/*` is exposed. It also
  advertises fields such as `T.orig` that never appear in the output — read the
  field list from the actual output arrays, not from the reader.

## The three.js client (2026-09-08)

`server/` extracts, `web/` draws. Read `docs/threejs-spike-report.md` for the
measured numbers behind the decision; this section is how the shipped thing is
built and what to be careful of.

### The part model — read this before touching the UI

The unit of work is a **part**: `boundary`, `slice`, `iso`, `stream`, `glyph`,
`geometry`. A request names the parts it wants, and the server extracts only
those. The **client** decides which to ask for, because it is the side that
knows what it still has:

- Every part has a **signature** built from exactly the query keys that change
  its geometry. The list is `PART_INPUTS` in `server/scene.py`, shipped to the
  browser in `/api/meta`, so the two sides cannot disagree about what a control
  affects. `useScene.js` re-requests a part only when its signature moves.
- Signatures may be **conditional** (`{"when": {...}, "keys": [...]}`). Two
  dependencies really are: the boundary depends on the cut plane *only while it
  is being clipped by it*, and arrows seed from the plane grid *or* the
  isosurface, never both. Getting the first one wrong is what made the plane
  slider re-ship the whole boundary surface — 15 of s2's 16.2 MB — to redraw a
  slice worth tens of kB. That bug was caught by
  `tests/check_client.py`, which asserts the release asks for `['slice']` and
  nothing else. Keep that check.
- Payloads are **cached by signature**, capped by BYTES not entries (one s2
  boundary is worth a thousand hotRoom slices). So stepping back to a time step
  or a slice position you have already visited is instant, with no request at
  all. The Trame path cannot do this: its unit of work is the whole scene, so it
  re-extracts every time.
- A part that goes invisible is **dropped from the GPU**, so hiding the boundary
  on a big case gives the memory back rather than just skipping its draw. It
  comes back from cache, free.

### What is client and what is server

This split *is* the architecture, and the UI tags every control `server` or
`client` so the cost is visible while you use it. Keep the tags.

| Client — a uniform or GPU state, instant | Server — re-extraction, one round trip |
|---|---|
| colour map, colour range, bands, opacity-by-value | colour **field** and component (scalars are baked per vertex) |
| per-part visibility and opacity, "colour by field" | cut-plane position/axis, isovalues, seed counts, glyph count/size |
| near-wall culling, shell mesh edges | time step, patch selection, crinkle slice, clip-at-plane, tubes |
| camera, view presets, F-pick, lighting, theme, legend | true cell values, robust range |

Two things moved from the server column to the client column in the port, and
both were wins: **bands and the opacity ramp** (a transfer function in VTK is a
*uniform* in a shader, and the opacity ramp was outright impossible in vtk.js
local mode — reverted there, see the note below), and **the centre-of-rotation
pick**, which needed a server `vtkCellPicker` round trip in Trame and is a
`THREE.Raycaster` call here because the geometry is already local.

### What the debouncing became

The Trame app deferred a lot of work behind drafts and Apply buttons. Some of
that was essential and is preserved; some of it existed only because the
setting rebuilt a VTK object, and has been dropped.

**Kept, because the work is still heavy:**

- **Streamline tuning** (seeds, max length, tubes, tube width) behind one Apply.
  `vtkStreamTracer` is 2.6 s of the 3.1 s server time on s2 and costs the same
  in either front end — it is not a cost of this architecture.
- **The plane's numeric X/Y/Z fields** are inert until their Apply. That is the
  debounce for typed input: keystrokes never re-extract.
- **Patch selection** behind an Apply — it reloads the case, not just a filter.
- **Commit-on-release** for every slider whose value the server needs: plane
  position, time, isosurface count, glyph count and size.

**Dropped, because they stopped costing anything:** bands, colour min/max and
opacity-by-value are now live (they were behind the Options Apply); per-part
opacity and lighting are live. The Options popover still has an Apply, but only
over the two settings that genuinely re-read or re-bake scalars (robust range,
true cell values) — and the button is disabled until the group is dirty, where
the Trame one always fired.

**Also dropped: the two line-width sliders**, on streamlines and on geometry.
WebGL caps `lineWidth` at 1, so the Trame app shipped two controls that provably
did nothing. Tubes are the real answer for thick streamlines and are one switch
away; `Line2`/`LineMaterial` would be the answer for the rest and is not wired.

`LabelledSlider` (`web/src/ui/controls.jsx`) has **three** callbacks and the
distinction matters: `onPreview` fires on every tick of a drag, `onChange` only
for `live` sliders, `onCommit` only on release. The first version had no
`onPreview`, so a deferred slider swallowed its own drag events and the
cut-plane frame never appeared — the failure looked like a rendering bug and was
a callback-plumbing bug.

### The red cut-plane outline

Thirty lines of ordinary three.js: a four-corner rectangle spanning the two
non-normal axes, slid along the normal by setting `position`, shown while the
slider is dragged and hidden on release (`_buildOutline` / `setOutlineAxis` /
`moveOutline` in `web/src/viewer/Viewer.js`).

Worth recording what that replaces, because the Trame version is a cautionary
tale that no longer applies: there it had to be a declarative vtk.js child
injected into the view's context, **mounted only during a drag**, because the
client renderer is transiently null after a view rebuild and the library's
`onMounted` has no null guard. None of that exists here — one renderer, ours,
and the outline is just an object in the scene. If you ever want the frame
persistent while the Cut plane tool is active, it is now a one-line change; it
is drag-only purely to match the UX that was tuned in Trame.

### Traps in this client

- **Mantine v7+ ships plain CSS**, so `@mantine/core/styles.css` must be
  imported in `main.jsx`. Omit it and everything renders unstyled, which looks
  like a build failure and is not.
- **The React tree shape must not change once the Viewer exists.** The first
  version early-returned a "loading" div until `/api/meta` answered, so
  `stageRef` was null when the Viewer's effect ran (`appendChild` of null) — and
  had the stage merely appeared *later*, the appearance effects would already
  have run against a null viewer and would never re-run, so initial styles,
  lighting and theme would be silently missing. Hence `EMPTY_REQUEST`: one
  stable tree from the first commit, with a placeholder request.
- **`base: ''` in `vite.config.js` is required**, not cosmetic. The viewer is
  served at `/` standalone and under `/viz/` behind the cfd-frontend nginx, so
  an absolute `/assets/...` would leave this service and hit the cockpit SPA at
  the origin root. The API calls are relative for the same reason — the same
  lesson the Trame app's screenshot link learned.
- **`THREE.RGBFormat` is gone**: `DataTexture` must be RGBA, so the 256×3 LUT
  from the server is expanded to 256×4 on upload.
- **`renderer.outputColorSpace = LinearSRGBColorSpace`, deliberately.** The LUT
  bytes are the same matplotlib samples VTK writes into its transfer function,
  so any output conversion would make the two renderers disagree on colour for
  no reason.
- **`OrbitControls` bakes `object.up` into a quaternion in its constructor** and
  never re-reads it, so `camera.up.set(0, 0, 1)` must come *before*
  `new OrbitControls(...)`, and the start position must be off-axis or azimuth is
  undefined at the pole. With that, it is a proper Z-up turntable — which is
  *not* available in trame-vtk 2.11.15 local mode at all. `setView` changes `up`
  for the top/bottom views, so it assigns `controls.object.up` explicitly.
- **A uniform is not enough for alpha.** A material only respects alpha when
  `transparent` is set, and a half-transparent fragment that writes depth hides
  what is behind it. `_applyBlending` owns that rule for new meshes, opacity
  changes and the opacity-map toggle alike. Blending is **unsorted** — no depth
  peeling, no OIT — so the near-zero `discard` in the shader is what keeps it
  looking sane, and `RENDER_ORDER` puts the big translucent shell last.
- **An uncoloured part has no value to ramp**, so `uOpacityMap` is multiplied by
  `uColored` in the shader. The shell defaults to *uncoloured* (neutral grey, so
  the slice inside it reads), which means opacity-by-value appears to do nothing
  until you turn "Colour by field" on. That is correct, and it cost a
  false-failing test to establish — the test now colours the shell first.
- **`wireframe: true` draws the triangulation, not the mesh** — never use it.
  `THREE.EdgesGeometry` suppresses edges between coplanar faces, so a
  triangulated quad loses its diagonal and the real cell grid comes back. It only
  holds on **flat** cells, though: on a curved surface genuine mesh edges are
  near-coplanar and vanish too. Room walls are flat, so the shell uses it; the
  *slice* gets its edges from the server's crinkle extraction instead.
- **Screenshot-based render checks lie.** The HUD, legend and busy overlay sit on
  top of the canvas, so a page screenshot of an *empty* scene comes back
  colourful. `Viewer.grab()` does a `readPixels` and counts distinct colours in
  the browser. And `Color.getHex()` colour-manages its output, so it does not
  match the framebuffer bytes — a background check built on it read 0% both ways
  and looked green. `grab()` reads a corner pixel as the reference instead. A
  check that cannot fail is worse than no check.
- **The busy overlay captures clicks by design** (you must not be able to queue
  edits onto a running VTK filter), so a browser test that clicks without
  waiting for it is racing the app. `wait_idle()` in `check_client.py` waits for
  both request quiet *and* the overlay's removal.
- **OrbitControls damping is still easing when a test reads the camera**, so
  camera assertions use a domain-relative tolerance, not a machine epsilon. A
  `1e-6` check on a `+Z` view failed on `2.499976` vs `2.5`.
- **Streamlines have no polyline primitive in three.js** that survives an index
  buffer, so an N-point line becomes N-1 `LineSegments` index pairs: one extra
  index per point, whole scene in one draw call.
- **OpenFOAM patches are polygons, not triangles** — `vtkTriangleFilter` is
  mandatory before the wire. Normals are computed server-side (`SplittingOff`, so
  the point count stays 1:1 with the scalars) and skipped when the input already
  has them (the isosurface arrives via `vtkPolyDataNormals`).

### Concurrency

Extraction runs in a worker thread (`asyncio.to_thread`) behind a single lock
(`server/app.py`). The thread keeps a 3-second `vtkStreamTracer` from freezing
the event loop; the lock exists because the filter graph is shared mutable state
and two concurrent extractions would interleave `update_plane` calls and hand
each other the wrong geometry. Still **one case per process**, as in Trame.

### Not done, deliberately

- **"Add to report".** The Trame app's Report button writes `figure_NN.png` +
  `figure_NN.json` into `<case>/report/`. It is **not ported**, because it needs
  a decision first, and the decision is on a real seam: does the report image
  come from the **client canvas** (matches what the user sees, bands and opacity
  included) or from a **server render** (needs every appearance setting to travel
  with the request)? Every setting the shader owns is a setting the server does
  not know — that is the cost of the control this architecture buys. Decide it,
  then port. See "Case-report figures" below for what already exists.
- **Cell-data (flat) colouring of the surface** needs non-indexed triangles with
  the cell scalar replicated per vertex (3× the vertex data). The server's
  `cell_data` flag is wired and bakes the array, but the client still draws
  point-interpolated values.
- **Picking a *cell*** (as opposed to a point): three.js hands back a triangle
  index, not a VTK cell id. Needs a server round trip with the world coordinate.
- **Volume rendering**: nothing. vtk.js has a volume mapper; here it is a project.
- **Boundary decimation.** 15 of s2's 16.2 MB raw is the boundary at full mesh
  resolution, mostly flat walls. gzip takes the payload to 24%, which made this
  non-urgent rather than solved; patch selection helps immediately and
  `vtkQuadricDecimation` is the real fix.
- **react-three-fiber.** Asked about, deferred on purpose. The UI never touches
  three.js — it calls `Viewer` methods — so R3F is a contained swap of one file.
  What it would restructure is the measured hot path (decode straight into
  `BufferAttribute`s, shared uniform objects mutated in place, 588 k triangles in
  0.7 ms) plus `_applyBlending`, the LUT `DataTexture` and the `readPixels` test
  hook. The idiomatic route is `<primitive object={mesh}/>` for the heavy parts
  and components for the declarative ones (camera, controls, outline, triad).
  Worth doing for the team's sake, not for the pixels.

## Trame traps hit here

**These sections are the RESTING Trame app** (`foamviz/app.py`, and the `trame`
branch). They are kept because the app is kept, because the deployed `cfd-viz`
service still builds from that branch, and because several of them are really
notes about `pipeline.py` — which is shared and live. Do not port a workaround
from here into the three.js client without checking whether the constraint that
forced it still exists; several do not (turntable rotation, the plane-outline
mount dance, the camera push, banding, the opacity ramp).



- **In local (vtk.js) mode the client owns its camera.** A camera set
  server-side (`renderer` camera + `ResetCamera`) is invisible until pushed:
  `view.push_camera()` (wired as `ctrl.view_push_camera`). `view.reset_camera()`
  only *refits* the client's own orientation, so calling it after a preset
  clobbered the orientation — which is why the X/Y/Z/Iso view buttons "never
  worked". `set_view` and the initial `reset_camera=True` load now push instead.
  - **Corollary — never `push_remote_camera_on_end_interaction()` in local
    mode.** That observer fires on every EndInteraction (mouse up / leave) and
    `setCamera()`s the server camera onto the client, which re-applies the focal
    point and **resets the client's centre of rotation** — orbiting felt broken
    and needed constant R. Removed 2026-08-18. Pushing a camera to the client is
    fine on a *deliberate* action (a view button); doing it on every mouse-up is
    not. The server camera already tracks the client in local mode, so it gained
    nothing.
  - **Turntable rotation is NOT available with trame-vtk 2.11.15 (local mode).**
    Rotation is a client (vtk.js) interactor setting, not a server
    `vtkInteractorStyle` (which does nothing in local mode). vtk.js's rotate
    manipulator *does* support turntable via `useWorldUpVec`/`worldUpVec`, but:
    (1) trame's `interactor_settings` applier (client `Md()`) forwards only
    `button/shift/control/alt/scrollEnabled/dragEnabled` and **drops** those
    keys; (2) the interactor/style helper is closure-captured on the client (no
    `expose()`, not global) so it can't be patched from injected JS or `js_call`.
    Tried the reactive-prop path (a `turntable` toggle) — the toggle changed the
    prop but the flag never reached the manipulator, so it did nothing; reverted
    2026-08-21. Re-enable once a trame-vtk forwards manipulator props: bind the
    local view's `interactor_settings` to a state var whose Rotate entry carries
    `useWorldUpVec: True, worldUpVec: [0,0,1]`.
- **Keyboard shortcuts are extensible via `KEY_SHORTCUTS`** (`app.py`): a pressed
  `event.key` → a CSS selector, and one injected `window` keydown listener
  (`client.Script`, `_KEY_JS_TEMPLATE`) clicks the matched element. So a shortcut
  rides an existing button's own click handler — no JS↔Python bridge. To add one:
  give the target element a `js-*` class and add a row. Shift makes an uppercase
  key (shift+x → `-x`). vtk.js already binds `r` to reset the camera.
  (`client.Script` renders as `<trame-script :script="trame__inline_script_N">`;
  the JS lives in that state var and runs client-side, like `client.Style`.)
- **`F` sets the centre of rotation from the point under the cursor**
  (ParaView-style focus). Unlike the axis shortcuts it needs the pointer
  position *and* a server round-trip, so it cannot ride the click-a-button
  bridge. `_FOCUS_JS` tracks the cursor and, on `F` over the 3D `<canvas>`,
  calls `window.trame.trigger('foamviz_pick_cor', [x, y, w, h])` — trame's own
  client→server call (`window.trame` exposes `.trigger(name, args, kwargs)`,
  the general JS→Python path when there's no button to click). The trigger is
  registered imperatively (`self.server.trigger(name)(fn)`; there is no
  `@controller.trigger` decorator). Server side, `pipeline.pick_cor` sizes the
  offscreen window to the client canvas so the projection aspect matches, casts
  a `vtkCellPicker`, and — since vtk.js orbits the *focal point* (no separate
  COR) — sets focal point to the pick and slides the camera along its view
  direction so the point lands at screen centre (view direction + distance
  preserved: no tilt, no zoom, just a pan-to-centre). Then `view_push_camera` +
  `view_update`. A miss (empty space) is a silent no-op.
- **`VBtnToggle(...).add_children([VBtn(...), ...])` renders the buttons twice.**
  A widget constructed while another element is the active parent attaches
  there too. Build children inside `with toggle:`.
- **Vue template expressions cannot see `document`, `window`, or the
  surrounding component's `$refs`.** Unknown identifiers resolve to `undefined`,
  so failures look like `Cannot read properties of undefined`. The PNG download
  therefore uses a real aiohttp route registered through
  `ctrl.on_server_bind` — see `_add_http_routes`. Do not "fix" it back into a
  `data:` URI: Chromium refuses a scripted click on a multi-megabyte data URL.
- **An aiohttp `@web.middleware`'s second parameter must be named `handler`.**
  aiohttp calls middlewares as `partial(mw, handler=next)` — by keyword — so any
  other name (e.g. `next_handler`) raises `got an unexpected keyword argument
  'handler'` on *every* request and 500s the whole app. Bit the `?case=`
  preselect middleware in `_add_http_routes`.
- `html.A` silently drops a `ref=` kwarg.
- `trame-vtk`'s client POSTs `/paraview/` on startup and gets a harmless 405.
  Expected; filtered in `tests/browser_check.py`.

## Testing

**`main.py` logs at WARNING for `--trame` and INFO otherwise, and that is not a
style choice.** trame_client/trame_server emit a line *per widget attribute* at
INFO — tens of thousands while the UI is built. If the caller piped stdout and
is not reading it (`tests/browser_check.py` does exactly that), the 64 kB pipe
buffer fills and the process **blocks before it listens**. The symptom is
`FAILURE: server never came up`, which is a long way from "the log level is too
low". Cost a debugging round after the front-end split; do not "tidy" the two
levels into one.

**`tests/browser_check.py` must pass `--trame`.** `main.py` now defaults to the
three.js client, so without the flag every selector in that suite misses.

- `tests/test_pipeline.py` — 29 checks, no browser, ~15 s. Asserts **output
  counts** for every filter, because an empty VTK filter raises nothing and
  renders as a plausible blank image.
- `tests/browser_check.py` — drives real Chromium through 9 steps, fails on any
  console error. Runs directly under `/opt/venv` now (no `LD_LIBRARY_PATH`).
- When checking whether the 3D view drew anything, screenshot the **page**, not
  the canvas: a WebGL canvas without `preserveDrawingBuffer` reads back blank
  after the frame is presented.
- `js-*` classes in `app.py` exist purely as test hooks; Vuetify's own markup
  has nothing stable to select on, and `get_by_label("Field")` also matches
  "Vector field".

## Demo data

`data/hotRoom` = OpenFOAM 13 `fluid/hotRoomBoussinesqSteady`, copied from
`$FOAM_TUTORIALS/fluid/hotRoomBoussinesqSteady`, with two edits:

- `system/blockMeshDict`: `hex (...) (20 10 20)` → `(40 20 40)` (32 000 cells)
- `system/controlDict`: `writeFormat ascii` → `binary`

10 × 5 × 10 m room, 1 m² of floor held at 600 K, everything else 300 K.
Converges at iteration 1730 and writes 19 time directories.

`constant/triSurface/building.obj` is a small **added fixture** (a box at the
room bounds), not part of the tutorial — it exercises the Geometry tool and its
pipeline test. `Allrun`/`Allclean` leave it alone. A real case ships its own
`building.obj` here. Regenerate the solution (not the OBJ):

```bash
source /opt/cfd/OpenFOAM-13/etc/bashrc
cd data/hotRoom && ./Allclean && ./Allrun
```

Chosen as the nearest tutorial analogue to an IDA ICE `HEATING` case:
buoyancy-driven room airflow with a thermal plume, steady state.

## Geometry tool + outline changes (2026-08-17)

- **Geometry tool** (6th tool): reads a building OBJ from `constant/triSurface/`
  via `vtkOBJReader` (lazily; `set_case` only sets the filename, `has_geometry`
  gates the UI). setupIceCase indexes geometry, so the file is `building.obj`
  **or** `building<N>.obj` (e.g. `building10.obj`) — `set_case` searches
  `building\d*\.obj`, first match (bare name first, then by index).
  - **ONE fixed actor/mapper fed by a single `vtkFeatureEdges`**, rendered as
    flat lines throughout. The mode is a **filter-parameter toggle**, not a
    scene/mapper mutation — so the output re-serialises to the vtk.js client
    cleanly, exactly like changing the contour count. This is the pattern that
    works; the ones that DIDN'T (each corrupted the client, learned the hard
    way): swapping the mapper's input (stale input → filled triangles), changing
    the actor's representation, and actor add/remove (re-added actors lost their
    properties → FE came back shaded, WF as surfaces).
  - **Feature edges** = FeatureEdges on, Manifold off (sharp + boundary — the
    architectural outline). **Wireframe** = FeatureEdges *off*, Manifold on
    (every edge). They are OPPOSITE toggles because `vtkFeatureEdges` quirk:
    feature+manifold *together* yields only the feature edges, but manifold
    *alone* yields all interior edges. Boundary stays on for both.
  - Footprint: only the current mode's edges exist — feature edges are small; the
    full all-edges set is built only when wireframe is actually chosen.
  State `geometry_visible/mode/opacity/line_width`; cheap handler. Only
  `building.obj` (or `building<N>.obj`) for now — more `triSurface` files later.
- **Red plane outline is drag-only, and now client-side.** Hidden by default; the
  position slider's `start` shows it and `plane_slider_release` hides it. It is a
  declarative vtk.js child moved in the browser — see the DONE note under
  "Cut-plane slider smoothness" for the full mechanism (this superseded the old
  server-side `plane_outline` actor / `_on_plane_slide` per-tick move).
- **The always-on domain outline box (`vtkOutlineFilter`) was removed** — the
  building geometry is the context now.

## Light/dark theme (2026-08-17)

The embedding app (cfd-frontend) drives the theme via `?theme=light|dark` on the
iframe URL. FoamViz is a shared single session (UI built once), so the theme
switches **reactively**, not by rebuild:

- `ui_theme` state is bound to `<VApp :theme>` (the layout is built with
  `theme=("ui_theme",)`, which renders `:theme="ui_theme"` — verified), so the
  whole Vuetify chrome (drawer/toolbar/controls) re-themes at runtime.
- The floating overlays (legend, bottom bar, mode switch, section headers) are
  styled with Vuetify's theme CSS vars — `rgba(var(--v-theme-surface), …)` /
  `rgb(var(--v-theme-on-surface))` — so they follow the same switch with no
  per-theme CSS.
- The 3D viewport is VTK, not CSS: `pipeline.set_theme(light)` flips the
  renderer background and **inverts the neutral geometry line colour** (light
  lines on dark, dark on light — field-coloured actors need no change).
- The `?theme` middleware (beside `?case` in `_add_http_routes`) calls
  `_set_theme`, which sets `ui_theme` + calls `pipeline.set_theme` + re-renders.
  Default is dark.

## Architecture in one paragraph

`case.py` wraps `vtkOpenFOAMReader` and hands out **snapshots** — concrete
datasets for one instant — rather than a live pipeline connection.
`pipeline.py` owns the renderer and every representation, and bakes the
selected field/component into a real scalar array (`FoamVizColor`) that
everything colours by. `app.py` is the Trame UI: state dict, change handlers,
one `update_scene()` that pushes all state into the pipeline and redraws.
`colors.py` samples matplotlib colour maps once and serves both the VTK
transfer function and the HTML legend gradient, so they cannot drift apart.

**Perf invariant (2026-09-01): a toggle must leave `case.internal`'s MTime
untouched.** `update_scene()` runs on *every* change (incl. a mere visibility/
opacity toggle). If it dirties `case.internal`, its MTime bumps and every filter
fed by it — the cutter, hence the stream **seeds**, hence the tracer + tube, plus
isosurfaces and glyphs — re-executes *and* re-serialises to the vtk.js client
(trame caches serialized arrays by MTime, so a bump forces a re-hash/re-encode of
the big streamline array). That made toggling any actor as expensive as
recomputing the streamlines. TWO places dirtied it and both are now guarded:
- `apply_color_array()` re-baked `FoamVizColor` (Remove/AddArray) every call —
  guarded by a `_baked` signature `(field, component, use_cell_data)`, cleared by
  `update_data()` on reload so fresh data still re-bakes.
- `SetActiveVectors(field)` bumps the MTime **even when that field is already
  active** (VTK doesn't short-circuit it) — `update_scene` now sets it only when
  `GetVectors().GetName()` actually differs.

Verified by instrumenting the real `update_scene`: on a geometry/surface/slice/
opacity toggle the internal→cutter→seeds→tracer→tube MTimes all stay stable; a
real field/vector change still bumps and re-integrates. Keep both guards.
The cut plane is the hub — the slice and the stream-tracer seeds derive from it
(the arrows have their own plane grid, see below).

## Fields, isosurfaces, arrows (reworked 2026-08-24)

- **Field loading is filtered at the reader** (`case.py`): `SKIP_FIELDS`
  (`p`, `alphat`, `omega`, `epsilon`, `rho`) are disabled after
  `EnableAllCellArrays()`, so they are never read or interpolated cell→point
  (memory saving; absent ones are ignored). **Temperature is converted K→°C once
  at read time** (`_to_celsius`, `KELVIN_FIELDS={"T"}`): replaces the `T` array
  with a converted copy (not in place — the reader's cache is shared by the
  shallow copies), so every downstream range/legend/contour value is already °C.
  `_FIELD_UNITS["T"]` is `[°C]`.
- **Isosurfaces**: count is 1/3/5 only (a slider stepping by 2 from 1; default
  1). `_contour_values()` builds the isovalues — the single `contour_value` for
  one surface, else interior fractions across `[contour_min, contour_max]`. All
  three seed from (track) the colour range in `_rescale`. `update_contour` takes
  an explicit values list and **always sets the isovalues** (even when the actor
  is hidden) so the "On isosurface" arrow source can read the contour output.
- **Arrows**: two seed sources. "On plane" lays a **regular grid over the cut
  plane** (`vtkPlaneSource` → `vtkProbeFilter` on the volume → `vtkThresholdPoints`
  on `vtkValidPointMask` to drop grid points outside the mesh) — evenly spaced,
  unlike mask-points on the cut faces which clump where the mesh is fine. "On
  isosurface" seeds off the contour output (mask-points). `update_glyphs` takes
  the plane axis+coord to size the grid.

## UX batch + arrows scaling + cut-plane smoothness (2026-09-01 → 09-04)

- **Arrows (3a316e5):** always orient by a selectable **vector field** (default
  U), independent of the colour field; **length normalised by the seed set's own
  max |U|**, not the whole-domain max (arrows went invisible when colouring by a
  large scalar like T). Selector shares `vector_field` with streamlines.
- **UX:** streamlines default to **lines** (two actors — line + tube — toggled by
  visibility, NOT a mapper-input swap, which caused ribbon artefacts, 86967fc);
  boundary defaults to **opacity 1**; colour **Bands** + range/cell options live
  in an **Options popover with an Apply button** (defers heavy work on big cases);
  **Auto-range** toggle sits in the top toolbar. NB **WebGL caps line width at 1**
  — the stream line-width slider has no visible effect; not fixable.
- **Colour-map weighted opacity: NOT available (reverted cd543e2).** The
  discretizable-CTF + opacity-ramp spike rendered nothing in vtk.js local mode
  and broke colour-map/bands (the discretizable LUT doesn't serialise). Don't
  retry without a fundamentally different approach.
- **Cut-plane slider smoothness — DONE (2026-09-04), client-side vtk.js outline.**
  The red plane frame is now a **declarative client-side outline** — a
  `VtkGeometryRepresentation` + `VtkPolyData` nested *inside* the
  `VtkRemoteLocalView` (see `_content`). It injects the same `"view"` context the
  view provides (`provide("view", c)` → `a.renderer.addActor`), so it renders into
  **the same vtk.js renderer and shares the camera** — no second renderer, no
  reverse-engineering. Its **actor `position` slides it** along the active axis by
  `plane_slider` via an inline ternary binding, moved **entirely in the browser**:
  the render is client-side and **no heavy per-tick server work runs** (no change
  handler, no `view.update()`/`full_state` re-store), so the lag is gone (verified:
  the release is the only committing round trip). That kills what the reverted
  server-side attempts couldn't (delta push 7ee3d35, throttle 03a5005 — all still
  paid the round trip + `full_state` re-store on every tick).
  - **`plane_slider` has NO `@change`.** The slider writes it live; the outline
    follows it client-side; the cut runs **once**, on release
    (`plane_slider_release` → commit active coord + `plane_apply`). A per-tick
    handler would just reintroduce the lag.
  - **Mount gotchas (cost time, don't re-derive):** in `VtkRemoteLocalView` the
    vtk.js renderer is **null until the first scene sync AND again after the view
    is torn down** (`beforeDelete` nulls it) — a **Client↔Server mode switch** (or a
    reconnect) rebuilds the view, so the renderer is transiently null. A child that
    is *mounted* during that window crashes in `a.renderer.addActor(null)` (the
    library's `Hh.onMounted` has no null guard). The obvious event gate — the view's
    `afterSceneLoaded` — **does NOT propagate through the wrapper** in trame-vtk
    2.11.16 (never fires on the Python side). So the outline is gated
    **`v_if="plane_outline_on"` — mounted only while a drag is in progress**
    (`start=` JS sets it true, `plane_slider_release` sets it false, both
    client-side, no round trip). It therefore never lingers mounted to be caught by
    a rebuild, and by drag time the scene has long rendered so the renderer is live.
    (An earlier gate on a persistent `plane_outline_ready` flag flipped on first
    grab still left the child mounted across a later mode switch → `addActor(null)`
    "after a while". Don't reintroduce a persistent mount.) Guarded by
    `browser_check.py` step **8b** (drags the plane after a Server→Client switch).
  - **Actor transforms are fine here.** The "actor transforms don't survive
    serialisation" trap (see Trame traps) is about *server→vtk.js* serialisation;
    this actor lives natively in the client, so `position`/`visibility` apply
    directly. A change to the reactive `actor` prop triggers
    `representation.dataChanged()` → `view.render()`, so it repaints with no
    server involvement.
  - **Base points** (the rectangle spanning the two non-normal axes, at coord 0 on
    the normal — position supplies the coordinate) are rebuilt in Python only on
    **case load / axis switch** by `_set_plane_outline_base`, from `_sync_plane_ui`.
  - **Server (remote) render mode:** the declarative child isn't rendered there, so
    a drag shows **no live outline** — the cut just lands on release. Acceptable:
    server mode is the GPU-less fallback, and it no longer round-trips per tick
    either. The old server-side `plane_outline` actor + `update_plane_outline` +
    `set_plane_outline_visible` were **removed** from `pipeline.py`.
  - Covered by `browser_check.py` step **1b/1c** (drags the plane slider, asserts
    the red frame appears mid-drag and the slice moves on release).

## Memory management (2026-09-07)

Where the memory actually sits, and what a case switch does with it. All figures
measured in the dev container with `data/s2` (1.1 GB on disk) and
`data/geometric-fancoil-and-beam` (300 MB, decomposed), VTK 9.7.

**One case is held at a time.** There is no case cache: `FoamViz.case` is a
single slot and `case_paths` holds only name→path strings. Four things hold real
memory:

1. **The reader's mesh cache** — `vtkOpenFOAMReader.CacheMesh` is on by default
   (checked: `GetCacheMesh() == 1`). This is why stepping in time is cheap: only
   the fields are re-read, not the mesh. `FoamCase.load()` also short-circuits
   entirely when time+patches are unchanged, so going back and forth on one case
   costs nothing.
2. **The snapshot** — `case.internal` + `case.boundary`, shallow copies of the
   reader output.
3. **The pipeline's filter outputs** — `FoamPipeline` lives for the process and
   every filter keeps its last-executed output.
4. **trame's serialized-array cache** — in local (vtk.js) mode every array
   shipped to the browser is kept in `SynchronizationContext.data_array_cache`,
   keyed by md5, holding a reference to the `vtkDataArray` itself.

**A case switch does free the old case** — plain refcounting, no reference
cycles (`gc.collect()` changes nothing). Two things used to spoil that, both
fixed by `FoamPipeline.release_case()` called at the top of `load_case`:

- The old mesh stayed wired into the cutter/contour/tracer/glyph filters until
  `update_data()` rewired them at the **end** of the load, so the entire new
  case was read with the old one still resident. Peak RSS on s2 → fancoil:
  **1653 → 1521 MB**.
- A hidden actor never re-executes its filter (`update_streamlines` /
  `update_glyphs` return early when invisible) and `load_case` deliberately
  starts every case with the heavy representations **off** — so case A's
  streamlines and isosurfaces sat in the tracer/tube/contour outputs until you
  happened to switch them on again under case B. Steady RSS after the switch:
  **1055 → 757 MB**.

**Why the container's RSS did not drop:** glibc keeps freed pages in its arenas.
A single `malloc_trim(0)` took RSS from 611 → 268 MB after an s2 → hotRoom
switch. `app._trim_heap()` (ctypes, best-effort — non-glibc libcs have no
`malloc_trim`) is called once per case switch at the end of `load_case`.
Deliberately **not** per time step: inside one case the arrays just freed are
the same size as the ones about to be allocated, so leaving them in the arena is
what keeps time stepping cheap.

**Still open — leaving the viewer frees nothing.** It is a shared single session
(one pipeline, one camera) with no teardown on disconnect, and trame's array
cache is pruned *only* inside `get_view_state` (trame_vtk
`modules/vtk/protocols/local_rendering.py`), for arrays with refcount 1 that are
>20 s old. Close the tab and nothing prunes, so the last case's arrays stay
cached; it self-cleans once you come back and interact. trame_server exposes no
`on_client_exit` hook (only `on_server_exited`), so a trim-on-leave would need
either a client-count heartbeat or the arrays to be dropped some other way.
Sizing rule of thumb meanwhile: **the viewer's RSS settles near the largest
single case opened, not the sum.**

## Backend integration

### Decided (2026-08-11, with Niklas)

Integrating into the EQUA CFD frontend (repo `cfd-restful-backend`, the
`cfd-backend`/`cfd-file-server`/`cfd-frontend` images):

- **Shape:** FoamViz becomes a **4th service, `cfd-viz`**, behind the nginx
  `cfd-frontend`, which proxies `/viz/*` (HTTP **and** WebSocket upgrade) to it.
  It reads cases straight off the shared `CFD_HOME` volume — no OpenFOAM install
  needed, `vtkOpenFOAMReader` reads the case files directly. React embeds it as
  a **full-page** view via `<iframe src="/viz/?case=<id>">`.
- **Process model:** **shared single session** (few users) for now — one
  `main.py --server --data $CFD_HOME` process. Per-session launcher is the later
  productisation, not now.
- **A1 done:** `?case=<name>` deep link — `_preselect()` + an aiohttp request
  middleware in `_add_http_routes` (server-side; window.location is unreachable
  from Vue expressions). Verify with the screenshot filename, see below.
- **A3 done:** service robustness. `main.py --server` no longer exits on an
  empty `--data` (interactive use still does); the app stores `case_root`,
  clears `_loading` on an empty start, and `_preselect()` re-scans the case root
  (`_rescan_cases()`, also refreshing the drawer) when the name is unknown — so
  cases created after startup resolve. Note: only lazy rescan on deep link; the
  drawer does not auto-poll for new cases.
  - **Empty-case startup must not crash the serializer (2026-09-05).** Serving
    empty used to crash on `on_server_ready`: trame's local-render serializer
    walks every actor and calls `mapper.GetInputAlgorithm().Update()`, and
    `surface_mapper` had **no input** until `update_surface` ran (never, with no
    case) → `AttributeError: 'NoneType'` → the process exits and the container
    **restart-loops**. This bit Niklas on Podman/WSL, where the case dir came up
    empty (a mis-mounted volume) — nothing to do with WSL, GL, or the harmless
    EGL/X11 probe warnings (VTK falls back to OSMesa; that path is fine).
    `pipeline._bootstrap_empty()` now defaults `surface_mapper` to `surface_input`
    and seeds the `case.internal`-fed filters (cutter/crinkle/contour/tracer +
    glyph_probe source) with an empty `vtkUnstructuredGrid`, so the scene
    serialises to empty geometry cleanly (also kills the `vtkCutter` "0
    connections" ERR spam). Guarded by a `test_pipeline.py` invariant: with no
    case loaded, no actor's mapper has a `None` input algorithm.
- **A2 pending (needs a live proxy, likely Niklas's env):** serving under the
  `/viz/` base path behind nginx — the wslink client must open its WebSocket and
  load assets relative to the mount.

**Verifying A1 without a browser:** the PNG route names its file
`foamviz-<case_name>-t<time>.png`. So: start `main.py --server --data data`,
`GET /?case=s2`, then `GET /foamviz/screenshot.png` and read the
`Content-Disposition` filename — it should contain `s2`.

### Remaining open questions

Related memories: `project_foamviz`, `project_openfoam_api` (Flask job-control
API on :5001) and `project_iceopenfoam` (EQUA's OpenFOAM-13 extension libs).

1. **Which backend, exactly?** The Flask job-control API, ICEOpenFOAM, or the
   IDA ICE client itself? That decides whether FoamViz is a service the API
   proxies to, or a component the client embeds.
2. **Process model.** A Trame server is one long-lived process holding one VTK
   pipeline and one camera — inherently single-user. Concurrency needs the
   trame launcher (process per session). Deciding this late is painful.
3. **Case discovery.** `find_cases()` scans for `system/controlDict`. The
   backend addresses cases by UUID directory with a `metadata.json`
   (`CASE-ID`, `N-CELLS`, `TURB-MODEL`, `ZONE-NAMES`, `END-ITER`, `CFD-OK`…)
   and a `building.opf` (`GLOBAL`/`MESH`/`SOLVER`/`GEOMETRY` sections). A real
   integration reads those instead — `metadata.json` alone gives the case list,
   cell count and readiness without touching the mesh.
4. **Zones — not a gap. Do not "fix" this.** An IDA ICE *zone* and an OpenFOAM
   *cellZone* are unrelated concepts that share a name. IDA ICE zones are rooms
   in the building selected for CFD analysis; they appear as `ZONES` under
   `GLOBAL` in `building.opf` and as `ZONE-NAMES` in `metadata.json`. They are
   an input-side grouping, not a mesh partition.
   Per Niklas (2026-08-11): **treat cases as single-region, single-zone.**
   The reader's `SetReadZones(1)` / `SetCopyDataToCellZones(1)` exist and work,
   but they address OpenFOAM `cellZones`, which is a different question and not
   one that is being asked. An earlier version of these notes had this wrong and
   called it the top integration gap; it is not.
5. **Decomposed cases — done & VERIFIED.** `case.py` picks `vtkPOpenFOAMReader`
   in `DECOMPOSED_CASE` mode when `_is_decomposed()` finds the newest time only
   in `processor*` (mirrors backend `time_in == 'parallel'`; detected from the
   filesystem, not the API, to keep cfd-viz decoupled). It is a
   `vtkOpenFOAMReader` subclass, so the rest is unchanged. Verified with the
   persistent venv on a real decomposePar'd hotRoom (root time 0, processor0 at
   1730): detection True, `vtkPOpenFOAMReader` present in the wheel, reads all
   processor dirs serially → 32 000 global cells, all fields. Drawer caption
   shows "· decomposed" when active.
6. **Render mode default.** Settled enough for now: Niklas reports client-side
   (vtk.js) rendering is "impressive already" at 12 M cells, so default to
   `local` and treat server mode as the fallback for GPU-less clients rather
   than the other way round.
7. **Comfort metrics.** Draught rate, PMV/PPD, operative temperature are what
   the IDA ICE side actually reports, and none are OpenFOAM fields. They would
   be derived arrays computed at load — the same mechanism as `FoamVizColor`,
   so the hook already exists (`pipeline.apply_color_array`).

## Slider debounce — DONE (2026-08-14)

The heavy sliders no longer re-render on every drag tick. `_slider(debounce=True)`
binds the thumb (and its live label) to a `<name>_draft` mirror and commits the
real state var only on release, via the VSlider `@end` event
(`end="<name> = <name>_draft"` — client-side JS, one flush). The real-var change
then runs the heavy handler once, behind the busy overlay. Debounced:
`plane_position`, `contour_count`, `stream_seeds`, `stream_length`, `glyph_count`
(listed in `_DEBOUNCED`). Cheap render-only sliders (opacity, tube width, glyph
size) stay live. `_sync_drafts()` (called from `load_case`) re-mirrors the drafts
so a slider follows programmatic changes instead of snapping back to a stale
drag value; it's the hook for the reset the To-do list will add.

Note on trame 3.2.5: `VSlider._event_names` is empty at class level — events are
resolved per instance, so `end=`/`start=` bind fine (verified: the template
emits `@end`), the earlier `_event_names` claim was wrong.

The **time** slider is deliberately left live: playback steps it programmatically
and `tests/browser_check.py` step 7 drives it with keyboard arrows expecting a
live label. A mouse-drag of it on a big case would still flood — revisit if it
bites (it would need the same draft treatment plus a per-step draft resync in the
play loop, and a client-side label mapping index→time).

Alternatives rejected during the original build: server-side throttle/debounce
(still pays the round trip, feels laggy not stepped) and lowering default counts
(treats the symptom).

## Case-report figures — in progress (2026-08-16)

Goal (with Niklas): build a scene, "Add to case report", and have it appear in
the cfd-frontend case report — a **frozen** interactive snapshot (rotate/zoom, no
toggles). The headline deliverable is a **single self-contained `.html` export**
of the report with the scenes inlined (opens offline, no running services); the
existing browser print-to-PDF stays (scenes show as their poster PNG there).

Format decided: **vtk.js scene** (`.vtkjs`), not glTF — it is the same vtk.js
renderer the client already uses, so the report looks identical to the live view
(glTF risks PBR-shading the flat CFD field colours). Storage: **inside the case**,
`<case>/report/` (Niklas confirmed cfd-viz has write access to `CFD_HOME`).

**DONE — capture (this repo).** The toolbar "Report" button → `add_to_report`
writes per figure into `<case>/report/`: `figure_NN.png` (poster/print) and
`figure_NN.json` (caption + field/component/range/preset/n_colors + gradient +
ticks, so the report redraws the colour bar from `colors.py` — the poster is the
3D view only, no legend). Caption comes from a state field or `_auto_caption()`.
The server render window's camera already tracks the client's orbit in local mode
(that's also how the Client→Server switch works), so the server-side screenshot
frames what the user set up. (Do NOT use `push_remote_camera_on_end_interaction()`
for this — see the camera trap under "Trame traps": it resets the client's centre
of rotation on every mouse-up. It was removed 2026-08-18.)

**vtk.js scene export is MOTHBALLED (2026-08-17):** `figure_NN.vtkjs`
(`vtkJSONSceneExporter` output, zipped) is no longer written — the interactive
report viewer is shelved, and unused scenes just pile up on disk. Gated behind
`EXPORT_VTKJS = False`; `write_vtkjs` and the zip code are kept, so it's a
one-line flip to re-enable once a viewer is built.

- **Trap:** `vtkJSONSceneExporter` leaves the render window in a state that
  **segfaults** a subsequent `vtkWindowToImageFilter`. So capture the PNG
  *before* the scene export, and `screenshot()` now calls `render_window.Render()`
  first (also needed because, with no live client driving it, the window may be
  unrendered during a headless export).

**TODO — the rest (cfd-backend repo).** (2) backend route to list/serve a case's
`report/` figures; (3) a Figures section in `frontend/src/pages/Report.jsx` (PNG
in print, interactive vtk.js viewer on screen); (4) the single-file HTML export —
assemble the React-rendered tables/charts + one inlined vtk.js viewer +
base64-embedded scenes into a downloadable `.html` (client-side assembly reuses
React's rendering; the viewer is one small vtk.js bundle inlined once).

## To-do list

Things for future consideration and work, added by Niklas. Remove items when
implemented, and feel free to fix formatting. We will fix and remove items as we
go, and Niklas may add more. Read the whole list before starting — the ordering
does not necessarily reflect a good implementation order.

### Widget re-arrangement — DONE 2026-08-15

All of the below shipped (see "Widget re-arrangement — DONE" implementation note):

- ~~Move the viewport buttons (X / Y / Z / Iso) and the time control to a bottom
  bar.~~ Floating bottom bar over the 3D view (`_bottom_bar`).
- ~~Add widget-type buttons (slice, isosurfaces, streamlines, boundary) to the top
  bar, revealing that widget's submenu in the side bar.~~ Top-bar tool selector
  (`active_tool`), sections shown via `v-show`.
    - ~~Merge "Room shell" and "Boundary patches" → "Boundary".~~
    - ~~Merge "Cut plane" and "Slice" → "Cut plane".~~
- ~~Colour settings stay permanently in the side bar, submenus below them.~~
    - ~~Integer input for number of colours ("banded" colouring).~~ `n_colors`
      (0 = smooth), banded via flat transfer-function nodes; legend bands too.

### Slider behaviour

- ~~Delay slider actions until the slider is released.~~ **Done 2026-08-14** for
  the heavy geometry sliders (see "Slider debounce — DONE" above).
    - ~~Draw a plane outline that follows the slider during the drag.~~ **Done**,
      then made fully client-side **2026-09-04** — the red frame is a declarative
      vtk.js child of the view, slid in the browser with no server round trip (see
      the "Cut-plane slider smoothness — DONE" note).
    - ~~Numeric input for the plane position in world coordinates.~~ **Done, then
      reworked 2026-08-16** into X/Y/Z world-point fields + an Apply button, with
      the world point (not a fraction) as the source of truth — see the plane
      note under implementation notes. Fields are inert until Apply; the slider
      previews live and auto-applies on release; a normal switch keeps X/Y/Z.
    - (Optional, not done) debounce the time slider too — see the note in the
      "Slider debounce — DONE" section.

### Visualisation options

- ~~Boundary visualisation: default to "cull front face", with a toggle.~~
  **Done 2026-08-14** — "Cull near walls" switch in the Room shell panel,
  default on (`surface_cull` → `SetFrontfaceCulling`).
- ~~Toggle between point-interpolated values and true cell values.~~
  **Done 2026-08-14** — "True cell values" switch in the Colour panel
  (`use_cell_data`); bakes a cell `FoamVizColor` and switches the surface/slice
  mapper association. Contour/streamlines/glyphs stay on point data.
- ~~Slice-plane visualisation: when the mesh is shown, switch to a "crinkle
  slice".~~ **Done 2026-08-15** — the "Mesh (crinkle)" switch on the Cut plane
  tool feeds the slice from a crinkle extractor (`vtkExtractGeometry` since
  2026-08-17) instead of the cutter (see the implementation note below).

## To-do — implementation notes (Claude)

Grounding notes for the list above; **not yet implemented**. Code pointers are to
the tree as it stands (line numbers drift). A suggested order is at the end.

### Slider behaviour — do this first

Already scoped under "Known work, deferred" above (the `VSlider` `start`/`end`
draft-variable approach — no server round trip during the drag, one
`update_scene()` on release). Worth doing first: it's the biggest felt win, and
it makes the **new busy overlay** pleasant on heavy sliders — otherwise a drag
flashes the overlay every tick, since `plane_position`/`contour_count`/
`stream_seeds`/`glyph_count` are in the "heavy" handler group now. `_slider()` in
`app.py` is shared by every panel, so add a `debounce=True` parameter to it
rather than editing each slider; keep the cheap sliders live.

The cut-plane controls were then **reworked (2026-08-16)** into a single clean
model — the earlier fraction/`plane_coord` bidirectional sync was fiddly. Now:

- **Source of truth = the world point `plane_x`/`plane_y`/`plane_z` + the normal
  `plane_axis`.** Only the active-axis coordinate positions the cut (the plane is
  axis-aligned); the other two are remembered, so switching the normal keeps
  them. `_active_coord()` reads `plane_<axis>`; everything funnels through
  `update_scene()`, which passes it to `update_plane` as a **world coordinate**,
  not a fraction (no fractions anywhere).
- **The X/Y/Z fields are inert** (no `@change`) until **Apply** (`plane_apply` →
  `_busy_call(_do_plane_apply)`), which clamps into range, reflects the value on
  the slider (`_sync_plane_ui`) and redraws. That is the "debounce" for typed
  input — keystrokes never redraw.
- **The slider drives `plane_slider` live, with NO `@change`** (ranged by
  `axis_min`/`axis_max`). The client-side red frame follows it in the browser
  (zero round trips); release (`@end` → `ctrl.plane_slider_release`) writes the
  active coordinate and cuts once. See "Cut-plane slider smoothness — DONE" for
  the outline mechanism and the mount gotchas.
- **Axis switch** (`_on_plane_axis`) just calls `plane_apply`, which re-ranges the
  slider to the new axis and redraws — X/Y/Z untouched.
- Kept axis-aligned; a free plane (arbitrary normal) would be a much bigger
  change and isn't what was asked. Fields take decimals (a slice needs sub-metre
  precision), not integers.

### Widget re-arrangement — DONE 2026-08-15

- **`TOOLS` is the single source** for the six tools (key, title, icon). It
  drives both the side-pane **tool stack** (`_tool_stack()`, bound to
  `active_tool`) and the settings sections, so they can't drift. Each tool has a
  `_tool_<key>(title, icon)` builder; `_drawer()` loops `TOOLS` and wraps each in
  a `v-show` div. `TOOL_VISIBLE` maps each tool key → its actor-visibility state
  var (the eye toggle).
- **Layout (rearranged 2026-08-21):** global **Colour** settings live in the
  **top bar** (essentials inline: Field/Component/Colour map/Bands/Rescale; the
  rest — range mode, min/max, cell values — behind an "Options" `VMenu` with
  `activator="parent"`). The **side pane** holds the vertical tool stack at top
  (each row: a tool button that selects its settings + an **eye toggle**,
  `mdi-eye`/`mdi-eye-off`, that flips the actor's `*_visible` var directly), the
  selected tool's settings below, then the collapsible Lighting panel.
- **Tools are control-only, not visibility.** Selecting a tool only changes which
  settings show (`v-show`, so panels stay mounted and keep their state).
  Visibility is the separate per-row eye toggle, so a slice + streamlines +
  isosurfaces can all be visible while you tweak just one. The eye sets the
  `*_visible` state var client-side; its `@change` handler (`_on_cheap` /
  `_on_heavy`) updates the actor — no per-tool render logic.
- **Cut-plane-hub question, resolved:** the plane controls live in the "Cut
  plane" tool (merged with the slice, per spec), and the "Slice, stream seeds and
  arrows all sit on this plane" caption stays. Moving the seeding plane while
  configuring streamlines/arrows means a hop to the Cut plane tool — acceptable,
  and it kept the layout duplication-free. If that hop proves annoying, the tidy
  fix is to promote the plane block to a second persistent section (like Colour),
  *not* to repeat the control in three tabs.
- **Bottom bar** is a floating strip inside `.foamviz-stage` (`_bottom_bar`),
  matching the legend/mode overlays — not the Vuetify `footer` (which carries the
  "Powered by trame" branding, kept). Camera presets left, time group right. The
  `js-time-slider` / `js-time-label` / `js-refresh-times` test hooks moved with
  it; the time slider stays live (not debounced) so playback and the keyboard
  browser-test step still work.
- **Sections:** `_panel()` (expansion panel) was replaced by `_section(title,
  icon)` — a plain header + body div — since only one tool shows at a time. The
  browser test's `panel()` accordion helper became `tool()` (clicks the top-bar
  button).
- **Banded colouring:** `n_colors` state (0 = smooth). `colors.color_transfer_function`
  bakes banding into the CTF *nodes* as flat plateaus (two coincident-value-safe
  nodes per band) — NOT `vtkDiscretizableColorTransferFunction`, whose `DeepCopy`
  (used by `set_color_range` on every range change) drops the discretize flag
  (verified). `css_gradient` gained a matching stepped branch, so the legend
  bands too.

### Visualisation options

- ~~**Cull front face**~~ — **Done.** `surface_cull` (default True) →
  `surface_actor.GetProperty().SetFrontfaceCulling`, in the cheap/live handler.
- ~~**Point-interpolated vs true cell values**~~ — **Done.** `use_cell_data`
  (cheap handler). `apply_color_array` bakes `FoamVizColor` into point data
  always, and into cell data too when the toggle is on; `_color_by_association`
  switches the surface/slice mapper between `UsePointFieldData` and
  `UseCellFieldData`. Cell arrays were verified present on the internal mesh,
  boundary patches, and the cutter output. Contour/streamlines/glyphs keep
  reading the point array. (Note: auto-range still samples point data — a cell
  extreme can slightly exceed it; not worth special-casing.)
- ~~**Crinkle slice**~~ — **Done 2026-08-15; extractor swapped 2026-08-17.** The
  "Mesh (crinkle)" switch feeds the slice from a crinkle extractor sharing the
  cutter's `vtkPlane` (position slider drives both), via a `vtkGeometryFilter`
  → polydata for the shared slice mapper; `update_slice` swaps the mapper input
  when `slice_edges` is on (a heavy/overlay handler — real extraction).
  - **Now `vtkExtractGeometry` + `ExtractBoundaryCellsOn`/`ExtractOnlyBoundaryCellsOn`**
    (general, single-threaded, all cell types — ParaView-style).
  - **Was `vtk3DLinearGridCrinkleExtractor`** (threaded 3D-linear fast path). It
    **hung the trame server** — steady memory growth, no output, container
    restart needed — even on small cases, while computing in ~2 ms *headlessly*.
    Could not reproduce headlessly (so unconfirmed), but the threaded fast path
    (vtkSMPTools) inside the async server is the prime suspect, and it is
    linear-cells-only. The general filter removed the thread pool and gained
    all-cell-type robustness. If it *still* hangs deployed, instrument the
    `crinkle_surface.Update()` path (it runs during render, so no app-level log
    today) or consider disabling crinkle behind a flag.

### Suggested order

1. ~~**Slider debounce**~~ — **done** (core; plane outline + numeric XYZ remain).
2. ~~**Cull-front-face** and **point/cell toggle**~~ — **done 2026-08-14**.
3. ~~**Widget re-arrangement** (+ banded colouring)~~ — **done 2026-08-15**.
4. ~~**Crinkle slice**~~ — **done 2026-08-15**.
5. ~~Slider refinements: plane outline during drag, numeric XYZ plane position.~~
   — **done 2026-08-15**.

**The backlog is now empty** (bar the optional time-slider debounce). All of it
is server-side verified but awaits Niklas's browser confirmation.
 
