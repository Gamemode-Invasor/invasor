import unittest

from invasor import config
from invasor.injector import Injector

# Real titles/URLs seen in the service log on 2026-10-01 (Spanish Steam, Legion Go).
CASES = [
    # Desktop client window: deliberately not injected (invasor is gamepad-UI only).
    ("Steam", "about:blank?createflags=18&minwidth=1010&browserType=4&useragent=Valve%20Steam%20Client", None),
    ("Modo Big\xa0Picture de Steam", "about:blank?createflags=6292738&browserType=4&useragent=Valve%20Steam%20Gamepad", "main"),
    ("QuickAccess_uid2", "about:blank?browserviewpopup=1&requestid=2&parentpopup=2", "quickaccess"),
    ("MainMenu_uid2", "about:blank?browserviewpopup=1&requestid=1&parentpopup=2", None),
    ("notificationtoasts_1_desktop", "about:blank?browserType=4", None),
    # Big Picture opened from the desktop: same gamepad UI as Game Mode, so it counts.
    ("Steam Big Picture Mode", "about:blank?createflags=6292738&browserType=4&useragent=Valve%20Steam%20Gamepad", "main"),
    # Same window seen with browserType=3 (Game Mode on the Legion Go, 2026-10-01).
    ("Modo Big\xa0Picture de Steam", "about:blank?createflags=6292754&minwidth=853&minheight=534&pid=0&browser=-1&browserType=3&useragent=Valve%20Steam%20Gamepad", "main"),
    ("notificationtoasts_uid16", "about:blank?browserviewpopup=1&requestid=5&parentpopup=16&createflags=512", None),
    ("Steam Root Menu", "about:blank?createflags=4538378&pid=0&browser=-1", None),
    ("Iniciar sesión en Steam", "about:blank?createflags=4098&pid=0&browser=-1", None),
    ("SharedJSContext", "https://steamloopback.host/routes/library/home", None),
]


class TargetRoles(unittest.TestCase):
    def setUp(self):
        self.injector = Injector(dict(config.DEFAULTS))

    def test_real_windows(self):
        for title, url, expected in CASES:
            with self.subTest(title=title):
                target = {"type": "page", "webSocketDebuggerUrl": "ws://x", "title": title, "url": url}
                self.assertEqual(self.injector._role(target), expected)

    def test_url_contains_accepts_a_single_string(self):
        injector = Injector({"targets": [{"role": "x", "url_contains": "foo"}]})
        self.assertEqual(injector._role({"type": "page", "webSocketDebuggerUrl": "ws://x", "title": "", "url": "a?foo"}), "x")

    def test_non_pages_and_unreachable_are_ignored(self):
        self.assertIsNone(self.injector._role({"type": "service_worker", "webSocketDebuggerUrl": "ws://x", "title": "Steam", "url": "browserType=4"}))
        self.assertIsNone(self.injector._role({"type": "page", "title": "Steam", "url": "browserType=4"}))



class ModulePayload(unittest.TestCase):
    def test_module_ui_is_wrapped_and_registered(self):
        js = Injector._module_payload('we"ird', "var __invasorModule = {default: 1};")
        self.assertTrue(js.startswith("(function () {"))
        self.assertIn('__invasorKit.register("we\\"ird", ', js)
        self.assertIn("typeof __invasorModule === 'undefined'", js)

    def test_core_payload_carries_version(self):
        import json as _json
        from invasor import __version__
        inj = Injector({"bundle": __file__, "api_port": 1, "token": "t", "targets": []})
        cfg = _json.loads(inj._payload("main").split(";\n", 1)[0].split("= ", 1)[1])
        self.assertEqual(cfg["version"], __version__)



