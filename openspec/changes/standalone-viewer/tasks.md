<!-- markdownlint-configure-file { "MD013": { "line_length": 1500 } } -->

# Standalone Viewer — Tasks (phase 1)

These tasks cover phase 1 only (proposal, "Scope of this change"); phases 2 and 3 get their own changes.

- Commits follow Conventional Commits from task 1.3 on, and are staged by name.
- All work from group 2 on happens on the branch `feat/standalone-foundation`. It reaches `main` only through group 11, because cfd-backend builds from whatever is on `main` (design D11).
- After every group from 2 to 9, `doit ci` must pass (until group 8 adds it, run the suites directly; the pipeline baseline is 72 since 3.2, the browser baseline 115 since 7.4): lint, both suites, the golden contract test and, once group 9 adds it, the cockpit contract check.

## 1. Baseline and safety net (before anything moves)

- [x] 1.1 Run `tests/test_pipeline.py` and `tests/check_client.py` on the current `main`, and record the results in this task line. Verify the counts are 63/63 and 93/93, or record the actual counts and failures.
  - Result (2026-10-20, `main` at `0cfabeb`): **64/64** and **104/104**, all green. The suites have grown since CLAUDE.md's figures. These counts are the baseline from here on.
  - Environment: VTK 9.7.1 on Python 3.12, with Node 24 and a fresh `web/dist` build. Rendering was software only: Mesa 26 EGL/llvmpipe and Chromium on SwiftShader, from conda-forge.
  - Three environment traps hit in this container:
    - there is no system OSMesa/EGL, so the pipeline suite segfaults at its render checks;
    - a `WAYLAND_DISPLAY` leaked from the host breaks Chromium's SwiftShader;
    - Chromium aborts without a fontconfig file.
- [x] 1.2 Record the ruff finding count with the archetype's rule set, with no fixes applied. Verify the count is written into this task line.
  - Result (ruff 0.15.12, on `main.py foamviz server tests`):
    - The template sets no `[tool.ruff]`, and neither does thermarest. Ruff's defaults give **1** finding (F401).
    - equa-ci's own set (`E,F,I,UP`, line length 100) gives **5**: 3 I001, 1 E501, 1 F401, 4 of them auto-fixable.
    - `ruff format --check` would reformat **9 of 12** files.
  - Group 8 adopts equa-ci's set.
