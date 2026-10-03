"""Which game is selected in the library and which one is running.

Deliberately avoids Steam UI internals:
- selected: the route SharedJSContext exposes in its URL on the CEF /json list
  (e.g. https://steamloopback.host/routes/library/app/<appid>).
- running: SteamAppId / SteamGameId in the environment of our own processes.
- names: appmanifest_<appid>.acf in every Steam library folder, or
  userdata/*/config/shortcuts.vdf for non-Steam shortcuts.
"""
import asyncio
import logging
import os
import re
from pathlib import Path

from . import cef, vdf

log = logging.getLogger("invasor.context")

POLL_INTERVAL = 3
STEAM_ROOT = Path.home() / ".local/share/Steam"
# STEAM TOUCHPOINT: SharedJSContext's URL shows the library route (no game shown if it changes).
ROUTE_APP = re.compile(r"/routes/library/app/(\d+)")
VDF_PATH = re.compile(r'"path"\s+"([^"]+)"')
VDF_NAME = re.compile(r'"name"\s+"([^"]*)"')


def _library_dirs():
    dirs = [STEAM_ROOT]
    try:
        text = (STEAM_ROOT / "steamapps/libraryfolders.vdf").read_text(errors="replace")
        dirs += [Path(p.replace("\\\\", "\\")) for p in VDF_PATH.findall(text)]
    except OSError:
        pass
    return list(dict.fromkeys(dirs))


def _app_name(appid):
    for lib in _library_dirs():
        try:
            text = (lib / f"steamapps/appmanifest_{appid}.acf").read_text(errors="replace")
        except OSError:
            continue
        if m := VDF_NAME.search(text):
            return m.group(1)
    return None


_shortcuts_cache = {"key": None, "names": {}, "exes": {}}


def _shortcut_exes():
    """{appid: executable path} of non-Steam shortcuts (quotes removed)."""
    _shortcut_names()
    return _shortcuts_cache["exes"]


def _shortcut_names():
    """{appid: name} for non-Steam shortcuts of every local Steam user, re-read only when files change."""
    files = sorted((STEAM_ROOT / "userdata").glob("*/config/shortcuts.vdf"))
    key = []
    for f in files:
        try:
            key.append((str(f), f.stat().st_mtime_ns))
        except OSError:
            pass
    if key == _shortcuts_cache["key"]:
        return _shortcuts_cache["names"]
    names, exes = {}, {}
    for f, _ in key:
        try:
            shortcuts = vdf.loads_binary(Path(f).read_bytes()).get("shortcuts", {})
        except (OSError, vdf.VDFError) as e:
            log.warning("cannot read %s: %s", f, e)
            continue
        for entry in shortcuts.values():
            # Keys are "AppName"/"appname" depending on who wrote the file.
            low = {k.lower(): v for k, v in entry.items()}
            if "appid" in low:
                appid = str(low["appid"] & 0xFFFFFFFF)
                names[appid] = low.get("appname")
                if isinstance(low.get("exe"), str):
                    exes[appid] = low["exe"].strip().strip('"')
    _shortcuts_cache.update(key=key, names=names, exes=exes)
    return names


def _running_appid():
    """AppID of a running game, from process environments (same user, no root)."""
    uid = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = dict(
                    kv.split(b"=", 1) for kv in f.read().split(b"\0") if b"=" in kv
                )
        except OSError:
            continue
        # Non-Steam shortcuts only carry SteamGameId; Steam games carry both.
        appid = env.get(b"SteamAppId", b"0")
        if appid in (b"", b"0"):
            appid = env.get(b"SteamGameId", b"0")
        if appid not in (b"", b"0"):
            try:
                n = int(appid) & 0xFFFFFFFFFFFFFFFF  # tolerate a signed rendering
            except ValueError:
                continue
            # SteamGameId of a shortcut is the 64-bit game id: (appid << 32) | 0x02000000.
            return str(n >> 32 if n > 0xFFFFFFFF else n)
    return None


class GameContext:
    def __init__(self):
        self.selected = None  # {"appid": str, "name": str|None}
        self.running = None
        self._names = {}

    def info(self, appid):
        """{"appid", "name", "shortcut"} of any app (installed Steam game or shortcut)."""
        return self._game(str(appid))

    def shortcut_exe(self, appid):
        """The executable a non-Steam shortcut launches (as in shortcuts.vdf), or None."""
        return _shortcut_exes().get(str(appid))

    def snapshot(self):
        return {"selected": self.selected, "running": self.running}

    def resolve_tile(self, tile):
        """The game for what a library tile shows ({"appid"} and/or {"name"}, read from
        Steam's page by the overlay), or None. By appid first; else by name, which must
        match exactly one non-Steam shortcut (two with the same name: can't tell, None)."""
        if not isinstance(tile, dict):
            return None
        appid = tile.get("appid")
        if isinstance(appid, str) and appid.isascii() and appid.isdigit() and len(appid) <= 20:
            return self._game(str(int(appid)))
        name = tile.get("name")
        if isinstance(name, str) and name:
            matches = [aid for aid, n in _shortcut_names().items() if n == name]
            if len(matches) == 1:
                return self._game(matches[0])
        return None

    def _game(self, appid):
        if appid is None:
            return None
        shortcut = appid in _shortcut_names()
        if appid not in self._names:
            name = _shortcut_names()[appid] if shortcut else _app_name(appid)
            if name is None:
                # Not cached: the game may get installed / the shortcut renamed later.
                return {"appid": appid, "name": None, "shortcut": shortcut}
            self._names[appid] = name
        return {"appid": appid, "name": self._names[appid], "shortcut": shortcut}

    async def run(self):
        while True:
            try:
                await self._poll()
            except Exception:
                log.exception("game context poll failed")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self):
        selected = None
        for t in await cef.list_targets():
            if t.get("title") == "SharedJSContext" and (m := ROUTE_APP.search(str(t.get("url") or ""))):
                selected = m.group(1)
        running = await asyncio.to_thread(_running_appid)

        sel, run = await asyncio.to_thread(lambda: (self._game(selected), self._game(running)))
        if sel != self.selected:
            log.info("selected: %s", sel)
        if run != self.running:
            log.info("running: %s", run)
        self.selected, self.running = sel, run
