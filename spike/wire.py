"""vtkPolyData -> a binary the browser hands straight to three.js.

The wire format is deliberately not glTF. glTF's material model has no concept
of a scientific scalar field, so carrying `U` magnitude per vertex means either
baking vertex colours server-side (which kills client-side re-colouring -- the
one interaction that must never cost a round trip) or smuggling the values
through a custom accessor and hoping the loader keeps them. Instead each part
ships as raw typed arrays that map 1:1 onto `THREE.BufferAttribute`:

    "FVS1" | uint32 header length | header JSON (utf-8) | buffer blobs

The header names every buffer by byte offset and length inside the blob region,
so the client does one fetch, one `arrayBuffer()`, and zero parsing. Scalars
travel raw, with their range in the header; the colour map is a 256-entry LUT
texture on the client, so re-colouring and rescaling are pure GPU work.

Everything here reads the *output of FoamPipeline's existing filters* -- the
point of the spike is that the server-side VTK work is already done and is
renderer-agnostic.
"""

import json
import struct

import numpy as np
import vtk
from vtkmodules.util.numpy_support import vtk_to_numpy

MAGIC = b"FVS1"
ALIGN = 4  # keep every buffer 4-byte aligned so typed-array views are valid


class Part:
    """One drawable: vertices, an index buffer, and the scalars to colour by."""

    def __init__(self, name, mode, attributes, index):
        self.name = name
        self.mode = mode  # "triangles" | "lines"
        self.attributes = attributes  # name -> (N, C) float32 array
        self.index = index  # flat uint32 array

    @property
    def n_vertices(self):
        pos = self.attributes.get("position")
        return 0 if pos is None else len(pos)

    @property
    def n_primitives(self):
        per = 3 if self.mode == "triangles" else 2
        return 0 if self.index is None else len(self.index) // per


def _f32(arr, components):
    """Contiguous float32 view shaped (N, components) -- what a BufferAttribute wants."""
    out = np.ascontiguousarray(arr, dtype=np.float32)
    return out.reshape(-1, components)


def _triangle_indices(polydata):
    """Flat uint32 triangle indices. Assumes the input is already triangulated."""
    polys = polydata.GetPolys()
    if polys is None or polys.GetNumberOfCells() == 0:
        return np.zeros(0, dtype=np.uint32)
    offsets = vtk_to_numpy(polys.GetOffsetsArray())
    conn = vtk_to_numpy(polys.GetConnectivityArray())
    strides = np.unique(np.diff(offsets))
    if not (strides.size == 1 and strides[0] == 3):
        raise ValueError(f"expected triangles, got cell strides {strides}")
    return conn.astype(np.uint32, copy=False)


def _line_indices(polydata):
    """Polylines -> flat uint32 pairs, for THREE.LineSegments.

    three.js has no polyline primitive that survives an index buffer, so an
    N-point streamline becomes N-1 independent segments. Costs one extra index
    per point and keeps the whole scene in a single draw call.
    """
    lines = polydata.GetLines()
    if lines is None or lines.GetNumberOfCells() == 0:
        return np.zeros(0, dtype=np.uint32)
    offsets = vtk_to_numpy(lines.GetOffsetsArray())
    conn = vtk_to_numpy(lines.GetConnectivityArray())
    picks = [np.arange(s, e - 1) for s, e in zip(offsets[:-1], offsets[1:]) if e - s >= 2]
    if not picks:
        return np.zeros(0, dtype=np.uint32)
    at = np.concatenate(picks)
    return np.column_stack([conn[at], conn[at + 1]]).ravel().astype(np.uint32, copy=False)


