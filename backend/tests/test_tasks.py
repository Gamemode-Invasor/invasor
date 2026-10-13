import asyncio
import threading
import unittest

from invasor.schema import InvalidArgument, Unavailable
from invasor.tasks import MAX_LABEL, MAX_TEXT, TaskRegistry


class Registry(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.events = []
        self.registry = TaskRegistry(on_event=lambda name, payload: self.events.append((name, payload)),
                                     clock=lambda: self.now, max_active=3, max_recent=2)

    def test_a_task_runs_and_ends(self):
        task = self.registry.start("rakun", "Installing")
        self.assertEqual(self.registry.snapshot(), {"recent": [], "active": [{
            "id": 1, "module": "rakun", "label": "Installing", "state": "running", "fraction": None,
            "text": "", "started": 100.0, "updated": 100.0, "finished": None}]})
        self.now = 101.0
        task.update(0.5, "halfway")
        view = self.registry.snapshot()["active"][0]
        self.assertEqual((view["fraction"], view["text"], view["updated"]), (0.5, "halfway", 101.0))
        self.now = 102.0
        task.done("all set")
        snap = self.registry.snapshot()
        self.assertEqual(snap["active"], [])
        self.assertEqual(snap["recent"], [{
            "id": 1, "module": "rakun", "label": "Installing", "state": "done", "fraction": 1.0,
            "text": "all set", "started": 100.0, "updated": 102.0, "finished": 102.0}])

    def test_a_failed_task_keeps_where_it_got_to(self):
        task = self.registry.start("core", "Updating")
        task.update(0.3)
        task.fail("no network")
        done = self.registry.snapshot()["recent"][0]
        self.assertEqual((done["state"], done["fraction"], done["text"]), ("failed", 0.3, "no network"))

    def test_update_replaces_both_values(self):
        task = self.registry.start("m", "x")
        task.update(0.4, "a")
        task.update(text="only text")
        view = self.registry.snapshot()["active"][0]
        self.assertEqual((view["fraction"], view["text"]), (None, "only text"))

    def test_after_the_end_nothing_changes(self):
        task = self.registry.start("m", "x")
        task.fail("first")
        task.update(0.9, "late")
        task.done("late")
        task.fail("late")
        self.assertEqual(len(self.registry.snapshot()["recent"]), 1)
        self.assertEqual(self.registry.snapshot()["recent"][0]["text"], "first")
        self.assertEqual([p["state"] for _, p in self.events], ["running", "failed"])

    def test_values_are_checked_and_cut(self):
        task = self.registry.start("m", "  " + "L" * 100 + "  ")
        self.assertEqual(self.registry.snapshot()["active"][0]["label"], "L" * MAX_LABEL)
        for fraction, expected in ((-1, 0.0), (7, 1.0), (1, 1.0), (0, 0.0), (0.25, 0.25)):
            task.update(fraction)
            self.assertEqual(self.registry.snapshot()["active"][0]["fraction"], expected)
        task.update(text="t" * 1000)
        self.assertEqual(len(self.registry.snapshot()["active"][0]["text"]), MAX_TEXT)
        for bad in (float("nan"), float("inf"), True, "0.5", [0.5]):
            with self.subTest(fraction=bad), self.assertRaises(InvalidArgument):
                task.update(bad)
        for bad in (None, 5, b"x"):
            with self.subTest(text=bad), self.assertRaises(InvalidArgument):
                task.update(0.1, bad)
        with self.assertRaises(InvalidArgument):
            task.fail(None)
        for bad in ("", "   ", None, 3):
            with self.subTest(label=bad), self.assertRaises(InvalidArgument):
                self.registry.start("m", bad)

    def test_too_many_running_at_once(self):
        tasks = [self.registry.start("m", f"t{i}") for i in range(3)]
        with self.assertRaisesRegex(Unavailable, "too many"):
            self.registry.start("m", "one more")
        tasks[0].done()
        self.registry.start("m", "now there is room")

    def test_only_the_last_ones_that_ended_are_kept_newest_first(self):
        for i in range(3):
            self.registry.start("m", f"t{i}").done()
        self.assertEqual([t["label"] for t in self.registry.snapshot()["recent"]], ["t2", "t1"])

    def test_snapshots_are_copies(self):
        self.registry.start("m", "x")
        snap = self.registry.snapshot()
        snap["active"][0]["label"] = "changed"
        snap["active"].clear()
        self.assertEqual(self.registry.snapshot()["active"][0]["label"], "x")

    def test_closing_an_owner_fails_only_its_tasks(self):
        mine = self.registry.start("gone", "a")
        other = self.registry.start("stays", "b")
        self.registry.close_owner("gone", "module unloaded")
        self.assertEqual((mine.state, other.state), ("failed", "running"))
        self.assertEqual(self.registry.snapshot()["recent"][0]["text"], "module unloaded")

    def test_the_event_comes_when_it_starts_and_ends_not_on_every_update(self):
        task = self.registry.start("m", "x")
        for i in range(10):
            task.update(i / 10)
        task.done()
        self.registry.start("m", "y").fail("boom")
        self.assertEqual([(n, p["id"], p["state"]) for n, p in self.events],
                         [("task", 1, "running"), ("task", 1, "done"), ("task", 2, "running"), ("task", 2, "failed")])

    def test_a_listener_that_fails_does_not_break_the_task(self):
        def boom(name, payload):
            raise RuntimeError("x")
        registry = TaskRegistry(on_event=boom)
        with self.assertLogs("invasor.tasks", "ERROR"):
            task = registry.start("m", "x")
            task.done()
        self.assertEqual(registry.snapshot()["recent"][0]["state"], "done")

    def test_the_event_is_sent_outside_the_lock(self):
        seen = []
        registry = TaskRegistry(on_event=lambda name, payload: seen.append(registry.snapshot()))  # would deadlock inside
        registry.start("m", "x").done()
        self.assertEqual(len(seen), 2)

    def test_many_threads_at_once(self):
        registry = TaskRegistry(max_active=100, max_recent=1000)

        def work(n):
            for i in range(20):
                with registry.start(f"m{n}", f"t{i}") as task:
                    task.update(0.5)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = registry.snapshot()
        self.assertEqual((len(snap["active"]), len(snap["recent"])), (0, 160))
        self.assertEqual(len({t["id"] for t in snap["recent"]}), 160)


class AsContextManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = TaskRegistry()

    def states(self):
        return [t["state"] for t in self.registry.snapshot()["recent"]]

    def test_leaving_normally_ends_it(self):
        with self.registry.start("m", "x") as task:
            task.update(0.2)
        self.assertEqual(self.states(), ["done"])

    def test_an_error_fails_it_and_goes_on(self):
        with self.assertRaisesRegex(ValueError, "bad"):
            with self.registry.start("m", "x"):
                raise ValueError("bad")
        done = self.registry.snapshot()["recent"][0]
        self.assertEqual((done["state"], done["text"]), ("failed", "bad"))

    def test_an_error_without_a_message_still_says_what_it_was(self):
        with self.assertRaises(KeyError):
            with self.registry.start("m", "x"):
                raise KeyError()
        self.assertEqual(self.registry.snapshot()["recent"][0]["text"], "KeyError")

    def test_what_the_block_ended_itself_stays(self):
        with self.registry.start("m", "x") as task:
            task.fail("on purpose")
        self.assertEqual(self.registry.snapshot()["recent"][0]["text"], "on purpose")

    async def test_the_same_task_works_with_async_with(self):
        async with self.registry.start("m", "x") as task:
            await asyncio.sleep(0)
            task.update(0.5, "mid")
        self.assertEqual(self.states(), ["done"])
        with self.assertRaises(ZeroDivisionError):
            async with self.registry.start("m", "y"):
                1 / 0
        self.assertEqual(self.states(), ["failed", "done"])


class Progress(unittest.TestCase):
    def test_a_transfer_is_mapped_onto_part_of_the_bar(self):
        registry = TaskRegistry()
        task = registry.start("core", "x")
        report = task.progress(0.0, 0.8, "Downloading")
        report(50, 100)
        view = registry.snapshot()["active"][0]
        self.assertEqual((view["fraction"], view["text"]), (0.4, "Downloading"))
        report(500, 100)  # more than announced: stays inside its stretch
        self.assertEqual(registry.snapshot()["active"][0]["fraction"], 0.8)
        report(10, None)  # the size isn't known: no bar, still the text
        view = registry.snapshot()["active"][0]
        self.assertEqual((view["fraction"], view["text"]), (None, "Downloading"))


if __name__ == "__main__":
    unittest.main()
