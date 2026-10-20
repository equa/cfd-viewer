/*
 * Every URL here is RELATIVE on purpose. The viewer is served at / standalone
 * and under /viz/ behind the cfd-frontend nginx, so an absolute '/api/scene'
 * would leave this service and hit the cockpit SPA at the origin root. Relative
 * resolves to /viz/api/scene, which nginx strips back to /api/scene -- the same
 * lesson the Trame app's screenshot link learned.
 */

const q = (params) => new URLSearchParams(
  Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
).toString()

export const api = {
  // What the server is and can do (mode, version, whether the start screen may
  // offer "Add folder" / "Upload case"). Cheap: opens no case.
  config: () => fetch('api/config').then(json),
  cases: () => fetch('api/cases').then(json),
  // The start screen's list: id, name, label, path, decomposed, cells per case.
  // `cases` (plain names) is kept by the server for old clients; this reads
  // only the richer `entries`.
  caseEntries: () => fetch('api/cases').then(json).then((d) => d.entries || []),
  meta: (name) => fetch(`api/meta?${q({ case: name })}`).then(json),
  times: (name) => fetch(`api/times?${q({ case: name })}`).then(json),
  // Just the data range for a field, with no extraction. What makes the robust
  // -range toggle instant, and what the isosurface seeds its values from.
  range: (params) => fetch(`api/range?${q(params)}`).then(json),
  lut: (name) => fetch(`api/lut?${q({ name })}`)
    .then(ok).then((r) => r.arrayBuffer()).then((b) => new Uint8Array(b)),
  sceneUrl: (params) => `api/scene?${q(params)}`,
}

/* An HTTP error with its status, so callers can tell "no such case" (404:
 * show the start screen with the server's reason) from a failed extraction. */
export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function ok(response) {
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()) || response.statusText)
  }
  return response
}

/* Put ?case=<id> into the address bar without a reload or a history entry, so
 * a reload or a bookmark reopens the same case. Every other parameter (the
 * cockpit's ?theme=) is kept. */
export function setUrlCase(id) {
  const url = new URL(location.href)
  if (url.searchParams.get('case') === id) return
  url.searchParams.set('case', id)
  history.replaceState(history.state, '', url)
}

async function json(response) {
  return (await ok(response)).json()
}

/* The theme comes in on the query string, the way the Trame app took it: the
 * cockpit embeds this viewer in an iframe and passes ?theme=light|dark so the
 * two match. Default dark. */
export function urlOptions() {
  const p = new URLSearchParams(location.search)
  return {
    case: p.get('case') || '',
    theme: p.get('theme') === 'light' ? 'light' : 'dark',
  }
}
