"""`ctx.steam`: the few changes a module may make to the user's Steam - add a non-Steam shortcut,
remove one it added, read and set launch options, open a web page in Steam - and
`ctx.steam_language()`. Plain methods block (call them from a plain, threaded method); each has an
`_async` twin for `async def`.

STEAM TOUCHPOINT (see docs/API-Steam.md): SteamClient.Apps.* and URL.ExecuteSteamURL over the
bridge, the steam://addnonsteamgame link as a fallback, and shortcuts.vdf to see what Steam did.
Whatever changes Steam is done on a user action only, and always leaves an INFO line in the log and
a Steam notification.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from urllib.parse import urlsplit

from . import config, paths, vdf
from .schema import InvalidArgument, Unavailable
from .steam import notify_later
from .storage import JsonStore

log = logging.getLogger("invasor.steam")

REGISTRY = Path.home() / ".steam/registry.vdf"
# Without this file Steam shows a dialog (or does nothing) for a steam://addnonsteamgame link.
MARKER = "/tmp/addnonsteamgamefile"
POLL = 0.5
MAX_URL, MAX_NAME, MAX_LINE = 2048, 256, 4096
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
BAD_LINE = re.compile(r"[\r\n\0]")
OPENERS = (("gio", "open"), ("xdg-open",))
DETAILS_TIMEOUT = 5000  # ms, on the JS side

# Each name: the sets of SteamClient functions that make it work (any complete set will do).
SUPPORTS = {
    "add_to": (("Apps.AddShortcut", "Apps.SetShortcutName"), ("URL.ExecuteSteamURL",)),
    "open_url": (("URL.ExecuteSteamURL",),),
    "remove_shortcut": (("Apps.RemoveShortcut",),),
    "launch_options": (("Apps.RegisterForAppDetails",),),
    "set_launch_options": (("Apps.SetAppLaunchOptions", "Apps.SetShortcutLaunchOptions", "Apps.RegisterForAppDetails"),),
}

TYPEOF_JS = """(() => {
  const out = {};
  for (const name of %s) {
    let fn = window.SteamClient;
    for (const key of name.split('.')) fn = fn == null ? undefined : fn[key];
    out[name] = typeof fn === 'function';
  }
  return out;
})()"""

# The user's launch options of a Steam game: the first answer of RegisterForAppDetails.
DETAILS_JS = """new Promise(resolve => {
  const apps = window.SteamClient && window.SteamClient.Apps;
  if (!apps || typeof apps.RegisterForAppDetails !== 'function') return resolve({missing: true});
  let reg = null, done = false;
  const stop = () => { try { if (reg && reg.unregister) reg.unregister(); } catch (e) {} };
  const finish = value => { if (done) return; done = true; clearTimeout(timer); stop(); resolve(value); };
  const timer = setTimeout(() => finish({timeout: true}), %(ms)d);
  reg = apps.RegisterForAppDetails(%(appid)s, d => finish(d && typeof d.strLaunchOptions === 'string' ? d.strLaunchOptions : null));
  if (done) stop();
})"""

LANGUAGES = {
    "english": "en", "spanish": "es", "latam": "es", "german": "de", "french": "fr", "italian": "it",
    "portuguese": "pt", "brazilian": "pt", "russian": "ru", "polish": "pl", "dutch": "nl", "swedish": "sv",
    "danish": "da", "finnish": "fi", "norwegian": "no", "turkish": "tr", "czech": "cs", "hungarian": "hu",
    "romanian": "ro", "ukrainian": "uk", "greek": "el", "bulgarian": "bg", "japanese": "ja", "koreana": "ko",
    "schinese": "zh", "tchinese": "zh", "thai": "th", "vietnamese": "vi", "indonesian": "id", "arabic": "ar",
}

# The graphical session's variables: the Invasor service doesn't have them, and gio/xdg-open need
# them to reach the running Steam.
SESSION_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
                "XDG_RUNTIME_DIR", "XDG_DATA_DIRS", "DBUS_SESSION_BUS_ADDRESS")

_unreadable = set()  # shortcuts.vdf files already reported


def language(raw=False):
    """Steam's language: a short code ("en", "es"...; "en" when unknown), or raw=True, as Steam
    names it ("" when unknown)."""
    try:
        value = vdf.get(vdf.loads_text(REGISTRY.read_text(errors="replace")),
                        "Registry", "HKCU", "Software", "Valve", "Steam", "language", default="")
    except (OSError, vdf.VDFError):
        value = ""
    value = value if isinstance(value, str) else ""
    return value if raw else LANGUAGES.get(value.lower(), "en")


def _environ(pid_dir):
    try:
        raw = (pid_dir / "environ").read_bytes()
    except OSError:
        return {}
    pairs = (item.partition(b"=") for item in raw.split(b"\0") if b"=" in item)
    return {k.decode(errors="replace"): v.decode(errors="replace") for k, _, v in pairs}


def session_env(proc_root="/proc"):
    """The session variables of the user's running Steam (else of any of their graphical processes)."""
    steam, other = {}, {}
    try:
        pids = [d for d in Path(proc_root).iterdir() if d.name.isdigit()]
    except OSError:
        return {}
    for d in pids:
        env = _environ(d)
        if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
            continue
        try:
            comm = (d / "comm").read_text().strip()
        except OSError:
            comm = ""
        picked = {k: env[k] for k in SESSION_VARS if k in env}
        if comm in ("steam", "steamwebhelper"):
            steam = steam or picked
        elif not other:
            other = picked
        if steam:
            break
    return steam or other


