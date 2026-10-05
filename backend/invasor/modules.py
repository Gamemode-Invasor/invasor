"""Discovers and loads modules (the contract is in docs/MODULES.md) from two places:
- the core's own, <install>/modules/<id>/ (demo, the _example template), replaced
  whenever Invasor is updated;
- the user's, ~/.local/share/invasor/user-modules/<id>/ (installed from a zip in
  Settings or with tools/install_module.py), never touched by Invasor's installer.
A user module with the same id as a core one wins (logged).

A module folder (its name is the module id) holds:
- module.json (required): validated by schema.parse_manifest; a bad one is reported
  in the panel and the module isn't loaded.
- backend.py (optional): METHODS, setup(ctx), teardown(), upgrade(ctx, previous_version)
  (before setup when the version changed), uninstall(ctx, purge) (before its files go).
- dist/ui.js (optional, built from ui.ts): injected on its own, after the core.
Folders starting with "_" or "." are templates/hidden and never loaded.
"""
import importlib.util
import json
import logging
import shutil
import sys
import threading

from . import __version__, config, schema, tomlio
from .schema import Unavailable
from .storage import JsonStore

log = logging.getLogger("invasor.modules")

# What a module's code may raise without taking the service down. SystemExit too: a
# module calling exit() must not stop Invasor (KeyboardInterrupt is left alone).
MODULE_ERRORS = (Exception, SystemExit)

MODULES_DIR = config.ROOT / "modules"
USER_MODULES_DIR = config.USER_MODULES_DIR
DATA_DIR = config.CONFIG_DIR / "modules"
# How long uninstall(ctx, purge) may take before the module is removed anyway.
UNINSTALL_TIMEOUT = 10
# How long teardown() may take before the module is considered stopped anyway.
TEARDOWN_TIMEOUT = 10


class Settings:
    """A module's settings, always valid against its module.json schema."""

    def __init__(self, module_id, fields, logger):
        self._store = JsonStore(DATA_DIR / module_id / "settings.json")
        self._fields = fields
        self._log = logger
        self._listeners = []

    def get(self, key=None):
        """All settings ({key: value}), or one. Missing/invalid stored values read as defaults."""
        values = {}

        def heal(stored):
            # Invalid or obsolete stored values are corrected in the file itself, so the
            # warning about them is logged once, not on every read.
            values.update(schema.clean(self._fields, stored, self._log))
            return {k: values[k] for k in stored if k in values}

        self._store.transform(heal)
        if key is None:
            return values
        if key not in values:
            raise schema.InvalidArgument(f"unknown setting {key!r}")
        return values[key]

    def set(self, key, value, notify=False):
        """Validate, normalize and save one setting; returns the value stored."""
        field = self._fields.get(key)
        if field is None:
            raise schema.InvalidArgument(f"unknown setting {key!r}")
        value = schema.coerce(field, value)
        self._store.update(**{key: value})
        if notify:
            for cb in list(self._listeners):
                try:
                    cb(key, value)
                except MODULE_ERRORS:
                    self._log.exception("settings on_change callback failed")
        return value

    def on_change(self, callback):
        """callback(key, value) whenever the user changes a setting from the panel."""
        self._listeners.append(callback)

    def clear_listeners(self):
        """The module was unloaded: its callbacks belong to code that no longer runs
        (a re-enabled module registers its own again in setup())."""
        self._listeners.clear()


class Form:
    """A named form of module.json "forms": the module stores its values itself
    (e.g. in another program's config); this validates them with the same rules."""

    def __init__(self, fields):
        self.fields = fields

    def coerce(self, key, value):
        """The value normalized for `key` (clamped, snapped…); InvalidArgument if it can't be."""
        field = self.fields.get(key)
        if field is None:
            raise schema.InvalidArgument(f"unknown field {key!r}")
        return schema.coerce(field, value)

    def clean(self, data):
        """Every field with a valid value: `data`'s where valid, defaults for the rest."""
        return schema.clean(self.fields, data)

    def defaults(self):
        return {k: f["default"] for k, f in self.fields.items()}


