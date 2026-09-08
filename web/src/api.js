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
  cases: () => fetch('api/cases').then(json),
  meta: (name) => fetch(`api/meta?${q({ case: name })}`).then(json),
  times: (name) => fetch(`api/times?${q({ case: name })}`).then(json),
  lut: (name) => fetch(`api/lut?${q({ name })}`)
    .then(ok).then((r) => r.arrayBuffer()).then((b) => new Uint8Array(b)),
  sceneUrl: (params) => `api/scene?${q(params)}`,
}

async function ok(response) {
  if (!response.ok) throw new Error((await response.text()) || response.statusText)
  return response
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
