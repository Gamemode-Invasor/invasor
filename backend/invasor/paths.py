"""Where Steam keeps things: its folder, the library folders, the current account's userdata,
and a game's compatdata/shadercache. Read only; every path is a pathlib.Path."""
import glob
import os
from pathlib import Path

from . import cef_flag, vdf
from .schema import InvalidArgument, Unavailable

STEAM_ROOT = None  # Steam's folder if set (tests); else found as the installer and cef_flag find it
# SD cards and USB drives, where Steam may keep a library it no longer lists.
MEDIA_GLOBS = ("/run/media/*/steamapps", "/run/media/*/*/steamapps")
STEAMID64_BASE = 76561197960265728  # SteamID64 of account 0: userdata/<SteamID64 - this>


def root():
    """Steam's folder, or None if it wasn't found."""
    return Path(STEAM_ROOT) if STEAM_ROOT else cef_flag.steam_dir()


def steam_root():
    """Steam's folder; Unavailable if it wasn't found."""
    found = root()
    if found is None:
        raise Unavailable("Steam's folder wasn't found")
    return found


def libraries():
    """Every existing steamapps folder, each once, the main one first."""
    main = steam_root() / "steamapps"
    candidates = [main]
    try:
        text = (main / "libraryfolders.vdf").read_text(errors="replace")
    except OSError:
        text = ""
    folders = vdf.get(vdf.loads_text(text), "libraryfolders", default={})
    if isinstance(folders, dict):
        for entry in folders.values():
            path = vdf.get(entry, "path")
            if isinstance(path, str) and path:
                candidates.append(Path(path) / "steamapps")
    for pattern in MEDIA_GLOBS:
        candidates += [Path(p) for p in sorted(glob.glob(pattern))]
    out, seen = [], set()
    for path in candidates:
        if not path.is_dir():
            continue
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            out.append(path)
    return out


def _accounts():
    """Every account folder in userdata/ (all-digit names, not 0), sorted."""
    try:
        entries = list((steam_root() / "userdata").iterdir())
    except OSError:
        return []
    found = [p for p in entries if p.name.isascii() and p.name.isdigit() and p.name != "0" and p.is_dir()]
    return sorted(found, key=lambda p: int(p.name))


def _current_account():
    """userdata/<account> of the account in use: loginusers.vdf's MostRecent user, else the
    newest login, else the only account folder there is."""
    base = steam_root() / "userdata"
    try:
        text = (steam_root() / "config/loginusers.vdf").read_text(errors="replace")
    except OSError:
        text = ""
    users = vdf.get(vdf.loads_text(text), "users", default={})
    users = users if isinstance(users, dict) else {}
    recent, newest, newest_time = None, None, -1
    for steamid, user in users.items():
        if not (steamid.isascii() and steamid.isdigit()) or int(steamid) <= STEAMID64_BASE:
            continue
        account = str(int(steamid) - STEAMID64_BASE)
        if vdf.get(user, "MostRecent") == "1" and recent is None:
            recent = account
        stamp = vdf.get(user, "timestamp")
        if isinstance(stamp, str) and stamp.isascii() and stamp.isdigit() and int(stamp) > newest_time:
            newest, newest_time = account, int(stamp)
    for account in (recent, newest):
        if account is not None and (base / account).is_dir():
            return base / account
    accounts = _accounts()
    if len(accounts) == 1:
        return accounts[0]
    raise Unavailable("couldn't tell which Steam account is in use")


def userdata(all=False):
    """userdata/<account> of the account in use; all=True: [every account folder]."""
    return _accounts() if all else _current_account()


def grid_dir():
    """The current account's custom artwork folder (may not exist yet)."""
    return userdata() / "config" / "grid"


def appid_str(appid):
    """appid (an int, or a string of digits, from 1 to 2**32-1) as a string; else InvalidArgument."""
    if isinstance(appid, bool) or not isinstance(appid, (int, str)):
        raise InvalidArgument(f"appid must be a number, not {appid!r}")
    if isinstance(appid, str):
        if not (appid.isascii() and appid.isdigit()):
            raise InvalidArgument(f"appid must be a number, not {appid!r}")
        appid = int(appid)
    if not 1 <= appid <= 2**32 - 1:
        raise InvalidArgument(f"appid out of range: {appid}")
    return str(appid)


def _game_dir(kind, appid):
    """<library>/<kind>/<appid>: the library where it exists, else the one holding the game,
    else the main library."""
    appid = appid_str(appid)
    libs = libraries()
    for lib in libs:
        if (lib / kind / appid).is_dir():
            return lib / kind / appid
    for lib in libs:
        if (lib / f"appmanifest_{appid}.acf").is_file():
            return lib / kind / appid
    return steam_root() / "steamapps" / kind / appid


def compatdata(appid):
    """The Proton prefix folder of a game: <library>/compatdata/<appid>."""
    return _game_dir("compatdata", appid)


def shadercache(appid):
    """The shader cache folder of a game: <library>/shadercache/<appid>."""
    return _game_dir("shadercache", appid)
