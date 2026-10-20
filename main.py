#!/usr/bin/env python3
"""cfd-viewer from a source checkout -- a shim over ``python -m equa.cfd_viewer``.

    python main.py --data ./data --port 5003

Kept because cfd-backend's cfd-viz image runs exactly
``python main.py --server --data "$CFD_HOME" --host 0.0.0.0 --port "$VIZ_PORT"``
from a clone of this repository (openspec standalone-viewer, design D9/D11).
It goes when cfd-backend builds from this repository's own Dockerfile.

The default ``--data`` stays ``<checkout>/data``, as it always was here.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from equa.cfd_viewer.__main__ import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not any(a == "--data" or a.startswith("--data=") for a in argv):
        argv = ["--data", str(HERE / "data"), *argv]
    main(argv)
