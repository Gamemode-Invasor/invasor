"""steamctx.py: ctx.steam (shortcuts, launch options, links) and ctx.steam_language().

No real Steam: a real SteamBridge (on a real loop in a thread) evaluates every expression in
node against a fake SteamClient, and this file plays Steam's side: it writes a temporary
shortcuts.vdf the way Steam does (AddShortcut names the entry after the exe and ignores the
name and options it's given, Steam stores the appid as a signed int32 and quotes Exe/StartDir).
gio/xdg-open, shutil.which, the session env, the marker file, tempfile's folder and the clock
of steamctx are all fakes."""
import asyncio
import json
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from invasor import config, steamctx
from invasor.context import GameContext
from invasor.schema import InvalidArgument, Unavailable
from invasor.steam import SteamBridge
from invasor.tasks import TaskRegistry

from .helpers import patch, temp_dir

NODE = shutil.which("node")  # resolved before shutil.which is replaced
REAL_SLEEP = time.sleep
MARKER = "/tmp/addnonsteamgamefile"
ACCOUNT = "111"  # where the fake Steam adds shortcuts
ALL = ("Apps.AddShortcut", "Apps.SetShortcutName", "Apps.SetShortcutStartDir", "Apps.SetShortcutLaunchOptions",
       "Apps.RemoveShortcut", "Apps.RegisterForAppDetails", "Apps.SetAppLaunchOptions", "URL.ExecuteSteamURL")
GAME = "620"  # a Steam game (not a shortcut) the fake Steam knows

HARNESS = r"""
const vm = require('vm');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const cfg = JSON.parse(input);
  const calls = [], alerts = [];
  const g = {};
  g.window = g;
  g.alert = (...a) => { alerts.push(a.map(String)); };
  g.console = {log() {}, warn() {}, error() {}, info() {}, debug() {}};
  g.setTimeout = (f, ms, ...a) => setTimeout(f, Math.min(ms || 0, 30), ...a);
  g.clearTimeout = clearTimeout;
  const SC = {};
  for (const path of cfg.functions) {
    const [ns, name] = path.split('.');
    SC[ns] = SC[ns] || {};
    SC[ns][name] = function (...args) {
      calls.push({path, args: args.map(a => typeof a === 'function' ? '<fn>' : a)});
      if (cfg.throws.includes(path)) throw new Error('Steam threw');
      if (path === 'Apps.AddShortcut') return Promise.resolve(cfg.add_appid);
      if (path === 'Apps.RegisterForAppDetails') {
        const [appid, cb] = args;
        let live = true;
        const key = String(appid);
        if (cfg.details_mode !== 'never') Promise.resolve().then(() => {
          if (live) cb(key in cfg.details ? {unAppID: appid, strDisplayName: 'G', strLaunchOptions: cfg.details[key]} : {unAppID: appid});
        });
        return {unregister() { live = false; }};
      }
      return undefined;
    };
  }
  g.SteamClient = SC;
  const out = o => {
    o.calls = calls; o.alerts = alerts;
    process.stdout.write(JSON.stringify(o));
  };
  try {
    const r = await vm.runInNewContext(cfg.expr, g);
    out({result: r === undefined ? null : r});
  } catch (e) {
    out({error: String(e && e.stack || e)});
  }
});
"""


# --- binary shortcuts.vdf, as Steam writes it -----------------------------------------------------

def _int32(n):
    return struct.pack("<i", n - 2**32 if n >= 2**31 else n)


def _entry(fields):
    out = b""
    for key, value in fields.items():
        if isinstance(value, int):
            out += b"\x02" + key.encode() + b"\x00" + _int32(value)
        else:
            out += b"\x01" + key.encode() + b"\x00" + value.encode() + b"\x00"
    return out + b"\x00tags\x00\x08" + b"\x08"


def shortcuts_file(entries):
    body = b"".join(b"\x00" + str(i).encode() + b"\x00" + _entry(e) for i, e in enumerate(entries))
    return b"\x00shortcuts\x00" + body + b"\x08\x08"


def steam_entry(appid, name, exe, start_dir="", options=""):
    return {"appid": appid, "AppName": name, "Exe": f'"{exe}"', "StartDir": f'"{start_dir}"',
            "icon": "", "LaunchOptions": options}


# --- the fakes ------------------------------------------------------------------------------------

