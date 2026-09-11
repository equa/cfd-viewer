# Pending improvements to the viz UX

> **Note (2026-09-08):** development moved to the React + three.js client
> (`server/` + `web/`); the Trame app is resting. Items below that were closed
> against the Trame UI stay closed — the UX was ported, not rebuilt. Where a
> control changed cost (bands and colour range are now instant; the two
> line-width sliders were dropped as provably inert under WebGL), see
> "What the debouncing became" in `CLAUDE.md`.

Claude: You may edit this file. Short comments on progress like "done" or "refused" for example.

Sometimes I (Niklas) want to extend on a topic that is marked as **done**. I will then keep
the original and add a **new request**, that you (Claude) should change to **done** or just remove
if we agree to discard the idea.

## General

**merge to CLAUDE.md** if not already there. — **done 2026-09-09**: now
`CLAUDE.md` → "Working agreements", and saved as a memory so it survives a fresh
session. Left here as the source of truth for edits.

When my requests require big changes, ask before. I might be asking for silly
things.

Always make sure to reuse existing functions, classes. I know how much easier it
is to write a new function instead of searching for existing implementations.
Better to generalize existing functions, as long as the argument list does not
grow (too much).

Prefer classes before passing arguments through several function calls and
before very long argument lists.

Always consider if it is possible to achieve a goal in local rendering
(three.js) and prefer it to server calls if it does not complicate code "too
much". Ask when needed.

Remember to be careful with API changes to the backend stuff, since it can serve
both trame and three.js ftb.

### Performance

## Widgets

- Mouse wheel disabled? I think I want mouse wheel on numeric inputs.
  — **done**. Mantine does not wire the wheel up, so `NumberField`
  (`web/src/ui/controls.jsx`) does, and every numeric field in the panels now
  uses it. It only responds **while the field has focus** — click it, then
  wheel. Deliberate: the panels scroll, so an unfocused wheel-over would
  silently edit whichever field was under the pointer. Say the word if you would
  rather it worked unfocused.
  Wiring it up exposed a second thing worth fixing: the steps were all 1, which
  is uselessly coarse on |U| (0..0.23) and far too coarse for a plane position
  the notes say needs sub-metre precision. Every numeric step is now **derived
  from the data range** (`stepFor`, ~1/100th snapped to 1/2/5): |U| → 0.002,
  T → 2, plane → 0.1, p → 1000. That fixes the spinner arrows too, not just the
  wheel.

## Actor colring

**regression** The Opacity by value stopped working recently.
— **fixed**. My doing, in the UX port: the ramp was multiplied by "colour by
field", and the shell ships *uncoloured* (neutral grey so the slice reads), so
the one surface you most want to see through ignored it. Colouring and the ramp
are independent questions; it is now gated only on the part actually having
scalars, which is the real precondition (without scalars a part would read 0 and
vanish). Covered by a check that uses the shell as it ships.

- Add color by field toggle to streamlines iso-surfaces and arrows. (Boundary
  has already). On iso, strls and arrows, on by default.
  — **done**, on by default for all three, and the boundary's bespoke switch was
  replaced by the same shared `ColourBy` control (which also carries the solid
  colour, below). Both halves are shader uniforms, so instant. This needed the
  line shader to gain `uColored`/`uFlat`, so streamlines can be solid too.

## Vector actor (Arrows)


## Iso surfaces

