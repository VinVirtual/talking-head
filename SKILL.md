---
name: talking-head
description: Turn ONE illustrated portrait (a cartoon or drawn head and shoulders, front-facing) into a small 3D head for a web page that follows the pointer, blinks, and moves its jaw with a voice. Makes a GLB of about 1 MB with a "jaw" morph target plus a JSON of where the eyes and mouth sit, and ships a framework-free three.js viewer. Works offline (inflates the outline, or uses a mesh the user already has); can optionally ask the public Hunyuan3D-2 Space for a better shape, which uploads the picture. Use whenever the user wants a talking avatar, a 3D mascot or guide character for a site, a portrait that turns its head or follows the cursor, a head that lip-flaps to audio or text-to-speech, or wants to rebuild, re-mark or shrink a head made this way. Not for photos of real people, face capture, lip-sync to phonemes or deepfakes.
---

# talking-head

Pipeline: **shape** (a raw mesh of the figure) → **mark** (eyes, mouth, chin in the picture) → **build** (headless Blender: fit the mesh to the drawing, project the drawing on as the texture, add a `jaw` morph target, export GLB + JSON) → **view** (`viewer/viewer.js`, three.js).

Skill folder: `~/.claude/skills/talking-head/` (written below as `$H`). Every command is `$H/head <command> <project>`. `$H/head help` lists them.

```
$H/head                        the command line (check, new, shape, mark, build, preview, verify, serve, test)
$H/pipeline/cli.py             the commands
$H/pipeline/headmath.py        the pure maths (feature mapping, jaw, validation, size budget), unit tested
$H/pipeline/blender/           the Blender steps (job.py is the entry; build.py, fit.py, inflate.py, imaging.py, mesh.py)
$H/pipeline/generate_space.py  the one call to the public generator
$H/viewer/viewer.js, math.js   the viewer to copy into the user's site
$H/pages/                      preview sheet page and marking page (served by the command line)
$H/examples/                   a finished example (Juno, a fictional character) and a demo page
$H/references/formats.md       config.json, marks.json, head.json and viewer options
$H/README.md                   install, licences, limits
```

## Before you start: three questions

1. **Whose picture is it?** If it shows a real person (a photo or a drawn likeness), the user needs that person's consent. Ask. Do not build a head of a public figure or of anyone the user cannot speak for. This tool makes a cartoon puppet, not a likeness capture, and must not be used to impersonate.
2. **May the picture leave this machine?** `--source space` uploads it to a public Hugging Face Space run by Tencent. Never choose that yourself. Offer it, say plainly that it uploads the picture and that the model's licence does not cover the EU, UK or South Korea (README, "Licence notes"), and use it only when the user says yes. The default path, `--source inflate`, sends nothing.
3. **Is the picture suitable?** Front-facing, head and shoulders, illustrated, transparent or flat-coloured background, 1000 pixels or more on the longer side. If it is a vector drawing, rasterise it large. If the background is busy, cut the figure out first (any tool) and save a PNG with transparency.

## Workflow

1. **Check the machine**: `$H/head check`. It says how to fix what is missing. Blender is required; Chrome is needed only for the preview; three.js only for the local pages.
2. **Start a project**: `$H/head new <dir> <portrait.png>`. Put it next to the user's other asset sources, not inside the web app's source tree.
3. **Shape**: pick one.
   - `$H/head shape <dir> --source inflate`: made here from the outline. Always works. A puffy shape with a nose bump and no other face detail.
   - `$H/head shape <dir> --mesh FILE`: any mesh of this figure the user already has (GLB, glTF, OBJ, PLY, STL). If it faces another way, pass `--front` or `--up` to `build`.
   - `$H/head shape <dir> --source space`: the public generator. Only after the user agreed to the upload. It asks once. If it is refused (quota, busy), **do not retry in a loop**: fall back to inflate and say so.
4. **Mark**: `$H/head mark <dir> --auto`, then check the numbers against the picture. They are a guess.
   - If you can see the picture, compare: each eye oval should cover the whole eye including its outline; the mouth is the centre of the closed mouth with the size of the *open* mouth; the chin is the bottom of the chin.
   - To fix them, either edit `marks.json` by hand (pixels from the top-left; `references/formats.md`) or run `$H/head mark <dir> --page` and give the user the address it prints. That command waits until they press Save.
