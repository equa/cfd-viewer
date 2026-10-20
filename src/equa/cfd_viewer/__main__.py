"""cfd-viewer -- browse OpenFOAM results in a web browser.

    cfd-viewer --data ./data --port 5003
    python -m equa.cfd_viewer --data ./data --port 5003

VTK extracts server-side (``foamviz/`` + ``server/``); the browser draws with
React and three.js (``web/``).

The original Trame + vtk.js front end was mothballed on 2026-09-12 and lives on
the frozen ``trame`` branch.
"""

import argparse
import logging

from .server.sources import DirectorySource

log = logging.getLogger("cfdviewer")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cfd-viewer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data",
        action="append",
        help="directory holding OpenFOAM cases, or a single case directory; "
             "repeat for several roots (default: ./data)",
    )
    parser.add_argument(
        "--scan-depth",
        type=int,
        default=1,
        metavar="N",
        help="directory levels below each root to look for cases (default 1: "
             "the root and its immediate children, as the cockpit expects)",
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
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)

    if args.scan_depth < 0:
        parser.error("--scan-depth must be 0 or more")
    source = DirectorySource(args.data or ["data"], depth=args.scan_depth)
    cases = source.list()
    if not cases:
        msg = (
            f"no OpenFOAM case found under {source.describe()!r} "
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
                 len(cases), source.describe(), ", ".join(c.name for c in cases))

    from .server.app import serve

    serve(source, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
