"""Reads what the command line needs from a PNG or JPEG file's own header: its size and its EXIF orientation."""
from __future__ import annotations

import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ORIENTATION_TAG = 0x0112
UPRIGHT = 1


class PictureError(ValueError):
    """The bytes are not a PNG or JPEG this tool can read."""


def size_of(data: bytes) -> tuple[int, int]:
    """Width and height of a PNG or JPEG."""
    if data[:8] == PNG_SIGNATURE and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        for marker, start, length in _jpeg_segments(data):
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and length >= 5:
                height, width = struct.unpack(">HH", data[start + 1:start + 5])  # after the sample precision byte
                return width, height
    raise PictureError("not a PNG or JPEG picture")


def _jpeg_segments(data: bytes):
    """(marker, payload start, payload length) for each segment before the image data."""
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            return
        length = struct.unpack(">H", data[position + 2:position + 4])[0]
        yield marker, position + 4, length - 2
        position += 2 + length


def tiff_orientation(tiff: bytes) -> int:
    """The orientation tag of the first image directory in an EXIF (TIFF) block, 1 when there is none."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return UPRIGHT
    order = "<" if tiff[:2] == b"II" else ">"
    directory = struct.unpack(order + "I", tiff[4:8])[0]
    if directory + 2 > len(tiff):
        return UPRIGHT
    count = struct.unpack(order + "H", tiff[directory:directory + 2])[0]
    for index in range(count):
        entry = directory + 2 + index * 12
        if entry + 12 > len(tiff):
            break
        tag, kind = struct.unpack(order + "HH", tiff[entry:entry + 4])
        if tag == ORIENTATION_TAG and kind == 3:  # a SHORT, stored in the first two bytes of the value
            return struct.unpack(order + "H", tiff[entry + 8:entry + 10])[0]
    return UPRIGHT


def orientation_of(data: bytes) -> int:
    """The EXIF orientation of a PNG (eXIf chunk) or JPEG (APP1 Exif segment): 1 means upright as stored."""
    if data[:8] == PNG_SIGNATURE:
        position = 8
        while position + 8 <= len(data):
            length, kind = struct.unpack(">I4s", data[position:position + 8])
            if kind == b"eXIf":
                return tiff_orientation(data[position + 8:position + 8 + length])
            if kind == b"IDAT":
                return UPRIGHT
            position += 12 + length
        return UPRIGHT
    if data[:2] == b"\xff\xd8":
        for marker, start, length in _jpeg_segments(data):
            if marker == 0xE1 and data[start:start + 6] == b"Exif\x00\x00":
                return tiff_orientation(data[start + 6:start + length])
    return UPRIGHT
