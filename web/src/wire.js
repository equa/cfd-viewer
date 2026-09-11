/*
 * Decoder for the "FVS1" wire format (server/wire.py).
 *
 *   "FVS1" | uint32 header length | header JSON | buffer blobs
 *
 * The header names every buffer by byte offset, type and component count, so
 * this is one arrayBuffer() and a handful of typed-array views -- no parsing.
 * Measured on s2: 588 k triangles decode in 0.3 ms.
 */

const MAGIC = 'FVS1'

export function decodeScene(buffer) {
  const bytes = new Uint8Array(buffer)
  const magic = String.fromCharCode(...bytes.subarray(0, 4))
  if (magic !== MAGIC) throw new Error(`not a scene payload (${magic})`)
  const headerLen = new DataView(buffer).getUint32(4, true)
  const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + headerLen)))
  const base = 8 + headerLen
  const view = (e) => (e.type === 'u32'
    ? new Uint32Array(buffer, base + e.offset, e.length / 4)
    : new Float32Array(buffer, base + e.offset, e.length / 4))
  const parts = header.parts.map((spec) => ({
    name: spec.name,
    mode: spec.mode,
    counts: spec.counts,
    index: view(spec.index),
    // Per-polyline (start, count) pairs, not per-vertex. Present only on LINES
    // parts, and what lets the client build tubes itself instead of the server
    // shipping ~9x the wire in tube geometry. COUNTS are explicit because the
    // polylines do NOT tile the point array -- the tracer leaves orphan points
    // between them, and inferring a length from the next start swallowed those.
    ranges: spec.ranges ? view(spec.ranges) : null,
    attributes: Object.fromEntries(
      Object.entries(spec.attributes)
        .map(([k, e]) => [k, { array: view(e), components: e.components }]),
    ),
  }))
  return { header, parts }
}
