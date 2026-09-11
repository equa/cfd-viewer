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
    px = image.tobytes()
    return sum(
        1 for i in range(0, len(px), 3)
        if px[i] > 120 and px[i] > px[i + 1] * 2 and px[i] > px[i + 2] * 2
    )


def frame_difference(page, gap_ms=450):
    """Mean absolute pixel difference between two frames `gap_ms` apart.

    The only honest way to test an animation: assert the picture *changes* while
    it is on and *holds still* while it is off. Everything else (uniform values,
    attribute presence) can be right while nothing actually moves.
    """
    box = page.locator(".stage canvas").bounding_box()
    first = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("L")
    page.wait_for_timeout(gap_ms)
    second = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("L")
    a = first.tobytes()
    b = second.tobytes()
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


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

            # Opacity-by-value must work on the shell AS IT SHIPS -- uncoloured,
            # a neutral grey so the slice inside it reads. It was reported broken
            # because the ramp had been multiplied by "colour by field", so the
            # one surface you most want to see through ignored it. The shell is
            # also the ideal subject: no-slip walls, so |U| is ~0 across all of
            # them and the room should all but empty out.
            page.keyboard.press("Escape")
            page.click(ctl("tool-boundary"))
            check("the shell ships uncoloured",
                  not page.locator(ctl("surface-colored")).is_checked())
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

            # ------------------------------------- colouring and solid colour
            print("\ncolour by field, per part")
            n = len(scene)
            page.click(ctl("surface-colored"))
            page.wait_for_timeout(600)
            check("colouring the shell costs no round trip", len(scene) == n)
            check("a solid-colour picker appears when colouring is off",
                  page.locator(ctl("surface-solid")).count() == 0,
                  "hidden while coloured")
            page.click(ctl("surface-colored"))
            page.wait_for_timeout(400)
            check("the solid-colour picker returns with colouring off",
                  page.locator(ctl("surface-solid")).count() == 1)
            for part in ("contour", "stream", "glyph"):
                check(f"{part} has its own colour-by-field toggle",
                      page.locator(ctl(f"{part}-colored")).count() == 1)

            # ----------------------------------- robust range / cell values
            print("\nsampling options (both were reported dead)")
            page.click(ctl("tool-boundary"))
            wait_idle(page, scene)
            # Robust range changes only the reported range, so it is in no
            # part's PART_INPUTS -- which is exactly why it used to do nothing.
            # It now comes from the cheap /api/range endpoint.
            range_calls = []
            page.on("request",
                    lambda r: range_calls.append(r.url) if "api/range" in r.url else None)
            # range-max lives inside the Options popover, so open it first.
            page.click(ctl("options"))
            before = page.locator(ctl("range-max")).input_value()
            page.click(ctl("robust-range"))
            page.click(ctl("apply-options"))
            wait_idle(page, scene)
            page.wait_for_timeout(600)
            after = page.locator(ctl("range-max")).input_value()
            check("robust range actually changes the range", before != after,
                  f"max {before} -> {after}")
            check("robust range asks the cheap range endpoint", len(range_calls) > 0,
                  f"{len(range_calls)} /api/range call(s)")
            page.click(ctl("robust-range"))
            page.click(ctl("apply-options"))
            wait_idle(page, scene)

            # True cell values must produce FLAT per-cell colour, which means
            # the server has to de-index the mesh: 3 vertices per triangle. The
            # wire only ever read point data before, so the toggle was inert.
            tris_before = page.evaluate("window.__viz.stats()")["triangles"]
            page.click(ctl("cell-data"))
            page.click(ctl("apply-options"))
            wait_idle(page, scene)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            flat = page.evaluate("""() => {
              const m = window.__viz.partInfo('boundary');
              return m ? m.verticesPerTriangle : null;
            }""")
            check("true cell values de-indexes the mesh", flat is not None and flat > 2.9,
                  f"{flat} vertices per triangle (3 = flat per cell)")
            check("triangle count is unchanged by it",
                  page.evaluate("window.__viz.stats()")["triangles"] == tris_before,
                  f"{tris_before:,} triangles")
            page.click(ctl("options"))
            page.click(ctl("cell-data"))
            page.click(ctl("apply-options"))
            wait_idle(page, scene)
            page.keyboard.press("Escape")

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

            # ------------------------------------ tubes, built in the browser
            print("\nstreamline tubes (client-built)")
            lines = page.evaluate("window.__viz.partInfo('stream')")
            check("streamlines arrive as lines", lines["mode"] == "lines",
                  f"{lines['primitives']:,} segments, {lines['vertices']:,} vertices")
            n = len(scene)
            page.click(ctl("stream-tubes"))
            page.wait_for_timeout(1500)
            tubes = page.evaluate("window.__viz.partInfo('stream')")
            # The whole point: the server ships lines only, because its tube
            # geometry was ~9x the wire (10.30 MB gzipped against 1.13 MB on s2)
            # for no saving in server time.
            check("switching to tubes costs no round trip", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("tubes are real triangle geometry", tubes["mode"] == "triangles",
                  f"{tubes['triangles']:,} triangles from {lines['vertices']:,} line vertices")
            check("the tube has many more vertices than the line",
                  tubes["vertices"] > lines["vertices"] * 4,
                  f"{lines['vertices']:,} -> {tubes['vertices']:,}")
            check("comets still ride the tube",
                  page.evaluate("window.__viz.comets()")["hasTravel"])

            # The guard against SPURIOUS CONNECTIONS, which counts cannot catch.
            # While the wire shipped only polyline starts and the client inferred
            # each length from the next one, the tube bridged the orphan points
            # vtkStreamTracer leaves between polylines -- drawing a straight run
            # from a line's end out to a stray seed on the cut plane and back.
            # The tube is the same centreline as the lines, so its longest step
            # must be the lines' longest step.
            spans = page.evaluate("window.__viz.streamSpans()")
            check("the tube introduces no step longer than the lines have",
                  spans["tubeMax"] <= spans["lineMax"] * 1.01 + 1e-9,
                  f"tube {spans['tubeMax']:.4f} vs lines {spans['lineMax']:.4f}")
            check("and no step spans a big fraction of the domain",
                  spans["tubeMax"] < spans["domain"] * 0.2,
                  f"longest step {spans['tubeMax']:.3f} of a {spans['domain']:.1f} domain")
            shot(page, out / "07-tubes.png")

            # Width is a shader uniform, so it must rebuild nothing at all.
            n = len(scene)
            before_verts = tubes["vertices"]
            slider_drag(page, ctl("stream-radius"), fraction=0.85)()
            page.wait_for_timeout(700)
            after = page.evaluate("window.__viz.partInfo('stream')")
            check("tube width costs no round trip", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("tube width rebuilds no geometry (it is a uniform)",
                  after["vertices"] == before_verts,
                  f"{before_verts:,} vertices before and after")

            n = len(scene)
            page.click(ctl("stream-tubes"))
            page.wait_for_timeout(1200)
            check("switching back to lines costs no round trip", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("and really is lines again",
                  page.evaluate("window.__viz.partInfo('stream')")["mode"] == "lines")

            # ------------------------------------------ streamline animation
            print("\nstreamline comets")
            wait_idle(page, scene)
            comet = page.evaluate("window.__viz.comets()")
            check("streamlines carry travel time", comet["hasTravel"],
                  f"travel {comet['travelMin']:.2f}..{comet['travelMax']:.2f}")
            # More than one period of travel means more than one comet exists to
            # see; a single period would animate as one lonely pulse.
            span = comet["travelMax"] - comet["travelMin"]
            check("travel spans several comet periods", span > comet["period"] * 3,
                  f"{span:.2f} over a period of {comet['period']}")
            check("animation is off by default", comet["uComets"] == 0)

            # Still while off. Damping has settled by now, and nothing else in
            # the scene moves, so this should be near-zero.
            still = frame_difference(page)
            n = len(scene)
            page.click(ctl("comets"))
            page.wait_for_timeout(700)
            moving = frame_difference(page)
            check("turning the animation on costs no round trip", len(scene) == n,
                  f"{len(scene) - n} request(s)")
            check("the animation actually moves the picture", moving > still + 0.15,
                  f"mean frame delta {still:.3f} off -> {moving:.3f} on")
            check("the animation is enabled on the GPU",
                  page.evaluate("window.__viz.comets()")["uComets"] == 1)
            shot(page, out / "04b-comets.png")

            # Speed is a live client control: no fetch, and it changes the rate.
            n = len(scene)
            slider_drag(page, ctl("comet-speed"), fraction=0.9)()
            page.wait_for_timeout(500)
            check("comet speed costs no round trip", len(scene) == n,
                  f"{len(scene) - n} request(s)")

            page.click(ctl("comets"))
            page.wait_for_timeout(700)
            check("turning it off stops the motion", frame_difference(page) <= still + 0.15,
                  f"mean frame delta back to {frame_difference(page):.3f}")

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

            # ------------------------------- isosurface: reseed, and the lock
            print("\nisosurface field: follows, then locks")
            page.click(ctl("show-contour"))
            page.click(ctl("tool-contour"))
            wait_idle(page, scene)
            value_u = float(page.locator(ctl("contour-value")).input_value())
            # |U| on this case runs 0..0.23, T runs 27..327, so the two ranges
            # cannot be confused -- which is what makes this assertion sharp.
            check("the isovalue starts in the colour field's units", value_u < 1,
                  f"contouring U, value {value_u}")

            # Following the colour field: switching it must MOVE the isovalue
            # into the new field's units. This is the reported regression --
            # the value used to be seeded once and then left in |U| units.
            page.click(ctl("field"))
            page.get_by_role("option", name="T", exact=True).click()
            wait_idle(page, scene)
            page.wait_for_timeout(600)
            value_t = float(page.locator(ctl("contour-value")).input_value())
            check("changing the colour field re-seeds the isovalue", value_t > 20,
                  f"{value_u} (U) -> {value_t} (T)")
            check("the panel names the field being contoured",
                  "T" in page.locator(ctl("contour-base")).inner_text(),
                  page.locator(ctl("contour-base")).inner_text().strip())

            # Locked: the colour field must now RECOLOUR the surface, not move
            # it. Same geometry, same isovalue, different scalars.
            page.click(ctl("contour-lock"))
            wait_idle(page, scene)
            page.wait_for_timeout(400)
            locked_verts = page.evaluate("window.__viz.partInfo('iso')")["vertices"]
            page.click(ctl("field"))
            page.get_by_role("option", name="U", exact=True).click()
            wait_idle(page, scene)
            page.wait_for_timeout(600)
            check("locking pins the isovalue against a colour-field change",
                  abs(float(page.locator(ctl("contour-value")).input_value()) - value_t) < 1e-6,
                  f"still {value_t}")
            check("locking pins the geometry too",
                  page.evaluate("window.__viz.partInfo('iso')")["vertices"] == locked_verts,
                  f"{locked_verts} vertices before and after")
            check("the locked field is still named",
                  "T" in page.locator(ctl("contour-base")).inner_text())
            shot(page, out / "06-iso-locked.png")

            # Release, and it starts following again.
            page.click(ctl("contour-lock"))
            wait_idle(page, scene)
            page.wait_for_timeout(600)
            check("releasing the lock re-seeds from the colour field",
                  float(page.locator(ctl("contour-value")).input_value()) < 1,
                  f"back to U units: {page.locator(ctl('contour-value')).input_value()}")
            page.click(ctl("show-contour"))
            wait_idle(page, scene)

            # ------------------------------------------- wheel on number inputs
            print("\nmouse wheel on numeric inputs")
            page.click(ctl("tool-cutplane"))
            page.wait_for_timeout(300)
            field = ctl("plane-z")
            box = page.locator(field).bounding_box()
            centre = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            # Unfocused: must NOT change. The panels scroll, so a wheel aimed at
            # the pane must never silently edit whatever sits under the pointer.
            page.click(ctl("tool-cutplane"))
            before = page.locator(field).input_value()
            page.mouse.move(*centre)
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(300)
            check("an unfocused wheel leaves the value alone",
                  page.locator(field).input_value() == before,
                  f"stayed at {before}")
            # Focused: steps up and down.
            page.click(field)
            page.wait_for_timeout(200)
            start = float(page.locator(field).input_value())
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(250)
            up = float(page.locator(field).input_value())
            page.mouse.wheel(0, 300)
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(250)
            down = float(page.locator(field).input_value())
            check("a focused wheel steps the value", up > start and down < start,
                  f"{start} -> up {up} -> down {down}")
            n = len(scene)
            check("wheeling a deferred field costs no round trip", len(scene) == n)

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
