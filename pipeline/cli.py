"""talking-head's command line. Run `head help` for the list of commands."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))

import glb  # noqa: E402
import picture  # noqa: E402
import headmath  # noqa: E402
import server  # noqa: E402

MIN_BLENDER = (4, 2)
MIN_NODE = 18
MIN_PYTHON = (3, 9)
DEFAULT_SPACE = "tencent/Hunyuan3D-2"
PICTURE_KINDS = (".png", ".jpg", ".jpeg")
MESH_KINDS = (".glb", ".gltf", ".obj", ".ply", ".stl")
PREVIEW_WAIT_SECONDS = 120
BLENDER_PLACES = ("/snap/bin/blender", "/usr/bin/blender", "/usr/local/bin/blender",
                  "/Applications/Blender.app/Contents/MacOS/Blender")
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
CHROME_PLACES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",)


def out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def fail(message: str) -> "SystemExit":
    return SystemExit(f"head: {message}")


# ---------------------------------------------------------------- finding programs

def find_blender() -> str | None:
    chosen = os.environ.get("HEAD_BLENDER")
    if chosen:
        return chosen if Path(chosen).exists() else None
    return shutil.which("blender") or next((place for place in BLENDER_PLACES if Path(place).exists()), None)


def blender_version(blender: str) -> tuple[int, ...] | None:
    try:
        text = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r"Blender (\d+)\.(\d+)", text)
    return (int(found.group(1)), int(found.group(2))) if found else None


def find_chrome() -> str | None:
    chosen = os.environ.get("HEAD_CHROME")
    if chosen:
        return chosen if Path(chosen).exists() else None
    for name in CHROME_NAMES:
        if shutil.which(name):
            return shutil.which(name)
    return next((place for place in CHROME_PLACES if Path(place).exists()), None)


def find_three() -> Path | None:
    places = [os.environ.get("HEAD_THREE"), SKILL / "node_modules" / "three", Path.cwd() / "node_modules" / "three"]
    for place in places:
        if place and (Path(place) / "build" / "three.module.js").exists():
            return Path(place)
    return None


def generator_python() -> str:
    return os.environ.get("HEAD_PYTHON") or sys.executable


def python_can(code: str, python: str | None = None) -> bool:
    """True when a one-line Python probe succeeds. -I and the skill folder as cwd keep the current folder,
    PYTHONPATH and user site-packages off the import path, so a planted gradio_client.py there cannot run."""
    try:
        return subprocess.run([python or generator_python(), "-I", "-c", code], capture_output=True, cwd=SKILL, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------- projects

class Project:
    """A project folder: portrait, config.json, marks.json, work/ and out/."""

    def __init__(self, folder: str) -> None:
        self.root = Path(folder).resolve()
        if not self.root.is_dir():
            raise fail(f"{folder} is not a folder. Start one with:  head new {folder} your-portrait.png")
        self.work, self.out = self.root / "work", self.root / "out"
        self.marks_path, self.config_path = self.root / "marks.json", self.root / "config.json"
        self.model, self.surface, self.preview = self.out / "head.glb", self.out / "head.json", self.out / "preview.png"

    @property
    def picture(self) -> Path:
        for kind in PICTURE_KINDS:
            if (self.root / f"portrait{kind}").exists():
                return self.root / f"portrait{kind}"
        raise fail(f"no portrait.png or portrait.jpg in {self.root}")

    def config(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        stored = read_json(self.config_path) if self.config_path.exists() else {}
        config = {**stored, **{key: value for key, value in (overrides or {}).items() if value is not None}}
        errors = headmath.validate_config(config)
        if errors:
            raise fail("config is not usable:\n  " + "\n  ".join(errors))
        return headmath.merged_config(config)

    def raw_mesh(self) -> Path:
        for kind in MESH_KINDS:
            if (self.work / f"raw{kind}").exists():
                return self.work / f"raw{kind}"
        raise fail(f"no shape yet. Run:  head shape {self.root} --source inflate   (or --mesh FILE, or --source space)")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise fail(f"cannot read {path}: {error}") from error


def picture_size(path: Path) -> tuple[int, int]:
    """Width and height of a PNG or JPEG, read from the file's own header."""
    try:
        return picture.size_of(Path(path).read_bytes())
    except (OSError, picture.PictureError) as error:
        raise fail(f"{path} is not a PNG or JPEG picture ({error})") from error


