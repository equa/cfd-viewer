"""/health and /api/config (openspec standalone-viewer, start-screen and viewer-build).

Both must be cheap: neither may scan a case root nor open a case. The app is
built over a source that fails the test if anything asks it for cases.

Also the platform's way of mounting a service (equa-deployment): served under
``ROOT_PATH`` without the edge stripping it, and configured from the
environment.
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


def _get(path, mode="server", root_path=""):
    async def run():
        app = make_app(Tripwire(), mode=mode, root_path=root_path)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path, allow_redirects=False)
            body = (await response.json() if response.content_type == "application/json"
                    else await response.text())
            if response.status in (301, 302, 307, 308):
                body = response.headers["Location"]
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


@pytest.mark.parametrize("root_path", ["/viewer", "/viewer/", "viewer"])
def test_routes_answer_under_the_root_path(root_path):
    # What Traefik forwards: the prefix is not stripped.
    assert _get("/viewer/health", root_path=root_path)[:2] == (200, "ok")
    assert _get("/viewer/api/config", root_path=root_path)[0] == 200
    assert _get("/viewer/", root_path=root_path)[0] == 200


def test_routes_still_answer_at_the_root():
    # The image's HEALTHCHECK asks 127.0.0.1:5003/health whatever the prefix.
    assert _get("/health", root_path="/viewer")[:2] == (200, "ok")


def test_the_bare_root_path_redirects_to_its_slash():
    # The client's URLs are relative: at /viewer its assets would miss.
    status, location, _case = _get("/viewer?case=x", root_path="/viewer")
    assert (status, location) == (308, "/viewer/?case=x")


def test_no_root_path_adds_no_prefixed_routes():
    assert _get("/viewer/health")[0] == 404


def _parsed(monkeypatch, argv, env):
    """What main() would serve, with *env* as the environment; starts nothing."""
    from equa.cfd_viewer import __main__ as cli
    from equa.cfd_viewer.server import app

    for name in ("CASE_ROOTS", "SCAN_DEPTH", "PORT", "ROOT_PATH"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    served = {}
    monkeypatch.setattr(app, "serve", lambda source, **kw: served.update(source=source, **kw))
    cli.main(argv)
    return served


def test_settings_come_from_the_environment(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    served = _parsed(monkeypatch, [], {
        "CASE_ROOTS": f"{a}:{b}", "SCAN_DEPTH": "2", "PORT": "8000", "ROOT_PATH": "/viewer"})
    assert served["port"] == 8000 and served["root_path"] == "/viewer"
    assert served["source"].depth == 2
    assert [str(r) for r in served["source"].roots] == [str(a), str(b)]


def test_the_command_line_wins_over_the_environment(monkeypatch, tmp_path):
    served = _parsed(monkeypatch, ["--data", str(tmp_path), "--port", "5003", "--root-path", ""],
                     {"CASE_ROOTS": "/nowhere", "PORT": "8000", "ROOT_PATH": "/viewer"})
    assert served["port"] == 5003 and served["root_path"] == ""
    assert [str(r) for r in served["source"].roots] == [str(tmp_path)]


def test_a_bad_number_in_the_environment_stops_startup(monkeypatch):
    with pytest.raises(SystemExit, match="PORT"):
        _parsed(monkeypatch, [], {"PORT": "eighty"})
