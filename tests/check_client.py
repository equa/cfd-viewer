#!/usr/bin/env python3
"""Drive the React + three.js client in a real browser and assert its contracts.

    python tests/check_client.py --case hotRoom --out /tmp/viz-shots

The point of this suite is not "does it render" -- it is **which controls cost a
round trip**. That boundary is the architecture (see CLAUDE.md), and it is the
kind of thing that decays silently: someone adds a dependency to a hook and
suddenly changing the colour map re-extracts the mesh, with no visible symptom
beyond the app feeling slow on a big case. So nearly every check here counts
`/api/scene` requests.

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
from urllib.parse import parse_qs, urlparse

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
    viewer exposes window.__viz.grab for exactly this (see Viewer.grab).
    """
    result = page.evaluate("window.__viz ? window.__viz.grab() : null")
    return (result or {}).get("colours", 0)


def gl_background(page):
    """Fraction of the view that is still the clear colour -- i.e. how empty it is."""
    result = page.evaluate("window.__viz ? window.__viz.grab() : null")
    return (result or {}).get("background", 1.0)


def shot(page, path):
    """Save what a user would see -- overlays included."""
    box = page.locator(".stage").bounding_box()
    Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB").save(path)


def red_pixels(page):
    """Count strongly-red pixels in the stage.

    Used to prove the cut-plane frame actually appears during a drag, rather
    than trusting that the code path ran. It is NOT an absolute measure: the
    triad's X arrow and the legend's red X key are also red and also inside the
    clip box, so callers must compare against a baseline taken before the drag.
    """
    box = page.locator(".stage canvas").bounding_box()
    image = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB")
    return sum(
        1 for r, g, b in image.getdata()
        if r > 120 and r > g * 2 and r > b * 2
    )


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


def settle(page, requests, quiet_ms=900, timeout_ms=180_000):
    """Wait until no new scene request has started for `quiet_ms`."""
    deadline = time.time() + timeout_ms / 1000
    last = len(requests)
    stable_since = time.time()
    while time.time() < deadline:
        page.wait_for_timeout(150)
        if len(requests) != last:
            last = len(requests)
            stable_since = time.time()
        elif (time.time() - stable_since) * 1000 >= quiet_ms:
            return
    raise AssertionError("scene requests never settled")


def wait_idle(page, requests):
    """Settle the scene requests AND wait for the busy overlay to go.

    The overlay captures clicks deliberately -- there is no way to abort a
    running VTK filter, so blocking input is how the app avoids queueing edits
    behind one. A test that clicks through it is racing the app, so every
    interaction block waits here first.
    """
    settle(page, requests)
    page.wait_for_selector(".busy", state="detached", timeout=180_000)


def ctl(name):
    return f'[data-ctl="{name}"]'


def slider_drag(page, selector, steps=20, fraction=0.75):
    """Drag a Mantine slider from its centre towards `fraction` of its width.

    Returns a callable that releases the mouse, so a caller can inspect the page
    mid-drag. Mantine renders a real thumb and listens for real pointer events,
    so this is a genuine drag -- the only way to exercise the live-preview /
    commit-on-release split that the whole cut-plane UX rests on.
    """
    box = page.locator(selector).bounding_box()
    y = box["y"] + box["height"] / 2
    start = box["x"] + box["width"] * 0.5
    end = box["x"] + box["width"] * fraction
    page.mouse.move(start, y)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(start + (end - start) * i / steps, y)
        page.wait_for_timeout(16)
    return lambda: page.mouse.up()


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