def same_file(first: Path, second: Path) -> bool:
    try:
        return first.exists() and second.exists() and first.samefile(second)
    except OSError:
        return False


def place_file(source: Path, target: Path, replaces: list[Path]) -> None:
    """
    Copies `source` to `target` through a temporary name, then removes the files it replaces. Nothing is
    removed until the copy has succeeded, and nothing at all happens when source and target are one file.
    """
    if not same_file(source, target):
        partial = target.with_name(target.name + ".part")
        try:
            shutil.copyfile(source, partial)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
    for old in replaces:
        if old != target and not same_file(old, target):
            old.unlink(missing_ok=True)


def run_blender(project: Project, job: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Runs one Blender task and returns its result. Only one Blender runs at a time."""
    blender = find_blender()
    if blender is None:
        raise fail("Blender was not found. Install it (https://www.blender.org/download/) or set HEAD_BLENDER to its path.")
    project.work.mkdir(parents=True, exist_ok=True)
    job_path = project.work / f"job-{job['task']}.json"
    job_path.write_text(json.dumps(job, indent=2))
    command = [blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(HERE / "blender" / "job.py"), "--", str(job_path)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    result: dict[str, Any] | None = None
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        if line.startswith("head-result: "):
            result = json.loads(line[len("head-result: "):])
        elif line.startswith("head: ") or verbose:
            out("  " + line[len("head: "):] if line.startswith("head: ") else "  | " + line)
    code = process.wait()
    (project.work / f"blender-{job['task']}.log").write_text("\n".join(lines) + "\n")
    if code != 0 or result is None:
        tail = "\n".join("  | " + line for line in lines[-25:])
        raise fail(f"the Blender step \"{job['task']}\" failed (exit {code}). Its last lines:\n{tail}\n"
                   f"  full log: {project.work / ('blender-' + job['task'] + '.log')}")
    return result


def load_marks(project: Project) -> dict[str, Any]:
    if not project.marks_path.exists():
        raise fail(f"no marks.json yet. Run:  head mark {project.root} --auto   (then check them:  head mark {project.root} --page)")
    marks = read_json(project.marks_path)
    errors = headmath.validate_marks(marks, *picture_size(project.picture))
    if errors:
        raise fail("marks.json is not usable:\n  " + "\n  ".join(errors))
    return marks


# ---------------------------------------------------------------- commands

def command_check(args: argparse.Namespace) -> int:
    good = True

    def line(ok: bool, text: str, *fix: str, needed: bool = True) -> None:
        nonlocal good
        good = good and (ok or not needed)
        out(("ok   " if ok else ("MISS " if needed else "note ")) + text)
        for hint in fix if not ok else ():
            out("       " + hint)

    line(sys.version_info >= MIN_PYTHON, f"python {sys.version.split()[0]}", f"needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")
    blender = find_blender()
    version = blender_version(blender) if blender else None
    if version is None:
        line(False, "blender", "install Blender 4.2 or newer (https://www.blender.org/download/, or: snap install blender --classic)",
             "or set HEAD_BLENDER to the blender program")
    else:
        line(version >= MIN_BLENDER, f"blender {version[0]}.{version[1]} ({blender})", f"needs Blender {MIN_BLENDER[0]}.{MIN_BLENDER[1]} or newer")
    node = shutil.which("node")
    node_major = 0
    if node:
        try:
            node_major = int(subprocess.run([node, "-p", "process.versions.node.split('.')[0]"], capture_output=True, text=True, timeout=30).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            node_major = 0
    line(node_major >= MIN_NODE, f"node {node_major or ''}".strip() + " (runs the viewer's unit tests)", f"install Node.js {MIN_NODE} or newer",
         needed=False)
    chrome = find_chrome()
    line(chrome is not None, f"chrome ({chrome})" if chrome else "chrome or chromium (draws the preview sheet)",
         "install Google Chrome or Chromium, or set HEAD_CHROME", needed=False)
    three = find_three()
    line(three is not None, f"three.js ({three})" if three else "three.js for the preview and example pages",
         f"cd {SKILL} && npm install     (needed by head preview, head mark --page, head serve and head test --browser)", needed=False)

    has_client = python_can("import gradio_client")
    line(has_client, "gradio_client (only for --source space)", f"{generator_python()} -m pip install gradio_client", needed=False)
    if not args.online:
        out("note the public generator was not contacted (add --online to check it; that sends a request to huggingface.co)")
    else:
        reachable, detail = space_status(args.space)
        line(reachable, f"public generator {args.space}: {detail}",
             "not needed: use  head shape <project> --source inflate  or  --mesh FILE", needed=False)
    out("all good" if good else "something needed is missing: fix the MISS lines above and run this again")
    return 0 if good else 1


def space_status(space: str) -> tuple[bool, str]:
    """Whether the Hugging Face Space answers and says it is running. Sends nothing but the request."""
    try:
        with urllib.request.urlopen(f"https://huggingface.co/api/spaces/{space}", timeout=15) as response:
            stage = (json.load(response).get("runtime") or {}).get("stage", "unknown")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return False, f"not reachable ({error})"
    return stage == "RUNNING", f"stage {stage} (free quota may still refuse a request)"


def command_new(args: argparse.Namespace) -> int:
    source = Path(args.portrait)
    if source.suffix.lower() not in PICTURE_KINDS or not source.is_file():
        raise fail(f"{args.portrait} must be an existing PNG or JPEG picture")
    data = source.read_bytes()
    try:
        width, height = picture.size_of(data)
    except picture.PictureError as error:
        raise fail(f"{args.portrait} cannot be read: {error}") from error
    turned = picture.orientation_of(data)
    if turned != picture.UPRIGHT:
        raise fail(f"{args.portrait} has an EXIF orientation tag ({turned}): viewers show it turned, but Blender and the\n"
                   "      marking page use the pixels as stored. Rotate it upright in an image editor, save it without the tag, and try again.")
    folder = Path(args.project)
    folder.mkdir(parents=True, exist_ok=True)
    kind = ".png" if source.suffix.lower() == ".png" else ".jpg"
    target = folder / f"portrait{kind}"
    place_file(source, target, [folder / f"portrait{old}" for old in PICTURE_KINDS])
    config_path = folder / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(dict(headmath.DEFAULT_CONFIG), indent=2) + "\n")
    out(f"project {folder} ready: portrait{kind} ({width}x{height}), config.json")
    if max(width, height) < 700:
        out("note: the picture is small. The texture can never be sharper than the picture it comes from.")
    out(f"next: head shape {folder} --source inflate   (offline)   or   --mesh FILE   or   --source space --yes-upload   (uploads the picture)")
    return 0


UPLOAD_WARNING = (
    "--source space UPLOADS the portrait to Hugging Face (huggingface.co), to the public Space {space},\n"
    "  a demo run by Tencent. The model's licence (Tencent Hunyuan 3D 2.0 Community License) does not apply in\n"
    "  the European Union, the United Kingdom or South Korea: do not use it, or show its output, there.\n"
    "  It also needs a separate licence above 1 million monthly users, and forbids using the output to train other AI models.\n"
    "  The upload is anonymous (no saved Hugging Face login is sent), but treat the picture as public."
)


def upload_agreed(args: argparse.Namespace) -> bool:
    """Whether the user agreed to the upload: --yes-upload, or a typed yes when someone is at the keyboard."""
    out(UPLOAD_WARNING.format(space=args.space))
    if args.yes_upload:
        return True
    if not sys.stdin.isatty():
        out("not uploading: add --yes-upload to agree to the upload")
        return False
    try:
        answer = input("  Upload it? Type yes to go ahead: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def command_shape(args: argparse.Namespace) -> int:
    project = Project(args.project)
    source = args.source or ("file" if args.mesh else None)
    if source is None:
        raise fail("say where the shape comes from:\n"
                   "  --source inflate   made on this machine from the picture's outline (simple, no face detail)\n"
                   "  --mesh FILE        a mesh you already have from any tool (GLB, glTF, OBJ, PLY, STL)\n"
                   "  --source space --yes-upload   the public Hunyuan3D-2 demo on Hugging Face. This UPLOADS the picture.")
    project.work.mkdir(parents=True, exist_ok=True)
    old_meshes = [project.work / f"raw{kind}" for kind in MESH_KINDS]
    if source == "file":
        mesh = Path(args.mesh or "")
        if mesh.suffix.lower() not in MESH_KINDS or not mesh.is_file():
            raise fail(f"--mesh must be an existing file of one of these kinds: {', '.join(MESH_KINDS)}")
        target = project.work / f"raw{mesh.suffix.lower()}"
        place_file(mesh, target, old_meshes)
        out(f"using your mesh: {target}")
    elif source == "inflate":
        marks = load_marks(project) if project.marks_path.exists() else None
        fresh = project.work / "raw.new.glb"
        run_blender(project, {"task": "inflate", "picture": str(project.picture), "marks": marks, "out": str(fresh)}, args.verbose)
        place_file(fresh, project.work / "raw.glb", old_meshes + [fresh])
    else:
        if not upload_agreed(args):
            return 1
        fresh = project.work / "raw.download.glb"
        command = [generator_python(), str(HERE / "generate_space.py"), str(project.picture), str(fresh),
                   "--space", args.space, "--seed", str(args.seed)]
        if subprocess.run(command).returncode != 0:
            fresh.unlink(missing_ok=True)
            return 1
        place_file(fresh, project.work / "raw.glb", old_meshes + [fresh])
    (project.work / "shape.json").write_text(json.dumps({"source": source, "space": args.space if source == "space" else None}) + "\n")
    out(f"next: head build {project.root}" if project.marks_path.exists() else f"next: head mark {project.root} --auto")
    return 0


def command_mark(args: argparse.Namespace) -> int:
    project = Project(args.project)
    size = picture_size(project.picture)
    if args.set:
        marks = read_json(Path(args.set))
        errors = headmath.validate_marks(marks, *size)
        if errors:
            raise fail(f"{args.set} is not usable:\n  " + "\n  ".join(errors))
        project.marks_path.write_text(json.dumps(marks, indent=2) + "\n")
        out(f"marks copied to {project.marks_path}")
    if args.auto:
        if project.marks_path.exists() and not args.force:
            raise fail(f"{project.marks_path} already exists. Add --force to replace it with a new estimate.")
        marks = run_blender(project, {"task": "marks", "picture": str(project.picture), "out": str(project.marks_path)}, args.verbose)["marks"]
        errors = headmath.validate_marks(marks, *size)
        if errors:
            out("the estimate is not usable as it is:\n  " + "\n  ".join(errors))
        out(f"eyes at {marks['eyes'][0]['at']} and {marks['eyes'][1]['at']}, mouth at {marks['mouth']['at']}, chin {marks['chin']}")
    if args.page:
        return mark_page(project, size, args.port)
    if not (args.set or args.auto):
        marks = load_marks(project)
        out(json.dumps(marks, indent=2))
        out("these marks are valid" + (" (still the automatic estimate: check them with --page)" if marks.get("estimated") else ""))
    return 0


def mark_page(project: Project, size: tuple[int, int], port: int) -> int:
    saved = threading.Event()

    def on_save(name: str, marks: Any) -> tuple[int, str]:
        if name != "marks":
            return 400, "unknown save"
        errors = headmath.validate_marks(marks, *size)
        if errors:
            return 422, "; ".join(errors)
        project.marks_path.write_text(json.dumps(marks, indent=2) + "\n")
        saved.set()
        return 200, f"saved to {project.marks_path}"

    routes = server.Routes(SKILL, project.root, find_three(), on_save)
    web, address = server.start(routes, port)
    out(f"open this page in a browser, drag the shapes onto the eyes and mouth, and press Save:\n"
        f"  {address}/pages/mark.html?picture=/project/{project.picture.name}#token={routes.token}")
    out("waiting for Save (Ctrl-C to stop without saving)")
    try:
        saved.wait()
    except KeyboardInterrupt:
        out("stopped without saving")
        return 1
    finally:
        server.stop(web)
    out(f"marks saved to {project.marks_path}")
    return 0


def command_build(args: argparse.Namespace) -> int:
    project = Project(args.project)
    overrides = {"faces": args.faces, "texture": args.texture, "budgetKB": args.budget, "front": args.front, "up": args.up,
                 "remesh": False if args.no_remesh else None}
    config = project.config(overrides)
    marks = load_marks(project)
    if marks.get("estimated"):
        out("note: marks.json is still the automatic estimate. Look at the preview; fix with  head mark <project> --page")
    project.out.mkdir(parents=True, exist_ok=True)
    job = {"task": "build", "picture": str(project.picture), "mesh": str(project.raw_mesh()), "marks": marks, "config": config,
           "outModel": str(project.model), "outSurface": str(project.surface)}
    result = run_blender(project, job, args.verbose)
    if result["fit"]["headOverlap"] < 0.8:
        out(f"warning: the shape only overlaps the picture's head by {result['fit']['headOverlap']:.0%}. "
            "Is the mesh facing the right way (--front)? Is it a shape of this picture?")
    if result["jawPoints"] == 0:
        out("warning: the jaw moves nothing. The mouth and chin marks are not on the front of the head: check the marks and --front.")
    out(f"next: head preview {project.root}   then look at {project.preview}")
    return 0 if result["underBudget"] and result["jawPoints"] > 0 else 1


def need_three() -> Path:
    three = find_three()
    if three is None:
        raise fail(f"three.js is not installed for the local pages. Run:  cd {SKILL} && npm install   (or set HEAD_THREE)")
    return three


def run_page(page: str, project: Path | None, handle: Callable[[str, Any, dict], bool], wait: float) -> dict:
    """
    Opens one of the skill's pages in headless Chrome and collects what it saves. `handle(name, payload,
    state)` stores a save and returns True when the page is finished. The page gets this run's token in
    the address after "#", which never leaves the browser.
    """
    chrome = find_chrome()
    if chrome is None:
        raise fail("Chrome or Chromium was not found (it draws the page). Install one or set HEAD_CHROME.")
    done = threading.Event()
    state: dict[str, Any] = {}

    def on_save(name: str, payload: Any) -> tuple[int, str]:
        try:
            finished = handle(name, payload, state)
        except ValueError as error:
            return 400, str(error)
        if finished:
            done.set()
        return 200, "saved"

    routes = server.Routes(SKILL, project, need_three(), on_save)
    web, address = server.start(routes)
    url = f"{address}/{page}#token={routes.token}"
    with tempfile.TemporaryDirectory(prefix="talking-head-chrome-") as profile:
        command = [chrome, "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                   "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--mute-audio",
                   "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={profile}", url]
        browser = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            finished = done.wait(wait)
        finally:
            browser.terminate()
            try:
                browser.wait(10)
            except subprocess.TimeoutExpired:
                browser.kill()
            server.stop(web)
    if not finished:
        raise fail(f"the browser did not finish {page.split('?')[0]} within {wait:.0f} seconds (does headless Chrome have WebGL here?)")
    if "error" in state:
        raise fail(f"the page reported: {state['error']}")
    return state


def command_preview(args: argparse.Namespace) -> int:
    project = Project(args.project)
    for needed in (project.model, project.surface):
        if not needed.exists():
            raise fail(f"{needed} is missing. Run:  head build {project.root}")

    def handle(name: str, payload: Any, state: dict) -> bool:
        if name == "preview":
            project.preview.write_bytes(server.png_from_data_url((payload or {}).get("png")))
        elif name == "error":
            state["error"] = str((payload or {}).get("message"))[:2000]
        else:
            raise ValueError("unknown save")
        return True

    run_page(f"pages/preview.html?model=/project/out/head.glb&surface=/project/out/head.json&tile={args.tile}",
             project.root, handle, PREVIEW_WAIT_SECONDS)
    out(f"preview sheet: {project.preview}\nLook at it: front, turned both ways, blink, mouth open, turned while talking.")
    return 0


def verify_project(project: Project, budget_kb: float) -> list[tuple[bool, str]]:
    """Every check `verify` makes, as (passed, sentence)."""
    checks: list[tuple[bool, str]] = []
    if not project.model.exists():
        return [(False, f"{project.model} does not exist")]
    size = project.model.stat().st_size
    try:
        info = glb.summary(glb.read_glb(project.model))
    except (glb.GlbError, KeyError, IndexError, TypeError) as error:
        return [(False, f"the GLB cannot be read: {error}")]
    checks.append((True, f"GLB loads: {info['triangles']} triangles, {info['points']} points, {info['meshes']} mesh"))
    checks.append((info["meshes"] == 1, "exactly one mesh"))
    checks.append(("jaw" in info["targets"], f"has the \"jaw\" morph target (targets: {', '.join(info['targets']) or 'none'})"))
    checks.append((len(info["images"]) == 1, f"one texture ({', '.join(info['images']) or 'none'})"))
    checks.append((size <= budget_kb * 1024, f"size {size / 1024:.0f} KB is within the {budget_kb:g} KB budget"))
    if not project.surface.exists():
        checks.append((False, f"{project.surface} does not exist"))
        return checks
    try:
        errors = headmath.validate_surface(json.loads(project.surface.read_text()))
    except (OSError, json.JSONDecodeError) as error:
        errors = [f"cannot be read: {error}"]
    checks.append((not errors, "the surface JSON is complete" if not errors else "the surface JSON: " + "; ".join(errors)))
    return checks


def command_verify(args: argparse.Namespace) -> int:
    project = Project(args.project)
    budget = args.budget if args.budget is not None else project.config()["budgetKB"]
    checks = verify_project(project, budget)
    for passed, text in checks:
        out(("ok   " if passed else "FAIL ") + text)
    good = all(passed for passed, _ in checks)
    out("verified" if good else "not verified: see the FAIL lines")
    return 0 if good else 1


def command_serve(args: argparse.Namespace) -> int:
    three = need_three()
    web, address = server.start(server.Routes(SKILL, Path(args.project).resolve() if args.project else None, three), args.port)
    out(f"serving the skill folder at {address}\n  example: {address}/examples/\n"
        + "Ctrl-C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop(web)
    return 0


BROWSER_TEST_WAIT = 120


def browser_tests() -> int:
    """Runs tests/browser/viewer.html in headless Chrome against the example head and prints its results."""
    def handle(name: str, payload: Any, state: dict) -> bool:
        if name != "results":
            raise ValueError("unknown save")
        state["results"] = payload
        return True

    results = run_page("tests/browser/viewer.html", None, handle, BROWSER_TEST_WAIT)["results"]
    failed = 0
    for result in results:
        passed = bool(result.get("ok"))
        failed += not passed
        out(("ok   " if passed else "FAIL ") + str(result.get("name")) + ("" if passed else f": {result.get('message')}"))
    out(f"browser: {len(results) - failed} passed, {failed} failed")
    return 0 if failed == 0 and results else 1


def command_test(args: argparse.Namespace) -> int:
    codes = [subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(SKILL / "tests"), "-p", "test_*.py"], cwd=SKILL).returncode]
    node = shutil.which("node")
    if node is None:
        out("node was not found: the viewer's tests were skipped")
        codes.append(1)
    else:
        tests = sorted(str(path) for path in (SKILL / "tests").glob("*.test.mjs"))
        codes.append(subprocess.run([node, "--test", *tests], cwd=SKILL).returncode)
    if args.browser:
        codes.append(browser_tests())
    return 0 if not any(codes) else 1


# ---------------------------------------------------------------- arguments

def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="head", description="One illustrated portrait in, a small talking 3D head for a web page out.")
    commands = top.add_subparsers(dest="command", metavar="command")

    def add(name: str, function: Any, summary: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary)
        sub.set_defaults(run=function)
        return sub

    check = add("check", command_check, "check Python, Blender, node, Chrome, three.js and the public generator")
    check.add_argument("--online", action="store_true", help="also ask huggingface.co whether the public generator is running")
    check.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)  # the old flag; offline is now the default
    check.add_argument("--space", default=DEFAULT_SPACE)

    new = add("new", command_new, "start a project folder from a portrait")
    new.add_argument("project")
    new.add_argument("portrait")

    shape = add("shape", command_shape, "get the raw 3D shape: inflate the outline, use your own mesh, or ask the public generator")
    shape.add_argument("project")
    shape.add_argument("--source", choices=("inflate", "file", "space"))
    shape.add_argument("--mesh", help="a mesh file you already have")
    shape.add_argument("--space", default=DEFAULT_SPACE, help="the Hugging Face Space to ask (with --source space)")
    shape.add_argument("--seed", type=int, default=1234)
    shape.add_argument("--yes-upload", action="store_true", help="agree to upload the picture to the public Space (with --source space)")

    mark = add("mark", command_mark, "set where the eyes, mouth and chin are: estimate, edit on a page, or copy a file")
    mark.add_argument("project")
    mark.add_argument("--auto", action="store_true", help="estimate the marks from the picture")
    mark.add_argument("--force", action="store_true", help="let --auto replace existing marks")
    mark.add_argument("--page", action="store_true", help="serve a page on this machine to drag the marks into place")
    mark.add_argument("--set", metavar="FILE", help="use this marks JSON file")
    mark.add_argument("--port", type=int, default=0)

    build = add("build", command_build, "clean and fit the shape, texture it from the picture, add the jaw, export GLB + JSON")
    build.add_argument("project")
    build.add_argument("--faces", type=int)
    build.add_argument("--texture", type=int)
    build.add_argument("--budget", type=float, help="largest GLB allowed, in KB")
    build.add_argument("--front", choices=tuple(headmath.FRONT_TURNS), help="which way the raw mesh faces (default -y)")
    build.add_argument("--up", choices=tuple(headmath.UP_TURNS), help="which way is up in the raw mesh (default +z)")
    build.add_argument("--no-remesh", action="store_true")

    preview = add("preview", command_preview, "draw a contact sheet PNG of the finished head in headless Chrome")
    preview.add_argument("project")
    preview.add_argument("--tile", type=int, default=420, help="size of one picture on the sheet, pixels")

    verify = add("verify", command_verify, "check the GLB loads, has the jaw, fits the budget, and the JSON is complete")
    verify.add_argument("project")
    verify.add_argument("--budget", type=float)

    serve = add("serve", command_serve, "serve the skill folder (and a project) on this machine, for the example page")
    serve.add_argument("project", nargs="?")
    serve.add_argument("--port", type=int, default=8437)

    test = add("test", command_test, "run the unit tests")
    test.add_argument("--browser", action="store_true", help="also run the viewer's tests in headless Chrome (needs three.js installed)")
    for sub in (shape, mark, build):
        sub.add_argument("--verbose", action="store_true", help="show everything Blender prints")
    return top


AXIS_OPTIONS = ("--front", "--up")


def joined_axis_options(arguments: list[str]) -> list[str]:
    """`--front -y` as `--front=-y`: on its own, a value starting with a dash would be read as another option."""
    joined: list[str] = []
    for argument in arguments:
        if joined and joined[-1] in AXIS_OPTIONS:
            joined[-1] = f"{joined[-1]}={argument}"
        else:
            joined.append(argument)
    return joined


def main(argv: list[str] | None = None) -> int:
    arguments = joined_axis_options(list(sys.argv[1:] if argv is None else argv))
    top = parser()
    if not arguments or arguments[0] in ("help", "-h", "--help"):
        top.print_help()
        return 0
    args = top.parse_args(arguments)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
