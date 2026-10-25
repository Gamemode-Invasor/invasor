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
            "text": "", "started": 100.0, "updated": 100.0, "finished": None, "question": None}]})
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
            "text": "all set", "started": 100.0, "updated": 102.0, "finished": 102.0, "question": None}])

    def test_a_task_callback_hears_its_start_and_its_end(self):
        heard = []
        self.registry.start("m", "One", lambda phase, view: heard.append((phase, view["state"]))).done("ok")
        self.registry.start("m", "Two", lambda phase, view: heard.append((phase, view["state"]))).fail("bad")
        self.assertEqual(heard, [("start", "running"), ("end", "done"), ("start", "running"), ("end", "failed")])

    def test_a_task_callback_is_quiet_when_its_owner_is_closed_and_its_failure_is_not_fatal(self):
        heard = []
        self.registry.start("m", "One", lambda phase, view: heard.append(phase))
        self.registry.close_owner("m", "unloaded")
        self.assertEqual(heard, ["start"])

        def boom(phase, view):
            raise RuntimeError("boom")
        with self.assertLogs("invasor.tasks", "ERROR"):
            self.registry.start("m", "Two", boom).done()
        self.assertEqual(self.registry.snapshot()["recent"][0]["state"], "done")

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

    def test_a_huge_int_is_cut_to_the_edges_not_an_overflow(self):
        task = self.registry.start("m", "big")
        task.update(10**400)
        self.assertEqual(self.registry.snapshot()["active"][0]["fraction"], 1.0)
        task.update(-10**400)
        self.assertEqual(self.registry.snapshot()["active"][0]["fraction"], 0.0)

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

    def test_a_watcher_hears_every_change_including_the_updates(self):
        heard = []
        self.registry.watch(lambda: heard.append(1))
        task = self.registry.start("m", "x")
        task.update(0.1)
        task.update(0.2)
        task.done()
        task.update(0.9)  # after the end: nothing changed
        task.fail("late")
        self.assertEqual(len(heard), 4)

    def test_a_watcher_that_fails_does_not_break_the_task_or_the_others(self):
        heard = []

        def boom():
            raise RuntimeError("x")
        self.registry.watch(boom)
        self.registry.watch(lambda: heard.append(1))
        with self.assertLogs("invasor.tasks", "ERROR"):
            task = self.registry.start("m", "x")
        task.done()
        self.assertEqual(len(heard), 2)
        self.assertEqual(self.registry.snapshot()["recent"][0]["state"], "done")

    def test_a_watcher_may_read_the_registry(self):
        seen = []
        self.registry.watch(lambda: seen.append(self.registry.snapshot()))  # would deadlock inside the lock
        self.registry.start("m", "x").update(0.5)
        self.assertEqual(len(seen), 2)

    def test_clearing_forgets_what_ended_and_keeps_what_runs(self):
        running = self.registry.start("m", "still going")
        self.registry.start("m", "a").done()
        self.registry.start("m", "b").fail("no")
        self.assertEqual(self.registry.clear_recent(), 2)
        snap = self.registry.snapshot()
        self.assertEqual(snap["recent"], [])
        self.assertEqual([t["label"] for t in snap["active"]], ["still going"])
        running.done()
        self.assertEqual(len(self.registry.snapshot()["recent"]), 1)  # it still ends normally

    def test_clearing_tells_the_watchers_once_and_only_if_it_did_something(self):
        heard = []
        self.registry.start("m", "a").done()
        self.registry.watch(lambda: heard.append(1))
        self.registry.clear_recent()
        self.assertEqual(len(heard), 1)
        self.assertEqual(self.registry.clear_recent(), 0)  # nothing left
        self.assertEqual(len(heard), 1)

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


import time  # noqa: E402  (for the tests below)


class _Box(list):
    worker = None


