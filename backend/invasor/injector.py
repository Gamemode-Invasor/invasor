"""Finds Steam's CEF windows and keeps the frontend bundle injected into them."""
import asyncio
import json
import logging
from pathlib import Path

from . import __version__, cef
from .schema import Unavailable

log = logging.getLogger("invasor.injector")

POLL_INTERVAL = 3


async def _destroy_overlay(target):
    """Remove the overlay (core and module UIs) from one window. True if it had one."""
    try:
        session = await cef.CDPSession.connect(target["webSocketDebuggerUrl"])
    except Exception:
        return False
    try:
        return await session.evaluate("(() => { const had = !!window.__invasor; window.__invasor?.destroy(); return had; })()") is True
    except Exception as e:
        log.debug("cleanup of %s failed: %s", target.get("title"), e)
        return False
    finally:
        await session.close()


async def remove_overlays():
    """Remove the overlay from every Steam window, now (uninstall: `python3 -m invasor
    --remove-overlay`, with the service already stopped). Returns how many had one."""
    targets = [t for t in await cef.list_targets() if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    done = await asyncio.gather(*(asyncio.wait_for(_destroy_overlay(t), 5) for t in targets), return_exceptions=True)
    return sum(r is True for r in done)


class Injector:
    def __init__(self, cfg, ui_scripts=lambda: [], on_steam_start=lambda instance: None):
        self.cfg = cfg
        self._ui_scripts = ui_scripts  # () -> [(module id, path of its dist/ui.js)]
        self._on_steam_start = on_steam_start  # (browser id) -> None, once per Steam instance
        self._steam_instance = None  # the last CEF browser id seen
        self._sessions = {}  # target id -> task
        self._live = {}  # target id -> (connected CDPSession, role)
        self._seen_titles = None
        self._cleaned = set()  # ids of untargeted main windows already cleaned
        self._background = set()  # strong refs to fire-and-forget tasks
        self._shared = None  # CDPSession to SharedJSContext, opened on demand
        self._shared_lock = None  # made on first use: on Python 3.9 asyncio.Lock() binds to the running loop when created

    def _role(self, target):
        """Role of this window per cfg["targets"], or None if we leave it alone."""
        if target.get("type") != "page" or not target.get("webSocketDebuggerUrl"):
            return None
        url = str(target.get("url") or "")
        title = str(target.get("title") or "")
        for rule in self.cfg["targets"]:
            # url_contains: one string, or a list that must all be present.
            needles = rule.get("url_contains", [])
            if isinstance(needles, str):
                needles = [needles]
            if not all(n in url for n in needles):
                continue
            if "title_prefix" in rule and not title.startswith(rule["title_prefix"]):
                continue
            if "title_not_prefix" in rule and title.startswith(rule["title_not_prefix"]):
                continue
            return rule["role"]
        return None

    def _payload(self, role):
        bundle = Path(self.cfg["bundle"]).read_text()
        front_cfg = {"apiPort": self.cfg["api_port"], "token": self.cfg["token"], "role": role, "version": __version__}
        return f"window.__INVASOR_CFG = {json.dumps(front_cfg)};\n{bundle}\n;true"

    @staticmethod
    def _module_payload(mid, script):
        """A module's UI, evaluated on its own: a syntax or runtime error in it can't
        touch the core or the other modules. It hands its definition to the core."""
        return (
            "(function () {\n"
            f"{script}\n"
            f";window.__invasorKit.register({json.dumps(mid)}, "
            "typeof __invasorModule === 'undefined' ? undefined : __invasorModule.default);\n"
            "})();\ntrue"
        )

    async def run(self):
        log.info("waiting for Steam CEF on %s:%s", cef.CEF_HOST, cef.CEF_PORT)
        while True:
            # One bad round (an odd window, a bug) is logged and retried: the injector
            # must never stop, or the overlay is gone until the service restarts.
            try:
                await self._round()
            except Exception:
                log.exception("injector round failed")
            await asyncio.sleep(POLL_INTERVAL)

    async def _check_steam_instance(self):
        # A new browser id is a new Steam process (started, restarted, Desktop <-> Game Mode).
        # None (not reachable right now) keeps the last one: a blip never counts as a restart.
        instance = await cef.browser_id()
        if instance is not None and instance != self._steam_instance:
            self._steam_instance = instance
            try:
                self._on_steam_start(instance)
            except Exception:
                log.exception("Steam start handler failed")

    async def _round(self):
        await self._check_steam_instance()
        targets = await cef.list_targets()
        titles = sorted({str(t.get("title") or "") for t in targets if t.get("type") == "page"})
        if titles != self._seen_titles:
            # Diagnostics: window names differ between Steam versions and desktop/Game Mode.
            log.debug("CEF windows: %s", titles)
            for t in targets:
                if t.get("type") == "page":
                    log.debug("  %r -> %s", t.get("title", ""), str(t.get("url", ""))[:200])
            self._seen_titles = titles
        # Forget finished sessions of windows that no longer exist.
        alive = {t["id"] for t in targets}
        for tid in [k for k, task in self._sessions.items() if task.done() and k not in alive]:
            del self._sessions[tid]
        for t in targets:
            tid = t["id"]
            role = self._role(t)
            if role and (tid not in self._sessions or self._sessions[tid].done()):
                self._sessions[tid] = asyncio.create_task(self._handle(t, role))
            elif not role and self._is_main_window(t) and tid not in self._cleaned:
                self._cleaned.add(tid)
                task = asyncio.create_task(self._clean(t))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
        self._cleaned &= alive

    @staticmethod
    def _is_main_window(target):
        # Steam's own top-level windows carry their useragent in the URL (desktop or gamepad UI).
        return target.get("type") == "page" and "useragent=Valve%20Steam" in str(target.get("url") or "") and bool(target.get("webSocketDebuggerUrl"))

    async def _clean(self, target):
        """Remove an overlay left in a main window we no longer target (e.g. dev_desktop
        switched off), instead of leaving it there until Steam restarts."""
        if await _destroy_overlay(target):
            log.info("removed leftover overlay from %s", target.get("title"))

    async def _handle(self, target, role):
        name = f"{target.get('title')} [{role} {target['id'][:8]}]"
        try:
            session = await cef.CDPSession.connect(target["webSocketDebuggerUrl"])
        except Exception as e:
            log.warning("connect to %s failed: %s", name, e)
            return
        reinject = asyncio.Event()

        def on_event(method, params):
            # Page reloaded / new JS context: our code is gone, put it back.
            if method == "Runtime.executionContextCreated" and params.get("context", {}).get("auxData", {}).get("isDefault", True):
                reinject.set()

        session.on_event(on_event)
        self._live[target["id"]] = (session, role)
        try:
            await session.send("Runtime.enable")  # emits executionContextCreated for the current context
            while not session.closed:
                closed = asyncio.create_task(session.wait_closed())
                wanted = asyncio.create_task(reinject.wait())
                await asyncio.wait({closed, wanted}, return_when=asyncio.FIRST_COMPLETED)
                closed.cancel()
                wanted.cancel()
                if session.closed:
                    break
                await asyncio.sleep(0.5)  # let the page settle, and coalesce event bursts
                reinject.clear()
                await self._inject(session, name, role)
        except Exception as e:
            log.warning("session %s ended: %s", name, e)
        finally:
            self._live.pop(target["id"], None)
            await session.close()
            log.info("lost %s", name)

    async def _inject(self, session, name, role):
        try:
            payload = self._payload(role)
        except OSError as e:
            log.error("cannot read bundle %s: %s", self.cfg["bundle"], e)
            return
        try:
            await session.evaluate(payload)
        except Exception as e:
            log.error("injection into %s failed: %s", name, e)
            return
        failed = []
        for mid, path in self._ui_scripts():
            try:
                await session.evaluate(self._module_payload(mid, Path(path).read_text()))
            except Exception as e:
                failed.append(mid)
                log.error("module %s: its UI failed to load in %s: %s", mid, name, e)
        log.info("injected into %s%s", name, f" (UI failed: {', '.join(failed)})" if failed else "")

    async def inject_module(self, mid, path):
        """Inject one module's UI into every window we're in, now (a module just
        installed): its kit.register then updates the open panels."""
        try:
            payload = self._module_payload(mid, Path(path).read_text())
        except OSError as e:
            log.error("module %s: can't read its UI: %s", mid, e)
            return
        for result in await self.evaluate_all(payload, timeout=10):
            if result is not True:
                log.warning("module %s: its UI didn't load in a window", mid)

    async def evaluate_all(self, expression, timeout=2, role=None):
        """Evaluate JS in every window we're injected into, in parallel; returns the values
        that came back. A hung window is skipped after `timeout` instead of stalling the rest."""

        async def one(session):
            try:
                return await asyncio.wait_for(session.evaluate(expression), timeout)
            except Exception as e:
                log.debug("evaluate_all failed: %s", e)
                return None

        sessions = [s for s, r in list(self._live.values()) if role is None or r == role]
        return list(await asyncio.gather(*(one(s) for s in sessions)))

    async def evaluate_shared(self, expression, timeout=15):
        """Evaluate JS in Steam's SharedJSContext page (where SteamClient lives). The
        session is opened on first use and reused while it lasts."""
        if self._shared_lock is None:
            self._shared_lock = asyncio.Lock()
        async with self._shared_lock:
            if self._shared is None or self._shared.closed:
                target = next((t for t in await cef.list_targets()
                               if t.get("title") == "SharedJSContext" and t.get("webSocketDebuggerUrl")), None)
                if target is None:
                    raise Unavailable("Steam's SharedJSContext isn't reachable")
                try:
                    self._shared = await cef.CDPSession.connect(target["webSocketDebuggerUrl"])
                except Exception as e:
                    raise Unavailable(f"can't connect to SharedJSContext: {e}") from None
            session = self._shared
        try:
            return await asyncio.wait_for(session.evaluate(expression, timeout), timeout + 1)
        except asyncio.TimeoutError:
            raise Unavailable(f"SharedJSContext didn't answer in {timeout}s") from None
