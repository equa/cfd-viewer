/*
 * The whole appearance pipeline, in about forty lines of GLSL.
 *
 * This is the payoff of the split: anything that is a *transfer function* in
 * VTK is a *uniform* here, and a uniform is instant. Colour map, colour range,
 * band count and the opacity ramp therefore cost no round trip and no
 * re-extraction -- where the Trame path has to rebuild a
 * vtkColorTransferFunction and re-serialise it, and could not do the opacity
 * ramp at all (the discretizable CTF does not serialise to vtk.js; reverted).
 *
 * The price, and it is real: every setting the shader owns is a setting the
 * server does not know. A server-side render (a report PNG) will not match this
 * view unless these values travel with the render request.
 */

export const VERT = /* glsl */`
  attribute float scalar;
  varying float vScalar;
  varying vec3 vNormal;
  void main() {
    vScalar = scalar;
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

export const VERT_LINE = /* glsl */`
  attribute float scalar;
  varying float vScalar;
  void main() {
    vScalar = scalar;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

/* Discrete colour bands are a quantisation of the *lookup coordinate*, not of
 * the table: t -> the centre of its band. Sampling the smooth 256-entry LUT at
 * (i+0.5)/N returns exactly cmap((i+0.5)/N), which is the colour FoamViz bakes
 * into transfer-function plateau i -- so the two renderers band identically
 * rather than merely similarly. */
const BAND = /* glsl */`
  float band(float t, float n) {
    if (n < 1.5) return t;
    return (min(floor(t * n), n - 1.0) + 0.5) / n;
  }`

/* Two-sided key + fill over an ambient floor, so no face reads as black -- the
 * rule vtkLightKit follows, hence abs() on the dots. uDiffuse scales the
 * directional part, uAmbient the floor: together they are the Lighting panel. */
export const FRAG = /* glsl */`
  uniform sampler2D uLut;
  uniform vec2 uRange;
  uniform float uOpacity;
  uniform float uAmbient;
  uniform float uDiffuse;
  uniform float uBands;
  uniform float uOpacityMap;
  // 1 = colour by the field, 0 = flat uFlat (the Trame app's "Colour by field"
  // off, where the shell is a neutral grey so the slice inside it reads).
  uniform float uColored;
  uniform vec3 uFlat;
  varying float vScalar;
  varying vec3 vNormal;
  ${BAND}
  void main() {
    float t = clamp((vScalar - uRange.x) / max(uRange.y - uRange.x, 1e-12), 0.0, 1.0);
    vec3 base = mix(uFlat, texture2D(uLut, vec2(band(t, uBands), 0.5)).rgb, uColored);
    vec3 n = normalize(vNormal);
    float key = abs(dot(n, normalize(vec3(0.35, 0.65, 0.55))));
    float fill = abs(dot(n, normalize(vec3(-0.6, -0.25, 0.5)))) * 0.35;
    float shade = uAmbient + uDiffuse * (key + fill);
    // Colour-map-weighted opacity, linear: alpha follows the *unbanded* t, so
    // the ramp stays a function of the value the way ParaView's own opacity
    // transfer function is, independent of how the colours are banded.
    float alpha = uOpacity * mix(1.0, t, uOpacityMap * uColored);
    if (alpha < 0.01) discard;  // transparent fragments must not blend or occlude
    gl_FragColor = vec4(base * shade, alpha);
  }`

export const FRAG_LINE = /* glsl */`
  uniform sampler2D uLut;
  uniform vec2 uRange;
  uniform float uOpacity;
  uniform float uBands;
  uniform float uOpacityMap;
  varying float vScalar;
  ${BAND}
  void main() {
    float t = clamp((vScalar - uRange.x) / max(uRange.y - uRange.x, 1e-12), 0.0, 1.0);
    float alpha = uOpacity * mix(1.0, t, uOpacityMap);
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(texture2D(uLut, vec2(band(t, uBands), 0.5)).rgb, alpha);
  }`
