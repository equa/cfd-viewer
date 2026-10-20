"""cfd-viewer -- browse OpenFOAM results in a web browser.

VTK extracts server-side (:mod:`.foamviz` + :mod:`.server`); the browser draws
with React and three.js (``web/``).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("equa-cfd-viewer")
except PackageNotFoundError:  # a source checkout run through main.py, not installed
    __version__ = "0.0.0+src"
