import {
  BackSide,
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Scene,
  SRGBColorSpace,
  WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import {
  capPatch,
  clamp,
  depthAt,
  discPatch,
  ease,
  eyeOpen,
  lidPoint,
  lookFromPointer,
  mergedColors,
  mouthFromSound,
  mouthOpen,
  mouthPoint,
  mouthShape,
  sharedNormals,
  surfaceProblems,
} from './math.js';

/**
 * A talking head for a web page: loads the GLB and JSON that `head build` writes, shows the head unlit
 * (the drawing already carries its own light and shade), and makes it follow the pointer, blink, and
 * move its jaw. Framework-free. three.js is a peer dependency.
 *
 *   const head = createTalkingHead(canvas, { model: '/head.glb', surface: '/head.json' });
 *   await head.ready;
 *   head.setTalking(true, () => loudness);   // loudness: 0 to 1, or leave it out for a speech-like rhythm
 *   head.dispose();
 */

const MAX_PIXEL_RATIO = 2;
const PITCH = 0.2;                 // how far the head tips up and down, radians
const DEFAULT_TURN = 0.6;          // how far it turns left and right when the build does not say
const FIELD_OF_VIEW = 15;          // a long lens, framed as the portrait is, so at rest it matches the picture
const CAMERA_DISTANCE = 7.5;
const LIFT = { rim: 0.006, mouth: 0.009, inside: 0.012, lid: 0.01, lash: 0.013 }; // how far each layer floats above the surface
const MOUTH_SHOWS_FROM = 0.05;
const LID_SHOWS_FROM = 0.02;

/** A small mesh whose points are laid onto the head's surface each time it is placed. */
function drapedPatch(patch, material) {
  const geometry = new BufferGeometry();
  const positions = new BufferAttribute(new Float32Array(patch.points.length * 3), 3);
  geometry.setAttribute('position', positions);
  geometry.setIndex(patch.indices);
  const mesh = new Mesh(geometry, material);
  mesh.frustumCulled = false;
  return {
    mesh,
    /** `where` turns a unit-disc point into [x, y] on the plane; `feature` gives the depth there. */
    place(where, feature, lift) {
      patch.points.forEach((point, index) => {
        const [x, y] = where(point);
        positions.setXYZ(index, x, y, depthAt(feature, x, y) + lift);
      });
      positions.needsUpdate = true;
    },
  };
}

/** An eyelid that comes down over the eye, following the curve of the head, with a lash line along its edge. */
function eyelid(eye, materials) {
  const lid = drapedPatch(discPatch({ segments: 36, rings: 5 }), materials.lid);
  const lash = drapedPatch(discPatch({ inner: 0.84, from: Math.PI * 1.04, to: Math.PI * 1.96, segments: 28, rings: 1 }), materials.lash);
  const group = new Group();
  group.add(lid.mesh, lash.mesh);
  return {
    group,
    set(closed) {
      group.visible = closed > LID_SHOWS_FROM;
      if (!group.visible) return;
      lid.place((point) => lidPoint(point, eye, closed), eye, LIFT.lid);
      lash.place((point) => lidPoint(point, eye, closed), eye, LIFT.lash);
    },
  };
}

/** The open mouth, drawn over the closed one in the picture: an outline, the dark inside, top teeth, tongue. */
function openMouth(mouth, jaw, materials) {
  const rim = drapedPatch(discPatch({ segments: 40, rings: 2 }), materials.lash);
  const inside = drapedPatch(discPatch({ segments: 40, rings: 3 }), materials.mouth);
  const teeth = drapedPatch(capPatch(0.42), materials.teeth);
  const tongue = drapedPatch(discPatch({ segments: 24, rings: 2 }), materials.tongue);
  const group = new Group();
  group.add(rim.mesh, inside.mesh, teeth.mesh, tongue.mesh);
  const tilt = mouth.tilt ?? 0;
  return {
    group,
    set(opening) {
      group.visible = opening > MOUTH_SHOWS_FROM;
      if (!group.visible) return;
      const shape = mouthShape(mouth, jaw, opening);
      const outline = Math.min(shape.width, shape.height) * 0.16 + mouth.half[0] * 0.03;
      const outer = { ...shape, width: shape.width + outline, height: shape.height + outline };
      rim.place((point) => mouthPoint(point, outer, tilt), mouth, LIFT.rim);
      inside.place((point) => mouthPoint(point, shape, tilt), mouth, LIFT.mouth);
      teeth.place((point) => mouthPoint([point[0] * 0.9, point[1] * 0.94], shape, tilt), mouth, LIFT.inside);
      tongue.place((point) => mouthPoint([point[0] * 0.56, -0.5 + point[1] * 0.36], shape, tilt), mouth, LIFT.inside);
    },
  };
}

/**
 * The drawing's outline, redrawn around the head so it is still there when the head turns: the same mesh
 * again, pushed out a little along its normals, showing only its inside faces, in the line's colour.
 */
function outlineHull(head, { width, color }) {
  const geometry = head.geometry;
  geometry.computeVertexNormals();
  geometry.setAttribute('normal', new BufferAttribute(sharedNormals(geometry.attributes.position.array, geometry.attributes.normal.array), 3));
  const material = new MeshBasicMaterial({ color, side: BackSide });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.outlineWidth = { value: width };
    shader.vertexShader = `uniform float outlineWidth;\n${shader.vertexShader}`.replace(
      '#include <morphtarget_vertex>',
      '#include <morphtarget_vertex>\n\ttransformed += normalize( normal ) * outlineWidth;',
    );
  };
  const hull = new Mesh(geometry, material);
  hull.morphTargetInfluences = head.morphTargetInfluences; // the same list, so the outline's jaw moves with the head's
  hull.morphTargetDictionary = head.morphTargetDictionary;
  hull.position.copy(head.position);
  hull.quaternion.copy(head.quaternion);
  hull.scale.copy(head.scale);
  return hull;
}

