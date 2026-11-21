"""
The build: a raw mesh and a portrait in, a small textured GLB with a "jaw" morph target and a JSON of
where the eyes and mouth sit on its surface out.

The mesh is moved into the picture's own space (the portrait's longer side spans -1 to 1 across and up),
so from the front it lines up with the drawing. The picture is projected on from the front. Surfaces the
picture never shows (turned away, or hidden behind something) take a plain colour carried along the
surface from where the picture was last seen, with the drawing's line work left out of that colour.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Callable

import bpy
import numpy as np
from mathutils import Vector

import fit
import headmath
import imaging
import mesh as meshes

GRID = 384                   # resolution of the outlines that are fitted to each other
SURE = 0.9                   # points using at least this much picture keep its colour as their own
RIM = 0.35                   # how side-on a point must be to count as part of the outline
FACING = (0.05, 0.3)          # below the first the picture is not used at all; above the second it is used fully
FRONT_CHART = 0.62           # share of the texture's width given to the part the picture covers
CHART_GAP = 0.012
FEATURE_GRID = 9             # depth samples across each eye and the mouth
FEATURE_SPAN = 1.8           # the samples cover this many half-sizes around the feature
MAX_ROUNDS = 8
OUTLINE_NEEDS = 0.5          # the drawing "has an outline" when this share of the figure's edge is line work
OUTLINE_SEARCH = 14          # pixels in from the edge that are searched for that line
OUTLINE_KEPT = 0.6           # the redrawn outline is this share of the drawn one (part of it is already in the texture)


def neighbour_mean(values: np.ndarray, edges: np.ndarray, own: float) -> np.ndarray:
    """Every point's value averaged with its neighbours'. `own` is the weight of the point itself."""
    wide = values.reshape(len(values), -1)
    total, links = wide * own, np.full(len(values), float(own))
    np.add.at(total, edges[:, 0], wide[edges[:, 1]])
    np.add.at(total, edges[:, 1], wide[edges[:, 0]])
    np.add.at(links, edges[:, 0], 1)
    np.add.at(links, edges[:, 1], 1)
    return (total / np.maximum(links, 1)[:, None]).reshape(values.shape)


def picture_share(shape: "bpy.types.Object", normals: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """How much of the picture each point takes: all of it where the surface faces the front with nothing in the way."""
    tree = meshes.tree_of(shape)
    share = headmath.smoothstep(*FACING, -normals[:, 1])
    for index, vertex in enumerate(shape.data.vertices):
        if share[index] > 0 and tree.ray_cast(vertex.co + vertex.normal * 0.008 + meshes.FRONT * 0.008, meshes.FRONT)[0] is not None:
            share[index] = 0
    for _ in range(3):  # soften, so the change from picture to plain colour has no hard line
        share = np.minimum(share, neighbour_mean(share, edges, 1))
    return share


def plain_colour(shape: "bpy.types.Object", share: np.ndarray, normals: np.ndarray, edges: np.ndarray,
                 flat: np.ndarray, side: np.ndarray) -> np.ndarray:
    """The colour (linear light) for what the picture does not show: hair stays hair, skin skin, cloth cloth."""
    co = meshes.points(shape.data)
    tree = meshes.tree_of(shape)
    fill = imaging.to_linear(imaging.sample_plane(flat, co[:, 0], co[:, 2]))
    known = share > SURE
    # Points on the outline itself (side-on, nothing in front of or behind them) take the outline's colour,
    # so the back of the hair is filled from hair and not from the ear or neck beside it.
    rim = np.zeros(len(co), bool)
    for index, vertex in enumerate(shape.data.vertices):
        if known[index] or abs(normals[index, 1]) > RIM:
            continue
        start = vertex.co + vertex.normal * 0.008
        if tree.ray_cast(start, meshes.FRONT)[0] is None and tree.ray_cast(start, meshes.BACK)[0] is None:
            rim[index] = True
    fill[rim] = imaging.to_linear(imaging.sample_plane(side, co[rim, 0], co[rim, 2]))
    known = known | rim
    seeds = known.copy()
    for _ in range(len(co)):  # ring by ring outwards: each unknown point takes the colour of the known points it touches
        to_first, to_second = known[edges[:, 1]] & ~known[edges[:, 0]], known[edges[:, 0]] & ~known[edges[:, 1]]
        if not (to_first.any() or to_second.any()):
            break
        total, links = np.zeros((len(co), 3)), np.zeros(len(co))
        np.add.at(total, edges[to_first, 0], fill[edges[to_first, 1]])
        np.add.at(links, edges[to_first, 0], 1)
        np.add.at(total, edges[to_second, 1], fill[edges[to_second, 0]])
        np.add.at(links, edges[to_second, 1], 1)
        reached = links > 0
        fill[reached] = total[reached] / links[reached][:, None]
        known = known | reached
    known = seeds
    for _ in range(8):  # a last even pass, so the plain colour has no blotches of its own
        fill = np.where(known[:, None], fill, neighbour_mean(fill, edges, 1))
    return fill


def projection_material(picture: "bpy.types.Image") -> "bpy.types.Material":
    """Shows the picture projected from the front, mixed by the "share" layer with the "fill" layer."""
    material = bpy.data.materials.new("projected")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    geometry = nodes.new("ShaderNodeNewGeometry")
    split = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geometry.outputs["Position"], split.inputs[0])
    uv = nodes.new("ShaderNodeCombineXYZ")
    for axis_name, socket in (("X", "X"), ("Z", "Y")):
        to_unit = nodes.new("ShaderNodeMath")
        to_unit.operation = "MULTIPLY_ADD"
        to_unit.inputs[1].default_value = to_unit.inputs[2].default_value = 0.5
        links.new(split.outputs[axis_name], to_unit.inputs[0])
        links.new(to_unit.outputs[0], uv.inputs[socket])
    projected = nodes.new("ShaderNodeTexImage")
    projected.image = picture
    projected.extension = "EXTEND"
    links.new(uv.outputs[0], projected.inputs["Vector"])
    amount = nodes.new("ShaderNodeVertexColor")
    amount.layer_name = "share"
    plain = nodes.new("ShaderNodeVertexColor")
    plain.layer_name = "fill"
    mix = nodes.new("ShaderNodeMixRGB")
    links.new(amount.outputs["Color"], mix.inputs["Fac"])
    links.new(plain.outputs["Color"], mix.inputs["Color1"])
    links.new(projected.outputs["Color"], mix.inputs["Color2"])
    emission = nodes.new("ShaderNodeEmission")
    links.new(mix.outputs[0], emission.inputs["Color"])
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def unwrap(shape: "bpy.types.Object", share: np.ndarray) -> float:
    """
    Two charts. Faces the picture fully covers are laid out exactly as the picture shows them, in one
    piece with no seams across the face, at the left of the texture, so the face keeps the picture's
    own sharpness. Everything else (mostly plain colour) is unwrapped automatically into the strip on
    the right. Returns the share of faces in the front chart.
    """
    mesh = shape.data
    mesh.uv_layers.new(name="uv")
    corner_vertex = np.empty(len(mesh.loops), int)
    mesh.loops.foreach_get("vertex_index", corner_vertex)
    starts, totals = np.empty(len(mesh.polygons), int), np.empty(len(mesh.polygons), int)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    face_of_corner = np.repeat(np.arange(len(mesh.polygons)), totals)
    front_face = np.minimum.reduceat((share >= 0.999)[corner_vertex], starts)

    meshes.activate(shape)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")

    uv = np.empty(len(mesh.loops) * 2)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    in_front = front_face[face_of_corner]
    strip_left = FRONT_CHART + CHART_GAP
    uv[~in_front, 0] = strip_left + uv[~in_front, 0] * (1 - strip_left)
    co = meshes.points(mesh)[corner_vertex[in_front]]
    if len(co):
        low, high = co[:, [0, 2]].min(0), co[:, [0, 2]].max(0)
        size = np.maximum(high - low, 1e-6)
        uv[in_front, 0] = (co[:, 0] - low[0]) / size[0] * (FRONT_CHART - 2 * CHART_GAP) + CHART_GAP
        uv[in_front, 1] = (co[:, 2] - low[1]) / size[1] * (1 - 2 * CHART_GAP) + CHART_GAP
    mesh.uv_layers.active.data.foreach_set("uv", uv.ravel())
    return float(front_face.mean())


def bake(shape: "bpy.types.Object", material: "bpy.types.Material", size: int) -> "bpy.types.Image":
    baked = bpy.data.images.new("head-baked", size, size)
    target = material.node_tree.nodes.new("ShaderNodeTexImage")
    target.image = baked
    material.node_tree.nodes.active = target
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.render.bake.margin = max(4, size // 170)
    meshes.activate(shape)
    bpy.ops.object.bake(type="EMIT")
    return baked


def final_material(baked: "bpy.types.Image") -> "bpy.types.Material":
    material = bpy.data.materials.new("head")
    material.use_nodes = True
    shader = material.node_tree.nodes["Principled BSDF"]
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = baked
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = 1
    return material


def surface_depth(tree: "meshes.BVHTree", x: float, y: float) -> float | None:
    hit = tree.ray_cast(Vector((x, -5, y)), meshes.BACK)
    return None if hit[0] is None else -hit[0].y


def feature_spot(tree: "meshes.BVHTree", feature: dict, size: tuple[int, int]) -> dict:
    """A feature on the plane, with a small grid of how far forward the surface is around it."""
    half_unit = headmath.plane_half(*size)
    at = headmath.to_plane(feature["at"], *size)
    half = [feature["half"][0] / half_unit, feature["half"][1] / half_unit]
    centre = surface_depth(tree, *at)
    if centre is None:
        raise SystemExit(f"head: a marked feature at {feature['at']} is not on the head. Check the marks.")
    steps = np.linspace(-1, 1, FEATURE_GRID)
    depth = []
    for v in steps:          # rows from the bottom
        for u in steps:      # columns from the left
            found = surface_depth(tree, at[0] + u * half[0] * FEATURE_SPAN, at[1] + v * half[1] * FEATURE_SPAN)
            depth.append(round(found if found is not None and abs(found - centre) < 0.4 else centre, 4))
    spot = {"at": [round(at[0], 4), round(at[1], 4)], "half": [round(half[0], 4), round(half[1], 4)],
            "grid": {"cols": FEATURE_GRID, "rows": FEATURE_GRID, "span": FEATURE_SPAN, "depth": depth}}
    if feature.get("tilt"):
        spot["tilt"] = feature["tilt"]
    return spot


def feature_colours(colour: np.ndarray, flat: np.ndarray, marks: dict, size: tuple[int, int]) -> dict:
    """Colours for the eyelids and lashes, taken from the picture around and inside the eyes."""
    pad_x, pad_y = (colour.shape[0] - size[0]) // 2, (colour.shape[0] - size[1]) // 2
    ring, inner = [], []
    for eye in marks["eyes"]:
        for angle in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            for reach, bucket, source in ((1.45, ring, flat), (0.98, inner, colour), (0.6, inner, colour)):
                x = int(np.clip(eye["at"][0] + pad_x + math.cos(angle) * eye["half"][0] * reach, 0, colour.shape[1] - 1))
                y = int(np.clip(eye["at"][1] + pad_y + math.sin(angle) * eye["half"][1] * reach, 0, colour.shape[0] - 1))
                bucket.append(source[y, x])
    lid = np.median(np.array(ring), axis=0)
    inner_colours = np.array(inner)
    lash = inner_colours[np.argsort(imaging.luminance(inner_colours))[:max(1, len(inner_colours) // 10)]].mean(0)
    return {"lid": headmath.to_hex(lid), "lash": headmath.to_hex(lash), "mouth": "#3a1215", "tongue": "#c9645c", "teeth": "#f4efe8"}


def drawn_outline(colour: np.ndarray, figure: np.ndarray, ink: np.ndarray) -> dict | None:
    """
    The width (plane units) and colour of the line drawn around the figure, or None when the drawing has
    no outline. The viewer redraws it around the head, so the outline survives when the head turns.
    """
    edge = figure & ~imaging.eroded(figure, 1)
    if not edge.any() or ink[edge].mean() < OUTLINE_NEEDS:
        return None
    near_edge = ink & ~imaging.eroded(figure, OUTLINE_SEARCH)
    width = min(OUTLINE_SEARCH, near_edge.sum() / edge.sum())
    return {"width": round(float(width) * OUTLINE_KEPT / (colour.shape[0] / 2), 4), "color": headmath.to_hex(colour[ink & edge].mean(0))}


def add_jaw(shape: "bpy.types.Object", jaw: dict) -> int:
    """The jaw: the chin and lower lip drop, most at the chin, nothing above the mouth or out at the cheeks."""
    co = meshes.points(shape.data)
    pull = headmath.jaw_pull(co[:, 0], co[:, 2], -co[:, 1], jaw)
    shape.shape_key_add(name="Basis")
    key = shape.shape_key_add(name="jaw")
    dropped = co.copy()
    dropped[:, 2] -= pull * jaw["drop"]
    dropped[:, 1] += pull * jaw["drop"] * 0.25
    key.data.foreach_set("co", dropped.ravel())
    key.value = 0
    return int((pull > 0.01).sum())


def export(shape: "bpy.types.Object", out_path: str, quality: int) -> int:
    meshes.activate(shape)
    for image in bpy.data.images:
        if image.name.startswith("head-baked"):
            image.file_format = "JPEG"
    bpy.ops.export_scene.gltf(filepath=out_path, export_format="GLB", use_selection=True, export_image_format="JPEG",
                              export_image_quality=quality, export_yup=True, export_animations=False, export_normals=False,
                              export_morph=True, export_morph_normal=False)
    return os.path.getsize(out_path)


def prepare_shape(job: dict, config: dict, say: Callable[[str], None]) -> "bpy.types.Object":
    """Import, turn to face the front, remove a backing plate if there is one, rebuild, smooth."""
    shape = meshes.import_mesh(job["mesh"])
    meshes.normalise(shape, headmath.FRONT_TURNS[config["front"]], headmath.UP_TURNS[config["up"]])
    plate = meshes.find_plate(shape.data) if config["removePlate"] != "off" else None
    if plate is not None:
        say(f"backing plate found at depth {plate[0]:.3f} to {plate[1]:.3f}: removed ({meshes.remove_plate(shape, plate)} points)")
    elif config["removePlate"] == "on":
        say("no backing plate found")
    if config["remesh"] or plate is not None:
        meshes.apply_modifier(shape, "REMESH", mode="VOXEL", voxel_size=config["voxel"], use_smooth_shade=True)
        shape = meshes.keep_largest_part(shape)
        meshes.apply_modifier(shape, "LAPLACIANSMOOTH", iterations=2, lambda_factor=0.4)
    else:
        shape = meshes.keep_largest_part(shape)
        meshes.activate(shape)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.quads_convert_to_tris()
        bpy.ops.object.mode_set(mode="OBJECT")
    meshes.normalise(shape)
    return shape


def finish(shape: "bpy.types.Object", settings: dict, layers: dict, marks: dict, size: tuple[int, int],
           config: dict, say: Callable[[str], None]) -> dict:
    """Everything after the fit, for one face count: texture, jaw, feature spots."""
    meshes.decimate(shape, settings["faces"])
    meshes.activate(shape)
    bpy.ops.object.shade_smooth()
    mesh = shape.data
    edges = np.array([edge.vertices[:] for edge in mesh.edges])
    normals = np.array([vertex.normal[:] for vertex in mesh.vertices])
    share = picture_share(shape, normals, edges)
    fill = plain_colour(shape, share, normals, edges, layers["flat"], layers["side"])
    mesh.color_attributes.new("share", "FLOAT_COLOR", "POINT").data.foreach_set("color", np.repeat(share, 4))
    mesh.color_attributes.new("fill", "FLOAT_COLOR", "POINT").data.foreach_set("color", np.concatenate([fill, np.ones((len(fill), 1))], 1).ravel())
    material = projection_material(layers["front"])
    mesh.materials.clear()
    mesh.materials.append(material)
    front_share = unwrap(shape, share)
    baked = bake(shape, material, settings["texture"])
    mesh.materials.clear()
    mesh.materials.append(final_material(baked))
    for name in ("share", "fill"):
        mesh.color_attributes.remove(mesh.color_attributes[name])

    tree = meshes.tree_of(shape)
    surface = {
        "version": headmath.SURFACE_VERSION,
        "eyes": [feature_spot(tree, eye, size) for eye in marks["eyes"]],
        "mouth": feature_spot(tree, marks["mouth"], size),
    }
    mouth_depth = surface["mouth"]["grid"]["depth"][len(surface["mouth"]["grid"]["depth"]) // 2]
    jaw = headmath.jaw_frame(marks, *size, mouth_depth=mouth_depth, drop=config["jawDrop"])
    moved = add_jaw(shape, jaw)
    surface["jaw"] = {"drop": round(jaw["drop"], 4), "share": headmath.JAW_SHARE}
    surface["maxTurn"] = config["maxTurn"]
    say(f"{len(mesh.polygons)} faces, picture covers {share.mean():.0%} of the points, front chart {front_share:.0%} of the faces, jaw moves {moved} points")
    return {"surface": surface, "baked": baked, "jawPoints": moved}


def build(job: dict, say: Callable[[str], None]) -> dict:
    config = headmath.merged_config(job.get("config") or {})
    colour, alpha, size = imaging.load_picture(job["picture"])
    figure = imaging.figure_mask(colour, alpha, size)
    marks = headmath.complete_marks(job["marks"])
    say(f"picture {size[0]}x{size[1]}, figure covers {figure.mean():.0%}")

    ink = imaging.ink_mask(colour, figure)
    flat = imaging.pull_push(colour, figure & ~ink)
    band = imaging.eroded(figure, 4) & ~imaging.eroded(figure, 16) & ~ink
    layers = {
        "flat": flat,
        "side": imaging.pull_push(colour, band),
    }
    front_picture = imaging.pull_push(colour, imaging.eroded(figure, 2))

    shape = prepare_shape(job, config, say)
    depth = meshes.front_depth(meshes.tree_of(shape), GRID)
    neck = marks.get("neck")
    fitted = fit.fit_outlines(~np.isnan(depth), meshes.SPAN, imaging.resized_mask(figure, GRID),
                              None if neck is None else headmath.to_plane((0, neck), *size)[1])
    say(f"fit: head overlap {fitted['headOverlap']:.3f}, body overlap {fitted['bodyOverlap']:.3f}"
        + ("" if fitted["meshNeck"] is not None else " (no neck found: one fit for the whole figure)"))
    meshes.set_points(shape.data, fit.placed(meshes.points(shape.data), fitted))
    meshes.cut_below(shape, -1.0)
    meshes.decimate(shape, max(config["faces"] * 2, 60000))
    fitted_mesh = shape.data.copy()

    settings = {key: config[key] for key in ("faces", "texture", "jpegQuality", "budgetKB")}
    made, made_for = None, None
    size_bytes = 0
    for _ in range(MAX_ROUNDS):
        if made is None or made_for["faces"] != settings["faces"]:
            if made is not None:  # start again from the fitted mesh with fewer faces
                shape.shape_key_clear()
                shape.data = fitted_mesh.copy()
            layers["front"] = imaging.to_blender_image("front", front_picture)
            made = finish(shape, settings, layers, marks, size, config, say)
        elif made_for["texture"] != settings["texture"]:
            made["baked"].scale(settings["texture"], settings["texture"])
        made_for = dict(settings)
        size_bytes = export(shape, job["outModel"], settings["jpegQuality"])
        following = headmath.next_budget_step(size_bytes, settings)
        if following == settings:
            break
        if following is None:
            say(f"could not get under {settings['budgetKB']} KB: the smallest it gets is {size_bytes / 1024:.0f} KB")
            break
        changed = ", ".join(f"{key} {settings[key]} -> {following[key]}" for key in settings if settings[key] != following[key])
        say(f"{size_bytes / 1024:.0f} KB is over the {settings['budgetKB']} KB budget: {changed}")
        settings = following

    surface = made["surface"]
    surface["colors"] = feature_colours(colour, flat, marks, size)
    surface["picture"] = {"width": size[0], "height": size[1]}
    outline = drawn_outline(colour, figure, ink)
    if outline is not None:
        surface["outline"] = outline
    Path(job["outSurface"]).write_text(json.dumps(surface, separators=(",", ":")) + "\n")
    say(f"{job['outModel']}: {size_bytes / 1024:.0f} KB, {settings['faces']} faces asked, texture {settings['texture']}, JPEG quality {settings['jpegQuality']}")
    return {"bytes": size_bytes, "settings": settings, "fit": {key: fitted[key] for key in ("headOverlap", "bodyOverlap")},
            "underBudget": size_bytes <= settings["budgetKB"] * 1024, "jawPoints": made["jawPoints"]}
