/** The viewer's timing and geometry sums. Pure, so they are tested without a browser. */

const BLINK_EVERY = 3.6;
const BLINK_LENGTH = 0.14;
const LOUDNESS_LIFT = 4.2; // quiet speech still moves the lips
export const COLOR_NAMES = ['lid', 'lash', 'mouth', 'tongue', 'teeth', 'lip'];
export const DEFAULT_COLORS = { lid: '#e0b090', lash: '#22161a', mouth: '#3a1215', tongue: '#c9645c', teeth: '#f4efe8', lip: '#c07a6a' };

/**
 * How open the eyes are (1 open, 0 shut) at time `t` seconds: a quick blink every few seconds.
 * @param {number} t
 * @returns {number}
 */
export function eyeOpen(t) {
  const into = ((t % BLINK_EVERY) + BLINK_EVERY) % BLINK_EVERY;
  if (into > BLINK_LENGTH) return 1;
  return Math.abs(into / BLINK_LENGTH - 0.5) * 2;
}

/**
 * How open the mouth is (0 to 1) while talking with no sound to follow: uneven, like syllables.
 * @param {number} t
 * @returns {number}
 */
export function mouthOpen(t) {
  const beat = Math.sin(t * 13.1) * 0.5 + Math.sin(t * 7.3 + 1.7) * 0.3 + Math.sin(t * 21.7 + 0.4) * 0.2;
  return clamp(0.45 + beat * 0.75, 0, 1);
}

/**
 * Moves `from` a share of the way to `to`, the same amount per second whatever the frame rate.
 * @param {number} from @param {number} to @param {number} rate @param {number} delta seconds
 * @returns {number}
 */
export const ease = (from, to, rate, delta) => from + (to - from) * (1 - Math.exp(-rate * delta));

/** @param {number} value @param {number} low @param {number} high @returns {number} */
export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * How open the mouth should be for a slice of sound, given as unsigned bytes centred on 128
 * (what an AnalyserNode's getByteTimeDomainData fills in).
 * @param {ArrayLike<number>} samples
 * @returns {number} 0 to 1
 */
export function mouthFromSound(samples) {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) sum += ((samples[index] - 128) / 128) ** 2;
  return Math.min(1, Math.sqrt(sum / samples.length) * LOUDNESS_LIFT);
}

/**
 * Where the pointer is relative to the middle of the head, as -1 to 1 across and down.
 * @param {{x: number, y: number}} pointer client position
 * @param {{left: number, top: number, width: number, height: number}} rect the canvas on the page
 * @param {{width: number, height: number}} view the window
 * @returns {{x: number, y: number}}
 */
export function lookFromPointer(pointer, rect, view) {
  const reach = Math.max(view.width, view.height) * 0.5 || 1;
  return {
    x: clamp((pointer.x - (rect.left + rect.width / 2)) / reach, -1, 1),
    y: clamp((pointer.y - (rect.top + rect.height / 2)) / reach, -1, 1),
  };
}

/**
 * How far forward the surface is at a point near a feature, from the feature's grid of depths.
 * `u` and `v` run -1 to 1 across the grid (left to right, bottom to top); outside it the edge is held.
 * @param {{cols: number, rows: number, depth: number[]}} grid
 * @param {number} u @param {number} v
 * @returns {number}
 */
export function sampleGrid(grid, u, v) {
  const column = clamp((u + 1) / 2, 0, 1) * (grid.cols - 1);
  const row = clamp((v + 1) / 2, 0, 1) * (grid.rows - 1);
  const c0 = Math.min(grid.cols - 2, Math.floor(column));
  const r0 = Math.min(grid.rows - 2, Math.floor(row));
  const fc = column - c0;
  const fr = row - r0;
  const at = (r, c) => grid.depth[r * grid.cols + c];
  const low = at(r0, c0) * (1 - fc) + at(r0, c0 + 1) * fc;
  const high = at(r0 + 1, c0) * (1 - fc) + at(r0 + 1, c0 + 1) * fc;
  return low * (1 - fr) + high * fr;
}

/**
 * How far forward the head's surface is at a point on the plane, near a feature.
 * @param {{at: number[], half: number[], grid: {cols: number, rows: number, span: number, depth: number[]}}} feature
 * @param {number} x @param {number} y
 * @returns {number}
 */
