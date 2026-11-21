"""Mesh work for the Blender steps: import, clean, outline and depth from the front."""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

SPAN = 1.05          # a normalised mesh sits inside -1..1; grids over it cover a little more
FRONT = Vector((0, -1, 0))
BACK = Vector((0, 1, 0))
PLATE_FLATNESS = 0.97    # a face is "flat to the front or back" when its normal is this close to the depth axis
PLATE_AREA_SHARE = 0.12  # a plate is there when one depth holds this share of the whole surface, flat
PLATE_MARGIN = 0.012
PLATE_GRID = 320


def import_mesh(path: str) -> "bpy.types.Object":
    """Empties the scene and loads one mesh file (GLB, glTF, OBJ, PLY or STL) as a single object."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    kind = Path(path).suffix.lower()
    if kind in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif kind == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif kind == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    elif kind == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    else:
        raise SystemExit(f"head: cannot read a {kind or 'nameless'} mesh. Use GLB, glTF, OBJ, PLY or STL.")
    meshes = [item for item in bpy.context.scene.objects if item.type == "MESH" and len(item.data.polygons) > 0]
    if not meshes:
        raise SystemExit(f"head: {path} has no mesh with faces in it")
    bpy.ops.object.select_all(action="DESELECT")
    for item in meshes:
        item.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    if len(meshes) > 1:
        bpy.ops.object.join()
    shape = bpy.context.view_layer.objects.active
    for other in [item for item in bpy.context.scene.objects if item != shape]:
        bpy.data.objects.remove(other)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    while shape.data.materials:
        shape.data.materials.pop()
    return shape


def activate(shape: "bpy.types.Object") -> None:
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = shape
    shape.select_set(True)


def points(mesh: "bpy.types.Mesh") -> np.ndarray:
    flat = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", flat)
    return flat.reshape(-1, 3)


def set_points(mesh: "bpy.types.Mesh", values: np.ndarray) -> None:
    mesh.vertices.foreach_set("co", np.ascontiguousarray(values, dtype=float).ravel())
    mesh.update()


def normalise(shape: "bpy.types.Object", turn_degrees: float) -> None:
    """Turns the mesh so its front faces -Y, centres it and scales it to fit -1..1."""
    co = points(shape.data)
    if turn_degrees:
        co = co @ np.array(Matrix.Rotation(math.radians(turn_degrees), 3, "Z")).T
    low, high = co.min(0), co.max(0)
    co = (co - (low + high) / 2) * (2 / max(high - low))
    set_points(shape.data, co)


def tree_of(shape: "bpy.types.Object") -> BVHTree:
    return BVHTree.FromObject(shape, bpy.context.evaluated_depsgraph_get())


def grid_axis(grid: int, span: float = SPAN) -> np.ndarray:
    return -span + 2 * span * (np.arange(grid) + 0.5) / grid


def front_depth(tree: BVHTree, grid: int, span: float = SPAN) -> np.ndarray:
    """
    How far forward the surface is, seen from the front, on a grid of rows from the top.
    NaN where there is nothing. "Forward" is towards the viewer (-Y), so larger is nearer.
    """
    axis = grid_axis(grid, span)
    depth = np.full((grid, grid), np.nan)
    for row, z in enumerate(axis[::-1]):
        for column, x in enumerate(axis):
            hit = tree.ray_cast(Vector((x, -5, z)), BACK)
            if hit[0] is not None:
                depth[row, column] = -hit[0].y
    return depth


def keep_largest_part(shape: "bpy.types.Object") -> "bpy.types.Object":
    activate(shape)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = sorted((item for item in bpy.context.scene.objects if item.type == "MESH"), key=lambda item: len(item.data.polygons))
    for part in parts[:-1]:
        bpy.data.objects.remove(part)
    activate(parts[-1])
    return parts[-1]


def apply_modifier(shape: "bpy.types.Object", kind: str, **settings: object) -> None:
    activate(shape)
    modifier = shape.modifiers.new(kind.lower(), kind)
    for name, value in settings.items():
        setattr(modifier, name, value)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def decimate(shape: "bpy.types.Object", faces: int) -> None:
    triangles = sum(len(polygon.vertices) - 2 for polygon in shape.data.polygons)
    if triangles > faces:
        apply_modifier(shape, "DECIMATE", ratio=faces / triangles)


def find_plate(mesh: "bpy.types.Mesh") -> tuple[float, float] | None:
    """
    The depth range of a flat backing plate, if the mesh has one. Some image-to-3D generators put a thin
    flat sheet behind a cut-out figure; it shows up as a lot of surface, flat to the front and back, at one depth.
    """
    count = len(mesh.polygons)
    normals, centres, areas = np.empty(count * 3), np.empty(count * 3), np.empty(count)
    mesh.polygons.foreach_get("normal", normals)
    mesh.polygons.foreach_get("center", centres)
    mesh.polygons.foreach_get("area", areas)
    normals, centres = normals.reshape(-1, 3), centres.reshape(-1, 3)
    flat = np.abs(normals[:, 1]) > PLATE_FLATNESS
    if not flat.any():
        return None
    histogram, edges = np.histogram(centres[flat, 1], bins=400, range=(-1.05, 1.05), weights=areas[flat])
    top = np.argsort(histogram)[-2:]
    if histogram[top].sum() < PLATE_AREA_SHARE * areas.sum() or abs(int(top[0]) - int(top[1])) > 12:
        return None
    width = edges[1] - edges[0]
    return float(edges[top].min()), float(edges[top].max() + width)


def remove_plate(shape: "bpy.types.Object", plate: tuple[float, float]) -> int:
    """
    Removes a backing plate: the figure's own outline is found where the plate cuts it, plate points
    outside that outline are pulled onto it, and faces that were wholly plate are deleted. Returns how
    many points were plate. Rebuild the surface afterwards (the remesh step) to close the join.
    """
    plate_front, plate_back = plate
    mesh = shape.data
    tree = tree_of(shape)

    def inside(point: tuple[float, float, float], direction: Vector) -> bool:
        crossings, origin = 0, Vector(point)
        while crossings < 40:
            hit = tree.ray_cast(origin, direction)
            if hit[0] is None:
                break
            crossings += 1
            origin = hit[0] + direction * 1e-4
        return crossings % 2 == 1

    axis = grid_axis(PLATE_GRID)
    section = np.zeros((PLATE_GRID, PLATE_GRID), bool)
    for row, z in enumerate(axis):
        for column, x in enumerate(axis):
            section[row, column] = (inside((x, plate_front - PLATE_MARGIN, z), FRONT)
                                    or inside((x, plate_back + PLATE_MARGIN, z), BACK))
    if not section.any():
        return 0
    filled = np.argwhere(section)
    co = points(mesh)
    band = (co[:, 1] > plate_front - PLATE_MARGIN) & (co[:, 1] < plate_back + PLATE_MARGIN)

    def cell(values: np.ndarray) -> np.ndarray:
        return np.clip(((values + SPAN) / (2 * SPAN) * PLATE_GRID).astype(int), 0, PLATE_GRID - 1)

    outside = band & ~section[cell(co[:, 2]), cell(co[:, 0])]
    targets = np.stack([cell(co[outside, 2]), cell(co[outside, 0])], 1)
    nearest = np.empty((len(targets), 2), int)
    sparse = filled[::3]
    for start in range(0, len(targets), 2000):
        chunk = targets[start:start + 2000]
        distance = ((chunk[:, None, :] - sparse[None, :, :]) ** 2).sum(-1)
        nearest[start:start + 2000] = sparse[distance.argmin(1)]
    co[outside, 2] = axis[nearest[:, 0]]
    co[outside, 0] = axis[nearest[:, 1]]
    co[outside, 1] = (plate_front + plate_back) / 2
    set_points(mesh, co)

    sheet = bmesh.new()
    sheet.from_mesh(mesh)
    sheet.verts.ensure_lookup_table()
    bmesh.ops.delete(sheet, geom=[face for face in sheet.faces if all(outside[v.index] for v in face.verts)], context="FACES")
    bmesh.ops.remove_doubles(sheet, verts=sheet.verts, dist=0.002)
    sheet.to_mesh(mesh)
    sheet.free()
    mesh.update()
    return int(outside.sum())


def cut_below(shape: "bpy.types.Object", height: float) -> None:
    """Cuts away everything below `height` (Z) and closes the cut, so the bust ends where the picture does."""
    mesh = shape.data
    if points(mesh)[:, 2].min() >= height:
        return
    sheet = bmesh.new()
    sheet.from_mesh(mesh)
    geometry = list(sheet.verts) + list(sheet.edges) + list(sheet.faces)
    cut = bmesh.ops.bisect_plane(sheet, geom=geometry, plane_co=(0, 0, height), plane_no=(0, 0, -1), clear_outer=True)
    rim = [item for item in cut["geom_cut"] if isinstance(item, bmesh.types.BMEdge)]
    if rim:
        bmesh.ops.holes_fill(sheet, edges=rim, sides=0)
    bmesh.ops.triangulate(sheet, faces=sheet.faces)
    sheet.to_mesh(mesh)
    sheet.free()
    mesh.update()