5. **Build**: `$H/head build <dir>`. About a minute. Read what it prints:
   - `fit: head overlap` should be above 0.9. Below 0.8 the mesh is probably facing the wrong way (`--front +y`, `--up +y`) or is not a shape of this picture.
   - `jaw moves N points` must not be 0. If it is, the mouth and chin marks are not on the face.
   - If it says the file is over budget it has already reduced quality, texture or faces. Tell the user what it gave up.
6. **Look**: `$H/head preview <dir>`, then **Read `out/preview.png`**. Never say it looks right without looking. Check:
   - front: does it match the picture? Features in the right place, no smears on the face;
   - turned left and right: are the sides acceptable? If not, lower `maxTurn` in `config.json` (0.4 hides most of it) and rebuild;
   - blink: do the lids cover both eyes completely, in a colour that matches the skin around them? If not, fix the eye marks (bigger `half`) or pass `colors: { lid }` to the viewer;
   - mouth open: does the opening hang from the drawing's own smile line, corners on the line's ends? The build prints `lip line found` or `not found`. If not found, or it caught the wrong line (a nose, a crease), move the mouth mark onto the line between the lips and rebuild;
   - turned, talking: do the overlays stay on the face?
   Fix the marks or config, rebuild, preview again.
7. **Verify**: `$H/head verify <dir>`. Every line must say `ok`.
8. **Put it in the page**:
   - Copy `out/head.glb` and `out/head.json` to the site's static assets, and `$H/viewer/viewer.js` and `math.js` into its source. three.js must be installed in the site (`npm i three`).
   - Mount it on a canvas the site sizes with CSS (square is best). Call `dispose()` when the component unmounts.
   - Drive the mouth: `head.setTalking(true, level)` where `level()` returns 0 to 1. For an `<audio>` element use `audioLevel(audio)` from the viewer (create it inside a click handler). For text-to-speech with no audio element, `head.setTalking(true)` gives a speech-like rhythm; call `setTalking(false)` when it ends.
   - Load the viewer lazily (dynamic `import()`) if the head is not at the top of the page: three.js is the heavy part.
   - Give the canvas an `aria-label`, and keep a static `<img>` of the portrait as the fallback for when WebGL or the files fail (`head.ready` rejects with the reason).
9. **Tell the user** what the head cannot do (README, "Limits"), which shape source was used, and, if the public generator was used, that the model's licence asks for machine-made content to be identified as such and does not cover the EU, UK or South Korea.

## What to expect, honestly

- From the front it is the drawing. Turned, it has real depth, but past about 35 degrees the sides are plain colour and stretched lines.
- Eyelids are simple shapes laid on the surface. The open mouth is shader-drawn from the drawing's own lip line. Both suit flat cartoon styles and look plain on painterly ones.
- The jaw follows loudness only. It is not lip-sync.
- With `--source inflate` the profile is a balloon with a nose. Say so when you use it.

## Changing things

- Smaller file: `--budget 800`, or `--faces 12000 --texture 1024`.
- Jaw opens too much or too little: `"jawDrop"` in `config.json` (plane units; the default is 15% of mouth-to-chin). Rebuild.
- Wrong lid or mouth colours: `createTalkingHead(canvas, { colors: { lid: '#…', mouth: '#…' } })`. No rebuild needed.
- No outline wanted, or a thicker one: `outline: false` or `outline: { width: 0.012, color: '#…' }` in the viewer options.
- A hand-made, already clean mesh: `--no-remesh` keeps its topology.

## Rules

- One Blender process at a time. Each `shape --source inflate`, `mark --auto` and `build` starts one and waits for it.
- Never upload a picture without the user's yes. Never retry the public generator in a loop.
- Do not commit `work/` folders. The GLB, the JSON and the marks are what is worth keeping.
- After changing anything in `pipeline/headmath.py` or `viewer/math.js`, run `$H/head test`.
