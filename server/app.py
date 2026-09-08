"""HTTP layer: the scene endpoints plus the built client.

Deliberately thin. Everything CFD is in :mod:`server.scene`; everything visual
is in the browser. What this module adds is the concurrency discipline the spike
did not have:

* extraction runs in a **worker thread** (``asyncio.to_thread``), so a 3-second
  ``vtkStreamTracer`` no longer freezes the event loop and the server keeps
  answering ``/api/meta`` and static requests while it works;
* a single **lock** serialises access to the filter graph, which is shared
  mutable state -- two concurrent extractions on one ``FoamPipeline`` would
  interleave ``update_plane`` calls and hand each other the wrong geometry.

That is one case per process, still, as in the Trame app: fine behind the
per-user cfd-stack deployment, and the thing to revisit if the viewer is ever
shared by several users at once.
"""

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from foamviz import colors

from .scene import SceneSource

log = logging.getLogger("cfdviewer")

ROOT = Path(__file__).resolve().parent.parent
# The Vite build output. Absent in development, where `npm run dev` serves the
# client on its own port and proxies /api here (see web/vite.config.js).
DIST = ROOT / "web" / "dist"

_DEV_HINT = """<!doctype html><meta charset="utf-8">
<title>cfd-viewer</title>
<body style="font:14px system-ui;padding:2rem;max-width:34rem">
<h3>No client build</h3>
<p><code>web/dist</code> does not exist, so there is nothing to serve here.</p>
<p>For development run the Vite dev server, which proxies <code>/api</code> to
this process:</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run dev</pre>
<p>To serve the client from this process instead, build it once:</p>
<pre>cd web &amp;&amp; npm run build</pre>
</body>"""


def make_app(data_root):
    source = SceneSource(data_root)
    # The filter graph is shared mutable state; one extraction at a time.
    lock = asyncio.Lock()

    async def in_thread(fn, *args):
        """Run a blocking VTK call off the event loop, one at a time."""
        async with lock:
            return await asyncio.to_thread(fn, *args)

    async def open_case(request):
        """Resolve ?case= (falling back to the first case) and open it."""
        name = request.query.get("case") or ""
        names = source.case_names()
        if not names:
            raise web.HTTPNotFound(text="no OpenFOAM case under the data root")
        if name not in names:
            name = names[0]
        try:
            await in_thread(source.open, name)
        except KeyError:
            raise web.HTTPNotFound(text=f"no case {name!r}")

    async def cases(_request):
        return web.json_response({"cases": source.case_names()})

    async def meta(request):
        await open_case(request)
        return web.json_response(await in_thread(source.meta))

    async def times(request):
        """Re-scan the case for time steps written since it was opened."""
        await open_case(request)
        return web.json_response({"times": await in_thread(source.refresh_times)})

    async def scene(request):
        await open_case(request)
        try:
            blob = await in_thread(source.scene, request.query)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        response = web.Response(body=blob, content_type="application/octet-stream")
        # Worth having on by default: the payload is float32 positions, repeated
        # normals and locally-coherent indices, which gzip takes to ~24% of raw
        # (16.2 -> 3.9 MB on s2; see tests/bench.py). Without this the wire size
        # looks far worse than the format deserves.
        response.enable_compression()
        return response

    async def lut(request):
        """The 256-entry colour table as 768 raw bytes.

        Sampled from the same matplotlib data the VTK transfer function uses, so
        the shader and a server-side VTK render agree on colour exactly rather
        than approximately. Cached hard on the client: a preset switch is a
        texture upload, never a geometry request.
        """
        name = request.query.get("name", "coolwarm")
        if name not in colors.PRESET_NAMES:
            raise web.HTTPBadRequest(text=f"no preset {name!r}")
        return web.Response(
            body=colors.rgb_table(name),
            content_type="application/octet-stream",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def index(_request):
        page = DIST / "index.html"
        if not page.is_file():
            return web.Response(text=_DEV_HINT, content_type="text/html")
        # No-store on the shell only: it names hashed asset files, so a cached
        # copy after a redeploy points at assets that no longer exist.
        return web.FileResponse(page, headers={"Cache-Control": "no-store"})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/cases", cases)
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/times", times)
    app.router.add_get("/api/scene", scene)
    app.router.add_get("/api/lut", lut)
    if (DIST / "assets").is_dir():
        # Vite fingerprints these, so they can be cached forever.
        app.router.add_static("/assets/", DIST / "assets")
    app["source"] = source
    return app


def serve(data_root, host="0.0.0.0", port=5003):
    log.info("cfd-viewer serving %s on http://%s:%d/", data_root, host, port)
    web.run_app(make_app(data_root), host=host, port=port, print=None)
