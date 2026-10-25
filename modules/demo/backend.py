"""Demo module: exercises the whole backend contract (docs/MODULES.md, section 4).

- settings: on_change, and set() for an option another one decides (preset -> fan);
- storage: ctx.data and ctx.game_data;
- forms + toml: the "profile" form, stored by the module in a TOML file (saved with a validate=
  check that sees the new file before it replaces the old one);
- game: info() and shortcut_exe() in whoami; ctx.id and ctx.path (the module's folder) too;
- Steam folders: ctx.paths (root, libraries, account, artwork) and ctx.vdf (libraryfolders.vdf) in steam_folders;
- archives: ctx.archive.extract and swap_dir in unpack_sample (assets/sample.zip -> its data folder);
- downloads: ctx.http.download in download_sample, with a checksum or asking the user in the Tasks panel;
- Steam: notify (with an icon, a timeout and a sound) / notify_async, steam_call / steam_call_async, always with a plan B;
- Steam changes: ctx.steam.add_to / launch_options / set_launch_options / remove_shortcut on a
  Demo shortcut of its own (never a real game), open_url, supports() for the plan B, and
  ctx.steam_language();
- clean errors: InvalidArgument (400) and Unavailable (503);
- plain and `async def` methods, a background thread stopped in teardown();
- ctx.events: counts the Steam starts it has seen and notes the last game started, stopped or selected;
- ctx.task: a long operation that reports its progress (slow_task), seen from anywhere in core.tasks(),
  and t.ask in ask_task (a question answered with Accept / Deny in the Tasks panel);
- upgrade() when a new version loads, uninstall() undoing what it left outside its folder;
- a helper file imported as `from . import helpers`.
"""
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from . import helpers

# A 1x1 PNG as a data: URL: notify()'s icon may be an https URL or an embedded image like this.
ICON = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

ctx = None
_stop = None
_uptime = 0  # seconds since setup, counted by the background thread
_events_lock = threading.Lock()  # callbacks run in threads of their own: one counter, one writer


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
    if context.data.load().get("shortcut"):
        # Not removed here: Steam may be gone by now. The user can remove it in Steam.
        context.log.info("the Demo shortcut stays in Steam")
    if purge:
        shutil.rmtree(profile_path(context).parent, ignore_errors=True)
        (context.data.path.parent / SHORTCUT_SCRIPT).unlink(missing_ok=True)
    context.log.info("uninstalled%s", " with its data" if purge else "")


def setup(context):
    global ctx, _stop
    ctx = context
    ctx.settings.on_change(on_change)
    _stop = threading.Event()
    threading.Thread(target=_tick, args=(_stop,), name="demo-tick", daemon=True).start()
    ctx.events.on("steam_start", steam_started)  # also runs now if Steam is already up
    for name in ("game_started", "game_stopped", "game_selected"):
        ctx.events.on(name, lambda game, name=name: game_event(name, game))
    ctx.log.info("demo module loaded")


def teardown():
    if _stop:
        _stop.set()


def _tick(stop):
    global _uptime
    _uptime = 0
    while not stop.wait(1):
        _uptime += 1


def steam_started(payload=None):
    """A new Steam instance (started or restarted). Runs in its own thread: it may block."""
    with _events_lock:
        starts = ctx.data.load().get("steam_starts", 0) + 1
        ctx.data.update(steam_starts=starts, last_steam_start=time.strftime("%H:%M:%S"))
    ctx.log.info("Steam start #%d seen", starts)


def game_event(name, game):
    """A game started, stopped or had its page opened; `game` is {"appid", "name", "shortcut"}."""
    what = f"{name.split('_', 1)[1]} {game.get('name') or game['appid']}"
    ctx.data.update(last_game_event=f"{what} at {time.strftime('%H:%M:%S')}")
    ctx.log.info("game event: %s", what)


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
    return {"module": ctx.id, "folder": Path(ctx.path).name, "game": game, "info": ctx.game.info(game["appid"]) if game else None, "exe": exe,
            "upgraded_from": ctx.data.load().get("upgraded_from")}


def steam_folders():
    """Where Steam keeps things. Each field is None when it can't be told (no Steam here, no account yet)."""
    def found(get):
        try:
            return get()
        except ctx.Unavailable:
            return None

    root = found(ctx.paths.steam_root)
    account = found(ctx.paths.userdata)
    grid = found(ctx.paths.grid_dir)
    entries = 0
    if root is not None:
        try:
            text = (root / "steamapps/libraryfolders.vdf").read_text(errors="replace")
            folders = ctx.vdf.get(ctx.vdf.loads_text(text), "libraryfolders", default={})
            entries = len(folders) if isinstance(folders, dict) else 0
        except OSError:
            pass
    return {"root": str(root) if root else None, "libraries": [str(p) for p in found(ctx.paths.libraries) or []],
            "account": str(account) if account else None, "grid": str(grid) if grid else None,
            "libraries_vdf_entries": entries}


def unpack_sample():
    """Unpack assets/sample.zip next to ctx.data (its folder goes with a purge) and swap it in
    as sample/: a second run replaces the first in one step. Returns the files unpacked."""
    data = ctx.data.path.parent
    data.mkdir(parents=True, exist_ok=True)
    work = tempfile.mkdtemp(dir=data, prefix=".sample-")
    try:
        top = ctx.archive.extract(ctx.path / "assets/sample.zip", Path(work) / "unpacked", single_root=True)
        target = ctx.archive.swap_dir(top, data / "sample")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"folder": str(target), "files": sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())}


