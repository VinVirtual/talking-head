"""Python probes must not import modules planted in the folder `head` is started from."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import cli  # noqa: E402


class PlantedModuleTest(unittest.TestCase):
    def test_probe_ignores_modules_in_the_current_folder(self):
        was = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "RAN"
            for name in ("json", "gradio_client"):
                (Path(folder) / f"{name}.py").write_text(f"open({str(marker)!r}, 'w').write({name!r})\n")
            os.chdir(folder)
            try:
                self.assertTrue(cli.python_can("import json"))
                cli.python_can("import gradio_client")  # may be missing; the planted copy must not run either way
                # exits 0 only when json came from outside the planted folder
                self.assertTrue(cli.python_can(f"import json, sys; sys.exit(json.__file__.startswith({folder!r}))"))
            finally:
                os.chdir(was)
            self.assertFalse(marker.exists(), "a planted module ran")


if __name__ == "__main__":
    unittest.main()
