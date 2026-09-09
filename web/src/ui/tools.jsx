import {
  Button, Divider, Group, MultiSelect, SegmentedControl, Select, Switch, Text,
} from '@mantine/core'
import {
  ColourBy, INPUT, LabelledSlider, NumberField, SELECT, Tag, stepFor, useDeferred,
} from './controls.jsx'

/* A switch label carrying its cost. The mixed panels -- Boundary especially --
 * are where the client/server split is least guessable, and a tag beside the
 * label is the cheapest way to make it visible while you use it. */
const tagged = (text, kind) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
    {text}<Tag kind={kind} />
  </span>
)

/*
 * One panel per tool, the same six the Trame app has, in the same order, with
 * the same controls and the same defaults. Selecting a tool changes only which
 * panel is on screen -- visibility is the separate eye toggle in the tool stack,
 * so a slice, streamlines and isosurfaces can all be drawn while you tune one.
 *
 * Where a control's cost changed, the control changed with it. Three notes
 * worth reading before editing:
 *
 * - The **line-width sliders are gone**, from streamlines and from geometry.
 *   WebGL caps `lineWidth` at 1, so the Trame app shipped two sliders that
 *   provably did nothing. Tubes are the real answer for thick streamlines and
 *   are one switch away; three.js `Line2`/`LineMaterial` would be the real
 *   answer for the rest, and is not wired yet.
 * - **Mesh edges on the shell** became a client control (EdgesGeometry), so it
 *   no longer costs a round trip.
 * - **Opacity** everywhere is a uniform, hence live.
 */

const AXES = [
  { label: 'X', value: 'x' },
  { label: 'Y', value: 'y' },
  { label: 'Z', value: 'z' },
]

export function CutPlaneTool({ meta, request, setRequest, style, setStyle, plane }) {
  const axis = request.plane_axis
  const [lo, hi] = meta.axisRange[axis]
  // A slice needs sub-metre precision, so the fields take decimals; the step
  // keeps the slider itself usable across domains of any size.
  const step = Math.max((hi - lo) / 500, 0.001)

  // The numeric world point is the source of truth and is INERT until Apply --
  // that is the debounce for typed input, so keystrokes never re-extract.
  const point = useDeferred(request.plane, (draft) => {
    const clamped = { ...draft }
    clamped[axis] = Math.min(Math.max(Number(draft[axis]), lo), hi)
    setRequest({ plane: clamped })
  })

  return (
    <>
      <Text size="xs" c="dimmed" mb="xs">
        Slice, stream seeds and arrows all sit on this plane.
      </Text>
      <SegmentedControl
        fullWidth
        size="xs"
        data={AXES}
        value={axis}
        onChange={(v) => setRequest({ plane_axis: v })}
        mb="sm"
        data-ctl="plane-axis"
      />

      {/*
        The position slider previews live and commits once, on release.
        During the drag the red frame follows it entirely in the browser -- no
        request, no extraction. The release is the only round trip.
      */}
      <LabelledSlider
        label="Position"
        unit=" m"
        min={lo}
        max={hi}
        step={step}
        value={request.plane[axis]}
        onPreview={plane.onDrag}
        onCommit={plane.onCommit}
        data-ctl="plane-slider"
      />

      <Group grow gap={6} mt={4}>
        {['x', 'y', 'z'].map((a) => {
          const [alo, ahi] = meta.axisRange[a]
          return (
            <NumberField
              key={a}
              {...INPUT}
              label={a.toUpperCase()}
              value={point.draft[a]}
              step={stepFor(ahi - alo)}
              decimalScale={3}
              onChange={(v) => point.set({ [a]: Number(v) })}
              data-ctl={`plane-${a}`}
            />
          )
        })}
      </Group>
      <Button
        fullWidth
        mt="xs"
        size="xs"
        variant="light"
        disabled={!point.dirty}
        onClick={point.apply}
        data-ctl="plane-apply"
      >
        Apply
      </Button>

      <Divider my="sm" />
      {/* With the mesh on, the slice becomes a crinkle slice: the whole cells
          the plane passes through, i.e. the true mesh, not a flat cut. That is a
          different extraction, so it stays a server control. */}
      <Switch
        size="xs"
        label={tagged('Mesh (crinkle)', 'server')}
        checked={request.slice_edges}
        onChange={(e) => setRequest({ slice_edges: e.currentTarget.checked })}
        data-ctl="slice-edges"
      />
      <LabelledSlider
        label="Opacity"
        min={0}
        max={1}
        step={0.01}
        live
        value={style.opacity ?? 1}
        onChange={(v) => setStyle({ opacity: v })}
        data-ctl="slice-opacity"
      />
    </>
  )
}

