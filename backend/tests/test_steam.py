import asyncio
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