class FakeClock:
    """steamctx's `time`: sleeping advances a fake clock, so polls that time out end at once."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)
        REAL_SLEEP(0.001)

    def __getattr__(self, name):
        return getattr(time, name)


class FakeSteam:
    """Steam's side: SteamClient functions (in node), shortcuts.vdf on disk, launch options of Steam games."""

    def __init__(self, root):
        self.root = root
        self.functions = set(ALL)
        self.throws = set()
        self.unreachable = False
        self.accounts = {ACCOUNT: [], "222": []}
        self.details = {GAME: "-novid"}  # Steam games' launch options
        self.details_mode = "answer"  # "never": RegisterForAppDetails never calls back
        self.after_set_mode = None
        self.set_app_to = None  # SetAppLaunchOptions stores this instead of the line
        self.next_appid = 3000000000  # Steam stores it as a negative int32
        self.apply = {"add": True, "name": True, "options": True, "remove": True}
        self.calls = []  # every SteamClient function called: (path, args)
        self.expressions = []
        self.alerts = []
        self.urls = []  # every link Steam was asked to open (ExecuteSteamURL, gio, xdg-open)
        self.desktops = []  # (path, text, marker existed) of each .desktop Steam was given
        self.save()

    def save(self):
        for account, entries in self.accounts.items():
            f = self.root / "userdata" / account / "config" / "shortcuts.vdf"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(shortcuts_file(entries))

    def find(self, appid):
        for entries in self.accounts.values():
            for e in entries:
                low = {k.lower(): v for k, v in e.items()}
                if low["appid"] & 0xFFFFFFFF == int(appid):
                    return e
        return None

    def add_entry(self, entry, account=ACCOUNT):
        self.accounts[account].append(entry)
        self.save()

    def paths(self):
        return [p for p, _ in self.calls]

    # SteamBridge's evaluate
    async def evaluate(self, expression, timeout):
        return await asyncio.to_thread(self._evaluate, expression)

    def _evaluate(self, expression):
        self.expressions.append(expression)
        if self.unreachable:
            raise ConnectionError("Steam isn't running")
        cfg = {"expr": expression, "functions": sorted(self.functions), "throws": sorted(self.throws),
               "add_appid": self.next_appid, "details": self.details, "details_mode": self.details_mode}
        p = subprocess.Popen([NODE, "-e", HARNESS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        out, err = p.communicate(json.dumps(cfg).encode(), timeout=30)
        res = json.loads(out or b"{}") if out else {"error": err.decode(), "calls": [], "alerts": []}
        self.alerts += res["alerts"]
        for call in res["calls"]:
            self.calls.append((call["path"], call["args"]))
            if call["path"] not in self.throws:
                self._steam_does(call["path"], call["args"])
        if "error" in res:
            raise RuntimeError(res["error"])
        return res["result"]

    def _steam_does(self, path, args):
        if path == "Apps.AddShortcut" and self.apply["add"]:
            name, exe, start_dir, options = (list(args) + [""] * 4)[:4]
            self.add_entry(steam_entry(self.next_appid, os.path.basename(exe), exe, start_dir))
        elif path in ("Apps.SetShortcutName", "Apps.SetShortcutStartDir", "Apps.SetShortcutLaunchOptions"):
            key = {"Apps.SetShortcutName": ("AppName", "name"), "Apps.SetShortcutStartDir": ("StartDir", "add"),
                   "Apps.SetShortcutLaunchOptions": ("LaunchOptions", "options")}[path]
            e = self.find(args[0])
            if e is not None and self.apply[key[1]]:
                for k in list(e):
                    if k.lower() == key[0].lower():
                        e[k] = args[1] if key[0] != "StartDir" else f'"{args[1]}"'
                self.save()
        elif path == "Apps.RemoveShortcut" and self.apply["remove"]:
            for entries in self.accounts.values():
                entries[:] = [e for e in entries if {k.lower(): v for k, v in e.items()}["appid"] & 0xFFFFFFFF != int(args[0])]
            self.save()
        elif path == "Apps.SetAppLaunchOptions":
            self.details[str(args[0])] = args[1] if self.set_app_to is None else self.set_app_to
            if self.after_set_mode:
                self.details_mode = self.after_set_mode
        elif path == "URL.ExecuteSteamURL":
            self.open_link(args[0])

    def open_link(self, link):
        """What Steam does with a link it's handed (by SteamClient, gio or xdg-open)."""
        self.urls.append(link)
        prefix = "steam://addnonsteamgame/"
        if not link.startswith(prefix):
            return
        desktop = urllib.parse.unquote(link[len(prefix):])
        marker = os.path.exists(self.marker)
        try:
            text = Path(desktop).read_text()
        except OSError:
            text = None
        self.desktops.append((desktop, text, marker))
        if text is None or not marker or not self.apply["add"]:
            return  # without the marker Steam shows a dialog and adds nothing
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        exe = desktop_unquote(fields.get("Exec", ""))
        name = fields.get("Name", "").replace("\\\\", "\0").replace("\\n", "\n").replace("\\t", "\t") \
            .replace("\\r", "\r").replace("\0", "\\")
        if not os.access(exe, os.X_OK):
            name = os.path.basename(desktop)  # Steam only reads Name/Exec of an executable target
        self.add_entry(steam_entry(self.next_appid, name, exe, fields.get("Path", "")))


def desktop_escape(path):
    """Exec's quoting: double quotes, with \\ " $ ` escaped by a backslash and % doubled (field codes)."""
    return '"' + "".join("\\" + c if c in '\\"$`' else "%%" if c == "%" else c for c in path) + '"'


def desktop_unquote(value):
    if not (value.startswith('"') and value.endswith('"')):
        return value
    out, it = [], iter(value[1:-1])
    for c in it:
        out.append(next(it, "") if c == "\\" else next(it, "") if c == "%" else c)  # %% is a %
    return "".join(out)


def find_attr(module, *values):
    """The name of a module-level constant of `module` equal to one of `values` (str or Path)."""
    for name, value in vars(module).items():
        if isinstance(value, (str, Path)) and str(value) in values:
            return name
    return None


# --- the base -------------------------------------------------------------------------------------

@unittest.skipUnless(NODE, "node not installed")
class Base(unittest.TestCase):
    MODULE = "good"

    def setUp(self):
        self.root = temp_dir(self)
        self.steam_root = self.root / "steam"
        patch(self, "invasor.paths.STEAM_ROOT", self.steam_root)
        patch(self, "invasor.paths.MEDIA_GLOBS", ())
        patch(self, "invasor.context._shortcuts_cache", {"key": None, "names": {}, "exes": {}})
        self.data = self.root / "core-data"
        # any constant of steamctx under the real data folder (OWNERS…) goes to the temp one
        real_data = Path(config.DATA_DIR)
        for name, value in list(vars(steamctx).items()):
            if isinstance(value, Path) and (value == real_data or real_data in value.parents):
                patch(self, f"invasor.steamctx.{name}", self.data / value.relative_to(real_data))
        patch(self, "invasor.config.DATA_DIR", self.data)
        patch(self, "invasor.modules.DATA_DIR", self.root / "module-data")
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        patch(self, "tempfile.tempdir", str(self.tmp))
        # the marker file (never the real /tmp one)
        self.marker = self.root / "addnonsteamgamefile"
        self.marker_attr = find_attr(steamctx, MARKER)
        if self.marker_attr:
            old = getattr(steamctx, self.marker_attr)
            patch(self, f"invasor.steamctx.{self.marker_attr}",
                  self.marker if isinstance(old, Path) else str(self.marker))
        # the clock of the polls
        self.clock = FakeClock()
        if getattr(steamctx, "time", None) is time:
            patch(self, "invasor.steamctx.time", self.clock)
        for name in ("monotonic", "sleep"):
            if getattr(steamctx, name, None) is getattr(time, name):
                patch(self, f"invasor.steamctx.{name}", getattr(self.clock, name))
        # the desktop tools and the graphical session
        self.tools = set()
        self.failing = {}  # tool -> returncode or exception
        self.ran = []
        patch(self, "shutil.which", self.which)
        patch(self, "subprocess.run", self.run_tool)
        self.session = {"WAYLAND_DISPLAY": "wayland-test", "DISPLAY": ":9"}
        patched_env = False
        for mod_name in ("invasor.steamctx", "invasor.session"):
            try:
                mod = __import__(mod_name, fromlist=["x"])
            except ImportError:
                continue
            if hasattr(mod, "session_env"):
                patch(self, f"{mod_name}.session_env", lambda *a, **k: dict(self.session))
                patched_env = True
        self.assertTrue(patched_env, "session_env not found in steamctx.py or session.py")

        self.fake = FakeSteam(self.steam_root)
        self.fake.marker = str(self.marker)
        self.loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        loop_thread.start()

        def stop_loop():
            self.loop.call_soon_threadsafe(self.loop.stop)
            loop_thread.join(5)
            self.loop.close()
        self.addCleanup(stop_loop)
        self.bridge = SteamBridge(self.fake.evaluate)
        self.bridge.loop = self.loop
        self.shown = []
        self.bridge.notify_sync = self.notify
        self.ctx = self.make_ctx(self.MODULE)
        self.exe = self.make_exe("game.sh")

    def make_ctx(self, module_id):
        from invasor.modules import ModuleContext
        return ModuleContext(module_id, self.root / "modules" / module_id, GameContext(), None,
                             steam=self.bridge, tasks=TaskRegistry())

    def make_exe(self, name, mode=0o755, folder="games"):
        d = self.root / folder
        d.mkdir(exist_ok=True)
        f = d / name
        f.write_text("#!/bin/sh\necho hi\n")
        f.chmod(mode)
        return str(f)

    def notify(self, title, body="", icon="", timeout=15, sound=""):
        self.shown.append((title, body))
        return True

    def wait_shown(self, count=1):
        deadline = time.monotonic() + 3
        while len(self.shown) < count and time.monotonic() < deadline:
            REAL_SLEEP(0.01)
        return self.shown

    def which(self, name, *args, **kwargs):
        return f"/usr/bin/{name}" if name in self.tools else None

    def run_tool(self, argv, *args, **kwargs):
        self.ran.append((list(argv), kwargs))
        tool = os.path.basename(argv[0])
        failure = self.failing.get(tool)
        if isinstance(failure, BaseException):
            raise failure
        if failure:
            return subprocess.CompletedProcess(argv, failure, b"", b"")
        self.fake.open_link(argv[-1])
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def owners(self):
        try:
            return json.loads((self.data / "steam-shortcuts.json").read_text())
        except FileNotFoundError:
            return {}

    def vdf_names(self):
        return {str(e["appid"] & 0xFFFFFFFF): e for es in self.fake.accounts.values() for e in es}


# --- supports -------------------------------------------------------------------------------------

class Supports(Base):
    def test_every_name_with_everything_there(self):
        for name in ("add_to", "open_url", "remove_shortcut", "launch_options", "set_launch_options"):
            with self.subTest(name=name):
                self.assertIs(self.ctx.steam.supports(name), True)

    def test_each_name_needs_its_functions(self):
        cases = [
            ("remove_shortcut", {"Apps.RemoveShortcut"}),
            ("launch_options", {"Apps.RegisterForAppDetails"}),
            ("set_launch_options", {"Apps.SetAppLaunchOptions"}),
            ("set_launch_options", {"Apps.SetShortcutLaunchOptions"}),
            ("set_launch_options", {"Apps.RegisterForAppDetails"}),
            ("open_url", {"URL.ExecuteSteamURL"}),
            ("add_to", {"Apps.AddShortcut", "URL.ExecuteSteamURL"}),
            ("add_to", {"Apps.SetShortcutName", "URL.ExecuteSteamURL"}),
        ]
        for name, missing in cases:
            with self.subTest(name=name, missing=missing):
                self.fake.functions = set(ALL) - missing
                self.assertIs(self.ctx.steam.supports(name), False)

    def test_add_to_either_path_is_enough(self):
        self.fake.functions = set(ALL) - {"URL.ExecuteSteamURL"}
        self.assertIs(self.ctx.steam.supports("add_to"), True)
        self.fake.functions = set(ALL) - {"Apps.AddShortcut"}
        self.assertIs(self.ctx.steam.supports("add_to"), True)

    def test_open_url_falls_back_on_desktop_tools(self):
        self.fake.functions = set(ALL) - {"URL.ExecuteSteamURL"}
        for tool in ("gio", "xdg-open"):
            with self.subTest(tool=tool):
                self.tools = {tool}
                self.assertIs(self.ctx.steam.supports("open_url"), True)

    def test_unknown_names_are_refused_before_steam(self):
        for bad in ("nope", "", "ADD_TO", "add_to'); alert(1); ('", None, 5, ["add_to"]):
            with self.subTest(name=bad), self.assertRaises(InvalidArgument):
                self.ctx.steam.supports(bad)
        self.assertEqual(self.fake.expressions, [])

    def test_steam_unreachable_is_false(self):
        self.fake.unreachable = True
        for name in ("add_to", "remove_shortcut", "launch_options", "set_launch_options", "open_url"):
            with self.subTest(name=name):
                self.assertIs(self.ctx.steam.supports(name), False)
        self.bridge.loop = None  # bridge not ready
        self.assertIs(self.ctx.steam.supports("remove_shortcut"), False)

    def test_it_only_checks_never_calls(self):
        self.ctx.steam.supports("add_to")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.alerts, [])


# --- open_url -------------------------------------------------------------------------------------

class OpenUrl(Base):
    URL = "https://github.com/Gamemode-Invasor/invasor"

    def test_principal_asks_steam_to_open_it(self):
        self.assertIsNone(self.ctx.steam.open_url(self.URL))
        self.assertEqual(self.fake.calls, [("URL.ExecuteSteamURL", ["steam://openurl/" + self.URL])])
        self.ctx.steam.open_url("http://example.org/a?b=c#d")
        self.assertEqual(self.fake.calls[-1][1], ["steam://openurl/http://example.org/a?b=c#d"])
        self.assertEqual(self.ran, [])

    def test_bad_urls_never_reach_steam(self):
        for bad in ("steam://install/620", "steam://openurl/https://x.org", "file:///etc/passwd", "javascript:alert(1)",
                    "ftp://x.org/a", "data:text/html,hi", "https://", "http:///path", "x.org/a", "",
                    "https://x.org/a\nb", "https://x.org/\x00", "https://x.org/\x7f", "https://x.org/\r",
                    "https://x.org/\t", "https://x.org/" + "a" * 2048, None, 5, b"https://x.org"):
            with self.subTest(url=bad), self.assertRaises(InvalidArgument):
                self.ctx.steam.open_url(bad)
        self.assertEqual((self.fake.calls, self.fake.expressions, self.ran), ([], [], []))

    def test_a_quote_in_the_url_is_data_not_code(self):
        url = "https://x.org/');alert(1);('\"`${alert(2)}`"
        self.ctx.steam.open_url(url)
        self.assertEqual(self.fake.calls, [("URL.ExecuteSteamURL", ["steam://openurl/" + url])])
        self.assertEqual(self.fake.alerts, [])

    def test_fallback_gio_then_xdg_open_with_the_session_env(self):
        self.fake.functions.discard("URL.ExecuteSteamURL")
        self.tools = {"gio", "xdg-open"}
        self.ctx.steam.open_url(self.URL)
        self.assertEqual(len(self.ran), 1)
        argv, kw = self.ran[0]
        self.assertEqual(os.path.basename(argv[0]), "gio")
        self.assertEqual(argv[1], "open")
        self.assertIn(argv[-1], (self.URL, "steam://openurl/" + self.URL))
        self.assertEqual(kw["env"]["WAYLAND_DISPLAY"], "wayland-test")
        self.assertEqual(kw["env"]["DISPLAY"], ":9")
        self.assertIn("PATH", kw["env"])  # the service's own environment too
        self.assertIsNotNone(kw.get("timeout"))
        self.assertNotIn("shell", {k for k, v in kw.items() if v})

        self.ran.clear()
        self.failing["gio"] = 1
        self.ctx.steam.open_url(self.URL)
        self.assertEqual([os.path.basename(a[0]) for a, _ in self.ran], ["gio", "xdg-open"])
        self.assertIn(self.ran[1][0][-1], (self.URL, "steam://openurl/" + self.URL))

        self.ran.clear()
        self.failing["gio"] = FileNotFoundError("gio")
        self.ctx.steam.open_url(self.URL)
        self.assertEqual(os.path.basename(self.ran[-1][0][0]), "xdg-open")

    def test_a_missing_gio_goes_to_xdg_open(self):
        self.fake.functions.discard("URL.ExecuteSteamURL")
        self.tools = {"xdg-open"}
        self.ctx.steam.open_url(self.URL)
        self.assertEqual([os.path.basename(a[0]) for a, _ in self.ran], ["xdg-open"])

    def test_steam_unreachable_uses_the_fallback(self):
        self.fake.unreachable = True
        self.tools = {"gio"}
        self.ctx.steam.open_url(self.URL)
        self.assertEqual(len(self.ran), 1)

    def test_nothing_works_is_unavailable(self):
        self.fake.functions.discard("URL.ExecuteSteamURL")
        with self.assertRaises(Unavailable):
            self.ctx.steam.open_url(self.URL)
        self.tools = {"gio", "xdg-open"}
        self.failing = {"gio": 4, "xdg-open": subprocess.TimeoutExpired("xdg-open", 10)}
        with self.assertRaises(Unavailable):
            self.ctx.steam.open_url(self.URL)


# --- add_to ---------------------------------------------------------------------------------------

class AddToArguments(Base):
    def test_control_characters_in_real_paths_never_reach_steam(self):
        # Real file and folder names may hold a newline: it would add lines to the fallback's .desktop.
        exe = self.make_exe("two\nlines.sh")
        folder = self.root / "dir\nname"
        folder.mkdir()
        for args, kw in ((("Game", exe), {}), (("Game", self.exe), {"start_dir": str(folder)})):
            with self.subTest(args=args, kw=kw), self.assertRaises(InvalidArgument):
                self.ctx.steam.add_to(*args, **kw)
        self.assertEqual(self.fake.calls, [])

    def test_bad_arguments_never_reach_steam(self):
        folder = os.path.dirname(self.exe)
        bad = [
            ((None, self.exe), {}), ((5, self.exe), {}), (("", self.exe), {}), (("   ", self.exe), {}),
            (("a" * 257, self.exe), {}), (("Line\nTwo", self.exe), {}), (("Tab\there", self.exe), {}),
            (("Nul\x00", self.exe), {}), (("Del\x7f", self.exe), {}), (("Cr\r", self.exe), {}),
            (("Game", "game.sh"), {}), (("Game", "./game.sh"), {}), (("Game", str(self.root / "missing.sh")), {}),
            (("Game", folder), {}), (("Game", "/dev/null"), {}), (("Game", None), {}), (("Game", b"/x"), {}),
            (("Game", Path(self.exe).as_posix() + "\n"), {}),
            (("Game", self.exe), {"start_dir": "games"}), (("Game", self.exe), {"start_dir": str(self.root / "nope")}),
            (("Game", self.exe), {"start_dir": self.exe}), (("Game", self.exe), {"start_dir": 5}),
            (("Game", self.exe), {"launch_options": "a\nb"}), (("Game", self.exe), {"launch_options": "a\rb"}),
            (("Game", self.exe), {"launch_options": "a\x00b"}), (("Game", self.exe), {"launch_options": "x" * 4097}),
            (("Game", self.exe), {"launch_options": 5}),
        ]
        for args, kw in bad:
            with self.subTest(args=args, kw=kw), self.assertRaises(InvalidArgument):
                self.ctx.steam.add_to(*args, **kw)
        self.assertEqual((self.fake.calls, self.ran, self.owners()), ([], [], {}))
        self.assertEqual(self.vdf_names(), {})

    def test_limits_are_inclusive_and_the_name_is_stripped(self):
        res = self.ctx.steam.add_to("  " + "n" * 256 + "  ", self.exe, launch_options="o" * 4096)
        self.assertFalse(res["existed"])
        self.assertIn(("Apps.SetShortcutName", [res["appid"], "n" * 256]), self.fake.calls)

    def test_a_symlinked_exe_is_kept_as_given(self):
        link = self.root / "games" / "link.sh"
        link.symlink_to(self.exe)
        self.ctx.steam.add_to("Linked", str(link))
        self.assertEqual(self.fake.calls[0][1][1], str(link))


class AddToPrincipal(Base):
    def test_adds_then_names_and_sets_everything_verified_on_disk(self):
        with self.assertLogs(level="INFO") as logs:
            res = self.ctx.steam.add_to("My Game", self.exe, launch_options="FOO=1 %command%")
        appid = 3000000000
        self.assertEqual(res, {"appid": appid, "existed": False})
        self.assertIsInstance(res["appid"], int)
        folder = os.path.dirname(self.exe)
        self.assertEqual(self.fake.calls, [
            ("Apps.AddShortcut", ["My Game", self.exe, folder, "FOO=1 %command%"]),
            ("Apps.SetShortcutName", [appid, "My Game"]),
            ("Apps.SetShortcutStartDir", [appid, folder]),
            ("Apps.SetShortcutLaunchOptions", [appid, "FOO=1 %command%"]),
        ])
        entry = self.vdf_names()[str(appid)]
        self.assertEqual((entry["AppName"], entry["LaunchOptions"]), ("My Game", "FOO=1 %command%"))
        # Steam stored it as a negative int32; the module gets the unsigned one
        raw = (self.steam_root / "userdata" / ACCOUNT / "config" / "shortcuts.vdf").read_bytes()
        self.assertIn(struct.pack("<i", appid - 2**32), raw)
        self.assertEqual(self.owners(), {str(appid): {"module": "good", "exe": self.exe}})
        text = "\n".join(logs.output)
        self.assertIn("good", text)
        self.assertIn(str(appid), text)
        self.assertIn("My Game", text)
        self.assertTrue(self.wait_shown(), "no notification")
        self.assertEqual(self.ran, [])

    def test_a_failing_set_call_still_leaves_it_removable(self):
        # AddShortcut worked, SetShortcutName didn't: the shortcut is there, and this module owns it.
        self.fake.throws.add("Apps.SetShortcutName")
        with self.assertRaises(Unavailable):
            self.ctx.steam.add_to("G", self.exe)
        self.assertIn("3000000000", self.owners())
        self.assertEqual(self.ctx.steam.remove_shortcut(3000000000), {"removed": True})

    def test_without_options_no_set_options_and_start_dir_as_given(self):
        start = self.root / "elsewhere"
        start.mkdir()
        res = self.ctx.steam.add_to("G", self.exe, start_dir=str(start))
        self.assertEqual(self.fake.calls[0], ("Apps.AddShortcut", ["G", self.exe, str(start), ""]))
        self.assertIn(("Apps.SetShortcutStartDir", [res["appid"], str(start)]), self.fake.calls)
        self.assertNotIn("Apps.SetShortcutLaunchOptions", self.fake.paths())

    def test_principal_doesnt_touch_the_exe_mode(self):
        exe = self.make_exe("plain.sh", 0o644)
        self.ctx.steam.add_to("Plain", exe)
        self.assertEqual(stat.S_IMODE(os.stat(exe).st_mode), 0o644)

    def test_idempotent_by_exe_across_accounts_without_calling_steam(self):
        self.fake.add_entry(steam_entry(42, "Old name", self.exe, options="OLD"), account="222")
        res = self.ctx.steam.add_to("New name", self.exe, launch_options="NEW")
        self.assertEqual(res, {"appid": 42, "existed": True})
        self.assertEqual((self.fake.calls, self.ran), ([], []))
        self.assertEqual(self.fake.find(42)["AppName"], "Old name")
        self.assertEqual(self.owners(), {})  # not ours: it was there before

    def test_idempotent_on_a_second_call(self):
        first = self.ctx.steam.add_to("G", self.exe)
        calls = len(self.fake.calls)
        self.assertEqual(self.ctx.steam.add_to("G", self.exe), {"appid": first["appid"], "existed": True})
        self.assertEqual(len(self.fake.calls), calls)

    def test_lowercase_keys_written_by_others_count(self):
        self.fake.add_entry({"appid": 77, "appname": "lc", "exe": f'"{self.exe}"', "launchoptions": ""})
        self.assertEqual(self.ctx.steam.add_to("G", self.exe), {"appid": 77, "existed": True})

    def test_a_name_that_never_sticks_still_returns_the_shortcut(self):
        self.fake.apply["name"] = False
        with self.assertLogs(level="WARNING"):
            res = self.ctx.steam.add_to("G", self.exe)
        self.assertEqual(res, {"appid": 3000000000, "existed": False})

    def test_steam_that_adds_nothing_is_unavailable(self):
        self.fake.apply["add"] = False
        self.fake.functions.discard("URL.ExecuteSteamURL")
        with self.assertRaises(Unavailable):
            self.ctx.steam.add_to("G", self.exe)
        self.assertEqual(self.owners(), {})

    def test_values_reach_steam_only_as_data(self):
        exe = self.make_exe("a'); alert(1); ('\"`${alert(2)}`.sh")
        name = "x'); alert(3); (' \" ` ${alert(4)} </script>"
        res = self.ctx.steam.add_to(name, exe, launch_options="'); alert(5); ('")
        self.assertEqual(self.fake.alerts, [])
        self.assertEqual(self.fake.calls[0][1][:2], [name, exe])
        self.assertIn(("Apps.SetShortcutName", [res["appid"], name]), self.fake.calls)
        self.assertIn(("Apps.SetShortcutLaunchOptions", [res["appid"], "'); alert(5); ('"]), self.fake.calls)

    def test_a_failing_notification_is_not_fatal(self):
        def boom(*a, **k):
            raise Unavailable("no notifications")
        self.bridge.notify_sync = boom
        self.assertFalse(self.ctx.steam.add_to("G", self.exe)["existed"])


@unittest.skipUnless(NODE, "node not installed")
class AddToFallback(Base):
    def setUp(self):
        super().setUp()
        if not self.marker_attr:
            self.fail(f"steamctx has no module constant equal to {MARKER!r}: the tests can't redirect the marker")
        self.fake.functions.discard("Apps.AddShortcut")

    def desktop(self):
        self.assertEqual(len(self.fake.desktops), 1, self.fake.desktops)
        return self.fake.desktops[0]

    def test_steam_url_with_marker_and_desktop_file(self):
        res = self.ctx.steam.add_to("Fallback Game", self.exe, launch_options="X=1 %command%")
        self.assertEqual(res, {"appid": 3000000000, "existed": False})
        path, text, marker = self.desktop()
        self.assertTrue(marker, "the marker must exist when Steam reads the link")
        self.assertEqual(self.fake.urls, ["steam://addnonsteamgame/" + urllib.parse.quote(path, safe="")])
        lines = text.splitlines()
        for line in ("Type=Application", "Name=Fallback Game", "Exec=" + desktop_escape(self.exe),
                     "Path=" + os.path.dirname(self.exe), "Terminal=false"):
            self.assertIn(line, lines)
        self.assertTrue(path.startswith(str(self.tmp)), path)
        self.assertTrue(os.path.basename(os.path.dirname(path)).startswith("invasor-steam-"))
        self.assertEqual(list(self.tmp.iterdir()), [], "the temporary folder must be removed")
        self.assertFalse(self.marker.exists(), "a marker we created is removed")
        self.assertEqual(self.owners(), {"3000000000": {"module": "good", "exe": self.exe}})
        self.assertTrue(self.wait_shown())

    def test_a_marker_that_was_there_stays(self):
        self.marker.write_text("")
        self.ctx.steam.add_to("G", self.exe)
        self.assertTrue(self.marker.exists())

    def test_a_percent_in_the_path_is_not_a_field_code(self):
        exe = self.make_exe("100%f.sh")
        self.ctx.steam.add_to("G", exe)
        _, text, _ = self.desktop()
        self.assertIn("Exec=" + '"' + self.root.as_posix() + '/games/100%%f.sh"', text.splitlines())

    def test_exec_and_name_escaping(self):
        exe = self.make_exe('we"ird $HOME `id` back\\slash.sh')
        self.ctx.steam.add_to("Back\\slash & co", exe)
        _, text, _ = self.desktop()
        lines = text.splitlines()
        self.assertIn("Exec=" + desktop_escape(exe), lines)
        self.assertIn("Exec=" + '"' + self.root.as_posix() + '/games/we\\"ird \\$HOME \\`id\\` back\\\\slash.sh"', lines)
        self.assertIn("Name=Back\\\\slash & co", lines)
        self.assertEqual(self.fake.find(3000000000)["Exe"], f'"{exe}"')

    def test_the_exe_gets_u_x_only_without_any_exec_bit(self):
        plain = self.make_exe("plain.sh", 0o644)
        with self.assertLogs(level="INFO"):
            self.ctx.steam.add_to("Plain", plain)
        self.assertEqual(stat.S_IMODE(os.stat(plain).st_mode), 0o744)
        self.fake.next_appid += 1
        group = self.make_exe("group.sh", 0o654)
        self.ctx.steam.add_to("Group", group)
        self.assertEqual(stat.S_IMODE(os.stat(group).st_mode), 0o654)

    def test_after_add_shortcut_fails_too(self):
        self.fake.functions.add("Apps.AddShortcut")
        self.fake.throws.add("Apps.AddShortcut")
        res = self.ctx.steam.add_to("G", self.exe)
        self.assertEqual(res["existed"], False)
        self.assertEqual(len(self.fake.desktops), 1)

    def test_without_execute_steam_url_gio_or_xdg_open(self):
        self.fake.functions.discard("URL.ExecuteSteamURL")
        self.tools = {"gio", "xdg-open"}
        self.failing["gio"] = 2
        res = self.ctx.steam.add_to("G", self.exe)
        self.assertFalse(res["existed"])
        self.assertEqual([os.path.basename(a[0]) for a, _ in self.ran], ["gio", "xdg-open"])
        self.assertTrue(self.ran[-1][0][-1].startswith("steam://addnonsteamgame/"))
        self.assertEqual(self.ran[-1][1]["env"]["WAYLAND_DISPLAY"], "wayland-test")
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_nothing_appears_is_unavailable_and_cleans_up(self):
        self.fake.apply["add"] = False
        with self.assertRaises(Unavailable):
            self.ctx.steam.add_to("G", self.exe)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.owners(), {})

    def test_no_way_to_hand_steam_the_link_is_unavailable(self):
        self.fake.functions.discard("URL.ExecuteSteamURL")
        with self.assertRaises(Unavailable):
            self.ctx.steam.add_to("G", self.exe)
        self.assertEqual(list(self.tmp.iterdir()), [])


