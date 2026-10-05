"""Built-in API used by the overlay itself (exposed as /api/core/<method>)."""
import asyncio
import logging
import platform

from . import __version__, config, gamepad, install
from .updater import Updater
from .schema import InvalidArgument, Unavailable
from .storage import JsonStore

log = logging.getLogger("invasor.frontend")


HIGHLIGHT_JS = "window.__invasor?.highlighted?.() ?? null"


def make_methods(manager, game, watcher, cfg, injector=None, steam=None, updater=None):
    updater = updater or Updater()

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

    # ---------- user modules (⚙ Settings › Install module) ----------

    def _reserved():
        # Ids shipped with Invasor can't be taken by an installed module.
        return {"core"} | {mid for mid, m in manager.manifests.items() if m.get("source") == "core"}

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
            mid = await asyncio.to_thread(install.install, path, _reserved(), bool(replace), before_replace)
        except BaseException:
            # Failed after the old version was unloaded: it's still in place, load it again.
            for old in replaced:
                if (config.USER_MODULES_DIR / old).is_dir():
                    await asyncio.to_thread(manager.add, old)
            raise
        entry = await asyncio.to_thread(manager.add, mid)
        ui_js = manager.manifests[mid]["dir"] / "dist" / "ui.js"
        if injector is not None and ui_js.is_file() and "error" not in manager.manifests[mid]:
            await injector.inject_module(mid, ui_js)
        log.info("installed module %s %s", mid, entry.get("version"))
        return entry

    def module_uninstall(id, purge=False):
        """Uninstall a user module (its uninstall() runs first). With purge its settings
        and data go too; otherwise they stay in case it's installed again."""
        m = manager.manifests.get(id)
        if m is None or m.get("source") != "user":
            raise InvalidArgument(f"{id!r} isn't an installed module (modules shipped with Invasor can only be disabled)")
        manager.remove(id, bool(purge))
        install.uninstall(id)
        log.info("uninstalled module %s%s", id, " and its data" if purge else "")
        return True

    async def module_rescan():
        """Read the module folders again and inject every module UI, so the panels see
        modules added, removed or edited on disk. Returns the new listing."""
        await asyncio.to_thread(manager.rescan)  # module code: never in the event loop
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
        after this returns, and Steam after it."""
        return updater.apply()

    def pads():
        return [{"device": dev, "name": name, "kind": kind} for dev, name, kind in gamepad.pads()]

    return {
        "ping": lambda: "pong",
        "prefs": prefs,
        "set_pref": set_pref,
        "set_qam_width": set_qam_width,
        "pads": pads,
        "update_status": update_status,
        "update_check": update_check,
        "update_apply": update_apply,
        "info": info,
        "report": report,
        "game": game_state,
        "steam_call": steam_call,
        "notify": notify,
        "module_browse": module_browse,
        "module_inspect": module_inspect,
        "module_install": module_install,
        "module_uninstall": module_uninstall,
        "module_rescan": module_rescan,
        "modules": modules,
        "set_enabled": set_enabled,
        "settings_get": settings_get,
        "settings_set": settings_set,
    }
