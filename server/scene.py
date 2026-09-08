"""Scene extraction: VTK on the server, triangles on the wire.

One open case plus the shared :class:`~foamviz.pipeline.FoamPipeline` filter
graph, driven by a flat query and packed by :mod:`server.wire`. The browser
draws the result with three.js and knows no CFD (see ``web/``).

The unit of work is a **part** -- ``boundary``, ``slice``, ``iso``, ``stream``,
``glyph``, ``geometry``. A request names the parts it wants and the server
extracts only those, so moving the cut plane costs a slice (tens of kB) and not
the boundary surface (15 MB on ``s2``). The client owns the decision, because it
is the side that knows what it still has cached: each part has a *signature*
built from exactly the inputs that change its geometry (:data:`PART_INPUTS`),
and the client re-requests a part only when its signature moves. That is the one
thing this path can do that the Trame path structurally cannot -- there, every
control re-executes the whole filter graph.

Appearance is not here. Colour map, colour range, bands, per-part visibility and
opacity are uniforms in the client's shader, so they never reach this module;
what the server owns is what has to be re-*extracted*. ``PART_INPUTS`` is the
written-down form of that boundary.
"""

import logging
import time
from pathlib import Path

from foamviz import colors
from foamviz.case import FIELD_UNITS, FoamCase, find_cases
from foamviz.pipeline import COLOR_ARRAY, FoamPipeline

from . import wire

log = logging.getLogger("cfdviewer.scene")

# The parts a scene can be made of, in draw order. Each maps to a builder method
# ``_part_<key>`` below and to a tool in the client's side pane.
PART_KEYS = ("boundary", "slice", "iso", "stream", "glyph", "geometry")

# Which query inputs actually change each part's geometry -- the client hashes
# these to decide whether its cached copy is still valid, so a control missing
# from a list is a control that will show a stale part, and a spurious entry is
# a needless refetch. Shipped to the client in /api/meta so the two cannot drift.
#
# `case` and `time_index` are implicit in every part; `field`/`component` are in
# every scalar-coloured part because the colour scalars are baked per vertex
# (that is the one appearance setting the client cannot own).
#
# An entry may be CONDITIONAL: ``{"when": {key: value}, "keys": [...]}`` counts
# those keys only while the condition holds. Two dependencies really are
# conditional, and getting them wrong is expensive in both directions:
#
#   * the boundary depends on the cut plane ONLY when it is being clipped by it.
#     Declared unconditionally, every nudge of the plane slider re-extracts and
#     re-ships the boundary surface -- 15 of s2's 16.2 MB -- to draw a slice that
#     is a few tens of kB. That is most of the win of per-part fetching, thrown
#     away by one list entry.
#   * arrows seed from either the plane grid or the isosurface, never both, so
#     each source's inputs are dead while the other is selected.
PART_INPUTS = {
    "boundary": [
        "patches", "field", "component", "cell_data", "surface_clip",
        {"when": {"surface_clip": "1"}, "keys": ["plane_axis", "plane_coord"]},
    ],
    "slice": ["field", "component", "cell_data", "slice_edges",
              "plane_axis", "plane_coord"],
    "iso": ["field", "component", "contour_count", "contour_value",
            "contour_min", "contour_max"],
    # The stream seeds are masked points off the cutter output, so the plane
    # always matters here -- no condition.
    "stream": ["vector_field", "stream_seeds", "stream_length", "stream_tubes",
               "stream_radius", "plane_axis", "plane_coord", "field", "component"],
    "glyph": [
        "vector_field", "glyph_source", "glyph_count", "glyph_scale",
        "glyph_scale_by", "field", "component",
        {"when": {"glyph_source": "slice"}, "keys": ["plane_axis", "plane_coord"]},
        {"when": {"glyph_source": "isosurface"},
         "keys": ["contour_count", "contour_value", "contour_min", "contour_max"]},
    ],
    # Static per case: the building OBJ never varies with time or field.
    "geometry": ["geometry_mode"],
}


def _flag(query, name, default=False):
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    return raw not in ("0", "false", "False")


def _num(query, name, default):
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _contour_values(count, value, lo, hi):
    """Isovalues for the current controls -- the same rule as the Trame app's
    ``_contour_values``: one surface sits at its own value; 3 or 5 spread evenly
    *inside* [lo, hi] on interior fractions, so none lands on the range extreme
    where an isosurface tends to come back empty."""
    n = max(int(count), 1)
    if n <= 1:
        return [float(value)]
    return [lo + (hi - lo) * (i + 1) / (n + 1) for i in range(n)]


