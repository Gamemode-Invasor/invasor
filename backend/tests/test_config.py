import json
import unittest

from invasor import config
from invasor.injector import Injector

from .helpers import patch, temp_dir

DESKTOP = {"type": "page", "webSocketDebuggerUrl": "ws://x", "title": "Steam",
           "url": "about:blank?createflags=18&browserType=4&useragent=Valve%20Steam%20Client"}
GAMEMODE = {"type": "page", "webSocketDebuggerUrl": "ws://x", "title": "Modo Big\xa0Picture de Steam",
            "url": "about:blank?browserType=4&useragent=Valve%20Steam%20Gamepad"}


class DevDesktop(unittest.TestCase):
    def setUp(self):
        self.file = temp_dir(self) / "config.json"
        patch(self, "invasor.config.CONFIG_FILE", self.file)

    def load(self, **user):
        if user:
            self.file.write_text(json.dumps(user))
        return config.load()

    def test_off_by_default_desktop_is_ignored(self):
        cfg = self.load()
        self.assertFalse(cfg["dev_desktop"])
        self.assertIsNone(Injector(cfg)._role(DESKTOP))
        self.assertEqual(Injector(cfg)._role(GAMEMODE), "main")

    def test_on_desktop_gets_main_role(self):
        with self.assertLogs("invasor.config", "WARNING"):
            cfg = self.load(dev_desktop=True)
        self.assertEqual(Injector(cfg)._role(DESKTOP), "main")
        self.assertEqual(Injector(cfg)._role(GAMEMODE), "main")

    def test_defaults_are_never_mutated(self):
        before = len(config.DEFAULTS["targets"])
        with self.assertLogs("invasor.config", "WARNING"):
            self.load(dev_desktop=True)
        self.assertEqual(len(config.DEFAULTS["targets"]), before)
        self.assertIsNone(Injector(self.load(dev_desktop=False))._role(DESKTOP))

    def test_bad_values_fall_back_to_defaults(self):
        with self.assertLogs("invasor.config", "WARNING") as logs:
            cfg = self.load(api_port="x", panel_side="up", open_combo="L3", dev_desktop="yes",
                            disabled_modules=[1], log_level="LOUD", qam_visible_w=5, accent_color="pink", handle_icon="image", update_channel="nightly", nope=1)
        for key in ("accent_color", "handle_icon", "api_port", "panel_side", "open_combo", "dev_desktop", "disabled_modules", "log_level", "qam_visible_w", "update_channel"):
            self.assertEqual(cfg[key], config.DEFAULTS[key], key)
        self.assertNotIn("nope", cfg)
        self.assertTrue(any("unknown key 'nope'" in line for line in logs.output))

    def test_malformed_targets_fall_back_to_defaults(self):
        for bad in ([{"role": "main", "url_contains": 5}], [{"role": "main", "title_prefix": None}],
                    [{"url_contains": "x"}], [{"role": "main", "typo": 1}], ["main"]):
            with self.subTest(targets=bad), self.assertLogs("invasor.config", "WARNING"):
                self.assertEqual(self.load(targets=bad)["targets"], config.DEFAULTS["targets"])
        ok = [{"role": "main", "url_contains": "x", "title_not_prefix": "y"}]
        self.assertEqual(self.load(targets=ok)["targets"], ok)

    def test_good_values_are_kept(self):
        cfg = self.load(api_port=40000, panel_side="left", open_combo=["L4", "R4"], qam_visible_w=348, handle_icon="none")
        self.assertEqual((cfg["api_port"], cfg["panel_side"], cfg["open_combo"], cfg["qam_visible_w"], cfg["handle_icon"]),
                         (40000, "left", ["L4", "R4"], 348, "none"))

    def test_not_an_object(self):
        self.file.write_text("[1, 2]")
        with self.assertLogs("invasor.config", "ERROR"):
            cfg = config.load()
        self.assertEqual(cfg["api_port"], config.DEFAULTS["api_port"])

    def test_main_window_detection_for_cleanup(self):
        self.assertTrue(Injector._is_main_window(DESKTOP))
        self.assertFalse(Injector._is_main_window({**DESKTOP, "url": "about:blank?browserviewpopup=1"}))
        self.assertTrue(Injector._is_main_window({**GAMEMODE, "url": "about:blank?browserType=3&useragent=Valve%20Steam%20Gamepad"}))


if __name__ == "__main__":
    unittest.main()
