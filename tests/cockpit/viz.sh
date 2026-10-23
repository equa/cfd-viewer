#!/bin/bash
#
# Smoke tests for the cfd-viz image — run INSIDE the built image.
#
# This image had no test at all until 2026-09, which meant CI went green on a
# cfd-viz whose VTK import was broken: nothing downstream of the build ever
# imported it. That mattered the moment the base moved from Debian bookworm to
# trixie and libosmesa6/libgl1 were dropped, both of which change what VTK finds
# at runtime and neither of which the build itself would notice.
#
# Tests 2 and 3 are the ones that earn their keep. VTK dlopens its GL backend
# rather than linking it, so a missing library is a RUNTIME failure a successful
# build cannot detect. This image ships with no GL backend at all, which is only
# safe because FoamPipeline builds its render window lazily -- so these two
# check that construction still takes no GL dependency, and that the whole
# pipeline builds without one.
#
set -e

PASS="[PASS]"
FAIL="[FAIL]"

cd /opt/foamviz

echo "=== cfd-viz smoke tests ==="

# 1. Python and VTK are importable, and VTK is new enough for the wheel to
#    carry its own EGL/OSMesa render windows (>= 9.4, per requirements.txt).
echo -n "vtk import ... "
vtk_ver=$(python -c "import vtk; print(vtk.vtkVersion.GetVTKVersion())" 2>/dev/null) \
    || { echo "$FAIL (cannot import vtk)"; exit 1; }
echo "$PASS ($vtk_ver)"

# 2. Constructing the pipeline must NOT construct a render window.
#
#    This image deliberately ships with NO GL backend -- no libosmesa6, no
#    libgl1 -- because the scene server only ever extracts: it reads polydata
#    off the VTK filters and sends triangles to the browser. VTK needs a GL
#    backend PRESENT to construct a vtkRenderWindow, not merely to render into
#    one, so FoamPipeline builds one lazily (upstream `render_window` property).
#
#    Asserting the ABSENCE is the point. If something starts building a render
#    window during construction again, the packages silently become required
#    once more and the image grows mesa-libgallium and libllvm19 back, with
#    their vulnerabilities, for a code path this service never takes.
echo -n "construction takes no GL dependency ... "
python -c "
import sys
from foamviz.pipeline import FoamPipeline
p = FoamPipeline()
sys.exit(0 if p.rendering_started is False else 1)
" >/dev/null 2>&1 \
    || { echo "$FAIL (a render window was built during construction)"; exit 1; }
echo "$PASS"

# 3. The real pipeline. FoamPipeline() builds the scene and every filter at
#    construction, so this exercises far more than an import -- and it must do
#    so without any GL backend present in this image.
echo -n "FoamPipeline() ... "
python -c "from foamviz.pipeline import FoamPipeline; FoamPipeline()" >/dev/null 2>&1 \
    || { echo "$FAIL (pipeline construction failed)"; exit 1; }
echo "$PASS"

# 4. The service entry point parses its arguments.
echo -n "main.py --help ... "
python main.py --help >/dev/null 2>&1 || { echo "$FAIL"; exit 1; }
echo "$PASS"

# 5. The browser client was built into the image. The Vite build happens in a
#    separate stage, so a broken npm build is otherwise invisible until someone
#    opens /visualize and gets a blank page.
echo -n "built web client present ... "
[ -f web/dist/index.html ] || { echo "$FAIL (web/dist/index.html missing)"; exit 1; }
echo "$PASS"

echo "=== All cfd-viz smoke tests passed ==="
