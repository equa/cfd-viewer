"""The HTTP contract, recorded and replayed (design D11, layer 1).

The cfd-backend cockpit embeds this server, so its HTTP behaviour is a contract.
``tools/record_contract.py`` runs a fixed query sequence against a server on
the demo case and writes what came back to ``tests/contract/hotroom.json``;
``tests/test_contract.py`` replays the same sequence against the current code
and compares.

The comparison is "the API only grows": every recorded field must still be
there with an equal value, new fields are fine. Scene payloads are compared as
their header (minus timings and byte offsets, which move whenever a buffer is
added) plus a sha256 per named buffer, so geometry has to be byte-identical.

The server is started as ``python main.py --data <root> --port <port>``,
the command line that exists both before and after the package move, and on a
private root holding only ``hotRoom``, so the case list does not depend on
whatever else is in ``data/``.

The query order matters and is part of the snapshot: the pipeline keeps state
between requests (the locked isosurface field, the cut plane), exactly as it
does for a real client.
"""

import hashlib
import json
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = ROOT / "data" / "hotRoom"
CASE = "hotRoom"
SNAPSHOT = ROOT / "tests" / "contract" / "hotroom.json"

# Scene header keys that legitimately differ between runs or move when a buffer
# is added; everything else in the header is compared.
VOLATILE = {"serverMs", "offset", "payloadBytes"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def server(python=sys.executable):
    """A server on a private data root holding only the demo case."""
    if not (CASE_DIR / "constant" / "polyMesh").is_dir():
        raise RuntimeError(f"{CASE_DIR} has no mesh; build it first (CLAUDE.md, Demo data)")
    with tempfile.TemporaryDirectory(prefix="cfdv-contract-") as tmp:
        root = Path(tmp)
        (root / CASE).symlink_to(CASE_DIR)
        port = free_port()
        proc = subprocess.Popen(
            [python, str(ROOT / "main.py"), "--data", str(root),
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.time() + 90
            while True:
                if proc.poll() is not None:
                    raise RuntimeError("server exited:\n" + proc.stdout.read())
                try:
                    urllib.request.urlopen(base + "/api/cases", timeout=2).read()
                    break
                except (urllib.error.URLError, ConnectionError):
                    if time.time() > deadline:
                        raise RuntimeError("server did not come up")
                    time.sleep(0.3)
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def fetch(base, path):
    """(status, content-type, body bytes) for a GET, errors included."""
    try:
        with urllib.request.urlopen(base + path, timeout=300) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode_scene(raw):
    """Header (volatile keys dropped) and a sha256 per named buffer."""
    if raw[:4] != b"FVS1":
        raise ValueError("not an FVS1 payload")
    (hlen,) = struct.unpack("<I", raw[4:8])
    header = json.loads(raw[8:8 + hlen].decode())
    blob = raw[8 + hlen:]
    buffers = {}
    for part in header.get("parts", []):
        named = dict(part.get("attributes", {}))
        named["@index"] = part["index"]
        if "ranges" in part:
            named["@ranges"] = part["ranges"]
        for name, entry in named.items():
            start, length = entry["offset"], entry["length"]
            buffers[f"{part['name']}/{name}"] = _sha(blob[start:start + length])
    return strip(header), buffers


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def record(base, path, kind):
    """One recorded exchange: what was asked and what came back."""
    status, ctype, body = fetch(base, path)
    entry = {"path": path, "kind": kind, "status": status, "type": ctype}
    if status != 200:
        entry["text"] = body.decode(errors="replace")
    elif kind == "json":
        entry["json"] = json.loads(body)
    elif kind == "scene":
        entry["header"], entry["buffers"] = decode_scene(body)
    else:
        entry["sha256"] = _sha(body)
    return entry


def scene_path(**params):
    query = {"case": CASE, "field": "U", "component": "magnitude"}
    query.update({k: v for k, v in params.items() if v is not None})
    return "/api/scene?" + urlencode(query)


def queries(meta):
    """The fixed query sequence, given the case's own /api/meta answer.

    Only the recorder calls this: the replay reads the paths back out of the
    snapshot, so the sequence cannot drift with the code under test.
    """
    q = [
        ("/api/cases", "json"),
        (f"/api/meta?case={CASE}", "json"),
        # Today an empty or unknown case falls back to the first case; after
        # the standalone-viewer change both answer 404 (see ALLOWED_CHANGES).
        ("/api/meta", "json"),
        ("/api/meta?case=nope", "json"),
        (f"/api/times?case={CASE}", "json"),
    ]
    for field, ncomp in sorted(meta["fields"].items()):
        components = ["magnitude"] if ncomp == 1 else ["magnitude", "x", "y", "z"]
        for component in components:
            for robust in ("0", "1"):
                q.append(("/api/range?" + urlencode(
                    {"case": CASE, "field": field, "component": component,
                     "robust": robust}), "json"))
    q.append((f"/api/range?case={CASE}&field=nope", "json"))
    for preset in meta["presets"]:
        q.append((f"/api/lut?name={preset}", "bytes"))
    q.append(("/api/lut?name=nope", "bytes"))

    every = "boundary,slice,iso,stream,glyph,geometry"
    first_patch = meta["patches"][0]
    scenes = [
        dict(parts="boundary"),
        dict(parts="slice"),
        dict(parts="iso"),
        dict(parts="stream"),
        dict(parts="glyph"),
        dict(parts="geometry"),
        dict(parts=every),
        dict(parts="boundary,slice", time_index="0"),
        dict(parts="boundary,slice", field="T"),
        dict(parts="slice", plane_axis="x", plane_coord="2.5"),
        dict(parts="slice,stream", plane_axis="y"),
        dict(parts="iso", field="T", contour_count="3"),
        dict(parts="glyph", glyph_source="slice", glyph_count="200"),
        dict(parts="glyph", glyph_source="isosurface"),
        dict(parts="slice", slice_edges="1"),
        dict(parts="boundary", surface_clip="1", plane_axis="z", plane_coord="1"),
        dict(parts="boundary,slice", cell_data="1"),
        dict(parts="slice", component="x"),
        dict(parts="iso", contour_field="T"),
        dict(parts="geometry", geometry_mode="wireframe"),
        dict(parts="slice", robust="1"),
        dict(parts="boundary", patches=first_patch),
        dict(parts="stream", stream_seeds="50", stream_length="0.5"),
        dict(parts="slice", field="nope"),
    ]
    q.extend((scene_path(**s), "scene") for s in scenes)
    return q


# Exchanges whose answer the standalone-viewer change alters on purpose. Each
# maps to the statuses accepted besides the recorded one. Tighten to the new
# status alone once the change lands (tasks 5.2).
ALLOWED_CHANGES = {
    "/api/meta": {404},
    "/api/meta?case=nope": {404},
}


def contained(recorded, actual, where=""):
    """Differences where *actual* fails to contain *recorded* (extra keys ok)."""
    if isinstance(recorded, dict):
        if not isinstance(actual, dict):
            return [f"{where}: expected an object, got {type(actual).__name__}"]
        out = []
        for key, value in recorded.items():
            if key not in actual:
                out.append(f"{where}.{key}: missing")
            else:
                out.extend(contained(value, actual[key], f"{where}.{key}"))
        return out
    if isinstance(recorded, list):
        if not isinstance(actual, list) or len(actual) != len(recorded):
            return [f"{where}: expected {len(recorded)} items, got "
                    f"{len(actual) if isinstance(actual, list) else type(actual).__name__}"]
        out = []
        for i, (r, a) in enumerate(zip(recorded, actual)):
            out.extend(contained(r, a, f"{where}[{i}]"))
        return out
    if recorded != actual:
        return [f"{where}: {recorded!r} != {actual!r}"]
    return []


def compare(recorded, actual):
    """Differences between one recorded exchange and its replay."""
    path = recorded["path"]
    if actual["status"] != recorded["status"]:
        if actual["status"] in ALLOWED_CHANGES.get(path, ()):
            return []
        return [f"status {recorded['status']} -> {actual['status']}"]
    if recorded["status"] != 200:
        return []  # error texts may be reworded; the status is the contract
    if recorded["kind"] == "json":
        return contained(recorded["json"], actual["json"])
    if recorded["kind"] == "scene":
        diffs = contained(recorded["header"], actual["header"], "header")
        for name, sha in recorded["buffers"].items():
            if actual["buffers"].get(name) != sha:
                diffs.append(f"buffer {name}: "
                             + ("missing" if name not in actual["buffers"] else "bytes differ"))
        return diffs
    if actual.get("sha256") != recorded["sha256"]:
        return ["body bytes differ"]
    return []
