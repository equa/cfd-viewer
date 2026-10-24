#!/usr/bin/env python3
"""The cockpit contract: cfd-backend's cfd-viz build and runtime, mirrored here.

    python tests/check_cockpit_contract.py                  # HEAD of this checkout
    python tests/check_cockpit_contract.py --repo /tmp/x    # a throwaway clone, to prove a failure
    python tests/check_cockpit_contract.py --runtime-only --base-url http://localhost:8080/viz/

cfd-backend builds its ``cfd-viz`` image from a clone of a release tag of
this repository (its ``Containerfile``, stages ``viz-build`` and ``cfd-viz``) and
smoke-tests it with its own ``tests/smoke/viz.sh``. Until it builds from this
repository's Dockerfile instead, every commit here must keep that build and
that runtime working (openspec standalone-viewer, viewer-build spec "The
cockpit contract holds", design D11 layer 3). This script does what they do,
in the same order, without a container:

build half
  1. ``git clone`` the commit -- so a file that exists only in the working
     tree, uncommitted, is missing exactly as it would be in the image;
  2. check the four files the Containerfile requires;
  3. ``npm ci && npm run build`` in ``web/``, then drop ``node_modules`` and
     ``.git`` as the image does;
  4. a fresh Python 3.14 venv and ``pip install -r requirements.txt``;
  5. cfd-backend's own ``viz.sh``, with the clone standing in for
     ``/opt/foamviz``: from a cfd-backend checkout when there is one, else
     the copy kept in ``tests/cockpit/`` (cfd-backend is private, so CI cannot
     clone it).

runtime half
  6. the image's exact CMD on a ``CFD_HOME`` holding the demo case under a
     UUID directory name;
  7. a proxy that strips ``/viz/`` the way the cockpit's nginx ``rewrite``
     does;
  8. Chromium through ``/viz/?case=<uuid>&theme=light``, the cockpit iframe;
  9. a case written after start-up is listed; an empty ``CFD_HOME`` start
     stays up.

Both halves run with no GL library on the library path, as the image has
none. ``--runtime-only --base-url`` runs step 8 and the API checks against a
real stack instead (tasks 11.2).

With a cfd-backend checkout, the kept copy must match its ``viz.sh`` byte for
byte, or the run fails: a stale copy would test yesterday's smoke test. Refresh
it with ``cp ../cfd-backend/tests/smoke/viz.sh tests/cockpit/`` and update
``VIZ_SH_FROM``.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "hotRoom"
REQUIRED = ["web/package.json", "web/package-lock.json", "requirements.txt", "main.py"]
# UUID-shaped directory names, as the backend names its cases.
CASE_ID = "e2614af7-0000-4000-8000-00000000c0c1"
LATE_ID = "e2614af7-0000-4000-8000-00000000c0c2"
# cfd-backend's test suite stages its cases one level down, in CFD_HOME/tests/.
# The one-level scan missed them; found at the phase-1 gate (2026-10-22).
TEST_CASE = "tests/window-radiator-no-ahu_0"

# cfd-backend's tests/smoke/viz.sh, kept here for checkouts without cfd-backend
# (CI: the repo is private). VIZ_SH_FROM is the cfd-backend commit it matches.
VIZ_SH_COPY = ROOT / "tests" / "cockpit" / "viz.sh"
VIZ_SH_FROM = "b56cd88 (2026-09-22)"

checks, failures = 0, []


def check(label, condition, detail=""):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)
    return condition


def run(cmd, **kw):
    """Run a step; on failure, return its tail instead of raising."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
    return result.returncode == 0, tail


def no_gl_env(extra=None):
    """The environment with no GL library on the path, as in the cfd-viz image."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_LIBRARY_PATH", "__EGL_VENDOR_LIBRARY_DIRS", "EGL_PLATFORM",
                        "LIBGL_ALWAYS_SOFTWARE")}
    env.update(extra or {})
    return env


def copy_case(dest):
    """The demo case's definition plus its latest time: what a solved case holds."""
    latest = max((d for d in DEMO.iterdir() if d.is_dir() and d.name.replace(".", "").isdigit()),
                 key=lambda d: float(d.name))
    for sub in ("system", "constant", "0", latest.name):
        shutil.copytree(DEMO / sub, dest / sub)


