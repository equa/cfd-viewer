# Case Sources — Spec

## Purpose

How the viewer finds, identifies, describes and opens OpenFOAM cases. In phase 1 every source is a directory on the server's filesystem; phase 3 adds uploads as a second kind of source under the same requirements.

## ADDED Requirements

### Requirement: Cases are discovered under one or more roots

The server SHALL accept one or more case roots (`--data`, repeatable). A directory SHALL be a case when it contains `system/controlDict`. Discovery SHALL search each root and its subdirectories down to a configurable depth (`--scan-depth`, in directory levels below the root). In `server` mode it SHALL find every case cfd-backend's own finder finds -- that finder takes any directory below `CFD_HOME` with `system/controlDict`, at any depth -- down to a bound of 4 levels, the default. Below a root, discovery SHALL NOT descend into a directory once that directory is a case, and SHALL skip `processor*` and numeric time directories. Hidden directories SHALL be searched, as the backend searches them. The backend's "bare" cases (no `system/controlDict`) are not listed: there is nothing in them to view. A root SHALL always be scanned, and SHALL itself be listed when it is a case, as `find_cases` did before this change.

#### Scenario: A nested case is found

- **WHEN** the root holds `projects/officeA/run1/system/controlDict` and the server runs with `--scan-depth 3`
- **THEN** `GET /api/cases` lists `run1`

#### Scenario: Server mode finds cases where cfd-backend does

- **WHEN** the server runs in `server` mode without `--scan-depth`, and `CFD_HOME` holds `<uuid>/system/controlDict` and `tests/<name>/system/controlDict` (cfd-backend's test suite stages its cases there)
- **THEN** both `<uuid>` and `<name>` are listed
- **AND** `/viz/?case=<name>` opens the test case

#### Scenario: The default bound

- **WHEN** a case sits 5 directory levels below `CFD_HOME` and the server runs without `--scan-depth`
- **THEN** it is not listed, while a case 4 levels down is

#### Scenario: A root that is a case

- **WHEN** `--data` points directly at `data/hotRoom`
- **THEN** `GET /api/cases` lists exactly one case, `hotRoom`

#### Scenario: Depth is bounded

- **WHEN** a case sits deeper than `--scan-depth` below every root
- **THEN** it is not listed

#### Scenario: Several roots

- **WHEN** the server starts with `--data A --data B`
- **THEN** cases under both A and B are listed

### Requirement: Cases have stable, opaque ids

Every listed case SHALL have an id derived from its root and its path relative to that root. The id SHALL be the same across restarts and rescans for the same root and path. It SHALL NOT contain the host path. Every endpoint that takes `?case=` SHALL accept an id.

#### Scenario: Ids survive a restart

- **WHEN** the server is restarted with the same `--data`
- **THEN** every case keeps its id

#### Scenario: Same name in two places

- **WHEN** `A/run1` and `B/run1` are both cases
- **THEN** they are listed with different ids
- **AND** each id opens its own case

### Requirement: Names keep working for the cockpit

`?case=` SHALL also accept a case's directory name when exactly one listed case has that name. A name that matches several cases, or matches no case, SHALL be answered with 404, and so SHALL a missing or empty `?case=`.

The old fallback, which opened the first case in place of an unknown one, is removed on purpose (agreed 2026-10-20). It is the one change in behaviour the cockpit can observe, and it only shows when the cockpit asks for a case that does not exist.

#### Scenario: The cockpit iframe

- **WHEN** the cfd-backend cockpit opens `/viz/?case=<uuid-dir>` against the shared `CFD_HOME`
- **THEN** that case opens, as before this change

#### Scenario: An unknown case is not silently replaced

- **WHEN** `GET /api/meta?case=nope` is requested and no case has that id or name
- **THEN** the server answers 404
- **AND** it does not open another case in its place

#### Scenario: An ambiguous name

- **WHEN** two listed cases are both called `run1` and `GET /api/meta?case=run1` is requested
- **THEN** the server answers 404 with a message saying the name is ambiguous

### Requirement: The case list is cheap and descriptive

`GET /api/cases` SHALL keep returning `cases` as a list of directory names, unchanged. It SHALL add `entries`, which gives for each case: `id`, `name` (directory name), `label`, `path` relative to its root, `decomposed`, and `cells` when it can be known without reading the mesh, otherwise `null`. Listing SHALL NOT construct a VTK reader or open any mesh file beyond its header.

- `label` SHALL be `NAME` from the case's `metadata.json` when present and non-empty, otherwise `name`.
- `cells` SHALL come from the `nCells:` entry in the header `note` of `constant/polyMesh/owner` (plain or `.gz`), otherwise from `case_info.ncells` in `metadata.json`. `N-CELLS` SHALL NOT be used: it is observed stale (0) on real cases.
- `decomposed` SHALL follow the same rule the reader uses: the newest time exists only in `processor*`.

#### Scenario: An IDA ICE case

- **WHEN** a case directory named by UUID has `metadata.json` with `NAME: "case-4"` and an `owner` header noting `nCells:15757775`
- **THEN** it is listed with label `case-4` and `cells` 15757775
- **AND** the listing completes without loading the mesh

#### Scenario: A case without metadata

- **WHEN** a case has no `metadata.json` and an ASCII `owner` with no `note`
- **THEN** it is listed with its directory name as label and `cells: null`

### Requirement: The HTTP API changes only by addition

Every endpoint, query parameter and response field that existed before this change SHALL keep its name, type and meaning, except that an unknown or ambiguous case now gets a 404. New information SHALL arrive in new fields or new endpoints only, for example `entries` in `/api/cases`, and `caseId` and `caseLabel` in `/api/meta`. For identical inputs:

- `/api/lut` SHALL return byte-identical bodies;
- `/api/scene` SHALL return byte-identical buffers, and a header that is equal apart from the timing field `serverMs` and fields added by this change.

#### Scenario: Golden responses still match

- **WHEN** the contract test replays the queries recorded on `main` before this change against hotRoom
- **THEN** every recorded field is present with an equal value
- **AND** every `/api/lut` body and every `/api/scene` buffer section has the recorded hash
- **AND** the only differences are added fields, plus the listed 404 cases

### Requirement: Cases are read in place without writing to them

Opening a case SHALL NOT create, modify or delete any file inside the case directory. When the case holds a `*.foam` file the reader SHALL use it. Otherwise it SHALL point the reader at a `*.foam` path in the case directory that is not created.

#### Scenario: A read-only case

- **WHEN** a case directory and everything in it are read-only to the server's user
- **THEN** the case lists, opens and renders its boundary, serial or decomposed

#### Scenario: Nothing is written

- **WHEN** a case without any `*.foam` file is opened and a scene is extracted
- **THEN** the case directory's listing and modification times are unchanged afterwards

### Requirement: New cases and time steps appear without a restart

A case written under a root after the server started SHALL be listed on a later `GET /api/cases` without a restart. `GET /api/times` SHALL keep re-reading the time steps of the open case.

#### Scenario: A solve finishes after start-up

- **WHEN** the server starts on an empty root and a case is then written into it
- **THEN** a later `GET /api/cases` lists it
