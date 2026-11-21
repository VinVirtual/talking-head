"""
The pure maths of the pipeline: no Blender, no numpy, no files. Everything here is unit tested.

The functions that take coordinates work on plain numbers and, unchanged, on numpy arrays, so the
Blender steps use the very same code the tests check.

Spaces used throughout:
  picture pixels   x right, y down, from the top-left of the portrait
  the plane        x right, y up, the portrait's longer side spans -1 to 1 (the model is built in it)
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

Number = Any  # a float, or a numpy array of them

DEFAULT_CONFIG: Mapping[str, Any] = {
    "faces": 24000,          # triangles in the finished head
    "texture": 2048,         # side of the baked texture, pixels
    "budgetKB": 1500,        # the GLB may not be larger than this
    "jpegQuality": 88,
    "front": "-y",           # which way the raw mesh faces after import: -y, +y, -x or +x
    "up": "+z",              # which way is up in the raw mesh after import: +z, +y, -y, +x, -x or -z
    "remesh": True,          # rebuild the surface from its volume (closes holes, joins parts)
    "voxel": 0.007,          # size of the rebuild's cells, in plane units
    "removePlate": "auto",   # auto, on or off: remove a flat backing plate some generators add
    "jawDrop": None,         # how far the chin drops, plane units; None means 15% of mouth-to-chin
    "maxTurn": 0.6,          # the furthest the viewer turns the head, radians
}
FRONT_TURNS = {"-y": 0, "+x": 90, "+y": 180, "-x": 270}  # degrees about the up axis that bring the front to -y
UP_TURNS = {"+z": None, "+y": ("X", 90), "-y": ("X", -90), "+x": ("Y", -90), "-x": ("Y", 90), "-z": ("X", 180)}  # axis and degrees that bring it to +z
TEXTURE_SIZES = (512, 1024, 2048, 4096)
MIN_FACES = 2000
JAW_DROP_SHARE = 0.15
JAW_SHARE = 0.45  # how much of the chin's drop the middle of the open mouth moves by
MIN_JPEG_QUALITY = 70
MIN_TEXTURE = 512


def _clip01(value: Number) -> Number:
    if hasattr(value, "clip"):
        return value.clip(0, 1)
    return min(1.0, max(0.0, value))


def smoothstep(low: float, high: float, value: Number) -> Number:
    """0 below `low`, 1 above `high`, an eased ramp between."""
    t = _clip01((value - low) / (high - low))
    return t * t * (3 - 2 * t)


def plane_half(width: float, height: float) -> float:
    """Half the portrait's longer side in pixels: one plane unit."""
    return max(width, height) / 2


def to_plane(point: Sequence[float], width: float, height: float) -> tuple[float, float]:
    """A picture pixel as a position on the plane."""
    half = plane_half(width, height)
    return ((point[0] - width / 2) / half, (height / 2 - point[1]) / half)


def to_pixel(point: Sequence[float], width: float, height: float) -> tuple[float, float]:
    """The reverse of `to_plane`."""
    half = plane_half(width, height)
    return (point[0] * half + width / 2, height / 2 - point[1] * half)


def find_neck(widths: Sequence[float]) -> int | None:
    """
    The row of the neck in a figure's outline, given how wide the outline is on every row from the top.
    The neck is the narrowest row below the head that has something clearly wider under it (shoulders).
    Returns None when there is no such row, for example a head with no body.
    """
    rows = [index for index, width in enumerate(widths) if width > 0]
    if len(rows) < 8:
        return None
    first, last = rows[0], rows[-1]
    widest = 0.0
    best: int | None = None
    for row in range(first, last + 1):
        width = widths[row]
        widest = max(widest, width)
        if width <= 0.8 * widest and (best is None or width < widths[best]):
            best = row
        elif best is not None and width >= 1.25 * widths[best]:
            return best  # it has widened again: `best` was the neck
    return None


def guess_marks(head_box: Sequence[float]) -> dict[str, Any]:
    """
    A first guess at where the eyes, mouth and chin are from the box around the head (left, top, right,
    bottom in pixels, hair included), using ordinary cartoon proportions. It is only a starting point.
    """
    left, top, right, bottom = head_box
    width, height = right - left, bottom - top
    centre = (left + right) / 2
    eye_y, mouth_y = top + 0.53 * height, top + 0.76 * height
    eye_half = [0.075 * width, 0.07 * height]
    return {
        "eyes": [
            {"at": [centre - 0.16 * width, eye_y], "half": list(eye_half)},
            {"at": [centre + 0.16 * width, eye_y], "half": list(eye_half)},
        ],
        "mouth": {"at": [centre, mouth_y], "half": [0.1 * width, 0.055 * height]},
        "chin": chin_from(eye_y, mouth_y),
    }