def get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def wait_http(url, proc=None, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- the nginx stand-in -------------------------------------------------------


def start_proxy(upstream_port):
    """``location /viz/ { rewrite ^/viz/(.*)$ /$1 break; proxy_pass ...; }``

    Everything else answers 404, so a request that leaves /viz/ shows up as a
    failure here even before the request-path check. Bodies pass through
    byte for byte (gzip included: no decompression on the way).
    """
    from aiohttp import ClientSession, web

    port = free_port()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def handle(request):
        path = request.rel_url.path
        if not path.startswith("/viz/"):
            return web.Response(status=404, text="outside /viz/ (the cockpit SPA would answer)")
        target = f"http://127.0.0.1:{upstream_port}/{path[len('/viz/'):]}"
        if request.rel_url.query_string:
            target += "?" + request.rel_url.query_string
        forwarded = {"Accept-Encoding": request.headers.get("Accept-Encoding", "")}
        async with ClientSession(auto_decompress=False) as session:
            async with session.get(target, headers=forwarded) as up:
                headers = {k: v for k, v in up.headers.items()
                           if k.lower() in ("content-type", "content-encoding", "cache-control")}
                return web.Response(status=up.status, body=await up.read(), headers=headers)

    async def main():
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        ready.set()
        await asyncio.Event().wait()

    threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True).start()
    ready.wait(10)
    return f"http://127.0.0.1:{port}/viz/"


# -- the halves ---------------------------------------------------------------


