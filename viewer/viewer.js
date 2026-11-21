import {
  BackSide,
  BufferAttribute,
  BufferGeometry,
  Color,
  DoubleSide,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Scene,
  ShaderMaterial,
  SRGBColorSpace,
  WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import {
  clamp,
  depthAt,
  discPatch,
  ease,
  eyeOpen,
  lidPoint,
  lookFromPointer,
  mergedColors,
  LINE_SAMPLES,
  lipLine,
  mouthBounds,
  mouthFromSound,
  openingDepth,
  sharedNormals,
  silencedByAnalyser,
  surfaceProblems,
  targetOpening,
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
const LIFT = { mouth: 0.011, lid: 0.01, lash: 0.013 }; // how far each layer floats above the surface
const MOUTH_SHOWS_FROM = 0.02;
const MOUTH_GRID = [28, 16]; // the mouth patch's columns and rows
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

const MOUTH_VERTEX = /* glsl */ `
  varying vec2 vPlane;
  void main() {
    vPlane = position.xy;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// Everything about the open mouth is drawn here, per pixel, on a patch seated on the face over the mouth.
// The opening starts on the drawing's own lip line: its corners stay pinned to the line's ends and only
// its lower edge drops. Inside: a shaded dark mouth, a tooth edge under the top lip, a tongue resting on
// the bottom, a thin line along the lower edge and a soft lower-lip rim below it. Outside it is clear,
// so the drawing shows through.
const MOUTH_FRAGMENT = /* glsl */ `
  #define SAMPLES ${LINE_SAMPLES}
  uniform float opening;
  uniform float depth;
  uniform vec2 ends;
  uniform float heights[SAMPLES];
  uniform vec3 insideColor;
  uniform vec3 teethColor;
  uniform vec3 tongueColor;
  uniform vec3 lipColor;
  uniform vec3 inkColor;
  varying vec2 vPlane;

  float lineAt(float share) {
    float place = clamp(share, 0.0, 1.0) * float(SAMPLES - 1);
    float height = heights[0];
    for (int index = 0; index < SAMPLES - 1; index++) {
      float part = clamp(place - float(index), 0.0, 1.0);
      if (place >= float(index)) height = mix(heights[index], heights[index + 1], part);
    }
    return height;
  }

  void main() {
    float width = ends.y - ends.x;
    float share = (vPlane.x - ends.x) / width;
    float along = clamp(share, 0.0, 1.0);
    float top = lineAt(along);
    float profile = pow(max(0.0, sin(3.14159265 * along)), 0.85);
    float drop = opening * depth * profile;
    float bottom = top - drop;
    float soft = max(fwidth(vPlane.y), 1e-5) * 1.2;
    float inRange = step(0.0, share) * step(share, 1.0);

    // Inside the opening: 0 at the lip line, 1 at the lower edge.
    float inside = inRange * smoothstep(-soft, soft, top - vPlane.y) * smoothstep(-soft, soft, vPlane.y - bottom) * step(1e-5, drop);
    float t = clamp((top - vPlane.y) / max(drop, 1e-5), 0.0, 1.0);
    float sideShade = 1.0 - 0.45 * pow(abs(along - 0.5) * 2.0, 2.0);
    vec3 color = insideColor * (0.55 + 0.6 * smoothstep(0.1, 0.9, t)) * sideShade;

    // The top teeth: a band hanging from the lip line, rounded off towards the corners, a little shade at its edge.
    float teethHeight = depth * 0.24 * smoothstep(0.08, 0.45, opening) * pow(profile, 0.6);
    float teethEdge = top - teethHeight;
    float teeth = smoothstep(-soft, soft, vPlane.y - teethEdge) * step(0.04, profile);
    vec3 tooth = teethColor * (1.0 - 0.18 * smoothstep(teethHeight * 0.4, teethHeight, top - vPlane.y));
    color = mix(color, tooth, teeth);

    // The tongue: a soft mound resting on the lower edge.
    vec2 tongueAt = vec2((along - 0.5) / 0.32, (vPlane.y - bottom) / max(drop * 0.42, 1e-5));
    float tongueShape = length(vec2(tongueAt.x, max(tongueAt.y, 0.0)));
    float tongue = (1.0 - smoothstep(0.85, 1.0, tongueShape)) * smoothstep(0.2, 0.55, opening) * (1.0 - teeth);
    color = mix(color, tongueColor * (0.8 + 0.25 * clamp(tongueAt.y, 0.0, 1.0)), tongue);

    // A line along the lower edge, as the drawing would have it, fading out at the corners.
    float edge = (1.0 - smoothstep(soft * 0.5, soft * 2.5, abs(vPlane.y - bottom))) * inRange * profile * smoothstep(0.05, 0.2, opening);

    // The lower lip: a soft rim just below the opening.
    float below = bottom - vPlane.y;
    float rimWidth = depth * 0.32 * profile;
    float rim = inRange * smoothstep(-soft, soft, below) * (1.0 - smoothstep(0.0, max(rimWidth, 1e-5), below)) * smoothstep(0.05, 0.3, opening);
    rim = rim * rim * 0.85;

    vec4 result = vec4(lipColor, rim);
    result = mix(result, vec4(color, 1.0), inside);
    result = mix(result, vec4(inkColor, 1.0), edge * 0.9);
    if (result.a < 0.003) discard;
    gl_FragColor = result;
    #include <colorspace_fragment>
  }
`;

/** A flat grid over a rectangle of the plane, laid onto the head's surface. */
function seatedGrid(bounds, feature, lift) {
  const [columns, rows] = MOUTH_GRID;
  const positions = new Float32Array((columns + 1) * (rows + 1) * 3);
  const indices = [];
  for (let row = 0; row <= rows; row += 1) {
    for (let column = 0; column <= columns; column += 1) {
      const x = bounds.left + ((bounds.right - bounds.left) * column) / columns;
      const y = bounds.bottom + ((bounds.top - bounds.bottom) * row) / rows;
      positions.set([x, y, depthAt(feature, x, y) + lift], (row * (columns + 1) + column) * 3);
      if (row < rows && column < columns) {
        const a = row * (columns + 1) + column;
        indices.push(a, a + 1, a + columns + 2, a, a + columns + 2, a + columns + 1);
      }
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  return geometry;
}

/** The open mouth: one patch seated on the face over the mouth, drawn by MOUTH_FRAGMENT. */
function openMouth(mouth, colors) {
  const line = lipLine(mouth);
  const material = new ShaderMaterial({
    vertexShader: MOUTH_VERTEX,
    fragmentShader: MOUTH_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
    uniforms: {
      opening: { value: 0 },
      depth: { value: openingDepth(mouth) },
      ends: { value: line.x },
      heights: { value: line.y },
      insideColor: { value: new Color(colors.mouth) },
      teethColor: { value: new Color(colors.teeth) },
      tongueColor: { value: new Color(colors.tongue) },
      lipColor: { value: new Color(colors.lip ?? colors.lid) },
      inkColor: { value: new Color(colors.lash) },
    },
  });
  const mesh = new Mesh(seatedGrid(mouthBounds(mouth, line), mouth, LIFT.mouth), material);
  mesh.frustumCulled = false;
  mesh.renderOrder = 1;
  return {
    group: mesh,
    material,
    set(opening) {
      mesh.visible = opening > MOUTH_SHOWS_FROM;
      material.uniforms.opening.value = opening;
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

/** Frees the geometry, materials and textures of a loaded model that was never shown. */
function freeLoaded(root) {
  root.traverse((object) => {
    object.geometry?.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.filter(Boolean).forEach((material) => {
      Object.values(material).forEach((value) => value?.isTexture && value.dispose());
      material.dispose();
    });
  });
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
 * @param {boolean} [options.releaseContext=false] true makes dispose() also force the WebGL context to be lost.
 *   Leave it off when the same canvas may be used again (React StrictMode, hot reloading): a lost
 *   context cannot be got back on that canvas.
 * @returns {{ready: Promise<void>, setTalking: Function, setLook: Function, renderPose: Function, dispose: Function}}
 */
export function createTalkingHead(canvas, { model, surface, colors, outline, maxTurn, followPointer = true, animate = true, releaseContext = false } = {}) {
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
    state.mouth = ease(state.mouth, targetOpening(state.talking, heard, reducedMotion, t), 26, delta);

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
    if (state.disposed) {
      freeLoaded(gltf.scene); // dispose() ran while this was loading: nothing else holds these
      return;
    }
    const problems = surfaceProblems(spots);
    if (problems.length > 0) throw new Error(`talking-head: the surface description is not usable: ${problems.join('; ')}`);
    const palette = mergedColors(spots.colors, colors);
    Object.entries(palette).forEach(([name, color]) => {
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
    face.mouth = openMouth(spots.mouth, palette);
    materials.openMouth = face.mouth.material;
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
  const visibility = new IntersectionObserver((entries) => {
    const entry = entries[entries.length - 1]; // the newest, when several changes arrive together
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
      if (releaseContext) renderer.forceContextLoss();
    },
  };
}

const analysers = new WeakMap();

/**
 * Listens to an <audio> or <video> element and reports how loud it is, for `setTalking`:
 *
 *   const voice = audioLevel(audioElement);           // null when it cannot listen without muting the sound
 *   audioElement.addEventListener('play', () => { voice?.resume(); head.setTalking(true, voice?.level); });
 *   audioElement.addEventListener('pause', () => head.setTalking(false));
 *
 * Call it from a click or key press the first time (browsers only start audio after one).
 *
 * The sound is routed through a Web Audio analyser and on to the speakers. A file from another origin
 * can only be analysed when it is fetched with CORS: give the element `crossorigin="anonymous"` and serve
 * the file with Access-Control-Allow-Origin. Without that, analysing it would silence it, so this
 * returns null, warns in the console and leaves the element alone; `setTalking(true)` with no level
 * then gives a speech-like rhythm instead.
 *
 * An element can be connected to Web Audio only once in its life. After `close()` the element stays
 * routed to the closed context, so make a new <audio> element rather than calling this again on it.
 * @param {HTMLMediaElement} element
 * @returns {{level: () => number, resume: () => Promise<void>, close: () => Promise<void>} | null}
 */
export function audioLevel(element) {
  let entry = analysers.get(element);
  if (!entry) {
    if (silencedByAnalyser(element.currentSrc || element.src, window.location.href, element.crossOrigin)) {
      console.warn('talking-head: this audio comes from another origin without CORS, so listening to it would mute it. '
        + 'Add crossorigin="anonymous" and serve it with Access-Control-Allow-Origin. Talking without a level instead.');
      return null;
    }
    const context = new (window.AudioContext || window.webkitAudioContext)();
    let source;
    try {
      source = context.createMediaElementSource(element);
    } catch (error) {
      context.close();
      console.warn(`talking-head: cannot listen to this element (${error.message}). It may already be connected to Web Audio.`);
      return null;
    }
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    analyser.connect(context.destination);
    entry = { context, analyser, samples: new Uint8Array(analyser.fftSize) };
    analysers.set(element, entry);
  }
  const current = entry;
  return {
    level() {
      if (current.context.state === 'closed') return 0;
      current.analyser.getByteTimeDomainData(current.samples);
      return mouthFromSound(current.samples);
    },
    resume: () => current.context.resume(),
    close: () => {
      if (analysers.get(element) === current) analysers.delete(element);
      return current.context.state === 'closed' ? Promise.resolve() : current.context.close();
    },
  };
}