async function loadSurface(surface) {
  if (typeof surface !== 'string') return surface;
  const response = await fetch(surface);
  if (!response.ok) throw new Error(`talking-head: could not load ${surface} (${response.status})`);
  return response.json();
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {object} options
 * @param {string} options.model address of the GLB
 * @param {string | object} options.surface address of the JSON written beside the GLB, or its contents
 * @param {Record<string, string>} [options.colors] lid, lash, mouth, tongue, teeth as #rrggbb, to override the picture's
 * @param {false | {width: number, color: string}} [options.outline] false for no outline; or your own width (plane units) and colour
 * @param {number} [options.maxTurn] how far the head turns to follow the pointer, radians
 * @param {boolean} [options.followPointer=true]
 * @param {boolean} [options.animate=true] false draws one still frame and waits for `renderPose`
 * @returns {{ready: Promise<void>, setTalking: Function, setLook: Function, renderPose: Function, dispose: Function}}
 */
export function createTalkingHead(canvas, { model, surface, colors, outline, maxTurn, followPointer = true, animate = true } = {}) {
  if (!model || !surface) throw new Error('talking-head: both "model" and "surface" are needed');
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.outputColorSpace = SRGBColorSpace;

  const scene = new Scene();
  const figure = new Group();
  scene.add(figure);
  const camera = new PerspectiveCamera(FIELD_OF_VIEW, 1, CAMERA_DISTANCE - 3.5, CAMERA_DISTANCE + 3.5);
  camera.position.set(0, 0, CAMERA_DISTANCE);

  const materials = {};
  const face = { lids: [], mouth: null, jaw: null };
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const state = {
    talking: false, level: null, look: { x: 0, y: 0 }, yaw: 0, pitch: 0, mouth: 0, turn: maxTurn ?? DEFAULT_TURN,
    clock: 0, last: performance.now(), frame: 0, visible: true, loaded: false, disposed: false,
  };
  let texture = null;

  const resize = () => {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || width;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // On a canvas taller than it is wide the lens widens, so the whole portrait still fits across.
    camera.fov = width >= height ? FIELD_OF_VIEW : (Math.atan(Math.tan((FIELD_OF_VIEW * Math.PI) / 360) / camera.aspect) * 360) / Math.PI;
    camera.updateProjectionMatrix();
    if (state.loaded && !animate) renderer.render(scene, camera);
  };

  /** Draws one frame of a given pose: turn and tip in radians, eyes 0 open to 1 shut, mouth 0 to 1. */
  function pose({ yaw = 0, pitch = 0, eyes = 0, mouth = 0, lift = 0 } = {}) {
    figure.rotation.set(pitch, yaw, 0);
    figure.position.y = lift;
    face.lids.forEach((lid) => lid.set(eyes));
    face.jaw?.(mouth);
    face.mouth?.set(mouth);
    renderer.render(scene, camera);
  }

  function draw() {
    const now = performance.now();
    const delta = Math.min(0.05, (now - state.last) / 1000);
    state.last = now;
    state.clock += delta;
    const t = state.clock;

    state.yaw = ease(state.yaw, state.look.x * state.turn, 5, delta);
    state.pitch = ease(state.pitch, state.look.y * PITCH, 5, delta);
    const sway = reducedMotion ? 0 : Math.sin(t * 0.6) * 0.035;
    const nod = state.talking && !reducedMotion ? Math.sin(t * 2.7) * 0.022 : 0;

    // While a sound plays, the mouth follows its loudness; otherwise it follows a speech-like rhythm.
    const heard = state.talking ? state.level?.() : null;
    const target = state.talking ? (heard ?? (reducedMotion ? 0.5 : mouthOpen(t))) : 0;
    state.mouth = ease(state.mouth, clamp(target, 0, 1), 26, delta);

    pose({
      yaw: state.yaw + sway,
      pitch: state.pitch + nod,
      eyes: reducedMotion ? 0 : 1 - eyeOpen(t),
      mouth: state.mouth,
      lift: reducedMotion ? 0 : Math.sin(t * 1.4) * 0.006, // breathing
    });
  }

  const loop = () => {
    state.frame = requestAnimationFrame(loop);
    if (state.visible && state.loaded) draw();
  };

  const ready = Promise.all([new GLTFLoader().loadAsync(model), loadSurface(surface)]).then(([gltf, spots]) => {
    if (state.disposed) return;
    const problems = surfaceProblems(spots);
    if (problems.length > 0) throw new Error(`talking-head: the surface description is not usable: ${problems.join('; ')}`);
    Object.entries(mergedColors(spots.colors, colors)).forEach(([name, color]) => {
      materials[name] = new MeshBasicMaterial({ color, side: DoubleSide });
    });
    const heads = [];
    gltf.scene.traverse((object) => {
      if (!object.isMesh) return;
      heads.push(object);
      texture = object.material.map;
      if (texture) {
        texture.colorSpace = SRGBColorSpace;
        texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      }
      object.material.dispose();
      object.material = new MeshBasicMaterial({ map: texture });
      materials.portrait = object.material;
      const jaw = object.morphTargetDictionary?.jaw;
      if (jaw !== undefined) face.jaw = (amount) => { object.morphTargetInfluences[jaw] = amount; };
    });
    const line = outline ?? spots.outline;
    if (line && line.width > 0) {
      heads.forEach((head) => {
        const hull = outlineHull(head, line);
        materials[`outline${head.id}`] = hull.material;
        head.parent.add(hull);
      });
    }
    if (!face.jaw) throw new Error('talking-head: the model has no "jaw" morph target (was it made by `head build`?)');
    if (maxTurn === undefined && Number.isFinite(spots.maxTurn)) state.turn = spots.maxTurn;
    face.lids = spots.eyes.map((eye) => eyelid(eye, materials));
    face.mouth = openMouth(spots.mouth, spots.jaw, materials);
    figure.add(gltf.scene, ...face.lids.map((lid) => lid.group), face.mouth.group);
    state.loaded = true;
    state.last = performance.now();
    pose();
  });

  const onPointer = (event) => {
    state.look = lookFromPointer({ x: event.clientX, y: event.clientY }, canvas.getBoundingClientRect(), { width: window.innerWidth, height: window.innerHeight });
  };

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  const visibility = new IntersectionObserver(([entry]) => {
    state.visible = entry.isIntersecting; // nothing is drawn while the head is off screen
    state.last = performance.now();
  });
  visibility.observe(canvas);
  if (followPointer) window.addEventListener('pointermove', onPointer, { passive: true });
  resize();
  if (animate) state.frame = requestAnimationFrame(loop);

  return {
    /** Settles once the head is loaded and its first frame is drawn. Rejects with the reason if it cannot load. */
    ready,
    /** `level`, when given, is a function returning how loud the voice is right now, from 0 to 1. */
    setTalking(talking, level = null) {
      state.talking = Boolean(talking);
      state.level = typeof level === 'function' ? level : null;
    },
    /** Points the head yourself: -1 to 1 across and down. Use with `followPointer: false`. */
    setLook(x, y) {
      state.look = { x: clamp(x, -1, 1), y: clamp(y, -1, 1) };
    },
    /** Draws one still frame of a pose (for previews and tests). Use with `animate: false`. */
    renderPose(wanted) {
      if (state.loaded) pose(wanted);
    },
    dispose() {
      state.disposed = true;
      cancelAnimationFrame(state.frame);
      resizeObserver.disconnect();
      visibility.disconnect();
      window.removeEventListener('pointermove', onPointer);
      scene.traverse((object) => object.geometry?.dispose());
      Object.values(materials).forEach((material) => material.dispose());
      texture?.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

const analysers = new WeakMap();

/**
 * Listens to an <audio> or <video> element and reports how loud it is, for `setTalking`:
 *
 *   const voice = audioLevel(audioElement);
 *   audioElement.addEventListener('play', () => { voice.resume(); head.setTalking(true, voice.level); });
 *   audioElement.addEventListener('pause', () => head.setTalking(false));
 *
 * Call it from a click or key press the first time (browsers only start audio after one). The element's
 * sound still plays through the speakers. A file from another site needs CORS headers and `crossorigin`.
 * @param {HTMLMediaElement} element
 * @returns {{level: () => number, resume: () => Promise<void>, close: () => Promise<void>}}
 */
export function audioLevel(element) {
  let entry = analysers.get(element);
  if (!entry) {
    const context = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaElementSource(element).connect(analyser);
    analyser.connect(context.destination);
    entry = { context, analyser, samples: new Uint8Array(analyser.fftSize) };
    analysers.set(element, entry);
  }
  return {
    level() {
      entry.analyser.getByteTimeDomainData(entry.samples);
      return mouthFromSound(entry.samples);
    },
    resume: () => entry.context.resume(),
    close: () => entry.context.close(),
  };
}
