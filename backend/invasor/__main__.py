import asyncio
import logging
import signal
import sys

from . import __version__, cef_flag, config, core, updater as updates
from .combo import decide_combo
from .context import GameContext
from .gamepad import ComboWatcher
from .injector import Injector, remove_overlays
from .modules import ModuleManager
from .server import ApiServer
from .steam import SteamBridge


async def main():
    # Logging first, so warnings raised while loading the config are formatted too.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    logging.getLogger().setLevel(cfg["log_level"])
    logging.getLogger("invasor").info("invasor %s starting", __version__)
    cef_flag.ensure()  # something may have deleted Steam's debugging flag: put it back before Steam next starts

    game = GameContext()
    # The bridge needs the injector, the injector needs the module list: wire lazily.
    injector = None
    steam = SteamBridge(lambda expression, timeout: injector.evaluate_shared(expression, timeout))
    steam.loop = asyncio.get_running_loop()
    manager = ModuleManager(cfg, game, steam)
    await asyncio.to_thread(manager.discover)  # module code never runs in the event loop
    injector = Injector(cfg, manager.ui_scripts, on_steam_start=manager.steam_started)
    log = logging.getLogger("invasor")

    async def toggle_overlay():
        # The panel exists in several windows (main UI, Quick Access); it's only ever
        # opened in the focused one, never in a window that isn't on screen.
        states = [s for s in await injector.evaluate_all("window.__invasor?.state?.()") if s]
        action = decide_combo(states)
        if action == "ignore":
            log.info("combo ignored: Steam UI not on screen (windows: %s)", states)
        elif action == "close":
            log.info("combo -> close (windows: %s)", states)
            await injector.evaluate_all("window.__invasor?.setOpen(false)")
        else:
            log.info("combo -> open (windows: %s)", states)
            await injector.evaluate_all(
                "(() => { const s = window.__invasor?.state?.(); if (s && s.focused) window.__invasor.setOpen(true); })()"
            )

    tasks = set()  # keep references: the event loop only holds weak ones

    def on_combo():
        task = asyncio.create_task(toggle_overlay())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    watcher = ComboWatcher(cfg["open_combo"], on_combo)
    # "core" is reserved for the overlay's own API; a module can't shadow it.
    updater = updates.Updater(channel=lambda: cfg.get("update_channel", "stable"))
    manager.registry["core"] = core.make_methods(manager, game, watcher, cfg, injector, steam, updater)

    # Stop cleanly on SIGTERM (systemctl stop/restart) so modules get their teardown().
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    services = [
        asyncio.create_task(c, name=n)
        for n, c in (
            ("api", ApiServer(cfg, manager.registry).run()),
            ("injector", injector.run()),
            ("game", game.run()),
            ("gamepad", watcher.run()),
            ("updates", updates.run(updater, cfg, steam)),
        )
    ]
    stopping = asyncio.create_task(stop.wait())
    await asyncio.wait([*services, stopping], return_when=asyncio.FIRST_COMPLETED)
    for t in services:
        if t.done() and not t.cancelled() and t.exception():
            # e.g. the API port is taken: exit with an error, systemd restarts us.
            log.error("%s stopped", t.get_name(), exc_info=t.exception())
        t.cancel()
    await asyncio.gather(*services, return_exceptions=True)
    manager.shutdown()
    log.info("invasor stopped")
    if not stop.is_set():
        raise SystemExit(1)


async def remove_overlay():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    count = await remove_overlays()
    logging.getLogger("invasor").info("overlay removed from %d Steam window(s)", count)


def uninstall_modules(purge):
    """Every module's uninstall(ctx, purge), Invasor's own and the installed ones, as
    when the user uninstalls one in ⚙ Settings. Run with the service stopped (no Steam
    bridge). Their files are left for the uninstaller to delete."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    manager = ModuleManager({"disabled_modules": []}, GameContext())
    manager.scan()
    ids = list(manager.manifests)
    for mid in ids:
        manager.remove(mid, purge)
    logging.getLogger("invasor").info("modules uninstalled: %s", ", ".join(ids) or "(none)")


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        # One-shots for the uninstaller: take the overlay out of Steam, or run the
        # modules' uninstall(); start nothing.
        if "--uninstall-modules" in args:
            uninstall_modules("--purge" in args)
        else:
            asyncio.run(remove_overlay() if "--remove-overlay" in args else main())
    except KeyboardInterrupt:
        pass
