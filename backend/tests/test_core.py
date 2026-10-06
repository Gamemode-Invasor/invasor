import json
import unittest

from invasor import config, core
from invasor.context import GameContext
from invasor.gamepad import ComboWatcher
from invasor.modules import ModuleManager

from .helpers import patch, temp_dir


class Prefs(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        self.config_file = root / "config.json"
        patch(self, "invasor.config.CONFIG_FILE", self.config_file)
        patch(self, "invasor.modules.MODULES_DIR", root / "modules")
        patch(self, "invasor.modules.USER_MODULES_DIR", root / "user-modules")
        patch(self, "invasor.modules.DATA_DIR", root / "data")
        mod = root / "modules" / "mod"
        mod.mkdir(parents=True)
        (mod / "module.json").write_text(json.dumps({"api": 1, "name": "Mod", "version": "1", "settings": [
            {"key": "fps", "type": "slider", "label": "FPS", "min": 15, "max": 144, "step": 5, "default": 60},
        ]}))
        self.cfg = {"open_combo": ["L3", "R3"], "panel_side": "auto", "disabled_modules": []}
        self.watcher = ComboWatcher(self.cfg["open_combo"], lambda: None)
        manager = ModuleManager(self.cfg, GameContext())
        manager.discover()
        self.api = core.make_methods(manager, GameContext(), self.watcher, self.cfg)

    def test_defaults(self):
        self.assertEqual(self.api["prefs"](), {"open_combo": ["L3", "R3"], "panel_side": "auto", "accent_color": "blue", "handle_icon": "icon", "module_order": [], "update_check": True, "update_channel": "stable", "qam_visible_w": None})

    def test_combo_changes_live_and_persists(self):
        self.api["set_pref"]("open_combo", ["back", "START"])
        self.assertEqual(self.watcher.combo, {"BACK", "START"})
        self.assertEqual(json.loads(self.config_file.read_text())["open_combo"], ["BACK", "START"])

    def test_accent_color(self):
        self.assertEqual(self.api["set_pref"]("accent_color", "red")["accent_color"], "red")
        self.assertEqual(json.loads(self.config_file.read_text())["accent_color"], "red")
        for bad in ("pink", "black"):
            with self.assertRaises(ValueError):
                self.api["set_pref"]("accent_color", bad)

    def test_handle_icon(self):
        self.assertEqual(self.api["set_pref"]("handle_icon", "letter")["handle_icon"], "letter")
        self.assertEqual(json.loads(self.config_file.read_text())["handle_icon"], "letter")
        self.assertEqual(self.api["set_pref"]("handle_icon", "none")["handle_icon"], "none")
        for bad in ("image", "", None, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.api["set_pref"]("handle_icon", bad)

    def test_module_order(self):
        self.assertEqual(self.api["set_pref"]("module_order", ["b", "a", "b"])["module_order"], ["b", "a"])  # each id once
        self.assertEqual(json.loads(self.config_file.read_text())["module_order"], ["b", "a"])
        self.assertEqual(self.api["set_pref"]("module_order", [])["module_order"], [])
        for bad in ("a", None, [1], ["a", None], {"a": 1}, [str(i) for i in range(201)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.api["set_pref"]("module_order", bad)

    def test_update_check_pref(self):
        self.assertIs(self.api["set_pref"]("update_check", False)["update_check"], False)
        self.assertIs(json.loads(self.config_file.read_text())["update_check"], False)
        for bad in ("yes", 1, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.api["set_pref"]("update_check", bad)

    def test_update_channel_pref(self):
        self.assertEqual(self.api["set_pref"]("update_channel", "beta")["update_channel"], "beta")
        self.assertEqual(json.loads(self.config_file.read_text())["update_channel"], "beta")
        self.assertEqual(self.api["set_pref"]("update_channel", "stable")["update_channel"], "stable")
        for bad in ("alpha", "Beta", True, None, ["beta"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.api["set_pref"]("update_channel", bad)

    def test_update_status_is_empty_until_a_check(self):
        self.assertIsNone(self.api["update_status"]())

    def test_panel_side(self):
        self.assertEqual(self.api["set_pref"]("panel_side", "left")["panel_side"], "left")
        self.assertEqual(json.loads(self.config_file.read_text())["panel_side"], "left")

    def test_invalid_values_and_keys_are_rejected(self):
        for key, value in (("open_combo", ["L3", "NOPE"]), ("open_combo", []), ("open_combo", "L3"), ("panel_side", "up")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.api["set_pref"](key, value)
        with self.assertRaises(ValueError):
            self.api["set_pref"]("api_port", 1)
        self.assertFalse(self.config_file.exists())
        self.assertEqual(self.watcher.combo, {"L3", "R3"})

    def test_qam_width_is_learned_and_validated(self):
        self.api["set_qam_width"](348)
        self.assertEqual(self.api["prefs"]()["qam_visible_w"], 348)
        self.assertEqual(json.loads(self.config_file.read_text())["qam_visible_w"], 348)
        for bad in (0, 50, 99999, "x"):
            with self.subTest(width=bad), self.assertRaises(ValueError):
                self.api["set_qam_width"](bad)
        self.assertEqual(self.api["prefs"]()["qam_visible_w"], 348)

    def test_qam_shown_hides_the_library_handle(self):
        import asyncio

        class Inj:
            def __init__(self):
                self.calls = []

            async def set_main_handle_hidden(self, hidden):
                self.calls.append(hidden)

        inj = Inj()
        api = core.make_methods(ModuleManager(self.cfg, GameContext()), GameContext(), self.watcher, self.cfg, injector=inj)
        self.assertTrue(asyncio.run(api["set_qam_shown"](True)))
        asyncio.run(api["set_qam_shown"](False))
        self.assertEqual(inj.calls, [True, False])
        for bad in (1, "true", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                asyncio.run(api["set_qam_shown"](bad))
        self.assertEqual(inj.calls, [True, False])

    def test_manage_invasor_commands(self):
        from unittest import mock

        from invasor.schema import Unavailable

        ok = mock.Mock(returncode=0, stdout="line 1\nline 2\n", stderr="")
        with mock.patch("invasor.updater.subprocess.run", return_value=ok) as run:
            self.assertTrue(self.api["restart_service"]())
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[:5], ["systemd-run", "--user", "--collect", "--quiet", "--unit=invasor-restart"])
            self.assertEqual(cmd[5:], ["systemctl", "--user", "restart", "invasor.service"])
        with mock.patch("invasor.updater.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(Unavailable):
                self.api["restart_service"]()

        # reset_config forgets config.json and restarts the service
        self.config_file.write_text("{}")
        with mock.patch("invasor.updater.subprocess.run", return_value=ok) as run:
            self.api["reset_config"]()
            self.assertFalse(self.config_file.exists())
            self.assertIn("--unit=invasor-restart", run.call_args[0][0])
        self.api["reset_config"]()  # nothing to forget is fine

        with mock.patch("invasor.core.subprocess.run", return_value=ok) as run:
            self.assertEqual(self.api["log_tail"](5), "line 1\nline 2\n")
            self.assertEqual(run.call_args[0][0][:7], ["journalctl", "--user", "-u", "invasor.service", "-n", "5", "--no-pager"])
        for bad in (0, 1001, "5", True, None):
            with self.subTest(lines=bad), self.assertRaises(ValueError):
                self.api["log_tail"](bad)
        with mock.patch("invasor.core.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(Unavailable):
                self.api["log_tail"]()

    def test_uninstall_invasor(self):
        from unittest import mock

        from invasor.schema import Unavailable

        patch(self, "invasor.config.DATA_DIR", temp_dir(self) / "invasor")  # never the real install
        script = config.DATA_DIR / "invasor-installation.sh"
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with self.assertRaises(Unavailable):  # an install made before the installer was kept
            self.api["uninstall_invasor"]()
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/bash\n")
        with mock.patch("invasor.updater.subprocess.run", return_value=ok) as run:
            self.api["uninstall_invasor"]()
            cmd = run.call_args[0][0]
            self.assertIn("--unit=invasor-uninstall", cmd)
            self.assertEqual(cmd[-2:], [str(script), "--uninstall"])
            self.api["uninstall_invasor"](True)
            self.assertEqual(run.call_args[0][0][-3:], [str(script), "--uninstall", "--purge"])
        for bad in (1, "yes", None):
            with self.subTest(purge=bad), self.assertRaises(ValueError):
                self.api["uninstall_invasor"](bad)

    def test_restart_steam_asks_steam_not_the_system(self):
        import asyncio
        from unittest import mock

        from invasor.schema import Unavailable

        steam = mock.Mock()
        steam.call = mock.AsyncMock(return_value=None)
        api = core.make_methods(ModuleManager(self.cfg, GameContext()), GameContext(), self.watcher, self.cfg, steam=steam)
        self.assertTrue(asyncio.run(api["restart_steam"]()))
        steam.call.assert_awaited_once_with("User.StartRestart", [False])
        steam.call = mock.AsyncMock(side_effect=Unavailable("SteamClient.User.StartRestart isn't available"))
        with self.assertRaises(Unavailable):
            asyncio.run(api["restart_steam"]())
        with self.assertRaises(Unavailable):  # no bridge at all
            asyncio.run(self.api["restart_steam"]())

    def test_a_shipped_module_is_uninstalled_through_the_api(self):
        import asyncio
        manager = ModuleManager(self.cfg, GameContext())
        manager.discover()
        api = core.make_methods(manager, GameContext(), self.watcher, self.cfg)
        self.assertTrue(api["module_uninstall"]("mod"))
        self.assertNotIn("mod", [m["id"] for m in api["modules"]()])
        self.assertEqual(api["module_removed"](), [{"id": "mod", "name": "Mod"}])
        with self.assertRaises(ValueError):
            api["module_uninstall"]("ghost")
        entry = asyncio.run(api["module_restore"]("mod"))
        self.assertEqual(entry["id"], "mod")
        self.assertEqual(api["module_removed"](), [])

    def test_module_settings_are_per_key_and_validated(self):
        self.assertEqual(self.api["settings_get"]("mod"), {"fps": 60})
        self.assertEqual(self.api["settings_set"]("mod", "fps", 151), 140)
        self.assertEqual(self.api["settings_get"]("mod"), {"fps": 140})
        with self.assertRaises(ValueError):
            self.api["settings_set"]("mod", "fps", "fast")
        with self.assertRaises(ValueError):
            self.api["settings_set"]("mod", "other", 1)
        with self.assertRaises(ValueError):
            self.api["settings_get"]("ghost")

    def test_unknown_module_is_a_caller_error(self):
        from invasor.schema import InvalidArgument
        with self.assertRaises(InvalidArgument):
            self.api["set_enabled"]("ghost", True)
        with self.assertRaises(InvalidArgument):
            self.api["set_qam_width"](True)

    def test_pads_lists_devices(self):
        self.assertIsInstance(self.api["pads"](), list)



class Highlighted(unittest.IsolatedAsyncioTestCase):
    """core.game: the tile highlighted in the library, asked of the main window."""

    def setUp(self):
        patch(self, "invasor.context._shortcut_names",
              lambda: {"3000000001": "Some Shortcut", "11": "Twin", "12": "Twin"})
        patch(self, "invasor.context._app_name", lambda appid: {"2100": "Dark Messiah"}.get(appid))
        self.tiles = []
        self.roles = []
        test = self

        class FakeInjector:
            async def evaluate_all(self, expression, timeout=2, role=None):
                test.roles.append(role)
                return test.tiles

        cfg = {"open_combo": ["L3", "R3"], "disabled_modules": []}
        self.api = core.make_methods(ModuleManager(cfg, GameContext()), GameContext(),
                                     ComboWatcher(cfg["open_combo"], lambda: None), cfg, FakeInjector())

    async def highlighted(self, *tiles):
        self.tiles = list(tiles)
        return (await self.api["game"]())["highlighted"]

    async def test_by_appid_from_the_artwork(self):
        self.assertEqual(await self.highlighted(None, {"appid": "2100"}),
                         {"appid": "2100", "name": "Dark Messiah", "shortcut": False})
        self.assertEqual(self.roles, ["main"])  # only the main window is asked

    async def test_shortcut_without_art_by_its_name(self):
        self.assertEqual(await self.highlighted({"name": "Some Shortcut"}),
                         {"appid": "3000000001", "name": "Some Shortcut", "shortcut": True})

    async def test_unknown_or_ambiguous_is_none(self):
        for tile in ({"name": "Twin"}, {"name": "Nope"}, {"appid": "../1"}, {"appid": 5}, "x", {}):
            with self.subTest(tile=tile):
                self.assertIsNone(await self.highlighted(tile))
        self.assertIsNone(await self.highlighted())  # no main window answered

    async def test_steam_call_needs_the_bridge_and_a_list(self):
        from invasor.schema import InvalidArgument, Unavailable
        with self.assertRaises(Unavailable):
            await self.api["steam_call"]("Apps.X", [])
        with self.assertRaises(InvalidArgument):
            await core.make_methods(None, GameContext(), None, {}, None, object())["steam_call"]("Apps.X", "notalist")

    async def test_running_and_selected_still_there(self):
        state = await self.api["game"]()
        self.assertEqual(set(state), {"running", "selected", "highlighted"})


class InstallRollback(unittest.IsolatedAsyncioTestCase):
    """A replace that fails after the old version was unloaded loads the old one again."""

    async def test_old_version_is_loaded_again(self):
        root = temp_dir(self)
        user = root / "user-modules"
        patch(self, "invasor.config.CONFIG_FILE", root / "config.json")
        patch(self, "invasor.config.USER_MODULES_DIR", user)
        patch(self, "invasor.modules.MODULES_DIR", root / "modules")
        patch(self, "invasor.modules.USER_MODULES_DIR", user)
        patch(self, "invasor.modules.DATA_DIR", root / "data")
        (user / "hello").mkdir(parents=True)
        (user / "hello" / "module.json").write_text(json.dumps({"api": 1, "name": "Hello", "version": "1"}))
        (user / "hello" / "backend.py").write_text("METHODS = {'hi': lambda: 'old'}\n")
        cfg = {"open_combo": ["L3", "R3"], "disabled_modules": []}
        manager = ModuleManager(cfg, GameContext())
        manager.discover()

        def failing_install(path, reserved, replace, before_replace):
            before_replace("hello")
            raise OSError("disk full")

        patch(self, "invasor.install.install", failing_install)
        api = core.make_methods(manager, GameContext(), ComboWatcher(cfg["open_combo"], lambda: None), cfg)
        with self.assertRaisesRegex(OSError, "disk full"):
            await api["module_install"]("Downloads/hello.zip", replace=True)
        self.assertEqual(manager.registry["hello"]["hi"](), "old")


if __name__ == "__main__":
    unittest.main()
