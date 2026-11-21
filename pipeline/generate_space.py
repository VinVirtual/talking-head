"""
Asks a public Hugging Face Space running Hunyuan3D-2 for an untextured 3D shape of one picture.

    python generate_space.py portrait.png out/raw.glb [--space tencent/Hunyuan3D-2] [--seed 1234]

Only the picture is uploaded, under the plain name "input.png". It needs the `gradio_client` package.
The Space is a free shared demo: it can be busy, rate-limited or gone. This script asks once and never retries.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
from pathlib import Path

DEFAULT_SPACE = "tencent/Hunyuan3D-2"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ALPHA_COLOUR_TYPES = (4, 6)


def png_has_alpha(path: Path) -> bool:
    """Whether a PNG file stores an alpha channel (read from its header, no image library needed)."""
    with open(path, "rb") as handle:
        header = handle.read(26)
    if len(header) < 26 or header[:8] != PNG_SIGNATURE:
        return False
    return struct.unpack(">B", header[25:26])[0] in ALPHA_COLOUR_TYPES


def generate(portrait: Path, out_path: Path, space: str, seed: int, steps: int, resolution: int) -> Path:
    try:
        from gradio_client import Client, handle_file
    except ImportError as error:
        raise SystemExit("head: the public generator needs gradio_client:  pip install gradio_client") from error

    with tempfile.TemporaryDirectory() as folder:
        upload = Path(folder) / "input.png"  # a plain name: nothing about whose picture it is goes with the request
        shutil.copy(portrait, upload)
        client = Client(space, verbose=False)
        result = client.predict(
            caption=None,
            image=handle_file(str(upload)),
            mv_image_front=None,
            mv_image_back=None,
            mv_image_left=None,
            mv_image_right=None,
            steps=steps,
            guidance_scale=5,
            seed=seed,
            octree_resolution=resolution,
            check_box_rembg=not png_has_alpha(portrait),  # a picture with no cut-out is cut out by the Space
            num_chunks=8000,
            randomize_seed=False,
            api_name="/shape_generation",
        )
    shape = result[0]
    source = shape["value"] if isinstance(shape, dict) else shape
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("portrait", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--resolution", type=int, default=384)
    args = parser.parse_args()
    try:
        written = generate(args.portrait, args.out, args.space, args.seed, args.steps, args.resolution)
    except SystemExit:
        raise
    except Exception as error:  # the Space reports every failure differently; say what it said and stop
        raise SystemExit(f"head: the generator did not return a shape ({type(error).__name__}: {error}).\n"
                         "      It is a shared public demo and may be busy or over its free quota. Try again later,\n"
                         "      or use a mesh from any other tool:  head shape <project> --mesh your-mesh.glb") from error
    sys.stdout.write(f"shape written to {written}\n")


if __name__ == "__main__":
    main()