export function depthAt(feature, x, y) {
  const { at, half, grid } = feature;
  return sampleGrid(grid, (x - at[0]) / (half[0] * grid.span), (y - at[1]) / (half[1] * grid.span));
}

/**
 * A flat patch as points on the unit disc plus triangles: the part of a disc between two radii and two
 * angles. With `inner` 0 it is a fan from the centre.
 * @param {{inner?: number, outer?: number, from?: number, to?: number, segments?: number, rings?: number}} [shape]
 * @returns {{points: number[][], indices: number[]}}
 */
export function discPatch({ inner = 0, outer = 1, from = 0, to = Math.PI * 2, segments = 32, rings = 4 } = {}) {
  const points = [];
  const indices = [];
  for (let ring = 0; ring <= rings; ring += 1) {
    const radius = inner + ((outer - inner) * ring) / rings;
    for (let step = 0; step <= segments; step += 1) {
      const angle = from + ((to - from) * step) / segments;
      points.push([Math.cos(angle) * radius, Math.sin(angle) * radius]);
    }
  }
  const across = segments + 1;
  for (let ring = 0; ring < rings; ring += 1) {
    for (let step = 0; step < segments; step += 1) {
      const a = ring * across + step;
      const b = a + across;
      indices.push(a, b, b + 1, a, b + 1, a + 1);
    }
  }
  return { points, indices };
}

/**
 * Where a point of the eyelid's unit disc is on the plane when the lid is `closed` (0 open, 1 shut).
 * The lid hangs from the top of the eye and comes down; its lower edge keeps the eye's curve.
 * @param {number[]} point on the unit disc
 * @param {{at: number[], half: number[]}} eye
 * @param {number} closed
 * @param {number} [grow] how much larger than the marked eye the lid is, to cover its outline
 * @returns {number[]} [x, y]
 */
export function lidPoint(point, eye, closed, grow = 1.2) {
  const width = eye.half[0] * grow;
  const height = eye.half[1] * grow;
  const top = eye.at[1] + height;
  return [eye.at[0] + point[0] * width, top - (1 - point[1]) * height * closed];
}

export const LINE_SAMPLES = 9;
const OPEN_DEPTH = 1.5; // the deepest opening, in mouth half-heights

/**
 * The line between the lips: the one the build found in the picture, or a straight line across the
 * marked mouth when it found none. Always `LINE_SAMPLES` heights, evenly spread from x[0] to x[1].
 * @param {{at: number[], half: number[], line?: {x: number[], y: number[]}}} mouth
 * @returns {{x: number[], y: number[]}}
 */
export function lipLine(mouth) {
  const line = mouth.line;
  if (line && line.x?.length === 2 && line.x[1] > line.x[0] && line.y?.length >= 2) {
    return { x: [...line.x], y: Array.from({ length: LINE_SAMPLES }, (_, index) => heightOn(line.y, index / (LINE_SAMPLES - 1))) };
  }
  return { x: [mouth.at[0] - mouth.half[0] * 0.9, mouth.at[0] + mouth.half[0] * 0.9], y: new Array(LINE_SAMPLES).fill(mouth.at[1]) };
}

/** A height on a list of evenly spread heights, at `share` (0 to 1) of the way along, straight between them. */
export function heightOn(heights, share) {
  const place = clamp(share, 0, 1) * (heights.length - 1);
  const before = Math.min(heights.length - 2, Math.floor(place));
  const part = place - before;
  return heights[before] * (1 - part) + heights[before + 1] * part;
}

/**
 * How far down the lower edge of the opening is, as a share of its deepest, at `share` (0 to 1) of the way
 * along the lip line. The corners (0 and 1) stay pinned to the line; the middle drops furthest.
 * @param {number} share
 * @returns {number}
 */
export const openingProfile = (share) => Math.pow(Math.max(0, Math.sin(Math.PI * clamp(share, 0, 1))), 0.85);

/** The deepest the mouth opens, on the plane: a share of the marked open mouth's half-height. */
export const openingDepth = (mouth) => mouth.half[1] * OPEN_DEPTH;