class SceneSource:
    """One open case and the FoamViz filter graph, reused across requests.

    Not re-entrant: the filter graph is shared mutable state, so the aiohttp
    layer serialises calls behind a lock and runs them off the event loop (see
    :mod:`server.app`).
    """

    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self.pipeline = FoamPipeline()
        self.case = None

    # -- case handling ----------------------------------------------------

    def case_names(self):
        return [p.name for p in find_cases(self.data_root)]

    def open(self, name):
        """Open *name*, or keep the current case if it is already the one asked
        for. Returns the case; raises KeyError if there is no such case."""
        if self.case is not None and self.case.name == name:
            return self.case
        paths = {p.name: p for p in find_cases(self.data_root)}
        if name not in paths:
            raise KeyError(name)
        # Same ordering as FoamViz.load_case: let the old case go before the new
        # one is read, or peak RSS is both cases at once (see CLAUDE.md
        # "Memory management").
        self.case = None
        self.pipeline.release_case()
        self.case = FoamCase(paths[name])
        self.case.load(self.case.times[-1])
        self.pipeline.set_case(self.case)
        self.pipeline.update_data()
        log.info("opened %s: %d cells", name, self.case.n_cells())
        return self.case

    def meta(self):
        """Everything the client needs to build its UI for this case."""
        c, p = self.case, self.pipeline
        b = list(c.bounds())
        return {
            "case": c.name,
            "cases": self.case_names(),
            "cells": c.n_cells(),
            "points": c.n_points(),
            "times": c.times,
            "patches": c.patches,
            "bounds": b,
            # Per-axis world range for the cut-plane slider and the numeric
            # X/Y/Z fields -- the plane's position is a world coordinate, never
            # a fraction (the Trame app learned that the hard way).
            "axisRange": {a: [b[2 * i], b[2 * i + 1]] for i, a in enumerate("xyz")},
            "centre": [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2],
            "decomposed": c.decomposed,
            "scalarFields": c.scalar_fields,
            "vectorFields": c.vector_fields,
            "fields": dict(c.fields),
            "units": FIELD_UNITS,
            "colorField": p.color_field,
            "vectorField": p.vector_field,
            "hasGeometry": p.has_geometry,
            "presets": colors.PRESET_NAMES,
            "partInputs": PART_INPUTS,
        }

    def refresh_times(self):
        """Re-scan for time steps written since the case was opened (a running
        solve keeps adding them). Returns the new list."""
        self.case.refresh_times()
        return self.case.times

    # -- extraction -------------------------------------------------------

    def scene(self, query):
        """Extract the requested parts and return the packed wire payload."""
        t0 = time.perf_counter()
        case, pipe = self.case, self.pipeline

        # --- time step ---------------------------------------------------
        idx = int(_num(query, "time_index", len(case.times) - 1))
        idx = max(0, min(idx, len(case.times) - 1))

        # --- patches (reading fewer patches is the cheapest way to shrink the
        # boundary, which dominates the wire) -----------------------------
        raw_patches = query.get("patches")
        patches = [p for p in (raw_patches or "").split(",") if p] or None
        if case.load(case.times[idx], patches=patches, force=False):
            pipe.update_data()

        # --- colour scalars ----------------------------------------------
        field = query.get("field") or pipe.color_field
        component = query.get("component") or "magnitude"
        if not field or case.fields.get(field) is None:
            raise ValueError(f"no field {field!r} in {case.name}")
        pipe.color_field = field
        pipe.color_component = component
        pipe.use_cell_data = _flag(query, "cell_data")
        pipe.apply_color_array()
        lo, hi = case.field_range(field, component, robust=_flag(query, "robust"))
        if hi - lo < 1e-12:  # a uniform field still needs a drawable range
            lo, hi = lo - 0.5, hi + 0.5

        vector_field = query.get("vector_field") or pipe.vector_field
        if case.vector_field_available(vector_field):
            pipe.vector_field = vector_field
            case.internal.GetPointData().SetActiveVectors(vector_field)

        # --- the shared cut plane ----------------------------------------
        # A world coordinate, not a fraction: the plane's position is the same
        # number the numeric X/Y/Z fields show, so nothing has to convert.
        axis = query.get("plane_axis") or "z"
        b = case.bounds()
        ai = "xyz".index(axis)
        centre = (b[2 * ai] + b[2 * ai + 1]) / 2
        coord = _num(query, "plane_coord", centre)
        pipe.update_plane(axis, coord)

        # --- isovalues (also needed by the "on isosurface" glyph source, even
        # when the isosurface itself is not drawn) ------------------------
        values = _contour_values(
            _num(query, "contour_count", 1),
            _num(query, "contour_value", (lo + hi) / 2),
            _num(query, "contour_min", lo),
            _num(query, "contour_max", hi),
        )
        pipe.update_contour(True, values, 1.0)

        wanted = [k for k in PART_KEYS if k in set((query.get("parts") or "").split(","))]
        ctx = dict(query=query, axis=axis, coord=coord, lo=lo, hi=hi,
                   vector_field=vector_field)

        timings, parts = {}, []
        for key in wanted:
            t = time.perf_counter()
            part = getattr(self, f"_part_{key}")(ctx)
            timings[key] = round((time.perf_counter() - t) * 1000, 1)
            if part is not None:
                parts.append(part)

        meta = {
            "case": case.name,
            "time": case.times[idx],
            "timeIndex": idx,
            "times": case.times,
            "field": field,
            "component": component,
            "unit": FIELD_UNITS.get(field, ""),
            "range": [lo, hi],
            "bounds": list(case.bounds()),
            "planeAxis": axis,
            "planeCoord": pipe.cutter.GetCutFunction().GetOrigin()[ai],
            "contourValues": values,
            "requested": wanted,
            "returned": [p.name for p in parts],
            "serverMs": {**timings, "total": round((time.perf_counter() - t0) * 1000, 1)},
        }
        blob = wire.pack(parts, meta)
        log.info("scene %s: %s -> %.1f kB, %s ms", case.name,
                 ",".join(wanted) or "-", len(blob) / 1024, meta["serverMs"]["total"])
        return blob

    # -- part builders ----------------------------------------------------
    #
    # Each pulls one branch of the filter graph and hands the polydata to
    # server.wire. They only ever *read* appearance-free geometry: opacity,
    # colour map and visibility are the client's business.

    def _part_boundary(self, ctx):
        """The room shell. 'Cut away at plane' clips it against the same plane
        the slice uses, which is how you see inside without transparency."""
        pipe = self.pipeline
        clip = _flag(ctx["query"], "surface_clip")
        source = pipe.surface_clip if clip else pipe.surface_input
        source.Update()
        return wire.surface_part("boundary", source.GetOutput(), COLOR_ARRAY)

    def _part_slice(self, ctx):
        """The cut plane. With the mesh on it becomes a *crinkle* slice -- the
        whole cells the plane passes through, i.e. the true mesh layer, rather
        than the flat triangulated cut."""
        pipe = self.pipeline
        if _flag(ctx["query"], "slice_edges"):
            pipe.crinkle_surface.Update()
            poly = pipe.crinkle_surface.GetOutput()
        else:
            pipe.cutter.Update()
            poly = pipe.cutter.GetOutput()
        return wire.surface_part("slice", poly, COLOR_ARRAY)

    def _part_iso(self, ctx):
        # Isovalues were set in scene(); contour_normals gives us normals for
        # free, so wire.surface_part skips its own normals pass.
        self.pipeline.contour_normals.Update()
        return wire.surface_part("iso", self.pipeline.contour_normals.GetOutput(), COLOR_ARRAY)

    def _part_stream(self, ctx):
        """Streamlines, as lines or as tubes.

        This is the expensive one -- ``vtkStreamTracer`` is 2.6 s of the 3.1 s
        server time on ``s2`` -- and it costs exactly the same in the Trame path.
        It is why the streamline panel keeps an Apply button.
        """
        pipe = self.pipeline
        q = ctx["query"]
        tubes = _flag(q, "stream_tubes")
        pipe.update_streamlines(
            True,
            int(_num(q, "stream_seeds", 200)),
            _num(q, "stream_radius", 1.0),
            _num(q, "stream_length", 1.0),
            tubes,
            1,
        )
        if tubes:
            # Tubes are real triangles, so they ship down the surface path and
            # arrive lit -- the WebGL line-width cap (1 px, which made the Trame
            # line-width slider inert) is exactly what tubes exist to escape.
            pipe.stream_tube.Update()
            return wire.surface_part("stream", pipe.stream_tube.GetOutput(), COLOR_ARRAY)
        pipe.tracer.Update()
        return wire.line_part("stream", pipe.tracer.GetOutput(), COLOR_ARRAY)

    def _part_glyph(self, ctx):
        """Arrows on the cut plane or on the isosurface. ``vtkGlyph3D`` emits
        polydata, so they need no special handling -- they are triangles like
        any other part."""
        pipe = self.pipeline
        q = ctx["query"]
        pipe.update_glyphs(
            True,
            q.get("glyph_source") or "plane",
            int(_num(q, "glyph_count", 400)),
            _num(q, "glyph_scale", 1.0),
            _flag(q, "glyph_scale_by"),
            ctx["axis"],
            ctx["coord"],
        )
        pipe.glyph.Update()
        return wire.surface_part("glyph", pipe.glyph.GetOutput(), COLOR_ARRAY)

    def _part_geometry(self, ctx):
        """Building geometry from ``constant/triSurface/building*.obj`` as flat
        lines: feature edges (sharp + boundary, the architectural outline) or
        wireframe (every edge). Carries no field, so it ships without scalars
        and the client draws it in a neutral colour."""
        pipe = self.pipeline
        if not pipe.has_geometry:
            return None
        mode = ctx["query"].get("geometry_mode") or "features"
        # Mode is a *filter parameter*, never an input or actor swap -- see the
        # geometry note in CLAUDE.md for the three ways that went wrong.
        pipe.update_geometry(True, mode, 1.0, 1.0)
        pipe.geometry_edges.Update()
        return wire.line_part("geometry", pipe.geometry_edges.GetOutput(), COLOR_ARRAY)
