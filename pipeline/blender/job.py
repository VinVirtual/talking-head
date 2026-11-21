"""
The Blender side of talking-head. Run by the `head` command, never by hand:

    blender -b --python job.py -- job.json

job.json names a task ("marks", "inflate" or "build") and its inputs. Lines printed with the "head:"
prefix are progress for the user; a line starting "head-result:" carries the task's JSON result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import headmath  # noqa: E402
import imaging  # noqa: E402


def say(message: str) -> None:
    sys.stdout.write(f"head: {message}\n")
    sys.stdout.flush()


def result(values: dict) -> None:
    sys.stdout.write("head-result: " + json.dumps(values) + "\n")
    sys.stdout.flush()


def original_pixels(row: float, column: float, side: int, size: tuple[int, int]) -> tuple[float, float]:
    """A position in the padded square picture as (x, y) in the original picture."""
    width, height = size
    return column - (side - width) // 2, row - (side - height) // 2


MOUTH_OPEN_SHAPE = 0.55  # an open mouth is at least this tall for its width


def widest_band(found: np.ndarray) -> np.ndarray:
    """
    Of the separate bands of rows with something drawn in them (nose, mouth, the crease under the lip),
    keeps the widest one: the mouth is usually the widest thing drawn between the nose and the chin.
    """
    filled = found.any(1)
    bands, start = [], None
    for row, on in enumerate(list(filled) + [False]):
        if on and start is None:
            start = row
        elif not on and start is not None:
            columns = np.nonzero(found[start:row].any(0))[0]
            bands.append((columns[-1] - columns[0], start, row))
            start = None
    if not bands:
        return found
    _, top, bottom = max(bands)
    kept = np.zeros_like(found)
    kept[top:bottom] = found[top:bottom]
    return kept


def estimate_marks(colour: np.ndarray, figure: np.ndarray, size: tuple[int, int]) -> dict:
    """
    A guess at the eyes, mouth and chin: ordinary proportions inside the box around the head, then each
    feature moved to what is actually drawn near it (whites of the eyes, anything not skin for the mouth).
    """
    side = colour.shape[0]
    pad_x, pad_y = (side - size[0]) // 2, (side - size[1]) // 2
    neck = headmath.find_neck(figure.sum(1).tolist())
    head_rows = figure[:neck] if neck is not None else figure
    rows, columns = np.nonzero(head_rows.any(1))[0], np.nonzero(head_rows.any(0))[0]
    if len(rows) == 0:
        raise SystemExit("head: the picture has no figure in it (is the background transparent or one flat colour?)")
    box = (columns[0], rows[0], columns[-1], rows[-1])
    marks = headmath.guess_marks(box)
    width, height = box[2] - box[0], box[3] - box[1]

    flat = imaging.pull_push(colour, figure & ~imaging.ink_mask(colour, figure))
    light = imaging.luminance(flat)
    eye_y, mouth_y = marks["eyes"][0]["at"][1], marks["mouth"]["at"][1]
    centre = int(marks["mouth"]["at"][0])
    patch = flat[int(eye_y + 0.35 * (mouth_y - eye_y)):int(eye_y + 0.75 * (mouth_y - eye_y)), centre - int(0.1 * width):centre + int(0.1 * width)]
    skin = np.median(patch.reshape(-1, 3), axis=0)
    skin_light = float(imaging.luminance(skin))
    away = np.abs(colour - skin).max(axis=2)

    def window(at: list[float], reach_x: float, reach_y: float) -> tuple[slice, slice]:
        return (slice(max(0, int(at[1] - reach_y)), min(side, int(at[1] + reach_y))),
                slice(max(0, int(at[0] - reach_x)), min(side, int(at[0] + reach_x))))

    def moved(feature: dict, found: np.ndarray, where: tuple[slice, slice], grow: float, least: int) -> dict:
        if found.sum() < least:
            return feature
        ys, xs = np.nonzero(found)
        low_x, high_x = np.percentile(xs, [2, 98])
        low_y, high_y = np.percentile(ys, [2, 98])
        at = [where[1].start + (low_x + high_x) / 2, where[0].start + (low_y + high_y) / 2]
        return {"at": at, "half": [max(4.0, (high_x - low_x) / 2 * grow), max(4.0, (high_y - low_y) / 2 * grow)]}

    eyes = []
    for eye in marks["eyes"]:
        where = window(eye["at"], 0.14 * width, 0.13 * height)
        whites = (light[where] > skin_light + 0.07) & (np.ptp(flat[where], axis=2) < 0.12) & figure[where]
        eyes.append(moved(eye, whites, where, 1.18, int(0.0004 * width * height)))
    eye_y = (eyes[0]["at"][1] + eyes[1]["at"][1]) / 2
    mouth_guess = {**marks["mouth"], "at": [(eyes[0]["at"][0] + eyes[1]["at"][0]) / 2, marks["mouth"]["at"][1]]}
    where = window(mouth_guess["at"], 0.16 * width, 0.11 * height)
    drawn = widest_band((away[where] > 0.2) & figure[where])
    mouth = moved(mouth_guess, drawn, where, 1.1, int(0.0002 * width * height))
    mouth["half"][1] = max(mouth["half"][1], MOUTH_OPEN_SHAPE * mouth["half"][0])  # room for the mouth to open

    def original(feature: dict) -> dict:
        return {"at": [round(feature["at"][0] - pad_x, 1), round(feature["at"][1] - pad_y, 1)], "half": [round(value, 1) for value in feature["half"]]}

    out = {"eyes": [original(eye) for eye in eyes], "mouth": original(mouth), "estimated": True}
    out["chin"] = round(headmath.chin_from(eye_y - pad_y, out["mouth"]["at"][1]), 1)
    if neck is not None:
        out["neck"] = float(neck - pad_y)
        out["chin"] = round(min(out["chin"], out["neck"] - 4), 1)
    return out


def task_marks(job: dict) -> None:
    colour, alpha, size = imaging.load_picture(job["picture"])
    marks = estimate_marks(colour, imaging.figure_mask(colour, alpha, size), size)
    Path(job["out"]).write_text(json.dumps(marks, indent=2) + "\n")
    say(f"estimated marks written to {job['out']} (a guess: check them)")
    result({"marks": marks, "size": list(size)})


def task_inflate(job: dict) -> None:
    import inflate

    colour, alpha, size = imaging.load_picture(job["picture"])
    figure = imaging.figure_mask(colour, alpha, size)
    marks = job.get("marks") or estimate_marks(colour, figure, size)
    faces = inflate.inflate(figure, headmath.complete_marks(marks), size, job["out"])
    say(f"inflated shape written to {job['out']} ({faces} faces)")
    result({"faces": faces})


def task_build(job: dict) -> None:
    import build

    result(build.build(job, say))


TASKS = {"marks": task_marks, "inflate": task_inflate, "build": task_build}


def main() -> None:
    job = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
    task = TASKS.get(job.get("task"))
    if task is None:
        raise SystemExit(f"head: unknown task {job.get('task')!r}")
    task(job)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:  # Blender exits 0 after a Python error unless told otherwise
        import traceback

        traceback.print_exc()
        sys.stdout.write(f"head: failed: {type(error).__name__}: {error}\n")
        sys.stdout.flush()
        sys.exit(1)
