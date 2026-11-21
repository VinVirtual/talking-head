import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  clamp, DEFAULT_COLORS, depthAt, discPatch, ease, eyeOpen, lidPoint, lookFromPointer, mergedColors,
  mouthFromSound, mouthOpen, sampleGrid, silencedByAnalyser, targetOpening, heightOn, lipLine, LINE_SAMPLES, mouthBounds, openingDepth, openingProfile, sharedNormals, surfaceProblems,
} from '../viewer/math.js';

const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-9, message ?? `${actual} is not ${expected}`);
const grid = { cols: 3, rows: 2, span: 2, depth: [0, 1, 2, 10, 11, 12] }; // rows from the bottom, columns from the left
const feature = { at: [0.5, -0.2], half: [0.1, 0.05], grid };
const jaw = { drop: 0.04, share: 0.45 };

test('eyes are open most of the time and shut in the middle of a blink', () => {
  assert.equal(eyeOpen(1), 1);
  assert.ok(eyeOpen(0.07) < 1e-6);
  assert.equal(eyeOpen(0), 1);
  assert.ok(eyeOpen(3.6 + 0.07) < 1e-6, 'and again one blink later');
});

test('eyeOpen stays between 0 and 1, also for negative time', () => {
  for (let t = -5; t < 10; t += 0.013) assert.ok(eyeOpen(t) >= 0 && eyeOpen(t) <= 1);
});

test('the talking rhythm stays between 0 and 1 and really moves', () => {
  let low = 1;
  let high = 0;
  for (let t = 0; t < 10; t += 0.01) {
    const open = mouthOpen(t);
    assert.ok(open >= 0 && open <= 1);
    low = Math.min(low, open);
    high = Math.max(high, open);
  }
  assert.ok(low < 0.1 && high > 0.9);
});

test('ease moves the same distance in one long step as in many short ones', () => {
  let value = 0;
  for (let step = 0; step < 10; step += 1) value = ease(value, 1, 5, 0.01);
  near(value, ease(0, 1, 5, 0.1));
  assert.equal(ease(3, 3, 5, 0.1), 3);
});

test('silence keeps the mouth shut and loud sound opens it fully', () => {
  assert.equal(mouthFromSound(new Uint8Array(64).fill(128)), 0);
  assert.equal(mouthFromSound(new Uint8Array(0)), 0);
  assert.equal(mouthFromSound(Uint8Array.from({ length: 64 }, (_, index) => (index % 2 ? 255 : 0))), 1);
  const quiet = mouthFromSound(Uint8Array.from({ length: 64 }, (_, index) => (index % 2 ? 136 : 120)));
  assert.ok(quiet > 0.2 && quiet < 0.4, 'quiet speech is lifted so the lips still move');
});

test('the pointer in the middle of the head looks straight ahead', () => {
  const rect = { left: 100, top: 100, width: 200, height: 200 };
  assert.deepEqual(lookFromPointer({ x: 200, y: 200 }, rect, { width: 1000, height: 800 }), { x: 0, y: 0 });
});

test('the pointer far away is clamped to the edge of the look range', () => {
  const rect = { left: 0, top: 0, width: 100, height: 100 };
  assert.deepEqual(lookFromPointer({ x: 5000, y: -5000 }, rect, { width: 1000, height: 800 }), { x: 1, y: -1 });
  near(lookFromPointer({ x: 300, y: 50 }, rect, { width: 1000, height: 800 }).x, 0.5);
});

test('sampleGrid returns the corners exactly', () => {
  assert.equal(sampleGrid(grid, -1, -1), 0);
  assert.equal(sampleGrid(grid, 1, -1), 2);
  assert.equal(sampleGrid(grid, -1, 1), 10);
  assert.equal(sampleGrid(grid, 1, 1), 12);
});

test('sampleGrid blends between samples and holds the edge outside', () => {
  near(sampleGrid(grid, 0, 0), 6);
  near(sampleGrid(grid, -0.5, -1), 0.5);
  assert.equal(sampleGrid(grid, 9, 9), 12);
  assert.equal(sampleGrid(grid, -9, -9), 0);
});

