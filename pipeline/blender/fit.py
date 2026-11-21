"""
Fits the front outline of a mesh to the outline of the figure in the picture (numpy only).

A fit is four numbers (scale across, scale up, shift across, shift up): a mesh point (x, z) lands on the
plane at (sx * x + cx, sz * z + cz). The head and the body are fitted separately, because generated
shapes rarely keep the drawing's proportions between the two.
"""
from __future__ import annotations

import numpy as np

from headmath import find_neck, smoothstep

NECK_BLEND = 0.1  # the head's fit hands over to the body's over this much height either side of the neck


def axis_of(side: int, span: float) -> np.ndarray:
    return -span + 2 * span * (np.arange(side) + 0.5) / side


def neck_height(mask: np.ndarray, span: float) -> float | None:
    """The height (up is positive) of the neck in a mask whose rows run from the top."""
    row = find_neck(mask.sum(1).tolist())
    return None if row is None else float(-axis_of(mask.shape[0], span)[row])


def box(mask: np.ndarray, span: float, rows: np.ndarray) -> tuple[float, float, float, float] | None:
    """(left, right, bottom, top) of the mask within the given rows, in its own units."""
    part = mask & rows[:, None]
    if not part.any():
        return None
    axis = axis_of(mask.shape[0], span)
    across, down = np.nonzero(part.any(0))[0], np.nonzero(part.any(1))[0]
    return float(axis[across[0]]), float(axis[across[-1]]), float(-axis[down[-1]]), float(-axis[down[0]])


def box_fit(mesh_box: tuple[float, float, float, float], picture_box: tuple[float, float, float, float]) -> list[float]:
    sx = (picture_box[1] - picture_box[0]) / max(mesh_box[1] - mesh_box[0], 1e-6)
    sz = (picture_box[3] - picture_box[2]) / max(mesh_box[3] - mesh_box[2], 1e-6)
    return [sx, sz, picture_box[0] - sx * mesh_box[0], picture_box[2] - sz * mesh_box[2]]


def overlap(fit: list[float], mesh_mask: np.ndarray, mesh_span: float, picture_mask: np.ndarray, region: np.ndarray) -> float:
    """How well the fitted mesh outline covers the picture's outline inside `region`: shared area over joint area."""
    sx, sz, cx, cz = fit
    side = picture_mask.shape[0]
    axis = axis_of(side, 1.0)
    grid = mesh_mask.shape[0]
    column = np.floor(((axis - cx) / sx + mesh_span) / (2 * mesh_span) * grid).astype(int)
    row = np.floor((mesh_span - (-axis - cz) / sz) / (2 * mesh_span) * grid).astype(int)
    seen = ((column >= 0) & (column < grid))[None, :] & ((row >= 0) & (row < grid))[:, None]
    placed = mesh_mask[np.clip(row, 0, grid - 1)][:, np.clip(column, 0, grid - 1)] & seen
    joint = ((placed | picture_mask) & region).sum()
    return float(((placed & picture_mask) & region).sum() / joint) if joint else 0.0


def refined(fit: list[float], mesh_mask: np.ndarray, mesh_span: float, picture_mask: np.ndarray, region: np.ndarray) -> tuple[list[float], float]:
    """Nudges the four numbers one at a time, in smaller and smaller steps, while the overlap improves."""
    best, score = list(fit), overlap(fit, mesh_mask, mesh_span, picture_mask, region)
    step = 0.04
    while step > 0.0008:
        improved = False
        for index in range(4):
            for change in (step, -step):
                trial = list(best)
                trial[index] += change
                if trial[0] <= 0.05 or trial[1] <= 0.05:
                    continue
                trial_score = overlap(trial, mesh_mask, mesh_span, picture_mask, region)
                if trial_score > score + 1e-6:
                    best, score, improved = trial, trial_score, True
        if not improved:
            step /= 2
    return best, score


def fit_outlines(mesh_mask: np.ndarray, mesh_span: float, picture_mask: np.ndarray, picture_neck: float | None) -> dict:
    """
    The head's fit and the body's, with the neck heights in both spaces. `picture_neck` (plane height)
    overrides the detected one. With no neck found, one fit serves for both.
    """
    side = picture_mask.shape[0]
    picture_up = -axis_of(side, 1.0)
    mesh_up = -axis_of(mesh_mask.shape[0], mesh_span)
    mesh_neck = neck_height(mesh_mask, mesh_span)
    if picture_neck is None:
        picture_neck = neck_height(picture_mask, 1.0)
    everywhere = np.ones((side, side), bool)

    if mesh_neck is None or picture_neck is None:
        start = box_fit(box(mesh_mask, mesh_span, np.ones(len(mesh_up), bool)), box(picture_mask, 1.0, np.ones(side, bool)))
        whole, score = refined(start, mesh_mask, mesh_span, picture_mask, everywhere)
        return {"head": whole, "body": whole, "meshNeck": None, "pictureNeck": None, "headOverlap": score, "bodyOverlap": score}

    start = box_fit(box(mesh_mask, mesh_span, mesh_up > mesh_neck), box(picture_mask, 1.0, picture_up > picture_neck))
    head_region = everywhere & (picture_up > picture_neck)[:, None]
    head, head_score = refined(start, mesh_mask, mesh_span, picture_mask, head_region)
    body, body_score = refined(head, mesh_mask, mesh_span, picture_mask, ~head_region)
    return {"head": head, "body": body, "meshNeck": mesh_neck, "pictureNeck": picture_neck, "headOverlap": head_score, "bodyOverlap": body_score}


def placed(co: np.ndarray, fitted: dict) -> np.ndarray:
    """
    Mesh points moved onto the plane: the head by its fit, the body by its own, blended across the neck.
    Depth is scaled by the head's size and centred on the head.
    """
    head, body = fitted["head"], fitted["body"]
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    if fitted["meshNeck"] is None:
        share = np.ones(len(co))
    else:
        share = smoothstep(fitted["meshNeck"] - NECK_BLEND, fitted["meshNeck"] + NECK_BLEND, z)
    across = share * (head[0] * x + head[2]) + (1 - share) * (body[0] * x + body[2])
    up = share * (head[1] * z + head[3]) + (1 - share) * (body[1] * z + body[3])
    head_part = share > 0.5
    middle = (y[head_part].min() + y[head_part].max()) / 2 if head_part.any() else 0.0
    depth_scale = float(np.sqrt(head[0] * head[1]))
    return np.stack([across, (y - middle) * depth_scale, up], 1)
