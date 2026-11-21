"""Picture work for the Blender steps, with numpy only (Blender's Python has numpy but no scipy or Pillow)."""
from __future__ import annotations

import bpy
import numpy as np

ALPHA_CUT = 0.5
BACKGROUND_TOLERANCE = 0.09   # how close to the border colour still counts as background
INK_SHARE = 0.62              # a pixel this much darker than its surroundings is line work


def load_picture(path: str) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """
    The picture as colour (rows from the top, sRGB values 0 to 1) and alpha, padded to a square with
    transparent pixels, plus its original (width, height).
    """
    image = bpy.data.images.load(path)
    image.alpha_mode = "STRAIGHT"
    width, height = image.size
    values = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(values)
    bpy.data.images.remove(image)
    values = values.reshape(height, width, 4)[::-1]
    side = max(width, height)
    square = np.zeros((side, side, 4), np.float32)
    top, left = (side - height) // 2, (side - width) // 2
    square[top:top + height, left:left + width] = values
    return square[..., :3].astype(float), square[..., 3].astype(float), (width, height)


def to_linear(colour: np.ndarray) -> np.ndarray:
    return np.where(colour <= 0.04045, colour / 12.92, ((colour + 0.055) / 1.055) ** 2.4)


def shifted(mask: np.ndarray, rows: int, columns: int, fill: bool) -> np.ndarray:
    out = np.full_like(mask, fill)
    height, width = mask.shape
    source = mask[max(0, -rows):height - max(0, rows), max(0, -columns):width - max(0, columns)]
    out[max(0, rows):height - max(0, -rows), max(0, columns):width - max(0, -columns)] = source
    return out


def eroded(mask: np.ndarray, rounds: int, border: bool = True) -> np.ndarray:
    """The mask shrunk by `rounds` pixels. `border` says what lies beyond the picture's edge."""
    for _ in range(rounds):
        mask = mask & shifted(mask, 1, 0, border) & shifted(mask, -1, 0, border) & shifted(mask, 0, 1, border) & shifted(mask, 0, -1, border)
    return mask


def dilated(mask: np.ndarray, rounds: int) -> np.ndarray:
    return ~eroded(~mask, rounds, border=True)