SAMPLE_URL = "https://raw.githubusercontent.com/Gamemode-Invasor/invasor/main/LICENSE"
SAMPLE_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


def download_sample(checked=True):
    """Download Invasor's LICENSE next to ctx.data as LICENSE.txt. With checked=True its sha256 is
    known, so it just downloads; with checked=False there is none and the core asks the user first
    (Tasks panel). A plain method: ctx.http.download blocks while it waits."""
    data = ctx.data.path.parent
    data.mkdir(parents=True, exist_ok=True)
    checksum = ("sha256", SAMPLE_SHA256) if checked else None
    got = ctx.http.download(SAMPLE_URL, data / "LICENSE.txt", checksum=checksum)
    return {"size": got["size"], "sha256": got["sha256"]}


def notify(title="Demo", body="", sound=""):
    ctx.notify(title, body, ICON, timeout=10, sound=sound)  # timeout: seconds to wait for Steam. No sound: Steam decides. InvalidArgument/Unavailable reach the UI as clean errors
    return {"ok": True}


async def notify_async(title="Demo", body="", sound=""):
    await ctx.notify_async(title, body, ICON, sound=sound)
    return {"ok": True}


def slow_task(seconds=5, fail=False):
    """A long operation others can watch: it advances once a second and can end failing.
    `with ctx.task(...)` ends it by itself (done, or failed if an error leaves the block)."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 30:
        raise ctx.InvalidArgument("seconds must be a whole number from 1 to 30")
    with ctx.task("Demo: slow task") as task:
        for i in range(seconds):
            task.update(i / seconds, f"Step {i + 1} of {seconds}")
            time.sleep(1)
        if fail:
            raise ctx.Unavailable("Demo failed this task on purpose (Unavailable: 503)")
    return {"seconds": seconds}


def ask_task(question="Demo asks: is this fine?"):
    """A task that asks the user (Accept / Deny in the Tasks panel) and waits for the answer.
    A plain method: t.ask blocks while it waits."""
    if not isinstance(question, str) or not question.strip() or len(question) > 256:
        raise ctx.InvalidArgument("question must be some text, at most 256 characters")
    with ctx.task("Demo: a question", notify=False) as t:
        return {"accepted": t.ask(question)}


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


SHORTCUT_SCRIPT = "demo-shortcut.sh"
STEAM_FEATURES = ("add_to", "open_url", "remove_shortcut", "launch_options", "set_launch_options")


def steam_supports():
    """What ctx.steam can do with this Steam: the UI shows a plan B for what it can't."""
    return {name: ctx.steam.supports(name) for name in STEAM_FEATURES}


def steam_shortcut_add():
    """Add a Demo shortcut (a tiny script in Demo's data folder) to Steam. Only on a button press:
    anything that changes the user's Steam waits for the user."""
    script = ctx.data.path.parent / SHORTCUT_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('#!/bin/sh\necho "Invasor Demo"\n')
    script.chmod(0o755)
    res = ctx.steam.add_to("Invasor Demo", str(script), launch_options="DEMO=1 %command%")
    ctx.data.update(shortcut=res["appid"])
    return res


def _shortcut():
    appid = ctx.data.load().get("shortcut")
    if not appid:
        raise ctx.InvalidArgument("Demo has no shortcut in Steam: add it first")
    return appid


def steam_shortcut_options(line=None):
    """The launch options of Demo's shortcut; with `line`, set them first."""
    appid = _shortcut()
    res = {"appid": appid}
    if line is not None:
        res.update(ctx.steam.set_launch_options(appid, line))
    return {**res, "options": ctx.steam.launch_options(appid)}


def steam_shortcut_remove():
    res = ctx.steam.remove_shortcut(_shortcut())
    ctx.data.transform(lambda d: {k: v for k, v in d.items() if k != "shortcut"})
    return res


def open_url():
    ctx.steam.open_url("https://github.com/Gamemode-Invasor/invasor")
    return {"ok": True}


def steam_language():
    return {"code": ctx.steam_language(), "raw": ctx.steam_language(raw=True)}


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
    # ctx.path: the module's own folder (here, to say how many Python files it ships).
    return {"uptime": _uptime, "files": len(list(Path(ctx.path).glob("*.py"))), "visits": data.get("visits", 0), "upgraded_from": data.get("upgraded_from"),
            "steam_starts": data.get("steam_starts", 0), "last_steam_start": data.get("last_steam_start"),
            "last_game_event": data.get("last_game_event")}


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
    "steam_folders": steam_folders,
    "unpack_sample": unpack_sample,
    "download_sample": download_sample,
    "notify": notify,
    "notify_async": notify_async,
    "steam_info": steam_info,
    "steam_info_async": steam_info_async,
    "fail": fail,
    "steam_supports": steam_supports,
    "steam_shortcut_add": steam_shortcut_add,
    "steam_shortcut_options": steam_shortcut_options,
    "steam_shortcut_remove": steam_shortcut_remove,
    "open_url": open_url,
    "steam_language": steam_language,
    "visit": visit,
    "stats": stats,
    "slow_task": slow_task,
    "ask_task": ask_task,
    "profile_get": profile_get,
    "profile_set": profile_set,
}