class ModuleContext:
    """What a module's backend gets in setup(ctx)."""

    # Errors a module raises to answer cleanly (one log line, no traceback):
    # `raise ctx.InvalidArgument("...")` -> 400 (the caller's mistake),
    # `raise ctx.Unavailable("...")` -> 503 (no network, Steam function missing…).
    InvalidArgument = schema.InvalidArgument
    Unavailable = schema.Unavailable

    # Read/write TOML config files without losing anything (see tomlio.py).
    toml = tomlio

    def __init__(self, module_id, path, game, settings, steam=None, forms=None):
        self.id = module_id
        self.forms = forms or {}  # {name: Form} from module.json "forms"
        self.path = path
        self.game = game  # context.GameContext: .selected / .running
        self.log = logging.getLogger(f"invasor.mod.{module_id}")
        self.settings = settings
        # Free-form storage for whatever isn't a user setting (caches, state…).
        self.data = JsonStore(DATA_DIR / module_id / "data.json")
        self._steam = steam
        self._steam_start_cbs = []
        # Set by the manager once the module is loaded: (cb) -> None, runs cb now if Steam is up.
        self._steam_start_now = None

    def on_steam_start(self, cb):
        """cb() every time a new Steam instance is seen (Steam started or restarted), and
        once soon after registering if Steam is already running. It runs in its own thread
        and may block; an exception is logged."""
        if not callable(cb):
            raise TypeError("on_steam_start needs a function")
        self._steam_start_cbs.append(cb)
        if self._steam_start_now is not None:
            self._steam_start_now(cb)

    def steam_call(self, path, *args, timeout=15):
        """SteamClient.<path>(*args), evaluated in Steam's SharedJSContext. For plain
        (threaded) methods. Raises schema.Unavailable when Steam or that function
        isn't there: always have a plan B. Example: steam_call("Apps.SetShortcutName", appid, name)."""
        if self._steam is None:
            raise Unavailable("SteamClient bridge not configured")
        return self._steam.call_sync(path, *args, timeout=timeout)

    async def steam_call_async(self, path, *args, timeout=15):
        """The same as steam_call(), for `async def` methods."""
        if self._steam is None:
            raise Unavailable("SteamClient bridge not configured")
        return await self._steam.call(path, args, timeout)

    def notify(self, title, body="", icon="", timeout=15):
        """Show a notification as Steam shows an achievement (title, optional body and
        icon: an https URL or data:image/…;base64). For plain (threaded) methods. Raises
        InvalidArgument for bad input and Unavailable when Steam can't show it: have a plan B."""
        if self._steam is None:
            raise Unavailable("SteamClient bridge not configured")
        return self._steam.notify_sync(title, body, icon, timeout=timeout)

    async def notify_async(self, title, body="", icon="", timeout=15):
        """The same as notify(), for `async def` methods."""
        if self._steam is None:
            raise Unavailable("SteamClient bridge not configured")
        return await self._steam.notify(title, body, icon, timeout)

    def game_data(self, appid):
        """Free-form storage for this module and one game."""
        # appid ends up in a file path: only plain numbers, never "../something".
        appid = str(appid)
        if not (appid.isascii() and appid.isdigit()):
            raise ValueError(f"invalid appid {appid!r}")
        return JsonStore(DATA_DIR / self.id / "games" / f"{appid}.json")


def _package(mid):
    return f"invasor_mod_{mid}"


def _forget(pkg):
    """Remove a module's package and its submodules from sys.modules."""
    for name in [n for n in sys.modules if n == pkg or n.startswith(pkg + ".")]:
        del sys.modules[name]


def _teardown(mid, module):
    """module.teardown(), in a thread of its own: one that hangs can't hold up the
    service (disabling, Rescan, replacing it) for more than TEARDOWN_TIMEOUT."""
    if not hasattr(module, "teardown"):
        return

    def run():
        try:
            module.teardown()
        except MODULE_ERRORS:
            log.exception("module %s teardown failed", mid)

    worker = threading.Thread(target=run, name=f"teardown-{mid}", daemon=True)
    worker.start()
    worker.join(TEARDOWN_TIMEOUT)
    if worker.is_alive():
        log.error("module %s: teardown() still running after %ss, considered stopped", mid, TEARDOWN_TIMEOUT)


