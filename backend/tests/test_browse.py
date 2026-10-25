import asyncio
import inspect
import os
import unittest
from pathlib import Path
from unittest import mock

from invasor import browse, core
from invasor.schema import InvalidArgument

from .helpers import patch, temp_dir


def names(entries):
    return [e["name"] for e in entries]


class Listdir(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(temp_dir(self)))
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "inhome").mkdir()
        patch(self, "pathlib.Path.home", lambda *a: self.home)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def assert_home(self, r):
        self.assertEqual(r["path"], str(self.home))
        self.assertEqual(names(r["dirs"]), ["inhome"])

    def test_no_path_is_home(self):
        self.assert_home(browse.listdir())
        self.assert_home(browse.listdir(None))
        self.assert_home(browse.listdir(""))

    def test_bad_paths_are_rejected(self):
        for bad in ["relative/dir", "rel", "/a\x00b", "/" + "a" * 4096, 123, b"/tmp", ["/"]]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidArgument):
                    browse.listdir(bad)

    def test_missing_or_file_is_home(self):
        (self.root / "file.txt").write_text("x")
        self.assert_home(browse.listdir(str(self.root / "nope")))
        self.assert_home(browse.listdir(str(self.root / "file.txt")))

    def test_shape_and_parent(self):
        d = self.root / "d"
        d.mkdir()
        r = browse.listdir(str(d))
        self.assertEqual(r["path"], str(d))
        self.assertEqual(r["parent"], str(self.root))
        self.assertEqual(r["dirs"], [])
        self.assertEqual(r["files"], [])
        self.assertFalse(r["error"])

    def test_root_has_no_parent(self):
        r = browse.listdir("/")
        self.assertEqual(r["path"], "/")
        self.assertIsNone(r["parent"])

    def test_link_is_listed_as_its_real_path(self):
        real = self.root / "real"
        (real / "inside").mkdir(parents=True)
        (self.root / "link").symlink_to(real)
        r = browse.listdir(str(self.root / "link"))
        self.assertEqual(r["path"], str(real))
        self.assertEqual(r["parent"], str(self.root))
        self.assertEqual(names(r["dirs"]), ["inside"])
        # A path with .. is resolved too.
        self.assertEqual(browse.listdir(str(real / "inside" / ".."))["path"], str(real))

    def test_hidden_left_out_and_sorted(self):
        d = self.root / "d"
        for n in ["b", "A", "c", ".hidden"]:
            (d / n).mkdir(parents=True)
        for n in ["y.txt", "X.txt", "z.txt", ".h.txt"]:
            (d / n).write_text("x")
        r = browse.listdir(str(d), files=True)
        self.assertEqual(names(r["dirs"]), ["A", "b", "c"])
        self.assertEqual(names(r["files"]), ["X.txt", "y.txt", "z.txt"])

    def test_hidden_listed_when_asked(self):
        d = self.root / "d"
        for n in ["b", ".A", ".hidden", "C"]:
            (d / n).mkdir(parents=True)
        for n in ["y.dll", ".X.DLL", ".z.txt", "w.txt"]:
            (d / n).write_text("x")
        off = browse.listdir(str(d), files=True)
        self.assertEqual(names(off["dirs"]), ["b", "C"])
        self.assertEqual(names(off["files"]), ["w.txt", "y.dll"])
        self.assertEqual(browse.listdir(str(d), files=True, hidden=False), off)
        on = browse.listdir(str(d), files=True, hidden=True)
        self.assertEqual(names(on["dirs"]), [".A", ".hidden", "b", "C"])
        self.assertEqual(names(on["files"]), [".X.DLL", ".z.txt", "w.txt", "y.dll"])
        # ext still filters hidden files; folders first and the limit as always.
        self.assertEqual(names(browse.listdir(str(d), files=True, ext=[".dll"], hidden=True)["files"]), [".X.DLL", "y.dll"])
        r = browse.listdir(str(d), files=True, hidden=True, limit=5)
        self.assertEqual(len(r["dirs"]), 4)
        self.assertEqual(names(r["files"]), [".X.DLL"])

    def test_no_files_unless_asked(self):
        d = self.root / "d"
        (d / "sub").mkdir(parents=True)
        (d / "f.txt").write_text("x")
        r = browse.listdir(str(d))
        self.assertEqual(names(r["dirs"]), ["sub"])
        self.assertEqual(r["files"], [])

    def test_ext_filter_is_case_insensitive(self):
        d = self.root / "d"
        d.mkdir()
        for n in ["a.dll", "B.DLL", "c.Dll", "d.dll.bak", "dll", "e.txt", "f.ini"]:
            (d / n).write_text("x")
        (d / "g.dll").mkdir()  # a folder stays a folder
        r = browse.listdir(str(d), files=True, ext=[".DLL", ".ini"])
        self.assertEqual(names(r["files"]), ["a.dll", "B.DLL", "c.Dll", "f.ini"])
        self.assertEqual(names(r["dirs"]), ["g.dll"])

    def test_bad_ext_is_rejected(self):
        d = self.root / "d"
        d.mkdir()
        for bad in [["dll"], ["."], ["." + "a" * 16], [".a"] * 21, ".dll", [1], ("x",)]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidArgument):
                    browse.listdir(str(d), files=True, ext=bad)
        # The limits themselves are fine.
        browse.listdir(str(d), files=True, ext=[".a"] * 20)
        browse.listdir(str(d), files=True, ext=["." + "a" * 15])

    def test_links_and_special_files(self):
        d = self.root / "d"
        d.mkdir()
        (self.root / "target").mkdir()
        (self.root / "t.txt").write_text("x")
        (d / "to-dir").symlink_to(self.root / "target")
        (d / "to-file").symlink_to(self.root / "t.txt")
        (d / "broken").symlink_to(self.root / "missing")
        os.mkfifo(d / "fifo")
        r = browse.listdir(str(d), files=True)
        self.assertEqual(names(r["dirs"]), ["to-dir"])
        self.assertEqual(names(r["files"]), ["to-file"])

    def test_limit_counts_folders_first(self):
        d = self.root / "d"
        for n in ["a", "b", "c"]:
            (d / n).mkdir(parents=True)
        for n in ["x", "y", "z"]:
            (d / n).write_text("x")
        r = browse.listdir(str(d), files=True, limit=4)
        self.assertEqual(names(r["dirs"]), ["a", "b", "c"])
        self.assertEqual(len(r["files"]), 1)
        r = browse.listdir(str(d), files=True, limit=2)
        self.assertEqual(len(r["dirs"]), 2)
        self.assertEqual(r["files"], [])
        self.assertEqual(browse.LIMIT, 1000)

    @unittest.skipIf(os.geteuid() == 0, "root reads any folder")
    def test_unreadable_folder_gives_an_error(self):
        d = self.root / "locked"
        (d / "sub").mkdir(parents=True)
        d.chmod(0)
        self.addCleanup(d.chmod, 0o755)
        r = browse.listdir(str(d), files=True)
        self.assertEqual(r["path"], str(d))
        self.assertEqual(r["parent"], str(self.root))
        self.assertEqual(r["dirs"], [])
        self.assertEqual(r["files"], [])
        self.assertIsInstance(r["error"], str)
        self.assertTrue(r["error"])

    def test_game_labels(self):
        games = {"123": "Some Game"}
        for parent in ["compatdata", "shadercache"]:
            with self.subTest(parent=parent):
                d = self.root / parent
                for n in ["123", "456", "12a", "0"]:
                    (d / n).mkdir(parents=True)
                r = browse.listdir(str(d), names_of=lambda appids: {a: games[a] for a in appids if a in games})
                labels = {e["name"]: e["label"] for e in r["dirs"]}
                self.assertEqual(labels, {"0": "0", "123": "123 - Some Game", "456": "456", "12a": "12a"})

    def test_names_are_asked_once_for_the_whole_folder(self):
        d = self.root / "compatdata"
        for n in ["1", "2", "3", "x"]:
            (d / n).mkdir(parents=True)
        calls = []
        browse.listdir(str(d), names_of=lambda appids: calls.append(sorted(appids)) or {})
        self.assertEqual(calls, [["1", "2", "3"]])

    def test_no_labels_outside_compatdata(self):
        d = self.root / "other"
        (d / "123").mkdir(parents=True)
        r = browse.listdir(str(d), names_of=lambda appids: {a: "Some Game" for a in appids})
        self.assertEqual(r["dirs"], [{"name": "123", "label": "123"}])

    def test_default_labels_are_the_name(self):
        d = self.root / "compatdata"
        (d / "123").mkdir(parents=True)
        self.assertEqual(browse.listdir(str(d))["dirs"], [{"name": "123", "label": "123"}])


