import json
import stat
import unittest
import zipfile
from unittest import mock

from invasor import install
from invasor.schema import InvalidArgument

from .helpers import patch, temp_dir

MANIFEST = {"api": 1, "name": "Hello", "version": "1.0.0", "author": "Some One <some@one.org>"}


class Zips(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.user = self.home / ".local/share/invasor/user-modules"
        patch(self, "invasor.install.HOME", self.home)
        patch(self, "invasor.config.USER_MODULES_DIR", self.user)
        (self.home / "Downloads").mkdir()

    def zip(self, name, files, links=()):
        p = self.home / "Downloads" / name
        with zipfile.ZipFile(p, "w") as z:
            for path, data in files.items():
                z.writestr(path, data if isinstance(data, (str, bytes)) else json.dumps(data))
            for path in links:
                info = zipfile.ZipInfo(path)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, "/etc/passwd")
        return f"Downloads/{name}"

    def good(self, name="hello.zip", backend="METHODS = {'hi': lambda: 'hi'}\n", **extra):
        files = {"hello/module.json": MANIFEST, "hello/backend.py": backend, "hello/helpers.py": "X = 1\n", **extra}
        return self.zip(name, files)

    def test_browse_lists_folders_and_zips_inside_home_only(self):
        self.good()
        (self.home / "Downloads" / "notes.txt").write_text("x")
        (self.home / "Downloads" / ".hidden.zip").write_text("x")
        listing = install.browse()
        self.assertEqual(listing["path"], "Downloads")
        self.assertEqual([e["name"] for e in listing["entries"]], ["hello.zip"])
        self.assertEqual(listing["parent"], "")
        self.assertIn("Downloads", [e["name"] for e in install.browse("")["entries"]])
        for bad in ("..", "../..", "/etc", "Downloads/../../x"):
            with self.subTest(path=bad), self.assertRaises(InvalidArgument):
                install.browse(bad)

    def test_localised_downloads_folder(self):
        (self.home / "Descargas").mkdir()
        (self.home / ".config").mkdir()
        (self.home / ".config/user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Descargas"\n')
        self.assertEqual(install.browse()["path"], "Descargas")

    def test_inspect_and_install_and_replace(self):
        path = self.good()
        info = install.inspect(path, {"core", "demo"})
        self.assertEqual((info["id"], info["name"], info["installed_version"]), ("hello", "Hello", None))
        self.assertEqual(info["author"], "Some One")  # the name only, as the UI shows it
        self.assertEqual(install.install(path, {"core"}), "hello")
        self.assertTrue((self.user / "hello" / "helpers.py").is_file())
        self.assertEqual(install.inspect(path, {"core"})["installed_version"], "1.0.0")
        with self.assertRaisesRegex(InvalidArgument, "already installed"):
            install.install(path, {"core"})
        replaced = []
        install.install(path, {"core"}, replace=True, before_replace=replaced.append)
        self.assertEqual(replaced, ["hello"])
        self.assertEqual(sorted(p.name for p in self.user.iterdir()), ["hello"])  # no staging left
        install.uninstall("hello")
        self.assertEqual(list(self.user.iterdir()), [])

    def test_a_failed_replace_keeps_the_old_version(self):
        path = self.good()
        install.install(path, {"core"})
        (self.user / "hello" / "marker").write_text("old")
        real = install.os.replace
        calls = []

        def replace(src, dst):
            calls.append(src)
            if len(calls) == 2:  # the new version into place
                raise OSError("disk full")
            return real(src, dst)

        with mock.patch.object(install.os, "replace", replace), self.assertRaisesRegex(OSError, "disk full"):
            install.install(path, {"core"}, replace=True)
        self.assertEqual((self.user / "hello" / "marker").read_text(), "old")
        self.assertEqual(sorted(p.name for p in self.user.iterdir()), ["hello"])  # no staging left

    def test_rejections(self):
        cases = {
            "not a zip": (lambda: ((self.home / "Downloads" / "bad.zip").write_text("nope"), "Downloads/bad.zip")[1], "valid zip"),
            "outside home": (lambda: "../x.zip", "inside your home"),
            "traversal": (lambda: self.zip("t.zip", {"hello/module.json": MANIFEST, "hello/../../evil": "x"}), "unsafe"),
            "double slash escape": (lambda: self.zip("d.zip", {"hello/module.json": MANIFEST, f"hello/{self.home}/escaped": "x"}), "unsafe"),
            "symlink": (lambda: self.zip("l.zip", {"hello/module.json": MANIFEST}, links=["hello/link"]), "symbolic link"),
            "root files": (lambda: self.zip("r.zip", {"module.json": MANIFEST}), "folder named after"),
            "no manifest": (lambda: self.zip("n.zip", {"x/readme.txt": "hi"}), "no <id>/module.json"),
            "two modules": (lambda: self.zip("2.zip", {"a/module.json": MANIFEST, "b/module.json": MANIFEST}), "module.json"),
            "bad manifest": (lambda: self.zip("m.zip", {"hello/module.json": {"api": 2, "name": "H", "version": "1"}}), "api"),
            "bad json": (lambda: self.zip("j.zip", {"hello/module.json": "{nope"}), "valid JSON"),
            "bad id": (lambda: self.zip("i.zip", {"Hello World/module.json": MANIFEST}), "invalid module id"),
            "newer core": (lambda: self.zip("c.zip", {"hello/module.json": {**MANIFEST, "min_core": "99.0.0"}}), "needs Invasor 99.0.0 or newer"),
            "reserved": (lambda: self.zip("demo.zip", {"demo/module.json": MANIFEST}), "reserved"),
            "unbuilt ui": (lambda: self.good("u.zip", **{"hello/ui.ts": "x"}), "dist/ui.js"),
            "broken backend": (lambda: self.good("b.zip", backend="raise RuntimeError('boom')\n"), "boom"),
            "exit backend": (lambda: self.good("e.zip", backend="import sys\nsys.exit(3)\n"), "SystemExit"),
            "bad METHODS": (lambda: self.good("x.zip", backend="METHODS = {'a': 1}\n"), "METHODS"),
            "forged verdict": (lambda: self.good("f.zip", backend="import atexit\natexit.register(lambda: print('{\"ok\": true, \"error\": null}'))\nraise RuntimeError('boom')\n"), "couldn't be checked"),
        }
        for label, (make, msg) in cases.items():
            with self.subTest(case=label), self.assertRaisesRegex(InvalidArgument, msg):
                install.install(make(), {"core", "demo"})
        self.assertEqual([p for p in self.user.iterdir()] if self.user.exists() else [], [])

    def test_a_double_slash_never_writes_outside_the_staging_folder(self):
        # "hello//<abs>" passes the name checks, but stripping "hello/" leaves an absolute path.
        outside = self.home / "outside"
        path = self.zip("slip.zip", {"hello/module.json": MANIFEST, f"hello/{outside}/marker": "x"})
        with self.assertRaisesRegex(InvalidArgument, "unsafe"):
            install.inspect(path, set())
        self.assertFalse(outside.exists())

    def test_min_core_met_installs(self):
        p = self.zip("ok.zip", {"hello/module.json": {**MANIFEST, "min_core": "0.0.1"}, "hello/backend.py": "METHODS = {}\n"})
        self.assertEqual(install.install(p, {"core"}), "hello")

    def test_size_limits(self):
        patch(self, "invasor.install.MAX_UNPACKED_BYTES", 100)
        with self.assertRaisesRegex(InvalidArgument, "unpacks to more"):
            install.inspect(self.good(**{"hello/big.bin": b"x" * 200}), set())

    def test_built_ui_is_accepted(self):
        path = self.good("ui.zip", **{"hello/ui.ts": "x", "hello/dist/ui.js": "var __invasorModule={};"})
        self.assertEqual(install.install(path, set()), "hello")
        self.assertTrue((self.user / "hello" / "dist" / "ui.js").is_file())


if __name__ == "__main__":
    unittest.main()