def chin_from(eye_y: float, mouth_y: float) -> float:
    """Where the chin usually is, given the eyes and the mouth (pixels, y down)."""
    return mouth_y + 0.62 * (mouth_y - eye_y)


def _is_pair(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value))


def _feature_errors(name: str, feature: Any, width: float, height: float) -> list[str]:
    if not isinstance(feature, Mapping):
        return [f"{name} must be an object with \"at\" and \"half\""]
    errors = []
    at, half = feature.get("at"), feature.get("half")
    if not _is_pair(at):
        errors.append(f"{name}.at must be [x, y] in picture pixels")
    elif not (0 <= at[0] <= width and 0 <= at[1] <= height):
        errors.append(f"{name}.at {list(at)} is outside the {width:g}x{height:g} picture")
    if not _is_pair(half):
        errors.append(f"{name}.half must be [half-width, half-height] in picture pixels")
    elif half[0] <= 0 or half[1] <= 0:
        errors.append(f"{name}.half must be larger than zero")
    return errors


def validate_marks(marks: Any, width: float, height: float) -> list[str]:
    """Everything wrong with a marks file, as sentences. An empty list means it can be used."""
    if not isinstance(marks, Mapping):
        return ["marks must be a JSON object"]
    errors: list[str] = []
    eyes = marks.get("eyes")
    if not isinstance(eyes, (list, tuple)) or len(eyes) != 2:
        errors.append("eyes must be a list of two eyes, the picture's left one first")
    else:
        for index, eye in enumerate(eyes):
            errors.extend(_feature_errors(f"eyes[{index}]", eye, width, height))
    errors.extend(_feature_errors("mouth", marks.get("mouth"), width, height))
    if errors:
        return errors
    mouth_y = marks["mouth"]["at"][1]
    if any(eye["at"][1] >= mouth_y for eye in eyes):
        errors.append("the mouth must be below both eyes")
    if eyes[0]["at"][0] >= eyes[1]["at"][0]:
        errors.append("eyes[0] must be the one on the left of the picture")
    chin = marks.get("chin")
    if chin is not None:
        if not isinstance(chin, (int, float)) or isinstance(chin, bool):
            errors.append("chin must be a y position in picture pixels")
        elif chin <= mouth_y or chin > height:
            errors.append("chin must be below the mouth and inside the picture")
    neck = marks.get("neck")
    if neck is not None and (not isinstance(neck, (int, float)) or isinstance(neck, bool) or not 0 < neck < height):
        errors.append("neck must be a y position inside the picture")
    tilt = marks["mouth"].get("tilt", 0)
    if not isinstance(tilt, (int, float)) or abs(tilt) > 0.6:
        errors.append("mouth.tilt must be an angle in radians between -0.6 and 0.6")
    return errors


def complete_marks(marks: Mapping[str, Any]) -> dict[str, Any]:
    """A valid marks file with the optional chin filled in."""
    eye_y = sum(eye["at"][1] for eye in marks["eyes"]) / 2
    chin = marks.get("chin")
    return {**marks, "chin": chin if chin is not None else chin_from(eye_y, marks["mouth"]["at"][1])}


def validate_config(config: Any) -> list[str]:
    """Everything wrong with a config file, as sentences."""
    if not isinstance(config, Mapping):
        return ["config must be a JSON object"]
    errors = [f"unknown setting \"{key}\"" for key in config if key not in DEFAULT_CONFIG]
    merged = {**DEFAULT_CONFIG, **config}

    def whole(key: str) -> bool:
        return isinstance(merged[key], int) and not isinstance(merged[key], bool)

    def number(key: str) -> bool:
        return isinstance(merged[key], (int, float)) and not isinstance(merged[key], bool)

    if not whole("faces") or merged["faces"] < MIN_FACES:
        errors.append(f"faces must be a whole number, {MIN_FACES} or more")
    if merged["texture"] not in TEXTURE_SIZES:
        errors.append(f"texture must be one of {', '.join(str(size) for size in TEXTURE_SIZES)}")
    if not number("budgetKB") or merged["budgetKB"] < 100:
        errors.append("budgetKB must be a number, 100 or more")
    if not whole("jpegQuality") or not 40 <= merged["jpegQuality"] <= 100:
        errors.append("jpegQuality must be a whole number from 40 to 100")
    if merged["front"] not in FRONT_TURNS:
        errors.append(f"front must be one of {', '.join(FRONT_TURNS)}")
    if merged["up"] not in UP_TURNS:
        errors.append(f"up must be one of {', '.join(UP_TURNS)}")
    if not isinstance(merged["remesh"], bool):
        errors.append("remesh must be true or false")
    if not number("voxel") or not 0.002 <= merged["voxel"] <= 0.05:
        errors.append("voxel must be between 0.002 and 0.05")
    if merged["removePlate"] not in ("auto", "on", "off"):
        errors.append("removePlate must be auto, on or off")
    if merged["jawDrop"] is not None and (not number("jawDrop") or not 0 < merged["jawDrop"] <= 0.2):
        errors.append("jawDrop must be null or a number between 0 and 0.2")
    if not number("maxTurn") or not 0 <= merged["maxTurn"] <= 1.2:
        errors.append("maxTurn must be between 0 and 1.2 radians")
    return errors


