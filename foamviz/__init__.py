"""Compatibility alias: ``foamviz`` is now ``equa.cfd_viewer.foamviz``.

cfd-backend's cfd-viz smoke test (``tests/smoke/viz.sh``) runs
``from foamviz.pipeline import FoamPipeline`` from the root of a clone of this
repository, so the old top-level name has to keep resolving until cfd-backend
builds from this repository's own Dockerfile (openspec standalone-viewer,
design D9/D11). New code imports ``equa.cfd_viewer.foamviz``.

The submodules are registered under their old names as the *same* module
objects, not re-imported copies, so a class reached through either name is the
same class.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from equa.cfd_viewer.foamviz import case, colors, pipeline  # noqa: E402

sys.modules[__name__ + ".case"] = case
sys.modules[__name__ + ".colors"] = colors
sys.modules[__name__ + ".pipeline"] = pipeline
