"""Built-in API used by the overlay itself (exposed as /api/core/<method>)."""
import asyncio
import logging
import platform
import shutil
import subprocess

from . import __version__, config, gamepad, install, modpool
from .market import Market
from .updater import Updater, run_detached
from .schema import InvalidArgument, Unavailable
from .storage import JsonStore

log = logging.getLogger("invasor.frontend")


HIGHLIGHT_JS = "window.__invasor?.highlighted?.() ?? null"

# Runs the installed installer's --uninstall from a copy: it deletes the folder it lives in.
UNINSTALL_SCRIPT = r'''
tmp="$(mktemp)" && cp "$1" "$tmp" && shift && bash "$tmp" "$@"
rm -f "$tmp"
'''
LOG_MAX_LINES = 1000


def make_methods(manager, game, watcher, cfg, injector=None, steam=None, updater=None, market=None):
    updater = updater or Updater()
    market = market or Market(
        installed=lambda: {m["id"]: m["version"] for m in manager.listing()},
        channel=lambda: cfg.get("update_channel", "stable"),
    )

    def info():
        return {"version": __version__, "python": platform.python_version(), "kernel": platform.release()}

    def report(**env):
        """The overlay reports where it landed (window role/size) so layouts can be tuned
        from the log. Its first report per load is INFO; later ones (open, refit) DEBUG."""
        (log.debug if "event" in env else log.info)("overlay %s: %s", env.get("event", "up"), env)
        return True

    def modules():
        return manager.listing()

    def set_enabled(id, enabled):
        if id not in manager.manifests:
            raise InvalidArgument(f"unknown module {id!r}")
        manager.set_enabled(id, bool(enabled))
        return manager.listing()

    def _settings(id):
        if id not in manager.settings:
            raise InvalidArgument(f"unknown module {id!r} (or its module.json is invalid)")
        return manager.settings[id]

    def settings_get(id):
        """Every setting of a module, valid against its module.json schema."""
        return _settings(id).get()

    def settings_set(id, key, value):
        """Change one setting from the panel: validated, saved, the module's backend told
        (ctx.settings.on_change). Returns the value actually stored (clamped, snapped…)."""
        return _settings(id).set(key, value, notify=True)

    async def game_state():
        """running / selected (kept up to date by GameContext) and highlighted: the tile
        under the cursor in the library, asked of the main window right now (on demand,
        never polled in the background). Best effort: None whenever it can't be told."""
        state = game.snapshot()
        tiles = await injector.evaluate_all(HIGHLIGHT_JS, role="main") if injector else []
        tile = next((t for t in tiles if t), None)
        state["highlighted"] = await asyncio.to_thread(game.resolve_tile, tile) if tile else None
        return state

    async def steam_call(path, args=None):
        """SteamClient.<path>(*args) in Steam's SharedJSContext, for UIs whose window has
        no SteamClient. 503 if it isn't available: callers must have a plan B."""
        if steam is None:
            raise Unavailable("SteamClient bridge not configured")
        if args is not None and not isinstance(args, list):
            raise InvalidArgument("args must be a list")
        return await steam.call(path, args or [])

    async def notify(title, body="", icon=""):
        """A notification as Steam shows an achievement (see SteamBridge.notify)."""
        if steam is None:
            raise Unavailable("SteamClient bridge not configured")
        return await steam.notify(title, body, icon)

    # ---------- ⚙ Settings › Manage Invasor ----------

    def restart_service():
        """Restart this service (the panel reloads by itself a few seconds later). Steam and
        any running game are not restarted."""
        run_detached("invasor-restart", ["systemctl", "--user", "restart", "invasor.service"], "restart")
        return True

    async def restart_steam():
        """Ask Steam to restart itself (it closes the running game). Unavailable if this
        Steam has no such function: nothing is killed from here."""
        # STEAM TOUCHPOINT (see docs/API-Steam.md): SteamClient.User.StartRestart(false), the call of Steam's own "Restart now" (it needs the argument).
        if steam is None:
            raise Unavailable("SteamClient bridge not configured")
        await steam.call("User.StartRestart", [False])
        return True

    def log_tail(lines=200):
        """The last lines of the service's log, as text."""
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= LOG_MAX_LINES:
            raise InvalidArgument(f"lines must be a whole number from 1 to {LOG_MAX_LINES}")
        cmd = ["journalctl", "--user", "-u", "invasor.service", "-n", str(lines), "--no-pager", "-o", "cat"]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=10, errors="replace")
        except FileNotFoundError:
            raise Unavailable("journalctl not found") from None
        except subprocess.TimeoutExpired:
            raise Unavailable("journalctl took too long") from None
        if done.returncode != 0:
            raise Unavailable(f"couldn't read the log ({done.stderr.strip() or done.returncode})")
        return done.stdout

    def reset_config():
        """Back to the default preferences: forget config.json (modules and their settings
        stay) and restart the service so everything reads the defaults."""
        config.CONFIG_FILE.unlink(missing_ok=True)
        return restart_service()

    def uninstall_invasor(purge=False):
        """Uninstall Invasor with its own installer, in a detached unit: the service stops
        and the overlay leaves Steam a moment after this returns. purge also deletes the
        configuration."""
        if not isinstance(purge, bool):
            raise InvalidArgument("purge must be true or false")
        script = config.DATA_DIR / "invasor-installation.sh"
        if not script.is_file():
            raise Unavailable("this install has no uninstaller: install Invasor once more to enable it")
        args = ["--uninstall", *(["--purge"] if purge else [])]
        run_detached("invasor-uninstall", ["bash", "-c", UNINSTALL_SCRIPT, "invasor-uninstall", str(script), *args], "uninstall")
        return True

    # ---------- user modules (⚙ Settings › Install module) ----------

    def _reserved():
        # Ids shipped with Invasor can't be taken by an installed module (not even an uninstalled one: it can come back).
        return {"core"} | {mid for mid, m in manager.manifests.items() if m.get("source") == "core"} | set(cfg.get("removed_modules", []))

    def module_browse(path=None):
        return install.browse(path)

    def module_inspect(path):
        return install.inspect(path, _reserved())

    async def module_install(path, replace=False):
        """Check, install and load a module from a zip, and inject its UI right away.
        Module code (teardown, import, setup) runs in a worker thread, never in the event loop."""
        replaced = []

        def before_replace(mid):
            replaced.append(mid)
            manager.unload(mid)

        try:
            mid = await modpool.run(install.install, path, _reserved(), bool(replace), before_replace)
        except BaseException:
            # Failed after the old version was unloaded: it's still in place, load it again.
            for old in replaced:
                if (config.USER_MODULES_DIR / old).is_dir():
                    await modpool.run(manager.add, old)
            raise
        entry = await modpool.run(manager.add, mid)
        ui_js = manager.manifests[mid]["dir"] / "dist" / "ui.js"
        if injector is not None and ui_js.is_file() and "error" not in manager.manifests[mid]:
            await injector.inject_module(mid, ui_js)
        log.info("installed module %s %s", mid, entry.get("version"))
        return entry

    # ---------- ⚙ Settings › Install module › Open the Market (backend/invasor/market.py) ----------

    async def market_list(refresh=False):
        """{modules: [card], notes}: the newest release of every module repository of the organisation."""
        return await asyncio.to_thread(market.list, bool(refresh))

    async def market_install(repo):
        """Download (sha256 checked) and install, or update, the market's module of a repository."""
        path, work, mid = await asyncio.to_thread(market.download, repo)
        try:
            return await module_install(path, replace=any(m["id"] == mid for m in manager.listing()))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def module_uninstall(id, purge=False):
        """Uninstall a module (its uninstall() runs first). With purge its settings and data
        go too; otherwise they stay in case it's installed again. One shipped with Invasor
        (demo) stays uninstalled across updates and can be restored (module_restore)."""
        m = manager.manifests.get(id)
        if m is None:
            raise InvalidArgument(f"{id!r} isn't an installed module")
        if m.get("source") == "core":
            manager.remove_core(id, bool(purge))
        else:
            manager.remove(id, bool(purge))
            install.uninstall(id)
        log.info("uninstalled module %s%s", id, " and its data" if purge else "")
        return True

    def module_removed():
        """The shipped modules that were uninstalled and can be restored."""
        return manager.removed_listing()

    async def module_restore(id):
        """Bring back a shipped module that was uninstalled, and inject its UI."""
        entry = await modpool.run(manager.restore_core, id)
        ui_js = manager.manifests[id]["dir"] / "dist" / "ui.js"
        if injector is not None and ui_js.is_file() and "error" not in manager.manifests[id]:
            await injector.inject_module(id, ui_js)
        log.info("restored module %s", id)
        return entry

    async def module_rescan():
        """Read the module folders again and inject every module UI, so the panels see
        modules added, removed or edited on disk. Returns the new listing."""
        await modpool.run(manager.rescan)  # module code: never in the event loop
        if injector is not None:
            for mid, ui_js in manager.ui_scripts():
                await injector.inject_module(mid, ui_js)
        return manager.listing()

    def prefs():
        return {
            "open_combo": sorted(watcher.combo),
            "panel_side": cfg.get("panel_side", "auto"),
            "accent_color": cfg.get("accent_color", "blue"),
            "handle_icon": cfg.get("handle_icon", "icon"),
            "module_order": cfg.get("module_order", []),
            "update_check": cfg.get("update_check", True),
            "update_channel": cfg.get("update_channel", "stable"),
            # Learned, not user-set: visible width of the Quick Access column (see overlay.ts).
            "qam_visible_w": cfg.get("qam_visible_w"),
        }

    def set_qam_width(width):
        """Remember the on-screen width of the Quick Access window, measured when Steam
        reports its position, for the times it doesn't (e.g. no game running)."""
        if not isinstance(width, (int, float)) or isinstance(width, bool) or not 200 <= width <= 4000:
            raise InvalidArgument(f"implausible width {width!r}")
        width = int(width)
        if cfg.get("qam_visible_w") != width:
            cfg["qam_visible_w"] = width
            JsonStore(config.CONFIG_FILE).update(qam_visible_w=width)
        return True

    async def set_qam_shown(shown):
        """Quick Access says whether its "I" is on screen: the library's hides while it is
        (with the panel on the left nothing covers it, and two would show)."""
        if not isinstance(shown, bool):
            raise InvalidArgument(f"shown must be true or false, not {shown!r}")
        if injector is not None:
            await injector.set_main_handle_hidden(shown)
        return True

    def set_pref(key, value):
        """Only user-facing preferences can be changed from the panel, and only to valid values."""
        if key == "open_combo":
            names = {str(b).upper() for b in value} if isinstance(value, list) else set()
            if not names or not names <= set(gamepad.BUTTONS):
                raise InvalidArgument(f"invalid combo {value!r}")
            value = sorted(names)
            watcher.set_combo(value)
        elif key == "accent_color":
            if value not in config.ACCENT_COLORS:
                raise InvalidArgument(f"invalid accent_color {value!r}")
        elif key == "module_order":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise InvalidArgument(f"invalid module_order {value!r}: a list of module ids")
            value = list(dict.fromkeys(value))  # each id once, in the order given
            if len(value) > config.MODULE_ORDER_MAX:
                raise InvalidArgument(f"module_order: more than {config.MODULE_ORDER_MAX} modules")
        elif key == "handle_icon":
            if value not in config.HANDLE_ICONS:
                raise InvalidArgument(f"invalid handle_icon {value!r}")
        elif key == "panel_side":
            if value not in config.PANEL_SIDES:
                raise InvalidArgument(f"invalid panel_side {value!r}")
        elif key == "update_check":
            if not isinstance(value, bool):
                raise InvalidArgument(f"invalid update_check {value!r}")
        elif key == "update_channel":
            if value not in config.UPDATE_CHANNELS:
                raise InvalidArgument(f"invalid update_channel {value!r}")
        else:
            raise InvalidArgument(f"unknown preference {key!r}")
        cfg[key] = value
        JsonStore(config.CONFIG_FILE).update(**{key: value})
        return prefs()

    # ---------- updates (⚙ Settings › Updates; backend/invasor/updater.py) ----------

    def update_status():
        """The last check's answer (None before the first one), without going to the network."""
        return updater.last

    def update_check():
        return updater.check()

    def update_apply():
        """Download, verify and install the new version. The service restarts a moment
        after this returns; Steam is not restarted."""
        return updater.apply()

    def pads():
        return [{"device": dev, "name": name, "kind": kind} for dev, name, kind in gamepad.pads()]

    return {
        "ping": lambda: "pong",
        "prefs": prefs,
        "set_pref": set_pref,
        "set_qam_width": set_qam_width,
        "set_qam_shown": set_qam_shown,
        "pads": pads,
        "update_status": update_status,
        "update_check": update_check,
        "update_apply": update_apply,
        "restart_service": restart_service,
        "restart_steam": restart_steam,
        "log_tail": log_tail,
        "reset_config": reset_config,
        "uninstall_invasor": uninstall_invasor,
        "info": info,
        "report": report,
        "game": game_state,
        "steam_call": steam_call,
        "notify": notify,
        "module_browse": module_browse,
        "module_inspect": module_inspect,
        "module_install": module_install,
        "market_list": market_list,
        "market_install": market_install,
        "module_uninstall": module_uninstall,
        "module_removed": module_removed,
        "module_restore": module_restore,
        "module_rescan": module_rescan,
        "modules": modules,
        "set_enabled": set_enabled,
        "settings_get": settings_get,
        "settings_set": settings_set,
    }
