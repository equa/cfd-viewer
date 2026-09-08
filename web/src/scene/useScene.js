import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { decodeScene } from '../wire.js'
import { toQuery } from './state.js'

/*
 * Fetching, per part, with a cache.
 *
 * This is the piece the spike said to build before any UI, because it decides
 * the shape of everything above it. Two ideas:
 *
 * 1. **Only stale parts are fetched.** Each part has a signature built from the
 *    query keys that actually change its geometry -- and the list comes from the
 *    SERVER (`meta.partInputs`, i.e. server/scene.py PART_INPUTS), so the two
 *    sides cannot disagree about what a control affects. Move the cut plane and
 *    `slice` (plus whatever seeds off the plane) is stale while `boundary` --
 *    15 MB of it on s2 -- is not, so it is never re-extracted and never re-sent.
 *
 * 2. **Payloads are cached by signature.** A request is immutable for a given
 *    signature, so stepping back to a time step you have already seen, or back
 *    to a slice position, is instant. The Trame path cannot do this at all: it
 *    re-extracts every time, because its unit of work is the whole scene.
 *
 * The cache is capped by BYTES, not entries -- one s2 boundary is worth a
 * thousand hotRoom slices, so counting entries would either cap nothing or cap
 * everything.
 */

const CACHE_BYTES = 96 * 1024 * 1024

class PartCache {
  constructor(limit = CACHE_BYTES) {
    this.limit = limit
    this.entries = new Map()  // key -> { part, header, bytes }
    this.bytes = 0
  }

  get(key) {
    const hit = this.entries.get(key)
    if (!hit) return undefined
    // Re-insert to make this the most recently used.
    this.entries.delete(key)
    this.entries.set(key, hit)
    return hit
  }

  set(key, value) {
    if (this.entries.has(key)) this.bytes -= this.entries.get(key).bytes
    this.entries.set(key, value)
    this.bytes += value.bytes
    // Evict oldest-first until we are back under the cap.
    for (const [k, v] of this.entries) {
      if (this.bytes <= this.limit) break
      if (k === key) continue
      this.entries.delete(k)
      this.bytes -= v.bytes
    }
  }

  clear() { this.entries.clear(); this.bytes = 0 }

  stats() { return { entries: this.entries.size, mb: this.bytes / 1024 / 1024 } }
}

/* A part's signature: its declared inputs, plus the case and time step, which
 * are implicit in every part. Anything not listed cannot make it stale.
 *
 * An input may be conditional -- `{when: {key: value}, keys: [...]}` -- because
 * two dependencies genuinely are. The boundary depends on the cut plane only
 * while it is being clipped by it, and arrows depend on either the plane grid or
 * the isosurface but never both. Ignoring the conditions would make the plane
 * slider re-ship the whole boundary surface on every release, which is most of
 * the point of fetching per part. See PART_INPUTS in server/scene.py -- the
 * declaration is the server's, so the two sides cannot disagree.
 */
function relevantKeys(inputs, query) {
  const keys = []
  for (const entry of inputs) {
    if (typeof entry === 'string') {
      keys.push(entry)
    } else if (Object.entries(entry.when).every(([k, v]) => String(query[k]) === v)) {
      keys.push(...entry.keys)
    }
  }
  return keys
}

function signature(part, query, partInputs) {
  const keys = relevantKeys(partInputs[part] || [], query)
  const values = ['case', 'time_index', ...keys].map((k) => `${k}=${query[k]}`)
  return `${part}|${values.join('&')}`
}

