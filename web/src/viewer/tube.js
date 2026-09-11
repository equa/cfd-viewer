/*
 * Tubes, built in the browser from line data.
 *
 * The server ships streamlines as lines and nothing else. `vtkTubeFilter` only
 * inflates data the client can inflate itself, and shipping its output costs
 * ~9x the wire for no saving in server time -- measured on s2: 10.30 MB gzipped
 * of tube geometry against 1.13 MB for the same streamlines as lines, with
 * `vtkStreamTracer` dominating both (3.4 s either way). So the inflation
 * happens here.
 *
 * Two consequences beyond the bandwidth, and they are the better half of the
 * deal:
 *
 *   * switching between lines and tubes re-extracts nothing, so it is instant
 *     the FIRST time, not just on a cache hit;
 *   * the width is a shader uniform, not geometry. This builder emits the tube
 *     CENTRELINE positions with the ring's radial direction as the vertex
 *     normal, and the vertex shader displaces by `normal * uTubeRadius`. So
 *     dragging the width slider moves no vertices and rebuilds nothing.
 *
 * Measured build cost: 4.3 ms for hotRoom's 18 k points, 28.5 ms for 120 k.
 * That is one or two frames, once per streamline fetch.
 */

/* A frame that does not twist.
 *
 * The naive approach -- pick a fixed "up" and cross it with the tangent -- makes
 * the tube spin as the streamline curves, and degenerates wherever the tangent
 * happens to align with that up vector. Parallel transport instead carries the
 * previous normal forward and removes whatever component has become parallel to
 * the new tangent, which is the same thing vtkTubeFilter does.
 */
function seedNormal(tx, ty, tz, out) {
  // Start from the axis the tangent leans on least, so the cross product is
  // well conditioned rather than nearly zero.
  const ax = Math.abs(tx)
  const ay = Math.abs(ty)
  const az = Math.abs(tz)
  if (ax <= ay && ax <= az) { out[0] = 0; out[1] = -tz; out[2] = ty } else if (ay <= az) {
    out[0] = -tz; out[1] = 0; out[2] = tx
  } else { out[0] = -ty; out[1] = tx; out[2] = 0 }
}

export function buildTubeGeometry({
  positions, offsets, attributes = {}, sides = 8,
}) {
  const lineCount = Math.max(offsets.length - 1, 0)
  let pointTotal = 0
  let segmentTotal = 0
  for (let l = 0; l < lineCount; l += 1) {
    const n = offsets[l + 1] - offsets[l]
    if (n < 2) continue
    pointTotal += n
    segmentTotal += n - 1
  }
  if (!pointTotal) return null

  const vertexCount = pointTotal * sides
  const centre = new Float32Array(vertexCount * 3)
  const radial = new Float32Array(vertexCount * 3)
  const index = new Uint32Array(segmentTotal * sides * 6)
  // Every per-vertex attribute the line carried (scalar, travel) is replicated
  // around each ring, so colouring and the comet animation work on a tube
  // exactly as they do on a line.
  const extras = {}
  for (const key of Object.keys(attributes)) extras[key] = new Float32Array(vertexCount)

  const cos = new Float32Array(sides)
  const sin = new Float32Array(sides)
  for (let s = 0; s < sides; s += 1) {
    const angle = (s / sides) * Math.PI * 2
    cos[s] = Math.cos(angle)
    sin[s] = Math.sin(angle)
  }

  const T = new Float32Array(3)
  const N = new Float32Array(3)
  let v = 0
  let w = 0

  for (let l = 0; l < lineCount; l += 1) {
    const start = offsets[l]
    const n = offsets[l + 1] - start
    if (n < 2) continue
    const ringBase = v / sides

    for (let i = 0; i < n; i += 1) {
      const p = (start + i) * 3
      // Central difference, clamped at the ends.
      const before = (start + Math.max(i - 1, 0)) * 3
      const after = (start + Math.min(i + 1, n - 1)) * 3
      T[0] = positions[after] - positions[before]
      T[1] = positions[after + 1] - positions[before + 1]
      T[2] = positions[after + 2] - positions[before + 2]
      let len = Math.hypot(T[0], T[1], T[2])
      if (len < 1e-12) { T[0] = 0; T[1] = 0; T[2] = 1; len = 1 }
      T[0] /= len; T[1] /= len; T[2] /= len

      if (i === 0) {
        seedNormal(T[0], T[1], T[2], N)
      } else {
        const d = N[0] * T[0] + N[1] * T[1] + N[2] * T[2]
        N[0] -= d * T[0]; N[1] -= d * T[1]; N[2] -= d * T[2]
      }
      len = Math.hypot(N[0], N[1], N[2])
      // A tangent that reverses ~180 degrees (a hairpin in the streamline)
      // collapses the transported normal to nothing. Re-seed rather than emit
      // NaNs, which would silently blank the whole draw call.
      if (len < 1e-6) {
        seedNormal(T[0], T[1], T[2], N)
        len = Math.hypot(N[0], N[1], N[2]) || 1
      }
      N[0] /= len; N[1] /= len; N[2] /= len

      const bx = T[1] * N[2] - T[2] * N[1]
      const by = T[2] * N[0] - T[0] * N[2]
      const bz = T[0] * N[1] - T[1] * N[0]

      for (let s = 0; s < sides; s += 1) {
        const rx = cos[s] * N[0] + sin[s] * bx
        const ry = cos[s] * N[1] + sin[s] * by
        const rz = cos[s] * N[2] + sin[s] * bz
        const o = v * 3
        // The centreline point, not the surface point: the shader adds
        // `normal * uTubeRadius`, which is what makes the width free.
        centre[o] = positions[p]
        centre[o + 1] = positions[p + 1]
        centre[o + 2] = positions[p + 2]
        radial[o] = rx
        radial[o + 1] = ry
        radial[o + 2] = rz
        for (const key of Object.keys(attributes)) {
          extras[key][v] = attributes[key][start + i]
        }
        v += 1
      }
    }

    for (let i = 0; i < n - 1; i += 1) {
      const r0 = (ringBase + i) * sides
      const r1 = (ringBase + i + 1) * sides
      for (let s = 0; s < sides; s += 1) {
        const s2 = (s + 1) % sides
        index[w] = r0 + s; index[w + 1] = r1 + s; index[w + 2] = r0 + s2
        index[w + 3] = r0 + s2; index[w + 4] = r1 + s; index[w + 5] = r1 + s2
        w += 6
      }
    }
  }

  return {
    position: centre,
    normal: radial,
    index,
    attributes: extras,
    counts: { vertices: v, triangles: w / 3, polylines: lineCount },
  }
}
