"""
A shape with no generator: the figure's outline, inflated like a balloon.

Every part is as deep as it is wide: the head becomes a dome, the neck a column, the shoulders a rounded
slab. A small bump is added for the nose. It knows nothing about faces, so there are no eye sockets,
lips, ears or hair volume: it is the offline fallback, not a match for a generated shape.
"""
from __future__ import annotations

import bmesh
import bpy
import numpy as np

import imaging
from headmath import to_plane

CELLS = 260          # the outline is sampled on a grid this many cells across
DEPTH = 0.82         # depth of a part, as a share of its half-width
BACK = 0.85          # the back is a little flatter than the front
LEVELS = 14
NOSE_HEIGHT = 0.3    # of the distance between the eyes
NOSE_WIDTH = 0.16
SMOOTHING = 8


def local_radius(distance: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """For every cell, the radius of the largest disc that fits inside the outline and covers it."""
    radius = distance.copy()
    top = distance.max()
    for level in np.linspace(top, top / LEVELS, LEVELS):
        core = distance >= level * 0.97
        reach = imaging.dilated(core, int(np.ceil(level))) & mask
        radius = np.where(reach, np.maximum(radius, level), radius)
    return radius


def depth_map(figure: np.ndarray, marks: dict, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Depth (plane units) at every grid point, and which grid points are inside the outline."""
    points = CELLS + 1
    index = np.clip((np.arange(points) / CELLS * figure.shape[0]).astype(int), 0, figure.shape[0] - 1)
    mask = figure[index][:, index]
    mask = imaging.eroded(imaging.dilated(mask, 1), 1)  # closes pin-holes
    distance = imaging.grid_distance(mask)
    radius = local_radius(distance, mask)
    cell = 2 / CELLS
    depth = DEPTH * cell * np.sqrt(np.maximum(distance * (2 * radius - distance), 0))
    for _ in range(3):  # soften the steps between levels, keeping the outline at zero depth
        depth = np.where(distance > 0, imaging.box_sum(depth, 1) / 9, 0)

    axis = -1 + 2 * np.arange(points) / CELLS
    across, up = np.meshgrid(axis, -axis)
    left, right = (to_plane(eye["at"], *size) for eye in marks["eyes"])
    mouth = to_plane(marks["mouth"]["at"], *size)
    eye_gap = right[0] - left[0]
    nose = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2 + 0.55 * (mouth[1] - (left[1] + right[1]) / 2))
    spread = NOSE_WIDTH * eye_gap
    bump = np.exp(-(((across - nose[0]) / spread) ** 2 + ((up - nose[1]) / (spread * 1.25)) ** 2))
    return depth + NOSE_HEIGHT * eye_gap * bump * (distance > 0), mask


def inflate(figure: np.ndarray, marks: dict, size: tuple[int, int], out_path: str) -> int:
    depth, mask = depth_map(figure, marks, size)
    points = CELLS + 1
    axis = -1 + 2 * np.arange(points) / CELLS
    across, up = np.meshgrid(axis, -axis)

    number = np.full((points, points), -1)
    number[mask] = np.arange(mask.sum())
    count = int(mask.sum())
    front = np.stack([across[mask], -depth[mask], up[mask]], 1)
    back = np.stack([across[mask], depth[mask] * BACK, up[mask]], 1)
    whole = mask[:-1, :-1] & mask[:-1, 1:] & mask[1:, :-1] & mask[1:, 1:]
    a, b, c, d = number[:-1, :-1][whole], number[:-1, 1:][whole], number[1:, 1:][whole], number[1:, :-1][whole]
    front_faces = np.stack([a, d, c, b], 1)
    back_faces = np.stack([a, b, c, d], 1) + count

    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new("inflated")
    mesh.from_pydata(np.concatenate([front, back]).tolist(), [], np.concatenate([front_faces, back_faces]).tolist())
    shape = bpy.data.objects.new("inflated", mesh)
    bpy.context.scene.collection.objects.link(shape)

    sheet = bmesh.new()
    sheet.from_mesh(mesh)
    bmesh.ops.remove_doubles(sheet, verts=sheet.verts, dist=1e-5)  # the two sides meet along the outline
    open_edges = [edge for edge in sheet.edges if edge.is_boundary]
    if open_edges:  # where the figure runs off the picture the two sides are apart: bridge them
        bmesh.ops.holes_fill(sheet, edges=open_edges, sides=0)
    for _ in range(SMOOTHING):  # the grid leaves a staircase along the outline
        bmesh.ops.smooth_vert(sheet, verts=sheet.verts, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
    bmesh.ops.recalc_face_normals(sheet, faces=sheet.faces)
    sheet.to_mesh(mesh)
    sheet.free()

    bpy.ops.object.select_all(action="DESELECT")
    shape.select_set(True)
    bpy.context.view_layer.objects.active = shape
    bpy.ops.export_scene.gltf(filepath=out_path, export_format="GLB", use_selection=True, export_yup=True,
                              export_materials="NONE", export_normals=False, export_texcoords=False)
    return len(mesh.polygons)
