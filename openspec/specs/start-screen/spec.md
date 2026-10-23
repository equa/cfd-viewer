# start-screen Specification

## Purpose
What the client shows before a case is open, and how it learns what the server it talks to can do. In phase 1 the server's only mode is `server`; phases 2 and 3 add "Add folder" and "Upload case" to the same screen through the same `/api/config` flags.

## Requirements

### Requirement: The server describes itself

`GET /api/config` SHALL return `mode`, `version`, `canAddFolders` and `canUpload`. In phase 1 `mode` SHALL be `server` and both flags SHALL be `false`. The endpoint SHALL NOT open a case.

#### Scenario: Phase 1 server

- **WHEN** `GET /api/config` is requested
- **THEN** it answers `{mode: "server", canAddFolders: false, canUpload: false, version: <package version>}`

### Requirement: No case chosen shows the start screen

When the page is opened without `?case=`, the client SHALL open the case directly if exactly one case is listed. Otherwise it SHALL show the start screen and SHALL NOT request `/api/meta` or `/api/scene` until a case is chosen.

The start screen SHALL list every case from `/api/cases` with its label, relative path, cell count (when known) and a "decomposed" mark, filterable by text. Choosing one SHALL open it and put its id into the URL as `?case=<id>`, so the view can be bookmarked and reloaded.

#### Scenario: Several cases

- **WHEN** the page is opened at `/` and three cases are listed
- **THEN** the start screen lists all three with their labels
- **AND** no `/api/scene` request has been made

#### Scenario: Choosing a case

- **WHEN** the user picks `case-4` on the start screen
- **THEN** the viewer opens it
- **AND** the address bar shows `?case=<its id>`

#### Scenario: One case

- **WHEN** the page is opened at `/` and exactly one case is listed
- **THEN** the viewer opens it without showing the start screen

#### Scenario: No cases

- **WHEN** no case is listed
- **THEN** the start screen says no case was found, names what makes a directory a case, and offers a refresh

### Requirement: An unknown case returns to the start screen

When `?case=` names no case, or an ambiguous name, the client SHALL show the start screen with the server's message instead of a broken viewer.

#### Scenario: A stale bookmark

- **WHEN** the page is opened with `?case=` naming a case that was deleted
- **THEN** the start screen is shown with a notice that the case was not found

### Requirement: Switching cases goes through the same list

The viewer's case switcher SHALL list the same entries as the start screen (label, with id underneath) and SHALL open cases by id.

#### Scenario: Two cases with the same directory name

- **WHEN** two cases are both called `run1`
- **THEN** both are offered in the switcher and each opens its own case

### Requirement: Embedding is unchanged

With `?case=` and `?theme=` given, as the cockpit does, the client SHALL open that case directly in that theme, and SHALL NOT show the start screen.

#### Scenario: Cockpit iframe

- **WHEN** the page is opened at `/viz/?case=<uuid-dir>&theme=light`
- **THEN** that case opens in the light theme, with no start screen shown first

### Requirement: The start screen does not touch scene state

The start screen and the switcher SHALL use only `/api/config` and `/api/cases`. They SHALL NOT change any part signature. Opening a case SHALL go through the existing `/api/meta` path.

#### Scenario: Browsing the list costs no extraction

- **WHEN** the user scrolls and filters the start screen
- **THEN** no `/api/meta`, `/api/range` or `/api/scene` request is made