class OddTargets(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_windows_never_break_a_round(self):
        from unittest import mock
        odd = [
            {"id": "a", "type": "page", "webSocketDebuggerUrl": "ws://x", "url": None, "title": None},
            {"id": "b", "type": "page", "webSocketDebuggerUrl": "ws://x", "url": 5, "title": 7},
        ]
        inj = Injector({"targets": config.DEFAULTS["targets"]})
        self.assertIsNone(inj._role(odd[0]))
        self.assertIsNone(inj._role(odd[1]))
        self.assertFalse(Injector._is_main_window(odd[0]))
        with mock.patch("invasor.cef.list_targets", mock.AsyncMock(return_value=odd)), \
                mock.patch("invasor.cef.browser_id", mock.AsyncMock(return_value=None)):
            await inj._round()  # must not raise

    async def test_list_targets_drops_garbage(self):
        from unittest import mock
        from invasor import cef
        with mock.patch("invasor.cef._list_targets_sync", return_value={"not": "a list"}):
            self.assertEqual(await cef.list_targets(), [])
        with mock.patch("invasor.cef._list_targets_sync", return_value=[1, {"x": 1}, {"id": "ok"}]):
            self.assertEqual(await cef.list_targets(), [{"id": "ok"}])



class SteamStart(unittest.IsolatedAsyncioTestCase):
    async def test_once_per_steam_instance(self):
        from unittest import mock
        seen = []
        inj = Injector({"targets": []}, on_steam_start=seen.append)
        # Steam up, still up, a blip (unreachable), the same Steam, then restarted.
        ids = ["aaa", "aaa", None, "aaa", "bbb", None]
        with mock.patch("invasor.cef.browser_id", mock.AsyncMock(side_effect=ids)), \
                mock.patch("invasor.cef.list_targets", mock.AsyncMock(return_value=[])):
            for _ in ids:
                await inj._round()
        self.assertEqual(seen, ["aaa", "bbb"])

    async def test_a_failing_handler_doesnt_break_the_round(self):
        from unittest import mock
        def boom(_):
            raise RuntimeError("boom")
        inj = Injector({"targets": []}, on_steam_start=boom)
        with mock.patch("invasor.cef.browser_id", mock.AsyncMock(return_value="aaa")), \
                mock.patch("invasor.cef.list_targets", mock.AsyncMock(return_value=[])), \
                self.assertLogs("invasor.injector", "ERROR"):
            await inj._round()

    async def test_browser_id_reads_the_guid(self):
        from unittest import mock
        from invasor import cef
        info = {"webSocketDebuggerUrl": "ws://localhost:8080/devtools/browser/7eb45db9-48b1-4bef"}
        with mock.patch("invasor.cef._version_sync", return_value=info):
            self.assertEqual(await cef.browser_id(), "7eb45db9-48b1-4bef")
        for bad in ({}, [], {"webSocketDebuggerUrl": 5}, {"webSocketDebuggerUrl": "ws://x/devtools/page/1"}):
            with mock.patch("invasor.cef._version_sync", return_value=bad):
                self.assertIsNone(await cef.browser_id())
        with mock.patch("invasor.cef._version_sync", side_effect=OSError("refused")):
            self.assertIsNone(await cef.browser_id())


class EvaluateByRole(unittest.IsolatedAsyncioTestCase):
    async def test_only_windows_of_the_role_are_asked(self):
        class Fake:
            def __init__(self, value):
                self.value = value

            async def evaluate(self, expression):
                return self.value

        inj = Injector({"targets": []})
        inj._live = {"a": (Fake("main"), "main"), "b": (Fake("qam"), "quickaccess")}
        self.assertEqual(await inj.evaluate_all("x", role="main"), ["main"])
        self.assertEqual(sorted(await inj.evaluate_all("x")), ["main", "qam"])


class RemoveOverlays(unittest.IsolatedAsyncioTestCase):
    async def test_every_page_is_cleaned_and_counted(self):
        from unittest import mock
        from invasor import injector

        asked = []

        class Fake:
            def __init__(self, url):
                self.url = url

            async def evaluate(self, expression):
                asked.append(self.url)
                if self.url == "ws://broken":
                    raise RuntimeError("window went away")
                return self.url == "ws://with-overlay"

            async def close(self):
                pass

        targets = [
            {"id": "1", "type": "page", "webSocketDebuggerUrl": "ws://with-overlay"},
            {"id": "2", "type": "page", "webSocketDebuggerUrl": "ws://without"},
            {"id": "3", "type": "page", "webSocketDebuggerUrl": "ws://broken"},
            {"id": "4", "type": "service_worker", "webSocketDebuggerUrl": "ws://worker"},
            {"id": "5", "type": "page"},
        ]
        connect = mock.AsyncMock(side_effect=lambda url: Fake(url))
        with mock.patch("invasor.cef.list_targets", mock.AsyncMock(return_value=targets)), \
                mock.patch("invasor.cef.CDPSession.connect", connect):
            self.assertEqual(await injector.remove_overlays(), 1)
        self.assertEqual(sorted(asked), ["ws://broken", "ws://with-overlay", "ws://without"])

    async def test_no_steam_is_nothing_to_do(self):
        from unittest import mock
        from invasor import injector
        with mock.patch("invasor.cef.list_targets", mock.AsyncMock(return_value=[])):
            self.assertEqual(await injector.remove_overlays(), 0)


if __name__ == "__main__":
    unittest.main()