# --- remove_shortcut ------------------------------------------------------------------------------

class RemoveShortcut(Base):
    def added(self, ctx=None, name="G", exe=None):
        return (ctx or self.ctx).steam.add_to(name, exe or self.exe)["appid"]

    def test_removes_its_own_shortcut(self):
        appid = self.added()
        self.shown.clear()
        with self.assertLogs(level="INFO") as logs:
            self.assertEqual(self.ctx.steam.remove_shortcut(appid), {"removed": True})
        self.assertEqual(self.fake.calls[-1], ("Apps.RemoveShortcut", [appid]))
        self.assertIsNone(self.fake.find(appid))
        self.assertEqual(self.owners(), {})
        self.assertIn(str(appid), "\n".join(logs.output))
        self.assertTrue(self.wait_shown())

    def test_appid_as_a_string(self):
        appid = self.added()
        self.ctx.steam.remove_shortcut(str(appid))
        self.assertEqual(self.fake.calls[-1], ("Apps.RemoveShortcut", [appid]))

    def test_another_modules_shortcut_is_refused(self):
        other = self.make_ctx("other")
        appid = self.added(ctx=other)
        calls = len(self.fake.calls)
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.remove_shortcut(appid)
        self.assertNotIn("Apps.RemoveShortcut", self.fake.paths()[calls:])
        self.assertIsNotNone(self.fake.find(appid))
        self.assertIn(str(appid), self.owners())
        other.steam.remove_shortcut(appid)  # its owner can

    def test_a_shortcut_not_added_through_add_to_is_refused(self):
        self.fake.add_entry(steam_entry(4000000000, "User's", self.exe))
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.remove_shortcut(4000000000)
        self.assertEqual(self.fake.calls, [])

    def test_a_reused_appid_with_another_exe_is_refused(self):
        appid = self.added()
        entry = self.fake.find(appid)
        entry["Exe"] = '"/home/deck/something/else.sh"'
        self.fake.save()
        calls = len(self.fake.calls)
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.remove_shortcut(appid)
        self.assertNotIn("Apps.RemoveShortcut", self.fake.paths()[calls:])

    def test_a_steam_game_is_refused(self):
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.remove_shortcut(GAME)
        self.assertNotIn("Apps.RemoveShortcut", self.fake.paths())

    def test_a_forged_record_for_a_steam_game_is_still_refused(self):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "steam-shortcuts.json").write_text(json.dumps({GAME: {"module": "good", "exe": self.exe}}))
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.remove_shortcut(GAME)
        self.assertNotIn("Apps.RemoveShortcut", self.fake.paths())

    def test_bad_appids(self):
        for bad in (True, False, 0, -1, 2**32, "", "12a", "+5", " 5", "5 ", "-5", "١٢", 1.0, None, [5]):
            with self.subTest(appid=bad), self.assertRaises(InvalidArgument):
                self.ctx.steam.remove_shortcut(bad)
        self.assertEqual(self.fake.calls, [])

    def test_steam_too_slow_keeps_the_record(self):
        appid = self.added()
        self.fake.apply["remove"] = False
        with self.assertRaises(Unavailable):
            self.ctx.steam.remove_shortcut(appid)
        self.assertIn(str(appid), self.owners())

    def test_no_remove_function_is_unavailable(self):
        appid = self.added()
        self.fake.functions.discard("Apps.RemoveShortcut")
        with self.assertRaises(Unavailable):
            self.ctx.steam.remove_shortcut(appid)
        self.assertIn(str(appid), self.owners())

    def test_the_record_is_not_the_module_s_data(self):
        self.added()
        module_dir = self.root / "module-data" / "good"
        self.assertFalse(any(p.name == "steam-shortcuts.json" for p in module_dir.rglob("*")) if module_dir.exists() else False)
        self.assertTrue((self.data / "steam-shortcuts.json").is_file())


