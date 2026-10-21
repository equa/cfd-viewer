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
import os
from pathlib import Path

from aiohttp import web

from .. import __version__
from ..foamviz import colors
from .scene import SceneSource
from .sources import AmbiguousCase, CaseNotFound

log = logging.getLogger("cfdviewer")

PACKAGE = Path(__file__).resolve().parent.parent
# src/equa/cfd_viewer -> the checkout root, when running from a source tree.
CHECKOUT = PACKAGE.parent.parent.parent


def client_dir():
    """Where the built client is, first match wins:

    1. ``WEB_DIST_DIR``, as the equa service images set it;
    2. the client packaged into the wheel (``equa/cfd_viewer/web``);
    3. ``web/dist`` in a source checkout -- what cfd-backend's cfd-viz image
       serves, since it builds the client in place in a clone of this repo.

    None of them may exist in development, where `npm run dev` serves the
    client on its own port and proxies /api here (see web/vite.config.js); the
    index route then shows a hint page instead.
    """
    candidates = [PACKAGE / "web", CHECKOUT / "web" / "dist"]
    if os.environ.get("WEB_DIST_DIR"):
        candidates.insert(0, Path(os.environ["WEB_DIST_DIR"]))
    for path in candidates:
        if (path / "index.html").is_file():
            return path
    return candidates[-1]


DIST = client_dir()

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


# What the server can do, per run mode. Phase 1 has only ``server`` (the
# cockpit's cfd-viz); ``local`` and ``hosted`` add "Add folder" and "Upload
# case" to the start screen through the same flags (openspec standalone-viewer,
# design D4).
MODES = {
    "server": {"canAddFolders": False, "canUpload": False},
}


def make_app(source, mode="server"):
    """The aiohttp app over a case source (``sources.DirectorySource``)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    scene_source = SceneSource(source)
    # The filter graph is shared mutable state; one extraction at a time.
    lock = asyncio.Lock()

    async def in_thread(fn, *args):
        """Run a blocking VTK call off the event loop, one at a time."""
        async with lock:
            return await asyncio.to_thread(fn, *args)

    async def open_case(request):
        """Open the case ?case= denotes: an id, or a name exactly one case has.

        Anything else is a 404 with the reason. There is deliberately no
        fallback to the first case any more: showing a different case than the
        one asked for is worse than saying so (openspec standalone-viewer).
        """
        key = request.query.get("case") or ""
        if not key:
            raise web.HTTPNotFound(text="no case given: pass ?case=<id or name>")
        try:
            await in_thread(scene_source.open, key)
        except AmbiguousCase as exc:
            raise web.HTTPNotFound(
                text=f"case name {key!r} is ambiguous; use one of the ids "
                     f"{', '.join(exc.args[1])}")
        except CaseNotFound:
            raise web.HTTPNotFound(text=f"no case {key!r}")

    async def health(_request):
        """Up and accepting requests. Touches no case root, opens no case."""
        return web.Response(text="ok")

    async def config(_request):
        """What this server is and can do; the client's start screen reads it.
        Cheap by design: no case root is scanned and no case is opened."""
        return web.json_response({"mode": mode, "version": __version__, **MODES[mode]})

    async def cases(_request):
        """``cases`` (names) as it always was, plus ``entries`` with the rest."""
        refs = await asyncio.to_thread(source.list)
        return web.json_response({"cases": [r.name for r in refs],
                                  "entries": [r.to_json() for r in refs]})

    async def meta(request):
        await open_case(request)
        return web.json_response(await in_thread(scene_source.meta))

    async def times(request):
        """Re-scan the case for time steps written since it was opened."""
        await open_case(request)
        return web.json_response({"times": await in_thread(scene_source.refresh_times)})

    async def field_range(request):
        """The data range for one field/component. Cheap by design -- see
        SceneSource.field_range for why this is not part of /api/scene."""
        await open_case(request)
        try:
            values = await in_thread(
                scene_source.field_range,
                request.query.get("field"),
                request.query.get("component"),
                request.query.get("robust") not in (None, "", "0", "false"),
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"range": values})

    async def scene(request):
        await open_case(request)
        try:
            blob = await in_thread(scene_source.scene, request.query)
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
    app.router.add_get("/health", health)
    app.router.add_get("/api/config", config)
    app.router.add_get("/api/cases", cases)
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/times", times)
    app.router.add_get("/api/range", field_range)
    app.router.add_get("/api/scene", scene)
    app.router.add_get("/api/lut", lut)
    if (DIST / "assets").is_dir():
        # Vite fingerprints these, so they can be cached forever.
        app.router.add_static("/assets/", DIST / "assets")
    app["source"] = scene_source
    return app


def serve(source, host="0.0.0.0", port=5003, mode="server"):
    log.info("cfd-viewer %s (%s mode) serving %s on http://%s:%d/",
             __version__, mode, source.describe(), host, port)
    web.run_app(make_app(source, mode=mode), host=host, port=port, print=None)