- [ ] 1.3 Commit `openspec/` (this change) and the pending `todo.md` edit as their own commits on `main`. Verify `git status` is clean apart from `.claude/`.
- [x] 1.4 Record the golden API snapshots on `main` (D11 layer 1).
  - Add `tools/record_contract.py`. It starts the server on hotRoom and writes `tests/contract/hotroom.json`, covering:
    - `/api/cases` and `/api/meta`;
    - `/api/times`;
    - `/api/range` for each field and component, robust and plain;
    - `/api/lut` hashes for every preset;
    - `/api/scene` for a fixed query set: each part alone, all parts together, two time steps, each plane axis, three isosurfaces, glyphs on the plane and on the isosurface, crinkle, clip-at-plane, true cell values. For each it keeps the header minus `serverMs`, and a sha256 per buffer section.
  - Commit the recorder and the snapshot.
  - Verify that recording twice on `main` gives an identical file.
  - Result: recorded 56 exchanges (23 scenes, 21 JSON, 9 colour tables, 3 error cases) into `tests/contract/hotroom.json`, with the shared helper `tests/contract.py`. Two recordings were byte-identical, including the randomly seeded streamlines and glyphs (the pipeline's seeding is deterministic for a fixed query order).
  - The snapshot holds for VTK 9.7.1. A VTK upgrade may legitimately move geometry bytes; re-record only as a reviewed change.
- [x] 1.5 Add `tests/test_contract.py`. It replays the snapshot's queries and compares field by field and hash by hash, and an explicit allow-list holds the added fields and the 404 cases. Verify it passes on `main`, and fails when one `/api/meta` field is renamed by a temporary edit.
  - Result: 56/56 on `main`, directly and under pytest. Temporarily renaming `meta.cells` to `nCells` failed the three `/api/meta` exchanges with `.cells: missing`, and the edit was reverted.
  - The allow-list starts as `ALLOWED_CHANGES` in `tests/contract.py`: `/api/meta` with an empty or unknown case may become 404.
- [x] 1.6 Ask Niklas for the current `ghcr.io/equa/cfd-viz:beta` digest (from the host: `podman image inspect` or `skopeo inspect`), and record it in this task line as the rollback point.
  - Result (2026-10-20, `skopeo inspect`): `ghcr.io/equa/cfd-viz:beta` is `sha256:b0fd1cf8aabfc7c84f4835041905d2bc598a28611a165a756c2db65bed22e2e0`. Roll back by retagging this digest as `:beta`.
- [x] 1.7 Create the branch `feat/standalone-foundation` from `main`. Verify `git branch --show-current`.
  - Result: the branch was created from `50d2ddc`.

## 2. Package layout, keeping the cockpit contract

- [x] 2.1 Move the code into the package (D9).
  - `git mv` `foamviz/` and `server/` to `src/equa/cfd_viewer/{foamviz,server}/`, and switch their imports to package-relative ones.
  - Move `main.py`'s argument handling into `src/equa/cfd_viewer/__main__.py`.
  - Verify `python -m equa.cfd_viewer --data data` serves hotRoom from a checkout.
  - Result: done. `server/` now imports `..foamviz`, and `__main__.py` carries the old `main.py` options unchanged.
- [x] 2.2 Add the top-level compatibility package `foamviz/` (`__init__.py`, `case.py`, `pipeline.py`, `colors.py`). It puts `src/` on the path and re-exports the moved modules (D9). Verify the cfd-backend `viz.sh` checks 2 and 3 pass from the checkout root: `python -c "from foamviz.pipeline import FoamPipeline; ..."`.
  - Result: cfd-backend `viz.sh` checks 2–4 pass from the checkout root. The alias registers the real submodules, so `foamviz.pipeline.FoamPipeline is equa.cfd_viewer.foamviz.pipeline.FoamPipeline`.
- [x] 2.3 Add `pyproject.toml` and `uv.lock`. Verify `uv sync` works on a fresh `.venv` under Python 3.12 and 3.14, and that `uv run cfd-viewer --help` runs.
  - Build with hatchling and hatch-vcs, with a fallback version.
  - `requires-python = ">=3.12"`.
  - Runtime dependencies: `vtk>=9.4`, `aiohttp>=3.9`, `numpy>=1.26` and `matplotlib>=3.8` (kept; D10 is withdrawn).
  - A dev group with pytest, ruff, playwright and pillow.
  - The console script `cfd-viewer`.
  - Result: `uv lock` resolves 32 packages. `uv sync` works on 3.12.3 and on 3.14.8, both with VTK 9.7.1. The version comes from git: `0.0.1.dev90+g…` while untagged. The ruff config (`E,F,I,UP`, line length 100) is in `pyproject.toml`; with it, ruff reports 7 findings, the 5 from 1.2 plus 2 import-grouping findings from the relative imports. Group 8 fixes them.
- [x] 2.4 Reduce `main.py` to a shim that calls the package's `main()` with the same options. Verify `python main.py --help` exits 0, and that `python main.py --server --data data --host 0.0.0.0 --port 5003` serves as before.
  - Result: `main.py` is a shim over `equa.cfd_viewer.__main__`. It keeps the default `--data <checkout>/data`.
- [x] 2.5 Generate `requirements.txt` from the lock file (`uv export --no-dev --no-hashes`). Verify `pip install -r requirements.txt` into a fresh Python 3.14 venv gives a runtime in which `python main.py --server --data data` serves hotRoom.
  - Result: verified with plain `pip` on a fresh 3.14.8 venv. The exact cfd-viz command line (`python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"`) on a UUID-named hotRoom serves `/api/cases`, `/api/scene` and the client from `web/dist`.
  - Finding: `vtk`'s own wheel declares `matplotlib>=2.0.0`, so matplotlib stays installed even after group 4 drops it from our dependencies. This led to withdrawing D10 and group 4: matplotlib stays in every deployment.
  - The old hand-written GL/VTK notes in `requirements.txt` move to CLAUDE.md in 11.3; the file is generated now.
- [x] 2.6 Find the client directory in this order: `WEB_DIST_DIR`, then the packaged `equa/cfd_viewer/web/`, then the repo's `web/dist` (the path the cfd-viz image uses), then the hint page. Include `web/dist` in the wheel as `equa/cfd_viewer/web/`. Verify:
  - an installed wheel in a temporary venv outside the repo serves `/` with the client;
  - a checkout with only `web/dist` still serves it.
  - Result: added `hatch_build.py`, a build hook that puts `web/dist` into the wheel as `equa/cfd_viewer/web` and refuses a wheel build without it (`CFD_VIEWER_NO_CLIENT=1` overrides). Editable installs are exempt. The wheel, installed in a venv outside the repo, serves its packaged client. The checkout's `web/dist` path serves too (2.5). The top-level `foamviz` alias is not in the wheel.
- [x] 2.7 Point `tests/test_pipeline.py`, `tests/check_client.py` and `tests/bench.py` at the package. Verify both suites report the task 1.1 counts, and that `test_contract.py` passes.
  - Result: `test_pipeline.py` and `bench.py` import `equa.cfd_viewer`. `check_client.py` starts `python -m equa.cfd_viewer`. The contract test deliberately keeps starting `main.py`, the cockpit's command line. Results on the new layout: **64/64**, **104/104**, contract **56/56**.

## 3. Read cases in place (D3)

- [x] 3.1 Replace `ensure_foam_stub` with a lookup that uses an existing `*.foam` file, or else a `<case>/<name>.foam` path that is never created. Verify:
  - `test_pipeline.py` still passes on hotRoom with its `case.foam` deleted, and no file appears;
  - the contract test passes.
  - Result: `ensure_foam_stub` is replaced by `foam_marker()`, which uses an existing `*.foam` or else the never-created name `<case>/<case>.foam`. With hotRoom's `case.foam` moved aside, the pipeline suite and the contract test (56/56) passed and no `*.foam` appeared. The marker was then put back.
- [x] 3.2 Add read-only checks to `test_pipeline.py`. Verify the checks pass, and that the decomposed one fails when the stub logic is reverted.
  - Use a `chmod -R a-w` copy of hotRoom, serial, and a decomposed copy (`decomposePar`, or a synthetic `processor*` layout built from the demo).
  - Both open and produce boundary geometry.
  - The directory listing and modification times are unchanged afterwards.
  - Result: 8 new checks, so the pipeline baseline is now **72**. Serial and decomposed read-only copies open, pick the right reader (`vtkPOpenFOAMReader` for the decomposed one), give 32 000 cells and 3 non-empty patches, and leave every size and mtime unchanged.
  - The decomposed copy is a hand-built one-processor decomposition, since this container has no OpenFOAM.
  - With the old `touch()` restored temporarily, both "opens" checks fail with `PermissionError` (66/68).
  - Browser suite 104/104.
- [x] 3.3 Correct the CLAUDE.md "VTK-Python traps" note that says the reader needs a `.foam` file. Verify the note cites the 2026-10-20 test.
  - Result: the CLAUDE.md note is rewritten. It says the marker need not exist, how that was checked, and which test guards it.

## 4. (Withdrawn: colour tables without matplotlib)

Withdrawn 2026-10-20 with design D10. VTK depends on matplotlib, so it stays in every deployment, and keeping deployments identical outweighs saving about 10% of the bundle. The group number is kept so the other references stay valid.

## 5. Case sources (D1)

- [x] 5.1 Add `server/sources.py` with `CaseRef` and `DirectorySource(roots, depth)`. It implements discovery, ids, labels, `cells` and `decomposed` as the case-sources spec defines them, and shares the decomposed rule with `FoamCase._is_decomposed`. Verify a new `tests/test_sources.py` covers every case-sources scenario on synthetic directory trees:
  - nested cases at depth 3, and server mode at depth 1 with a nested `old/<uuid2>`;
  - a root that is itself a case, and the depth bound;
  - two roots, and duplicate names;
  - the metadata label;
  - the owner `note`, plain and `.gz`;
  - missing metadata, and `N-CELLS: 0` ignored.
  - Result: `server/sources.py` adds `CaseRef`, `DirectorySource`, `CaseNotFound` and `AmbiguousCase`. The decomposed rule moves to a module-level `is_decomposed()` in `foamviz/case.py`, which `FoamCase` now calls too, so both use one definition. `tests/test_sources.py` covers every scenario: **24 tree tests plus 4 HTTP tests**. One of them checks that depth-1 discovery equals `find_cases` on the real `data/`.
- [x] 5.2 Make `SceneSource` take a source instead of a root, and open cases by id. `open_case` in `server/app.py` resolves an id or a unique name, and otherwise answers 404 with a reason, with no fallback to the first case. Verify pytest cases for the "unknown case" and "ambiguous name" scenarios against the aiohttp test client, and that the contract test passes with exactly the allow-listed 404s.
  - Result: `SceneSource(source)` opens cases by id or unique name. It skips the directory walk when the key denotes the case already open, so slider requests cost nothing extra. `open_case` answers 404 with the reason for a missing, unknown or ambiguous case (the ambiguous message lists the ids). `ALLOWED_CHANGES` is tightened: those two exchanges must now be 404. Contract 56/56.
- [x] 5.3 Make the API changes additive only:
  - `GET /api/cases` keeps `cases` and adds `entries`;
  - `/api/meta` keeps `case` and adds `caseId` and `caseLabel`.

  Verify:
  - the contract test passes;
  - listing the 15.8 M-cell test case returns `cells` 15757775 and label `case-4` in under 1 s, without constructing a reader.
  - Result: `/api/cases` returns `cases` (names, unchanged) plus `entries`. `/api/meta` adds `caseId` and `caseLabel`, with `case` and `cases` unchanged. On the 15.8 M-cell test case, with the VTK reader classes replaced by ones that raise, the listing took **0.4 ms** and gave `cells` 15757775, label `case-4`; resolving by name took 0.2 ms. `resolve()` reads metadata only for the match.
- [x] 5.4 Make `--data` repeatable, and add `--scan-depth` with a default of 1 in `server` mode. An empty root stays non-fatal. Verify by starting with two roots, and with an empty one.
  - Result: `--data` is repeatable and `--scan-depth N` defaults to 1. Two roots list both cases. An empty root in `--server` mode serves `{"cases": [], "entries": []}`, and without `--server` it exits 2 with the old message. `--scan-depth -1` is refused. Suites: pytest 28, pipeline 72/72, browser 104/104, contract 56/56. Ruff is unchanged at 7.

## 6. Config and health endpoints

- [x] 6.1 Add `GET /api/config` (`mode`, `version`, `canAddFolders`, `canUpload`) and `GET /health`. Add `--mode`, which accepts only `server` for now and defaults to it, with `--server` as an alias. Verify pytest cases for both endpoints, including that neither opens a case nor scans a root.
  - Result: `GET /health` returns 200 `ok`. `GET /api/config` returns `{mode, version, canAddFolders: false, canUpload: false}`. `--mode {server}` is the default, with `--server` kept as an alias that stores the same value.
  - `tests/test_endpoints.py` builds the app over a tripwire source that fails the test if a root is scanned, and checks that no case is opened.
  - Behaviour change, as agreed in the proposal round: the default mode is now `server`, so an empty `--data` warns and serves instead of exiting 2 (group 5 still had the old exit).
  - Results: pytest 31, contract 56/56.

## 7. Start screen (client)

- [x] 7.1 Update `api.js`:
  - add `config()`;
  - add `caseEntries()`, which reads `entries`;
  - surface an unknown-case 404 from `/api/meta` as a typed error.

  Verify through the browser suite.
  - Result: `api.config()` and `api.caseEntries()` are added. Errors now throw `ApiError` carrying `.status`, so a 404 from `/api/meta` is told apart from a failed extraction. `setUrlCase(id)` writes `?case=` with `history.replaceState` and keeps `?theme=`.
- [x] 7.2 Add `web/src/ui/StartScreen.jsx`, built with Mantine.
  - It holds a case table with label, relative path, cells and a decomposed badge, plus a text filter, a refresh button, and the empty and not-found notices.
  - It shows when there is no `?case=` and more than one case, or after a not-found error.
  - Keep the React tree stable (CLAUDE.md, "The React tree shape must not change once the Viewer exists"): the start screen overlays the stage, which stays mounted.
  - Verify visually against `data/` with its several cases.
  - Result: `web/src/ui/StartScreen.jsx` holds a header bar (brand, version, mode), "Open a case", the filter, Refresh, the not-found notice, and a table (label with the directory name under it when they differ, path, cells, decomposed badge).
  - It overlays `.app` with `position: absolute`, so the stage and the Viewer stay mounted; `Busy` is hidden while it shows.
  - Checked visually on `data/`: three cases, including `case-1` (the label from `geometric-fancoil-and-beam`'s metadata), 644,413 cells, decomposed; s2 2,337,156; hotRoom 32,000. Both themes.
- [x] 7.3 Choosing a case sets `?case=<id>` with `history.replaceState` and keeps `?theme=`. The TopBar case switcher lists the label, with the id underneath, from `entries`. Verify that a reload reopens the same case.
  - Result: `App` keeps a `caseKey` (from `?case=` or a pick). `/api/meta` runs only when there is one. `initialRequest` stores `meta.caseId`, so every later request names the case by id. The switcher lists `entries` by id, showing the label with the path or name and the id underneath. A reload reopens the same case. The screenshot file name uses `meta.case`.
- [x] 7.4 Extend `tests/check_client.py` with the start-screen scenarios. Verify the suite passes with the new checks, and that the task 1.1 count is still met.
  - The start screen shows with several cases, and no `/api/scene` request is made until a pick.
  - One case opens directly.
  - `?case=` plus `?theme=` skips the start screen.
  - An unknown `?case=` shows the notice.
  - Filtering makes no `/api/meta`, `/api/range` or `/api/scene` request.
  - Result: 11 new checks, so the browser baseline is now **115/115**. They cover every start-screen scenario, including a second server on a root with exactly one case. No `/api/meta`, `/api/range` or `/api/scene` request is made before a pick or while filtering. pytest 31, pipeline 72/72, contract 56/56. Ruff is unchanged at 7.

## 8. Tooling

- [ ] 8.1 Add `dodo.py` with these tasks:
  - `lint_py` (ruff check and format check) and `lint`;
  - `test_py` (pytest without the browser marker; includes the contract test) and `test_ui` (the browser suite);
  - `test`;
  - `web_build` (`npm ci && npm run build`);
  - `package` (`uv build` after `web_build`, then a wheel-contents check for `equa/cfd_viewer/web/index.html`);
  - `requirements_check` (the exported requirements equal `requirements.txt`);
  - `ci`.

  Verify `uv run doit ci` passes, and that it fails when `web/dist` is left out of the wheel or `requirements.txt` is edited by hand.
- [ ] 8.2 Fix the ruff findings recorded in 1.2, or scope the rule set and record the reasons. Verify `doit lint_py` is clean, and that the contract test still passes.
- [ ] 8.3 Add `commitlint.config.js` and a pre-commit config with ruff and commitlint, matching the archetype. Verify a non-conventional commit message is rejected locally.

## 9. Cockpit contract check (D11 layer 3)

- [ ] 9.1 Add `tests/check_cockpit_contract.py`. It mirrors cfd-backend's `cfd-viz` build and runtime in a temporary directory:
  - `git clone` the current commit;
  - check the four required files;
  - run `npm ci && npm run build` in `web/`;
  - build a fresh Python 3.14 venv (`uv python install 3.14`) and `pip install -r requirements.txt`;
  - run `../cfd-backend/tests/smoke/viz.sh` with the clone as `/opt/foamviz`'s stand-in. If that checkout is absent, warn loudly and mark the run incomplete; don't pass silently.

  Verify it passes on the branch, and fails when `requirements.txt` is uncommitted or the `foamviz/` compatibility package is removed.
- [ ] 9.2 Extend the same check with the runtime half.
  - Start the exact CMD line (`python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"`) on a `CFD_HOME` holding hotRoom under a UUID folder name.
  - Put an aiohttp proxy in front that strips `/viz/` the way nginx's `rewrite` does.
  - Drive Chromium through `/viz/?case=<uuid>&theme=light`, and check:
    - the case opens with no start screen;
    - the light theme applies;
    - every request stays under `/viz/`;
    - a case copied in after start-up is listed;
    - a separate start on an empty `CFD_HOME` stays up and answers `/api/cases`.

  Verify every step passes.
- [ ] 9.3 Add the `cockpit_contract` task to `dodo.py` and to `ci`. Verify `uv run doit ci` runs it.

## 10. Image (this repository's own)

- [ ] 10.1 Add a three-stage `Dockerfile` and a `.dockerignore`. The stages are a node web build, a uv wheel build, and a slim Python runtime as an unprivileged user, with no GL libraries, a `HEALTHCHECK` on `/health`, and `CMD cfd-viewer --mode server --data /data --host 0.0.0.0 --port 5003`. Verify with rootless Podman on the host (a manual step for Niklas; the commands go in the task notes) that:
  - the image runs with `data/hotRoom` mounted read-only;
  - `/health` answers 200;
  - a boundary scene extracts.
- [ ] 10.2 Write a note for cfd-backend, not a change there, on switching `cfd-viz` from cloning plus `pip install -r requirements.txt` to this `Dockerfile`, and on when the shims can then go (D9). Verify the note is in `docs/` and linked from the README.

## 11. Gate and merge (D11 layers 4 and 5)

- [ ] 11.1 Ask Niklas to approve pushing `feat/standalone-foundation` to `origin`. Verify the branch exists on GitHub.
- [ ] 11.2 Niklas, on the host:
  - build cfd-backend's unchanged `Containerfile`: `podman build --target cfd-viz --build-arg VIZ_BRANCH=feat/standalone-foundation -t cfd-viz:candidate .`;
  - run `podman run --rm cfd-viz:candidate bash /tests/smoke/viz.sh`;
  - run the stack with `VIZ_IMAGE=cfd-viz:candidate`, then run `check_cockpit_contract.py --runtime-only --base-url http://localhost:8080/viz/` against it.

  Record all results in this task line. Verify the smoke test passes, the browser check passes, and the cockpit's Visualize page opens a real case.
- [ ] 11.3 Update the docs:
  - README: installing from source with `uv`, the new options, the start screen and the `doit` tasks;
  - CLAUDE.md: the package layout and shims, case sources and ids, the additive-API rule, the client resolution order, the start-screen tree rule, the cockpit contract and its check, and the measured 15.8 M-cell numbers.

  Verify every README command runs as written.
- [ ] 11.4 Rebase onto `main` and rerun `uv run doit ci`. Then fast-forward `main` to the branch and push. Both steps need Niklas's approval. Verify `main` holds the rebased commits.
- [ ] 11.5 Watch the next cfd-backend release run (its `release.yml` "Smoke-test cfd-viz" step). Verify it is green, or roll back by retagging the digest recorded in 1.6 and reverting on `main`.
