import unittest

from invasor.context import GameContext

from .helpers import patch


def game(appid, name=None):
    return {"appid": appid, "name": name, "shortcut": False}


class GameEvents(unittest.IsolatedAsyncioTestCase):
    """The events GameContext._poll raises when the selected or the running game changes."""

    def setUp(self):
        self.selected = None
        self.running = None
        self.names = {}
        self.events = []

        async def targets():
            if self.selected is None:
                return []
            return [{"title": "SharedJSContext", "url": f"https://steamloopback.host/routes/library/app/{self.selected}"}]

        patch(self, "invasor.context.cef.list_targets", targets)
        patch(self, "invasor.context._running_appid", lambda: self.running)
        patch(self, "invasor.context._shortcut_names", lambda: {})
        patch(self, "invasor.context._app_name", lambda appid: self.names.get(appid))
        self.context = GameContext()
        self.context.on_event = lambda name, payload: self.events.append((name, payload["appid"]))

    async def poll(self, selected=None, running=None):
        self.selected, self.running = selected, running
        self.events.clear()
        await self.context._poll()
        return list(self.events)

    async def test_start_and_stop(self):
        self.assertEqual(await self.poll(running="10"), [("game_started", "10")])
        self.assertEqual(await self.poll(running="10"), [])  # nothing changed
        self.assertEqual(await self.poll(), [("game_stopped", "10")])

    async def test_switching_games_stops_one_then_starts_the_other(self):
        await self.poll(running="10")
        self.assertEqual(await self.poll(running="20"), [("game_stopped", "10"), ("game_started", "20")])

    async def test_opening_a_game_page_is_selected_and_leaving_it_is_silent(self):
        self.assertEqual(await self.poll(selected="10"), [("game_selected", "10")])
        self.assertEqual(await self.poll(selected="10"), [])
        self.assertEqual(await self.poll(selected="20"), [("game_selected", "20")])
        self.assertEqual(await self.poll(), [])

    async def test_a_name_that_turns_up_late_is_not_a_new_game(self):
        self.assertEqual(await self.poll(running="10"), [("game_started", "10")])
        self.names["10"] = "Late Name"
        self.assertEqual(await self.poll(running="10"), [])
        self.assertEqual(self.context.running["name"], "Late Name")

    async def test_a_game_already_running_when_invasor_starts_counts_as_started(self):
        self.assertEqual(await self.poll(selected="10", running="10"), [("game_started", "10"), ("game_selected", "10")])

    async def test_the_payload_is_a_copy(self):
        got = []
        self.context.on_event = lambda name, payload: got.append(payload)
        await self.poll(running="10")
        got[0]["appid"] = "hacked"
        self.assertEqual(self.context.running["appid"], "10")

    async def test_a_failing_listener_does_not_break_the_poll(self):
        def boom(name, payload):
            raise RuntimeError("x")
        self.context.on_event = boom
        with self.assertLogs("invasor.context", "ERROR"):
            await self.poll(running="10")
        self.assertEqual(self.context.running["appid"], "10")

    async def test_no_listener_is_fine(self):
        self.context.on_event = None
        await self.poll(running="10")
        self.assertEqual(self.context.running["appid"], "10")


if __name__ == "__main__":
    unittest.main()