class ModuleManager:
    def __init__(self, cfg, game, steam=None):
        self.cfg = cfg
        self.game = game
        self.steam = steam  # steam.SteamBridge, handed to modules as ctx.steam_call
        self.manifests = {}  # id -> parsed module.json (+ "dir", or "error")
        self.settings = {}  # id -> Settings
        self.registry = {}  # id -> METHODS, shared live with the API server
        self._loaded = {}  # id -> the imported backend module (for teardown)
        self._contexts = {}  # id -> ModuleContext of a loaded module (its on_steam_start callbacks)
        self.steam_instance = None  # CEF browser id of the running Steam, once seen
        # API methods run in worker threads and the injector in the event loop: one change
        # to the module set at a time, and nobody reads it half-changed.
        self._lock = threading.RLock()

    def steam_started(self, instance):
        """A new Steam instance (from the injector): every loaded module's on_steam_start
        callbacks run. Called from the event loop, which must never wait for the lock (a
        module may be loading in a worker thread), so the work happens in a thread."""
        def run():
            with self._lock:
                log.info("Steam started (%s)", instance)
                self.steam_instance = instance
                for mid, context in list(self._contexts.items()):
                    for cb in list(context._steam_start_cbs):
                        self._run_steam_start(mid, cb)

        threading.Thread(target=run, name="steam-started", daemon=True).start()

    def _run_steam_start(self, mid, cb):
        def run():
            try:
                cb()
            except MODULE_ERRORS:
                log.exception("module %s: on_steam_start callback failed", mid)

        threading.Thread(target=run, name=f"steam-start-{mid}", daemon=True).start()

    def _clear_listeners(self, mid):
        if mid in self.settings:
            self.settings[mid].clear_listeners()

    def _forget_context(self, mid):
        context = self._contexts.pop(mid, None)
        if context is not None:
            context._steam_start_now = None
            context._steam_start_cbs.clear()

    def discover(self):
        with self._lock:
            self.scan()
            for mid in self.manifests:
                if self.enabled(mid):
                    self.load(mid)
            log.info("modules: %s", ", ".join(f"{m}{'' if self.enabled(m) else ' (off)'}" for m in self.manifests) or "(none)")

    def scan(self):
        """Read every module folder's module.json, loading nothing."""
        found = {}
        for source, base in (("core", MODULES_DIR), ("user", USER_MODULES_DIR)):
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if not d.is_dir() or d.name[0] in "_.":
                    continue
                if d.name in found:
                    log.warning("module %s: the installed one (%s) replaces the core's", d.name, d)
                found[d.name] = (d, source)
        if not found:
            log.info("no modules in %s or %s", MODULES_DIR, USER_MODULES_DIR)
        for mid, (d, source) in found.items():
            self._read(d, source)

    def _read(self, d, source):
        """Read one module folder's module.json into self.manifests (bad ones too, with
        their error, so the panel can say why)."""
        if d.name == "core":
            log.error('module folder "core": the name is reserved, skipped')
            return
        try:
            manifest = schema.parse_manifest(json.loads((d / "module.json").read_text()), d.name)
        except (OSError, ValueError) as e:
            # The folder name is the id, so backend, frontend and settings always agree.
            log.error("module %s: invalid module.json: %s", d.name, e)
            self.manifests[d.name] = {"id": d.name, "name": d.name, "dir": d, "source": source,
                                      "error": f"invalid module.json: {e}"}
            return
        problem = schema.core_problem(manifest, __version__)
        if problem:
            log.error("module %s: %s", d.name, problem)
            self.manifests[d.name] = {"id": d.name, "name": manifest["name"], "dir": d, "source": source,
                                      "error": problem}
            return
        manifest["dir"] = d
        manifest["source"] = source
        self.manifests[d.name] = manifest
        self.settings[d.name] = Settings(d.name, manifest["fields"], logging.getLogger(f"invasor.mod.{d.name}"))

    def add(self, mid):
        """(Re)load one user module after it was installed or replaced: its old code is
        torn down first. Returns its listing entry."""
        with self._lock:
            self.unload(mid)
            self.manifests.pop(mid, None)
            self.settings.pop(mid, None)
            self._read(USER_MODULES_DIR / mid, "user")
            if self.enabled(mid):
                self.load(mid)
            return next(m for m in self.listing() if m["id"] == mid)

    def remove(self, mid, purge=False):
        """Forget a user module that is being uninstalled: its code is torn down, then
        its uninstall(ctx, purge) undoes what it left outside its folder. With purge its
        settings and data go too. Nothing the module does can stop the uninstall."""
        with self._lock:
            module = self._loaded.get(mid)
            manifest = self.manifests.get(mid, {})
            if module is None and manifest and "error" not in manifest:
                # Disabled: its code is imported for the hook, without setup().
                try:
                    module = self._import(mid)
                except MODULE_ERRORS:
                    log.exception("module %s: its code doesn't load, uninstalled without its uninstall()", mid)
            self.registry.pop(mid, None)
            self._forget_context(mid)
            if self._loaded.pop(mid, None) is not None:
                _teardown(mid, module)
            # Still importable here: uninstall() may import its own helper files.
            if module is not None and hasattr(module, "uninstall"):
                self._run_uninstall(mid, module, purge)
            _forget(_package(mid))
            self.manifests.pop(mid, None)
            self.settings.pop(mid, None)
            if purge:
                shutil.rmtree(DATA_DIR / mid, ignore_errors=True)

    def _run_uninstall(self, mid, module, purge):
        def run():
            try:
                module.uninstall(context, purge)
            except MODULE_ERRORS:
                log.exception("module %s: uninstall() failed", mid)

        try:
            context = self._context(mid)
        except MODULE_ERRORS:
            log.exception("module %s: no context for uninstall()", mid)
            return
        worker = threading.Thread(target=run, name=f"uninstall-{mid}", daemon=True)
        worker.start()
        worker.join(UNINSTALL_TIMEOUT)
        if worker.is_alive():
            log.error("module %s: uninstall() still running after %ss, uninstalled anyway", mid, UNINSTALL_TIMEOUT)

    def rescan(self):
        """Read the module folders again (⚙ Settings › Rescan modules): every module is
        torn down and loaded afresh, so added, removed, renamed or edited ones are seen
        without restarting the service."""
        with self._lock:
            for mid in list(self.manifests):
                self.unload(mid)
            self.manifests.clear()
            self.settings.clear()
            self.discover()

    def enabled(self, mid):
        return mid not in self.cfg.get("disabled_modules", [])

    def _version_store(self, mid):
        """The version of the module last loaded (upgrade() is called when it changes)."""
        return JsonStore(DATA_DIR / mid / "version.json")

    def _context(self, mid):
        manifest = self.manifests[mid]
        forms = {name: Form(fields) for name, fields in manifest["form_fields"].items()}
        return ModuleContext(mid, manifest["dir"], self.game, self.settings[mid], self.steam, forms)

    def _import(self, mid):
        """Import a module's backend.py as a package (without running setup()). Returns
        it, or None if it has none. Raises what the module's code raises."""
        manifest = self.manifests[mid]
        backend = manifest["dir"] / "backend.py"
        if not backend.exists():
            return None
        # Loaded as a package (backend.py is its __init__), so it can import its own
        # helper files: `from . import helpers` -> modules/<id>/helpers.py.
        pkg = _package(mid)
        # A .pyc is trusted on the source's mtime (whole seconds) and size: code replaced
        # within the same second by a same-sized file would run stale. Always compile afresh.
        for cache in manifest["dir"].rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        spec = importlib.util.spec_from_file_location(pkg, backend, submodule_search_locations=[str(manifest["dir"])])
        module = importlib.util.module_from_spec(spec)
        sys.modules[pkg] = module
        spec.loader.exec_module(module)
        # Checked before setup(): a module rejected here never starts anything.
        methods = getattr(module, "METHODS", {})
        if not isinstance(methods, dict) or not all(isinstance(k, str) and callable(v) for k, v in methods.items()):
            raise TypeError("METHODS must be a dict of {name: function}")
        return module

    def load(self, mid):
        """Load a module's backend. A broken module is logged and skipped, never fatal."""
        with self._lock:
            manifest = self.manifests[mid]
            if "error" in manifest:
                return False
            if mid in self.registry:
                return True
            try:
                module = self._import(mid)
                previous = self._version_store(mid).load().get("version")
                if module is not None:
                    context = self._context(mid)
                    if previous is not None and previous != manifest["version"] and hasattr(module, "upgrade"):
                        # Not recorded if it fails: the next load tries again.
                        log.info("module %s: upgrading from %s to %s", mid, previous, manifest["version"])
                        module.upgrade(context, previous)
                    if hasattr(module, "setup"):
                        try:
                            module.setup(context)
                        except MODULE_ERRORS:
                            _teardown(mid, module)  # release whatever setup() got to start
                            raise
                if previous != manifest["version"]:
                    self._version_store(mid).save({"version": manifest["version"]})
                self.registry[mid] = dict(getattr(module, "METHODS", {}))
                if module is not None:
                    self._loaded[mid] = module
                    self._adopt_context(mid, context)
                return True
            except MODULE_ERRORS:
                log.exception("module %s failed to load", mid)
                self._clear_listeners(mid)  # whatever setup() registered before failing
                _forget(_package(mid))
                return False

    def _adopt_context(self, mid, context):
        """Steam start callbacks registered in setup() run now if Steam is already up;
        later ones as soon as they're registered."""
        def now(cb):
            if self.steam_instance is not None:
                self._run_steam_start(mid, cb)

        self._contexts[mid] = context
        context._steam_start_now = now
        for cb in list(context._steam_start_cbs):
            now(cb)

    def unload(self, mid):
        """Stop serving its methods and let it release what it holds (teardown()).
        Its code is dropped from sys.modules: enabling it again starts afresh."""
        with self._lock:
            self.registry.pop(mid, None)
            self._forget_context(mid)
            module = self._loaded.pop(mid, None)
            if module is not None:
                _teardown(mid, module)
            self._clear_listeners(mid)
            _forget(_package(mid))

    def shutdown(self):
        with self._lock:
            for mid in list(self._loaded):
                self.unload(mid)

    def set_enabled(self, mid, enabled):
        with self._lock:
            if mid not in self.manifests:
                raise KeyError(f"unknown module {mid}")
            disabled = set(self.cfg.get("disabled_modules", []))
            if enabled:
                disabled.discard(mid)
                self.load(mid)
            else:
                disabled.add(mid)
                self.unload(mid)
            self.cfg["disabled_modules"] = sorted(disabled)
            JsonStore(config.CONFIG_FILE).update(disabled_modules=self.cfg["disabled_modules"])

    def ui_scripts(self):
        """[(id, path of dist/ui.js)] of every valid module with a UI, enabled or not:
        a disabled module's UI is injected too, so enabling it needs no re-injection.
        No lock: the injector calls it from the event loop; it reads a copy."""
        out = []
        for mid, m in list(self.manifests.items()):
            path = m["dir"] / "dist" / "ui.js"
            if "error" not in m and path.is_file():
                out.append((mid, path))
        return out

    def listing(self):
        with self._lock:
            out = []
            for mid, m in self.manifests.items():
                d = m["dir"]
                out.append({
                    "id": mid,
                    "name": m["name"],
                    # Short label for the module's tab in the panel (falls back to name).
                    "tab": m.get("tab") or m["name"],
                    "version": m.get("version"),
                    "description": m.get("description", ""),
                    "author": m.get("author_name", ""),  # the name only: the email never reaches the UI
                    "order": m.get("order", 100),
                    "enabled": self.enabled(mid),
                    "loaded": mid in self.registry,
                    "error": m.get("error"),
                    # "core": shipped with Invasor; "user": installed by the user (can be uninstalled).
                    "source": m.get("source", "core"),
                    # Has a UI script (built or not): the panel then expects it to register.
                    "ui": (d / "dist" / "ui.js").is_file() or (d / "ui.ts").is_file(),
                    "ui_built": (d / "dist" / "ui.js").is_file(),
                    "settings": m.get("settings", []),
                    "no_qam": m.get("no_qam", False),
                    "forms": m.get("forms", {}),
                })
            # The user's order first; the modules it doesn't mention follow by their module.json order.
            rank = {mid: i for i, mid in enumerate(self.cfg.get("module_order", []))}
            out.sort(key=lambda m: (0, rank[m["id"]], 0, "") if m["id"] in rank
                     else (1, m["order"], m["name"].lower(), m["id"]))
            return out