def figure_mask(colour: np.ndarray, alpha: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """
    Where the figure is, in the padded square picture. A picture with a transparent background is used
    as it is. Otherwise the background is taken to be the flat colour around the picture's border, and
    everything joined to the border in that colour is removed.
    """
    side = colour.shape[0]
    top, left = (side - size[1]) // 2, (side - size[0]) // 2
    inside = (slice(top, top + size[1]), slice(left, left + size[0]))
    figure = np.zeros((side, side), bool)
    figure[inside] = own_figure(colour[inside], alpha[inside])
    return figure


def own_figure(colour: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    if (alpha < ALPHA_CUT).mean() > 0.005:
        return alpha >= ALPHA_CUT
    border = np.concatenate([colour[0], colour[-1], colour[:, 0], colour[:, -1]])
    background = np.median(border, axis=0)
    similar = np.abs(colour - background).max(axis=2) < BACKGROUND_TOLERANCE
    reached = np.zeros_like(similar)
    reached[0], reached[-1], reached[:, 0], reached[:, -1] = similar[0], similar[-1], similar[:, 0], similar[:, -1]
    while True:
        grown = dilated(reached, 1) & similar
        if (grown == reached).all():
            break
        reached = grown
    return ~reached


def pull_push(colour: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """
    The picture with every pixel outside `valid` filled from the valid pixels near it (coarser and
    coarser averages the further away it is). Valid pixels are unchanged.
    """
    side = 1
    while side < max(colour.shape[:2]):
        side *= 2
    total = np.zeros((side, side, 3))
    weight = np.zeros((side, side))
    total[:colour.shape[0], :colour.shape[1]] = colour * valid[..., None]
    weight[:colour.shape[0], :colour.shape[1]] = valid
    levels = [(total, weight)]
    while levels[-1][1].shape[0] > 1:
        total, weight = levels[-1]
        half = total.shape[0] // 2
        levels.append((total.reshape(half, 2, half, 2, 3).sum((1, 3)), weight.reshape(half, 2, half, 2).sum((1, 3))))
    total, weight = levels[-1]
    filled = total / np.maximum(weight, 1e-9)[..., None]
    for total, weight in reversed(levels[:-1]):
        coarse = soft_doubled(filled)
        own = total / np.maximum(weight, 1e-9)[..., None]
        filled = np.where((weight > 0)[..., None], own, coarse)
    return filled[:colour.shape[0], :colour.shape[1]]


def soft_doubled(image: np.ndarray) -> np.ndarray:
    """Twice the size, smoothly: each new pixel leans three quarters on its own cell and a quarter on the next."""
    padded = np.pad(image, ((1, 1), (1, 1), (0, 0)), mode="edge")
    rows = np.empty((image.shape[0] * 2, padded.shape[1], 3))
    rows[0::2] = 0.75 * padded[1:-1] + 0.25 * padded[:-2]
    rows[1::2] = 0.75 * padded[1:-1] + 0.25 * padded[2:]
    out = np.empty((image.shape[0] * 2, image.shape[1] * 2, 3))
    out[:, 0::2] = 0.75 * rows[:, 1:-1] + 0.25 * rows[:, :-2]
    out[:, 1::2] = 0.75 * rows[:, 1:-1] + 0.25 * rows[:, 2:]
    return out


def box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    """The sum of every (2*radius+1) square window, edges included as they are."""
    padded = np.pad(values, ((radius + 1, radius), (radius + 1, radius)), mode="constant")
    table = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    return table[size:, size:] - table[:-size, size:] - table[size:, :-size] + table[:-size, :-size]


def luminance(colour: np.ndarray) -> np.ndarray:
    return colour @ np.array([0.2126, 0.7152, 0.0722])


def ink_mask(colour: np.ndarray, figure: np.ndarray) -> np.ndarray:
    """Line work: pixels of the figure much darker than the figure around them."""
    radius = max(4, colour.shape[0] // 40)
    light = luminance(colour)
    around = box_sum(light * figure, radius) / np.maximum(box_sum(figure.astype(float), radius), 1)
    return figure & (light < around * INK_SHARE)


def resized_mask(mask: np.ndarray, side: int) -> np.ndarray:
    """The mask sampled on a `side` x `side` grid (nearest pixel)."""
    index = np.clip(((np.arange(side) + 0.5) / side * mask.shape[0]).astype(int), 0, mask.shape[0] - 1)
    return mask[index][:, index]


def sample_plane(image: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The picture's value at positions on the plane (x right, y up, -1 to 1), nearest pixel, edges held."""
    side = image.shape[0]
    column = np.clip(((x + 1) / 2 * side).astype(int), 0, side - 1)
    row = np.clip(((1 - y) / 2 * side).astype(int), 0, side - 1)
    return image[row, column]


def to_blender_image(name: str, colour: np.ndarray) -> "bpy.types.Image":
    """An sRGB picture (rows from the top) as a Blender image."""
    side = colour.shape[0]
    image = bpy.data.images.new(name, side, side, alpha=False)
    rgba = np.concatenate([colour[::-1], np.ones((side, side, 1))], axis=2).astype(np.float32)
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    return image


def grid_distance(mask: np.ndarray) -> np.ndarray:
    """How many cells each cell of the mask is from the mask's edge (0 on the edge itself), roughly round."""
    distance = np.zeros(mask.shape)
    current = mask.copy()
    step = 0
    while current.any():
        ring = eroded(current, 1, border=True)
        if step % 2 == 1:  # every other round also looks diagonally, so "near" is an octagon, not a diamond
            ring = ring & shifted(current, 1, 1, True) & shifted(current, 1, -1, True) & shifted(current, -1, 1, True) & shifted(current, -1, -1, True)
        current = ring
        distance += current
        step += 1
    return distance
