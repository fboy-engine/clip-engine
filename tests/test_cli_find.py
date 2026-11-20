import unittest
from unittest import mock

from clip_engine import cli


class T(unittest.TestCase):
    def test_find_command_wires_args(self):
        with mock.patch("clip_engine.findrun.run", return_value=0) as r:
            rc = cli.main(["find", "out/source.mp4", "--offset", "1200", "--model", "small", "--top", "6"])
        self.assertEqual(rc, 0)
        args = r.call_args[0]
        self.assertEqual(args[:4], ("out/source.mp4", 1200.0, "small", 6))

    def test_find_failure_is_reported_not_faked(self):
        with mock.patch("clip_engine.findrun.run", side_effect=RuntimeError("boom")):
            self.assertEqual(cli.main(["find", "x.mp4"]), 1)


if __name__ == "__main__":
    unittest.main()