def surface_part(name, polydata, scalar_array, normals=True):
    """A lit, scalar-coloured triangle mesh.

    Triangulated here because the OpenFOAM reader emits quads and general
    polygons for boundary patches, and WebGL draws triangles only. Normals are
    computed server-side (SplittingOff, so the point count stays 1:1 with the
    scalars) -- three.js `computeVertexNormals()` would do it in JS on the main
    thread, which is exactly the work we are trying to keep off the client.
    """
    tri = vtk.vtkTriangleFilter()
    tri.SetInputData(polydata)
    tri.PassLinesOff()
    tri.PassVertsOff()
    tri.Update()
    src = tri
    # Skip the normals pass when the input already has them (the isosurface
    # arrives via vtkPolyDataNormals, and the triangle filter carries them
    # through) -- recomputing would be pure waste.
    if normals and tri.GetOutput().GetPointData().GetNormals() is None:
        norm = vtk.vtkPolyDataNormals()
        norm.SetInputConnection(tri.GetOutputPort())
        norm.SplittingOff()
        norm.ConsistencyOn()
        norm.ComputePointNormalsOn()
        norm.ComputeCellNormalsOff()
        src = norm
    src.Update()
    out = src.GetOutput()
    if out.GetNumberOfPoints() == 0:
        return None

    attrs = {"position": _f32(vtk_to_numpy(out.GetPoints().GetData()), 3)}
    nrm = out.GetPointData().GetNormals()
    if nrm is not None:
        attrs["normal"] = _f32(vtk_to_numpy(nrm), 3)
    scalars = out.GetPointData().GetArray(scalar_array)
    if scalars is not None:
        attrs["scalar"] = _f32(vtk_to_numpy(scalars), 1)
    return Part(name, "triangles", attrs, _triangle_indices(out))


def line_part(name, polydata, scalar_array):
    """Streamlines as coloured line segments."""
    if polydata.GetNumberOfPoints() == 0:
        return None
    attrs = {"position": _f32(vtk_to_numpy(polydata.GetPoints().GetData()), 3)}
    scalars = polydata.GetPointData().GetArray(scalar_array)
    if scalars is not None:
        attrs["scalar"] = _f32(vtk_to_numpy(scalars), 1)
    index = _line_indices(polydata)
    if index.size == 0:
        return None
    return Part(name, "lines", attrs, index)


def pack(parts, meta):
    """Serialise *parts* + a JSON *meta* dict into the wire format."""
    blobs, header_parts, cursor = [], [], 0

    def add(buf):
        nonlocal cursor
        raw = buf.tobytes()
        pad = (-len(raw)) % ALIGN
        entry = {"offset": cursor, "length": len(raw)}
        blobs.append(raw + b"\0" * pad)
        cursor += len(raw) + pad
        return entry

    for part in parts:
        if part is None:
            continue
        attributes = {}
        for attr, values in part.attributes.items():
            entry = add(values)
            entry["components"] = values.shape[1]
            entry["type"] = "f32"
            attributes[attr] = entry
        index = add(part.index)
        index["type"] = "u32"
        header_parts.append({
            "name": part.name,
            "mode": part.mode,
            "attributes": attributes,
            "index": index,
            "counts": {"vertices": part.n_vertices, "primitives": part.n_primitives},
        })

    header = dict(meta)
    header["parts"] = header_parts
    header["payloadBytes"] = cursor
    raw_header = json.dumps(header, separators=(",", ":")).encode()
    pad = (-len(raw_header)) % ALIGN
    return b"".join(
        [MAGIC, struct.pack("<I", len(raw_header) + pad), raw_header, b" " * pad, *blobs]
    )


def unpack(blob):
    """Decode what pack() wrote -- used by the tests, and as the format's spec."""
    if blob[:4] != MAGIC:
        raise ValueError("not a FoamViz scene payload")
    (header_len,) = struct.unpack("<I", blob[4:8])
    header = json.loads(blob[8:8 + header_len])
    base = 8 + header_len
    dtypes = {"f32": np.float32, "u32": np.uint32}

    def view(entry):
        start = base + entry["offset"]
        arr = np.frombuffer(blob[start:start + entry["length"]], dtype=dtypes[entry["type"]])
        return arr.reshape(-1, entry["components"]) if "components" in entry else arr

    parts = []
    for spec in header["parts"]:
        parts.append({
            "name": spec["name"],
            "mode": spec["mode"],
            "counts": spec["counts"],
            "attributes": {k: view(v) for k, v in spec["attributes"].items()},
            "index": view(spec["index"]),
        })
    return header, parts
