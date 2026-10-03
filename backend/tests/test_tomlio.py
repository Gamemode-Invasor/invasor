import os
import tempfile
import unittest
from pathlib import Path

from invasor import tomlio
from invasor.schema import InvalidArgument, Unavailable

try:
    import tomllib  # the reference parser: Python 3.11+
except ModuleNotFoundError:
    tomllib = None

# The shape of a real lsfg-vk v2 config (made-up values), as its own UI writes it.
LSFG = """version = 2

[global]
allow_fp16 = true
log_level = "info"

[[profile]]
active_in = [ "1000", "Game.exe" ]
flow_scale = 0.75
multiplier = 2
name = "default"
pacing_mode = "vsync"
performance_mode = true

[[profile]]
flow_scale = 1.0
multiplier = 3
name = "3x"
"""


@unittest.skipIf(tomllib is None, "reading TOML needs tomllib (Python 3.11+)")
class Toml(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "conf.toml"

    def test_round_trip_keeps_everything_and_the_order(self):
        self.path.write_text(LSFG)
        data, mtime = tomlio.load(self.path)
        self.assertEqual(tomllib.loads(tomlio.dumps(data)), data)
        self.assertEqual(list(data["profile"][0]), ["active_in", "flow_scale", "multiplier", "name", "pacing_mode", "performance_mode"])
        data["profile"][1]["multiplier"] = 4
        data["profile"][1]["unknown_to_us"] = "kept"
        tomlio.save(self.path, data, mtime)
        again, _ = tomlio.load(self.path)
        self.assertEqual(again, data)
        self.assertEqual(again["global"], {"allow_fp16": True, "log_level": "info"})

    def test_backup_once(self):
        self.path.write_text(LSFG)
        data, mtime = tomlio.load(self.path)
        data["global"]["log_level"] = "debug"
        mtime = tomlio.save(self.path, data, mtime)
        backup = self.path.with_name("conf.toml.invasor-backup")
        self.assertEqual(backup.read_text(), LSFG)
        data["global"]["log_level"] = "error"
        tomlio.save(self.path, data, mtime)
        self.assertEqual(backup.read_text(), LSFG)  # the original, not the previous save

    def test_changed_by_someone_else_is_refused(self):
        self.path.write_text(LSFG)
        data, mtime = tomlio.load(self.path)
        os.utime(self.path, ns=(mtime + 10**9, mtime + 10**9))  # another program saved
        with self.assertRaises(Unavailable):
            tomlio.save(self.path, data, mtime)
        self.assertEqual(self.path.read_text(), LSFG)

    def test_values_and_strings(self):
        data = {"version": 2, "s": 'quote " back \\ tab\t nl\n ñ', "f": 0.1, "whole": 1.0, "e": 1e-05,
                "l": [], "ls": ["a", "b c"], "t": {"x": True, "sub": {"y": 1}}, "weird key": 1}
        self.assertEqual(tomllib.loads(tomlio.dumps(data)), data)
        for bad in ({"x": float("inf")}, {"x": [[1]]}, {"x": None}, {"x": [{"a": 1}, 2]}):
            with self.subTest(data=bad), self.assertRaises(InvalidArgument):
                tomlio.save(self.path, bad)
        self.assertFalse(self.path.exists())

    def test_missing_and_invalid_files(self):
        self.assertEqual(tomlio.load(self.path), ({}, None))
        tomlio.save(self.path, {"version": 2})  # creates it, no backup of nothing
        self.assertFalse(self.path.with_name("conf.toml.invasor-backup").exists())
        self.path.write_text("version = = 2")
        with self.assertRaises(InvalidArgument):
            tomlio.load(self.path)

    def test_validate_before_replacing(self):
        self.path.write_text(LSFG)
        data, mtime = tomlio.load(self.path)
        data["global"]["log_level"] = "debug"
        seen = []

        def refuse(tmp):
            seen.append(tmp.read_text())
            raise InvalidArgument("rejected by the program")

        with self.assertRaisesRegex(InvalidArgument, "rejected"):
            tomlio.save(self.path, data, mtime, validate=refuse)
        self.assertIn('log_level = "debug"', seen[0])
        self.assertEqual(self.path.read_text(), LSFG)  # original untouched
        self.assertEqual([p.name for p in self.path.parent.iterdir() if p.name.startswith(".")], [])  # no temp left
        tomlio.save(self.path, data, mtime, validate=lambda tmp: None)
        self.assertIn('log_level = "debug"', self.path.read_text())

    def test_permissions_are_kept(self):
        self.path.write_text(LSFG)
        self.path.chmod(0o600)
        data, mtime = tomlio.load(self.path)
        tomlio.save(self.path, data, mtime)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(tomllib is not None, "only where tomllib is missing (Python < 3.11)")
class WithoutTomllib(unittest.TestCase):
    def test_reading_says_it_needs_a_newer_python(self):
        with self.assertRaisesRegex(Unavailable, "Python 3.11"):
            tomlio.load(Path("whatever.toml"))
