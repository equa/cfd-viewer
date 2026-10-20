"""Hatch build hook: ship the built client inside the wheel.

The server looks for the client at ``equa/cfd_viewer/web`` inside the installed
package (see ``client_dir`` in ``server/app.py``), so a wheel without it serves
only the "no client build" page. That is a broken release that would look
like a working install, so a regular wheel build refuses to run without
``web/dist`` -- build it first with ``npm ci && npm run build`` in ``web/``.

Editable installs (``uv sync``) are exempt: they run from the source tree, where
``web/dist`` is found directly. ``CFD_VIEWER_NO_CLIENT=1`` builds a wheel
without the client on purpose, e.g. for a quick server-only test.
"""

import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ClientHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "equa/cfd_viewer/web"
        elif os.environ.get("CFD_VIEWER_NO_CLIENT") != "1":
            raise RuntimeError(
                "web/dist/index.html is missing: build the client first "
                "(cd web && npm ci && npm run build), or set CFD_VIEWER_NO_CLIENT=1"
            )
