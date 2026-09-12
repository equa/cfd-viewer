#!/usr/bin/env python3
"""cfd-viewer -- browse OpenFOAM results in a web browser.

    python main.py --data ./data --port 5003

VTK extracts server-side (``foamviz/`` + ``server/``); the browser draws with
React and three.js (``web/``).

The original Trame + vtk.js front end was mothballed on 2026-09-12 and lives on
the frozen ``trame`` branch.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from foamviz.case import find_cases

log = logging.getLogger("cfdviewer")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data",
        default=str(Path(__file__).parent / "data"),
        help="directory holding OpenFOAM cases (or a single case directory)",
    )
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--server",
        action="store_true",
        help="service mode: tolerate an empty --data root (cases are picked up "
             "as they appear) and never open a browser. Required for the "
             "container: without it an empty CFD_HOME is fatal and the "
             "service restart-loops",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)

    cases = find_cases(args.data)
    if not cases:
        msg = (
            f"no OpenFOAM case found under {args.data!r} "
            "(a case is a directory containing system/controlDict)"
        )
        # Interactive use: a mistyped --data is worth failing on. Service mode
        # keeps running and discovers cases on demand as they appear -- a solve
        # writing its first time step must not need a viewer restart.
        if not args.server:
            parser.error(msg)
        log.warning("%s; serving empty, will pick up cases on demand", msg)
    else:
        log.info("%d case(s) under %s: %s",
                 len(cases), args.data, ", ".join(c.name for c in cases))

    from server.app import serve

    serve(args.data, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
