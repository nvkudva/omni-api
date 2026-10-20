"""The published CLI surface: `omni --version` and the subcommands around it."""
import contextlib
import io
import unittest

from omni.commands import build_parser


class VersionTest(unittest.TestCase):
    def test_version_prints_and_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit:
            build_parser().parse_args(["--version"])
        self.assertEqual(exit.exception.code, 0)
        self.assertRegex(out.getvalue(), r"^omni \d+\.\d+\.\d+")

    def test_a_subcommand_still_parses(self):
        args = build_parser().parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_host_and_port_keep_their_defaults_under_a_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--port", "10001"])
        self.assertEqual(args.port, 10001)
        self.assertTrue(args.host)


if __name__ == "__main__":
    unittest.main()
