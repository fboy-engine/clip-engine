"""The download step of clip.yml runs BEFORE numpy/opencv are installed: building the CLI parser (any subcommand) must not import them.
(Run 45126040434 failed in 'Download window' with ModuleNotFoundError: numpy, raised by an argparse default that imported cam_sr.)"""
import subprocess
import sys
import unittest

CODE = """
import sys
for m in ("numpy", "cv2", "onnxruntime"):
    sys.modules[m] = None          # any import of these now raises ImportError
from clip_engine import cli
try:
    cli.main(["fetch", "--help"])
except SystemExit as e:
    sys.exit(e.code)
"""


class CliLightweight(unittest.TestCase):
    def test_fetch_parser_needs_no_numpy_or_opencv(self):
        p = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("--start", p.stdout)


if __name__ == "__main__":
    unittest.main()