test('depthAt reads the grid around the feature', () => {
  near(depthAt(feature, 0.5, -0.2), 6);
  near(depthAt(feature, 0.5 + 0.1 * 2, -0.2 + 0.05 * 2), 12);
});

test('discPatch makes a full fan with valid triangles', () => {
  const { points, indices } = discPatch({ segments: 8, rings: 2 });
  assert.equal(points.length, 9 * 3);
  assert.equal(indices.length, 8 * 2 * 6);
  assert.ok(indices.every((index) => index >= 0 && index < points.length));
  assert.ok(points.every(([x, y]) => Math.hypot(x, y) <= 1 + 1e-9));
});

test('discPatch can make a band along the lower edge', () => {
  const { points } = discPatch({ inner: 0.8, from: Math.PI, to: Math.PI * 2, segments: 6, rings: 1 });
  assert.ok(points.every(([x, y]) => y <= 1e-9 && Math.hypot(x, y) >= 0.8 - 1e-9));
});

test('an open lid is folded up at the top of the eye', () => {
  const eye = { at: [0, 0], half: [0.1, 0.1] };
  near(lidPoint([0, -1], eye, 0, 1)[1], 0.1);
  near(lidPoint([0, 1], eye, 0, 1)[1], 0.1);
});

test('a shut lid covers the eye from top to bottom, and a little more to hide its outline', () => {
  const eye = { at: [0.2, 0.3], half: [0.1, 0.05] };
  near(lidPoint([0, -1], eye, 1, 1)[1], 0.25);
  near(lidPoint([0, 1], eye, 1, 1)[1], 0.35);
  near(lidPoint([1, 0], eye, 1, 1)[0], 0.3);
  assert.ok(lidPoint([0, -1], eye, 1)[1] < 0.25);
});

test('the lip line found in the picture is resampled to a fixed number of heights', () => {
  const mouth = { at: [0, -0.1], half: [0.1, 0.05], line: { x: [-0.08, 0.08], y: [-0.09, -0.12, -0.09] } };
  const line = lipLine(mouth);
  assert.equal(line.y.length, LINE_SAMPLES);
  near(line.y[0], -0.09);
  near(line.y[LINE_SAMPLES - 1], -0.09);
  near(line.y[(LINE_SAMPLES - 1) / 2], -0.12);
  assert.deepEqual(line.x, [-0.08, 0.08]);
});

test('with no line found the mouth opens from a straight line across the mark', () => {
  const line = lipLine({ at: [0.2, -0.1], half: [0.1, 0.05] });
  assert.ok(line.y.every((height) => height === -0.1));
  near(line.x[0], 0.11);
  near(line.x[1], 0.29);
  near(lipLine({ at: [0, 0], half: [0.1, 0.05], line: { x: [0.1, 0.1], y: [0, 0] } }).x[0], -0.09, 'a line with no width is ignored');
});

test('heightOn blends between neighbours and holds the ends', () => {
  near(heightOn([0, 10, 20], 0.25), 5);
  assert.equal(heightOn([0, 10, 20], 1), 20);
  assert.equal(heightOn([0, 10, 20], -1), 0);
});

test('the opening keeps its corners pinned and drops most in the middle', () => {
  near(openingProfile(0), 0);
  near(openingProfile(1), 0);
  near(openingProfile(0.5), 1);
  assert.ok(openingProfile(0.25) > 0.5 && openingProfile(0.25) < 1);
  for (let share = 0; share <= 1; share += 0.05) assert.ok(openingProfile(share) >= 0 && openingProfile(share) <= 1);
});

test('the mouth patch covers the line, the deepest opening and the rim below it', () => {
  const mouth = { at: [0, -0.1], half: [0.1, 0.05], line: { x: [-0.08, 0.08], y: [-0.09, -0.12, -0.09] } };
  const bounds = mouthBounds(mouth);
  assert.ok(bounds.left < -0.08 && bounds.right > 0.08);
  assert.ok(bounds.top > -0.09);
  assert.ok(bounds.bottom < -0.12 - openingDepth(mouth));
});

