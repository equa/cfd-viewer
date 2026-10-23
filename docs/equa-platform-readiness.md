# Readiness for equa-ci and equa-deployment

Working notes, 2026-10-22, written at the end of the standalone-viewer phase 1
for the session that picks this up. Not a decision record yet: the decisions
listed under "Open questions" are Niklas's to make, and the first openspec
change written from them supersedes this note.

## Where things stand

- **Phase 1 of `standalone-viewer` is shipped.** GitHub `main` is `46b4cf2`, and
  cfd-backend's CI passed with it. Local `main` is a few commits ahead
  (close-out, the contract shape/value split, the cavity demo case), not yet
  pushed. Every task in `openspec/changes/standalone-viewer/tasks.md` is ticked.
  The change is **not archived** yet.
- **Phases 2 and 3 are designed but not proposed.** That is `local-desktop-viewer`
  (Windows bundle; design D4-D6) and `hosted-case-upload` (uploads, session
  workers; D7-D8).
- **Niklas's direction (2026-10-22):** enrol cfd-viewer in equa-ci and
  equa-deployment, but first make it *ready*. That readiness work comes before
  phase 2, because equa-ci would then carry phase 2's Windows build.

## What equa-ci gives, and what it does not

Checked in `/work/equa-ci` (template `ci.yml.jinja`, `copier.yml`,
`src/equa/ci/tasks/`).

- **The kind would be `repo_kind=service`, as thermarest is.** A service gets:
  - checks (`doit ci`);
  - a Linux image to `ghcr.io/equa/<repo>`, pulled back and run with
    `deploy/verify.env` to check it answers;
  - semantic-release on `main`/`beta`;
  - on beta, a `repository_dispatch` `service-released` to `equa-deployment`.
- **Windows builds exist only for native kinds.** `cmake`/`lisp` publish per-OS
  zips to Azure Universal Packages (`publish_zip`); `component` publishes
  per-platform wheels. There is **no PyInstaller, installer or code signing**
  anywhere in equa-ci, and a service gets no Windows job. Phase 2's bundle
  therefore needs either:
  - (a) a new equa-ci capability (for example `desktop_bundle: true`: a
    `windows-latest` job running `doit bundle_win`, publishing the zip the way
    zips are published today); or
  - (b) a workflow owned by this repo. (a) is the equa-ci way, and a change in
    equa-ci.
- **What adoption brings:**
  - template ownership of `ci.yml`, `pyproject.toml` parts, `.releaserc.json`,
    the root `package.json`, `renovate.json`, `.pre-commit-config.yaml`
    (already identical), the devcontainer and `CLAUDE.md`;
  - `dodo.py` becomes `create_tasks(...)` from the `equa-ci` wheel on the
    private `equa-dev` feed, and that needs `AZURE_DEVOPS_TOKEN` in every
    environment that syncs dev dependencies, this container included;
  - openspec artefacts move to
    `equa-platform-docs/docs/platform/modules/<short>/`, reached through
    symlinks; the repo's own `openspec/` then holds only those links;
  - a row in `equa-ci/repos.toml.template`, plus `doit sync_secrets` and
    `sync_repo_config`.
- **The worked example to follow:**
  `equa-platform-docs/docs/platform/modules/thermarest/changes/archive/2026-10-05-adopt-service-archetype/`,
  with its proposal, design D1-D13 and tasks. The procedure is
  `equa-platform-docs/docs/ci/copier-provisioning.md` ("adopt").
  `service-repo-archetype.md` and `repo-anatomy.md` sit next to it.

## What equa-deployment expects

Checked in `/work/equa-deployment` (`render/core.py`,
`deployments/common/services.toml`, `deployments/common/services/*/compose.yaml`).

- **Each service has two entries.** One is in `deployments/common/services.toml`:
  image, version, digest, `database`. The other is a
  `deployments/common/services/<name>/compose.yaml` with an `x-equa` block:
  `settings`, `secrets`, `health`, and `app` (launcher name, description,
  icon). Look at `equa-thermarest/compose.yaml`.