# --- launch options -------------------------------------------------------------------------------

class LaunchOptions(Base):
    def test_a_shortcut_is_read_from_shortcuts_vdf(self):
        self.fake.add_entry(steam_entry(4000000000, "S", self.exe, options="A=1 %command%"))
        self.fake.details["4000000000"] = "stale"  # what RegisterForAppDetails would lag with
        self.assertEqual(self.ctx.steam.launch_options(4000000000), "A=1 %command%")
        self.assertEqual(self.ctx.steam.launch_options("4000000000"), "A=1 %command%")
        self.fake.add_entry({"appid": 9, "AppName": "no options", "Exe": '"/x"'})
        self.assertEqual(self.ctx.steam.launch_options(9), "")
        self.assertNotIn("Apps.RegisterForAppDetails", self.fake.paths())

    def test_a_steam_game_through_app_details(self):
        self.assertEqual(self.ctx.steam.launch_options(GAME), "-novid")
        self.assertEqual(self.fake.calls[-1], ("Apps.RegisterForAppDetails", [int(GAME), "<fn>"]))
        self.fake.details["10"] = ""
        self.assertEqual(self.ctx.steam.launch_options(10), "")

    def test_unknown_app_is_invalid(self):
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.launch_options(123456)

    def test_timeout_or_missing_function_is_unavailable(self):
        self.fake.details_mode = "never"
        with self.assertRaises(Unavailable):
            self.ctx.steam.launch_options(GAME)
        self.fake.details_mode = "answer"
        self.fake.functions.discard("Apps.RegisterForAppDetails")
        with self.assertRaises(Unavailable):
            self.ctx.steam.launch_options(GAME)
        self.fake.unreachable = True
        with self.assertRaises(Unavailable):
            self.ctx.steam.launch_options(GAME)

    def test_bad_appids(self):
        for bad in (True, 0, -1, 2**32, "abc", "1e3", 1.5, None):
            with self.subTest(appid=bad), self.assertRaises(InvalidArgument):
                self.ctx.steam.launch_options(bad)
        self.assertEqual(self.fake.expressions, [])

    def test_the_appid_is_data_in_the_expression(self):
        self.ctx.steam.launch_options(GAME)
        expr = self.fake.expressions[-1]
        self.assertIn("RegisterForAppDetails", expr)
        self.assertIn(GAME, expr)
        self.assertEqual(self.fake.alerts, [])


