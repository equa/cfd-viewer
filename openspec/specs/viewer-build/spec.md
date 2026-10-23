# viewer-build Specification

## Purpose
How cfd-viewer is laid out, built, tested, packaged and started, following the equa-ci service archetype without adopting its template. The Windows bundle (phase 2) builds on the wheel defined here.

## Requirements

### Requirement: An installable package with the client inside

The server SHALL be the Python package `equa.cfd_viewer` (distribution `equa-cfd-viewer`), declared in `pyproject.toml` with a lock file. Installing its wheel SHALL be enough to run the viewer, client included: the built client SHALL be package data, and no source checkout or `web/dist` SHALL be needed.

The served client SHALL be chosen in this order: `WEB_DIST_DIR` when set, then the packaged client, then `web/dist` in a source checkout, then the existing "no client build" page.

#### Scenario: Run from the wheel

- **WHEN** the wheel is installed into a fresh environment outside the repository and `cfd-viewer --data <dir>` is run
- **THEN** `GET /` serves the client
- **AND** a case under `<dir>` opens

#### Scenario: A wheel without the client fails the checks

- **WHEN** the client build is left out of the wheel
- **THEN** `doit ci` fails

### Requirement: Entry points and compatibility

The viewer SHALL start from the console script `cfd-viewer` and from `python -m equa.cfd_viewer`, with the options `--data` (repeatable), `--scan-depth`, `--host`, `--port` and `--mode`. In phase 1 `--mode` SHALL accept only `server`, and SHALL default to it. `--server` SHALL remain accepted as an alias.

`main.py` SHALL remain as a shim with the same options, so `python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"` keeps working. `requirements.txt` SHALL remain, generated from the lock file.

#### Scenario: The cfd-backend container is unaffected

- **WHEN** the cfd-backend `cfd-viz` image is rebuilt unchanged against this repository
- **THEN** it starts, and `tests/smoke/viz.sh` in cfd-backend passes

#### Scenario: requirements.txt cannot drift

- **WHEN** a dependency is changed in `pyproject.toml` without regenerating `requirements.txt`
- **THEN** `doit ci` fails

#### Scenario: An empty root is not fatal

- **WHEN** the server starts in `server` mode with an empty `--data`
- **THEN** it keeps running and answers `/api/cases` with an empty list

### Requirement: The cockpit contract holds

Until cfd-backend builds from this repository's own `Dockerfile`, a checkout of this repository SHALL keep satisfying cfd-backend's `cfd-viz` build and its smoke test unchanged. cfd-backend clones `main`, so this SHALL hold for every commit on `main`:

- the root holds `web/package.json`, `web/package-lock.json`, `requirements.txt` and `main.py`;
- `npm ci && npm run build` in `web/` produces `web/dist/index.html`, and the server started from the checkout serves it;
- `pip install -r requirements.txt` on **Python 3.14** installs a working runtime that needs no GL libraries;
- with the checkout root as working directory, `from foamviz.pipeline import FoamPipeline` works, and constructing it does not start rendering. A top-level `foamviz` compatibility package re-exports the moved modules;
- `python main.py --help` exits 0, and `python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"` serves, as an unprivileged user, behind a proxy that strips `/viz/`;
- `/viz/?case=<directory name>&theme=light|dark` opens that case directly in that theme, and every request the page makes stays under `/viz/`;
- every case cfd-backend's finder lists under `CFD_HOME` is found, including its test-suite cases in `CFD_HOME/tests/<name>`;
- an empty `CFD_HOME` is not fatal, and a case written later is picked up without a restart.

#### Scenario: The emulated cfd-viz build passes

- **WHEN** the cockpit contract check runs on a fresh `git clone` of the branch
- **THEN** it performs the steps above in cfd-backend's order, runs cfd-backend's own `tests/smoke/viz.sh` from `../cfd-backend`, and drives the `/viz/` page in Chromium, and every step passes

#### Scenario: A forgotten file is caught

- **WHEN** a file the build needs exists in the working tree but is not committed
- **THEN** the cockpit contract check fails, because it works on a clone

#### Scenario: The real image passes before merge

- **WHEN** cfd-backend's unchanged `Containerfile` builds target `cfd-viz` with `VIZ_BRANCH` set to the change's branch
- **THEN** its smoke test passes in the image
- **AND** the `/viz/` browser check passes against the running container

### Requirement: A health endpoint

`GET /health` SHALL answer 200 once the server is accepting requests. It SHALL NOT scan roots or open a case.

#### Scenario: Healthy at start

- **WHEN** the server has started and `GET /health` is requested
- **THEN** it answers 200 without touching any case root

### Requirement: One dependency set for every deployment

Every deployment SHALL install the same locked runtime dependency set, from `uv.lock`, without leaving out a package or module: the cfd-viz image, this repository's image, an installed wheel, and the Windows bundle from phase 2. Leaving anything out of one deployment SHALL be a recorded decision, based on a measured saving, with a test that runs on that deployment.

#### Scenario: requirements.txt and the lock agree

- **WHEN** `requirements.txt` is regenerated from `uv.lock`
- **THEN** it is identical to the committed file

### Requirement: One command checks everything

`dodo.py` SHALL define the equa-ci task names `lint_py`, `lint`, `test_py`, `test_ui`, `test`, `web_build`, `package` and `ci`, implemented locally without the `equa-ci` package. `doit ci` SHALL run lint, the pipeline suite, the browser suite, the client build and the wheel-contents check, and SHALL fail if any of them fails.

`tests/test_pipeline.py` and `tests/check_client.py` SHALL stay runnable directly, and SHALL also run under pytest, where the browser suite is marked so that it can be deselected.

#### Scenario: CI is one command

- **WHEN** `uv run doit ci` is run in a checkout with the demo case built
- **THEN** it runs lint, both suites, the client build and the wheel check, and exits non-zero if any fails

#### Scenario: The direct scripts still work

- **WHEN** `python tests/test_pipeline.py` is run
- **THEN** it runs and reports as before this change

### Requirement: An image built from this repository

The repository SHALL have a `Dockerfile` that builds the client and the wheel, runs as an unprivileged user, exposes port 5003 with `GET /health` as its health check, and starts in `server` mode on a mounted `--data`. The image SHALL NOT need GL libraries.

#### Scenario: The image serves a mounted case

- **WHEN** the image is run with `data/hotRoom` mounted read-only as its data root
- **THEN** `/health` answers 200
- **AND** the case lists and its boundary scene extracts