def merged_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_CONFIG, **config}


def decimate_ratio(faces_now: int, faces_wanted: int) -> float:
    """The share of faces to keep. Never more than all of them."""
    if faces_now <= 0:
        return 1.0
    return min(1.0, faces_wanted / faces_now)


def next_budget_step(size_bytes: int, settings: Mapping[str, int]) -> dict[str, int] | None:
    """
    What to reduce when the exported file is over its budget, one step at a time: JPEG quality first,
    then the texture's size, then the face count. `settings` has faces, texture, jpegQuality, budgetKB.
    Returns the new settings, the same settings when the file already fits, or None when nothing is left.
    """
    if size_bytes <= settings["budgetKB"] * 1024:
        return dict(settings)
    if settings["jpegQuality"] > MIN_JPEG_QUALITY:
        return {**settings, "jpegQuality": max(MIN_JPEG_QUALITY, settings["jpegQuality"] - 8)}
    if settings["texture"] > MIN_TEXTURE:
        return {**settings, "texture": settings["texture"] // 2}
    if settings["faces"] > MIN_FACES:
        return {**settings, "faces": max(MIN_FACES, int(settings["faces"] * 0.7))}
    return None


def jaw_frame(marks: Mapping[str, Any], width: float, height: float, mouth_depth: float,
              drop: float | None = None) -> dict[str, float]:
    """The numbers the jaw needs, in plane units, from complete marks and the surface depth at the mouth."""
    half = plane_half(width, height)
    centre_x, mouth_y = to_plane(marks["mouth"]["at"], width, height)
    chin_y = to_plane((0, marks["chin"]), width, height)[1]
    eye_gap = abs(marks["eyes"][1]["at"][0] - marks["eyes"][0]["at"][0]) / half
    unit = mouth_y - chin_y
    return {
        "mouthY": mouth_y,
        "chinY": chin_y,
        "centreX": centre_x,
        "reach": max(eye_gap * 1.05, marks["mouth"]["half"][0] / half * 2.2),
        "mouthDepth": mouth_depth,
        "unit": unit,
        "drop": drop if drop is not None else unit * JAW_DROP_SHARE,
    }


def jaw_pull(x: Number, y: Number, depth: Number, jaw: Mapping[str, float]) -> Number:
    """
    How much of the jaw's drop a point of the surface takes, 0 to 1. `x`, `y` are on the plane and
    `depth` is how far towards the viewer the point is. Most at the chin, nothing above the mouth,
    out at the cheeks, or back towards the neck and ears.
    """
    unit = jaw["unit"]
    below_mouth = smoothstep(0, 0.23 * unit, jaw["mouthY"] - y)
    above_throat = 1 - smoothstep(0, 0.33 * unit, jaw["chinY"] - 0.065 * unit - y)
    across = 1 - smoothstep(0.45, 1, abs(x - jaw["centreX"]) / jaw["reach"])
    forward = smoothstep(jaw["mouthDepth"] - 1.4 * unit, jaw["mouthDepth"] - 0.65 * unit, depth)
    return below_mouth * above_throat * across * forward


def mouth_drop(jaw_drop: float, opening: float, share: float = JAW_SHARE) -> float:
    """How far the middle of the open mouth moves down when the jaw is `opening` (0 to 1) open."""
    return jaw_drop * share * opening


LINE_GAP = 3        # columns with no ink that a lip line may jump over
LINE_STEP = 4       # rows a lip line may move between neighbouring columns
LINE_LEAST = 6      # a lip line shorter than this many columns is not one


def _runs(rows: Sequence[int]) -> list[float]:
    """The middles of the runs of consecutive rows."""
    middles, start = [], None
    for index, row in enumerate(rows):
        if start is None:
            start = row
        if index == len(rows) - 1 or rows[index + 1] != row + 1:
            middles.append((start + row) / 2)
            start = None
    return middles


def lip_line(columns: Sequence[Sequence[int]], centre: float, samples: int) -> tuple[int, int, list[float]] | None:
    """
    The drawn line between the lips, from the rows of line work in each column of a window around the
    mouth. Starts at the column nearest the middle that has line work close to `centre` (a row), and
    follows it left and right while it stays joined. Returns (first column, last column, `samples` rows
    evenly spread between them), or None when no line is found.
    """
    if not columns:
        return None
    reach = max(LINE_STEP * 3, len(columns) // 6)
    picked: list[float | None] = []
    for rows in columns:
        middles = [middle for middle in _runs(sorted(rows)) if abs(middle - centre) <= reach]
        picked.append(min(middles, key=lambda middle: abs(middle - centre)) if middles else None)
    middle = len(columns) // 2
    start_at = next((index for offset in range(len(columns))
                     for index in (middle - offset, middle + offset)
                     if 0 <= index < len(columns) and picked[index] is not None), None)
    if start_at is None:
        return None
    kept = {start_at: picked[start_at]}
    for direction in (-1, 1):
        last, gap, index = start_at, 0, start_at + direction
        while 0 <= index < len(columns) and gap <= LINE_GAP:
            value = picked[index]
            if value is not None and abs(value - kept[last]) <= LINE_STEP * (1 + abs(index - last)) / 2 + 1:
                kept[index], last, gap = value, index, 0
            else:
                gap += 1
            index += direction
    first, final = min(kept), max(kept)
    if final - first + 1 < LINE_LEAST:
        return None
    known = sorted(kept)
    rows_out = []
    for step in range(samples):
        column = first + (final - first) * step / (samples - 1)
        after = next(index for index in known if index >= column)
        before = max(index for index in known if index <= column)
        share = 0 if after == before else (column - before) / (after - before)
        rows_out.append(kept[before] * (1 - share) + kept[after] * share)
    return first, final, rows_out


SURFACE_VERSION = 1
COLOUR_NAMES = ("lid", "lash", "mouth", "tongue", "teeth")  # "lip" is optional


def _grid_errors(name: str, grid: Any) -> list[str]:
    if not isinstance(grid, Mapping):
        return [f"{name}.grid is missing"]
    cols, rows, depth = grid.get("cols"), grid.get("rows"), grid.get("depth")
    if not isinstance(cols, int) or not isinstance(rows, int) or cols < 2 or rows < 2:
        return [f"{name}.grid needs cols and rows of 2 or more"]
    if not isinstance(depth, (list, tuple)) or len(depth) != cols * rows:
        return [f"{name}.grid.depth must have cols x rows numbers"]
    if not isinstance(grid.get("span"), (int, float)) or grid["span"] <= 0:
        return [f"{name}.grid.span must be larger than zero"]
    return []


def validate_surface(surface: Any) -> list[str]:
    """Everything missing from the JSON written beside the GLB, as sentences."""
    if not isinstance(surface, Mapping):
        return ["the surface file must be a JSON object"]
    errors: list[str] = []
    if surface.get("version") != SURFACE_VERSION:
        errors.append(f"version must be {SURFACE_VERSION}")
    eyes = surface.get("eyes")
    features = [("mouth", surface.get("mouth"))]
    if not isinstance(eyes, (list, tuple)) or len(eyes) != 2:
        errors.append("eyes must be a list of two")
    else:
        features += [(f"eyes[{index}]", eye) for index, eye in enumerate(eyes)]
    for name, feature in features:
        if not isinstance(feature, Mapping) or not _is_pair(feature.get("at")) or not _is_pair(feature.get("half")):
            errors.append(f"{name} needs \"at\" and \"half\"")
            continue
        errors.extend(_grid_errors(name, feature.get("grid")))
    line = surface.get("mouth", {}).get("line") if isinstance(surface.get("mouth"), Mapping) else None
    if line is not None and (not isinstance(line, Mapping) or not _is_pair(line.get("x"))
                             or not isinstance(line.get("y"), (list, tuple)) or len(line["y"]) < 2):
        errors.append("mouth.line needs \"x\" (from, to) and \"y\" (two or more heights)")
    jaw = surface.get("jaw")
    if not isinstance(jaw, Mapping) or not all(isinstance(jaw.get(key), (int, float)) for key in ("drop", "share")):
        errors.append("jaw needs \"drop\" and \"share\"")
    elif jaw["drop"] <= 0:
        errors.append("jaw.drop must be larger than zero")
    colours = surface.get("colors")
    if not isinstance(colours, Mapping):
        errors.append("colors is missing")
    else:
        errors.extend(f"colors.{name} must be a #rrggbb colour" for name in COLOUR_NAMES if not is_hex_colour(colours.get(name)))
    return errors


def is_hex_colour(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 7 and value[0] == "#"
            and all(character in "0123456789abcdefABCDEF" for character in value[1:]))


def to_hex(rgb: Sequence[float]) -> str:
    """A colour with channels from 0 to 1 as #rrggbb."""
    return "#" + "".join(f"{round(min(1.0, max(0.0, channel)) * 255):02x}" for channel in rgb)
