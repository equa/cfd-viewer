# Standalone Viewer — Design

## Context

Today (see CLAUDE.md for the long form):

- One aiohttp process, `main.py --data <root> --port 5003`, extracts geometry with VTK and serves the built React/three.js client. All URLs are relative, so one build serves at `/` and under `/viz/`.
- Cases come from **one directory**: `find_cases(root)` takes the root and its immediate children that contain `system/controlDict`, keyed by directory basename, rescanned on every request. `SceneSource` takes that root; `FoamCase` takes a path.
- `ensure_foam_stub()` writes `case.foam` into every case it opens, so a case directory must be writable.
- One shared `FoamPipeline` behind one `asyncio.Lock`: one open case per process.
- The only deployment is the `cfd-viz` container built by cfd-backend's Containerfile, which clones this repo. The viewer never calls the cfd-backend API; it reads the shared volume.

Constraints from the users:

- Windows 11, frequently **without admin rights**. Install must be per-user, container-free, and explainable on one page.
- The hosted viewer must accept cases **uploaded from the user's machine**.
- Repository work follows the equa-ci service archetype "in the shadow": same layout and task names, no template, no private feed, no `equa-deployment` hookup.

## Goals / Non-Goals

**Goals**

- One codebase, three run modes: `local` (desktop), `server` (today's cockpit container), `hosted` (uploads).
- Cases on a read-only location can be viewed.
- A Windows user installs by unzipping (or a no-admin installer) and double-clicking.
- The cockpit iframe keeps working with no change on the cfd-backend side.
- The extraction layer (`foamviz/pipeline.py`) is untouched.

**Non-Goals**

- Reading OpenFOAM in the browser.
- The equa-ui migration (the start screen is designed to map onto `TopBar` + a card list later, but is built in Mantine now).
- Multi-user scaling beyond a small, fixed pool of sessions.
- macOS/Linux installers.

## Decisions

### D1. A `CaseSource` interface replaces the data root

```python
class CaseSource(Protocol):
    def list(self) -> list[CaseRef]: ...        # id, name, label, origin, size hints
    def path(self, case_id: str) -> Path: ...    # a real directory VTK can read
```

- `DirectorySource(roots, depth=N)`: scans one or more roots, recursively up to a depth limit, for `system/controlDict`. This replaces `find_cases`.
- `UploadSource(workspace)`: lists the finished uploads of one session (D7).
- `SceneSource` takes a source rather than a root. Everything below `path()` is unchanged, because VTK still reads a real directory.
- **Case ids are opaque and stable**: a short hash of the resolved root plus the case's path relative to that root, so the same relative path under two roots gets two ids. Once discovery is recursive, two cases can share a basename, so the name alone stops being unique. `?case=` accepts an id. If no case has that id, it falls back to a unique name, which keeps `/viz/?case=<name>` working for the cockpit.
- The silent fallback to the first case on an unknown `?case=` is removed. An unknown case returns 404, and the client then shows the start screen. Agreed 2026-10-20: it was a long-standing issue, and showing the wrong case is worse than saying so.
- **The API only grows.** `/api/cases` keeps `cases: [names]` and adds `entries`. `/api/meta` keeps `case` as the name and adds `caseId` and `caseLabel`. Nothing that exists today changes name, type or meaning (D11).
- **`server` mode keeps today's one-level scan** (`--scan-depth 1`). A deeper scan of `CFD_HOME` could find nested cases whose names collide with top-level ones, and a name the cockpit uses today would then answer 404. Deeper scans are for `local` mode.

*Alternatives:* keep basenames and refuse duplicates (fragile across several roots), or use full paths as ids (they leak host paths into URLs and are awkward on Windows).

### D2. VTK stays on the server in every mode

The same Python server runs on the user's machine in local mode, so the "local" story needs no new reader.

Reading OpenFOAM in the browser would mean either a VTK WebAssembly build that includes `vtkOpenFOAMReader` and every filter we use, or our own parser plus our own cutter, contour filter and stream tracer. Either way it rewrites the part of the project that CLAUDE.md calls the durable one, and it gains nothing over local mode, which already reads the user's own disk.

### D3. No `.foam` stub written into the case

- `FoamCase` points the reader at an existing `*.foam` file when there is one, and otherwise at `<case>/<name>.foam` **without creating it**.
- Verified 2026-10-20 with VTK 9.7.1 on a `chmod -R a-w` copy of `hotRoom` (with `0` and `1730`) that has no `.foam` file: `UpdateInformation()` and `Update()` succeed, with 2 time values and the full output.
- Still to verify: `vtkPOpenFOAMReader` on a decomposed case, and the Windows wheel.
- If either needs the file to exist, the fallback is a stub in a per-user cache directory plus `SetCaseType`/case path. That fallback is an implementation detail of `case.py`.
- CLAUDE.md's "VTK-Python traps" note that the stub is *needed* gets corrected.

### D4. Run modes

| | `local` | `server` | `hosted` |
|---|---|---|---|
| Bind | `127.0.0.1`, free port | `0.0.0.0:5003` | `0.0.0.0:5003` behind a proxy |
| Sources | `DirectorySource` over user-chosen folders | `DirectorySource(--data)` | `UploadSource` per session (+ optional shared read-only `--data`) |
| Opens browser | yes | no | no |
| Auth | per-launch token | none (cockpit's nginx) | none in-app; the deployment edge's login (D8) |
| Add folder / Upload | Add folder | neither | Upload |

- `--mode` defaults to `server`. That keeps the cfd-backend container's command line valid as it is today. `--server` is kept as an alias.
- The client learns which mode it is in from a new cheap endpoint, `GET /api/config`, which returns `{mode, canAddFolders, canUpload, limits}`.
- This endpoint moves no part signature.
- The start-screen controls are client UI served by `/api/config`, `/api/cases`, `/api/folders` and `/api/uploads`. They are not scene controls, so the `PART_INPUTS` rule does not apply. They are listed here because `rules.design` asks for it.

**Hardening local mode.** The viewer binds to loopback only. A web page the user has open could still send requests to `localhost`, and other users on a shared machine could reach the port, so:

- the launcher generates a random token per launch and opens `http://127.0.0.1:<port>/?t=<token>`;
- the server swaps that for an `HttpOnly` cookie and rejects any request that lacks it;
- the server rejects any `Host` header other than `127.0.0.1:<port>` or `localhost:<port>`, which closes off DNS rebinding.

**Single instance.** Launching a second time finds the running instance (port and token are kept in a per-user lock file) and only opens a new browser tab.

### D5. Choosing folders in local mode

A browser cannot hand a *path* to the server: both the File System Access API and `<input webkitdirectory>` give the page file handles, not paths. In local mode, though, the server is on the same machine, so the server does the choosing:

- **Folder browser in the UI.** `GET /api/folders?path=` lists drives (`C:\`, mapped network drives) and subdirectories, marking those that are cases or that contain cases. "Add folder" adds a root to the `DirectorySource`.
- **Recent folders** are stored in `%APPDATA%\equa\cfd-viewer\settings.json` and offered on the start screen.
- **Command line, drag and drop.** Paths given as arguments are added as roots, and Windows passes the path of a folder dropped onto the `.exe` as an argument. "Send to → CFD Viewer" and an "Open with" association on `*.foam` come for free on top of that.

*Alternative:* a native folder dialog opened by the server through tkinter. It looks native, but the dialog opens behind the browser window and adds Tcl/Tk to the bundle. It's worth keeping as an option if users find the in-UI browser odd.

### D6. Windows packaging: PyInstaller one-folder, as a zip and a per-user installer

- **PyInstaller `--onedir`**, built on a `windows-latest` GitHub Actions runner from `uv.lock`.
  - The built client is inside, as package data.
  - The output is `CFD Viewer\cfd-viewer.exe` plus `_internal\`.
  - It needs no Python, VTK or admin rights on the user's machine.
  - *Not* `--onefile`: that unpacks hundreds of MB to `%TEMP%` on every launch, starts slowly, and is what antivirus products flag most.
- **Two artefacts from one build:**
  1. `cfd-viewer-<ver>-win64.zip`: unzip anywhere and double-click. This is the zero-assumption route.
  2. `cfd-viewer-<ver>-setup.exe`: Inno Setup with `PrivilegesRequired=lowest`.
     - It installs to `%LOCALAPPDATA%\Programs\CFD Viewer`.
     - It adds a Start-menu entry, the `*.foam` "Open with" entry and an uninstaller.
     - It never asks for elevation.
- **Size is the cost.** The VTK wheel is large: `vtkmodules` is 564 MB unpacked on Linux.
  - PyInstaller's VTK hook collects every module. We exclude the modules we don't import, using an explicit list in the spec file, and measure.
  - The target is a zip under 250 MB. The first build will show whether that holds.
  - Dropping `matplotlib` (D10) helps.
- **The console window** stays visible in v1, titled "CFD Viewer — close this window to stop". It is honest and it shows errors.
  - A tray icon (`pystray`) and `--noconsole` can follow.
  - The UI also gets a "Quit viewer" item in local mode.
- **No auto-update in v1.** The start screen shows the version.
- **Signing and distribution.** EQUA has a code-signing certificate, but it isn't available yet.
  - The build has a signing step (`signtool` on the `.exe`, then on the installer) that is skipped while no certificate is configured. Unsigned builds are for internal testing only.
  - The release artefacts are the zip, the installer and a `SHA256SUMS` file. They are uploaded **by hand** to EQUA's customer download area; this change automates nothing past producing them.

*Alternatives considered:*

- **An embeddable-Python zip** (python-build-standalone plus site-packages and a launcher). There are no freezing pitfalls, but it needs a separate launcher `.exe` to avoid a `.cmd`, and it is harder to make look like an app. This is the fallback if PyInstaller and VTK misbehave.
- **Nuitka.** Slower builds and the same size.
- **MSIX.** Needs signing and is blocked by some IT policies.
- **`uvx`/pip.** It works today for developers and is documented as such, but it is not for these users.

### D7. Uploads to a hosted viewer

**Picking the case (client side).**

- `<input type=file webkitdirectory>` and folder drag-and-drop (`DataTransferItem.webkitGetAsEntry`). Both work in Edge, Chrome and Firefox. `showDirectoryPicker` is Chromium-only and adds nothing here.
- The client reads the **file list only**, with no contents, and recognises the case layout:
  - `system/controlDict` and `constant/polyMesh/*`
  - numeric time directories
  - `processor*/` with their own `constant/polyMesh` and time directories
  - `constant/triSurface/building.obj`
- **Selection dialog.**
  - Time steps: the latest is ticked by default, and "all" is available.
  - Fields: those found in the chosen times, minus `SKIP_FIELDS`.
  - It shows the resulting upload size.
  - Logs, `postProcessing/`, `dynamicCode/` and other non-case files are never sent.

**Transfer.**

1. `POST /api/uploads` with the manifest (relative paths and sizes) returns an upload id. The server checks the quota and rejects bad paths at this point.
2. `PUT /api/uploads/<id>/files/<relpath>` sends one request per file, three to four in parallel. Each body is gzip-compressed in the browser with `CompressionStream`, which halves ASCII OpenFOAM files or better.
3. Retrying a file is idempotent, so an interrupted upload resumes by re-sending the manifest; the server answers with what it is missing.
4. `POST /api/uploads/<id>/finish` validates the layout, and the case appears in that session's `UploadSource`.

**Storage.**

- One workspace directory per session holds one subdirectory per upload.
- Paths are normalised and confined, so `..`, absolute paths, drive letters and symlinks are refused.
- Files are stored decompressed in v1. vtkOpenFOAMReader can read `*.gz` field files, which would save disk; that is worth a measured follow-up, not a v1 assumption.

**Sizes, serial and parallel.** Cases have up to about 20 M cells, which is 2–4 GB on disk.

- There is **no hard upload limit**. The design has to cope with multi-GB transfers:
  - the server streams each file straight to disk and never buffers a whole request;
  - per-file resume is required, not a nice-to-have;
  - the client shows progress and the remaining amount.
- Optional limits stay available as settings, all off by default: a per-upload maximum, a total disk cap, and a minimum free-disk margin.
- **Serial or parallel data.** A case may hold reconstructed time directories, `processor*/` directories, or both.
  - The selection dialog offers whichever holds the chosen time steps, and prefers the one that holds the latest time. That is the same rule `_is_decomposed()` applies on the server.
  - When processor directories are chosen, only those directories, `system/` and the needed parts of `constant/` are sent.

**Retention.** These are customer models.

- A workspace is deleted after it has been idle for a configurable time (24 h by default), and "Delete case" in the UI removes one immediately.
- Nothing is logged beyond file names and sizes.

### D8. Hosted mode: one worker process per session

The single lock is fine for local mode, where there is one user, and for today's cockpit. A hosted viewer serving several people cannot share one pipeline, because the cases would interleave.

- **Front process:** aiohttp, serves the client, uploads and `/api/cases`. It holds no VTK state.
- **Workers:** one `SceneSource` worker process per active session, spawned on demand with `multiprocessing`'s spawn method (the same code path on Windows).
  - The front process forwards `/api/meta|times|range|scene` to the session's worker over a local socket.
  - Workers are capped (`--max-sessions`, e.g. 4). An idle worker is stopped after N minutes.
  - Over the cap, a new session waits in a queue with a visible notice rather than evicting someone's open case.
- **Sizing:** RAM ≈ the number of sessions × the largest case each one opens (CLAUDE.md, "Memory management").
- **The other modes are untouched.** `local` and `server` keep running in a single process, so this machinery is only switched on with `--mode hosted`.

This is the "process per session, just more processes" answer CLAUDE.md already anticipates.

**Kept as simple as possible, because hosted mode is secondary.** The likely home is `equa-deployment`, whose edge already does the login: Traefik forward-auth runs through oauth2-proxy against Keycloak and passes `X-Auth-Request-User` to the service (`deployments/common/edge/compose.yaml`). So:

- The viewer has **no auth code of its own**. In hosted mode it expects to sit behind that login middleware.
- **Session key.** With `--trust-identity-header`, the session is keyed by `X-Auth-Request-User`, so one user's uploads follow them across tabs. Without it, the key is an anonymous random cookie, which is enough for a pilot on a closed network.
- It serves under a path prefix. That already works, because every URL is relative. It also answers `GET /health`, which the deployment's compose files expect.
- The image is the repo's `Dockerfile` (D9). Registering it in `equa-deployment` is not part of this change.

### D9. Repository layout along the service archetype (shadow adoption)

```
src/equa/cfd_viewer/
  foamviz/        case.py, pipeline.py, colors.py   (moved, API unchanged apart from D3)
  server/         app.py, scene.py, wire.py, sources.py, uploads.py, workers.py
  launcher.py     local-mode entry: port, token, single instance, open browser
  __main__.py     argparse; `python -m equa.cfd_viewer`
  web/            built client, copied in at build time (gitignored)
web/              unchanged Vite project
tests/            pytest: test_pipeline.py and check_client.py as-is, wrapped; new test_sources.py, test_uploads.py
packaging/windows/  cfd-viewer.spec (PyInstaller), cfd-viewer.iss (Inno Setup)
pyproject.toml    hatchling + hatch-vcs, console script `cfd-viewer`
uv.lock
dodo.py           lint, test, ci, web_build, bundle_win, with the same task names as equa-ci
Dockerfile        three stages like thermarest's: node web build → uv builder → slim runtime
```

- **Server framework.** aiohttp stays. The archetype's FastAPI move is a separate change, as it was for thermarest.
- **`dodo.py`** implements its tasks locally with ruff, pytest and `npm run build`, without depending on the `equa-ci` wheel from the private feed. Formal adoption would later swap it for `create_tasks(...)` with the same task names.
- **`main.py`** stays as a three-line shim, so existing command lines and the cfd-backend Containerfile keep working.
- **A top-level `foamviz/` compatibility package** re-exports `equa.cfd_viewer.foamviz`. cfd-backend's `viz.sh` imports `foamviz.pipeline` from the checkout root, and moving the package would otherwise fail its smoke tests 2 and 3.
- **`requirements.txt`** is generated from the lock file and must resolve on Python 3.14, which is what the `cfd-viz` image runs.
- **Removing the shims.** `main.py`, the `foamviz/` package and `requirements.txt` go only after cfd-backend builds from this repo's `Dockerfile` or image. Until then they are part of the contract (D11).
- **Commits** follow Conventional Commits from here on. Versions use semantic-release's `1.0.0-beta.N` scheme. A local `.releaserc.json` and commitlint config are added only when CI is.

### D10. Drop matplotlib (agreed 2026-10-20)

`colors.py` uses matplotlib only to sample colour maps into 256-entry tables. Writing those tables once to a small committed data file keeps the colours byte-identical. The `/api/lut` bytes and the VTK transfer function both come from the same tables, and it stops the viewer loading matplotlib at all.

The PyPI `vtk` wheel declares `matplotlib>=2.0.0` itself (found 2026-10-20 in task 2.5), so matplotlib stays *installed* in pip/uv environments and in the cfd-viz image. The real saving is in the Windows bundle (D6): since nothing imports it, PyInstaller can exclude matplotlib and its dependencies (pillow, fonttools, kiwisolver, contourpy and others).

`tests/test_pipeline.py` gains a check that the committed tables match matplotlib, when matplotlib happens to be installed.

### D11. Regression strategy: the cockpit contract

**What cfd-backend relies on** (read 2026-10-20 from its `Containerfile`, `tests/smoke/viz.sh`, `docker-compose.yml`, `frontend/nginx.conf.template`, `frontend/src/api.js` and `pages/Visualize.jsx`):

- It clones **our `main`** at build time, so every commit on `main` ships with the next cfd-backend build.
- It needs four files at the root: `web/package.json`, `web/package-lock.json`, `requirements.txt` and `main.py`.
- It installs and runs:
  - `npm ci && npm run build`;
  - `pip install -r requirements.txt` on Python 3.14;
  - `python main.py --server --data "$CFD_HOME" ...` as uid 2345, with no GL libraries.
- Its smoke test imports `foamviz.pipeline` from the checkout root, runs `main.py --help`, and checks for `web/dist/index.html`.
- nginx strips `/viz/`, and the iframe opens `/viz/?case=<basename>&theme=`.
- The cockpit calls no `/viz/api/*` endpoint itself.

**Five layers.** Each one catches what the one before it cannot.

1. **Golden API snapshots** (`tests/test_contract.py`), recorded on today's `main` *before anything moves*.
   - Covers `/api/cases`, `/api/meta`, `/api/times`, `/api/range` and `/api/lut`, plus `/api/scene` for a fixed query set on hotRoom.
   - For scenes it keeps the header minus `serverMs`, and a hash per buffer section.
   - Later commits must match, apart from added fields and an explicit list of 404 cases. This is where an accidental API change shows up.
2. **The existing suites:** `test_pipeline.py` and `check_client.py`, green after every task group.
3. **The cockpit contract check** (`tests/check_cockpit_contract.py`, the `doit cockpit_contract` task, part of `doit ci`).
   - It runs cfd-backend's build steps in order on a **fresh `git clone`** of the branch, so uncommitted files are caught.
   - It installs `requirements.txt` with `pip` into a fresh Python 3.14 venv (`uv python install 3.14`).
   - It runs cfd-backend's **own** `viz.sh` from `../cfd-backend`, skipping the step with a loud warning when that checkout is absent.
   - It starts the exact CMD line on a `CFD_HOME` holding hotRoom under a UUID folder name.
   - It fronts the server with a small aiohttp proxy that strips `/viz/` the way nginx does, then drives Chromium through `/viz/?case=<uuid>&theme=light`. It checks:
     - the case opens directly, with no start screen;
     - the light theme applies;
     - every request stays under `/viz/`;
     - a case copied in after start-up is listed;
     - an empty `CFD_HOME` start stays up.
4. **The real image.** cfd-backend's unchanged `Containerfile`, target `cfd-viz`, built with `--build-arg VIZ_BRANCH=<branch>`. Its smoke test runs in the image, and layer 3's browser check runs against the container.
   - This needs the branch pushed (it clones from GitHub) and Podman on the host. Both are manual steps for Niklas, gated before merge.
5. **Branch and rollback discipline.**
   - Phase 1 lives on a branch and reaches `main` only after layers 1–4 pass.
   - The current `ghcr.io/equa/cfd-viz:beta` digest is recorded first, so a rollback is a retag.
   - cfd-backend's next release after the merge is watched with its `release.yml` smoke step.

## Risks / Trade-offs

- **Corporate execution policies.**
  - AppLocker/WDAC rules often block `.exe` files under `%LOCALAPPDATA%` for users without admin rights.
  - Unsigned binaries trigger SmartScreen's "Windows protected your PC".
  - Mitigation: code-sign the `.exe` and the installer. That needs a certificate, which is an open question. Test on a locked-down machine early.
- **Bundle size and antivirus false positives.** Use one-folder builds, sign them, and measure size per release in CI.
- **Memory on the user's laptop: measured 2026-10-20, and it is the real constraint.**
  - The test case was a simulated IDA ICE case (`e2614af7-…`): 15.8 M cells, binary, serial, times `0` and `90`, 10 fields, 4.73 GB unpacked, 2.25 GB of which is the mesh.
  - With the current server on Linux and VTK 9.7.1:
    - `/api/meta`, which opens the case and loads the latest time, takes **13 s**, and RAM is at 5.8 GB by then.
    - The boundary part extracts in 1.8 s and goes out as 111 MB raw, **25 MB gzipped**. A slice takes 0.7 s.
    - Peak RAM is **7.3 GB**, and it settles at **6.6 GB**.
  - So a 16 GB laptop copes, and an 8 GB one does not.
  - **The user owns the hardware question** (agreed 2026-10-20: CFD is expensive, and users have to understand that). The viewer documents and warns; it does not refuse.
    - Phase 2's install docs carry a hardware-requirements table based on these measurements: about 0.45 GB of RAM per million cells, so 16 GB for cases up to about 20 M cells, plus a WebGL 2 capable GPU.
    - The start screen shows the cell count (read from `metadata.json`, or the mesh header's `note` line, without loading the mesh), from phase 1.
    - From phase 2 it also warns when the case is large for the machine's RAM.
  - **Out of scope here, but the obvious lever:** the reader loads every non-skipped field and interpolates it to points (`SetCreateCellToPoint`). Loading only the fields the scene uses would cut most of the field share of that 6.6 GB. That is a `case.py` change worth its own proposal.
- **Upload size.** The same case is 2.8 GB as a zip. Its binary mesh barely compresses, so mesh plus one time step is about 2.5–3 GB on the wire whichever fields are chosen. That takes roughly 4 min at 100 Mbit/s and 20 min at 20 Mbit/s.
  - Mitigations: per-time-step selection, compression, per-file resume, and visible progress. Per-field selection saves little when the mesh dominates.
  - The server must not run out of disk; that is what the optional free-disk margin is for.
- **Data protection.** The hosted mode stores customer building models. Mitigations: the edge login, time-to-live, delete-now, and no copies outside the workspace. Hosting location and retention need sign-off before customers use it.
- **The process model adds moving parts.** It is confined to `--mode hosted`, with a worker cap and tests that run two sessions side by side.
- **Case ids change URLs.** Bare names keep resolving when unambiguous, and the cockpit is unaffected.
- **Windows-only behaviour gets less CI coverage.** The Windows runner builds the bundle and runs a headless smoke test: start it, open `/api/cases` on `hotRoom`, then fetch one `/api/scene`. Developers stay on Linux.

## Migration Plan

Three phases. Each one is shippable, and each can become its own change when tasks are written.

1. **Foundation.** Package layout, `pyproject`, `dodo.py`, D3 (no stub), D1 (`CaseSource` and ids), `/api/config`, and the start screen in `server` mode. The cockpit is verified unchanged. The Dockerfile lands, and the follow-up in cfd-backend is to build from it.
2. **Local desktop.** D4 local mode and hardening, D5 folder browser and recent folders, D6 Windows bundle and one-page install docs, and D10 if agreed.
3. **Hosted uploads.** D7 upload flow, D8 workers, limits, retention and login.

Rollback is per phase. The shims (D9), `--mode server` as the default and the contract layers (D11) keep the cfd-backend container unaffected at every step. A bad merge is undone by retagging the recorded `cfd-viz:beta` digest and reverting on `main`.

## Open Questions

Answered 2026-10-20:

- **Signing:** a certificate exists and will be used (D6).
- **Hosted:** as simple as possible, secondary, and likely deployed in `equa-deployment` behind its Keycloak edge (D8).
- **Sizes:** up to about 20 M cells and 2–4 GB, no hard limit, serial or parallel by available data (D7).
- **matplotlib:** drop it (D10).
- **Distribution:** EQUA's customer download area, uploaded by hand (D6).

- **Console window:** a visible console is acceptable in v1 (D6).
- **Locked-down machines:** left to the customer. If their policy forbids a signed per-user binary, the viewer is not for them. Both the zip and the installer ship; the installer is the documented route.
- **A large test case:** provided. It was measured above, under "Memory on the user's laptop".

No open questions remain for phase 1.
