#!/usr/bin/env python3
"""Server-side numbers: what comes out of each part, how big, how long.

    python tests/bench.py [case ...]

Checks the payload round-trips (unpack() sees the same counts pack() wrote) and
prints the per-part extraction time and wire size. Those per-part figures are
what justify fetching per part rather than per scene -- on a big case the
boundary dwarfs everything else, so a control that only moves the slice must not
drag the boundary along with it.
"""

import sys
import time
from pathlib import Path

import gzip
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import wire  # noqa: E402
from server.scene import SceneSource  # noqa: E402

# Every part, so one run shows the whole cost distribution.
PARTS = "boundary,slice,iso,stream,glyph,geometry"


def report(source, name):
    source.open(name)
    meta = source.meta()
    print(f"\n{name}: {meta['cells']:,} cells, {len(meta['times'])} step(s), "
          f"fields {sorted(meta['fields'])}"
          + (" (decomposed)" if meta["decomposed"] else ""))

    t = time.perf_counter()
    blob = source.scene(dict(parts=PARTS, stream_seeds="200", glyph_count="400"))
    wall = (time.perf_counter() - t) * 1000
    header, parts = wire.unpack(blob)

    print(f"  wire {len(blob) / 1024:9.1f} kB total, server {wall:7.1f} ms"
          f"  (field {header['field']}/{header['component']}, "
          f"range {header['range'][0]:.3g}..{header['range'][1]:.3g})")
    print(f"  {'part':<12}{'verts':>10}{'prims':>10}{'kB':>10}{'ms':>8}   attributes")
    for part in parts:
        kb = sum(a.nbytes for a in part["attributes"].values()) + part["index"].nbytes
        ms = header["serverMs"].get(part["name"], float("nan"))
        print(f"  {part['name']:<12}{part['counts']['vertices']:>10,}"
              f"{part['counts']['primitives']:>10,}{kb / 1024:>10.1f}{ms:>8.1f}"
              f"   {', '.join(sorted(part['attributes']))}")
        pos = part["attributes"]["position"]
        idx = part["index"]
        assert len(pos) == part["counts"]["vertices"], "vertex count mismatch"
        assert idx.max(initial=0) < len(pos), "index out of range"
        if "scalar" in part["attributes"]:
            sc = part["attributes"]["scalar"]
            assert len(sc) == len(pos), "scalar/vertex mismatch"
            assert np.isfinite(sc).all(), "non-finite scalars"
    # Wire size is the metric that decides this, so measure what the transport
    # would actually send. Indices and normals are the bulk and both compress.
    packed = len(gzip.compress(blob, 6))
    print(f"  gzip -6: {packed / 1024:.1f} kB ({100 * packed / len(blob):.0f}% of raw)")
    for part in parts:
        for attr, arr in sorted(part["attributes"].items()):
            z = len(zlib.compress(arr.tobytes(), 6))
            print(f"    {part['name']}.{attr:<9}{arr.nbytes / 1024:9.1f} kB"
                  f" -> {z / 1024:8.1f} kB gz")
        z = len(zlib.compress(part["index"].tobytes(), 6))
        print(f"    {part['name']}.{'index':<9}{part['index'].nbytes / 1024:9.1f} kB"
              f" -> {z / 1024:8.1f} kB gz")
    print(f"  round-trip ok; payload {header['payloadBytes'] / 1024:.1f} kB "
          f"+ header {len(blob) / 1024 - header['payloadBytes'] / 1024:.1f} kB")

    # The same request again, with nothing dirtied: no filter re-executes, so
    # this is the *floor* for a round trip -- pure extraction + packing. Moving a
    # slider re-executes the filter and costs the per-part time above on top.
    t = time.perf_counter()
    source.scene(dict(parts=PARTS, stream_seeds="200", glyph_count="400"))
    print(f"  re-pack only (nothing dirtied): {(time.perf_counter() - t) * 1000:7.1f} ms"
          f"  <- floor for any round trip")


def main():
    names = sys.argv[1:] or ["hotRoom", "s2"]
    source = SceneSource(ROOT / "data")
    available = source.case_names()
    for name in names:
        if name not in available:
            print(f"skip {name}: not under data/ ({available})")
            continue
        report(source, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