def parts_of(url):
    """The `parts=` list of a scene request -- what the server was asked to build."""
    return set(parse_qs(urlparse(url).query).get("parts", [""])[0].split(",")) - {""}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="hotRoom")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default="/tmp/viz-shots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py"),
         "--data", args.data, "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        cases = wait_for_server(PORT, server)
        print(f"server up; cases: {', '.join(cases)}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            scene = []
            page.on("request", lambda r: scene.append(r.url) if "api/scene" in r.url else None)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

            # ---------------------------------------------------------- load
            print(f"\nloading {args.case}")
            t0 = time.time()
            page.goto(f"http://127.0.0.1:{PORT}/?case={args.case}",
                      wait_until="domcontentloaded")
            colours, t_render = wait_for_render(page)
            check("first frame renders", colours >= 40, f"{colours} distinct colours")
            check("time to first frame", True, f"{t_render - t0:.1f} s")
            wait_idle(page, scene)
            shot(page, out / "01-first-frame.png")

            # The UX port: is the whole trame layout actually there?
            print("\nlayout")
            for name, label in [
                ("field", "top bar: field"), ("component", "top bar: component"),
                ("preset", "top bar: colour map"), ("auto-range", "top bar: auto range"),
                ("rescale", "top bar: rescale"), ("options", "top bar: options"),
                ("screenshot", "top bar: screenshot"),
                ("case", "side pane: case"),
                ("tool-cutplane", "tool stack: cut plane"),
                ("tool-boundary", "tool stack: boundary"),
                ("tool-contour", "tool stack: isosurfaces"),
                ("tool-stream", "tool stack: streamlines"),
                ("tool-glyph", "tool stack: arrows"),
                ("tool-geometry", "tool stack: geometry"),
                ("show-cutplane", "tool stack: eye toggle"),
                ("time", "bottom bar: time"),
                ("view-pz", "bottom bar: +Z view"),
                ("refresh-times", "bottom bar: refresh"),
            ]:
                check(label, page.locator(ctl(name)).count() == 1)
            check("legend present", page.locator(".legend-bar").count() == 1)
            check("axis key present", page.locator(".axis-key .ax-z").count() == 1)
            check("HUD present", page.locator(".hud").count() == 1)

            # ------------------------------------------- client-side controls
            # Each of these must be pure GPU state: zero scene requests.
            print("\nclient controls cost no round trip")
            n = len(scene)
            # Mantine Select is a combobox, not a <select>: click, then pick.
            page.click(ctl("preset"))
            page.get_by_role("option", name="Viridis").click()
            page.wait_for_timeout(700)
            check("colour map switch does not refetch", len(scene) == n,
                  f"{len(scene) - n} request(s)")

            n = len(scene)
            page.click(ctl("options"))
            page.fill(ctl("bands"), "5")
            page.wait_for_timeout(600)
            banded = gl_colours(page)
            check("banding does not refetch", len(scene) == n, f"{len(scene) - n} request(s)")
            check("banding collapses the colour count", banded < colours,
                  f"{colours} -> {banded}")
            shot(page, out / "02-bands.png")

            n = len(scene)
            page.fill(ctl("bands"), "0")
            page.wait_for_timeout(400)
            check("banding is reversible", gl_colours(page) > banded)
            check("un-banding does not refetch", len(scene) == n)

            # Opacity-by-value needs something COLOURED to ramp: the shader
            # deliberately ignores the ramp on an uncoloured part, because there
            # is no value there to ramp against, and the shell defaults to
            # uncoloured (a neutral grey, so the slice inside it reads). So
            # colour the shell first -- and it is the ideal subject, being
            # no-slip walls where |U| is ~0 everywhere, so the room should all
            # but empty out. The spike measured 75% -> 96% empty on s2.
            page.keyboard.press("Escape")
            page.click(ctl("tool-boundary"))
            page.click(ctl("surface-colored"))
            page.wait_for_timeout(800)
            before_bg = gl_background(page)
            n = len(scene)
            page.click(ctl("options"))
            page.click(ctl("opacity-map"))
            page.wait_for_timeout(900)
            after_bg = gl_background(page)
            check("opacity mapping does not refetch", len(scene) == n)
            check("opacity mapping fades low values", after_bg > before_bg + 0.05,
                  f"background {before_bg:.0%} -> {after_bg:.0%}")
            shot(page, out / "02b-opacity-map.png")
            page.click(ctl("opacity-map"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            check("opacity mapping is reversible",
                  abs(gl_background(page) - before_bg) < 0.05,
                  f"background back to {gl_background(page):.0%}")
            # Leave the shell as it was found, so later checks see the defaults.
            page.click(ctl("surface-colored"))
            page.wait_for_timeout(300)

            # ------------------------------------------------ eye toggles
            print("\nvisibility, and the part cache")
            wait_idle(page, scene)
            tris_before = page.evaluate("window.__viz.stats()")["triangles"]
            n = len(scene)
            page.click(ctl("show-boundary"))
            page.wait_for_timeout(700)
            tris_after = page.evaluate("window.__viz.stats()")["triangles"]
            check("hiding a part does not refetch", len(scene) == n)
            check("hiding a part drops its triangles", tris_after < tris_before,
                  f"{tris_before:,} -> {tris_after:,}")

            n = len(scene)
            page.click(ctl("show-boundary"))
            settle(page, scene)
            check("showing it again is served from cache", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("triangles come back",
                  page.evaluate("window.__viz.stats()")["triangles"] >= tris_before)

            # ------------------------------------------------ the cut plane
            print("\ncut plane: drag is free, release costs one request")
            page.click(ctl("tool-cutplane"))
            wait_idle(page, scene)
            n = len(scene)
            baseline_red = red_pixels(page)      # triad X arrow + legend X key
            release = slider_drag(page, ctl("plane-slider"))
            mid_drag_red = red_pixels(page)
            during = len(scene) - n
            shot(page, out / "03-plane-drag.png")
            release()
            wait_idle(page, scene)
            after = len(scene) - n
            rest_red = red_pixels(page)

            check("dragging the cut plane does not refetch", during == 0,
                  f"{during} request(s) during the drag")
            check("the red outline appears during the drag",
                  mid_drag_red - baseline_red > 200,
                  f"{baseline_red} -> {mid_drag_red} red pixels")
            check("releasing refetches exactly once", after == 1, f"{after} request(s)")
            check("the outline is hidden after release",
                  rest_red - baseline_red < (mid_drag_red - baseline_red) / 4,
                  f"{rest_red} red pixels vs {baseline_red} baseline")

            # The headline: the release must NOT re-extract the boundary.
            asked = parts_of(scene[-1])
            check("only plane-dependent parts are re-extracted",
                  "boundary" not in asked, f"asked for {sorted(asked) or ['-']}")

            # ------------------------------------------------ apply buttons
            print("\nApply gates the heavy groups")
            page.click(ctl("tool-stream"))
            page.click(ctl("show-stream"))
            wait_idle(page, scene)
            n = len(scene)
            slider_drag(page, ctl("stream-seeds"))()
            page.wait_for_timeout(900)
            check("streamline seeds do nothing until Apply", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("Apply is enabled once the group is dirty",
                  page.locator(ctl("apply-stream")).is_enabled())
            page.click(ctl("apply-stream"))
            wait_idle(page, scene)
            check("Apply commits in one request", len(scene) - n == 1,
                  f"{len(scene) - n} request(s)")
            check("Apply is disabled again once clean",
                  not page.locator(ctl("apply-stream")).is_enabled())
            shot(page, out / "04-streamlines.png")

            # The plane's numeric fields are inert until their own Apply.
            page.click(ctl("tool-cutplane"))
            n = len(scene)
            page.fill(ctl("plane-z"), "2.2")
            page.wait_for_timeout(800)
            check("typing a plane coordinate does not refetch", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            page.click(ctl("plane-apply"))
            wait_idle(page, scene)
            check("plane Apply commits once", len(scene) - n == 1, f"{len(scene) - n} request(s)")

            # ------------------------------------------------ time stepping
            print("\ntime stepping, and going back")
            n = len(scene)
            page.click(ctl("tool-boundary"))
            wait_idle(page, scene)
            slider_drag(page, ctl("time"), fraction=0.3)()
            settle(page, scene)
            stepped = len(scene) - n
            check("a time step refetches", stepped >= 1, f"{stepped} request(s)")
            label_a = page.locator(ctl("time-label")).inner_text()
            wait_idle(page, scene)
            n = len(scene)
            slider_drag(page, ctl("time"), fraction=0.999)()
            wait_idle(page, scene)
            label_b = page.locator(ctl("time-label")).inner_text()
            check("the time label follows the slider", label_a != label_b,
                  f"{label_a.strip()} -> {label_b.strip()}")
            check("returning to a visited step is served from cache", len(scene) == n,
                  f"{len(scene) - n} request(s)")

            # ------------------------------------------------ tools + camera
            print("\ntools and camera")
            page.click(ctl("tool-glyph"))
            check("selecting a tool shows its panel",
                  page.locator(ctl("glyph-source")).is_visible())
            check("and hides the others",
                  not page.locator(ctl("plane-axis")).is_visible())
            n = len(scene)
            check("selecting a tool does not refetch", len(scene) == n)

            up = page.evaluate("window.__viz.camera()")["up"]
            check("camera up is +Z", abs(up[2] - 1) < 1e-6 and abs(up[0]) < 1e-6, f"up={up}")
            before = page.evaluate("window.__viz.camera()")
            orbit(page)
            after = page.evaluate("window.__viz.camera()")
            moved = any(abs(a - b) > 1e-6 for a, b in zip(before["position"], after["position"]))
            check("orbiting moves the camera", moved)
            check("orbiting keeps +Z up (no roll)",
                  abs(after["up"][2] - 1) < 1e-6 and abs(after["up"][0]) < 1e-6)
            check("orbiting keeps the target fixed",
                  all(abs(a - b) < 1e-6 for a, b in zip(before["target"], after["target"])))

            # EPS is a fraction of the domain, not a machine epsilon: damping
            # is still easing the camera into place when we read it.
            eps = 0.05
            n = len(scene)
            page.click(ctl("view-pz"))
            page.wait_for_timeout(600)
            top = page.evaluate("window.__viz.camera()")
            check("a view button aims the camera down that axis",
                  abs(top["position"][0] - top["target"][0]) < eps
                  and abs(top["position"][1] - top["target"][1]) < eps
                  and top["position"][2] > top["target"][2],
                  f"pos={[round(v, 2) for v in top['position']]} "
                  f"target={[round(v, 2) for v in top['target']]}")
            check("a view button costs no round trip", len(scene) == n)

            # Keyboard: x looks down +X, shift+x down -X.
            page.click(".stage canvas", position={"x": 5, "y": 5})
            page.keyboard.press("x")
            page.wait_for_timeout(600)
            cam_x = page.evaluate("window.__viz.camera()")
            check("the x shortcut aims down +X",
                  cam_x["position"][0] > cam_x["target"][0]
                  and abs(cam_x["position"][1] - cam_x["target"][1]) < eps
                  and abs(cam_x["position"][2] - cam_x["target"][2]) < eps,
                  f"pos={[round(v, 2) for v in cam_x['position']]} "
                  f"target={[round(v, 2) for v in cam_x['target']]}")

            # F sets the centre of rotation to the point under the cursor.
            page.click(ctl("view-iso")) if page.locator(ctl("view-iso")).count() else None
            page.wait_for_timeout(300)
            box = page.locator(".stage canvas").bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.55)
            before = page.evaluate("window.__viz.camera()")["target"]
            n = len(scene)
            page.keyboard.press("f")
            page.wait_for_timeout(400)
            after = page.evaluate("window.__viz.camera()")["target"]
            check("F re-centres on the picked point",
                  any(abs(a - b) > 1e-9 for a, b in zip(before, after)),
                  f"target {[round(v, 2) for v in before]} -> {[round(v, 2) for v in after]}")
            check("F costs no round trip", len(scene) == n)

            # ------------------------------------------------ still alive
            print("\nfinal state")
            stats = page.evaluate("window.__viz.stats()")
            check("renders while interacting", stats["fps"] > 0,
                  f"{stats['fps']} fps (SwiftShader)")
            check("scene still drawn", gl_colours(page) >= 40, f"{gl_colours(page)} colours")
            check("no page or console errors", not errors,
                  "; ".join(errors[:3]) if errors else "")
            shot(page, out / "05-final.png")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\n{checks - len(failures)}/{checks} checks passed; screenshots in {out}")
    if failures:
        print("failed:")
        for f in failures:
            print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