/**
 * The part of the plane the mouth's patch must cover: the lip line, the deepest opening and the rim below.
 * @returns {{left: number, right: number, top: number, bottom: number}}
 */
export function mouthBounds(mouth, line = lipLine(mouth)) {
  const width = line.x[1] - line.x[0];
  const depth = openingDepth(mouth);
  return {
    left: line.x[0] - width * 0.08,
    right: line.x[1] + width * 0.08,
    top: Math.max(...line.y) + depth * 0.2,
    bottom: Math.min(...line.y) - depth * 1.45,
  };
}

/**
 * Normals that agree wherever two points share a position. A model's points are doubled along texture
 * seams; without this the outline drawn around the head would split there.
 * @param {ArrayLike<number>} positions x, y, z for every point
 * @param {ArrayLike<number>} normals x, y, z for every point
 * @returns {Float32Array} new normals, unit length
 */
export function sharedNormals(positions, normals) {
  const sums = new Map();
  const keys = [];
  for (let index = 0; index < positions.length; index += 3) {
    const key = `${Math.round(positions[index] * 1e4)},${Math.round(positions[index + 1] * 1e4)},${Math.round(positions[index + 2] * 1e4)}`;
    keys.push(key);
    const sum = sums.get(key) ?? [0, 0, 0];
    sum[0] += normals[index];
    sum[1] += normals[index + 1];
    sum[2] += normals[index + 2];
    sums.set(key, sum);
  }
  const shared = new Float32Array(positions.length);
  keys.forEach((key, point) => {
    const [x, y, z] = sums.get(key);
    const length = Math.hypot(x, y, z) || 1;
    shared.set([x / length, y / length, z / length], point * 3);
  });
  return shared;
}

const isPair = (value) => Array.isArray(value) && value.length === 2 && value.every((item) => Number.isFinite(item));
const isColor = (value) => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);

function featureProblems(name, feature) {
  if (!feature || !isPair(feature.at) || !isPair(feature.half)) return [`${name} needs "at" and "half"`];
  const grid = feature.grid;
  if (!grid || !Number.isInteger(grid.cols) || !Number.isInteger(grid.rows) || grid.cols < 2 || grid.rows < 2) {
    return [`${name}.grid needs cols and rows of 2 or more`];
  }
  if (!Array.isArray(grid.depth) || grid.depth.length !== grid.cols * grid.rows) return [`${name}.grid.depth must have cols x rows numbers`];
  if (!(grid.span > 0)) return [`${name}.grid.span must be larger than zero`];
  return [];
}

/**
 * Everything wrong with a surface description (the JSON written beside the GLB), as sentences.
 * @param {unknown} surface
 * @returns {string[]} empty when it can be used
 */
export function surfaceProblems(surface) {
  if (!surface || typeof surface !== 'object') return ['the surface description must be an object'];
  const problems = [];
  if (surface.version !== 1) problems.push('version must be 1');
  if (!Array.isArray(surface.eyes) || surface.eyes.length !== 2) problems.push('eyes must be a list of two');
  else surface.eyes.forEach((eye, index) => problems.push(...featureProblems(`eyes[${index}]`, eye)));
  problems.push(...featureProblems('mouth', surface.mouth));
  if (!surface.jaw || !(surface.jaw.drop > 0) || !Number.isFinite(surface.jaw.share)) problems.push('jaw needs "drop" and "share"');
  if (surface.outline !== undefined && (!(surface.outline.width >= 0) || !isColor(surface.outline.color))) problems.push('outline needs "width" and "color"');
  return problems;
}

/**
 * The colours to draw the eyelids and mouth with: the defaults, then the ones found in the picture,
 * then the caller's own. Anything that is not a #rrggbb colour is ignored.
 * @param {Record<string, string>} [fromPicture]
 * @param {Record<string, string>} [chosen]
 * @returns {Record<string, string>}
 */
export function mergedColors(fromPicture = {}, chosen = {}) {
  const colors = { ...DEFAULT_COLORS };
  for (const source of [fromPicture, chosen]) {
    for (const name of COLOR_NAMES) if (isColor(source?.[name])) colors[name] = source[name];
  }
  return colors;
}