class SetLaunchOptions(Base):
    def shortcut(self, options="OLD"):
        self.fake.add_entry(steam_entry(4000000000, "My Shortcut", self.exe, options=options))
        return 4000000000

    def test_a_shortcut_verified_on_disk(self):
        appid = self.shortcut()
        with self.assertLogs(level="INFO") as logs:
            res = self.ctx.steam.set_launch_options(appid, "NEW=1 %command%")
        self.assertEqual(res, {"applied": True, "verified": True})
        self.assertIn(("Apps.SetShortcutLaunchOptions", [appid, "NEW=1 %command%"]), self.fake.calls)
        self.assertNotIn("Apps.SetAppLaunchOptions", self.fake.paths())
        self.assertEqual(self.fake.find(appid)["LaunchOptions"], "NEW=1 %command%")
        text = "\n".join(logs.output)
        self.assertIn("OLD", text)
        self.assertIn("NEW=1 %command%", text)
        self.assertIn("good", text)
        self.assertTrue(self.wait_shown())

    def test_a_shortcut_steam_ignores_is_unavailable(self):
        appid = self.shortcut()
        self.fake.apply["options"] = False
        with self.assertRaises(Unavailable):
            self.ctx.steam.set_launch_options(appid, "NEW")

    def test_clearing_a_shortcut_s_options(self):
        appid = self.shortcut()
        self.assertEqual(self.ctx.steam.set_launch_options(appid, ""), {"applied": True, "verified": True})
        self.assertEqual(self.fake.find(appid)["LaunchOptions"], "")

    def test_a_steam_game_verified(self):
        with self.assertLogs(level="INFO") as logs:
            res = self.ctx.steam.set_launch_options(GAME, "gamemoderun %command%")
        self.assertEqual(res, {"applied": True, "verified": True})
        self.assertIn(("Apps.SetAppLaunchOptions", [int(GAME), "gamemoderun %command%"]), self.fake.calls)
        self.assertEqual(self.fake.details[GAME], "gamemoderun %command%")
        text = "\n".join(logs.output)
        self.assertIn("-novid", text)
        self.assertIn("gamemoderun %command%", text)
        self.assertTrue(self.wait_shown())

    def test_a_steam_game_read_back_timing_out_is_unverified(self):
        self.fake.after_set_mode = "never"
        self.assertEqual(self.ctx.steam.set_launch_options(GAME, "X"), {"applied": True, "verified": False})

    def test_a_steam_game_with_another_value_after_is_unavailable(self):
        self.fake.set_app_to = "something else"
        with self.assertRaises(Unavailable):
            self.ctx.steam.set_launch_options(GAME, "X")

    def test_unknown_appid_is_refused_before_writing(self):
        with self.assertRaises(InvalidArgument):
            self.ctx.steam.set_launch_options(555, "X")
        self.assertNotIn("Apps.SetAppLaunchOptions", self.fake.paths())
        self.assertNotIn("Apps.SetShortcutLaunchOptions", self.fake.paths())

    def test_bad_lines_and_appids_never_reach_steam(self):
        appid = self.shortcut()
        for line in ("a\nb", "a\rb", "a\x00b", "x" * 4097, None, 5, b"x"):
            with self.subTest(line=line), self.assertRaises(InvalidArgument):
                self.ctx.steam.set_launch_options(appid, line)
        for bad in (True, 0, 2**32, "x", None):
            with self.subTest(appid=bad), self.assertRaises(InvalidArgument):
                self.ctx.steam.set_launch_options(bad, "X")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.ctx.steam.set_launch_options(appid, "y" * 4096)["applied"], True)

    def test_the_line_is_data(self):
        appid = self.shortcut()
        line = "'); alert(1); (' \" ` ${alert(2)}"
        self.ctx.steam.set_launch_options(appid, line)
        self.ctx.steam.set_launch_options(GAME, line)
        self.assertEqual(self.fake.alerts, [])
        self.assertEqual(self.fake.find(appid)["LaunchOptions"], line)
        self.assertEqual(self.fake.details[GAME], line)

    def test_missing_functions_are_unavailable(self):
        appid = self.shortcut()
        self.fake.functions.discard("Apps.SetShortcutLaunchOptions")
        with self.assertRaises(Unavailable):
            self.ctx.steam.set_launch_options(appid, "X")
        self.fake.functions.discard("Apps.SetAppLaunchOptions")
        with self.assertRaises(Unavailable):
            self.ctx.steam.set_launch_options(GAME, "X")

    def test_a_failing_notification_is_not_fatal(self):
        def boom(*a, **k):
            raise RuntimeError("no notifications")
        self.bridge.notify_sync = boom
        self.assertTrue(self.ctx.steam.set_launch_options(GAME, "X")["verified"])


