import asyncio
import shutil
import subprocess
import json
import unittest

from invasor.schema import InvalidArgument, Unavailable
from invasor import steam
from invasor.steam import SteamBridge


class Bridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.answer = {"value": 42}

        async def evaluate(expression, timeout):
            self.calls.append(expression)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        self.bridge = SteamBridge(evaluate)
        self.bridge.loop = asyncio.get_running_loop()

    async def test_calls_the_path_with_json_args(self):
        self.assertEqual(await self.bridge.call("Apps.SetShortcutName", [123, "Name"]), 42)
        self.assertIn('"Apps.SetShortcutName"', self.calls[0])
        self.assertIn(json.dumps([123, "Name"]), self.calls[0])

    async def test_bad_paths_and_args_are_rejected_before_steam(self):
        for path in ("apps.X", "Apps", "Apps.__proto__", "Apps.constructor", "Apps.x;alert(1)", 5, "A.b.c.d.e.f.g"):
            with self.subTest(path=path), self.assertRaises(InvalidArgument):
                await self.bridge.call(path, [])
        with self.assertRaises(InvalidArgument):
            await self.bridge.call("Apps.X", [object()])
        self.assertEqual(self.calls, [])

    async def test_missing_or_failing_steam_is_unavailable(self):
        self.answer = {"missing": True}
        with self.assertRaises(Unavailable):
            await self.bridge.call("Apps.Gone", [])
        self.answer = RuntimeError("JS threw")
        with self.assertRaises(Unavailable):
            await self.bridge.call("Apps.Throws", [])
        self.answer = None
        with self.assertRaises(Unavailable):
            await self.bridge.call("Apps.Odd", [])

    async def test_sync_calls_from_threads_but_not_from_the_loop(self):
        self.assertEqual(await asyncio.to_thread(self.bridge.call_sync, "Apps.X", 1), 42)
        with self.assertRaises(RuntimeError):
            self.bridge.call_sync("Apps.X")

    async def test_a_sync_call_steam_never_answers_is_unavailable(self):
        """Modules' plan B catches Unavailable: a timeout must be one, not a bare TimeoutError."""
        async def hang(expression, timeout):
            await asyncio.sleep(60)

        self.bridge._evaluate = hang
        with self.assertRaisesRegex(Unavailable, "didn't answer"):
            await asyncio.to_thread(self.bridge.call_sync, "Apps.X", timeout=-4.9)
        with self.assertRaisesRegex(Unavailable, "didn't answer"):
            await asyncio.to_thread(self.bridge.notify_sync, "Title", timeout=-4.9)



class Notify(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls, self.answer = [], {"value": True}

        async def evaluate(expression, timeout):
            self.calls.append(expression)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        self.bridge = SteamBridge(evaluate)
        self.bridge.loop = asyncio.get_running_loop()

    async def test_sends_an_achievement_message(self):
        self.assertTrue(await self.bridge.notify(" Hello ", "World", "https://example.com/i.png"))
        js = self.calls[0]
        self.assertIn("OnNotification(id, 5, new Uint8Array(", js)
        msg = bytes(json.loads(js.split("new Uint8Array(")[1].split("))")[0]))
        self.assertEqual(msg, steam.achievement_message("Hello", "World", "https://example.com/i.png"))
        self.assertIn(b"\x1a\x05Hello", msg)  # field 3 (name), length 5
        self.assertIn(b"\x22\x05World", msg)  # field 4 (description)

    async def test_sound_is_a_steam_sound_number(self):
        await self.bridge.notify("t", sound="message")
        self.assertIn("const sound = 4;", self.calls[0])
        await self.bridge.notify("t", sound="none")
        self.assertIn("const sound = 0;", self.calls[1])
        for name in ("", ):
            await self.bridge.notify("t", sound=name)  # Steam decides
            self.assertIn("const sound = null;", self.calls[-1])
        for bad in ("../x.wav", "deck_ui_toast.wav", "nope"):
            with self.subTest(bad=bad), self.assertRaises(InvalidArgument):
                await self.bridge.notify("t", sound=bad)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_script_sets_the_sound_of_the_toast_in_a_store_like_steam_s(self):
        """Runs NOTIFY_JS against a stand-in NotificationStore whose OnNotification is read-only
        (a MobX action in Steam) and whose toast plays its sound later, through
        PlayNotificationSound, from the type's configuration."""
        harness = r"""
        const vm = require('vm');
        class Store {
          m_nNextTestNotificationID = 7;
          played = [];
          queue = [];
          ChooseSound(info, n) { return info.playSound ? info.sound : null; }
          PlayNotificationSound(n) { const s = this.ChooseSound({sound: 5, playSound: true}, n); if (s !== null) this.played.push(s); }
        }
        Object.defineProperty(Store.prototype, 'OnNotification', {value(id) { this.queue.push({notificationID: id}); }});
        Object.defineProperty(Store.prototype, 'ProcessNotification', {value() {}});
        const store = new Store();
        const win = {NotificationStore: store};
        const run = sound => vm.runInNewContext(SCRIPT.replace('SOUND', sound), {window: win, Date, Object});
        const toastShows = () => store.PlayNotificationSound(store.queue.shift());
        const out = {};
        for (const [name, sound] of [['plain', 'null'], ['message', '4'], ['none', '0'], ['again', '3']]) {
          out[name] = run(sound); toastShows();
        }
        out.played = store.played;
        out.patched = store.__invasorSound;
        out.ownChoose = Object.prototype.hasOwnProperty.call(store, 'ChooseSound');
        console.log(JSON.stringify(out));
        """
        script = steam.NOTIFY_JS % {"type": 5, "bytes": "[1]", "sound": "SOUND"}
        res = subprocess.run(["node", "-e", f"const SCRIPT = {json.dumps(script)};" + harness],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["played"], [5, 4, 3])  # Steam's own, "message", (none: silent), "again"
        self.assertEqual(out["patched"], 2)
        self.assertFalse(out["ownChoose"])
        self.assertTrue(all(out[k] == {"value": True} for k in ("plain", "message", "none", "again")))

    async def test_bad_input_never_reaches_steam(self):
        for args in (("",), ("x" * 65,), ("t", "x" * 257), ("t\x00",), ("t", "b", "http://x/i.png"),
                     ("t", "b", "javascript:alert(1)"), ("t", "b", "data:text/html;base64,AAAA"), (5,)):
            with self.subTest(args=args), self.assertRaises(InvalidArgument):
                await self.bridge.notify(*args)
        self.assertEqual(self.calls, [])
        await self.bridge.notify("t", "line one\nline two", "data:image/png;base64,iVBORw0KGgo=")

    async def test_missing_or_failing_steam_is_unavailable(self):
        for answer in ({"missing": True}, RuntimeError("JS threw"), None):
            self.answer = answer
            with self.subTest(answer=answer), self.assertRaises(Unavailable):
                await self.bridge.notify("t")

    async def test_sync_variant(self):
        self.assertTrue(await asyncio.to_thread(self.bridge.notify_sync, "t"))
        with self.assertRaises(RuntimeError):
            self.bridge.notify_sync("t")


if __name__ == "__main__":
    unittest.main()
