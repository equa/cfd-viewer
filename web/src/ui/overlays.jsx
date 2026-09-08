import { useEffect, useRef } from 'react'
import { Loader, Text } from '@mantine/core'

/*
 * The three things that sit on top of the canvas: the colour legend, the perf
 * HUD, and the busy overlay.
 */

/*
 * The legend, built from the same LUT bytes the shader samples and banded the
 * same way -- a legend promising a smooth ramp over banded geometry would be
 * worse than no legend.
 *
 * One honest gap carried over from the spike: with "opacity by value" on, the
 * bar is still drawn fully opaque, so it over-promises. Showing the ramp means
 * compositing the bar over a checkerboard; not done.
 */
export function Legend({ lut, range, bands, title }) {
  const ref = useRef(null)

  useEffect(() => {
    const bar = ref.current
    if (!bar || !lut) return
    const css = (i) => `rgb(${lut[i * 3]},${lut[i * 3 + 1]},${lut[i * 3 + 2]})`
    const stops = []
    if (bands > 1) {
      for (let b = 0; b < bands; b += 1) {
        const colour = css(Math.min(Math.floor(((b + 0.5) / bands) * 256), 255))
        stops.push(`${colour} ${(b / bands) * 100}%`, `${colour} ${((b + 1) / bands) * 100}%`)
      }
    } else {
      for (let i = 0; i < 256; i += 16) stops.push(`${css(i)} ${(i / 255) * 100}%`)
    }
    bar.style.background = `linear-gradient(to top, ${stops.join(',')})`
  }, [lut, bands])

  const [lo, hi] = range
  const ticks = formatTicks([4, 3, 2, 1, 0].map((i) => lo + ((hi - lo) * i) / 4))

  return (
    <div className="legend">
      <div className="legend-title">{title}</div>
      <div className="legend-body">
        <div className="legend-bar" ref={ref} />
        <div className="legend-ticks">
          {ticks.map((t, i) => <span key={i}>{t}</span>)}
        </div>
      </div>
      {/* Decodes the in-scene triad: the arrow colours are the only thing
          naming the axes, so say which is which. */}
      <div className="axis-key">
        <span className="ax-x">X</span>
        <span className="ax-y">Y</span>
        <span className="ax-z">Z</span>
      </div>
    </div>
  )
}

/*
 * Label ticks so neighbouring ones are always distinguishable. Fixed
 * significant figures are not enough: a 300.0–300.45 range renders as five
 * identical "300"s at 3 s.f. The precision has to come from the SPAN, not from
 * the magnitude. Ported from the Trame app's _format_ticks.
 */
function formatTicks(values) {
  const span = Math.abs(values[0] - values[values.length - 1])
  if (span === 0) return values.map(() => values[0].toPrecision(4))
  const largest = Math.max(...values.map((v) => Math.abs(v)))
  if (largest >= 1e5 || (largest > 0 && largest < 1e-3)) {
    return values.map((v) => v.toExponential(2))
  }
  const decimals = Math.max(0, Math.ceil(-Math.log10(span / values.length)) + 1)
  return values.map((v) => v.toFixed(Math.min(decimals, 8)))
}

export function Hud({ info, stats }) {
  if (!info) return <div className="hud">loading…</div>
  const { bytes, wireBytes, fetchMs, decodeMs, buildMs, cache, header } = info
  const mb = (n) => (n / 1024 / 1024).toFixed(2)
  return (
    <div className="hud">
      <div>
        <b>{mb(wireBytes || bytes)} MB</b> on the wire
        {wireBytes ? <span> ({mb(bytes)} MB raw)</span> : null}
        {' · server '}<b>{header.serverMs.total} ms</b>
        {' · fetch '}<b>{fetchMs.toFixed(0)} ms</b>
        {' · decode '}<b>{decodeMs.toFixed(1)} ms</b>
        {' · upload '}<b>{buildMs.toFixed(1)} ms</b>
      </div>
      <div>
        {header.returned.length
          ? header.parts.map((p) => `${p.name} ${p.counts.primitives.toLocaleString()}`).join(' · ')
          : 'nothing re-extracted'}
        {cache ? ` · cache ${cache.entries} parts / ${cache.mb.toFixed(0)} MB` : ''}
      </div>
      <div>
        <b>{stats.fps} fps</b> · {stats.triangles.toLocaleString()} tris ·{' '}
        {stats.calls} draw call{stats.calls === 1 ? '' : 's'} ·{' '}
        {header.field}/{header.component}
      </div>
    </div>
  )
}

/*
 * Shown while an extraction runs, and it captures clicks: there is no reliable
 * way to abort a running VTK filter (even ParaView cannot), so the overlay
 * stops more work piling on rather than trying to interrupt what is running.
 *
 * It should now appear far less often than in the Trame app, because only the
 * stale parts are re-extracted and a cached part never gets here at all.
 */
export function Busy({ show, error }) {
  if (error) {
    return (
      <div className="busy error">
        <Text size="sm" ta="center">{error}</Text>
      </div>
    )
  }
  if (!show) return null
  return (
    <div className="busy">
      <Loader size="md" />
      <Text size="sm" mt="sm">Extracting…</Text>
    </div>
  )
}