class Asking(unittest.TestCase):
    """A task that waits for the user: ask() in a worker thread, answer() from the panel."""

    def setUp(self):
        self.events = []
        self.changes = []
        self.registry = TaskRegistry(on_event=lambda name, payload: self.events.append((name, payload)))
        self.registry.watch(lambda: self.changes.append(1))
        self.task = self.registry.start("good", "Downloading")

    def asking(self, question="Download it?", **kw):
        """Start task.ask() in a thread, wait until it waits. A list that gets the answer."""
        box = _Box()
        worker = threading.Thread(target=lambda: box.append(self.task.ask(question, **kw)), daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        deadline = time.monotonic() + 3
        while self.task.state != "waiting" and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertEqual(self.task.state, "waiting")
        box.worker = worker
        return box

    def ended(self, box, within=1.0):
        box.worker.join(within)
        self.assertFalse(box.worker.is_alive(), "the waiter didn't wake up")
        return box[0]

    def view(self):
        return self.registry.snapshot()["active"][0]

    def test_accept(self):
        box = self.asking("Download x from y?")
        view = self.view()
        self.assertEqual((view["state"], view["question"]), ("waiting", "Download x from y?"))
        self.assertTrue(self.registry.answer(self.task.id, True))
        self.assertIs(self.ended(box), True)
        self.assertEqual((self.task.state, self.view()["state"], self.view()["question"]), ("running", "running", None))
        self.task.update(0.5, "goes on")
        self.assertEqual(self.view()["fraction"], 0.5)
        self.task.done()
        self.assertEqual(self.registry.snapshot()["recent"][0]["state"], "done")

    def test_deny(self):
        box = self.asking()
        self.assertTrue(self.registry.answer(self.task.id, False))
        self.assertIs(self.ended(box), False)
        self.assertEqual(self.view()["question"], None)

    def test_the_question_is_none_when_not_waiting(self):
        self.assertIsNone(self.view()["question"])
        self.task.done()
        self.assertIsNone(self.registry.snapshot()["recent"][0]["question"])

    def test_timeout_is_a_no(self):
        start = time.monotonic()
        self.assertIs(self.task.ask("anyone?", timeout=0.05), False)
        self.assertLess(time.monotonic() - start, 2)
        self.assertNotEqual(self.task.state, "waiting")
        self.assertIsNone(self.view()["question"])
        self.assertFalse(self.registry.answer(self.task.id, True))  # too late

    def test_ending_it_while_waiting_wakes_the_waiter(self):
        for end in (lambda: self.task.done("x"), lambda: self.task.fail("x"),
                    lambda: self.registry.close_owner("good", "module unloaded")):
            with self.subTest(end=end):
                self.task = self.registry.start("good", "Downloading")
                box = self.asking()
                end()
                self.assertIs(self.ended(box, within=1.0), False)
                self.assertIn(self.task.state, ("done", "failed"))
                self.assertFalse(self.registry.answer(self.task.id, True))

    def test_asking_an_ended_or_waiting_task_is_a_no_at_once(self):
        box = self.asking()
        start = time.monotonic()
        self.assertIs(self.task.ask("twice?"), False)  # already waiting
        self.assertEqual(self.view()["question"], "Download it?")
        self.registry.answer(self.task.id, True)
        self.ended(box)
        self.task.done()
        self.assertIs(self.task.ask("after the end?"), False)
        self.assertLess(time.monotonic() - start, 1)

    def test_update_is_ignored_while_waiting(self):
        self.task.update(0.3, "before")
        box = self.asking()
        self.task.update(0.9, "while waiting")
        view = self.view()
        self.assertEqual((view["state"], view["fraction"], view["text"], view["question"]),
                         ("waiting", 0.3, "before", "Download it?"))
        self.registry.answer(self.task.id, True)
        self.ended(box)

    def test_the_question_is_checked_and_cut(self):
        for bad in ("", None, 5, b"x"):
            with self.subTest(question=bad), self.assertRaises(InvalidArgument):
                self.task.ask(bad, timeout=0.01)
        self.assertEqual(self.task.state, "running")
        box = self.asking("q" * 1000)
        self.assertEqual(len(self.view()["question"]), MAX_TEXT)
        self.registry.answer(self.task.id, False)
        self.ended(box)

    def test_answer_with_nobody_waiting(self):
        self.assertFalse(self.registry.answer(self.task.id, True))  # running, not waiting
        self.assertFalse(self.registry.answer(9999, True))
        self.assertEqual(self.task.state, "running")

    def test_answer_checks_its_types(self):
        for task_id, accept in ((True, True), (str(self.task.id), True), (1.0, True), (None, True),
                                (self.task.id, "yes"), (self.task.id, 1), (self.task.id, None)):
            with self.subTest(id=task_id, accept=accept), self.assertRaises(InvalidArgument):
                self.registry.answer(task_id, accept)

    def test_waiting_is_an_event_and_a_change(self):
        events_before, changes_before = len(self.events), len(self.changes)
        box = self.asking()
        self.assertIn("waiting", [p["state"] for _, p in self.events[events_before:]])
        waiting_event = [p for _, p in self.events if p["state"] == "waiting"][0]
        self.assertEqual(waiting_event["question"], "Download it?")
        self.assertGreater(len(self.changes), changes_before)
        changes_waiting = len(self.changes)
        self.registry.answer(self.task.id, True)
        self.ended(box)
        self.assertGreater(len(self.changes), changes_waiting)

    def test_core_task_answer(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from invasor import core
        from invasor.context import GameContext
        from invasor.gamepad import ComboWatcher
        from invasor.modules import ModuleManager
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        with mock.patch("invasor.modules.MODULES_DIR", root / "m"), \
                mock.patch("invasor.modules.USER_MODULES_DIR", root / "um"), \
                mock.patch("invasor.modules.DATA_DIR", root / "d"), \
                mock.patch("invasor.config.CONFIG_FILE", root / "config.json"):
            cfg = {"open_combo": ["L3", "R3"], "panel_side": "auto", "disabled_modules": []}
            manager = ModuleManager(cfg, GameContext())
            api = core.make_methods(manager, GameContext(), ComboWatcher(cfg["open_combo"], lambda: None), cfg)
            self.registry = manager.tasks
            self.task = manager.tasks.start("good", "Downloading")
            box = self.asking()
            answer = api["task_answer"](self.task.id, True)
            self.assertIs(self.ended(box), True)
            self.assertEqual(answer["active"], api["tasks"]()["active"])
            self.assertIn("recent", answer)
            if "answered" in answer:
                self.assertIs(answer["answered"], True)
                self.assertIs(api["task_answer"](self.task.id, True)["answered"], False)
            with self.assertRaises(InvalidArgument):
                api["task_answer"]("1", True)
            self.task.done()


class AskRules(unittest.TestCase):
    def setUp(self):
        self.registry = TaskRegistry()
        self.task = self.registry.start("good", "Downloading")

    def test_bad_timeouts_are_refused(self):
        for bad in (0, -1, 3601, True, "10", None, float("nan")):
            with self.assertRaises(InvalidArgument, msg=repr(bad)):
                self.task.ask("Now?", timeout=bad)
        self.assertEqual(self.task.state, "running")

    def test_on_the_event_loop_it_is_refused_and_ask_async_works(self):
        async def run():
            with self.assertRaises(Unavailable):
                self.task.ask("Now?")
            waiting = asyncio.ensure_future(self.task.ask_async("Now?"))
            while self.task.state != "waiting":
                await asyncio.sleep(0.005)
            self.registry.answer(self.task.id, True)
            return await waiting

        self.assertIs(asyncio.run(run()), True)
