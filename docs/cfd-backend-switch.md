# Switching cfd-backend's `cfd-viz` to this repository's image

A note for cfd-backend, not a change there. It describes how `cfd-viz` could
stop cloning this repository and build from this repository's own `Dockerfile`
instead, and what can be removed here once it does (openspec
standalone-viewer, tasks 10.2; design D9 and D11).

## Today

cfd-backend's `Containerfile` builds `cfd-viz` in two stages:

1. `viz-build` (node) runs `git clone --branch main` on this repository, checks
   for `web/package.json`, `web/package-lock.json`, `requirements.txt` and
   `main.py`, and runs `npm ci && npm run build` in `web/`.
2. `cfd-viz` (`python:3.14-slim-trixie`) copies the clone to `/opt/foamviz`,
   runs `pip install -r requirements.txt`, and starts
   `python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"`
   as uid 2345.

`tests/smoke/viz.sh` then runs inside the image: it imports VTK, checks that
`foamviz.pipeline.FoamPipeline` builds no render window, runs
`python main.py --help`, and checks for `web/dist/index.html`.

So cfd-backend depends on this repository's **file layout**, not only on its
behavior. That is why this repository keeps three shims (below), and why
every commit here runs `doit cockpit_contract`, which repeats those steps on a
clone.

## The switch

This repository's `Dockerfile` builds the same thing from the lock file: a
`python:3.14-slim-trixie` runtime, uid 2345 in group `users`, no GL libraries,
the client packaged inside the wheel. It adds `/health` and a `HEALTHCHECK`.

```sh
podman build --format docker --build-arg SERVICE_VERSION=<version> -t cfd-viewer:<version> \
    https://github.com/equa/cfd-viewer.git#main
```

On the cfd-backend side, three changes:

1. **Containerfile.** Replace the `viz-build` and `cfd-viz` stages with either:
   - an image published from this repository:
     `FROM ghcr.io/equa/cfd-viewer:<version> AS cfd-viz` (needs CI here to
     publish it; not set up yet); or
   - no stage at all, with compose building it:
     `build: { context: "https://github.com/equa/cfd-viewer.git#main" }`.
2. **Compose.** The image reads `/data` by default. Either mount the volume
   there, or keep `/home/cfd/run` and pass the root on the command line:

   ```yaml
   cfd-viz:
     image: "${VIZ_IMAGE:-ghcr.io/equa/cfd-viewer:beta}"
     user: cfd
     volumes:
       - cfd-home:/home/cfd/run:ro     # read-only now works
     command: ["cfd-viewer", "--mode", "server", "--data", "/home/cfd/run",
               "--host", "0.0.0.0", "--port", "5003"]
   ```

   **The volume can be mounted read-only.** The compose comment about the
   viewer writing a `.foam` marker no longer applies: since the
   standalone-viewer change, cases are read in place and nothing is written to
   them.
3. **Smoke test.** `viz.sh` checks the old file layout, so it needs these
   replacements:

   | Today | After the switch |
   |---|---|
   | `cd /opt/foamviz` | none; the package is installed |
   | `from foamviz.pipeline import FoamPipeline` | `from equa.cfd_viewer.foamviz.pipeline import FoamPipeline` |
   | `python main.py --help` | `cfd-viewer --help` |
   | `[ -f web/dist/index.html ]` | `python -c "import equa.cfd_viewer.server.app as a, sys; sys.exit(0 if (a.DIST / 'index.html').is_file() else 1)"` |
   | none | `/health` answers 200 (the image's own `HEALTHCHECK`) |

   Test 2, that no render window is built during construction, keeps its
   meaning unchanged. Only the import path changes.

Nothing changes in the cockpit itself: the `/viz/` nginx location, the
`?case=<dir>&theme=` iframe URL, and the case names stay as they are.

## What can go here afterwards

Once cfd-backend builds from this repository's `Dockerfile` or image, these
exist only for the old build and can be removed together:

- `main.py`, the shim over `python -m equa.cfd_viewer`;
- `foamviz/`, the top-level alias for `equa.cfd_viewer.foamviz`;
- `requirements.txt`, together with the `requirements_check` task;
- the `web/dist` fallback in `client_dir()` (`server/app.py`), keeping
  `WEB_DIST_DIR` and the packaged client;
- `tests/check_cockpit_contract.py` and the `cockpit_contract` task, replaced
  by a check against the built image;
- in the specs: the viewer-build requirement "The cockpit contract holds",
  rewritten to describe the image.

`tests/test_contract.py`, the recorded HTTP contract, **stays**. The cockpit
embeds the viewer whichever way it is built, so its HTTP behavior remains the
contract.