test('sharedNormals makes doubled points agree', () => {
  const positions = [0, 0, 0, 0, 0, 0, 1, 0, 0];
  const normals = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const shared = sharedNormals(positions, normals);
  assert.ok(Math.abs(shared[0] - Math.SQRT1_2) < 1e-6 && Math.abs(shared[1] - Math.SQRT1_2) < 1e-6);
  assert.deepEqual([...shared.slice(0, 3)], [...shared.slice(3, 6)]);
  assert.deepEqual([...shared.slice(6, 9)], [0, 0, 1]);
});

function goodSurface() {
  const spot = { at: [0, 0], half: [0.1, 0.1], grid: { cols: 2, rows: 2, span: 1.8, depth: [0, 0, 0, 0] } };
  return { version: 1, eyes: [spot, spot], mouth: spot, jaw: { drop: 0.02, share: 0.45 } };
}

test('a complete surface has no problems', () => {
  assert.deepEqual(surfaceProblems(goodSurface()), []);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), outline: { width: 0.008, color: '#1d2433' } }), []);
});

test('surfaceProblems names what is missing', () => {
  assert.deepEqual(surfaceProblems(null), ['the surface description must be an object']);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), eyes: [] }), ['eyes must be a list of two']);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), jaw: { drop: 0 } }), ['jaw needs "drop" and "share"']);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), mouth: { at: [0, 0] } }), ['mouth needs "at" and "half"']);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), version: 7 }), ['version must be 1']);
  assert.deepEqual(surfaceProblems({ ...goodSurface(), outline: { width: 1 } }), ['outline needs "width" and "color"']);
  const badGrid = goodSurface();
  badGrid.mouth = { ...badGrid.mouth, grid: { cols: 3, rows: 3, span: 1, depth: [0] } };
  assert.deepEqual(surfaceProblems(badGrid), ['mouth.grid.depth must have cols x rows numbers']);
});

test('colours: the caller beats the picture, the picture beats the defaults, bad values are ignored', () => {
  const colors = mergedColors({ lid: '#f6c39c', lash: 'navy' }, { teeth: '#ffffff', lid: '#000000' });
  assert.equal(colors.lid, '#000000');
  assert.equal(colors.lash, DEFAULT_COLORS.lash);
  assert.equal(colors.teeth, '#ffffff');
  assert.deepEqual(mergedColors(), DEFAULT_COLORS);
});

test('clamp', () => {
  assert.equal(clamp(5, 0, 1), 1);
  assert.equal(clamp(-5, 0, 1), 0);
  assert.equal(clamp(0.3, 0, 1), 0.3);
});

test('targetOpening uses a real loudness and ignores NaN, Infinity and junk', () => {
  assert.equal(targetOpening(false, 0.8, false, 1), 0);
  assert.equal(targetOpening(true, 0.8, false, 1), 0.8);
  assert.equal(targetOpening(true, 7, false, 1), 1);
  assert.equal(targetOpening(true, -1, false, 1), 0);
  for (const junk of [Number.NaN, Infinity, -Infinity, undefined, null, '0.5', {}]) {
    const open = targetOpening(true, junk, false, 1);
    assert.ok(Number.isFinite(open) && open >= 0 && open <= 1, String(junk));
    assert.equal(open, mouthOpen(1));
  }
  assert.equal(targetOpening(true, Number.NaN, true, 1), 0.5);
});

test('only cross-origin audio fetched without CORS is silenced by an analyser', () => {
  const page = 'http://127.0.0.1:8437/examples/';
  assert.equal(silencedByAnalyser('juno/voice.mp3', page, null), false);
  assert.equal(silencedByAnalyser('http://127.0.0.1:8437/a.mp3', page, null), false);
  assert.equal(silencedByAnalyser('https://cdn.example/a.mp3', page, null), true);
  assert.equal(silencedByAnalyser('https://cdn.example/a.mp3', page, 'anonymous'), false);
  assert.equal(silencedByAnalyser('http://127.0.0.1:9999/a.mp3', page, ''), true);
  assert.equal(silencedByAnalyser('data:audio/wav;base64,AAAA', page, null), false);
  assert.equal(silencedByAnalyser('blob:http://127.0.0.1:8437/1234', page, null), false);
  assert.equal(silencedByAnalyser('', page, null), false);
});