export function BoundaryTool({ meta, request, setRequest, style, setStyle }) {
  const patches = useDeferred({ patches: request.patches }, (draft) => setRequest(draft))
  return (
    <>
      <ColourBy
        ctl="surface"
        colored={style.colored ?? false}
        solid={style.solid}
        onChange={setStyle}
      />
      <Switch
        mt={6}
        size="xs"
        label={tagged('Cull near walls', 'client')}
        checked={style.cull ?? true}
        onChange={(e) => setStyle({ cull: e.currentTarget.checked })}
        data-ctl="surface-cull"
      />
      <Switch
        mt={6}
        size="xs"
        label={tagged('Mesh edges', 'client')}
        checked={style.edges ?? false}
        onChange={(e) => setStyle({ edges: e.currentTarget.checked })}
        data-ctl="surface-edges"
      />
      <Switch
        mt={6}
        mb="xs"
        size="xs"
        label={tagged('Cut away at plane', 'server')}
        checked={request.surface_clip}
        onChange={(e) => setRequest({ surface_clip: e.currentTarget.checked })}
        data-ctl="surface-clip"
      />
      <LabelledSlider
        label="Opacity"
        min={0}
        max={1}
        step={0.01}
        live
        value={style.opacity ?? 1}
        onChange={(v) => setStyle({ opacity: v })}
        data-ctl="surface-opacity"
      />

      <Divider my="sm" />
      {/* Reading fewer patches is the cheapest way to shrink the boundary, which
          dominates the wire (15 of s2's 16.2 MB raw). Deferred behind Apply
          because it forces a reload of the case, not just a re-extraction. */}
      <MultiSelect
        {...SELECT}
        label="Patches to read"
        description="Empty = all"
        data={meta.patches}
        value={patches.draft.patches}
        onChange={(v) => patches.set({ patches: v })}
        clearable
        data-ctl="patches"
      />
      <Button
        fullWidth
        mt="xs"
        size="xs"
        variant="light"
        disabled={!patches.dirty}
        onClick={patches.apply}
        data-ctl="patches-apply"
      >
        Apply
      </Button>
    </>
  )
}

export function ContourTool({ meta, request, setRequest, style, setStyle }) {
  const single = Number(request.contour_count) <= 1
  const locked = !!request.contour_field
  const fields = meta ? Object.keys(meta.fields).sort() : []
  // Which field the surface is actually contoured from -- the thing that was
  // previously left to guesswork.
  const base = request.contour_field || request.field
  // The values are in the contoured field's units, so the step has to be too.
  const isoStep = stepFor(Number(request.contour_max) - Number(request.contour_min))
  return (
    <>
      {/*
        The isosurface contours the COLOUR field by default, which is usually
        what you want and is why its values re-seed when that field changes.
        Locking pins it to a field of its own, so recolouring then recolours the
        surface instead of moving it -- which is how you get the classic
        "speed isosurface, coloured by temperature" view.
      */}
      <Switch
        size="xs"
        label={tagged('Lock field', 'server')}
        description={locked
          ? 'Pinned — the colour field no longer moves this surface'
          : `Following the colour field (${base})`}
        checked={locked}
        onChange={(e) => setRequest({
          contour_field: e.currentTarget.checked ? request.field : '',
        })}
        data-ctl="contour-lock"
      />
      {locked ? (
        <Select
          {...SELECT}
          mt={6}
          data={fields}
          value={request.contour_field}
          onChange={(v) => v && setRequest({ contour_field: v })}
          allowDeselect={false}
          data-ctl="contour-field"
        />
      ) : null}
      <Text size="xs" c="dimmed" mt={6} mb="xs" data-ctl="contour-base">
        Contouring <b>{base}</b>; values below are in that field's units.
      </Text>
      {/* 1 / 3 / 5 surfaces (odd, so one sits mid-range): a slider stepping by 2. */}
      <LabelledSlider
        label="Surfaces"
        min={1}
        max={5}
        step={2}
        precision={0}
        value={request.contour_count}
        onCommit={(v) => setRequest({ contour_count: v })}
        data-ctl="contour-count"
      />
      {single ? (
        <NumberField
          {...INPUT}
          label="Value"
          value={request.contour_value}
          step={isoStep}
          decimalScale={4}
          onChange={(v) => setRequest({ contour_value: Number(v) })}
          data-ctl="contour-value"
        />
      ) : (
        <Group grow gap={6}>
          <NumberField
            {...INPUT}
            label="Min"
            value={request.contour_min}
            step={isoStep}
            decimalScale={4}
            onChange={(v) => setRequest({ contour_min: Number(v) })}
            data-ctl="contour-min"
          />
          <NumberField
            {...INPUT}
            label="Max"
            value={request.contour_max}
            step={isoStep}
            decimalScale={4}
            onChange={(v) => setRequest({ contour_max: Number(v) })}
            data-ctl="contour-max"
          />
        </Group>
      )}
      <Text size="xs" c="dimmed" mt={6} mb="xs">
        Several surfaces spread evenly inside [min, max].
      </Text>
      <ColourBy
        ctl="contour"
        colored={style.colored ?? true}
        solid={style.solid}
        onChange={setStyle}
      />
      {/* Nested translucent shells stack up fast, hence the 0.35 default. */}
      <LabelledSlider
        label="Opacity"
        min={0.05}
        max={1}
        step={0.05}
        live
        value={style.opacity ?? 0.35}
        onChange={(v) => setStyle({ opacity: v })}
        data-ctl="contour-opacity"
      />
    </>
  )
}