- **No prefix stripping.** The renderer sets `ROOT_PATH=/<path>`, routes
  `PathPrefix(ROOT_PATH/)` to the service unchanged, and checks health at
  `ROOT_PATH + health`. **The app must serve under `ROOT_PATH` itself**,
  including `/<path>/health`.
- **Login by default.** Every service sits behind the edge login: Traefik
  forward-auth to oauth2-proxy against Keycloak (`LOGIN_MIDDLEWARES`),
  unless it opts out. The identity arrives as `X-Auth-Request-User` and the
  related headers.
- **There is no CFD service on the platform today.** The deployed services are
  materials, simulation, home, thermarest (plus engines), daylight and gsys.
  So there is no `CFD_HOME` volume to read.

## The gaps, by what they block

### A. Blocks adoption (CI must be able to run `doit ci`)

1. ~~**The demo data is not in git.**~~ **Decided and done 2026-10-23:** the
   demo cases are now tiny and versioned with their results (hotRoom coarsened
   to 500 cells, ~400 kB; cavity 400 cells, ~330 kB; both ASCII). A fresh
   checkout runs every suite with no OpenFOAM. See CLAUDE.md "Demo data".
2. ~~**Chromium's libraries on the CI runner.**~~ **Done 2026-10-23:** no GL
   needed anywhere, and the `chromium` doit task (a dependency of `test_ui` and
   `cockpit_contract`) runs `playwright install --only-shell chromium`, adding
   `--with-deps` when root -- thermarest's `test_ui` pattern, which works in
   equa-ci's root `equa-base-ci` container. Not yet run in that container.
3. ~~**The cockpit check needs a cfd-backend checkout.**~~ **Done 2026-10-23.**
   cfd-backend (`equa/cfd-restful-backend`) is **private** -- this note used to
   say public -- and `ci.yml` is template-owned, so CI cannot check it out.
   `tests/cockpit/viz.sh` is a byte-identical copy (`VIZ_SH_FROM` names the
   commit) that runs when no checkout is present; with one, a stale copy fails
   the check. No INCOMPLETE exit any more.
4. **Map the repo-owned tasks onto `create_tasks`.** Part of the adoption
   itself (it needs the `equa-ci` wheel from the private feed). `test_ui`,
   `chromium`, `cockpit_contract`, `requirements_check` and the wheel-contents
   check get appended, as thermarest appends `test_ui` and `kinds_types`;
   `lint_py` is already equa-ci's (ruff check only). Nothing to do before.
5. ~~**Python version.**~~ **Covered:** `requires-python = ">=3.12"` matches the
   template, and `cockpit_contract` already installs `requirements.txt` into a
   fresh Python 3.14 venv, so the image's interpreter is tested by `doit ci`.

### B. Blocks deployment (equa-deployment)

6. ~~**Serve under `ROOT_PATH`.**~~ **Done 2026-10-23:** every route answers
   under `ROOT_PATH` and at the root (as Starlette's `root_path` does for the
   other services), and `/<path>` redirects to `/<path>/` keeping its query.
   Checked in Chromium through `/viewer/`: the case renders and every request
   stays under the prefix (`check_cockpit_contract.py --runtime-only`).
7. ~~**Configuration from the environment.**~~ **Done 2026-10-23:**
   `CASE_ROOTS`, `SCAN_DEPTH`, `PORT`, `ROOT_PATH` (`WEB_DIST_DIR` already);
   the command line wins. The Dockerfile sets `CASE_ROOTS=/data`, `PORT=5003`
   and runs a bare `cfd-viewer`; its HEALTHCHECK reads `PORT`. The image itself
   is not rebuilt yet (no Podman in the dev container).