def _open_with_desktop(link):
    """Open link with gio, else xdg-open, in the graphical session. Unavailable if neither works."""
    env = {**os.environ, **session_env()}
    for argv in OPENERS:
        if shutil.which(argv[0]) is None:
            continue
        try:
            done = subprocess.run([*argv, link], env=env, timeout=10, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("%s failed: %s", argv[0], e)
            continue
        if done.returncode == 0:
            log.debug("opened with %s", argv[0])
            return
    raise Unavailable("neither Steam nor gio/xdg-open could open it")


def _text(value):
    return value if isinstance(value, str) else ""


def _shortcuts():
    """{appid: {"name", "exe", "options", "start_dir"}} of every account's non-Steam shortcuts."""
    out = {}
    for account in paths.userdata(all=True):
        f = account / "config" / "shortcuts.vdf"
        try:
            data = vdf.loads_binary(f.read_bytes())
        except FileNotFoundError:
            continue
        except (OSError, vdf.VDFError) as e:
            if str(f) not in _unreadable:
                _unreadable.add(str(f))
                log.debug("can't read %s: %s", f, e)
            continue
        entries = vdf.get(data, "shortcuts", default={})
        for entry in (entries.values() if isinstance(entries, dict) else ()):
            appid = vdf.get(entry, "appid")
            if isinstance(appid, int) and not isinstance(appid, bool):
                out[str(appid & 0xFFFFFFFF)] = {
                    "name": _text(vdf.get(entry, "AppName")),
                    "exe": _text(vdf.get(entry, "Exe")).strip().strip('"'),
                    "options": _text(vdf.get(entry, "LaunchOptions")),
                    "start_dir": _text(vdf.get(entry, "StartDir")).strip().strip('"'),
                }
    return out


def _wait_for(check, seconds):
    """check() every POLL seconds until it gives something truthy (returned), or None after `seconds`."""
    deadline = time.monotonic() + seconds
    while True:
        found = check()
        if found or time.monotonic() >= deadline:
            return found or None
        time.sleep(POLL)


def _plain(value, what, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or CONTROL.search(value):
        raise InvalidArgument(f"{what} must be plain text of 1 to {limit} characters")
    return value.strip()


def _line(line):
    if not isinstance(line, str) or len(line) > MAX_LINE or BAD_LINE.search(line):
        raise InvalidArgument(f"launch options must be one line of up to {MAX_LINE} characters")
    return line


def _url(url):
    if not isinstance(url, str) or len(url) > MAX_URL or CONTROL.search(url) \
            or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise InvalidArgument(f"not an http(s) URL (up to {MAX_URL} characters): {str(url)[:100]!r}")
    return url


def _desktop_value(text, quote=False):
    if quote:
        for ch in '\\"$`':
            text = text.replace(ch, "\\" + ch)
        return f'"{text}"'
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


class ModuleSteam:
    """`ctx.steam`. Only on a user action (a button), never on its own: Steam is the user's."""

    def __init__(self, ctx):
        self._ctx = ctx

    # ---------- what works ----------
    def supports(self, name):
        """Whether `name` ("add_to", "open_url", "remove_shortcut", "launch_options",
        "set_launch_options") can work with this Steam. False when Steam isn't reachable."""
        self._check_call("supports")
        if not isinstance(name, str) or name not in SUPPORTS:
            raise InvalidArgument(f"supports(): one of {', '.join(SUPPORTS)}")
        if name == "open_url" and any(shutil.which(argv[0]) for argv in OPENERS):
            return True
        found = self._functions(sorted({f for group in SUPPORTS[name] for f in group}))
        return any(all(found.get(f) for f in group) for group in SUPPORTS[name])

    def _functions(self, names):
        try:
            found = self._ctx._steam.run_sync(TYPEOF_JS % json.dumps(names), timeout=10)
        except Unavailable:
            return {}
        return found if isinstance(found, dict) else {}

    # ---------- web pages ----------
    def open_url(self, url):
        """Open the http(s) `url` in Steam's browser (else the desktop's). Unavailable if neither can."""
        self._check_call("open_url")
        url = _url(url)
        try:
            self._ctx._steam.call_sync("URL.ExecuteSteamURL", "steam://openurl/" + url)
            log.debug("%s opened %s in Steam", self._ctx.id, url)
        except Unavailable as e:
            log.debug("Steam can't open it (%s): trying the desktop", e)
            _open_with_desktop("steam://openurl/" + url)  # still for Steam: the desktop's browser isn't seen in Game Mode

    # ---------- shortcuts ----------
    def add_to(self, name, exe, *, start_dir=None, launch_options=None):
        """Add `exe` (an absolute path to a file) to Steam as a non-Steam game called `name`:
        {"appid", "existed"}. A shortcut to that exe already there is left as it is (existed True)."""
        self._check_call("add_to")
        name = _plain(name, "name", MAX_NAME)
        if not isinstance(exe, str) or not os.path.isabs(exe) or not os.path.isfile(exe):
            raise InvalidArgument("exe must be the absolute path of a file that exists")
        if start_dir is None:
            start_dir = os.path.dirname(exe)
        elif not isinstance(start_dir, str) or not os.path.isabs(start_dir) or not os.path.isdir(start_dir):
            raise InvalidArgument("start_dir must be the absolute path of a folder that exists")
        if launch_options is not None:
            _line(launch_options)
        same = [a for a, s in _shortcuts().items() if s["exe"] == exe]
        if same:
            return {"appid": int(same[0]), "existed": True}
        appid = self._add_principal(name, exe, start_dir, launch_options)
        if appid is None:
            # AddShortcut may have run but not answered in time: never add it twice.
            appid = next((a for a, s in _shortcuts().items() if s["exe"] == exe), None) \
                or self._add_fallback(name, exe, start_dir)
        _owners().transform(lambda d: {**d, appid: {"module": self._ctx.id, "exe": exe}})
        log.info("%s added %s to Steam as %r (appid %s)", self._ctx.id, exe, name, appid)
        self._notify("Added to Steam", name)
        return {"appid": int(appid), "existed": False}

    def _add_principal(self, name, exe, start_dir, options):
        """The appid (str) SteamClient.Apps.AddShortcut gave, or None if it isn't there."""
        call = self._ctx._steam.call_sync
        try:
            appid = call("Apps.AddShortcut", name, exe, start_dir, options or "")
        except Unavailable as e:
            log.debug("AddShortcut unavailable (%s): using the steam:// link", e)
            return None
        if isinstance(appid, bool) or not isinstance(appid, int) or appid <= 0:
            raise Unavailable("Steam didn't give the new shortcut's appid")
        appid = appid & 0xFFFFFFFF
        # AddShortcut ignores name and options: these set them.
        call("Apps.SetShortcutName", appid, name)
        call("Apps.SetShortcutStartDir", appid, start_dir)
        if options:
            call("Apps.SetShortcutLaunchOptions", appid, options)
        appid = str(appid)
        def mine():
            s = _shortcuts().get(appid)
            return s if s and s["exe"] == exe else None
        def done():
            s = mine()
            return s if s and s["name"] == name and (not options or s["options"] == options) else None
        if _wait_for(done, 5) is None:
            if _wait_for(mine, 0) is None:
                raise Unavailable("Steam didn't add the shortcut in time")
            log.warning("Steam added %s (appid %s) but not with the name/options asked", exe, appid)
        return appid

    def _add_fallback(self, name, exe, start_dir):
        """The steam://addnonsteamgame link with a temporary .desktop file: the appid (str)."""
        mode = os.stat(exe).st_mode
        if not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            # Steam reads Name/Exec from the .desktop only when its target is executable.
            os.chmod(exe, mode | stat.S_IXUSR)
            log.info("%s: made %s executable for Steam", self._ctx.id, exe)
        folder = tempfile.mkdtemp(prefix="invasor-steam-")
        made_marker = False
        try:
            desktop = os.path.join(folder, "invasor-shortcut.desktop")
            with open(desktop, "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName={_desktop_value(name)}\n"
                        f"Exec={_desktop_value(exe, quote=True)}\nPath={_desktop_value(start_dir)}\nTerminal=false\n")
            try:  # never through a link someone else left in /tmp
                os.close(os.open(MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
                made_marker = True
            except FileExistsError:
                pass
            link = "steam://addnonsteamgame/" + urllib.parse.quote(desktop, safe="")
            try:
                self._ctx._steam.call_sync("URL.ExecuteSteamURL", link)
            except Unavailable:
                _open_with_desktop(link)
            found = _wait_for(lambda: next((a for a, s in _shortcuts().items() if s["exe"] == exe), None), 15)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            if made_marker:
                try:
                    os.unlink(MARKER)
                except OSError:
                    pass
        if found is None:
            raise Unavailable("Steam didn't add the shortcut in time")
        return found

    def remove_shortcut(self, appid):
        """Remove a non-Steam shortcut this module added: {"removed": True}."""
        self._check_call("remove_shortcut")
        appid = paths.appid_str(appid)
        shortcut = _shortcuts().get(appid)
        if shortcut is None:
            raise InvalidArgument(f"{appid} is not a non-Steam shortcut")
        owner = _owners().load().get(appid)
        if not isinstance(owner, dict) or owner.get("module") != self._ctx.id or owner.get("exe") != shortcut["exe"]:
            raise InvalidArgument("this module didn't add that shortcut")
        self._ctx._steam.call_sync("Apps.RemoveShortcut", int(appid))
        if _wait_for(lambda: appid not in _shortcuts(), 5) is None:
            raise Unavailable("Steam didn't remove it in time")
        _owners().transform(lambda d: {k: v for k, v in d.items() if k != appid})
        log.info("%s removed the shortcut %r (appid %s) from Steam", self._ctx.id, shortcut["name"], appid)
        self._notify("Removed from Steam", shortcut["name"] or appid)
        return {"removed": True}

    # ---------- launch options ----------
    def launch_options(self, appid):
        """The launch options the user has for a game or shortcut ("" when none)."""
        self._check_call("launch_options")
        appid = paths.appid_str(appid)
        shortcut = _shortcuts().get(appid)
        return shortcut["options"] if shortcut else self._read_details(appid)

    def _read_details(self, appid):
        res = self._ctx._steam.run_sync(DETAILS_JS % {"ms": DETAILS_TIMEOUT, "appid": json.dumps(int(appid))}, timeout=10)
        if isinstance(res, str):
            return res
        if res is None:
            raise InvalidArgument("Steam doesn't know that app")
        if isinstance(res, dict) and res.get("missing"):
            raise Unavailable("SteamClient.Apps.RegisterForAppDetails isn't available")
        raise Unavailable("Steam didn't give the app's details in time")

    def set_launch_options(self, appid, line):
        """Set the launch options of a game or shortcut: {"applied": True, "verified"}; verified is
        False when Steam took them but couldn't be read back."""
        self._check_call("set_launch_options")
        appid, line = paths.appid_str(appid), _line(line)
        call = self._ctx._steam.call_sync
        shortcut = _shortcuts().get(appid)
        if shortcut is not None:
            old = shortcut["options"]
            call("Apps.SetShortcutLaunchOptions", int(appid), line)
            # It never fails, even for a bogus appid: what Steam writes is the answer.
            if _wait_for(lambda: (_shortcuts().get(appid) or {}).get("options") == line, 3) is None:
                raise Unavailable("Steam didn't apply the launch options")
            verified = True
        else:
            old = self._read_details(appid)  # unknown app -> InvalidArgument before writing
            call("Apps.SetAppLaunchOptions", int(appid), line)
            seen = []
            def check():
                try:
                    seen.append(self._read_details(appid))
                except (Unavailable, InvalidArgument):
                    seen.append(None)
                return seen[-1] == line
            verified = bool(_wait_for(check, 3))
            if not verified and seen[-1] is not None:
                raise Unavailable("Steam didn't apply the launch options")
        game = shortcut["name"] if shortcut else self._game_name(appid)
        log.info("%s changed the launch options of %s (appid %s): %r -> %r", self._ctx.id, game, appid, old, line)
        self._notify("Launch options changed", game)
        return {"applied": True, "verified": verified}

    def _game_name(self, appid):
        try:
            return (self._ctx.game.info(appid) or {}).get("name") or appid
        except Exception:
            return appid

    # ---------- async twins ----------
    async def supports_async(self, name):
        """The same as supports(), for `async def` methods."""
        return await asyncio.to_thread(self.supports, name)

    async def open_url_async(self, url):
        """The same as open_url(), for `async def` methods."""
        return await asyncio.to_thread(self.open_url, url)

    async def add_to_async(self, name, exe, **options):
        """The same as add_to(), for `async def` methods."""
        return await asyncio.to_thread(self.add_to, name, exe, **options)

    async def remove_shortcut_async(self, appid):
        """The same as remove_shortcut(), for `async def` methods."""
        return await asyncio.to_thread(self.remove_shortcut, appid)

    async def launch_options_async(self, appid):
        """The same as launch_options(), for `async def` methods."""
        return await asyncio.to_thread(self.launch_options, appid)

    async def set_launch_options_async(self, appid, line):
        """The same as set_launch_options(), for `async def` methods."""
        return await asyncio.to_thread(self.set_launch_options, appid, line)

    # ---------- internals ----------
    def _check_call(self, name):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise Unavailable(f"ctx.steam.{name}() waits for Steam: call it from a plain (threaded) method, "
                              f"or use {name}_async() in `async def`")
        if self._ctx._dead:
            raise Unavailable("this module was unloaded: it can't change Steam anymore")
        if self._ctx._steam is None:
            raise Unavailable("SteamClient bridge not configured")

    def _notify(self, title, body):
        notify_later(self._ctx._steam, title, body, self._ctx.log, "steam", "invasor-steam-notify")


def _owners():
    """{appid: {"module", "exe"}}: who added which shortcut. Core data, out of every module's reach."""
    return JsonStore(config.DATA_DIR / "steam-shortcuts.json")