export function StreamTool({
  meta, request, setRequest, style, setStyle, comets, setComets, canAnimate,
}) {
  /*
   * Everything here is heavy: vtkStreamTracer is 2.6 s of the 3.1 s server time
   * on s2, and it costs the same in the Trame path -- it is not a cost of this
   * architecture. So the whole group defers behind Apply, exactly as the Trame
   * app decided. The vector field stays live because it is also the arrows'
   * orientation field, and changing it while arrows are shown should be visible.
   */
  const tune = useDeferred({
    stream_seeds: request.stream_seeds,
    stream_length: request.stream_length,
    stream_tubes: request.stream_tubes,
    stream_radius: request.stream_radius,
  }, (draft) => setRequest(draft))

  return (
    <>
      <Select
        {...SELECT}
        label="Vector field"
        data={meta.vectorFields}
        value={request.vector_field || null}
        onChange={(v) => v && setRequest({ vector_field: v })}
        allowDeselect={false}
        mb="sm"
        data-ctl="vector-field"
      />
      <LabelledSlider
        label="Seeds"
        min={5}
        max={400}
        step={5}
        precision={0}
        value={tune.draft.stream_seeds}
        onCommit={(v) => tune.set({ stream_seeds: v })}
        data-ctl="stream-seeds"
      />
      <LabelledSlider
        label="Max length (× domain)"
        min={0.5}
        max={15}
        step={0.5}
        precision={1}
        value={tune.draft.stream_length}
        onCommit={(v) => tune.set({ stream_length: v })}
        data-ctl="stream-length"
      />
      {/* Tubes are triangles, so they escape the WebGL 1-px line-width cap that
          made the Trame line-width slider inert. That slider is not ported. */}
      <Switch
        size="xs"
        label={tagged('Tubes', 'server')}
        description="Real geometry — thick lines are impossible in WebGL"
        checked={tune.draft.stream_tubes}
        onChange={(e) => tune.set({ stream_tubes: e.currentTarget.checked })}
        data-ctl="stream-tubes"
      />
      {tune.draft.stream_tubes ? (
        <LabelledSlider
          label="Tube width"
          min={0.2}
          max={5}
          step={0.1}
          precision={1}
          value={tune.draft.stream_radius}
          onCommit={(v) => tune.set({ stream_radius: v })}
          data-ctl="stream-radius"
        />
      ) : null}
      <Button
        fullWidth
        mt="xs"
        size="xs"
        variant="light"
        disabled={!tune.dirty}
        onClick={tune.apply}
        data-ctl="apply-stream"
      >
        Apply
      </Button>

      <ColourBy
        ctl="stream"
        colored={style.colored ?? true}
        solid={style.solid}
        onChange={setStyle}
      />

      <Divider my="sm" />

      {/*
        Animation. Entirely client-side -- an animated dash pattern over the
        per-vertex travel time the server already ships, so it is instant and
        sits outside the Apply group above on purpose.

        It is not only decoration: a static streamline is direction-ambiguous,
        and because the comets ride real transport time rather than arc length
        they visibly speed up where the flow does.
      */}
      <Switch
        size="xs"
        label={tagged('Animate flow', 'client')}
        description={canAnimate
          ? 'Comets ride the streamlines at the local flow speed'
          : 'Needs streamlines to be drawn first'}
        checked={comets.on}
        disabled={!canAnimate}
        onChange={(e) => setComets({ on: e.currentTarget.checked })}
        data-ctl="comets"
      />
      {comets.on ? (
        <div style={{ marginTop: 8 }}>
          <LabelledSlider
            label="Speed"
            min={0.05}
            max={2}
            step={0.05}
            precision={2}
            live
            value={comets.speed}
            onChange={(v) => setComets({ speed: v })}
            data-ctl="comet-speed"
          />
          {/* Spacing between comet heads, in the same normalised travel units:
              small = a dense stream of short comets, large = a few long ones. */}
          <LabelledSlider
            label="Spacing"
            min={0.1}
            max={2}
            step={0.05}
            precision={2}
            live
            value={comets.period}
            onChange={(v) => setComets({ period: v })}
            data-ctl="comet-spacing"
          />
        </div>
      ) : null}
    </>
  )
}

