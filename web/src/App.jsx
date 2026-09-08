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

  const setRequest = useCallback((patch) => {
    setRequestState((r) => (typeof patch === 'function' ? patch(r) : { ...r, ...patch }))
  }, [])
  const setAppearance = useCallback((patch) => {
    setAppearanceState((a) => ({ ...a, ...patch }))
  }, [])

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
    }
    return () => { viewer.dispose(); viewerRef.current = null }
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      if (viewerRef.current) setStats(viewerRef.current.stats())
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

  // Seed the isosurface controls from the colour range the first time a case
  // reports one -- otherwise a fresh case opens with an isovalue of 0.5 that
  // sits nowhere near the data.
  useEffect(() => {
    if (info && request && request.contour_min === 0 && request.contour_max === 1) {
      const [lo, hi] = info.header.range
      setRequest({
        contour_min: round(lo),
        contour_max: round(hi),
        contour_value: round(lo + (hi - lo) / 2),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info?.header.case])

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

  const onView = useCallback((direction) => viewerRef.current?.setView(direction), [])
  const onReset = useCallback(() => {
    if (info) viewerRef.current?.frameAll(info.header.bounds)
  }, [info])
  const onPick = useCallback((x, y) => viewerRef.current?.pickCentre(x, y), [])
  useShortcuts({ onView, onReset, onPick })

  const onRescale = useCallback(() => {
    if (info) setAppearance({ range: info.header.range })
  }, [info, setAppearance])

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
