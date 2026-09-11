import { useCallback, useEffect, useRef, useState } from 'react'
import { api, urlOptions } from './api.js'
import { Viewer } from './viewer/Viewer.js'
import { useScene } from './scene/useScene.js'
import { initialAppearance, initialRequest } from './scene/state.js'
import { TopBar } from './ui/TopBar.jsx'
import { SidePane } from './ui/SidePane.jsx'
import { BottomBar } from './ui/BottomBar.jsx'
import { Busy, Hud, Legend } from './ui/overlays.jsx'
import { useShortcuts } from './ui/shortcuts.js'

/*
 * The composition root. It owns the two state objects (see scene/state.js) and
 * the one imperative Viewer, and it is the only place the two meet.
 *
 * Note what is NOT here: any three.js. React drives the scene through Viewer
 * methods, so the UI has no opinion about how pixels are made -- which is what
 * would let react-three-fiber take over the viewer later without touching a
 * single control.
 */

export default function App() {
  const url = useRef(urlOptions()).current
  const stageRef = useRef(null)
  const viewerRef = useRef(null)
  const lutCache = useRef(new Map())

  const [meta, setMeta] = useState(null)
  const [request, setRequestState] = useState(null)
  const [appearance, setAppearanceState] = useState(() => ({
    ...initialAppearance(url.theme), activeTool: 'cutplane',
  }))
  const [lut, setLut] = useState(null)
  const [stats, setStats] = useState({ fps: 0, triangles: 0, calls: 0 })
  // Whether the streamlines on the GPU actually carry travel time. Polled with
  // the stats rather than derived from state, because it depends on what the
  // server returned, not on what was asked for -- an empty stream part cannot
  // be animated however the request looked.
  const [canAnimate, setCanAnimate] = useState(false)

  const setRequest = useCallback((patch) => {
    setRequestState((r) => (typeof patch === 'function' ? patch(r) : { ...r, ...patch }))
  }, [])
  const setAppearance = useCallback((patch) => {
    setAppearanceState((a) => ({ ...a, ...patch }))
  }, [])

  // The domain diagonal, so tube widths scale with the case instead of being a
  // fixed number that is invisible in a hall and absurd in a duct.
  const domain = meta
    ? Math.hypot(meta.bounds[1] - meta.bounds[0],
                 meta.bounds[3] - meta.bounds[2],
                 meta.bounds[5] - meta.bounds[4]) || 1
    : 1

  // -- the viewer ------------------------------------------------------

  useEffect(() => {
    const viewer = new Viewer(stageRef.current)
    viewerRef.current = viewer
    // Test hooks for tests/check_client.py -- see Viewer.grab for why the pixel
    // count is computed in the browser rather than shipped out.
    window.__viz = {
      grab: () => viewer.grab(),
      camera: () => viewer.camera_state(),
      stats: () => viewer.stats(),
      comets: () => viewer.comet_state(),
      // Lets a test (or a tuning sweep) drive the animation's shape directly,
      // including the two knobs the UI keeps fixed at their tuned defaults.
      tuneComets: (o) => viewer.setComets(o),
      partInfo: (name) => viewer.partInfo(name),
    }
    return () => { viewer.dispose(); viewerRef.current = null }
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      const viewer = viewerRef.current
      if (!viewer) return
      setStats(viewer.stats())
      setCanAnimate(viewer.canAnimateStreams())
    }, 500)
    return () => clearInterval(id)
  }, [])

  // -- metadata --------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    const name = request?.case || url.case
    api.meta(name).then((m) => {
      if (cancelled) return
      setMeta(m)
      // A case switch keeps the appearance but rebuilds the request: bounds,
      // fields and time steps are all new.
      setRequestState((r) => (r && r.case === m.case ? r : initialRequest(m)))
    }).catch(() => { /* surfaced by the scene fetch */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.case])

  // -- the colour table ------------------------------------------------

  // 768 bytes per preset, fetched once and cached hard. Switching a colour map
  // is a texture upload: no geometry request, which is the whole point.
  useEffect(() => {
    let cancelled = false
    const apply = (rgb) => {
      if (cancelled) return
      viewerRef.current?.setLut(rgb)
      setLut(rgb)
    }
    const cached = lutCache.current.get(appearance.preset)
    if (cached) { apply(cached); return undefined }
    api.lut(appearance.preset).then((rgb) => {
      lutCache.current.set(appearance.preset, rgb)
      apply(rgb)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [appearance.preset])

  // -- appearance -> viewer --------------------------------------------
  //
  // Every one of these is a uniform or a GPU flag, so this whole block is
  // instant and provably causes no fetch (tests/check_client.py asserts zero
  // /api/scene requests for each of them).

  useEffect(() => {
    viewerRef.current?.setRange(appearance.range[0], appearance.range[1])
  }, [appearance.range])

  useEffect(() => { viewerRef.current?.setBands(appearance.bands) }, [appearance.bands])

  useEffect(() => {
    viewerRef.current?.setOpacityMap(appearance.opacityMap)
  }, [appearance.opacityMap])

  useEffect(() => {
    for (const [part, style] of Object.entries(appearance.styles)) {
      viewerRef.current?.setStyle(part, style)
    }
  }, [appearance.styles])

  useEffect(() => {
    for (const [part, on] of Object.entries(appearance.visible)) {
      viewerRef.current?.setVisible(part, on)
    }
  }, [appearance.visible])

  useEffect(() => { viewerRef.current?.setLighting(appearance.lighting) }, [appearance.lighting])

  useEffect(() => { viewerRef.current?.setComets(appearance.comets) }, [appearance.comets])

  useEffect(() => { viewerRef.current?.setTubes(appearance.tubes) }, [appearance.tubes])

  // Scale the default tube radius to the case the first time its bounds arrive.
  useEffect(() => {
    if (!meta) return
    setAppearanceState((a) => (a.tubes.sized === meta.case
      ? a
      : { ...a, tubes: { ...a.tubes, radius: domain * 0.0015, sized: meta.case } }))
  }, [meta, domain])

  useEffect(() => {
    viewerRef.current?.setTheme(appearance.theme === 'light')
  }, [appearance.theme])

  // -- the scene -------------------------------------------------------

  const onHeader = useCallback((header) => {
    // Auto range tracks the data as field or time changes; manual leaves it be.
    setAppearanceState((a) => (a.autoRange ? { ...a, range: header.range } : a))
  }, [])

  const { busy, error, info } = useScene({
    meta, request: request || {}, appearance, viewerRef, onHeader,
  })

  /* The isosurface's values, re-seeded whenever the field it contours CHANGES
   * IDENTITY -- not when the user edits a value.
   *
   * This is the fix for the isosurface behaving "randomly": the values were
   * seeded exactly once per case, so switching the colour field from U to T
   * left the isovalue at a |U| number, i.e. nowhere near the T range, and the
   * surface came back empty or arbitrary. It contours whatever field is
   * selected, so its values have to follow that field's units.
   *
   * Deps are field IDENTITY only (case, colour field/component, the locked
   * field), so typing a value never triggers a reseed.
   */
  const isoField = request?.contour_field || request?.field
  const isoComponent = request?.contour_field ? 'magnitude' : request?.component
  useEffect(() => {
    if (!request?.case || !isoField) return undefined
    let cancelled = false
    api.range({ case: request.case, field: isoField, component: isoComponent })
      .then(({ range: [lo, hi] }) => {
        if (cancelled) return
        setRequest({
          contour_min: round(lo),
          contour_max: round(hi),
          contour_value: round(lo + (hi - lo) / 2),
        })
      })
      .catch(() => {})
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.case, isoField, isoComponent])

  /* The colour range, when only `robust` moved.
   *
   * `robust` changes no geometry, so it is deliberately in no part's
   * PART_INPUTS -- which meant it moved no signature, triggered no refetch and
   * silently did nothing. It gets the range from the cheap endpoint instead, so
   * it stays instant rather than re-extracting every visible part to deliver
   * two floats. (A scene fetch still reports the range in its header, which is
   * what keeps auto-range tracking a time step; both come from the same server
   * function, so they agree.)
   */
  useEffect(() => {
    if (!request?.case || !request?.field) return undefined
    let cancelled = false
    api.range({
      case: request.case,
      field: request.field,
      component: request.component,
      robust: request.robust ? 1 : 0,
    }).then(({ range }) => {
      if (!cancelled) setAppearanceState((a) => (a.autoRange ? { ...a, range } : a))
    }).catch(() => {})
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.case, request?.field, request?.component, request?.robust])

  // -- actions ---------------------------------------------------------

  const setStyle = useCallback((part, patch) => {
    setAppearanceState((a) => ({
      ...a,
      styles: { ...a.styles, [part]: { ...(a.styles[part] || {}), ...patch } },
    }))
  }, [])

  /* The cut-plane drag. This is the interaction the whole architecture is
   * arranged around: `onDrag` moves a Vector3 in the browser (the red frame
   * follows the thumb, no request, no extraction) and `onCommit` fires exactly
   * one refetch of the parts that actually depend on the plane. */
  const plane = {
    onDrag: (coord) => {
      const viewer = viewerRef.current
      if (!viewer || !request) return
      viewer.setOutlineAxis(request.plane_axis)
      viewer.moveOutline(coord)
      viewer.showOutline(true)
    },
    onCommit: (coord) => {
      if (!request) return
      viewerRef.current?.showOutline(false)
      setRequest({ plane: { ...request.plane, [request.plane_axis]: coord } })
    },
  }

  // Re-point the outline whenever the normal changes, so the first drag after an
  // axis switch shows a frame in the right plane.
  useEffect(() => {
    if (request?.plane_axis) viewerRef.current?.setOutlineAxis(request.plane_axis)
  }, [request?.plane_axis])

  const setComets = useCallback((patch) => {
    setAppearanceState((a) => ({ ...a, comets: { ...a.comets, ...patch } }))
  }, [])

  const setTubes = useCallback((patch) => {
    setAppearanceState((a) => ({ ...a, tubes: { ...a.tubes, ...patch } }))
  }, [])

  const onView = useCallback((direction) => viewerRef.current?.setView(direction), [])
  const onReset = useCallback(() => {
    if (info) viewerRef.current?.frameAll(info.header.bounds)
  }, [info])
  const onPick = useCallback((x, y) => viewerRef.current?.pickCentre(x, y), [])
  useShortcuts({ onView, onReset, onPick })

  const onRescale = useCallback(async () => {
    if (!request?.field) return
    // Swallowed on purpose: a failed range lookup should leave the current
    // range alone, not reject unhandled out of a click handler.
    const answer = await api.range({
      case: request.case,
      field: request.field,
      component: request.component,
      robust: request.robust ? 1 : 0,
    }).catch(() => null)
    if (!answer) return
    const { range } = answer
    setAppearance({ range })
    // The Trame app's _rescale also re-seeded the isosurface from the new
    // range. Kept -- except when the isosurface is locked to a field of its
    // own, where moving its values would defeat the point of locking.
    if (!request.contour_field) {
      const [lo, hi] = range
      setRequest({
        contour_min: round(lo),
        contour_max: round(hi),
        contour_value: round(lo + (hi - lo) / 2),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.case, request?.field, request?.component, request?.robust,
      request?.contour_field, setAppearance, setRequest])

  const onScreenshot = useCallback(() => {
    const data = viewerRef.current?.screenshot()
    if (!data) return
    const link = document.createElement('a')
    link.href = data
    link.download = `foamviz-${request?.case || 'scene'}-t${info?.header.time ?? 0}.png`
    link.click()
  }, [request?.case, info])

  const onRefreshTimes = useCallback(async () => {
    const { times } = await api.times(request.case)
    setMeta((m) => ({ ...m, times }))
    setRequest({ time_index: Math.max(times.length - 1, 0) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.case])

  // One stable tree, always. `request` is null only until /api/meta answers;
  // substituting a placeholder is what keeps `.stage` -- and therefore the
  // Viewer's container and its WebGL context -- mounted from the first commit.
  const req = request || EMPTY_REQUEST

  const legendTitle = [
    info?.header.field ?? req.field,
    meta && meta.fields[req.field] === 3 ? `(${req.component})` : '',
    info?.header.unit ?? '',
  ].filter(Boolean).join(' ')

  return (
    <div className="app" data-theme={appearance.theme}>
      <TopBar
        meta={meta}
        request={req}
        setRequest={setRequest}
        appearance={appearance}
        setAppearance={setAppearance}
        onRescale={onRescale}
        onScreenshot={onScreenshot}
      />
      <div className="body">
        <SidePane
          meta={meta}
          request={req}
          setRequest={setRequest}
          appearance={appearance}
          setAppearance={setAppearance}
          setStyle={setStyle}
          plane={plane}
          comets={appearance.comets}
          setComets={setComets}
          canAnimate={canAnimate}
          tubes={appearance.tubes}
          setTubes={setTubes}
          domain={domain}
        />
        <div className="stage" ref={stageRef}>
          <Hud info={info} stats={stats} />
          <Legend
            lut={lut}
            range={appearance.range}
            bands={appearance.bands}
            title={legendTitle}
          />
          <BottomBar
            meta={meta}
            request={req}
            setRequest={setRequest}
            onView={onView}
            onRefreshTimes={onRefreshTimes}
          />
          <Busy show={busy || !request} error={error} />
        </div>
      </div>
    </div>
  )
}

const round = (v) => Number(v.toPrecision(6))

/* Stands in for `request` between mount and the first /api/meta response. Only
 * ever read, never sent: useScene refuses to fetch without a field. */
const EMPTY_REQUEST = {
  case: '', field: '', component: 'magnitude', plane_axis: 'z',
  plane: { x: 0, y: 0, z: 0 }, patches: [], time_index: 0,
}