export function GlyphTool({ meta, request, setRequest, style, setStyle }) {
  return (
    <>
      {/* Arrows always orient by a vector field (default U), independent of the
          colour field -- they went invisible when colouring by a large scalar
          like T. Shares the selection with streamlines. */}
      <Select
        {...SELECT}
        label="Vector field"
        data={meta.vectorFields}
        value={request.vector_field || null}
        onChange={(v) => v && setRequest({ vector_field: v })}
        allowDeselect={false}
        mb="sm"
        data-ctl="glyph-vector-field"
      />
      <SegmentedControl
        fullWidth
        size="xs"
        data={[
          { label: 'On plane', value: 'slice' },
          { label: 'On isosurface', value: 'isosurface' },
        ]}
        value={request.glyph_source}
        onChange={(v) => setRequest({ glyph_source: v })}
        mb="sm"
        data-ctl="glyph-source"
      />
      {/* Room airflow spans orders of magnitude, so a plume core 100x faster
          than the bulk makes everything else vanish. Uniform-length arrows --
          which still carry speed in their colour -- are the readable default. */}
      <Switch
        size="xs"
        mb="xs"
        label={tagged('Length follows magnitude', 'server')}
        checked={request.glyph_scale_by}
        onChange={(e) => setRequest({ glyph_scale_by: e.currentTarget.checked })}
        data-ctl="glyph-scale-by"
      />
      <LabelledSlider
        label="Count"
        min={20}
        max={3000}
        step={20}
        precision={0}
        value={request.glyph_count}
        onCommit={(v) => setRequest({ glyph_count: v })}
        data-ctl="glyph-count"
      />
      {/* Size is a vtkGlyph3D scale factor, so it rebuilds the arrows: a server
          control, and it commits on release rather than staying live as it did
          in the Trame app (where it was equally heavy but bound live). */}
      <LabelledSlider
        label="Size"
        min={0.1}
        max={5}
        step={0.1}
        precision={1}
        value={request.glyph_scale}
        onCommit={(v) => setRequest({ glyph_scale: v })}
        data-ctl="glyph-scale"
      />
      <ColourBy
        ctl="glyph"
        colored={style.colored ?? true}
        solid={style.solid}
        onChange={setStyle}
      />
    </>
  )
}

export function GeometryTool({ meta, request, setRequest, style, setStyle }) {
  if (!meta.hasGeometry) {
    return (
      <Text size="xs" c="dimmed">
        No building.obj (or building&lt;N&gt;.obj) in constant/triSurface.
      </Text>
    )
  }
  return (
    <>
      {/* Feature edges = sharp + boundary, the architectural outline.
          Wireframe = every edge. Both are flat lines; the mode is a filter
          parameter on one vtkFeatureEdges, never an input or actor swap. */}
      <SegmentedControl
        fullWidth
        size="xs"
        data={[
          { label: 'Feature edges', value: 'features' },
          { label: 'Wireframe', value: 'wireframe' },
        ]}
        value={request.geometry_mode}
        onChange={(v) => setRequest({ geometry_mode: v })}
        mb="sm"
        data-ctl="geometry-mode"
      />
      <LabelledSlider
        label="Opacity"
        min={0}
        max={1}
        step={0.05}
        live
        value={style.opacity ?? 1}
        onChange={(v) => setStyle({ opacity: v })}
        data-ctl="geometry-opacity"
      />
    </>
  )
}
