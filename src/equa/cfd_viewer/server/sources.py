"""Where cases come from: one or more directories, scanned on demand.

A *case source* lists the cases it can see and turns a ``?case=`` key into a
directory VTK can read. Phase 1 has one kind, :class:`DirectorySource`; the
hosted viewer's uploads (openspec standalone-viewer, design D7) become a second
kind with the same two methods.

Listing is deliberately cheap -- it runs on every ``/api/cases`` and every
``?case=`` lookup, so a case written after start-up shows up without a restart
-- and never constructs a VTK reader: what it reports (label, cell count,
decomposed) comes from ``metadata.json`` and the mesh header alone.

Identity
--------
A case has an **id**, a short hash of its root and its path below that root:
stable across restarts and rescans, unique across roots, and free of host paths.
``?case=`` takes an id, or a directory **name** when exactly one listed case has
it -- which is what keeps the cfd-backend cockpit's ``/viz/?case=<uuid-dir>``
working. A name that matches several cases, or nothing, is an error the HTTP
layer turns into a 404; the old behaviour of opening the first case instead is
gone on purpose.

Discovery depth
---------------
Server mode finds cases the way cfd-backend does: its ``cfd_cases.find`` takes
every directory below ``CFD_HOME`` that has ``system/controlDict``, at any
depth (``rglob``), and the cockpit links a case by its directory name. So the
viewer searches every level too, bounded by ``depth`` -- directory levels below
a root, :data:`SERVER_SCAN_DEPTH` by default -- so a stray deep tree cannot
slow every request. Test-suite cases, for one, live in ``CFD_HOME/tests/<name>``,
which the old one-level scan never saw (found at the phase-1 gate, 2026-10-22).

A root is always scanned, and listed when it is itself a case (``find_cases``
did the same). Below it, a case directory is not descended into -- a case holds
no cases, and pruning there is what keeps the walk cheap -- and neither are
``processor*`` or numeric time directories. Hidden directories are searched,
as the backend searches them.

Names must be unique for the cockpit's links to work; the backend relies on
that as well (``/api/wd/ls/<name>`` takes the first match). Where they are not,
a name is ambiguous here and gets a 404 listing the ids, rather than the
backend's silent first match.

The backend also lists "bare" cases (``building.opf`` + ``geometry/building.obj``,
not yet meshed); there is nothing to view in those, so they are left out.
"""

import gzip
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..foamviz.case import is_decomposed

log = logging.getLogger("cfdviewer.sources")

# Directory levels below a root searched in server mode. CFD_HOME/<case> and
# CFD_HOME/tests/<case> need 1 and 2; 4 leaves room for deeper grouping.
SERVER_SCAN_DEPTH = 4

# Enough to hold an OpenFOAM FoamFile header, note line included.
_HEADER_BYTES = 4096
_NCELLS = re.compile(rb"nCells:\s*(\d+)")


_warned = set()


def _warn_once(directory, exc):
    if str(directory) not in _warned:
        _warned.add(str(directory))
        log.warning("cannot search %s for cases (%s); cases below it are not listed",
                    directory, exc.strerror or exc)


class CaseNotFound(KeyError):
    """No listed case has this id or name."""


class AmbiguousCase(KeyError):
    """Several listed cases share this name; an id is needed."""


@dataclass(frozen=True)
class CaseRef:
    id: str
    name: str
    label: str
    path: str  # relative to its root, POSIX separators; "." for the root itself
    directory: Path
    decomposed: bool
    cells: int | None

    def to_json(self):
        return {"id": self.id, "name": self.name, "label": self.label,
                "path": self.path, "decomposed": self.decomposed, "cells": self.cells}


def is_case(directory):
    # On Python 3.12, Path.is_file() RAISES PermissionError for a path inside a
    # directory the server cannot search (it ignores only ENOENT-like errors);
    # 3.14 answers False, as os.path.isfile always has (both checked
    # 2026-10-22). The package supports 3.12, so guard: one locked directory
    # under CFD_HOME must not turn the listing into a 500 there.
    try:
        return (directory / "system" / "controlDict").is_file()
    except OSError as exc:
        _warn_once(directory, exc)
        return False


def case_id(root, relative):
    digest = hashlib.sha1(f"{Path(root).resolve()}\0{relative}".encode()).hexdigest()
    return digest[:12]


