# File formats and options

All positions in `marks.json` are **picture pixels**: x from the left, y from the top of the portrait as you supplied it.
All positions in `head.json` are on **the plane**: x right, y up, with the portrait's longer side spanning -1 to 1. The model is built in the same space, so a position on the plane plus a depth is a point on the head.

## Project folder

```
my-head/
  portrait.png      or portrait.jpg. Transparent background, or one flat colour around the figure
  config.json       optional build settings (below)
  marks.json        eyes, mouth, chin, neck
  work/             raw mesh, job files, Blender logs. Safe to delete
  out/head.glb      the model
  out/head.json     the surface description the viewer needs
  out/preview.png   the contact sheet
```

## config.json

Every key is optional. Command-line options override the file.

| Key | Default | Meaning |
|---|---|---|
| `faces` | `24000` | triangles in the finished head (2000 or more) |
| `texture` | `2048` | side of the baked texture: 512, 1024, 2048 or 4096 |
| `budgetKB` | `1500` | the GLB may not be larger than this |
| `jpegQuality` | `88` | texture quality, 40 to 100 |
| `front` | `"-y"` | which way the raw mesh faces after import, with Z up: `-y`, `+y`, `-x` or `+x`. Generated GLBs usually face `-y` |
| `up` | `"+z"` | which way is up in the raw mesh after import: `+z`, `+y`, `-y`, `+x`, `-x` or `-z`. Blender reads glTF and OBJ as Y-up files and stands them up itself, so this is only for files written another way |
| `remesh` | `true` | rebuild the surface from its volume: closes holes and joins loose parts. Turn off for a clean hand-made mesh |
| `voxel` | `0.007` | cell size of that rebuild, in plane units (0.002 to 0.05). Smaller keeps more detail and takes longer |
| `removePlate` | `"auto"` | `auto`, `on` or `off`: remove a flat backing plate that some image-to-3D generators put behind a cut-out figure |
| `jawDrop` | `null` | how far the chin drops, in plane units. `null` means 15% of the mouth-to-chin distance |
| `maxTurn` | `0.6` | the furthest the viewer turns the head, in radians. Lower it if the sides look poor |

When the file is over `budgetKB`, the build lowers `jpegQuality` in steps of 8 down to 70, then halves `texture` down to 512, then cuts `faces` by 30% at a time down to 2000, and reports each step.

## marks.json

```json
{
  "eyes": [
    { "at": [436.5, 447.5], "half": [33.6, 39.5] },
    { "at": [586.5, 447.0], "half": [33.6, 40.1] }
  ],
  "mouth": { "at": [511.5, 575.0], "half": [49.0, 26.9] },
  "chin": 654.2,
  "neck": 672.0
}
```

- `eyes`: two, the one on the left of the picture first. `at` is the centre, `half` the half-width and half-height of an oval that covers the whole eye, outline included. The eyelid is drawn 20% larger than this.
- `mouth`: `at` is the centre of the closed mouth. `half` is the size of the **open** mouth. A lopsided smile needs nothing extra: the build traces the drawn line between the lips, and the mouth opens from that line.
- `chin` (optional): y of the bottom of the chin. Left out, it is estimated from the eyes and the mouth.
- `neck` (optional): y of the narrowest part of the neck. Left out, it is found from the outline. The head above it and the body below it are fitted to the shape separately.
- `estimated: true` is written by `head mark --auto` and makes `build` remind you to check.

## head.json

Written by `head build`. Version 1.

```json
{
  "version": 1,
  "eyes": [{ "at": [-0.1475, 0.126], "half": [0.0656, 0.0771],
             "grid": { "cols": 9, "rows": 9, "span": 1.8, "depth": [0.2654, "…81 numbers"] } }, "…"],
  "mouth": { "at": [-0.001, -0.123], "half": [0.0957, 0.0525], "grid": { "…": "…" },
             "line": { "x": [-0.0938, 0.0918], "y": [-0.1045, -0.1179, -0.1306, -0.1377, -0.1396, -0.1377, -0.1306, -0.1179, -0.1045] } },
  "jaw": { "drop": 0.0232, "share": 0.45 },
  "maxTurn": 0.6,
  "colors": { "lid": "#f6c39c", "lash": "#1d2433", "mouth": "#3a1215", "tongue": "#c9645c", "teeth": "#f4efe8", "lip": "#d88164" },
  "outline": { "width": 0.0079, "color": "#1c2433" },
  "picture": { "width": 1024, "height": 1024 }
}
```