- Add base field name string (the field that it is generated from)
  — **done**. The panel names it ("Contouring **U**; values below are in that
  field's units") and the scene header carries `contourField`/`contourLocked`.
- **regression** or oportunity? The ISO surface has always follows the global
  field selection. Presently this behaves rather randomly. But this is sometimes
  exactly what I want. I want to be able to lock (and release) which field it is based on.
  — **done, both halves.**
  - The "random" part was a real bug: the isovalues were seeded **once** per
    case, so switching the colour field U→T left the isovalue at a |U| number,
    nowhere near the T range, and the surface came back empty or arbitrary. They
    now re-seed whenever the contoured field changes **identity** (never when
    you type a value).
  - **Lock field** pins the isosurface to a field of its own
    (`pipeline.contour_field`, baked into a separate `FoamVizContour` array).
    Locked, the colour field recolours the surface instead of moving it — so you
    get "speed isosurface, coloured by temperature", verified: same geometry
    (217 verts at |U|=0.115), T-valued scalars. Worth knowing: an isosurface
    coloured by its *own* field is a single flat colour, which is precisely why
    locking is useful.
  - Note **Rescale** re-seeds the isovalues when following (as the Trame app
    did) but leaves them alone when locked.

## Fields

## Streamlines

- Add line width parameter for line mode. Also to affect Comets
  — **needs your call, this is the one big item.** WebGL caps `lineWidth` at 1
  and always will; the only real fix is `Line2`/`LineMaterial` (three.js draws
  thick lines as camera-facing quads in a shader). That means a different
  geometry class (`LineSegmentsGeometry`), and — the expensive part — porting
  our custom LUT/bands/opacity/comet shader into a `LineMaterial` derivative,
  since Line2 brings its own. Roughly a day, and it touches the one shader that
  is now carrying four features. **Tubes already give you thick, lit,
  comet-animated lines today** for the price of a round trip. Want me to do
  Line2, or is tubes enough?
- Can the switch between tubes/lines be local to renderer? Presently strls are
  recreated on server I think, when tubes are turned on strls are re-generated
  (or added). Careful not do destroy the Comets though.
  — **done: tubes are now built in the browser.** You were right that the
  backend was regenerating them, and right that it did not need to. The server
  ships lines only; `web/src/viewer/tube.js` inflates them with a
  parallel-transport frame (what `vtkTubeFilter` does), in 4.3 ms for hotRoom
  and 28.5 ms for 120 k points.
  - It was costing far more than expected. On s2: **10.30 MB gzipped of tube
    geometry against 1.13 MB for the same streamlines as lines — 9x the wire —
    for no saving in server time**, since `vtkStreamTracer` dominates either way
    (3.4 s both). 19x raw on hotRoom.
  - Switching is now instant the *first* time, not just on a cache hit.
  - **The width became free too.** The builder emits centreline points with the
    ring's radial direction as the vertex normal, so the shader displaces by
    `normal * uTubeRadius` — dragging the width slider moves no vertices and
    rebuilds nothing (verified: identical vertex count before and after). So you
    do get a live thickness control for streamlines after all, just via tubes
    rather than `Line2`.
  - Comets survive: `travel` is replicated around each ring.
  - **Bug you spotted, fixed (2026-09-11):** a few tubes were closed by straight
    runs out to the cut-plane edge. Cause was the wire format, not the tube
    maths: it shipped only each polyline's *start* and the client inferred the
    length from the next start, which assumes the polylines tile the point array.
    They do not — `vtkStreamTracer` leaves orphan points between them (26 of
    18 219 here, after 6 of 94 lines), seeds it abandoned without integrating,
    and they sit on the cut plane because the seeds are masked points off the
    cutter. So the tube ran straight through them. Now the wire carries explicit
    `(start, count)` pairs, verified to match VTK exactly. Lines mode was never
    affected. A geometric check now guards it: the tube's longest step must
    equal the lines' longest step (measured identical to 17 significant digits).
  - Only Seeds and Max length still sit behind Apply, because only they re-run
    the tracer. `pipeline.stream_tube` stays for the Trame app, which still
    tubes server-side — the shared pipeline API was not touched.


## Color map and color range options

- Add a solid color selection and solid color selector
  — **done**, as the second half of the shared `ColourBy` control: turn "Colour
  by field" off on any part and a colour picker appears (with a swatch row).
  Per part rather than global, since that is what makes "grey shell, coloured
  slice" or "white streamlines over a coloured plane" possible. Instant.
- **regression** Robust range and true cell values does not work.
  — **both fixed, and they were broken for different reasons.**
  - **Robust range** changes only the *reported range*, no geometry, so it is
    deliberately in no part's `PART_INPUTS` — which meant it moved no cache
    signature, triggered no refetch, and silently did nothing. There is now a
    cheap `GET /api/range` endpoint, so it stays instant instead of re-extracting
    every visible part to deliver two floats. **Rescale** goes through it too, so
    it re-reads the data (and honours robust) rather than reusing the last
    header.
  - **True cell values** was worse: the server baked the cell array and
    `server/wire.py` only ever read *point* data, so the toggle could never have
    worked. Flat per-cell colour is impossible on an indexed mesh — neighbouring
    cells share vertices, so there is nowhere to put a per-cell value — so the
    wire now **de-indexes** when the toggle is on: 3 vertices per triangle, each
    carrying its own cell value. Verified flat (861 shared verts → 4800, every
    triangle single-valued). Costs 3x the vertex data, which is why it only
    happens when asked. Scoped to the shell and the slice, as in the Trame app.

## Boundary


## Streamlines


## Camera and scene persistence

**Deferred — but the plan below is now WRONG, and much easier than it says.**
It was written for Trame, and every hard part was a Trame problem:

- `server.state`, `window.trame.trigger`, `view_push_camera` and the
  `push_remote_camera_on_end_interaction` hazard no longer exist here.
- **The client owns everything.** Scene state (B) is `JSON.stringify` of the two
  state objects that already exist — `request` (server-affecting) and
  `appearance` (client-affecting) — which is a file-save and a file-load, not a
  whitelist. The split was designed for exactly this and the whitelist is
  already written down as those two objects.
- **Camera slots (A) stop being "the tricky half".** There is no client→server
  camera sync to reverse-engineer: `Viewer.camera_state()` already returns
  position/target/up (the browser test uses it), and setting them back is the
  same code path as the view-preset buttons. The F-key bridge is not needed.

So the open decisions collapse: (1) is moot — no bridge needed. (2) and (3) are
still real product questions, and only (2) matters much: in-session slots plus a
downloaded JSON, or scenes persisted server-side per case (a file in the case
dir) so they survive without a download? Say which and this is a small job now,
maybe half of what the note below assumes. Kept verbatim below as the record of
what was decided when.

**Original note (Trame era):** Two sub-features:

### B. Scene state export / load  (the easy, robust half — do first)
- `server.state` is a dict. Whitelist the viz vars (field/component/preset/
  n_colors/auto_range/robust_range/range_min/max/use_cell_data, plane_axis +
  plane_x/y/z, every `*_visible`, the per-tool settings incl. the new
  contour_value/min/max + glyph_source, lighting, ui_theme).
- Export = dump that subset to JSON (download). Load = read JSON → set the vars
  → `update_scene()`. Rides the existing change-handler path → low risk, no
  camera-sync involvement.

### A. Camera slots 1–4  (the tricky half)
- **Recall** is trivial & proven: set the server camera params + `view_push_camera`
  (same mechanism as the X/Y/Z/Iso view buttons).
- **Save** is the catch: in local (vtk.js) mode the *client* owns the camera,
  and `push_remote_camera_on_end_interaction` was removed (it caused the COR
  resets), so there is no live client→server camera sync now. To capture the
  current view, reuse the **F-key client→server bridge** (client JS reads the
  vtk.js camera → `window.trame.trigger` → a server handler stores it). Same
  pattern already working for F-key COR picking.
- If the 4 slots live in `state`, a scene export (B) bundles them for free.

### Open decisions (answer before implementing A)
1. Camera capture via the client-JS→trigger bridge (like F-key)? — the only
   reliable way to read the live client camera. (Recommended.)
2. Persistence model: cameras 1–4 as in-session quick-save buttons + export/load
   as a downloaded JSON bundling cameras + all settings? OR persist saved scenes
   server-side per case (a file in the case dir) so they survive without a
   download?
3. Scope of "scene state": just viz settings (+cameras), or also the selected
   case/time step?

Recommendation when resumed: build B first (safe), then A once (1)/(2) decided.