def _metadata(directory):
    """The IDA ICE backend's ``metadata.json``, or {} if absent or unreadable."""
    try:
        data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _header_cells(owner_dir):
    """``nCells`` from the ``note`` in an ``owner`` header, or None.

    OpenFOAM writes ``note "nPoints:… nCells:… nFaces:…"`` into the header of
    ``polyMesh/owner``; reading the first few kB gives the cell count of a
    multi-GB mesh without parsing it. ASCII meshes written by some tools carry
    no note, hence None.
    """
    for name, opener in (("owner", open), ("owner.gz", gzip.open)):
        path = owner_dir / name
        try:
            if not path.is_file():
                continue
            with opener(path, "rb") as f:
                head = f.read(_HEADER_BYTES)
        except OSError:
            return None
        match = _NCELLS.search(head)
        return int(match.group(1)) if match else None
    return None


def _cells(directory, metadata, decomposed):
    """Cell count without loading the mesh, or None when it cannot be known.

    The mesh header first; for a decomposed case whose root has no mesh, the
    sum over ``processor*``. Then ``case_info.ncells`` from ``metadata.json``.
    ``N-CELLS`` is never used: it is observed stale (0) on real cases.
    """
    cells = _header_cells(directory / "constant" / "polyMesh")
    if cells is None and decomposed:
        counts = [_header_cells(p / "constant" / "polyMesh")
                  for p in sorted(directory.glob("processor*"))]
        if counts and None not in counts:
            cells = sum(counts)
    if cells is None:
        info = metadata.get("case_info")
        value = info.get("ncells") if isinstance(info, dict) else None
        if isinstance(value, int) and value > 0:
            cells = value
    return cells


def _skip(directory):
    name = directory.name
    if name.startswith("processor"):
        return True
    try:
        float(name)  # a time directory
        return True
    except ValueError:
        return False


class DirectorySource:
    """Cases under one or more directory roots, rescanned on every call."""

    def __init__(self, roots, depth=SERVER_SCAN_DEPTH):
        self.roots = [Path(r) for r in roots]
        self.depth = depth

    def describe(self):
        return ", ".join(str(r) for r in self.roots)

    def _walk(self, root):
        """Case directories under *root*, root first, then sorted, depth-bounded."""
        if not root.is_dir():
            return
        if is_case(root):
            yield root
        level = [root]
        for _ in range(self.depth):
            below = []
            for parent in level:
                try:
                    children = sorted(p for p in parent.iterdir() if p.is_dir())
                except OSError as exc:
                    # Skip it and list the rest -- but say so: a directory the
                    # server's user cannot read looks exactly like "no cases"
                    # otherwise. Once per directory, not on every rescan.
                    _warn_once(parent, exc)
                    continue
                for child in children:
                    if _skip(child):
                        continue
                    if is_case(child):
                        yield child
                    else:
                        below.append(child)
            level = below

    def _found(self):
        """``(root, directory, relative, id)`` for every case: names only, no I/O
        beyond the directory walk."""
        for root in self.roots:
            for directory in self._walk(root):
                relative = (directory.relative_to(root).as_posix()
                            if directory != root else ".")
                yield root, directory, relative, case_id(root, relative)

    def _ref(self, root, directory, relative, ident):
        metadata = _metadata(directory)
        decomposed = is_decomposed(directory)
        label = metadata.get("NAME")
        return CaseRef(
            id=ident,
            name=directory.name,
            label=label if isinstance(label, str) and label.strip() else directory.name,
            path=relative,
            directory=directory,
            decomposed=decomposed,
            cells=_cells(directory, metadata, decomposed),
        )

    def list(self):
        """Every case, with its label, cell count and layout."""
        return [self._ref(*found) for found in self._found()]

    def names(self):
        """Directory names, in listing order (the original ``/api/cases``)."""
        return [directory.name for _, directory, _, _ in self._found()]

    def resolve(self, key):
        """The case an id or a unique name denotes.

        Only the matching case's metadata and mesh header are read, so a lookup
        costs a directory walk, not a full listing. Raises
        :class:`CaseNotFound` or :class:`AmbiguousCase`.
        """
        found = list(self._found())
        for entry in found:
            if entry[3] == key:
                return self._ref(*entry)
        named = [entry for entry in found if entry[1].name == key]
        if len(named) == 1:
            return self._ref(*named[0])
        if named:
            raise AmbiguousCase(key, [entry[3] for entry in named])
        raise CaseNotFound(key)
