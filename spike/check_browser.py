#!/usr/bin/env python3
"""Drive the spike in a real browser and report the numbers that decide it.

    python spike/check_browser.py --case hotRoom --out /tmp/shots

Measures time-to-first-frame, wire size, frame rate while orbiting, and -- the
part that matters for the interaction model -- which controls cause a geometry
refetch and which are pure GPU state.

NB the frame rates come from headless Chromium on SwiftShader (software GL), so
treat them as a floor and a smoke test, not as what real hardware will do.
"""

import argparse
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8191

checks, failures = 0, []


def check(label, condition, detail=""):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def wait_for_server(port, proc, timeout=90):
    """Poll /api/cases until it answers -- the server builds the VTK pipeline and
    scans the case root before it listens, which takes a few seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early:\n{proc.stdout.read()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cases", timeout=2) as r:
                return json.load(r)["cases"]
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.5)
    raise AssertionError(f"server not listening after {timeout}s")


def gl_colours(page):
    """Distinct colours in the GL back buffer, counted in the browser.

    Not a screenshot: the HUD, legend and busy overlay sit on top of the canvas,
    so a page screenshot of an *empty* scene still comes back colourful. The
    viewer exposes window.__spikeGrab for exactly this (see Viewer.grab).
    """
    result = page.evaluate("window.__spikeGrab ? window.__spikeGrab() : null")
    return (result or {}).get("colours", 0)


def shot(page, path):
    """Save what a user would see -- overlays included."""
    box = page.locator(".stage").bounding_box()
    Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB").save(path)


def wait_for_render(page, minimum=40, timeout=180):
    """Wait until the GL buffer holds more than the flat background."""
    page.wait_for_selector(".stage canvas", timeout=timeout * 1000)
    deadline = time.time() + timeout
    best = 0
    while time.time() < deadline:
        count = gl_colours(page)
        best = max(best, count)
        if count >= minimum:
            return count, time.time()
        page.wait_for_timeout(100)
    raise AssertionError(f"nothing rendered within {timeout}s (best {best} colours)")


def orbit(page):
    """Drag across the canvas so OrbitControls spins the scene."""
    box = page.locator(".stage canvas").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(24):
        page.mouse.move(cx + i * 6, cy + (i % 6) * 4)
        page.wait_for_timeout(16)
    page.mouse.up()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="hotRoom")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default="/tmp/spike-shots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "spike" / "server.py"),
         "--data", args.data, "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        cases = wait_for_server(PORT, server)
        print(f"server up; cases: {', '.join(cases)}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            scene_requests = []
            page.on("request", lambda r: scene_requests.append(r.url) if "/api/scene" in r.url else None)
            page.on("pageerror", lambda e: print(f"  ! page error: {e}"))

            print(f"\nloading {args.case}")
            t0 = time.time()
            page.goto(f"http://127.0.0.1:{PORT}/?case={args.case}", wait_until="domcontentloaded")
            colours, t_render = wait_for_render(page)
            check("first frame renders", colours >= 40, f"{colours} distinct colours")
            check("time to first frame", True, f"{t_render - t0:.1f} s")
            shot(page, out / "01-first-frame.png")

            page.wait_for_function(
                "() => document.querySelector('.hud')?.innerText.includes('MB on the wire')",
                timeout=60_000,
            )
            hud = page.locator(".hud").inner_text()
            print("  HUD:", " | ".join(hud.splitlines()))
            check("payload reported", "MB on the wire" in hud)
            check("draw calls reported", "draw call" in hud)

            n_before = len(scene_requests)

            # --- client-side controls must not refetch geometry -------------
            page.select_option('[data-ctl=preset]', "viridis")
            page.wait_for_timeout(1200)
            check("colour map switch does not refetch",
                  len(scene_requests) == n_before, f"{len(scene_requests) - n_before} request(s)")
            shot(page, out / "02-viridis.png")

            page.fill('[data-ctl=range-max]', "0.1")
            page.keyboard.press("Tab")
            page.wait_for_timeout(1200)
            check("colour range change does not refetch",
                  len(scene_requests) == n_before, f"{len(scene_requests) - n_before} request(s)")
            shot(page, out / "03-rescaled.png")

            tris_before = page.evaluate("window.__spikeStats?.triangles ?? 0")
            page.uncheck('[data-ctl="part-boundary"]')
            page.wait_for_timeout(1200)
            tris_after = page.evaluate("window.__spikeStats?.triangles ?? 0")
            check("hiding a part does not refetch", len(scene_requests) == n_before)
            check("hiding a part drops triangles", tris_after < tris_before,
                  f"{tris_before:,} -> {tris_after:,}")
            shot(page, out / "04-boundary-hidden.png")

            # --- colour banding is a shader uniform -------------------------
            # Isolate the slice: it is drawn unlit, so its pixel colours are the
            # colour map itself and the band count is directly measurable.
            page.uncheck('[data-ctl="part-iso"]')
            page.uncheck('[data-ctl="part-streamlines"]')
            page.wait_for_timeout(600)
            smooth_colours = gl_colours(page)
            page.fill('[data-ctl=bands]', "5")
            page.wait_for_timeout(600)
            banded_colours = gl_colours(page)
            check("banding does not refetch", len(scene_requests) == n_before,
                  f"{len(scene_requests) - n_before} request(s)")
            check("banding collapses the colour count",
                  banded_colours * 4 < smooth_colours,
                  f"{smooth_colours} smooth -> {banded_colours} with 5 bands")
            shot(page, out / "06-banded.png")
            page.fill('[data-ctl=bands]', "0")
            page.check('[data-ctl="part-iso"]')
            page.check('[data-ctl="part-streamlines"]')
            page.wait_for_timeout(600)
            check("banding is reversible", gl_colours(page) > banded_colours)

            # --- dragging must not refetch; releasing must ------------------
            # A range input fires `input` per pixel of a drag and `change` on
            # release. 20 input events used to mean 20 extractions queued.
            page.eval_on_selector(
                '[data-ctl=slice-frac]',
                """el => {
                    for (let i = 0; i < 20; i += 1) {
                        el.value = 0.3 + i * 0.01
                        el.dispatchEvent(new Event('input', { bubbles: true }))
                    }
                }""",
            )
            page.wait_for_timeout(2000)
            check("dragging the cut plane does not refetch",
                  len(scene_requests) == n_before,
                  f"{len(scene_requests) - n_before} request(s) for 20 drag events")

            page.eval_on_selector(
                '[data-ctl=slice-frac]',
                "el => { el.value = 0.25; el.dispatchEvent(new Event('change', {bubbles:true})) }",
            )
            page.wait_for_timeout(6000)
            check("releasing the cut plane refetches once",
                  len(scene_requests) == n_before + 1, f"{len(scene_requests) - n_before} request(s)")
            shot(page, out / "05-slice-moved.png")

            # --- navigation: Z-up turntable ---------------------------------
            up = page.evaluate("window.__spikeCamera().up")
            check("camera up is +Z", abs(up["z"] - 1) < 1e-6 and abs(up["x"]) < 1e-6,
                  f"({up['x']:.2f}, {up['y']:.2f}, {up['z']:.2f})")
            before = page.evaluate("window.__spikeCamera()")
            orbit(page)
            page.wait_for_timeout(800)
            after = page.evaluate("window.__spikeCamera()")
            moved = any(abs(after["position"][k] - before["position"][k]) > 1e-6 for k in "xyz")
            check("orbiting moves the camera", moved)
            check("orbiting keeps +Z up (no roll)",
                  abs(after["up"]["z"] - 1) < 1e-6,
                  f"up.z {after['up']['z']:.6f}")
            check("orbiting keeps the target fixed",
                  all(abs(after["target"][k] - before["target"][k]) < 1e-6 for k in "xyz"))
            shot(page, out / "07-orbited.png")

            # --- interaction cost -------------------------------------------
            orbit(page)
            page.wait_for_timeout(1500)
            stats = page.evaluate("window.__spikeStats")
            print(f"  stats after orbiting: {json.dumps(stats)}")
            check("renders while orbiting", (stats or {}).get("fps", 0) > 0,
                  f"{(stats or {}).get('fps')} fps (SwiftShader)")
            check("scene still drawn", gl_colours(page) >= 40, f"{gl_colours(page)} colours")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\n{checks - len(failures)}/{checks} checks passed; screenshots in {out}")
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