class FakeGame:
    def __init__(self, info):
        self._info = info

    def info(self, appid):
        return self._info(appid)

    def names(self, appids):
        return {a: n for a in appids if (n := (self._info(a) or {}).get("name"))}


def run(value):
    return asyncio.run(value) if inspect.isawaitable(value) else value


class CoreBrowsePath(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(temp_dir(self)))
        self.compat = self.root / "compatdata"
        (self.compat / "123").mkdir(parents=True)
        (self.compat / "f.dll").write_text("x")
        (self.compat / "f.txt").write_text("x")

    def method(self, info):
        return core.make_methods(None, FakeGame(info), None, {})["browse_path"]

    def test_labels_from_the_game_context(self):
        r = run(self.method(lambda appid: {"appid": appid, "name": "Game " + appid})(str(self.compat)))
        self.assertEqual(r["dirs"], [{"name": "123", "label": "123 - Game 123"}])

    def test_unknown_or_failing_game_gives_the_plain_name(self):
        def boom(appid):
            raise RuntimeError("no Steam")

        for info in [lambda appid: None, lambda appid: {"appid": appid, "name": None}, boom]:
            with self.subTest(info=info):
                r = run(self.method(info)(str(self.compat)))
                self.assertEqual(r["dirs"], [{"name": "123", "label": "123"}])

    def test_files_and_ext(self):
        browse_path = self.method(lambda appid: None)
        r = run(browse_path(str(self.compat), True, [".DLL"]))
        self.assertEqual(names(r["files"]), ["f.dll"])
        r = run(browse_path(path=str(self.compat), files=True))
        self.assertEqual(names(r["files"]), ["f.dll", "f.txt"])
        self.assertEqual(run(browse_path(str(self.compat)))["files"], [])

    def test_files_must_be_a_bool(self):
        browse_path = self.method(lambda appid: None)
        for bad in ["yes", 1, None, []]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidArgument):
                    run(browse_path(str(self.compat), bad))

    def test_hidden(self):
        (self.compat / ".dot").mkdir()
        browse_path = self.method(lambda appid: None)
        self.assertEqual(names(run(browse_path(str(self.compat)))["dirs"]), ["123"])
        self.assertEqual(names(run(browse_path(str(self.compat), hidden=True))["dirs"]), [".dot", "123"])
        self.assertEqual(names(run(browse_path(str(self.compat), False, None, True))["dirs"]), [".dot", "123"])
        for bad in ["yes", 1, None, []]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidArgument):
                    run(browse_path(str(self.compat), hidden=bad))

    def test_bad_path_is_rejected(self):
        with self.assertRaises(InvalidArgument):
            run(self.method(lambda appid: None)("relative"))


if __name__ == "__main__":
    unittest.main()