8. **Where the cases come from on the platform.** Options:
   - a data volume, if a CFD service writes into one;
   - phase 3's uploads (D7), so nothing is visible until someone uploads;
   - a shared or read-only case library.

   This is a product decision, and it decides whether deployment waits for
   phase 3.
9. **Several users.** One process holds one open case behind one lock. Behind
   the platform login several people share it, and one person's case switch
   moves everyone's. D8 (a worker per session) is the designed answer; until
   then the deployment is single-user in practice.
10. **Login and identity.** The edge login covers access, with no code needed.
    Use `X-Auth-Request-User` only when per-user state arrives (phase 3's
    workspaces). Whether the platform expects `GET /v1/me` from every service
    (thermarest has it, for the equa-ui user menu) is still to be checked.

### C. Expected on the platform, not blocking

11. **equa-ui.** Platform web apps use `@equa/ui`: the `TopBar`, the launcher,
    the theme cookie, the user menu. This client is Mantine. thermarest
    adopted with `with_web=false` first and moved to equa-ui later
    (`adopt-equa-ui`); the same order works here. The start screen was laid
    out to map onto the `TopBar` and a card list.
12. **The repository name.** Platform repos are `equa-<name>`
    (`equa-thermarest`). Renaming `equa/cfd-viewer` would need
    cfd-backend's `VIZ_REPO` updated; GitHub redirects clones meanwhile.
13. **The openspec move.** Archive `standalone-viewer` first, so its specs
    become `openspec/specs/`. Then move both into equa-platform-docs with the
    adoption.

### Unchanged and still binding

- **The cockpit contract.** cfd-backend clones `main` and runs `main.py`.
  Adoption keeps `main` as the stable channel, but anything merged there
  still ships with cfd-backend's next build. The shims (`main.py`,
  `foamviz/`, `requirements.txt`) stay until cfd-backend switches to this
  repo's image (`docs/cfd-backend-switch.md`), and equa-ci adoption would
  publish that image.

## Decisions (Niklas, 2026-10-23)

1. **Order: adopt now, deploy later.** Adoption into equa-ci goes ahead;
   deployment waits.
2. **Demo data in CI:** tiny cases, versioned with their results (done).
3. **Cases on the platform: deployment waits for phase 3.** There is no CFD
   data on the platform, and without uploads the viewer is useless there. So
   B8-B10 wait with it.
4. **The repository is renamed `equa-cfd-viewer`** (the package already is).
   cfd-backend's `VIZ_REPO` follows; GitHub redirects clones meanwhile.
5. **The Windows bundle (phase 2):** still open, decided with phase 2.

## Suggested sequence

1. Push local `main`. Archive `standalone-viewer` (`/opsx archive`).
2. Settle questions 1-4.
3. A change, say `ci-readiness`, in this repo: A1-A5, B6-B7, with `doit ci`
   green on a fresh clone, demo data included, in a clean environment.
4. The adoption change, as thermarest's `adopt-service-archetype`: Copier
   adopt, the openspec move, the repos.toml row, secrets, the image to GHCR.
   This touches equa-ci and equa-platform-docs, beyond this repo.
5. The equa-deployment entry when B8-B9 allow, then phase 2 (with the equa-ci
   Windows capability) and phase 3.

## For the session that starts from this

- **This container** has no root and no system GL, and exposes no ports.
  Niklas's host is reachable at 10.88.192.203; the cockpit is on :8080 and
  the viewer at `/viz/`.
- **The toolchain lives in `/work/.tools`** (Node 24, Chromium's libraries
  from conda-forge, Playwright's Chromium) and survives container rebuilds:
  `source /work/.tools/cfd-test-env.sh`. See CLAUDE.md "Environment".
- **Since 2026-10-23 Niklas works in the container too**, so `.venv` and the
  git hooks belong to the container side. A venv cannot be moved or shared
  with the host: its scripts hard-code the interpreter path.
- **`doit ci` is the gate** (about 3 min). Its cockpit check clones HEAD, so
  commit before running it.
