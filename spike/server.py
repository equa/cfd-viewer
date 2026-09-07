#!/usr/bin/env python3
"""Spike: the SimScale split -- VTK extracts server-side, three.js draws.

    python spike/server.py --data data --port 8090

What this is testing, and nothing more: whether the CFD work can stay in the
existing Python/VTK pipeline while the browser side becomes ordinary React +
three.js. So it deliberately reuses `foamviz.case` and `foamviz.pipeline`
untouched -- the same reader, the same cutter/contour/tracer, the same baked
COLOR_ARRAY and the same colour-map tables the Trame app uses. If a filter needs
changing to serve three.js, that is a finding, not a fix.

Not production: one shared case per process (like FoamViz), extraction runs on
the event loop, and no decimation. See spike/README.md.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import wire  # noqa: E402  (spike-local)
from foamviz import colors  # noqa: E402
from foamviz.case import FoamCase, find_cases  # noqa: E402
from foamviz.pipeline import COLOR_ARRAY, FoamPipeline  # noqa: E402

log = logging.getLogger("spike")
WEB = Path(__file__).resolve().parent / "web"


class SceneSource:
    """One open case plus the FoamViz filter graph, reused across requests."""

    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self.pipeline = FoamPipeline()
        self.case = None

    def case_names(self):
        return [p.name for p in find_cases(self.data_root)]

    def open(self, name):
        if self.case is not None and self.case.name == name:
            return
        paths = {p.name: p for p in find_cases(self.data_root)}
        if name not in paths:
            raise web.HTTPNotFound(text=f"no case {name!r}")
        # Same ordering as FoamViz.load_case: let the old case go first.
        self.case = None
        self.pipeline.release_case()
        self.case = FoamCase(paths[name])
        self.case.load(self.case.times[-1])
        self.pipeline.set_case(self.case)
        self.pipeline.update_data()
        log.info("opened %s: %d cells", name, self.case.n_cells())

    def meta(self):
        c, p = self.case, self.pipeline
        return {
            "case": c.name,
            "cells": c.n_cells(),
            "points": c.n_points(),
            "times": c.times,
            "patches": c.patches,
            "bounds": list(c.bounds()),
            "decomposed": c.decomposed,
            "scalarFields": c.scalar_fields,
            "vectorFields": c.vector_fields,
            "fields": {n: comps for n, comps in c.fields.items()},
            "colorField": p.color_field,
            "presets": colors.PRESET_NAMES,
        }

    # -- the actual extraction -------------------------------------------

    def scene(self, q):
        """Build the requested parts and return the packed payload."""
        t0 = time.perf_counter()
        case, pipe = self.case, self.pipeline

        idx = max(0, min(int(q.get("time_index", len(case.times) - 1)), len(case.times) - 1))
        if case.load(case.times[idx], force=False):
            pipe.update_data()

        field = q.get("field") or pipe.color_field
        component = q.get("component", "magnitude")
        if case.fields.get(field) is None:
            raise web.HTTPBadRequest(text=f"no field {field!r}")
        pipe.color_field, pipe.color_component = field, component
        pipe.apply_color_array()
        lo, hi = case.field_range(field, component, robust=q.get("robust") == "1")

        wanted = set((q.get("parts") or "boundary,slice").split(","))
        axis = q.get("slice_axis", "z")
        frac = float(q.get("slice_frac", 0.5))
        b = case.bounds()
        ai = "xyz".index(axis)
        coord = b[2 * ai] + (b[2 * ai + 1] - b[2 * ai]) * frac
        pipe.update_plane(axis, coord)

        timings, parts = {}, []

        def timed(name, build):
            t = time.perf_counter()
            part = build()
            timings[name] = round((time.perf_counter() - t) * 1000, 1)
            if part is not None:
                parts.append(part)

        if "boundary" in wanted:
            def boundary():
                pipe.surface_input.Update()
                return wire.surface_part("boundary", pipe.surface_input.GetOutput(), COLOR_ARRAY)
            timed("boundary", boundary)

        if "slice" in wanted:
            def cut():
                pipe.cutter.Update()
                return wire.surface_part("slice", pipe.cutter.GetOutput(), COLOR_ARRAY)
            timed("slice", cut)

        if "iso" in wanted:
            def iso():
                iso_frac = float(q.get("iso_frac", 0.5))
                pipe.update_contour(True, [lo + (hi - lo) * iso_frac], 1.0)
                pipe.contour_normals.Update()
                return wire.surface_part("iso", pipe.contour_normals.GetOutput(), COLOR_ARRAY)
            timed("iso", iso)

        if "streamlines" in wanted:
            def streams():
                pipe.update_streamlines(
                    True, int(q.get("seeds", 200)), 1.0,
                    float(q.get("stream_length", 1.0)), False, 1,
                )
                pipe.tracer.Update()
                return wire.line_part("streamlines", pipe.tracer.GetOutput(), COLOR_ARRAY)
            timed("streamlines", streams)

        meta = {
            "case": case.name,
            "time": case.times[idx],
            "timeIndex": idx,
            "field": field,
            "component": component,
            "range": [lo, hi],
            "bounds": list(case.bounds()),
            "serverMs": {**timings, "total": round((time.perf_counter() - t0) * 1000, 1)},
        }
        blob = wire.pack(parts, meta)
        log.info("scene %s: %d part(s), %.1f kB, %s ms",
                 case.name, len(parts), len(blob) / 1024, meta["serverMs"]["total"])
        return blob


def make_app(data_root):
    source = SceneSource(data_root)
    app = web.Application()

    async def cases(_request):
        return web.json_response({"cases": source.case_names()})

    async def meta(request):
        source.open(request.query.get("case") or (source.case_names() or [""])[0])
        return web.json_response(source.meta())

    async def scene(request):
        source.open(request.query.get("case") or (source.case_names() or [""])[0])
        blob = source.scene(request.query)
        response = web.Response(body=blob, content_type="application/octet-stream")
        # Worth having on by default: the payload is float32 positions, repeated
        # normals and locally-coherent indices, which gzip takes to ~24% (see
        # spike/bench.py). Without this the wire size looks far worse than the
        # architecture deserves.
        response.enable_compression()
        return response

    async def lut(request):
        name = request.query.get("name", "coolwarm")
        if name not in colors.PRESET_NAMES:
            raise web.HTTPBadRequest(text=f"no preset {name!r}")
        return web.Response(body=colors.rgb_table(name), content_type="application/octet-stream")

    async def index(_request):
        return web.FileResponse(WEB / "index.html")

    app.router.add_get("/", index)
    app.router.add_get("/api/cases", cases)
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/scene", scene)
    app.router.add_get("/api/lut", lut)
    app.router.add_static("/web/", WEB)
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log.info("serving %s on http://%s:%d/", args.data, args.host, args.port)
    web.run_app(make_app(args.data), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
