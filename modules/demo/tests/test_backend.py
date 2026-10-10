import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# Invasor's core (ctx.toml, forms): $INVASOR_CORE (set by tools/check_module.py), else the
# core this module sits in (modules/demo).
sys.path.insert(0, str(Path(os.environ.get("INVASOR_CORE") or HERE.parents[1]) / "backend"))

from invasor import schema, tomlio  # noqa: E402
from invasor.modules import Form, Settings  # noqa: E402
from invasor.storage import JsonStore  # noqa: E402

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
    id = "demo"

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
        self.steam_start_cbs = []

    def game_data(self, appid):
        return JsonStore(self._folder / "games" / f"{appid}.json")

    def notify(self, title, body="", icon="", sound=""):
        self.notified.append((title, body))

    def on_steam_start(self, cb):
        self.steam_start_cbs.append(cb)


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
        self.assertEqual(self.ctx.steam_start_cbs, [self.b.steam_started])
        self.assertEqual(self.b.stats()["steam_starts"], 0)
        self.b.steam_started()
        self.b.steam_started()
        stats = self.b.stats()
        self.assertEqual(stats["steam_starts"], 2)
        self.assertRegex(stats["last_steam_start"], r"^\d\d:\d\d:\d\d$")

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

    def test_profile_path_is_named_after_the_module_id(self):
        b = load_backend()
        self.ctx.id = "smoke-demo"
        self.assertEqual(b.profile_path(self.ctx).parent.name, "invasor-smoke-demo")



if __name__ == "__main__":
    unittest.main()
