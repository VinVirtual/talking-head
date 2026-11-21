"""Reads the description inside a binary glTF (.glb) file with the standard library only, for `head verify`."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

MAGIC = 0x46546C67       # "glTF"
JSON_CHUNK = 0x4E4F534A  # "JSON"
HEADER = struct.Struct("<III")
CHUNK = struct.Struct("<II")


class GlbError(ValueError):
    """The file is not a readable GLB."""


def parse_glb(data: bytes) -> dict[str, Any]:
    """The JSON part of a GLB file."""
    if len(data) < HEADER.size + CHUNK.size:
        raise GlbError("the file is too short to be a GLB")
    magic, version, length = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise GlbError("the file does not start with \"glTF\"")
    if version != 2:
        raise GlbError(f"glTF version {version} is not supported (need 2)")
    if length != len(data):
        raise GlbError(f"the header says {length} bytes but the file has {len(data)}")
    chunk_length, chunk_type = CHUNK.unpack_from(data, HEADER.size)
    start = HEADER.size + CHUNK.size
    if chunk_type != JSON_CHUNK or start + chunk_length > len(data):
        raise GlbError("the first chunk is not a complete JSON chunk")
    try:
        return json.loads(data[start:start + chunk_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GlbError(f"the JSON chunk cannot be read: {error}") from error


def read_glb(path: Path) -> dict[str, Any]:
    return parse_glb(Path(path).read_bytes())


def summary(gltf: dict[str, Any]) -> dict[str, Any]:
    """What `verify` needs to know: triangles, points, morph target names, images."""
    accessors = gltf.get("accessors", [])
    triangles = points = 0
    targets: list[str] = []
    for mesh in gltf.get("meshes", []):
        names = (mesh.get("extras") or {}).get("targetNames") or []
        for primitive in mesh.get("primitives", []):
            position = primitive.get("attributes", {}).get("POSITION")
            if position is not None:
                points += accessors[position]["count"]
            if primitive.get("indices") is not None:
                triangles += accessors[primitive["indices"]]["count"] // 3
            count = len(primitive.get("targets", []))
            for index in range(count):
                name = names[index] if index < len(names) else f"target{index}"
                if name not in targets:
                    targets.append(name)
    return {
        "meshes": len(gltf.get("meshes", [])),
        "triangles": triangles,
        "points": points,
        "targets": targets,
        "images": [image.get("mimeType", "?") for image in gltf.get("images", [])],
        "generator": gltf.get("asset", {}).get("generator", ""),
    }