- `grid.depth` holds how far towards the viewer the surface is, on a `cols` by `rows` grid covering `span` half-sizes either side of the feature, rows from the bottom, columns from the left.
- `mouth.line` (optional) is the drawn line between the lips: its left and right ends (`x`) and its height at evenly spaced points between them (`y`). The viewer opens the mouth from it, corners pinned. Without it the mouth opens from a straight line across the marked mouth.
- `jaw.drop` is how far the chin moves when the `jaw` morph target is at 1. `jaw.share` is how much of that the middle of the open mouth moves by.
- `colors.lid` is the middle colour of a ring just outside the eyes, with line work removed. `colors.lash` is the darkest tenth of the colours inside the eyes. `colors.lip` is the skin just below the mouth, deepened towards red, for the lower-lip rim. `mouth`, `tongue` and `teeth` are fixed defaults: override them in the viewer if they do not suit the drawing.
- `outline` is present when at least half of the figure's edge in the picture is line work.

In the GLB, the plane's x is glTF X, the plane's y is glTF Y, and depth is glTF Z (towards the camera).

## Viewer

```js
import { createTalkingHead, audioLevel } from './viewer/viewer.js';
const head = createTalkingHead(canvas, options);
```

| Option | Default | Meaning |
|---|---|---|
| `model` | required | address of the GLB |
| `surface` | required | address of `head.json`, or its contents as an object |
| `colors` | from `head.json` | any of `lid`, `lash`, `mouth`, `tongue`, `teeth`, `lip` as `#rrggbb` |
| `outline` | from `head.json` | `false` for none, or `{ width, color }` |
| `maxTurn` | from `head.json`, else `0.6` | how far the head turns to follow the pointer, radians |
| `followPointer` | `true` | listen to `pointermove` on the window |
| `animate` | `true` | `false` draws one still frame and waits for `renderPose` |
| `releaseContext` | `false` | `true` makes `dispose()` also force the WebGL context to be lost. Leave it off if the same canvas may be used again |

| Returned | Meaning |
|---|---|
| `ready` | a promise: settles when the head is loaded and drawn, rejects with the reason when it cannot load |
| `setTalking(talking, level?)` | `level` is a function returning the loudness now, 0 to 1. Without it the mouth follows a speech-like rhythm |
| `setLook(x, y)` | point the head yourself, -1 to 1 across and down |
| `renderPose({ yaw, pitch, eyes, mouth })` | draw one still pose: radians, eyes 0 open to 1 shut, mouth 0 to 1 |
| `dispose()` | stop, remove listeners, free the GPU memory (also when called before loading finished). The canvas can be used again |

`audioLevel(mediaElement)` returns `{ level, resume, close }`, or `null` when it cannot listen without muting the sound. It routes the element through a Web Audio analyser and on to the speakers. Create it inside a click or key handler. Audio from another origin needs CORS (`crossorigin="anonymous"` on the element and `Access-Control-Allow-Origin` on the file): without it, analysing would mute the element, so `audioLevel` returns `null` with a console warning and leaves playback alone. An element can be connected to Web Audio once in its life: after `close()`, use a new element. A `level` function that returns NaN or anything that is not a number is ignored, and the mouth falls back to the speech-like rhythm.

The canvas is drawn at its CSS size times the device pixel ratio (at most 2). The viewer does not draw while the canvas is off screen. With `prefers-reduced-motion: reduce` it does not sway, nod, breathe or blink; it still follows the pointer and opens its mouth.
