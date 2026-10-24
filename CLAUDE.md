# CLAUDE.md

This repository follows the shared Equa platform conventions. The canonical
sources live in two sibling repositories — resolve them by repository **name**
(the checkout location differs between the Linux devcontainer and a Windows
host, so no absolute path is given):

- **equa-ci** — build, commit, code/comment, CI, and OpenSpec workflow conventions.
  Root `CLAUDE.md` (canonical:
  <https://github.com/equa/equa-ci/blob/beta/CLAUDE.md>).
- **equa-platform-docs** — documentation, wiki, and doc-surface conventions.
  Root `CLAUDE.md` (canonical:
  <https://github.com/equa/equa-platform-docs/blob/beta/CLAUDE.md>).

## Repository-specific notes

- `equa-cfd-viewer` is a service shipped as the container image
  `ghcr.io/equa/equa-cfd-viewer`; it publishes no package. Its `Dockerfile` and
  `.dockerignore` are its own. A beta release deploys it to dev through
  `equa-deployment`, which holds its deployment files.
- **Documentation and OpenSpec artifacts live in `equa-platform-docs`**, under
  `docs/platform/modules/cfd-viewer/`. This
  repository keeps only `docs/README.md`; `openspec/specs` and
  `openspec/changes` are symlinks into that tree, so the `openspec` CLI works
  here when `equa-platform-docs` is checked out beside this repository and the
  links dangle harmlessly otherwise. A change's proposal, design, tasks and
  spec deltas are therefore committed in `equa-platform-docs`, while the code
  is committed here.
- Build orchestration: `uv run doit <task>` — run `uv run doit list` for all
  tasks; `uv run doit ci` runs the local CI suite.
- The `equa-ci` package is installed from the Azure DevOps `equa-dev` feed via
  `[tool.uv.sources]` in `pyproject.toml`.
- This repository is scaffolded from the Copier template in `equa-ci/template/`
  (answers in `.copier-answers.yml`). Never hand-edit a templated file to
  diverge; change the answer or the template.


Browser-based post-processing for OpenFOAM cases, aimed at an IDA ICE CFD
backend. See `README.md` for what it does and how to run it; this file records
what cost time to discover.

## The Trame front end is mothballed (2026-09-12)

This repo is a **React + three.js client over a Python/VTK extraction server**.
`foamviz/` reads OpenFOAM and drives the filters, `server/` packs geometry onto
the wire (both under `src/equa/cfd_viewer/` since 2026-10-21), `web/` draws it.

```
uv run cfd-viewer --data ./data          # or, the old way: python main.py --data ./data
uv run doit ci                           # the gate: lint, tests, wheel, cockpit contract
```

The original **FoamViz (Trame + vtk.js) front end lives on the frozen `trame`
branch** and is gone from here. It was the whole app until 2026-09-08, then a
second entry point while the three.js client caught up, and is now retired. If
you need it — to compare behaviour, or to recover a feature that was never
ported — `git show trame:foamviz/app.py`.

What that means in practice:

- **`pipeline.py` no longer has a second consumer**, so its API is free to
  change. The rule that kept it additive ("both front ends call this") is
  retired with the branch; `tests/test_pipeline.py` is now the only guard.
- **Its render half is dead weight.** The renderer, light kit, actors, mappers,
  triad, camera helpers, `screenshot`, `write_vtkjs` and the offscreen render
  window exist only because Trame rendered server-side. This server never calls
  `Render()`. Stripping them is a real simplification — see "Not done,
  deliberately".
  **Resolved 2026-09-22 — the render window is lazy now.** VTK needs a GL
  backend present to *construct* a render window, not only to render into one,
  so building one in `_build_scene` made `libosmesa6`/`libgl1` a hard
  dependency of everything — including the scene server, which never renders.
  `render_window` and `interactor` are `@property` now, built on first access,
  and `cfd-viz` ships without either package. `screenshot`, `write_vtkjs` and
  `pick_cor` still get a window the moment they ask.
  **Gone 2026-10-23:** the render window, interactor, `pick_cor`, `set_view`,
  `screenshot` and `write_vtkjs`. Only `tests/test_pipeline.py` called them,
  and only to test themselves -- they were the one reason any suite needed GL.
  The suite now swaps `vtk.vtkRenderWindow` for a stub that raises, so a render
  window cannot creep back. What is left of the render half (renderer, light
  kit, actors, mappers, triad, `reset_camera`, theme/lighting setters) is
  GL-free to construct; stripping it is a simplification, not a dependency fix.
- The `trame` branch is **frozen**, not maintained. If a pipeline fix ever
  matters there, cherry-pick it deliberately.

## The standalone-viewer change, phase 1 (2026-10-20 .. 22)

`openspec/changes/archive/2026-10-23-standalone-viewer/` is the design record
(proposal, design D1-D11, specs, and `tasks.md` with the measured result of
every task); its specs are now the baseline in `openspec/specs/`
(`case-sources`, `start-screen`, `viewer-build`). Phase 1
turned the cockpit-only viewer into a package that can also stand alone;
phases 2 (Windows desktop bundle) and 3 (hosted uploads) are separate changes
still to come. What phase 1 left in the code, and what not to undo:

- **Package layout.** `src/equa/cfd_viewer/{foamviz,server}`, installable as
  `equa-cfd-viewer` (`pyproject.toml`, `uv.lock`, console script `cfd-viewer`).
  `hatch_build.py` puts `web/dist` into the wheel as `equa/cfd_viewer/web` and
  refuses a wheel without it. The client is served from, first match:
  `WEB_DIST_DIR`, the packaged copy, the checkout's `web/dist`.
- **The cockpit contract -- read before moving files.** cfd-backend's `cfd-viz`
  image is built from a clone of this repo's **`main`** (`git clone --branch
  main`), checks for `web/package.json`, `web/package-lock.json`,
  `requirements.txt` and `main.py`, runs `pip install -r requirements.txt` on
  Python 3.14 and `python main.py --server --data "$CFD_HOME" ...`, and its
  smoke test `tests/smoke/viz.sh` does `from foamviz.pipeline import
  FoamPipeline` from the checkout root. Hence three shims: `main.py`, the
  top-level `foamviz/` alias (registers the real submodules, so classes are
  identical), and `requirements.txt` exported from the lock
  (`requirements_check` keeps it in sync). `tests/check_cockpit_contract.py`
  (`doit cockpit_contract`) repeats that build and runtime on a clone of HEAD,
  runs cfd-backend's own `viz.sh` (from `../cfd-backend`, else the byte-identical
  copy in `tests/cockpit/`, which fails the check when stale; cfd-backend is
  **private**, so CI cannot clone it), and drives `/viz/?case=<uuid>&theme=light`
  through a proxy that strips `/viz/` like the cockpit's nginx. Every commit
  that reaches `main` ships with the next cfd-backend build. The way out is
  `docs/cfd-backend-switch.md`; the shims go only after that.
- **The HTTP API only grows.** `tests/test_contract.py` replays 56 recorded
  exchanges (`tests/contract/hotroom.json`; first recorded on `main` before
  anything moved, which proved the package move changed no extraction byte).
  It always fails on a lost field, a changed JSON type or a changed status;
  new data goes in new fields (`entries`, `caseId`, `caseLabel`), and the two
  allowed changes are in `contract.ALLOWED_CHANGES`. Values and geometry/LUT
  bytes are compared only while the inputs match the snapshot's fingerprint
  (hotRoom's files, VTK/numpy/matplotlib versions); after a data change or
  upgrade the replay checks shape only and says so -- re-record with
  `tools/record_contract.py` and review the snapshot's diff (a dropped or
  retyped field there is an API change, not a data change). Case ids are
  compared by shape even then: they hash the replay's temporary root.
- **Case sources** (`server/sources.py`). `DirectorySource(roots, depth)`
  replaces `find_cases` in the server (`find_cases` stays, and depth 1 is
  tested to equal it). Ids are 12 hex chars of sha1(root, relative path);
  `?case=` takes an id or a name exactly one case has, else **404 with the
  reason** -- the old silent fallback to the first case is gone (agreed with
  Niklas). **Discovery mirrors cfd-backend's finder** (`cfd_cases.find`, an
  `rglob` of `CFD_HOME`), bounded at `SERVER_SCAN_DEPTH` = 4 levels: test-suite
  cases live in `CFD_HOME/tests/<name>`, which a one-level scan never saw -- the
  phase-1 gate found a stack whose every case was there. It prunes at cases,
  `processor*` and time dirs; hidden dirs are searched, as the backend does.
  Names must be unique (the backend assumes it too); a duplicate is a 404 with
  the ids. Labels
  come from `metadata.json` `NAME`; cell counts from the `nCells:` note in
  `polyMesh/owner`'s header (summed over `processor*`), then
  `case_info.ncells`. **`N-CELLS` in metadata.json is stale (observed 0) --
  never use it.** Listing constructs no reader (0.4 ms for 15.8 M cells), and a
  request for the already-open case skips the directory walk.
- **Cases are read in place** -- no `.foam` marker is written (see the trap
  below). Read-only shares and `:ro` mounts work.
- **Run modes.** `--mode server` is the only mode and the default (`--server`
  is an alias), so an empty `--data` no longer exits.
- **Platform mounting (2026-10-23).** `ROOT_PATH`/`--root-path` registers every
  route twice, under the prefix and at the root (Starlette's `root_path`
  behaviour, which the other platform services get for free): Traefik forwards
  `/viewer/...` unstripped, the image HEALTHCHECK asks `/health`. `/viewer`
  308-redirects to `/viewer/` *with its query* (`with_path` drops it -- a test
  caught that); the client's relative URLs need the slash. Settings come from
  `CASE_ROOTS` (os.pathsep list), `SCAN_DEPTH`, `PORT`, `ROOT_PATH`; the CLI
  wins. **Not `HOST`**: equa-deployment uses it for the public domain and zsh
  sets it. The Dockerfile's CMD is bare `cfd-viewer` so env settings apply;
  cfd-viz's `main.py --server --data "$CFD_HOME"` is unaffected. `/health` and
  `/api/config` (mode, version, `canAddFolders`, `canUpload`) are cheap and
  open nothing.
- **The start screen** (`web/src/ui/StartScreen.jsx`) shows when there is no
  `?case=` and not exactly one case, or after a 404. It **overlays** `.app`
  rather than replacing anything, because the React tree must not change shape
  once the Viewer exists (see "Traps in this client"). It costs no
  `/api/meta`/`range`/`scene` request (checked). After the first `/api/meta`,
  `request.case` holds the **id**, so every later call is unambiguous; a pick
  writes `?case=<id>` with `history.replaceState`.
- **matplotlib stays** (design D10, a drop was proposed and withdrawn): the
  `vtk` wheel itself requires it, and the rule is one locked dependency set for
  every deployment.
- **Tooling** follows equa-ci by name, implemented locally: `dodo.py`
  (`lint_py` = ruff check only, no format check, as in equa-ci; `test_py`,
  `test_ui`, `package`, `requirements_check`, `cockpit_contract`, `ci`), the
  template's `.pre-commit-config.yaml` and `commitlint.config.js`, and a
  `Dockerfile` on cfd-viz's base and uid. doit keeps its state in `.doit.json`
  (JSON backend): dbm files differ between Python 3.12 and 3.13+.
- **Host and dev container:** since 2026-10-23 development, tests and commits
  happen in the container (see "Environment"); a `.venv` or hook installed on
  one side does not work on the other. Note `pre-commit run
  --commit-msg-filename` does NOT test commitlint (the hook lints
  `.git/COMMIT_EDITMSG`); test it with a real commit in a throwaway clone.

## Working agreements (Niklas, merged from todo.md 2026-09-09)

Standing instructions for how to work in this repo, not one-off requests.

- **Ask before big changes.** "I might be asking for silly things." A request
  that turns out to need a new dependency, a new rendering path or a schema
  change is a conversation, not a fait accompli.
- **Reuse and generalise before adding.** Search for an existing function or
  class first — writing a new one is always easier than finding the old one, and
  that is exactly the temptation to resist. Prefer generalising what is there,
  **as long as the argument list does not grow much**.
- **Prefer a class to a long argument list**, and to threading arguments through
  several call layers.
- **Prefer local (three.js) rendering to a server call** wherever a goal can be
  reached that way without complicating the code "too much" — ask when the
  trade-off is unclear. This is the same instinct the client/server table
  encodes; when in doubt, the client column is the better place to land.
- **Be careful with API changes to `foamviz/`.** `case.py`, `pipeline.py` and
  `colors.py` are the CFD layer, and the thing this project has proven is
  durable — it survived a whole front-end change untouched. It no longer has a
  second consumer (the Trame app retired with its branch), so the old "keep it
  additive" rule is relaxed; `tests/test_pipeline.py` is now the only guard, so
  lean on it.

## Resuming in a new workspace

The project lives at `/workspace/cfd-viewer` and is under version control
(`origin/main` on GitHub). What actually needs to travel is about **1 MB**:

```
CLAUDE.md README.md pyproject.toml uv.lock requirements.txt hatch_build.py
dodo.py Dockerfile .dockerignore .gitignore .pre-commit-config.yaml
commitlint.config.js main.py foamviz/ src/ web/ tests/ tools/ docs/ openspec/
data/                                                 # both demo cases, results included (~0.7 MB)
```

Everything else rebuilds:

- `web/node_modules/` — `cd web && npm install`
- `web/dist/` — `cd web && npm run build` (the Python server serves it from there;
  without it, `/` shows a short "no client build" page instead)
- the Python env -- `uv sync` (see "Environment" below)

`tests/test_pipeline.py` hardcodes the demo case (500 cells, 3 patches,
T range 300–600 K, more than one time step). Point it elsewhere and those
specific assertions need updating — the checks are deliberately concrete.

**The rule for test data (2026-10-22):** a check may depend on hotRoom's
*definition* -- the versioned files: mesh size, patches, fields, boundary
conditions -- because that changes only by a deliberate edit. It may not
depend on run artefacts that can change on a mere rerun or trim: how many time
steps were kept, timings, the exact solved values. The one place that does
compare values, the recorded HTTP contract, does so only while a fingerprint of
the case's files and of VTK/numpy/matplotlib matches the recording; otherwise
it checks shape only and says so (`tests/contract.py`).

## Environment

Since 2026-10-23 Niklas works in the same dev container as Claude (`/work`,
Ubuntu 24.04, Python 3.12, **no root**, no system GL or Chromium libraries,
no exposed ports). `/work` is the only host bind mount; home is container-local.

- **Everything extra lives in `/work/.tools`**, so it survives a rebuild:
  - `node/` -- Node 24 (the system `node` is 18, too old for Vite 8:
    "`node:util` does not provide an export named `styleText`"). `~/.bashrc`
    puts it on `PATH`.
  - `bin/micromamba` + `testenv/` -- Chromium's system libraries and fonts from
    conda-forge (nss, atk, at-spi2, dbus, the X libs, libxkbcommon, alsa-lib,
    libgbm, fontconfig; ~120 MB). **No Mesa**: nothing needs it.
  - `ms-playwright/` -- `playwright install chromium-headless-shell`.
  - `cfd-test-env.sh` -- `source` it before the suites: `LD_LIBRARY_PATH`,
    `FONTCONFIG_FILE` (else Chromium aborts in SkFontMgr),
    `PLAYWRIGHT_BROWSERS_PATH`, and **unsets `WAYLAND_DISPLAY`** (leaks in
    from the host and breaks Chromium's SwiftShader, so no WebGL).
- **The Python env is `uv sync` -> `.venv`.** A venv cannot move: its scripts
  hard-code the interpreter path, and `uv sync` does not rewrite them. "Failed
  to spawn: doit / No such file" means a venv built elsewhere -- `rm -rf .venv
  && uv sync`. The pre-commit hooks record an interpreter path too; they are
  installed from the container, so commits belong on that side.
- Removing a conda package can take a shared library with it (removing Mesa
  removed `libgbm`), and a plain reinstall may not restore the symlinks:
  `micromamba install --force-reinstall`.

### VTK packaging — checked 2026-08-11, don't re-derive

- `pyproject.toml` (and the `requirements.txt` exported from it) asks for plain **`vtk>=9.4`**. Since 9.4 the stock PyPI
  wheels contain EGL *and* OSMesa render windows and fall back X11 → EGL →
  OSMesa at runtime. They **dlopen** libEGL/libOSMesa instead of bundling, so
  anything that RENDERS needs `apt-get install libosmesa6`. A container that
  only *extracts* does not — but only since the render window became lazy
  (2026-09-22). Before that, merely constructing `FoamPipeline` required a
  backend, which is why removing the packages on 2026-09-21 failed outright.
  `cfd-viz` ships without them, and since 2026-10-23 so do the tests.
- `vtk-osmesa` is a dead end and should not be the default: not on PyPI
  (`--extra-index-url https://wheels.vtk.org`, which 301s to a GitLab package
  index), frozen at **9.3.1**, wheels only for cp36–cp312 on linux x86_64 and
  win amd64. Python 3.13+/macOS/arm64 cannot resolve it at all.
- OpenFOAM 13 was at `/opt/cfd/OpenFOAM-13` in the old dev image; the /work container has none (regenerating demo data needs the host or an OpenFOAM image).

## VTK-Python traps hit here

- **Default lighting is a single camera headlight** → faces angled away go
  black (the boundary shell especially). Fixed with a `vtkLightKit` (key + fill
  + back + head, like ParaView) + `TwoSidedLightingOn` on the renderer, plus an
  **ambient floor** on the lit actors. Controls live in a **collapsible
  "Lighting" panel** at the bottom of the side pane (hidden by default): a **Light kit**
  toggle (default on; off → `RemoveAllLights` → VTK's default headlight) and
  Ambient/Diffuse sliders (default 0.3/0.7) → `set_light_kit` / `set_lighting`.
  All of this lives in `pipeline.py`'s render half, which nothing in this repo
  uses any more (see "mothballed"). The client lights the scene itself in a
  shader; the note survives because the *rule* still applies there — two-sided
  key + fill over an ambient floor, so no face reads as black.
- **Never pass a freshly constructed source inline.**
  `glyph.SetSourceConnection(vtk.vtkArrowSource().GetOutputPort())` **segfaults**
  — Python collects the temporary while the pipeline still references it. Keep an
  attribute for every source. This cost the most time of anything in the build.
- **Do not colour by vector mode on a lookup table.** Same reason. `pipeline.py`
  bakes a derived scalar array (`FoamVizColor`) instead, so both render modes
  agree and the data range is trivially correct.
- **Time stepping**: pull with `reader.UpdateTimeStep(t)` and hand downstream
  filters the result via `SetInputData`. A connected pipeline re-negotiates the
  time request on every `Update()` and silently falls back to `t=0`.
- **`vtkOpenFOAMReader` does NOT need the `*.foam` marker file to exist**
  (corrected 2026-10-20; this note used to say it did). It takes the case root
  from the *directory* of the file name it is given and never opens the file,
  so `foam_marker()` passes an existing `*.foam` if there is one and otherwise
  just a name, `<case>/<case>.foam`, that is never created. Checked on VTK
  9.7.1, serial and decomposed (`vtkPOpenFOAMReader`), on `chmod -R a-w`
  copies of hotRoom; `tests/test_pipeline.py` "read-only cases" guards it and
  fails with `PermissionError` if the old `touch()` comes back. The old
  `ensure_foam_stub` touched `case.foam` into every case, which is why the
  cfd-viz volume had to be mounted read-write. It advertises `patch/*` and `group/*` arrays;
  groups duplicate their member patches, so only `patch/*` is exposed. It also
  advertises fields such as `T.orig` that never appear in the output — read the
  field list from the actual output arrays, not from the reader.
- **`vtkFeatureEdges`: feature and manifold edges are OPPOSITE toggles.**
  Feature + manifold *together* yields only the feature edges, but manifold
  *alone* yields every interior edge. So the building geometry's two modes are
  feature edges = `FeatureEdges` on / `Manifold` off (sharp + boundary, the
  architectural outline), and wireframe = `FeatureEdges` **off** / `Manifold`
  on. Boundary edges stay on for both. Only the current mode's edges are ever
  built, so the big all-edges set costs nothing until wireframe is chosen.
- **Crinkle slice: use `vtkExtractGeometry`, NOT
  `vtk3DLinearGridCrinkleExtractor`.** The latter is VTK's threaded 3D-linear
  fast path and it **hung the server** — steady memory growth, no output,
  restart needed — even on small cases, while computing in ~2 ms headlessly.
  Never reproduced headlessly, so unconfirmed, but its `vtkSMPTools` thread pool
  inside an async server is the prime suspect, and it handles linear cells only.
  `vtkExtractGeometry` + `ExtractBoundaryCellsOn`/`ExtractOnlyBoundaryCellsOn`
  is the general, single-threaded, all-cell-type equivalent (what ParaView's
  crinkle relies on). Slower, but crinkle is a deliberate "show me the mesh".
- **Arrow length must be normalised by the SEED set's own max |vector|**, not
  the whole domain's. Scaling by a domain-wide peak made the arrows vanish
  whenever the colour field was a large scalar like T; `_seed_vector_max` reads
  the plane grid / isosurface samples where arrows are actually drawn.

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
| camera, view presets, F-pick, lighting, theme, legend | true cell values, the isosurface's locked field |
| per-part solid colour, colour-by-field | *robust range and Rescale: a cheap `/api/range`, no extraction* |
| **streamline comets** (an animated dash pattern over a baked `travel` attribute) | — |
| **streamlines as tubes, and their width** (built from the line data in the browser) | streamline seeds and max length (the tracer) |

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

### Tubes are built in the browser (2026-09-11)

The server ships streamlines as **lines only**. `vtkTubeFilter` was doing
nothing the client cannot do, and shipping its output was expensive:

| `s2`, 200 seeds | server time | gzipped wire | vertices |
|---|---|---|---|
| lines | 3481 ms | **1.13 MB** | 59 863 |
| tubes | 3368 ms | **10.30 MB** | 441 808 |

**9x the wire for no saving in server time** — the 3.4 s is `vtkStreamTracer`,
which runs either way. On hotRoom it is 19x raw (0.5 MB against 9.6 MB).

`web/src/viewer/tube.js` rebuilds it locally with a parallel-transport frame,
the same approach `vtkTubeFilter` takes. Measured build cost: **4.3 ms** for
hotRoom's 18 k points, **28.5 ms** for 120 k — one or two frames, once per
fetch.

**The width is a uniform, not geometry.** The builder emits the tube's
*centreline* positions with the ring's radial direction as the vertex normal,
and the vertex shader displaces by `normal * uTubeRadius`. So dragging the width
slider moves no vertices and rebuilds nothing — verified: the vertex count is
identical before and after. `uTubeRadius` is 0 on every other material, where
the displacement is an exact no-op.

Consequences worth keeping straight:

- `stream_tubes` and `stream_radius` left `PART_INPUTS` and moved from `request`
  to `appearance`. Switching representation is instant **the first time**, not
  just on a cache hit, and the width never round-trips.
- Only `stream_seeds` and `stream_length` still sit behind the Apply button,
  because only they re-run the tracer. Deferring the others would have been a
  lie about their cost.
- Comets work unchanged: `travel` is replicated around each ring, so a tubed
  streamline animates exactly like a line one.
- `pipeline.stream_tube` is now **unused** — it was kept for the Trame app,
  which tubed server-side. It goes when the render half does.

**Traps hit building it:**

- **The polyline offsets had to be shipped.** `_line_indices` flattens polylines
  into segment pairs, which loses where each line starts — and a tube needs to
  walk a line in order. `_line_offsets` adds them (0.7 kB against 214 kB of
  positions). It is only this cheap because the tracer's connectivity is
  *sequential* within each polyline; that is checked at runtime rather than
  assumed, and the function returns no offsets if it ever stops being true.
- **`decodeScene` has to read the new buffer.** Forgetting it made `partInfo`
  return null with no error anywhere — the tube silently failed to build. If a
  new per-part buffer is added, both ends need it.
- **`const` is not hoisted.** Placing the `domain` helper below the effect that
  used it was a temporal-dead-zone `ReferenceError` at render, i.e. a blank
  page, not a warning.
- **Inflate the bounding sphere by the radius.** It is computed from the
  centreline, so without the correction the tube gets frustum-culled while still
  partly on screen.
- **A ~180 degree tangent flip collapses the transported normal.** A hairpin in
  a streamline would otherwise emit NaNs and blank the whole draw call; the
  builder re-seeds the frame instead.
- **`partInfo` assumed triangles**, so it reported "1.51 vertices per triangle"
  for a line part. Lines carry 2 indices per primitive.

### Streamline comets (2026-09-09)

Animated particles riding the streamlines, the way wind maps do. Asked for as
"ricing", but it earns its place on a real gap: **a static streamline is
direction-ambiguous.** The lines showed you the path and nothing about which way
the air goes; motion answers that instantly, and for someone not used to reading
flow viz it is the difference between a picture and an explanation.

**It is an animated dash pattern, not particles.** No particle buffer, no
re-seeding, no per-frame JS: one uniform. Per-vertex `travel` plus a phase gives
a pulse that advances along every line at once, in the fragment shader
(`COMET` in `web/src/viewer/shaders.js`).

**`travel` is real transport time, and that was free.** `vtkStreamTracer`
already emits **`IntegrationTime`** — the integrator's own time-of-flight from
the seed, negative upstream since we integrate both directions, and verified
monotonic along every polyline. So `_add_travel` in `server/scene.py` does no
integration of its own; it normalises what is already there. The payoff is that
comets move at the **local flow speed** (~32x variation along the demo case's
streamlines), so they rip through a plume and crawl in the corners. Riding arc
length instead would have looked similar and meant nothing.

**Why normalised, and why by the median.** Dimensionless travel is what lets the
UI's speed and spacing defaults work on any case. The divisor is the *median*
per-polyline span, not the max or the mean, because a room's slowest
recirculating streamline can span 10x the typical one (30 000 s against a
2 000 s median on hotRoom) and dividing by that would leave every normal comet
effectively frozen. Dividing by **one** global figure rather than per line is
what keeps relative speeds physical — a slow streamline still takes
proportionally longer. `travelDivisor` in the scene header carries the
seconds-per-unit back, so the timescale is recoverable.

**Tubes animate too, and read much better.** `travel` is added to the tracer
output *before* the tube filter runs, so `vtkTubeFilter` interpolates it onto
the tube it generates (checked). On 1-px lines a comet is a short bright segment
that gets lost in a coiled streamline tangle; on tubes it is a discrete object.
Worth suggesting tubes to anyone who tries the animation and finds it noisy —
along with fewer seeds and a shorter max length, since 60 long recirculating
lines are unreadable animated or not.

**What it costs on the wire, measured.** `travel` is one float32 per streamline
vertex, and it barely compresses (103.8 kB raw → 94.0 kB gzipped on hotRoom),
because a smooth float ramp is not what gzip is good at. That is **+24% on the
gzipped stream part** (396 → 490 kB), and it is paid whether or not the
animation is switched on.

That was a deliberate choice over the alternative, which was to make `travel`
conditional on the animation and add it to the stream part's `PART_INPUTS`.
Doing that would turn the toggle into a **server** control: on `s2` enabling the
animation would mean re-running `vtkStreamTracer`, ~2.6 s, for a control that
otherwise costs nothing. Paying a fixed 24% on a part that is opt-in already
(streamlines start hidden) is the better trade. If it ever does matter, the lever
is precision, not conditionality: float32 over a ±20 range is wild overkill for
something whose only job is to look smooth, and a quantised uint16 with a
scale/offset in the header would halve it.

**Traps and tuning:**

- **The phase is accumulated on the CPU**, not derived from `uTime * speed`. A
  raw clock drifts out of float32 precision inside `fract()` over a long
  session, and changing the speed would make the whole pattern jump. `_tick`
  advances a phase by `dt * speed` and wraps it to one period; `dt` is clamped
  so a backgrounded tab does not teleport every comet on return.
- **`uComets` is per material, not shared**, and is gated on the `travel`
  attribute actually being present. That matters: a missing attribute makes
  three.js supply 0 for every vertex, so the whole line set would pulse *in
  unison* — which looks like a slightly odd animation rather than like a bug.
  `canAnimateStreams()` backs the UI's disabled state for the same reason.
- **Animating modulates alpha, so the material must blend** even at opacity 1,
  or the dimming between comets does nothing. `_applyBlending` accounts for it.
- **`uComets == 0` is an exact no-op** — glow 0, dim 1. Keep it that way; it is
  what makes the feature free when off.
- Defaults (period 0.4, tail 12, dim 0.16) were **swept against the demo case**,
  not guessed. Wider periods with a sharper tail degenerate into sparse dots.
- **When measuring this, hide the slice.** A sweep that looked like "the
  animation barely does anything" was measuring the big blue cut plane filling
  the crop; with only the streamlines visible the lit fraction goes 14.7% → 3-9%
  depending on settings. Also note the *bright* fraction legitimately **drops**
  when the animation is on: only ~8% of each line is a head, and the rest is
  dimmed, so a naive "is it brighter?" metric reads backwards.

### Four regressions, and what they teach (2026-09-09)

Niklas reported four broken controls. They are worth keeping together because
three of them share a shape: **a control that changes no part signature does
nothing at all, silently.** That is the failure mode this architecture invites,
and there is no error to notice.

**Opacity by value did nothing on the shell.** The ramp was written as
`uOpacityMap * uColored`, and the shell ships *uncoloured* (a neutral grey so
the slice inside it reads), so the one surface you most want to see through
ignored the ramp. My error in the port: I coupled two independent questions.
"Fade out the low values" is meaningful on a flat grey surface too. It is now
gated on `uHasScalar`, which is the *real* precondition — a part with no scalar
attribute reads `vScalar == 0`, hence `t == 0`, hence `alpha == 0`, and would
vanish entirely the moment the ramp came on. Note the browser check had been
*written around* the bug (it coloured the shell first); it now uses the shell as
it ships. A test that documents a bug as intended behaviour is worse than no
test.

**Robust range did nothing at all.** It changes only the reported range, no
geometry, so it is correctly in no part's `PART_INPUTS` — and therefore moved no
cache signature, triggered no refetch, and never delivered its new range. The
fix is a cheap **`GET /api/range`** endpoint rather than adding `robust` to every
part's inputs: two floats should not cost a re-extraction of every visible part.
`Rescale` now goes through it too, so it re-reads the data and honours robust
instead of reusing the last scene header.

**True cell values could never have worked.** The pipeline baked the cell array
and `server/wire.py` only ever read `GetPointData()`. Worth understanding *why*
it needs more than a plumbing fix: flat per-cell colour is **impossible on an
indexed mesh**, because neighbouring cells share vertices and there is nowhere
to put a per-cell value. So `_de_index_cells` emits three vertices per triangle,
each carrying its own cell value — 3x the vertex data, which is why it happens
only when the toggle is on. (GLSL's `flat` qualifier is not a shortcut: it takes
the *provoking vertex's* value, which on a shared-vertex mesh is an arbitrary
neighbour's, not the cell's.) Scoped to the shell and the slice, as in the Trame
app; the derived filters read point data by construction.

**The isosurface behaved "randomly" across a field change.** Its values were
seeded exactly once per case, guarded on `contour_min === 0 && contour_max === 1`.
Switch the colour field from U to T and the isovalue stayed at a `|U|` number,
nowhere near the T range, so the surface came back empty or arbitrary. They now
re-seed whenever the contoured field changes **identity** — deps are field
identity only, so typing a value never triggers a reseed.

**The lesson to keep:** when adding a server-side control, ask *which part's
signature does this move?* If the answer is "none", it will do nothing, and you
will not find out from an error. Either add it to `PART_INPUTS`, or give it a
cheap endpoint of its own — and prefer the endpoint when the control does not
actually change geometry.

### Numeric inputs: wheel-driven, with data-derived steps

`NumberField` (`web/src/ui/controls.jsx`) is the number input every panel uses.
Two decisions in it are deliberate:

- **The wheel only acts while the field has focus.** Doing it unconditionally
  would be worse than not having it: the panels scroll, so a wheel gesture aimed
  at the pane would silently edit whichever field sat under the pointer. Click,
  then wheel. `passive: false` on the listener is required — it has to
  `preventDefault` to stop the pane scrolling underneath, and wheel listeners
  default to passive, where `preventDefault` is ignored.
- **Steps come from the data range**, via `stepFor(span)` (~1/100th of the span,
  snapped to 1/2/5 x a power of ten). A fixed step of 1 is wrong for nearly
  every field here: uselessly coarse on `|U|` (0..0.23 → 0.002), far too coarse
  for a plane position that needs sub-metre precision (0..10 → 0.1), and far too
  fine on `p` (0..1e5 → 1000). This governs the spinner arrows as well, so it
  matters even for anyone who never touches the wheel.

### Locking the isosurface's field

`pipeline.contour_field` (None = follow the colour field) plus a separate baked
`CONTOUR_ARRAY`, so an isosurface of one field can be **coloured by another** —
contour speed, colour by temperature. That is what "lock" has to mean, and it is
also the useful case: an isosurface coloured by its *own* field is a single flat
colour, verified (a T isosurface ships scalars 176.9..176.9).

`apply_contour_array()` was added rather than folded into `update_contour()`,
which is untouched; with `contour_field` unset the filter contours `COLOR_ARRAY`
exactly as before. It carries its own
`_baked_contour` signature guard for the same reason `apply_color_array` does —
baking dirties `case.internal`, whose MTime bump re-executes every filter fed by
it (see the perf invariant). `update_data()` and `release_case()` clear it.

Verified: colour=T locked to U gives the *same* geometry as colour=U unlocked
(217 verts, isovalue 0.1153) with T-valued scalars. `Rescale` re-seeds the
isovalues when following and deliberately leaves them alone when locked.

### Concurrency

Extraction runs in a worker thread (`asyncio.to_thread`) behind a single lock
(`server/app.py`). The thread keeps a 3-second `vtkStreamTracer` from freezing
the event loop; the lock exists because the filter graph is shared mutable state
and two concurrent extractions would interleave `update_plane` calls and hand
each other the wrong geometry. Still **one case per process**, as in Trame.

### Not done, deliberately

- **"Add to report".** Not ported, and it needs a decision before it is: does
  the figure come from the client canvas or from a server render? See
  "Case-report figures" below — that is where the seam is.
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

## Testing

`uv run doit ci` runs everything below (about 3 min); each also runs directly.

| | what it covers | last run (2026-10-23) |
|---|---|---|
| `tests/test_pipeline.py` | the VTK pipeline incl. read-only serial/decomposed cases, no browser, no GL | **64/64** |
| `tests/check_client.py` | the three.js client in real Chromium, incl. the start screen | **115/115** |
| `tests/test_contract.py` | 56 recorded HTTP exchanges replayed (the API only grows) | **56/56** |
| `tests/test_sources.py`, `test_endpoints.py` | discovery, ids, labels, 404s; `/health`, `/api/config` | pytest 32 |
| `tests/check_cockpit_contract.py` | cfd-backend's cfd-viz build + runtime, mirrored | **22/22** (21 without `../cfd-backend`) |
| `tests/bench.py` | per-part extraction sizes and timings (not pass/fail) | — |

`tests/test_suites.py` runs the two script suites under pytest (the browser
one marked `browser`, which `doit test_py` deselects).

- `test_pipeline.py` asserts **output counts** for every filter, because an
  empty VTK filter raises nothing and renders as a perfectly plausible blank
  image. With Trame gone it is the **only** guard on `pipeline.py`. Note ~35 of
  its checks still assert on actors and mappers — render-side state this server
  never uses — which is what makes stripping the render half a real (if
  tractable) job rather than a delete.
- `check_client.py` mostly does **not** ask "did it render" — it asks **which
  controls cause a refetch**, by counting `/api/scene` requests around each
  interaction (zero for a colour-map switch, zero for a whole cut-plane drag,
  exactly one for the release, zero for returning to a cached time step). That
  boundary is the architecture and it decays silently, so those counts are the
  most valuable assertions in the repo. It also counts red pixels to prove the
  plane outline actually appears mid-drag.
- **Reading rendered pixels: use the in-page hook, not a screenshot.** The HUD,
  legend and busy overlay sit on top of the canvas, so a page screenshot of an
  *empty* scene comes back colourful. `window.__viz.grab()` does a `readPixels`
  and counts distinct colours in the browser (`Viewer.grab`). The older advice
  here — "screenshot the page, not the canvas, because a WebGL canvas without
  `preserveDrawingBuffer` reads back blank" — applied to the Trame/vtk.js view
  and is **not** how the three.js suite works; that renderer sets
  `preserveDrawingBuffer` (it needs it for `toDataURL` screenshots anyway).
- Selectors: every control carries `data-ctl="<name>"` for exactly this
  purpose. Mantine's own markup has nothing stable to select on, and note
  `data-ctl` lands on the `input` element itself, not a wrapper.
- Two things that make browser tests flaky if ignored, both learned the hard
  way and both handled by `wait_idle()` / a domain-relative epsilon in
  `check_client.py`: the **busy overlay captures clicks by design**, so
  clicking through it races the app rather than testing it; and
  **OrbitControls damping is still easing** when a test reads the camera, so a
  `1e-6` tolerance tests the easing curve, not the view.

## Demo data

**Both demo cases are versioned with their results** (2026-10-23, Niklas's
call): tiny, ASCII, so CI and a fresh checkout run every suite with no
OpenFOAM and no download. "No need to test viz at CI level on MB datasets."
Big cases are for `bench.py` and by-eye checks, via `--data`.

`data/hotRoom` = OpenFOAM 13 `fluid/hotRoomBoussinesqSteady`, with these edits:

- `system/blockMeshDict`: `(20 10 20)` → **`(10 5 10)`** (500 cells of 1 m).
- `system/controlDict`: `writeFormat ascii` (the tutorial's), `purgeWrite 2`
  so `Allrun` leaves exactly `0` + the last two writes.
- `system/setFieldsDict`: heater box `(4.5…5.5)²` → **`(4…6)²`**. On 1 m cells
  the old box edges coincide with face centres (a tie); the new one selects the
  centre 2×2 floor faces, a 4 m² heater, still centred.
- `Allrun` deletes `postProcessing` and `log.*` after the run.

10 × 5 × 10 m room, 4 m² of floor at 600 K, everything else 300 K. Converges
at iteration 740 → times `0`, `700`, `740`; ~400 kB. Still genuinely 3-D, which
is why it was kept over the 1-D `shockTube` that was also considered (its
slices and isosurfaces degenerate, and the reader skips its `p` and `rho`).
`constant/triSurface/building.obj` is a small **added fixture** (a box at the
room bounds), not part of the tutorial; `Allclean` leaves it alone.

`data/cavity` = lid-driven cavity for the `fluid` solver, 20 × 20 × 1 = 400
cells (2-D), ASCII, times `0`, `0.5`, `1`, ~330 kB. Its T is constant (the case
is isothermal). It is the second case for the start screen; `check_client.py`
*fails* on fewer than two.

Regenerate on the host (no OpenFOAM in the container), then re-record the
contract and review its diff:

```bash
cd data/hotRoom && ./Allclean && ./Allrun
uv run python tools/record_contract.py
```

Thresholds that depend on the mesh are written in terms of the definition
(`N_CELLS`, `LAYER` in `test_pipeline.py`); `check_client.py`'s animation
check uses a `MOTION` margin measured on this case (~0.13 on vs ~0.003 off).

## Architecture in one paragraph

**Shared.** `case.py` wraps `vtkOpenFOAMReader` and hands out **snapshots** —
concrete datasets for one instant — rather than a live pipeline connection.
`pipeline.py` owns the filter graph and every representation, and bakes the
selected field/component into a real scalar array (`FoamVizColor`) that
everything colours by. `colors.py` samples matplotlib colour maps once and
serves the VTK transfer function, the CSS legend gradient *and* the 256-entry
RGB table the three.js shader samples, so all three cannot drift apart.

**The three.js front end.** `server/scene.py` drives that pipeline per **part**
and `server/wire.py` packs the polydata as typed arrays; `web/` decodes them
into `BufferGeometry` and owns appearance in a shader. There is no server-side
render in this path. See "The three.js client" above.

**Perf invariant: do not dirty `case.internal` for nothing.** Bumping its MTime
re-executes every filter fed by it — the cutter, hence the stream seeds, hence
the tracer, plus isosurfaces and glyphs. Two things used to do that on every
update and are guarded: `update_scene()` runs on *every* change (incl. a mere visibility/
opacity toggle). If it dirties `case.internal`, its MTime bumps and every filter
fed by it — the cutter, hence the stream **seeds**, hence the tracer + tube, plus
isosurfaces and glyphs — re-executes. On a big case that makes an incidental
change as expensive as recomputing the streamlines. TWO places dirtied it and both are now guarded:
- `apply_color_array()` re-baked `FoamVizColor` (Remove/AddArray) every call —
  guarded by a `_baked` signature `(field, component, use_cell_data)`, cleared by
  `update_data()` on reload so fresh data still re-bakes.
- `SetActiveVectors(field)` bumps the MTime **even when that field is already
  active** (VTK does not short-circuit it), so it is set only when the active
  vector name actually differs.

Keep both guards. The per-part signatures in `server/scene.py` are the same
discipline one level up: they stop work reaching the pipeline at all.
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
4. **The client's part cache** — but that one lives in the *browser*, capped by
   bytes, and is not this process's memory at all (see the part model above).

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

**Sizing rule of thumb: the server's RSS settles near the largest single case
opened, not the sum.** Measured 2026-10-20 on a 15.8 M-cell IDA ICE case
(binary, serial, 10 fields, 4.7 GB on disk): 13 s to open, **7.3 GB peak,
6.6 GB settled**, boundary 111 MB raw / 25 MB gzipped -- about 0.45 GB per
million cells. Most of the field share is every non-skipped field being read
and interpolated to points; loading only the fields a scene uses is the lever. Leaving the viewer frees nothing on the server — one
process holds one open case with no teardown on disconnect — but the browser
side now owns its own geometry, so closing the tab does reclaim that half.

## Backend integration

### How it deploys (decided 2026-08-11 with Niklas; still current)

- **Shape:** a 4th service, **`cfd-viz`**, behind the nginx `cfd-frontend`,
  which proxies `/viz/*` to it. It reads cases straight off the shared
  `CFD_HOME` volume — no OpenFOAM install needed, `vtkOpenFOAMReader` parses the
  case files directly. The React cockpit embeds it **full-page** as
  `<iframe src="/viz/?case=<id>">` -- `<id>` there is the backend's UUID
  directory name, which the viewer resolves as a name (its own ids are
  different; both work).
- **Process model:** one shared process. Per-session launching is the later
  productisation; see the open questions below.
- **Deep links:** `?case=<name>` and `?theme=light|dark`, read from the URL by
  the client itself (`urlOptions` in `web/src/api.js`). Serving under the
  `/viz/` prefix works because the build is relative — `base: ''` plus relative
  API paths — so one build serves at `/` and under `/viz/`.
- **Empty `CFD_HOME` must not be fatal.** `--server` makes it non-fatal and the
  server picks cases up as they appear (since 2026-10-21 `server` is the
  default mode, so this holds without the flag too); before that, without it
  the process exited and the container **restart-loops**. This bit Niklas once on Podman/WSL with a
  mis-mounted volume — nothing to do with WSL, GL, or the harmless EGL/X11 probe
  warnings. `pipeline._bootstrap_empty()` is the other half: it seeds the
  `case.internal`-fed filters (cutter/crinkle/contour/tracer + glyph_probe
  source) with an empty `vtkUnstructuredGrid` so their `Update()` is a clean
  no-op before any case loads, which also kills the `vtkCutter` "0 connections"
  ERR spam. Guarded by a `test_pipeline.py` invariant.
- **The 502 that cost two rounds, fixed in cfd-backend (`b7dbd07`).** nginx pins
  the upstream IP it resolved at startup, so a restarted `cfd-viz` with a new IP
  became unreachable: `connect() failed (113: Host is unreachable)`. Fix is
  `resolver ${NGINX_RESOLVER}` plus a **variable** `proxy_pass` (a variable is
  what forces per-request re-resolution). **The gotcha:** the resolver var must
  be set by an entrypoint hook that is **executable** — the nginx entrypoint
  only *sources* `docker-entrypoint.d/*.envsh` with `+x`, silently ignoring the
  rest. Hence `--chmod=755` on `scripts/nginx-resolver.envsh`.
- Niklas runs rootless, nginx on host port 8080. The `cfd-viz` image clones this
  repo at build time, so **push before rebuilding** -- and see "The cockpit
  contract" above: what reaches `main` ships.
- **The `cfd-home` volume could be mounted `:ro` now** -- the viewer writes
  nothing into cases since 2026-10-20. cfd-backend's compose still mounts it rw
  with a comment about the marker file; changing that is cfd-backend's call.

### Remaining open questions

Related memories: `project_foamviz`, `project_openfoam_api` (Flask job-control
API on :5001) and `project_iceopenfoam` (EQUA's OpenFOAM-13 extension libs).

1. **Which backend, exactly?** The Flask job-control API, ICEOpenFOAM, or the
   IDA ICE client itself? That decides whether FoamViz is a service the API
   proxies to, or a component the client embeds.
2. **Process model — improved, still open.** One long-lived process holding one
   VTK pipeline and one open case. In the three.js path extraction now runs in
   a worker thread behind a lock (`server/app.py`), so a 3-second
   `vtkStreamTracer` no longer freezes the event loop and the server keeps
   answering while it works — and the client owns its own camera, so the
   "one camera" half of this problem is simply gone. What remains is that two
   users still share one open case: real concurrency needs a process per
   session, which needs no special machinery here — just more processes.
3. **Case discovery -- partly done (2026-10-21).** Listing now reads
   `metadata.json` `NAME` as the label and the cell count from the mesh header
   (`server/sources.py`), but still discovers by scanning for
   `system/controlDict`, not by `metadata.json`. `N-CELLS` turned out stale
   (0) on a real case; `case_info.ncells` was right. The
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
   `vtkOpenFOAMReader` subclass, so the rest is unchanged. Verified on a real
   decomposePar'd hotRoom (root time 0, processor0 at 1730): detection True,
   `vtkPOpenFOAMReader` present in the wheel, reads all processor dirs serially
   → 32 000 global cells, all fields. The top bar shows "· decomposed" when it
   is in play (`data/geometric-fancoil-and-beam` exercises it, 644 413 cells).
6. **Render mode — CLOSED.** Rendering happens in the browser, full stop; the
   server never renders. Niklas had already reported client-side rendering
   "impressive already" at 12 M cells, which is what made server rendering
   redundant. If a genuinely GPU-less client ever matters again, that is a new
   feature, not a toggle.
7. **Comfort metrics.** Draught rate, PMV/PPD, operative temperature are what
   the IDA ICE side actually reports, and none are OpenFOAM fields. They would
   be derived arrays computed at load — the same mechanism as `FoamVizColor`,
   so the hook already exists (`pipeline.apply_color_array`).

## Case-report figures — still open, and it needs a decision first

Goal (with Niklas): build a scene, "Add to case report", and have it appear in
the cfd-frontend case report as a **frozen** snapshot (rotate/zoom, no toggles).
The headline deliverable is a **single self-contained `.html` export** with the
scenes inlined, so it opens offline with no running services; browser
print-to-PDF stays, with scenes showing as a poster PNG.

**The three.js client has no Report button, deliberately.** The decision to make
first sits on a real seam this architecture creates: does the figure come from
the **client canvas** — matching exactly what the user sees, bands, opacity ramp
and all — or from a **server render**, which would need every appearance setting
to travel with the request? Every setting the shader owns is a setting the
server does not know. That is the price of the control the split buys, and it
lands here.

Client-canvas is the obvious answer now that nothing renders server-side:
`Viewer.screenshot()` already exists (it is the toolbar camera button), and
`preserveDrawingBuffer` is already on. The open part is the interactive scene,
not the poster.

**What the Trame app did**, for reference — `git show trame:foamviz/app.py`, and
the figures it wrote are still readable by the cfd-backend side: per figure into
`<case>/report/`, a `figure_NN.png` poster plus a `figure_NN.json` carrying
caption, field/component/range/preset/bands, and the colour-bar gradient and
ticks so the report redraws the bar from `colors.py` (the poster is the 3D view
only, no legend). Interactive `.vtkjs` export was built and then mothballed —
unused scenes just accumulated.

**Still TODO on the cfd-backend side** regardless of which route wins: a route
to list and serve a case's `report/` figures, a Figures section in
`frontend/src/pages/Report.jsx`, and the single-file HTML export.
