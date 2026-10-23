"""cfd-viewer -- browse OpenFOAM results in a web browser.

    cfd-viewer --data ./data --port 5003
    python -m equa.cfd_viewer --data ./data --port 5003

VTK extracts server-side (``foamviz/`` + ``server/``); the browser draws with
React and three.js (``web/``).

Every option can also come from the environment, as platform services are
configured (equa-deployment); a command-line option wins over its variable:

    CASE_ROOTS   --data, several roots separated by os.pathsep (":" on Linux)
    SCAN_DEPTH   --scan-depth
    PORT         --port
    ROOT_PATH    --root-path, the path the edge serves this under (/viewer)

The original Trame + vtk.js front end was mothballed on 2026-09-12 and lives on
the frozen ``trame`` branch.
"""

import argparse
import logging
import os

from .server.sources import SERVER_SCAN_DEPTH, DirectorySource

log = logging.getLogger("cfdviewer")


def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise SystemExit(f"cfd-viewer: {name}={raw!r} is not an integer")


def _env_roots():
    raw = os.environ.get("CASE_ROOTS", "").strip()
    return [r for r in raw.split(os.pathsep) if r] or None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cfd-viewer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data",
        action="append",
        help="directory holding OpenFOAM cases, or a single case directory; "
             "repeat for several roots (default: $CASE_ROOTS, else ./data)",
    )
    parser.add_argument(
        "--scan-depth",
        type=int,
        default=_env_int("SCAN_DEPTH", SERVER_SCAN_DEPTH),
        metavar="N",
        help="directory levels below each root to search for cases (default "
             f"{SERVER_SCAN_DEPTH}; cases are found at any level up to it, as "
             "cfd-backend finds them, and never inside another case)",
    )
    parser.add_argument("--port", type=int, default=_env_int("PORT", 5003))
    # Not from $HOST: equa-deployment uses HOST for the public domain, and zsh
    # sets it to the machine name.
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--root-path",
        default=os.environ.get("ROOT_PATH", ""),
        metavar="PATH",
        help="serve under PATH (e.g. /viewer) as well as at the root, for an "
             "edge proxy that does not strip it (default: $ROOT_PATH, else none)",
    )
    parser.add_argument(
        "--mode",
        choices=["server"],
        default="server",
        help="run mode (default and, for now, only: server). Server mode is what "
             "the cfd-backend container runs: an empty --data root is not fatal "
             "-- cases are picked up as they appear, so an empty CFD_HOME does "
             "not restart-loop the service",
    )
    parser.add_argument(
        "--server",
        dest="mode",
        action="store_const",
        const="server",
        help="alias for --mode server, kept for the cfd-viz image's command line",
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
    source = DirectorySource(args.data or _env_roots() or ["data"], depth=args.scan_depth)
    cases = source.list()
    if not cases:
        msg = (
            f"no OpenFOAM case found under {source.describe()!r} "
            "(a case is a directory containing system/controlDict)"
        )
        # Server mode keeps running and discovers cases on demand as they
        # appear -- a solve writing its first time step must not need a viewer
        # restart. (The local mode of phase 2 will rather ask for a folder.)
        log.warning("%s; serving empty, will pick up cases on demand", msg)
    else:
        log.info("%d case(s) under %s: %s",
                 len(cases), source.describe(), ", ".join(c.name for c in cases))

    from .server.app import serve

    serve(source, host=args.host, port=args.port, mode=args.mode, root_path=args.root_path)


if __name__ == "__main__":
    main()
