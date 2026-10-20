# Standalone Viewer — Proposal

## Summary

- cfd-viewer becomes usable on its own, not only as the `/viz/` iframe of the cfd-backend cockpit, in two ways:
  - **Local**: a per-user Windows 11 install (no admin rights, no container) that runs the viewer on the user's machine and opens it in their browser on cases in their own folders.
  - **Hosted**: a remote viewer to which the user uploads a case from their machine through the browser.
- Both rest on one change inside the server: a **case source** abstraction in place of today's single `--data` directory, so "a folder on this machine" and "an upload in a session workspace" are two implementations of the same thing.
- The embedded cockpit mode keeps working unchanged (`/viz/?case=<name>&theme=`).
- The repository is reshaped along the equa-ci service archetype (package layout, `pyproject.toml`, `dodo.py`, Conventional Commits, its own `Dockerfile`) **without** formally adopting the template. Adoption is a later, separate decision; this change only makes it cheap.

### Scope of this change

`design.md` covers all three phases, because their decisions depend on each other. **This change implements phase 1 only**: the foundation that both later phases build on. Its specs and tasks are phase 1's:

| Phase | Change | Contents |
| --- | --- | --- |
| 1 | **this change** (`standalone-viewer`) | package layout and tooling, case sources and ids, read-only cases, matplotlib out, `/api/config`, `/health`, the start screen, the Dockerfile |
| 2 | `local-desktop-viewer` (later) | local mode and its hardening, the folder browser, the Windows bundle, install docs and hardware requirements (design D4–D6) |
| 3 | `hosted-case-upload` (later) | uploads, session workers, retention (design D7–D8) |

Phases 2 and 3 each get their own proposal, specs and tasks, and refer back to this design.

## Why

The viewer can only show cases that sit on the cfd-backend's `cfd-home` volume, so it is only reachable through the cockpit.
Users who run OpenFOAM themselves, or receive a case from a colleague, have no way to look at it with the viewer.
Our users are on Windows 11, often without admin rights, so "install Python and VTK" or "run a container" is not an answer; it has to be an unzip-or-click user install with a one-page how-to.

A hosted viewer covers the users who cannot or will not install anything, and the same upload path serves sharing a case for review.

The server already does almost all of the work: it reads a directory, extracts geometry with VTK and serves a built client over relative URLs. What is missing is packaging, a way to pick cases that are not under one fixed root, and a way to get files from the browser to a remote server.

## What Changes

- **Case sources.** `SceneSource` stops taking a filesystem root and takes a `CaseSource` instead: `DirectorySource` (today's behaviour, plus recursive discovery) and `UploadSource` (per-session workspaces). Cases get an opaque id; a plain name stays accepted for the cockpit.
- **Read-only cases.** The `case.foam` stub is no longer written into the case directory; VTK reads a case through a non-existent `*.foam` path (checked with VTK 9.7.1 on a read-only copy of `hotRoom`).
- **Run modes.** `--mode local` binds to `127.0.0.1`, opens the browser and allows choosing case folders; `--mode server` is today's container behaviour; `--mode hosted` enables uploads.
- **Local folder choice.** In local mode the user adds case folders through a server-side folder browser in the UI, by passing a path, or by dropping a folder onto the launcher. Recent folders are remembered.
- **Uploads (hosted, secondary).** The client lets the user pick or drop a case folder (up to about 20 M cells, 2–4 GB, serial or decomposed), reads its layout in the browser, lets them choose time steps and fields, and uploads only the files needed, compressed and resumable per file. The server streams them into a session workspace with a time-to-live. No hard size limit. No auth code in the viewer: hosted mode sits behind `equa-deployment`'s Keycloak edge login.
- **A case picker.** When no `?case=` is given, the client shows a start screen: the cases it can see, and (by mode) "Add folder" or "Upload case".
- **Windows bundle.** A frozen build (PyInstaller, one-folder) with the built client inside, shipped as a zip and as a per-user installer. Code-signed once the certificate is available. Uploaded by hand to EQUA's customer download area. One-page install docs.
- **Repository layout along the archetype.** `src/equa/cfd_viewer/`, `pyproject.toml` + `uv.lock`, a console entry point, `dodo.py` with `lint`/`test`/`ci`, the two test scripts run by pytest, a repo-owned `Dockerfile` for the server image (so cfd-backend stops cloning this repo to build it).
- **Docs.** README install sections (Windows, hosted); CLAUDE.md records the case-source model and the run modes.

### Out of scope

| Item | Where it goes |
| --- | --- |
| Moving the client from Mantine to `@equa/ui` | Its own change; the start screen is laid out so it maps onto the equa-ui shell |
| Formal equa-ci adoption (Copier template, feed, `equa-deployment`) | A later decision |
| Reading OpenFOAM in the browser (WASM) | Not planned; see design D2 |
| macOS/Linux desktop bundles | Later if asked for; `uvx`/pip already works there |
| Reading the backend's `metadata.json` for discovery | Separate open question in CLAUDE.md |
| Persisting scene/camera state | `todo.md`, unchanged |

## Capabilities

### New Capabilities

This change (phase 1):

- `case-sources`: how the viewer finds, identifies and opens cases. Phase 1 covers directories; uploads are added by phase 3.
- `start-screen`: what the client shows when no case is chosen, and how it learns what the server can do.
- `viewer-build`: how the viewer is laid out, built, tested, packaged and started.

Later changes:

- `local-desktop-run`: the Windows per-user install and the local run mode (phase 2).
- `case-upload`: getting a case from the user's machine into a hosted viewer (phase 3).

### Modified Capabilities

None yet; the viewer has no specs. The `/api/*` contract gains a case id and new endpoints; existing calls keep working.

## Impact

- **Server**: `server/scene.py`, `server/app.py`, `main.py` and `foamviz/case.py` change; `foamviz/pipeline.py` does not.
- **Client**: a start screen, an upload flow and an "add folder" dialog; the viewer itself is unchanged.
- **cfd-backend**: its Containerfile can build from this repo's `Dockerfile` or image instead of cloning; the iframe URL is unchanged. Changing cfd-backend is a follow-up there, not part of this change.
- **Dependencies**: PyInstaller and Inno Setup (build only). `matplotlib` is removed; its colour maps are kept as committed tables (agreed 2026-10-20).
- **Security**: local mode listens on loopback only, with a per-launch token; hosted mode receives customer files, so it relies on the edge login and deletes them after a time-to-live. See design.