def build_half(repo, work, backend):
    """Steps 1-5. Returns (clone, python) or None when the build cannot go on."""
    print("build half (cfd-backend Containerfile: viz-build + cfd-viz)")
    clone = work / "foamviz"   # stands in for /src, then /opt/foamviz
    ok, tail = run(["git", "clone", "--quiet", "--no-local", str(repo), str(clone)])
    if not check("git clone of the committed tree", ok, tail):
        return None

    missing = [f for f in REQUIRED if not (clone / f).is_file()]
    if not check("the four files the Containerfile requires", not missing,
                 f"missing: {' '.join(missing)}" if missing else ", ".join(REQUIRED)):
        return None

    ok, tail = run(["npm", "ci", "--no-audit", "--no-fund"], cwd=clone / "web")
    ok = ok and run(["npm", "run", "build"], cwd=clone / "web")[0]
    check("npm ci && npm run build", ok and (clone / "web" / "dist" / "index.html").is_file(),
          "" if ok else tail)
    shutil.rmtree(clone / "web" / "node_modules", ignore_errors=True)
    shutil.rmtree(clone / ".git", ignore_errors=True)   # the image copies the tree without it

    # Looked up from the scratch directory with --no-project, so uv returns an
    # interpreter of its own and never this checkout's .venv (which it would
    # otherwise prefer, project or not).
    find = ["uv", "python", "find", "--no-project", "3.14"]
    ok, found = run(find, cwd=work)
    if not ok:
        run(["uv", "python", "install", "3.14"], cwd=work)
        ok, found = run(find, cwd=work)
    if not check("a Python 3.14 interpreter (cfd-viz runs python:3.14-slim)", ok, found):
        return None
    venv = work / "venv"
    ok, tail = run([found.strip(), "-m", "venv", str(venv)])
    python = venv / "bin" / "python"
    ok = ok and run([str(python), "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
                    cwd=clone)[0]
    if not check("pip install -r requirements.txt on 3.14", ok, "" if ok else tail):
        return None

    viz_sh = backend / "tests" / "smoke" / "viz.sh" if backend else None
    if viz_sh is not None and viz_sh.is_file():
        source = f"cfd-backend's {viz_sh.relative_to(backend)}"
        same = VIZ_SH_COPY.read_bytes() == viz_sh.read_bytes()
        check("the kept copy of viz.sh matches cfd-backend's", same,
              "" if same else f"{VIZ_SH_COPY.relative_to(ROOT)} is stale: "
              f"cp {viz_sh} {VIZ_SH_COPY.parent}/ and update VIZ_SH_FROM")
    else:
        viz_sh = VIZ_SH_COPY
        source = f"cfd-backend's viz.sh, kept copy @ {VIZ_SH_FROM}"
        print(f"  (no cfd-backend checkout at {backend}: running the kept copy of its viz.sh)")
    script = viz_sh.read_text()
    if script.count("cd /opt/foamviz") != 1:
        check("viz.sh still changes into /opt/foamviz (needed to stand the clone in)", False,
              "cfd-backend's smoke test changed shape; update this check")
        return clone, python
    local = work / "viz.sh"
    local.write_text(script.replace("cd /opt/foamviz", 'cd "$FOAMVIZ_ROOT"'))
    ok, tail = run(["bash", str(local)],
                   env=no_gl_env({"FOAMVIZ_ROOT": str(clone),
                                  "PATH": f"{venv / 'bin'}{os.pathsep}{os.environ['PATH']}"}))
    check(f"{source} (no GL libraries)", ok,
          tail.splitlines()[-1] if ok else tail)
    return clone, python


def start_cmd(clone, python, cfd_home, port):
    """The image's CMD, verbatim: ``exec python main.py --server --data "$CFD_HOME" ...``."""
    env = no_gl_env({"CFD_HOME": str(cfd_home), "VIZ_PORT": str(port),
                     "PATH": f"{python.parent}{os.pathsep}{os.environ['PATH']}"})
    return subprocess.Popen(
        ["sh", "-c", 'exec python main.py --server --data "$CFD_HOME" '
                     '--host 0.0.0.0 --port "$VIZ_PORT"'],
        cwd=clone, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def why_down(proc, up):
    if up:
        return ""
    return proc.stdout.read() if proc.poll() is not None else "timeout"


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def browser_checks(base, case):
    """Step 8: the cockpit iframe URL in Chromium. *base* ends in /viz/."""
    from playwright.sync_api import sync_playwright

    prefix = urlparse(base).path            # "/viz/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 800})
        requests, bad = [], []
        page.on("request", lambda r: requests.append(r.url))
        page.on("response",
                lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.goto(f"{base}?case={case}&theme=light", wait_until="domcontentloaded")
        page.wait_for_selector(".stage canvas", timeout=120_000)
        page.wait_for_selector(".busy", state="detached", timeout=180_000)
        colours = 0
        for _ in range(100):
            grab = page.evaluate("window.__viz ? window.__viz.grab() : null") or {}
            colours = grab.get("colours", 0)
            if colours >= 40:
                break
            page.wait_for_timeout(100)
        check("the cockpit URL opens the case and renders", colours >= 40, f"{colours} colours")
        check("no start screen in front of it",
              page.locator('[data-ctl="start-screen"]').count() == 0)
        check("?theme=light is applied", page.get_attribute(".app", "data-theme") == "light")
        outside = [u for u in requests if not urlparse(u).path.startswith(prefix)]
        check(f"every request stays under {prefix}", not outside, ", ".join(outside[:3]))
        check("no request failed", not bad, ", ".join(bad[:3]))
        browser.close()


def runtime_half(clone, python, work):
    """Steps 6-9."""
    print("\nruntime half (the image's CMD behind a /viz/ proxy)")
    cfd_home = work / "cfd-home"
    copy_case(cfd_home / CASE_ID)
    copy_case(cfd_home / TEST_CASE)
    test_name = TEST_CASE.rsplit("/", 1)[-1]
    port = free_port()
    server = start_cmd(clone, python, cfd_home, port)
    try:
        up = wait_http(f"http://127.0.0.1:{port}/api/cases", server)
        if not check("the CMD line serves (python main.py --server ...)", up, why_down(server, up)):
            return
        base = start_proxy(port)
        cases = get_json(base + "api/cases")["cases"]
        check("cases are keyed by directory name, CFD_HOME/<uuid> and CFD_HOME/tests/<name>",
              cases == [CASE_ID, test_name], str(cases))
        browser_checks(base, CASE_ID)
        print("  (a test-suite case, one level down)")
        browser_checks(base, test_name)

        copy_case(cfd_home / LATE_ID)
        cases = get_json(base + "api/cases")["cases"]
        check("a case written after start-up is listed", LATE_ID in cases, str(cases))
    finally:
        stop(server)

    empty = work / "cfd-home-empty"
    empty.mkdir()
    port = free_port()
    server = start_cmd(clone, python, empty, port)
    try:
        up = wait_http(f"http://127.0.0.1:{port}/api/cases", server)
        time.sleep(2)
        alive = up and server.poll() is None
        check("an empty CFD_HOME stays up", alive, why_down(server, alive))
        if alive:
            listed = get_json(f"http://127.0.0.1:{port}/api/cases")["cases"]
            check("and lists no case", listed == [], str(listed))
    finally:
        stop(server)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=ROOT,
                    help="repository to clone (its checked-out HEAD); default this checkout")
    ap.add_argument("--backend", type=Path, default=ROOT.parent / "cfd-backend",
                    help="cfd-backend checkout providing tests/smoke/viz.sh "
                         "(without one, the copy in tests/cockpit/ runs)")
    ap.add_argument("--runtime-only", action="store_true",
                    help="only drive a running stack at --base-url (no build)")
    ap.add_argument("--base-url", help="with --runtime-only: the /viz/ URL of a running stack")
    ap.add_argument("--case", default=None,
                    help="with --runtime-only: the case to open (default: the first listed)")
    args = ap.parse_args()

    if args.runtime_only:
        if not args.base_url:
            ap.error("--runtime-only needs --base-url")
        base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
        print(f"runtime only, against {base}")
        cases = get_json(base + "api/cases")["cases"]
        check("the stack lists cases", bool(cases), f"{len(cases)} case(s)")
        if cases:
            browser_checks(base, args.case or cases[0])
    else:
        with tempfile.TemporaryDirectory(prefix="cfdv-cockpit-") as tmp:
            built = build_half(args.repo.resolve(), Path(tmp),
                               args.backend.resolve() if args.backend else None)
            if built is not None and not failures:
                runtime_half(built[0], built[1], Path(tmp))

    print(f"\n{checks - len(failures)}/{checks} cockpit-contract checks passed")
    if failures:
        print("failed:\n" + "\n".join(f"  - {f}" for f in failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
