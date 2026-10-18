import unittest

from invasor import context
from invasor.context import GameContext

from .helpers import patch, temp_dir


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



def stat_line(pid, name, start):
    """A /proc/<pid>/stat line: pid (comm) state, then fields 4..21, start time (22), more."""
    return f"{pid} ({name}) S " + " ".join(["0"] * 18) + f" {start} 0 0\n"


class RunningAppid(unittest.TestCase):
    """_running_appid reads each process's environment once (a fake /proc here)."""

    def setUp(self):
        self.proc = temp_dir(self)
        patch(self, "invasor.context._environ_cache", {})

    def process(self, pid, env, name="game", start=100):
        d = self.proc / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(stat_line(pid, name, start))
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")

    def test_appids_from_the_environment(self):
        self.process(10, {"PATH": "/bin"})
        self.assertIsNone(context._running_appid(self.proc))
        self.process(11, {"SteamAppId": "620", "SteamGameId": "620"})
        self.assertEqual(context._running_appid(self.proc), "620")

    def test_a_shortcut_gives_its_64_bit_game_id_as_the_appid(self):
        self.process(12, {"SteamAppId": "0", "SteamGameId": str(4000000000 << 32 | 0x02000000)})
        self.assertEqual(context._running_appid(self.proc), "4000000000")

    def test_each_environment_is_read_once(self):
        self.process(20, {"SteamAppId": "620"})
        self.assertEqual(context._running_appid(self.proc), "620")
        (self.proc / "20" / "environ").unlink()  # read again, it would be gone
        self.assertEqual(context._running_appid(self.proc), "620")

    def test_a_reused_pid_or_an_exec_is_read_again(self):
        self.process(30, {"PATH": "/bin"}, name="steam", start=100)
        self.assertIsNone(context._running_appid(self.proc))
        self.process(30, {"SteamAppId": "620"}, name="steam", start=200)  # another process, same pid
        self.assertEqual(context._running_appid(self.proc), "620")
        self.process(30, {"SteamAppId": "730"}, name="reaper", start=200)  # the same one after exec
        self.assertEqual(context._running_appid(self.proc), "730")

    def test_a_name_with_spaces_and_parentheses(self):
        self.process(40, {"SteamAppId": "620"}, name="Game (x86) ) v2")
        self.assertEqual(context._running_appid(self.proc), "620")

    def test_processes_that_ended_leave_the_cache(self):
        self.process(50, {"SteamAppId": "620"})
        self.process(51, {"PATH": "/bin"})
        context._running_appid(self.proc)
        self.assertEqual(len(context._environ_cache), 2)
        for f in (self.proc / "50").iterdir():
            f.unlink()
        (self.proc / "50").rmdir()
        self.assertIsNone(context._running_appid(self.proc))
        self.assertEqual([key[0] for key in context._environ_cache], ["51"])

    def test_unreadable_processes_are_skipped(self):
        self.process(60, {"SteamAppId": "620"})
        (self.proc / "61").mkdir()  # no stat, no environ
        (self.proc / "self").mkdir()
        self.assertEqual(context._running_appid(self.proc), "620")
        self.assertNotIn("61", [key[0] for key in context._environ_cache])


class SteamRoot(unittest.TestCase):
    """The Steam folder is looked for as the installer does: ~/.steam/steam, then ~/.local/share/Steam."""

    def test_only_dot_steam(self):
        home = temp_dir(self)
        (home / ".steam/steam/steamapps").mkdir(parents=True)
        (home / ".steam/steam/steamapps/appmanifest_620.acf").write_text('"AppState"\n{\n\t"name"\t\t"Portal 2"\n}\n')
        patch(self, "invasor.cef_flag.HOME", home)
        self.assertEqual(context._steam_root(), home / ".steam/steam")
        self.assertEqual(context._app_name("620"), "Portal 2")

    def test_the_override_wins(self):
        root = temp_dir(self)
        patch(self, "invasor.context.STEAM_ROOT", root)
        self.assertEqual(context._steam_root(), root)


if __name__ == "__main__":
    unittest.main()
