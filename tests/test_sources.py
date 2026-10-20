"""Case discovery, ids, labels and lookups (openspec standalone-viewer, case-sources).

Runs under pytest. The directory-tree tests build synthetic cases -- a case is
just ``system/controlDict`` to discovery, plus whatever metadata a test needs --
so they take milliseconds; the HTTP tests at the end open the real demo case.
"""

import asyncio
import gzip
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from equa.cfd_viewer.foamviz.case import find_cases
from equa.cfd_viewer.server.app import make_app
from equa.cfd_viewer.server.sources import (
    AmbiguousCase,
    CaseNotFound,
    DirectorySource,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "hotRoom"

OWNER_HEADER = """FoamFile
{{
    format      binary;
    class       labelList;
    note        "nPoints:10  nCells:{cells}  nFaces:100  nInternalFaces:80";
    location    "constant/polyMesh";
    object      owner;
}}
"""


def make_case(directory, *, cells=None, gz=False, metadata=None, times=("0",)):
    """A directory discovery treats as a case, with optional mesh header and metadata."""
    directory = Path(directory)
    (directory / "system").mkdir(parents=True)
    (directory / "system" / "controlDict").write_text("FoamFile {}\n")
    for t in times:
        (directory / t).mkdir()
    if cells is not None:
        mesh = directory / "constant" / "polyMesh"
        mesh.mkdir(parents=True)
        header = OWNER_HEADER.format(cells=cells).encode()
        if gz:
            with gzip.open(mesh / "owner.gz", "wb") as f:
                f.write(header)
        else:
            (mesh / "owner").write_bytes(header)
    if metadata is not None:
        (directory / "metadata.json").write_text(json.dumps(metadata))
    return directory


def names(source):
    return [ref.name for ref in source.list()]


# -- discovery ---------------------------------------------------------------


def test_nested_case_found_with_depth(tmp_path):
    make_case(tmp_path / "projects" / "officeA" / "run1")
    assert names(DirectorySource([tmp_path], depth=3)) == ["run1"]


def test_server_mode_scans_one_level(tmp_path):
    make_case(tmp_path / "e2614af7")
    make_case(tmp_path / "old" / "a1b2c3d4")
    assert names(DirectorySource([tmp_path])) == ["e2614af7"]


def test_root_that_is_a_case(tmp_path):
    case = make_case(tmp_path / "hotRoom")
    refs = DirectorySource([case]).list()
    assert [(r.name, r.path) for r in refs] == [("hotRoom", ".")]


def test_depth_is_bounded(tmp_path):
    make_case(tmp_path / "a" / "b" / "c" / "deep")
    assert names(DirectorySource([tmp_path], depth=3)) == []
    assert names(DirectorySource([tmp_path], depth=4)) == ["deep"]


def test_several_roots(tmp_path):
    make_case(tmp_path / "A" / "run1")
    make_case(tmp_path / "B" / "run2")
    assert names(DirectorySource([tmp_path / "A", tmp_path / "B"])) == ["run1", "run2"]


def test_skips_hidden_processor_and_time_directories(tmp_path):
    make_case(tmp_path / ".trash" / "hidden")
    make_case(tmp_path / "processor0" / "inside")
    make_case(tmp_path / "1730" / "timedir")
    make_case(tmp_path / "visible")
    assert names(DirectorySource([tmp_path], depth=3)) == ["visible"]


def test_does_not_descend_into_a_case(tmp_path):
    outer = make_case(tmp_path / "outer")
    make_case(outer / "nested")
    assert names(DirectorySource([tmp_path], depth=3)) == ["outer"]


def test_new_case_appears_without_restart(tmp_path):
    source = DirectorySource([tmp_path])
    assert source.list() == []
    make_case(tmp_path / "late")
    assert names(source) == ["late"]


def test_depth_one_matches_find_cases():
    """Default discovery is exactly the old one-level scan, on the real data/."""
    data = ROOT / "data"
    assert DirectorySource([data]).names() == [p.name for p in find_cases(data)]


def test_missing_root_lists_nothing(tmp_path):
    assert DirectorySource([tmp_path / "absent"]).list() == []


# -- identity ----------------------------------------------------------------


def test_ids_are_stable_and_hide_the_path(tmp_path):
    make_case(tmp_path / "runs" / "run1")
    first = DirectorySource([tmp_path], depth=2).list()[0]
    again = DirectorySource([tmp_path], depth=2).list()[0]
    assert first.id == again.id
    assert first.path == "runs/run1"
    assert str(tmp_path) not in first.id and "/" not in first.id


def test_same_name_in_two_places(tmp_path):
    a = make_case(tmp_path / "A" / "run1")
    b = make_case(tmp_path / "B" / "run1")
    source = DirectorySource([tmp_path / "A", tmp_path / "B"])
    ids = [r.id for r in source.list()]
    assert len(set(ids)) == 2
    assert [source.resolve(i).directory for i in ids] == [a, b]
    with pytest.raises(AmbiguousCase) as exc:
        source.resolve("run1")
    assert sorted(exc.value.args[1]) == sorted(ids)


def test_unique_name_resolves(tmp_path):
    case = make_case(tmp_path / "e2614af7")
    assert DirectorySource([tmp_path]).resolve("e2614af7").directory == case


def test_unknown_key(tmp_path):
    make_case(tmp_path / "run1")
    with pytest.raises(CaseNotFound):
        DirectorySource([tmp_path]).resolve("nope")


# -- description -------------------------------------------------------------


def test_ida_ice_case(tmp_path):
    """Label from metadata NAME, cells from the owner note, N-CELLS ignored."""
    make_case(tmp_path / "e2614af7-3602", cells=15757775,
              metadata={"NAME": "case-4", "N-CELLS": 0, "case_info": {"ncells": 1}})
    ref = DirectorySource([tmp_path]).list()[0]
    assert (ref.label, ref.cells, ref.decomposed) == ("case-4", 15757775, False)


def test_gzipped_owner(tmp_path):
    make_case(tmp_path / "run1", cells=1234, gz=True)
    assert DirectorySource([tmp_path]).list()[0].cells == 1234


def test_case_without_metadata_or_note(tmp_path):
    case = make_case(tmp_path / "run1")
    mesh = case / "constant" / "polyMesh"
    mesh.mkdir(parents=True)
    (mesh / "owner").write_text("FoamFile { format ascii; object owner; }\n3\n(0 0 1)\n")
    ref = DirectorySource([tmp_path]).list()[0]
    assert (ref.label, ref.cells) == ("run1", None)


def test_cells_from_case_info_when_no_header(tmp_path):
    make_case(tmp_path / "run1", metadata={"N-CELLS": 999, "case_info": {"ncells": 4321}})
    assert DirectorySource([tmp_path]).list()[0].cells == 4321


def test_stale_n_cells_alone_is_not_used(tmp_path):
    make_case(tmp_path / "run1", metadata={"N-CELLS": 999})
    assert DirectorySource([tmp_path]).list()[0].cells is None


def test_blank_name_falls_back_to_directory(tmp_path):
    make_case(tmp_path / "run1", metadata={"NAME": "  "})
    assert DirectorySource([tmp_path]).list()[0].label == "run1"


def test_unreadable_metadata_is_ignored(tmp_path):
    case = make_case(tmp_path / "run1")
    (case / "metadata.json").write_text("{not json")
    assert DirectorySource([tmp_path]).list()[0].label == "run1"


def test_decomposed_case(tmp_path):
    """Newest time only in processor*: decomposed, cells summed over processors."""
    case = make_case(tmp_path / "par", times=("0",))
    for i, n in enumerate((100, 250)):
        proc = case / f"processor{i}"
        (proc / "90").mkdir(parents=True)
        mesh = proc / "constant" / "polyMesh"
        mesh.mkdir(parents=True)
        (mesh / "owner").write_text(OWNER_HEADER.format(cells=n))
    ref = DirectorySource([tmp_path]).list()[0]
    assert (ref.decomposed, ref.cells) == (True, 350)


def test_json_shape(tmp_path):
    make_case(tmp_path / "run1", cells=7)
    entry = DirectorySource([tmp_path]).list()[0].to_json()
    assert set(entry) == {"id", "name", "label", "path", "decomposed", "cells"}


# -- HTTP --------------------------------------------------------------------


def _get(source, *paths):
    """[(status, body), ...] for GETs against an app over *source*."""

    async def run():
        async with TestClient(TestServer(make_app(source))) as client:
            out = []
            for path in paths:
                response = await client.get(path)
                body = (await response.json() if response.content_type == "application/json"
                        else await response.text())
                out.append((response.status, body))
            return out

    return asyncio.run(run())


def test_cases_endpoint_keeps_names_and_adds_entries(tmp_path):
    make_case(tmp_path / "run1", cells=7, metadata={"NAME": "Office"})
    [(status, body)] = _get(DirectorySource([tmp_path]), "/api/cases")
    assert status == 200
    assert body["cases"] == ["run1"]
    assert body["entries"][0]["label"] == "Office"


def test_unknown_and_missing_case_are_404(tmp_path):
    make_case(tmp_path / "run1")
    (s1, t1), (s2, t2) = _get(DirectorySource([tmp_path]),
                              "/api/meta?case=nope", "/api/meta")
    assert (s1, s2) == (404, 404)
    assert "no case 'nope'" in t1 and "no case given" in t2


def test_ambiguous_name_is_404_with_ids(tmp_path):
    make_case(tmp_path / "A" / "run1")
    make_case(tmp_path / "B" / "run1")
    source = DirectorySource([tmp_path / "A", tmp_path / "B"])
    [(status, text)] = _get(source, "/api/meta?case=run1")
    assert status == 404 and "ambiguous" in text
    assert all(ref.id in text for ref in source.list())


@pytest.mark.skipif(not (DEMO / "constant" / "polyMesh").is_dir(),
                    reason="demo case not built")
def test_open_by_id_and_by_name(tmp_path):
    (tmp_path / "hotRoom").symlink_to(DEMO)
    source = DirectorySource([tmp_path])
    ident = source.list()[0].id
    (s1, by_id), (s2, by_name) = _get(source, f"/api/meta?case={ident}",
                                      "/api/meta?case=hotRoom")
    assert (s1, s2) == (200, 200)
    assert by_id["case"] == "hotRoom" and by_id["caseId"] == ident
    assert by_name["caseId"] == ident and by_name["caseLabel"] == "hotRoom"
