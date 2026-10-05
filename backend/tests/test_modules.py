import json
import logging
import unittest

from invasor.context import GameContext
from invasor.modules import ModuleManager

from .helpers import patch, temp_dir

SETTINGS = [
    {"key": "level", "type": "slider", "label": "Level", "min": 0, "max": 10, "default": 5},
    {"section": "More", "items": [{"key": "on", "type": "toggle", "label": "On", "default": True}]},
]


def manifest(name, **extra):
    return {"api": 1, "name": name, "version": "1.0.0", **extra}


class Modules(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        self.mods = root / "modules"
        self.data = root / "data"
        patch(self, "invasor.modules.MODULES_DIR", self.mods)
        patch(self, "invasor.modules.USER_MODULES_DIR", root / "user-modules")
        patch(self, "invasor.modules.DATA_DIR", self.data)
        patch(self, "invasor.config.CONFIG_FILE", root / "config.json")
        self.add("good", (
            "ctx = None\n"
            "events = []\n"
            "def setup(c):\n"
            "    global ctx; ctx = c\n"
            "    c.settings.on_change(lambda k, v: events.append((k, v)))\n"
            "def teardown():\n    events.append('teardown')\n"
            "METHODS = {'hi': lambda: 'hola', 'store': lambda appid: ctx.game_data(appid),\n"
            "           'level': lambda: ctx.settings.get('level'), 'events': lambda: events}\n"
        ), settings=SETTINGS)
        self.add("broken", "raise RuntimeError('boom')\n")
        # Must not have run setup (it would leave a marker): METHODS is checked first.
        self.add("badmethods", (
            "import pathlib\n"
            "def setup(c):\n    (pathlib.Path(c.path) / 'setup-ran').write_text('x')\n"
            "METHODS = {'x': 1}\n"
        ))
        self.add("quitter", "import sys\ndef setup(c):\n    sys.exit(1)\n")
        self.add("halfsetup", (
            "import pathlib\n"
            "here = pathlib.Path(__file__).parent\n"
            "def setup(c):\n    raise RuntimeError('setup failed half way')\n"
            "def teardown():\n    (here / 'torn-down').write_text('x')\n"
        ))
        self.add("multi-file", "from . import helpers\nMETHODS = {'twice': helpers.twice}\n")
        (self.mods / "multi-file" / "helpers.py").write_text("def twice(n):\n    return 2 * n\n")
        self.add("_template", "raise RuntimeError('must never load')\n")
        self.add("oldapi", "", raw={"name": "Old"})
        self.add("typo", "", raw={**manifest("Typo"), "setings": []})
        self.add("toonew", "METHODS = {}\n", raw={**manifest("Too new"), "min_core": "99.0.0"})
        self.add("fits", "METHODS = {}\n", raw={**manifest("Fits"), "min_core": "0.0.1"})
        (self.mods / "core").mkdir()
        (self.mods / "core" / "module.json").write_text("{}")
        (self.mods / "good" / "dist").mkdir()
        (self.mods / "good" / "dist" / "ui.js").write_text("var __invasorModule = {};")
        self.manager = ModuleManager({"disabled_modules": []}, GameContext())
        with self.assertLogs("invasor.modules", "ERROR"):
            self.manager.discover()

    def add(self, mid, backend, raw=None, **extra):
        d = self.mods / mid
        d.mkdir(parents=True)
        (d / "module.json").write_text(json.dumps(raw if raw is not None else manifest(mid.title(), **extra)))
        if backend:
            (d / "backend.py").write_text(backend)

    def listing(self):
        return {m["id"]: m for m in self.manager.listing()}

    def test_broken_module_is_isolated(self):
        self.assertEqual(self.manager.registry["good"]["hi"](), "hola")
        self.assertNotIn("broken", self.manager.registry)
        self.assertNotIn("badmethods", self.manager.registry)
        listing = self.listing()
        self.assertFalse(listing["broken"]["loaded"])
        self.assertTrue(listing["good"]["loaded"])
        self.assertIs(listing["good"]["no_qam"], False)

    def test_exit_and_failing_setup_are_contained(self):
        for mid in ("quitter", "halfsetup", "badmethods"):
            self.assertNotIn(mid, self.manager.registry)
        self.assertTrue((self.mods / "halfsetup" / "torn-down").exists())  # teardown after a failed setup
        self.assertFalse((self.mods / "badmethods" / "setup-ran").exists())

    def test_backend_can_import_its_own_files(self):
        import sys
        self.assertEqual(self.manager.registry["multi-file"]["twice"](21), 42)
        self.assertIn("invasor_mod_multi-file.helpers", sys.modules)
        self.manager.set_enabled("multi-file", False)
        self.assertFalse(any(n.startswith("invasor_mod_multi-file") for n in sys.modules))
        (self.mods / "multi-file" / "helpers.py").write_text("def twice(n):\n    return n + n + 1\n")
        self.manager.set_enabled("multi-file", True)  # re-enabled: fresh code
        self.assertEqual(self.manager.registry["multi-file"]["twice"](21), 43)

    def test_invalid_manifests_are_reported_not_loaded(self):
        listing = self.listing()
        self.assertIn("api", listing["oldapi"]["error"])
        self.assertIn("setings", listing["typo"]["error"])
        self.assertIsNone(listing["good"]["error"])
        self.manager.set_enabled("oldapi", True)
        self.assertNotIn("oldapi", self.manager.registry)

    def ids(self):
        return [m["id"] for m in self.manager.listing()]

    def test_listing_order_is_the_users_then_the_manifests(self):
        default = self.ids()
        self.assertEqual(default, sorted(default, key=lambda i: (self.listing()[i]["order"], self.listing()[i]["name"].lower(), i)))
        self.manager.cfg["module_order"] = ["typo", "good", "gone", "multi-file"]  # "gone" is not installed
        got = self.ids()
        self.assertEqual(got[:3], ["typo", "good", "multi-file"])
        self.assertEqual(got[3:], [i for i in default if i not in ("typo", "good", "multi-file")])  # the rest, as before
        self.assertEqual(sorted(got), sorted(default))
        self.manager.cfg["module_order"] = []
        self.assertEqual(self.ids(), default)

    def test_min_core_newer_than_the_core_is_reported_not_loaded(self):
        listing = self.listing()
        self.assertIn("needs Invasor 99.0.0 or newer", listing["toonew"]["error"])
        self.assertIsNone(listing["fits"]["error"])
        self.manager.set_enabled("toonew", True)
        self.assertNotIn("toonew", self.manager.registry)
        self.assertIn("fits", self.manager.registry)

    def test_templates_and_reserved_ids_are_skipped(self):
        self.assertNotIn("_template", self.manager.manifests)
        self.assertNotIn("core", self.manager.manifests)

    def test_enable_disable_persists_and_tears_down(self):
        events = self.manager.registry["good"]["events"]()
        self.manager.set_enabled("good", False)
        self.assertNotIn("good", self.manager.registry)
        self.assertEqual(events, ["teardown"])
        self.assertEqual(self.manager.cfg["disabled_modules"], ["good"])
        self.manager.set_enabled("good", True)
        self.assertIn("good", self.manager.registry)

    def test_settings_are_validated_and_notified(self):
        settings = self.manager.settings["good"]
        self.assertEqual(settings.get(), {"level": 5, "on": True})
        self.assertEqual(settings.set("level", 99, notify=True), 10)  # clamped
        self.assertEqual(self.manager.registry["good"]["level"](), 10)
        self.assertEqual(self.manager.registry["good"]["events"](), [("level", 10)])
        settings.set("on", False)  # from the backend itself: no notification
        self.assertEqual(self.manager.registry["good"]["events"](), [("level", 10)])
        with self.assertRaises(ValueError):
            settings.set("nope", 1)
        with self.assertRaises(ValueError):
            settings.set("on", "yes")

    def test_corrupt_stored_settings_read_as_defaults(self):
        path = self.data / "good" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"level": "high", "on": False, "old": 1}))
        with self.assertLogs("invasor.mod.good", "WARNING"):
            self.assertEqual(self.manager.settings["good"].get(), {"level": 5, "on": False})
        # Corrected in the file itself: warned once, not on every read.
        self.assertEqual(json.loads(path.read_text()), {"level": 5, "on": False})
        # No assertNoLogs before Python 3.10: a marker line must be the only warning.
        with self.assertLogs("invasor.mod.good", "WARNING") as logs:
            logging.getLogger("invasor.mod.good").warning("marker")
            self.manager.settings["good"].get()
        self.assertEqual([r.getMessage() for r in logs.records], ["marker"])

    def test_game_data_only_accepts_numeric_appids(self):
        store = self.manager.registry["good"]["store"]
        store("4000000000").save({"x": 1})
        self.assertEqual(store(4000000000).load(), {"x": 1})
        for bad in ("../x", "1/2", "", "abc", "²", "٣"):
            with self.subTest(appid=bad), self.assertRaises(ValueError):
                store(bad)

    def test_ui_scripts_and_listing(self):
        self.assertEqual([mid for mid, _ in self.manager.ui_scripts()], ["good"])
        good = self.listing()["good"]
        self.assertTrue(good["ui"] and good["ui_built"])
        self.assertEqual(good["settings"][1]["section"], "More")
        self.assertEqual(good["tab"], "Good")
        self.assertEqual(good["author"], "")

    def test_listing_shows_the_author_without_email(self):
        self.add("authored", "METHODS = {}\n", author="Some One <some@one.org>")
        with self.assertLogs("invasor.modules", "ERROR"):  # the broken fixtures, as in setUp
            self.manager.rescan()
        self.assertEqual(self.listing()["authored"]["author"], "Some One")

    def test_forms_reach_the_backend_and_the_listing(self):
        self.add("former", (
            "ctx = None\n"
            "def setup(c):\n    global ctx; ctx = c\n"
            "METHODS = {'fix': lambda k, v: ctx.forms['profile'].coerce(k, v),\n"
            "           'defaults': lambda: ctx.forms['profile'].defaults(),\n"
            "           'toml': lambda: ctx.toml.dumps({'a': 1})}\n"
        ), forms={"profile": [{"key": "n", "type": "number", "label": "N", "min": 2, "max": 9, "default": 2}]})
        manager = ModuleManager({"disabled_modules": []}, GameContext())
        with self.assertLogs("invasor.modules", "ERROR"):
            manager.discover()
        fns = manager.registry["former"]
        self.assertEqual((fns["fix"]("n", 99), fns["defaults"](), fns["toml"]()), (9, {"n": 2}, "a = 1\n"))
        with self.assertRaises(ValueError):
            fns["fix"]("other", 1)
        listing = {m["id"]: m for m in manager.listing()}
        self.assertEqual(listing["former"]["forms"]["profile"][0]["key"], "n")

    def test_user_modules_load_and_override_core_ones(self):
        import invasor.modules as m
        user = m.USER_MODULES_DIR
        (user / "good").mkdir(parents=True)
        (user / "good" / "module.json").write_text(json.dumps(manifest("User Good")))
        (user / "extra").mkdir()
        (user / "extra" / "module.json").write_text(json.dumps(manifest("Extra")))
        manager = ModuleManager({"disabled_modules": []}, GameContext())
        with self.assertLogs("invasor.modules", "WARNING"):
            manager.discover()
        listing = {x["id"]: x for x in manager.listing()}
        self.assertEqual((listing["good"]["name"], listing["good"]["source"]), ("User Good", "user"))
        self.assertEqual(listing["extra"]["source"], "user")
        self.assertEqual(listing["broken"]["source"], "core")

    def test_hot_add_and_remove(self):
        import invasor.modules as m
        d = m.USER_MODULES_DIR / "hot"
        d.mkdir(parents=True)
        (d / "module.json").write_text(json.dumps(manifest("Hot")))
        (d / "backend.py").write_text("METHODS = {'ping': lambda: 'v1'}\n")
        self.assertEqual(self.manager.add("hot")["loaded"], True)
        self.assertEqual(self.manager.registry["hot"]["ping"](), "v1")
        (d / "backend.py").write_text("METHODS = {'ping': lambda: 'v2'}\n")
        self.manager.add("hot")  # replaced: fresh code
        self.assertEqual(self.manager.registry["hot"]["ping"](), "v2")
        self.manager.remove("hot")
        self.assertNotIn("hot", self.manager.registry)
        self.assertNotIn("hot", self.manager.manifests)

    def test_rescan_sees_folders_added_removed_and_edited(self):
        import shutil
        events = self.manager.registry["good"]["events"]()
        shutil.rmtree(self.mods / "multi-file")
        (self.mods / "halfsetup").rename(self.mods / "_halfsetup")
        self.add("newcomer", "METHODS = {'ping': lambda: 'v1'}\n")
        (self.mods / "good" / "module.json").write_text(json.dumps(manifest("Good Renamed", settings=SETTINGS)))
        with self.assertLogs("invasor.modules", "ERROR"):
            self.manager.rescan()
        self.assertEqual(events, ["teardown"])  # the old code was torn down
        listing = self.listing()
        self.assertNotIn("multi-file", listing)
        self.assertNotIn("halfsetup", listing)
        self.assertNotIn("multi-file", self.manager.registry)
        self.assertEqual(self.manager.registry["newcomer"]["ping"](), "v1")
        self.assertEqual(listing["good"]["name"], "Good Renamed")
        self.assertEqual(self.manager.registry["good"]["events"](), [])  # fresh code

    def user_module(self, mid, backend, version="1.0.0", **extra):
        import invasor.modules as m
        d = m.USER_MODULES_DIR / mid
        d.mkdir(parents=True, exist_ok=True)
        (d / "module.json").write_text(json.dumps({**manifest(mid.title(), **extra), "version": version}))
        (d / "backend.py").write_text(backend)
        return d

    UPGRADER = (
        "import pathlib\n"
        "here = pathlib.Path(__file__).parent\n"
        "def upgrade(c, previous):\n"
        "    if (here / 'fail').exists(): raise RuntimeError('migration failed')\n"
        "    with open(here.parent / 'upgrades', 'a') as f: f.write(f'{previous}->{c.id}\\n')\n"
        "METHODS = {}\n"
    )

    def upgrades(self):
        import invasor.modules as m
        path = m.USER_MODULES_DIR / "upgrades"
        return path.read_text().split() if path.exists() else []

    def test_upgrade_gets_the_previous_version(self):
        self.user_module("up", self.UPGRADER, "1.0.0")
        self.manager.add("up")
        self.assertEqual(self.upgrades(), [])  # first load: nothing to upgrade from
        self.manager.add("up")
        self.assertEqual(self.upgrades(), [])  # same version
        self.user_module("up", self.UPGRADER, "1.1.0")
        self.manager.add("up")
        self.assertEqual(self.upgrades(), ["1.0.0->up"])
        self.assertEqual(json.loads((self.data / "up" / "version.json").read_text()), {"version": "1.1.0"})

    def test_failed_upgrade_doesnt_load_and_retries(self):
        d = self.user_module("up", self.UPGRADER, "1.0.0")
        self.manager.add("up")
        self.user_module("up", self.UPGRADER, "2.0.0")
        (d / "fail").write_text("x")
        with self.assertLogs("invasor.modules", "ERROR"):
            self.assertFalse(self.manager.add("up")["loaded"])
        self.assertEqual(json.loads((self.data / "up" / "version.json").read_text()), {"version": "1.0.0"})
        (d / "fail").unlink()
        self.manager.add("up")
        self.assertEqual(self.upgrades(), ["1.0.0->up"])

    def test_disabled_module_upgrades_when_enabled(self):
        self.user_module("up", self.UPGRADER, "1.0.0")
        self.manager.add("up")
        self.manager.set_enabled("up", False)
        self.user_module("up", self.UPGRADER, "1.1.0")
        self.manager.add("up")
        self.assertEqual(self.upgrades(), [])
        self.manager.set_enabled("up", True)
        self.assertEqual(self.upgrades(), ["1.0.0->up"])

    UNINSTALLER = (
        "import pathlib\n"
        "from . import helpers\n"
        "here = pathlib.Path(__file__).parent\n"
        "events = []\n"
        "def setup(c):\n    events.append('setup')\n"
        "def teardown():\n    events.append('teardown')\n"
        "def uninstall(c, purge):\n"
        "    from . import late\n"  # its own files are still importable
        "    (here.parent / 'uninstalled').write_text(f'{c.id} {purge} {late.X} {events}')\n"
    )

    def uninstall_module(self, mid="gone"):
        d = self.user_module(mid, self.UNINSTALLER)
        (d / "helpers.py").write_text("")
        (d / "late.py").write_text("X = 1\n")
        self.manager.add(mid)
        (self.data / mid).mkdir(parents=True, exist_ok=True)
        (self.data / mid / "data.json").write_text("{}")
        return d.parent / "uninstalled"

    def test_uninstall_runs_after_teardown_and_keeps_data(self):
        import sys
        marker = self.uninstall_module()
        self.manager.remove("gone")
        self.assertEqual(marker.read_text(), "gone False 1 ['setup', 'teardown']")
        self.assertTrue((self.data / "gone" / "data.json").exists())
        self.assertNotIn("gone", self.manager.manifests)
        self.assertFalse(any(n.startswith("invasor_mod_gone") for n in sys.modules))

    def test_uninstall_of_a_disabled_module_imports_it_without_setup(self):
        marker = self.uninstall_module()
        self.manager.set_enabled("gone", False)
        self.manager.remove("gone", purge=True)
        self.assertEqual(marker.read_text(), "gone True 1 []")
        self.assertFalse((self.data / "gone").exists())

    def test_a_bad_uninstall_never_blocks(self):
        import invasor.modules as m
        patch(self, "invasor.modules.UNINSTALL_TIMEOUT", 0.2)
        cases = {
            "raises": "def uninstall(c, purge):\n    raise RuntimeError('no')\n",
            "exits": "import sys\ndef uninstall(c, purge):\n    sys.exit(1)\n",
            "hangs": "import threading\ndef uninstall(c, purge):\n    threading.Event().wait(5)\n",
        }
        for mid, backend in cases.items():
            with self.subTest(mid):
                self.user_module(mid, backend)
                self.manager.add(mid)
                with self.assertLogs("invasor.modules", "ERROR"):
                    self.manager.remove(mid, purge=True)
                self.assertNotIn(mid, self.manager.manifests)
        # Code that doesn't even import any more (disabled, then broken on disk).
        self.user_module("rotten", "METHODS = {}\n")
        self.manager.add("rotten")
        self.manager.set_enabled("rotten", False)
        (m.USER_MODULES_DIR / "rotten" / "backend.py").write_text("raise RuntimeError('rotten')\n")
        with self.assertLogs("invasor.modules", "ERROR"):
            self.manager.remove("rotten", purge=True)
        self.assertNotIn("rotten", self.manager.manifests)

    def test_uninstalling_invasor_runs_every_modules_uninstall(self):
        import logging
        from invasor.__main__ import uninstall_modules
        # A command-line entry point: its logging.basicConfig() would make every later test print its logs.
        root = logging.getLogger()
        self.addCleanup(lambda handlers=list(root.handlers), level=root.level: (
            setattr(root, "handlers", handlers), root.setLevel(level)))
        marker = self.uninstall_module()  # a user module; "good" and the rest are core ones
        self.add("coreclean", "import pathlib\ndef uninstall(c, purge):\n"
                 "    (pathlib.Path(c.path).parent / 'core-cleaned').write_text(str(purge))\n")
        with self.assertLogs("invasor", "INFO") as logs:
            uninstall_modules(purge=True)
        self.assertEqual(marker.read_text(), "gone True 1 []")  # imported without setup
        self.assertEqual((self.mods / "core-cleaned").read_text(), "True")
        self.assertFalse((self.data / "gone").exists())
        self.assertIn("coreclean", logs.output[-1])

    def steam_module(self, mid):
        """A user module that counts its on_steam_start calls in a file."""
        d = self.user_module(mid, (
            "import pathlib, threading\n"
            "here = pathlib.Path(__file__).parent\n"
            "lock = threading.Lock()\n"
            "def started():\n"
            "    with lock:\n"
            "        f = here / 'starts'\n"
            "        f.write_text(str(int(f.read_text() if f.exists() else 0) + 1))\n"
            "def setup(c):\n    c.on_steam_start(started)\n"
        ))
        self.manager.add(mid)
        return lambda: int((d / "starts").read_text()) if (d / "starts").exists() else 0

    def settle(self):
        import threading, time
        deadline = time.monotonic() + 2
        # "steam-started" (the manager's own thread) and the "steam-start-<id>" callbacks it starts.
        while any(t.name.startswith("steam-start") for t in threading.enumerate()) and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_on_steam_start_runs_on_every_new_steam(self):
        starts = self.steam_module("watcher")
        self.settle()
        self.assertEqual(starts(), 0)  # Steam not seen yet
        with self.assertLogs("invasor.modules", "INFO"):
            self.manager.steam_started("aaa")
            self.settle()
        self.assertEqual(starts(), 1)
        self.manager.steam_started("bbb")
        self.settle()
        self.assertEqual(starts(), 2)

    def test_on_steam_start_runs_at_once_if_steam_is_up(self):
        self.manager.steam_started("aaa")
        starts = self.steam_module("late")
        self.settle()
        self.assertEqual(starts(), 1)
        # Disabled: forgotten. Enabled again: registered afresh, and Steam is still up.
        self.manager.set_enabled("late", False)
        self.manager.steam_started("bbb")
        self.settle()
        self.assertEqual(starts(), 1)
        self.manager.set_enabled("late", True)
        self.settle()
        self.assertEqual(starts(), 2)

    def test_a_failing_steam_start_callback_is_logged(self):
        self.user_module("bad", "def setup(c):\n    c.on_steam_start(lambda: 1 / 0)\n")
        self.manager.add("bad")
        with self.assertLogs("invasor.modules", "ERROR"):
            self.manager.steam_started("aaa")
            self.settle()
        self.assertIn("bad", self.manager.registry)

    def test_reenabled_module_hears_each_change_once(self):
        """A disabled module's on_change callbacks go with it: enabled again, its setup()
        registers new ones and the old instance's are never called."""
        self.manager.set_enabled("good", False)
        self.manager.set_enabled("good", True)
        self.manager.settings["good"].set("level", 7, notify=True)
        self.assertEqual(self.manager.registry["good"]["events"](), [("level", 7)])
        self.assertEqual(len(self.manager.settings["good"]._listeners), 1)  # not the dead instance's too

    def test_failed_setup_leaves_no_listener(self):
        self.user_module("halfway", (
            "def setup(c):\n"
            "    c.settings.on_change(lambda k, v: None)\n"
            "    raise RuntimeError('setup failed after registering')\n"
        ), settings=SETTINGS)
        with self.assertLogs("invasor.modules", "ERROR"):
            self.manager.add("halfway")
        self.assertEqual(self.manager.settings["halfway"]._listeners, [])

    def test_a_hanging_teardown_doesnt_block(self):
        self.user_module("stuck", "import time\ndef teardown():\n    time.sleep(30)\n")
        self.manager.add("stuck")
        patch(self, "invasor.modules.TEARDOWN_TIMEOUT", 0.2)
        import time
        start = time.monotonic()
        with self.assertLogs("invasor.modules", "ERROR") as logs:
            self.manager.set_enabled("stuck", False)
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn("still running", logs.output[0])
        self.assertNotIn("stuck", self.manager.registry)

    def test_concurrent_changes_dont_break_the_listing(self):
        import threading
        errors = []

        def flip():
            try:
                for i in range(20):
                    self.manager.set_enabled("multi-file", i % 2 == 1)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def read():
            try:
                for _ in range(50):
                    self.manager.listing()
                    self.manager.ui_scripts()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=flip), threading.Thread(target=read), threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertIn("multi-file", self.manager.registry)

    def test_shutdown_tears_everything_down(self):
        events = self.manager.registry["good"]["events"]()
        self.manager.shutdown()
        self.assertEqual(events, ["teardown"])
        self.assertEqual(self.manager.registry, {})


if __name__ == "__main__":
    unittest.main()