# --- the rules every method follows ---------------------------------------------------------------

class Rules(Base):
    def calls(self):
        appid = 4000000000
        self.fake.add_entry(steam_entry(appid, "S", self.exe))
        return [
            ("supports", ("add_to",), {}),
            ("open_url", ("https://example.org",), {}),
            ("add_to", ("G", self.make_exe("other.sh")), {}),
            ("remove_shortcut", (appid,), {}),
            ("launch_options", (GAME,), {}),
            ("set_launch_options", (GAME, "X"), {}),
        ]

    def test_an_unloaded_module_gets_unavailable(self):
        calls = self.calls()
        self.ctx._dead = True
        for name, args, kw in calls:
            with self.subTest(name=name), self.assertRaises(Unavailable):
                getattr(self.ctx.steam, name)(*args, **kw)
        self.assertEqual((self.fake.calls, self.fake.expressions, self.ran), ([], [], []))

    def test_no_bridge_is_unavailable(self):
        calls = self.calls()
        from invasor.modules import ModuleContext
        ctx = ModuleContext("good", self.root / "modules" / "good", GameContext(), None, steam=None, tasks=TaskRegistry())
        for name, args, kw in calls:
            if name == "supports":
                continue  # Steam not reachable: False or Unavailable, both fine
            with self.subTest(name=name), self.assertRaises(Unavailable):
                getattr(ctx.steam, name)(*args, **kw)

    def test_on_the_event_loop_it_is_unavailable(self):
        calls = self.calls()

        async def on_loop():
            for name, args, kw in calls:
                with self.subTest(name=name), self.assertRaises(Unavailable):
                    getattr(self.ctx.steam, name)(*args, **kw)
        asyncio.run(on_loop())
        self.assertEqual(self.fake.calls, [])

    def test_async_twins(self):
        for name, _, _ in self.calls():
            self.assertTrue(callable(getattr(self.ctx.steam, f"{name}_async", None)), name)

        async def go():
            self.assertTrue(await self.ctx.steam.supports_async("launch_options"))
            self.assertEqual(await self.ctx.steam.launch_options_async(GAME), "-novid")
            res = await self.ctx.steam.add_to_async("Async", self.make_exe("async.sh"), launch_options="A=1")
            self.assertFalse(res["existed"])
            self.assertEqual(await self.ctx.steam.set_launch_options_async(res["appid"], "B=2"),
                             {"applied": True, "verified": True})
            self.assertEqual(await self.ctx.steam.remove_shortcut_async(res["appid"]), {"removed": True})
            self.assertIsNone(await self.ctx.steam.open_url_async("https://example.org"))
            with self.assertRaises(InvalidArgument):
                await self.ctx.steam.open_url_async("steam://x")
        asyncio.run(go())

    def test_steam_is_a_module_steam_and_the_bridge_stays_private(self):
        self.assertIsInstance(self.ctx.steam, steamctx.ModuleSteam)
        self.assertIs(self.ctx._steam, self.bridge)