export function useScene({ meta, request, appearance, viewerRef, onHeader }) {
  const cache = useRef(new PartCache())
  const inflight = useRef(null)
  const loaded = useRef(new Map())   // part -> the signature currently on the GPU
  const framed = useRef(null)        // the case we have already framed the camera on
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)

  // A case switch invalidates every cached part, the framing, AND whatever is
  // on the GPU. Dropping the meshes matters: the incoming case may return
  // nothing for a part the outgoing one had -- no building OBJ, an empty
  // isosurface -- and the old geometry would then sit in the new case's scene
  // looking like real data.
  useEffect(() => {
    cache.current.clear()
    loaded.current.clear()
    framed.current = null
    viewerRef.current?.dropAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.case])

  const wanted = Object.entries(appearance.visible)
    .filter(([, on]) => on)
    .map(([part]) => part)

  const query = meta ? toQuery(request) : null
  // A stable dependency for the effect below: the signature of every visible
  // part. Changing an appearance control does not move it, so appearance
  // changes provably cannot trigger a fetch.
  const wantedSignatures = meta
    ? wanted.map((p) => signature(p, query, meta.partInputs)).join('\n')
    : ''

  const sync = useCallback(async () => {
    if (!meta || !request.field) return
    const viewer = viewerRef.current
    if (!viewer) return

    // Parts that went invisible: drop them from the GPU. Hiding the boundary on
    // a big case gives the memory back, rather than just skipping its draw.
    for (const part of [...loaded.current.keys()]) {
      if (!appearance.visible[part]) {
        viewer.dropPart(part)
        loaded.current.delete(part)
      }
    }

    const stale = []
    for (const part of wanted) {
      const sig = signature(part, query, meta.partInputs)
      if (loaded.current.get(part) === sig) continue
      const hit = cache.current.get(sig)
      if (hit) {
        // Cache hit: straight back onto the GPU, no request at all.
        viewer.setParts({ header: hit.header, parts: [hit.part] }, appearance)
        loaded.current.set(part, sig)
        continue
      }
      stale.push([part, sig])
    }
    if (!stale.length) { setBusy(false); return }

    // One extraction at a time: abandon whatever is still in flight rather than
    // letting requests stack up behind it. The deferred sliders stop a drag from
    // queueing twenty of these; this stops the selects and number inputs from
    // queueing the rest, and means the newest request always wins.
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    setBusy(true)
    try {
      const t0 = performance.now()
      const url = api.sceneUrl({ ...query, parts: stale.map(([p]) => p).join(',') })
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok) throw new Error((await response.text()) || response.statusText)
      const buffer = await response.arrayBuffer()
      const t1 = performance.now()
      // What actually crossed the wire, not what we unpacked: the response is
      // gzipped, and on a big case the compressed size is a quarter of the byte
      // length. Reporting byteLength alone slanders the format.
      const entry = performance.getEntriesByType('resource')
        .reverse().find((e) => e.name.includes('api/scene'))
      const wireBytes = entry?.encodedBodySize || 0
      const scene = decodeScene(buffer)
      const t2 = performance.now()
      const header = viewer.setParts(scene, appearance)
      const t3 = performance.now()

      const bySignature = new Map(stale)
      for (const part of scene.parts) {
        const sig = bySignature.get(part.name)
        if (!sig) continue
        loaded.current.set(part.name, sig)
        const bytes = Object.values(part.attributes)
          .reduce((n, a) => n + a.array.byteLength, part.index.byteLength)
        cache.current.set(sig, { part, header, bytes })
      }
      // A part the server returned nothing for (an empty isosurface, a case
      // with no building OBJ) must still be marked loaded, or every render
      // would ask for it again.
      for (const [part, sig] of stale) {
        if (!scene.parts.some((p) => p.name === part)) loaded.current.set(part, sig)
      }

      if (framed.current !== request.case) {
        viewer.frameAll(header.bounds)
        viewer.setOutlineAxis(request.plane_axis, header.bounds)
        framed.current = request.case
      }
      setInfo({
        bytes: buffer.byteLength,
        wireBytes,
        fetchMs: t1 - t0,
        decodeMs: t2 - t1,
        buildMs: t3 - t2,
        cache: cache.current.stats(),
        header,
      })
      onHeader?.(header)
      setError(null)
    } catch (e) {
      if (e.name === 'AbortError') return   // superseded by a newer request
      setError(String(e.message || e))
    } finally {
      if (inflight.current === controller) {
        inflight.current = null
        setBusy(false)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, wantedSignatures])

  useEffect(() => { sync() }, [sync])

  return { busy, error, info, cacheStats: () => cache.current.stats() }
}
