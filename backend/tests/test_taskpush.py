import asyncio
import json
import threading
import unittest

from invasor.taskpush import TaskPusher


class Pusher(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.sleeps = []
        self.state = {"active": [], "recent": []}
        self.fail_next = False

        async def send(js):
            if self.fail_next:
                self.fail_next = False
                raise OSError("window gone")
            self.sent.append(js)

        async def sleep(seconds):
            self.sleeps.append(seconds)
            await asyncio.sleep(0)

        self.pusher = TaskPusher(lambda: self.state, send, asyncio.get_running_loop(), min_gap=1.0, sleep=sleep)

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(0)

    def pushed(self):
        """The snapshots the windows were told, decoded from the JS that was sent."""
        out = []
        for js in self.sent:
            assert js.startswith("window.__invasor?.tasks?.(") and js.endswith(")")
            out.append(json.loads(js[len("window.__invasor?.tasks?.("):-1]))
        return out

    async def test_a_change_is_pushed_with_the_whole_snapshot(self):
        self.state = {"active": [{"id": 1, "label": "x"}], "recent": []}
        self.pusher.notify()
        await self.settle()
        self.assertEqual(self.pushed(), [self.state])

    async def test_a_burst_is_one_push_with_the_last_state(self):
        for i in range(5000):
            self.state = {"active": [{"id": 1, "fraction": i / 5000}], "recent": []}
            self.pusher.notify()
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.pushed()[0]["active"][0]["fraction"], 4999 / 5000)

    async def test_what_changes_while_it_waits_goes_in_the_next_push(self):
        gate = asyncio.Event()

        async def sleep(seconds):
            self.sleeps.append(seconds)
            await gate.wait()

        self.pusher._sleep = sleep
        self.state = {"active": [{"id": 1}], "recent": []}
        self.pusher.notify()
        await self.settle()
        self.state = {"active": [], "recent": [{"id": 1}]}
        self.pusher.notify()
        self.pusher.notify()
        await self.settle()
        self.assertEqual(len(self.sent), 1)  # still waiting its gap
        gate.set()
        await self.settle()
        self.assertEqual(len(self.pushed()), 2)
        self.assertEqual(self.pushed()[1], self.state)
        self.assertEqual(self.sleeps[0], 1.0)

    async def test_it_goes_idle_and_wakes_again(self):
        self.pusher.notify()
        await self.settle()
        self.assertFalse(self.pusher._running)
        self.pusher.notify()
        await self.settle()
        self.assertEqual(len(self.sent), 2)

    async def test_a_failing_push_is_logged_and_the_next_one_works(self):
        self.fail_next = True
        with self.assertLogs("invasor.taskpush", "ERROR"):
            self.pusher.notify()
            await self.settle()
        self.pusher.notify()
        await self.settle()
        self.assertEqual(len(self.sent), 1)

    async def test_the_json_survives_quotes_newlines_and_emoji(self):
        label = 'Say "hi"\n  </script> \U0001F680 \\ end'
        self.state = {"active": [{"id": 1, "label": label}], "recent": []}
        self.pusher.notify()
        await self.settle()
        self.assertEqual(self.pushed()[0]["active"][0]["label"], label)
        self.assertNotIn("\n", self.sent[0])  # one line of JS

    async def test_notify_from_other_threads(self):
        threads = [threading.Thread(target=lambda: [self.pusher.notify() for _ in range(200)]) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        await self.settle()
        await asyncio.sleep(0.05)
        self.assertGreaterEqual(len(self.sent), 1)
        self.assertLessEqual(len(self.sent), 3)  # coalesced, not 1600
        self.assertFalse(self.pusher._running)

    async def test_stop_ends_it_and_later_changes_do_nothing(self):
        gate = asyncio.Event()

        async def sleep(seconds):
            await gate.wait()

        self.pusher._sleep = sleep
        self.pusher.notify()
        await self.settle()
        self.pusher.stop()
        await self.settle()
        self.pusher.notify()
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.pusher._running)

    async def test_notify_with_the_loop_gone_does_not_raise(self):
        loop = asyncio.new_event_loop()
        loop.close()
        pusher = TaskPusher(lambda: {}, lambda js: None, loop)
        pusher.notify()
        self.assertFalse(pusher._running)


if __name__ == "__main__":
    unittest.main()
