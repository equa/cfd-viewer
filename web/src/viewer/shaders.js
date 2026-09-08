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
  attribute float travel;
  varying float vScalar;
  varying float vTravel;
  varying vec3 vNormal;
  void main() {
    vScalar = scalar;
    vTravel = travel;
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

export const VERT_LINE = /* glsl */`
  attribute float scalar;
  attribute float travel;
  varying float vScalar;
  varying float vTravel;
  void main() {
    vScalar = scalar;
    vTravel = travel;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

/* Discrete colour bands are a quantisation of the *lookup coordinate*, not of
 * the table: t -> the centre of its band. Sampling the smooth 256-entry LUT at
 * (i+0.5)/N returns exactly cmap((i+0.5)/N), which is the colour FoamViz bakes
 * into transfer-function plateau i -- so the two renderers band identically
 * rather than merely similarly. */
/* Comets riding the streamlines.
 *
 * An animated dash pattern, which is all a "particle" needs to be: `travel` is
 * per-vertex transport time along the streamline (server/scene.py bakes it from
 * vtkStreamTracer's own IntegrationTime), so a pulse at a fixed phase offset
 * advances at the LOCAL FLOW SPEED without anything moving on the CPU. No
 * particle buffer, no re-seeding, no per-frame JS: one uniform.
 *
 * Because travel is real transport time and not arc length, comets speed up
 * where the flow is fast -- ~32x variation along the demo case's streamlines --
 * which is the point. It also gives the streamlines something they simply did
 * not have before: a visible DIRECTION. A static streamline is
 * direction-ambiguous.
 *
 * `fract` of (phase - travel) puts the head at f == 0 and the tail just behind
 * it: a point slightly upstream has slightly larger f, so an exponential decay
 * in f trails the comet upstream, which is the way a tail points.
 *
 * uComets == 0 must be an exact no-op -- glow 0 and dim 1, so the fragment is
 * bit-identical to the un-animated one.
 */
const COMET = /* glsl */`
  uniform float uComets;
  uniform float uCometPhase;
  uniform float uCometPeriod;
  uniform float uCometTail;
  uniform float uCometDim;
  varying float vTravel;

  float cometGlow() {
    if (uComets < 0.5) return 0.0;
    return exp(-fract((uCometPhase - vTravel) / uCometPeriod) * uCometTail);
  }
  // Between comets the line dims, so the comets read as bright objects on a
  // faint path rather than as brightness noise along a solid one.
  float cometDim() { return mix(1.0, uCometDim, uComets); }
  // A modest lift toward white: enough to read as a comet head, not enough to
  // throw away the field colour the line is carrying.
  vec3 cometTint(vec3 base, float glow) { return mix(base, vec3(1.0), glow * 0.6); }`

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
  ${COMET}
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
    float glow = cometGlow();
    alpha *= mix(cometDim(), 1.0, glow);
    if (alpha < 0.01) discard;  // transparent fragments must not blend or occlude
    gl_FragColor = vec4(cometTint(base * shade, glow), alpha);
  }`

export const FRAG_LINE = /* glsl */`
  uniform sampler2D uLut;
  uniform vec2 uRange;
  uniform float uOpacity;
  uniform float uBands;
  uniform float uOpacityMap;
  varying float vScalar;
  ${BAND}
  ${COMET}
  void main() {
    float t = clamp((vScalar - uRange.x) / max(uRange.y - uRange.x, 1e-12), 0.0, 1.0);
    float alpha = uOpacity * mix(1.0, t, uOpacityMap);
    float glow = cometGlow();
    alpha *= mix(cometDim(), 1.0, glow);
    if (alpha < 0.01) discard;
    vec3 base = texture2D(uLut, vec2(band(t, uBands), 0.5)).rgb;
    gl_FragColor = vec4(cometTint(base, glow), alpha);
  }`
