#!/usr/bin/env python3
"""cfd-viewer -- browse OpenFOAM results in a web browser.

Two front ends over one VTK pipeline (see CLAUDE.md "Two front ends"):

    python main.py --data ./data              # React + three.js (default)
    python main.py --data ./data --trame      # FoamViz, the Trame/vtk.js app

The pipeline (``foamviz/case.py``, ``pipeline.py``, ``colors.py``) is shared and
maintained. The Trame UI (``foamviz/app.py``) is resting: kept working and
kept honest about pipeline changes, but no longer where new UX work goes.
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
        "--trame",
        action="store_true",
        help="serve the resting Trame/vtk.js front end instead of the three.js client",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="do not open a browser; just serve (Trame only -- the three.js "
             "client never opens one)",
    )
    args = parser.parse_args()

    # WARNING for the Trame path, deliberately. trame_client/trame_server log a
    # line per widget attribute at INFO, which is tens of thousands of lines
    # while the UI is built -- and if the caller has piped stdout without
    # reading it (tests/browser_check.py does), the 64 kB pipe buffer fills and
    # the process BLOCKS BEFORE IT LISTENS. That reads as "server never came
    # up", which is a long way from "the log level is too low".
    logging.basicConfig(
        level=logging.WARNING if args.trame else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.setLevel(logging.INFO)
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

    if args.trame:
        from foamviz.app import FoamViz

        # Trame defaults to 8080; keep that rather than the viewer's 5003, so an
        # existing --trame invocation behaves as it always did.
        port = args.port if args.port != 5003 else 8080
        FoamViz(args.data).start(port=port, host=args.host,
                                 open_browser=not args.server)
        return

    from server.app import serve

    serve(args.data, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
