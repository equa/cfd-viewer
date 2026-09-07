#!/bin/sh
# Vendor the browser deps so the spike runs with no npm, no build step and no
# network. Pinned on purpose: a spike that drifts is not a measurement.
set -eu
DIR="$(dirname "$0")/web/vendor"
mkdir -p "$DIR"
THREE=0.185.1
REACT=18.3.1
HTM=3.1.1
fetch() { echo "  $2"; curl -fsSL "$1" -o "$DIR/$2"; }
fetch "https://cdn.jsdelivr.net/npm/three@$THREE/build/three.module.js" three.module.js
# three >=0.17x splits the build: three.module.js re-exports ./three.core.js.
fetch "https://cdn.jsdelivr.net/npm/three@$THREE/build/three.core.js" three.core.js
fetch "https://cdn.jsdelivr.net/npm/three@$THREE/examples/jsm/controls/OrbitControls.js" OrbitControls.js
fetch "https://cdn.jsdelivr.net/npm/react@$REACT/umd/react.production.min.js" react.js
fetch "https://cdn.jsdelivr.net/npm/react-dom@$REACT/umd/react-dom.production.min.js" react-dom.js
fetch "https://cdn.jsdelivr.net/npm/htm@$HTM/dist/htm.umd.js" htm.js
echo "vendored three $THREE, react $REACT, htm $HTM"
