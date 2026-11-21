"""
Runs the real pipeline in Blender. Slow (about a minute), so it only runs when asked:

    HEAD_BLENDER_TESTS=1 ./head test
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "pipeline"))

import cli  # noqa: E402

EXAMPLE = SKILL / "examples" / "juno"


@unittest.skipUnless(os.environ.get("HEAD_BLENDER_TESTS") == "1", "set HEAD_BLENDER_TESTS=1 to run the Blender tests")
class Pipeline(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def run_cli(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = cli.main([str(argument) for argument in arguments])
        return code, printed.getvalue()

    def test_a_built_head_with_a_jaw_shape_key_can_be_used_as_the_mesh(self):
        project = self.folder / "again"
        self.assertEqual(self.run_cli("new", project, EXAMPLE / "portrait.png")[0], 0)
        self.assertEqual(self.run_cli("mark", project, "--set", EXAMPLE / "marks.json")[0], 0)
        self.assertEqual(self.run_cli("shape", project, "--mesh", EXAMPLE / "out" / "head.glb")[0], 0)
        code, printed = self.run_cli("build", project, "--faces", "6000", "--texture", "512")
        self.assertEqual(code, 0, printed)
        self.assertIn("lip line found", printed)
        self.assertEqual(self.run_cli("verify", project)[0], 0)