# --- steam_language -------------------------------------------------------------------------------

class SteamLanguage(Base):
    def setUp(self):
        super().setUp()
        self.registry = self.root / "registry.vdf"
        patched = False
        for mod in ("invasor.steamctx", "invasor.modules"):
            module = __import__(mod, fromlist=["x"])
            if hasattr(module, "REGISTRY"):
                patch(self, f"{mod}.REGISTRY", self.registry)
                patched = True
        self.assertTrue(patched, "no REGISTRY constant to patch")

    def write(self, language, keys=("Registry", "HKCU", "Software", "Valve", "Steam", "language")):
        text = ""
        for k in keys[:-1]:
            text += f'"{k}"\n{{\n'
        text += f'"{keys[-1]}" "{language}"\n' if language is not None else '"other" "x"\n'
        text += "}\n" * (len(keys) - 1)
        self.registry.write_text(text)

    def test_codes(self):
        for raw, code in (("english", "en"), ("spanish", "es"), ("latam", "es"), ("koreana", "ko"),
                          ("schinese", "zh"), ("tchinese", "zh"), ("brazilian", "pt"), ("german", "de"),
                          ("ukrainian", "uk"), ("klingon", "en"), ("", "en")):
            with self.subTest(raw=raw):
                self.write(raw)
                self.assertEqual(self.ctx.steam_language(), code)
                self.assertEqual(self.ctx.steam_language(raw=True), raw)

    def test_keys_are_case_insensitive(self):
        self.write("spanish", keys=("registry", "hkcu", "software", "valve", "steam", "Language"))
        self.assertEqual(self.ctx.steam_language(), "es")
        self.assertEqual(self.ctx.steam_language(raw=True), "spanish")

    def test_missing_or_unreadable(self):
        self.assertEqual((self.ctx.steam_language(), self.ctx.steam_language(raw=True)), ("en", ""))
        self.write(None)
        self.assertEqual((self.ctx.steam_language(), self.ctx.steam_language(raw=True)), ("en", ""))
        self.registry.write_text("garbage { { \"")
        self.assertEqual(self.ctx.steam_language(), "en")
        self.registry.unlink()
        self.registry.mkdir()
        self.assertEqual((self.ctx.steam_language(), self.ctx.steam_language(raw=True)), ("en", ""))

    def test_no_steam_call(self):
        self.write("spanish")
        self.fake.unreachable = True
        self.assertEqual(self.ctx.steam_language(), "es")
        self.assertEqual(self.fake.expressions, [])


