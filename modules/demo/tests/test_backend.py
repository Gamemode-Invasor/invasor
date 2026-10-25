import hashlib
import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# Invasor's core (ctx.toml, forms): $INVASOR_CORE (set by tools/check_module.py), else the
# core this module sits in (modules/demo).
sys.path.insert(0, str(Path(os.environ.get("INVASOR_CORE") or HERE.parents[1]) / "backend"))

from invasor import archive, paths, schema, tomlio, vdf  # noqa: E402
from invasor.modules import Form, Settings  # noqa: E402
from invasor.storage import JsonStore  # noqa: E402
from invasor.tasks import TaskRegistry  # noqa: E402

import helpers  # noqa: E402


def load_backend():
    name = "demo_backend_under_test"
    for n in [n for n in sys.modules if n == name or n.startswith(name + ".")]:
        del sys.modules[n]
    spec = importlib.util.spec_from_file_location(name, HERE / "backend.py", submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeGame:
    running = {"appid": "1000", "name": "Test Game", "shortcut": False}
    selected = None

    def info(self, appid):
        return {"1000": self.running}.get(str(appid))


class FakeCtx:
    InvalidArgument = schema.InvalidArgument
    Unavailable = schema.Unavailable
    toml = tomlio
    paths = paths
    vdf = vdf
    archive = archive
    id = "demo"
    path = HERE

    def __init__(self, folder):
        manifest = schema.parse_manifest(json.loads((HERE / "module.json").read_text()), "demo")
        self.forms = {n: Form(f) for n, f in manifest["form_fields"].items()}
        self.game = FakeGame()
        self.log = logging.getLogger("test.demo")
        self.settings = Settings("demo", manifest["fields"], self.log)
        self.settings._store = JsonStore(folder / "settings.json")
        self.data = JsonStore(folder / "data.json")
        self._folder = folder
        self.notified = []
        self.events = FakeEvents()
        self.registry = TaskRegistry()
        self.http = FakeHttp()
        self.steam = FakeSteam()

    def steam_language(self, raw=False):
        return "spanish" if raw else "es"

    def game_data(self, appid):
        return JsonStore(self._folder / "games" / f"{appid}.json")

    def notify(self, title, body="", icon="", timeout=15, sound=""):
        self.notified.append((title, body))
        self.last_notify = {"timeout": timeout, "sound": sound}


    def task(self, label):
        return self.registry.start("demo", label)


class FakeHttp:
    """ctx.http without the network: writes a fixed text and remembers what it was asked."""
    BODY = b"GNU GENERAL PUBLIC LICENSE"

    def __init__(self):
        self.asked = []

    def download(self, url, dest, *, checksum=None, user_chose=False, max_bytes=None, task=None):
        self.asked.append((url, Path(dest).name, checksum))
        Path(dest).write_bytes(self.BODY)
        return {"path": str(dest), "size": len(self.BODY), "sha256": hashlib.sha256(self.BODY).hexdigest(), "sha512": ""}


class FakeSteam:
    """ctx.steam without Steam: one list of shortcuts, and the calls it got."""

    def __init__(self):
        self.shortcuts = {}  # appid -> {"name", "exe", "options"}
        self.calls = []
        self.missing = set()

    def supports(self, name):
        return name not in self.missing

    def add_to(self, name, exe, *, start_dir=None, launch_options=None):
        self.calls.append(("add_to", name, exe, launch_options))
        for appid, s in self.shortcuts.items():
            if s["exe"] == exe:
                return {"appid": appid, "existed": True}
        self.shortcuts[3000000001] = {"name": name, "exe": exe, "options": launch_options or ""}
        return {"appid": 3000000001, "existed": False}

    def launch_options(self, appid):
        return self.shortcuts[appid]["options"]

    def set_launch_options(self, appid, line):
        self.shortcuts[appid]["options"] = line
        return {"applied": True, "verified": True}

    def remove_shortcut(self, appid):
        del self.shortcuts[appid]
        return {"removed": True}

    def open_url(self, url):
        self.calls.append(("open_url", url))


class FakeEvents:
    def __init__(self):
        self.listeners = {}

    def on(self, name, cb):
        self.listeners.setdefault(name, []).append(cb)

    def emit(self, name, payload):
        for cb in self.listeners.get(name, []):
            cb(payload)


class Helpers(unittest.TestCase):
    def test_presets(self):
        self.assertEqual((helpers.preset_fan("quiet"), helpers.preset_fan("max"), helpers.preset_fan("custom")), (30, 100, None))
        self.assertEqual(helpers.preset_notification("max", "hi"), ("Demo: Max preset", "hi"))
        self.assertEqual(helpers.profile_section({"profile": 3}), {})


class Backend(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.folder = Path(d.name)
        self.b = load_backend()
        self.b.profile_path = lambda context=None: self.folder / "other-program" / "profile.toml"
        self.ctx = FakeCtx(self.folder)
        self.b.setup(self.ctx)
        self.addCleanup(self.b.teardown)

    def test_preset_sets_the_fan_and_notifies(self):
        self.ctx.settings.set("notify_preset", True)
        self.ctx.settings.set("preset", "quiet", notify=True)
        self.assertEqual(self.ctx.settings.get("fan"), 30)
        self.ctx.settings.set("preset", "custom", notify=True)
        self.assertEqual(self.ctx.settings.get("fan"), 30)  # Custom leaves it alone
        for _ in range(50):
            if self.ctx.notified:
                break
            time.sleep(0.02)  # the notification is sent from its own thread
        self.assertEqual(self.ctx.notified, [("Demo: Quiet preset", "Preset applied")])

    def test_profile_lives_in_a_toml_file_and_keeps_the_rest(self):
        path = self.b.profile_path()
        self.assertEqual(self.b.profile_get(), self.ctx.forms["profile"].defaults())
        self.assertEqual(self.b.profile_set("scale", 0.31), 0.3)  # snapped by the form
        path.write_text(path.read_text() + '\n[other]\nkept = "yes"\n')
        self.assertEqual(self.b.profile_set("vsync", False), False)
        data = tomllib.loads(path.read_text())
        self.assertEqual((data["profile"]["scale"], data["profile"]["vsync"], data["other"]), (0.3, False, {"kept": "yes"}))
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("pacing", "nope")

    def test_storage_and_clean_errors(self):
        self.assertEqual(self.b.visit("1000"), {"visits": 1, "game_visits": 1})
        self.assertEqual(self.b.visit(), {"visits": 2, "game_visits": None})
        self.assertEqual(self.b.stats()["visits"], 2)
        self.assertEqual(self.b.whoami()["info"]["name"], "Test Game")
        with self.assertRaises(schema.InvalidArgument):
            self.b.fail("invalid")
        with self.assertRaises(schema.Unavailable):
            self.b.fail("unavailable")

    def test_steam_starts_are_counted(self):
        self.assertEqual(self.ctx.events.listeners["steam_start"], [self.b.steam_started])
        self.assertEqual(self.b.stats()["steam_starts"], 0)
        self.ctx.events.emit("steam_start", {"instance": "a"})
        self.ctx.events.emit("steam_start", {"instance": "b"})
        stats = self.b.stats()
        self.assertEqual(stats["steam_starts"], 2)
        self.assertRegex(stats["last_steam_start"], r"^\d\d:\d\d:\d\d$")

    def test_slow_task_reports_its_steps_and_ends(self):
        self.b.time = mock.Mock()  # no real seconds in a test
        self.assertEqual(self.b.slow_task(3), {"seconds": 3})
        done = self.ctx.registry.snapshot()["recent"][0]
        self.assertEqual((done["module"], done["state"], done["text"]), ("demo", "done", ""))
        self.assertEqual(self.b.time.sleep.call_count, 3)

    def test_slow_task_can_fail_on_purpose_and_checks_its_argument(self):
        self.b.time = mock.Mock()
        with self.assertRaises(schema.Unavailable):
            self.b.slow_task(2, fail=True)
        failed = self.ctx.registry.snapshot()["recent"][0]
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["fraction"], 0.5)  # where it got to
        for bad in (0, 31, "5", True, 2.5):
            with self.subTest(seconds=bad), self.assertRaises(schema.InvalidArgument):
                self.b.slow_task(bad)

    def test_game_events_are_noted(self):
        self.assertIsNone(self.b.stats()["last_game_event"])
        self.ctx.events.emit("game_started", {"appid": "1", "name": "Test Game", "shortcut": False})
        self.assertRegex(self.b.stats()["last_game_event"], r"^started Test Game at \d\d:\d\d:\d\d$")
        self.ctx.events.emit("game_stopped", {"appid": "1", "name": None, "shortcut": False})
        self.assertRegex(self.b.stats()["last_game_event"], r"^stopped 1 at ")
        self.ctx.events.emit("game_selected", {"appid": "2", "name": "Other", "shortcut": True})
        self.assertRegex(self.b.stats()["last_game_event"], r"^selected Other at ")

    def test_notify_passes_timeout_and_sound_and_ctx_path_is_used(self):
        self.b.notify("T", "B", sound="trophy")
        self.assertEqual(self.ctx.last_notify, {"timeout": 10, "sound": "trophy"})
        self.assertEqual(self.b.whoami()["folder"], HERE.name)  # whatever the folder is called (the smoke runs a renamed copy)
        self.assertGreaterEqual(self.b.stats()["files"], 2)  # backend.py and helpers.py

    def test_upgrade_is_remembered(self):
        self.assertIsNone(self.b.whoami()["upgraded_from"])
        self.b.upgrade(self.ctx, "0.1.0")
        self.assertEqual(self.b.whoami()["upgraded_from"], "0.1.0")

    def test_uninstall_removes_what_it_left_outside_only_with_purge(self):
        self.b.profile_set("vsync", False)
        folder = self.b.profile_path().parent
        self.b.uninstall(self.ctx, False)
        self.assertTrue(folder.exists())
        self.b.uninstall(self.ctx, True)
        self.assertFalse(folder.exists())

    def test_steam_folders(self):
        steam = self.folder / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        (steam / "userdata/116437234/config").mkdir(parents=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{steam}"\n\t}}\n}}\n')
        with mock.patch.object(paths, "MEDIA_GLOBS", ()):
            with mock.patch.object(paths, "STEAM_ROOT", self.folder / "nowhere"):
                none = self.b.steam_folders()
            with mock.patch.object(paths, "STEAM_ROOT", steam):
                found = self.b.steam_folders()
        self.assertIsNone(none["account"])
        self.assertEqual(none["libraries"], [])
        self.assertEqual(found, {"root": str(steam), "libraries": [str(steam / "steamapps")],
                                 "account": str(steam / "userdata/116437234"),
                                 "grid": str(steam / "userdata/116437234/config/grid"), "libraries_vdf_entries": 1})

    def test_unpack_sample(self):
        first = self.b.unpack_sample()
        (self.folder / "sample/stale.txt").write_text("from before")
        again = self.b.unpack_sample()
        self.assertEqual(first, again)
        self.assertEqual(again, {"folder": str(self.folder / "sample"),
                                 "files": ["README.txt", "hello.txt", "notes/list.txt"]})
        self.assertFalse((self.folder / "sample/stale.txt").exists())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir() if p.name.startswith(".")), [])

    def test_download_sample_with_and_without_a_checksum(self):
        got = self.b.download_sample()
        self.assertEqual(got, {"size": len(FakeHttp.BODY), "sha256": hashlib.sha256(FakeHttp.BODY).hexdigest()})
        self.assertEqual((self.folder / "LICENSE.txt").read_bytes(), FakeHttp.BODY)
        self.b.download_sample(checked=False)
        self.assertEqual(self.ctx.http.asked, [
            (self.b.SAMPLE_URL, "LICENSE.txt", ("sha256", self.b.SAMPLE_SHA256)),
            (self.b.SAMPLE_URL, "LICENSE.txt", None),
        ])

    def test_the_demo_shortcut_is_added_read_set_and_removed(self):
        with self.assertRaises(schema.InvalidArgument):
            self.b.steam_shortcut_options()  # none yet
        added = self.b.steam_shortcut_add()
        script = self.folder / "demo-shortcut.sh"
        self.assertEqual(added, {"appid": 3000000001, "existed": False})
        self.assertEqual(self.ctx.steam.calls, [("add_to", "Invasor Demo", str(script), "DEMO=1 %command%")])
        self.assertTrue(script.read_text().startswith("#!/bin/sh"))
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertTrue(self.b.steam_shortcut_add()["existed"])
        self.assertEqual(self.b.steam_shortcut_options(), {"appid": 3000000001, "options": "DEMO=1 %command%"})
        self.assertEqual(self.b.steam_shortcut_options("DEMO=2 %command%"),
                         {"appid": 3000000001, "applied": True, "verified": True, "options": "DEMO=2 %command%"})
        self.assertEqual(self.b.steam_shortcut_remove(), {"removed": True})
        self.assertEqual(self.ctx.steam.shortcuts, {})
        with self.assertRaises(schema.InvalidArgument):
            self.b.steam_shortcut_remove()

    def test_uninstall_leaves_the_shortcut_and_removes_the_script_only_with_purge(self):
        self.b.steam_shortcut_add()
        self.b.uninstall(self.ctx, False)
        self.assertTrue((self.folder / "demo-shortcut.sh").exists())
        self.b.uninstall(self.ctx, True)
        self.assertFalse((self.folder / "demo-shortcut.sh").exists())
        self.assertIn(3000000001, self.ctx.steam.shortcuts)  # never removed in uninstall

    def test_open_url_language_and_supports(self):
        self.assertEqual(self.b.open_url(), {"ok": True})
        self.assertEqual(self.ctx.steam.calls, [("open_url", "https://github.com/Gamemode-Invasor/invasor")])
        self.assertEqual(self.b.steam_language(), {"code": "es", "raw": "spanish"})
        self.ctx.steam.missing = {"remove_shortcut"}
        self.assertEqual(self.b.steam_supports(), {"add_to": True, "open_url": True, "remove_shortcut": False,
                                                   "launch_options": True, "set_launch_options": True})

    def test_profile_path_is_named_after_the_module_id(self):
        b = load_backend()
        self.ctx.id = "smoke-demo"
        self.assertEqual(b.profile_path(self.ctx).parent.name, "invasor-smoke-demo")



if __name__ == "__main__":
    unittest.main()
