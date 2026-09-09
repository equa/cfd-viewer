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


def _extra_attrs(out, extras):
    """Pull named 1-component point arrays out as float32 attributes.

    Used for anything a part needs beyond position/normal/scalar -- currently
    just the streamlines' ``travel`` (see TRAVEL_ARRAY in server/scene.py). They
    go through VTK's own point data rather than being appended afterwards, which
    is what makes them survive a filter that generates new points: the tube
    filter interpolates them onto the tube it builds, so a tubed streamline
    animates exactly like a line one.
    """
    found = {}
    for attribute, array_name in (extras or {}).items():
        array = out.GetPointData().GetArray(array_name)
        if array is not None:
            found[attribute] = _f32(vtk_to_numpy(array), 1)
    return found


def _de_index_cells(out, attrs, index, scalar_array):
    """Turn an indexed mesh into per-triangle vertices carrying CELL scalars.

    "True cell values" means flat, un-interpolated colour per cell. A GPU can
    only interpolate what sits on vertices, and an indexed mesh SHARES vertices
    between neighbouring cells, so there is nowhere to put a per-cell value:
    this is why the toggle silently did nothing for so long -- the server baked
    the cell array and the wire only ever read point data.

    The fix is to stop sharing: emit three vertices per triangle and give each
    the triangle's own cell value. Costs 3x the vertex data, which is why it
    happens only when the toggle is on.

    (GLSL's `flat` qualifier is not a shortcut here. It takes the provoking
    vertex's value, which on a shared-vertex mesh is an arbitrary neighbour's,
    not the cell's.)
    """
    cells = out.GetCellData().GetArray(scalar_array)
    if cells is None or index.size == 0:
        return attrs, index
    values = vtk_to_numpy(cells)
    n_tris = index.size // 3
    if values.shape[0] != n_tris:
        return attrs, index          # not one value per triangle: leave it alone
    expanded = {
        key: np.ascontiguousarray(arr[index], dtype=np.float32)
        for key, arr in attrs.items()
    }
    expanded["scalar"] = _f32(np.repeat(values, 3), 1)
    return expanded, np.arange(index.size, dtype=np.uint32)


def surface_part(name, polydata, scalar_array, normals=True, extras=None,
                 cell_scalars=False):
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
    attrs.update(_extra_attrs(out, extras))
    index = _triangle_indices(out)
    if cell_scalars:
        attrs, index = _de_index_cells(out, attrs, index, scalar_array)
    return Part(name, "triangles", attrs, index)


def line_part(name, polydata, scalar_array, extras=None):
    """Streamlines as coloured line segments."""
    if polydata.GetNumberOfPoints() == 0:
        return None
    attrs = {"position": _f32(vtk_to_numpy(polydata.GetPoints().GetData()), 3)}
    scalars = polydata.GetPointData().GetArray(scalar_array)
    if scalars is not None:
        attrs["scalar"] = _f32(vtk_to_numpy(scalars), 1)
    attrs.update(_extra_attrs(polydata, extras))
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