# --- the graphical session ------------------------------------------------------------------------

class SessionEnv(unittest.TestCase):
    def setUp(self):
        mod = steamctx if hasattr(steamctx, "session_env") else __import__("invasor.session", fromlist=["x"])
        self.session_env, self.vars = mod.session_env, mod.SESSION_VARS
        self.proc = temp_dir(self)

    def proc_entry(self, pid, comm, env):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")

    def test_session_env(self):
        self.assertEqual(self.session_env(self.proc), {})
        self.proc_entry(10, "bash", {"HOME": "/h", "XDG_RUNTIME_DIR": "/run/user/1000"})
        self.assertEqual(self.session_env(self.proc), {})  # no display: not graphical
        self.proc_entry(11, "plasmashell", {"WAYLAND_DISPLAY": "wayland-1", "XDG_CURRENT_DESKTOP": "KDE", "X": "y"})
        self.assertEqual(self.session_env(self.proc), {"WAYLAND_DISPLAY": "wayland-1", "XDG_CURRENT_DESKTOP": "KDE"})
        self.proc_entry(12, "steamwebhelper", {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/b", "SECRET": "s"})
        self.assertEqual(self.session_env(self.proc), {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/b"})
        self.assertEqual(self.session_env(self.proc / "missing"), {})

    def test_steam_wins_and_only_session_vars(self):
        self.proc_entry(5, "steam", {"WAYLAND_DISPLAY": "wayland-0", "LD_PRELOAD": "/evil.so", "PATH": "/x"})
        self.proc_entry(6, "kwin_wayland", {"WAYLAND_DISPLAY": "wayland-9"})
        (self.proc / "self").mkdir()
        (self.proc / "77").mkdir()  # unreadable / gone
        env = self.session_env(self.proc)
        self.assertEqual(env, {"WAYLAND_DISPLAY": "wayland-0"})
        self.assertTrue(set(env) <= set(self.vars))
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            self.assertIn(name, self.vars)
        self.assertNotIn("LD_PRELOAD", self.vars)


if __name__ == "__main__":
    unittest.main()
