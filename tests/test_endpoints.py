"""/health and /api/config (openspec standalone-viewer, start-screen and viewer-build).

Both must be cheap: neither may scan a case root nor open a case. The app is
built over a source that fails the test if anything asks it for cases.
"""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from equa.cfd_viewer import __version__
from equa.cfd_viewer.server.app import make_app


class Tripwire:
    """A case source that must not be touched."""

    def describe(self):
        return "tripwire"

    def _touched(self, *_args):
        raise AssertionError("the endpoint scanned a case root")

    list = names = resolve = _touched


def _get(path, mode="server"):
    async def run():
        app = make_app(Tripwire(), mode=mode)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path)
            body = (await response.json() if response.content_type == "application/json"
                    else await response.text())
            return response.status, body, app["source"].case

    return asyncio.run(run())


def test_health():
    status, body, case = _get("/health")
    assert (status, body, case) == (200, "ok", None)


def test_config_in_server_mode():
    status, body, case = _get("/api/config")
    assert status == 200 and case is None
    assert body == {"mode": "server", "version": __version__,
                    "canAddFolders": False, "canUpload": False}


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        make_app(Tripwire(), mode="desktop")
