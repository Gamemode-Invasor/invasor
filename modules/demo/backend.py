"""Demo module: exercises the whole backend contract (docs/MODULES.md, section 4).

- settings: on_change, and set() for an option another one decides (preset -> fan);
- storage: ctx.data and ctx.game_data;
- forms + toml: the "profile" form, stored by the module in a TOML file (saved with a validate=
  check that sees the new file before it replaces the old one);
- game: info() and shortcut_exe() in whoami;
- Steam: notify (with an icon) / notify_async, steam_call / steam_call_async, always with a plan B;
- clean errors: InvalidArgument (400) and Unavailable (503);
- plain and `async def` methods, a background thread stopped in teardown();
- on_steam_start: counts the Steam starts it has seen;
- upgrade() when a new version loads, uninstall() undoing what it left outside its folder;
- a helper file imported as `from . import helpers`.
"""
import os
import shutil
import threading
import time
from pathlib import Path

from . import helpers

# A 1x1 PNG as a data: URL: notify()'s icon may be an https URL or an embedded image like this.
ICON = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

ctx = None
_stop = None
_uptime = 0  # seconds since setup, counted by the background thread


def profile_path(context=None):
    """The TOML file the "profile" form lives in. It stands in for another program's
    config file: Invasor doesn't own it, so ctx.toml keeps whatever else it holds.
    Named after the module id, so a copy installed under another id has its own."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / f"invasor-{(context or ctx).id}" / "profile.toml"


def upgrade(context, previous_version):
    """A different version than last time is loading (before setup): migrate stored data
    here. If it raises, the module isn't loaded and it runs again next time."""
    context.log.info("upgrading from %s", previous_version)
    context.data.update(upgraded_from=previous_version)


def uninstall(context, purge):
    """The user is uninstalling Demo (after teardown, before its files go): undo what
    it left outside its folder. With purge the user also asked to delete its data."""
    if purge:
        shutil.rmtree(profile_path(context).parent, ignore_errors=True)
    context.log.info("uninstalled%s", " with its data" if purge else "")


def setup(context):
    global ctx, _stop
    ctx = context
    ctx.settings.on_change(on_change)
    _stop = threading.Event()
    threading.Thread(target=_tick, args=(_stop,), name="demo-tick", daemon=True).start()
    ctx.on_steam_start(steam_started)  # also runs now if Steam is already up
    ctx.log.info("demo module loaded")


def teardown():
    if _stop:
        _stop.set()


def _tick(stop):
    global _uptime
    _uptime = 0
    while not stop.wait(1):
        _uptime += 1


def steam_started():
    """A new Steam instance (started or restarted). Runs in its own thread: it may block."""
    starts = ctx.data.load().get("steam_starts", 0) + 1
    ctx.data.update(steam_starts=starts, last_steam_start=time.strftime("%H:%M:%S"))
    ctx.log.info("Steam start #%d seen", starts)


def on_change(key, value):
    """The user changed a setting in the panel. Must return quickly: slow work goes to a thread."""
    if key != "preset":
        return
    fan = helpers.preset_fan(value)
    if fan is not None:
        ctx.settings.set("fan", fan)  # doesn't call on_change again
    if fan is not None and ctx.settings.get("notify_preset"):
        title, body = helpers.preset_notification(value, ctx.settings.get("notify_text"))
        threading.Thread(target=_notify_quietly, args=(title, body), daemon=True).start()


def _notify_quietly(title, body):
    try:
        ctx.notify(title, body, ICON)
    except ctx.Unavailable as e:
        ctx.log.warning("notification not shown: %s", e)  # plan B: the log


def whoami():
    game = ctx.game.running or ctx.game.selected
    # shortcut_exe: what a non-Steam shortcut launches (None for a Steam game).
    exe = ctx.game.shortcut_exe(game["appid"]) if game and game.get("shortcut") else None
    return {"module": ctx.id, "game": game, "info": ctx.game.info(game["appid"]) if game else None, "exe": exe,
            "upgraded_from": ctx.data.load().get("upgraded_from")}


def notify(title="Demo", body="", sound=""):
    ctx.notify(title, body, ICON, sound=sound)  # no sound: Steam decides. InvalidArgument/Unavailable reach the UI as clean errors
    return {"ok": True}


async def notify_async(title="Demo", body="", sound=""):
    await ctx.notify_async(title, body, ICON, sound=sound)
    return {"ok": True}


def _system(info):
    keys = ("sOSName", "sOSVersionId", "sSteamBuildDate", "sCPUName")
    return {k: info.get(k) for k in keys} if isinstance(info, dict) else {"result": info}


def steam_info():
    """A read-only SteamClient call from a plain method, with a plan B."""
    try:
        return {"available": True, **_system(ctx.steam_call("System.GetSystemInfo"))}
    except ctx.Unavailable as e:
        return {"available": False, "reason": str(e)}


async def steam_info_async():
    """The same from an `async def` method (it runs in the event loop: never block here)."""
    try:
        return {"available": True, **_system(await ctx.steam_call_async("System.GetSystemInfo"))}
    except ctx.Unavailable as e:
        return {"available": False, "reason": str(e)}


def fail(kind="invalid"):
    if kind == "invalid":
        raise ctx.InvalidArgument("Demo refused this on purpose (InvalidArgument: 400)")
    raise ctx.Unavailable("Demo can't do this right now, on purpose (Unavailable: 503)")


def visit(appid=None):
    """Count a visit to Demo's Backend tab, in total (ctx.data) and per game (ctx.game_data)."""
    visits = ctx.data.load().get("visits", 0) + 1
    ctx.data.update(visits=visits)
    game = None
    if appid is not None:
        store = ctx.game_data(appid)
        game = store.load().get("visits", 0) + 1
        store.save({"visits": game})
    return {"visits": visits, "game_visits": game}


def stats():
    data = ctx.data.load()
    return {"uptime": _uptime, "visits": data.get("visits", 0), "upgraded_from": data.get("upgraded_from"),
            "steam_starts": data.get("steam_starts", 0), "last_steam_start": data.get("last_steam_start")}


def profile_get():
    data, _ = ctx.toml.load(profile_path())
    return ctx.forms["profile"].clean(helpers.profile_section(data))


def _check_profile(tmp):
    section = helpers.profile_section(ctx.toml.load(tmp)[0])
    for key in ctx.forms["profile"].fields:
        if key in section:
            ctx.forms["profile"].coerce(key, section[key])  # InvalidArgument if it wouldn't read back


def profile_set(key, value):
    form = ctx.forms["profile"]
    value = form.coerce(key, value)  # InvalidArgument if it can't be stored
    path = profile_path()
    data, mtime = ctx.toml.load(path)
    section = helpers.profile_section(data)
    if not section:
        section = data["profile"] = form.defaults()
    section[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refused if another program saved it meanwhile; validate= sees the new file first and
    # can reject it (the old one stays untouched).
    ctx.toml.save(path, data, mtime, validate=_check_profile)
    return value


METHODS = {
    "whoami": whoami,
    "notify": notify,
    "notify_async": notify_async,
    "steam_info": steam_info,
    "steam_info_async": steam_info_async,
    "fail": fail,
    "visit": visit,
    "stats": stats,
    "profile_get": profile_get,
    "profile_set": profile_set,
}
