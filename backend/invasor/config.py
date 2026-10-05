import json
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("invasor.config")

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "invasor"
CONFIG_FILE = CONFIG_DIR / "config.json"
# Modules installed by the user (zip from Settings, tools/install_module.py): outside the
# core's install folder, so updating Invasor never touches them.
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "invasor"
USER_MODULES_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "invasor" / "user-modules"

DEFAULTS = {
    # STEAM TOUCHPOINT: which CEF windows get the overlay, and in which role. Window titles are
    # localised by Steam, so match on things that aren't: URL params / internal names.
    "targets": [
        # Main window of Steam's gamepad UI: Game Mode and Big Picture. The desktop
        # client window (useragent "Valve Steam Client") is deliberately left alone.
        # Only the useragent: browserType has been seen as both 3 and 4 for this window.
        {
            "role": "main",
            "url_contains": ["useragent=Valve%20Steam%20Gamepad"],
            "title_not_prefix": "notificationtoasts",
        },
        # Quick Access (···) window: the one gamescope shows on top of a running game.
        {"role": "quickaccess", "title_prefix": "QuickAccess_"},
    ],
    "api_port": 33801,
    "bundle": str(ROOT / "frontend" / "dist" / "invasor.js"),
    "log_level": "INFO",
    # Gamepad buttons held together to open/close the overlay (see gamepad.BUTTONS).
    "open_combo": ["L3", "R3"],
    # Which side the panel opens on: "auto" (right in the library, left over Quick Access), "left", "right".
    "panel_side": "auto",
    # Accent colour of the overlay and its "I" handle (one of ACCENT_COLORS).
    "accent_color": "blue",
    # What the handle shows: the Invasor icon, the letter "I", or nothing but the coloured tab (HANDLE_ICONS).
    "handle_icon": "icon",
    # Module ids in the order the user wants their tabs; the ones not listed follow, by their module.json order.
    "module_order": [],
    # Module ids switched off from the panel.
    "disabled_modules": [],
    # DEVELOPMENT ONLY: also inject into the desktop client window, so the UI can be
    # tested (tools/smoke.py) without Game Mode/Big Picture. Keep false in normal use.
    "dev_desktop": False,
    # Learned, not user-set: on-screen width of the Quick Access column (see overlay.ts).
    "qam_visible_w": None,
    # Look for a newer Invasor release on GitHub (at startup, then daily) and tell Steam about it.
    "update_check": True,
    # Which releases to follow: "stable", or "beta" (also the pre-releases, X.Y.Z-rcN).
    "update_channel": "stable",
    # Learned: the last version announced in a Steam notification, so each is announced once.
    "update_last_notified": "",
}

PANEL_SIDES = ("auto", "left", "right")
UPDATE_CHANNELS = ("stable", "beta")
HANDLE_ICONS = ("icon", "letter", "none")
ACCENT_COLORS = ("blue", "yellow", "green", "red", "purple", "white")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


MODULE_ORDER_MAX = 200


def _id_list(v):
    """A module order: module ids, each once."""
    return _str_list(v) and len(v) <= MODULE_ORDER_MAX and len(set(v)) == len(v)


# What a valid value looks like for each key. A bad one falls back to its default with
# a warning: a typo in config.json must never stop the service from starting.
def _target_rule(t):
    """One "targets" rule: {"role": str, "url_contains": str | [str], "title_prefix": str, "title_not_prefix": str}."""
    if not isinstance(t, dict) or not isinstance(t.get("role"), str):
        return False
    if set(t) - {"role", "url_contains", "title_prefix", "title_not_prefix"}:
        return False
    needles = t.get("url_contains", [])
    if not (isinstance(needles, str) or _str_list(needles)):
        return False
    return all(isinstance(t[k], str) for k in ("title_prefix", "title_not_prefix") if k in t)


VALID = {
    "targets": lambda v: isinstance(v, list) and all(_target_rule(t) for t in v),
    "api_port": lambda v: _is_int(v) and 1 <= v <= 65535,
    "bundle": lambda v: isinstance(v, str) and bool(v),
    "log_level": lambda v: v in LOG_LEVELS,
    "open_combo": lambda v: _str_list(v) and bool(v),  # button names are checked by gamepad.parse_combo
    "panel_side": lambda v: v in PANEL_SIDES,
    "accent_color": lambda v: v in ACCENT_COLORS,
    "handle_icon": lambda v: v in HANDLE_ICONS,
    "disabled_modules": _str_list,
    "module_order": _id_list,
    "dev_desktop": lambda v: isinstance(v, bool),
    "qam_visible_w": lambda v: v is None or (_is_int(v) and 200 <= v <= 4000),
    "update_check": lambda v: isinstance(v, bool),
    "update_channel": lambda v: v in UPDATE_CHANNELS,
    "update_last_notified": lambda v: isinstance(v, str),
}

DEV_DESKTOP_TARGET = {
    "role": "main",
    "url_contains": ["useragent=Valve%20Steam%20Client"],
    "title_not_prefix": "notificationtoasts",
}


def load():
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_FILE.read_text())
        if not isinstance(user, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        user = {}
    except Exception as e:
        log.error("invalid %s (%s), using defaults", CONFIG_FILE, e)
        user = {}
    for key, value in user.items():
        check = VALID.get(key)
        if check is None:
            log.warning("unknown key %r in %s, ignored", key, CONFIG_FILE)
        elif check(value):
            cfg[key] = value
        else:
            log.warning("invalid %s %r in %s, using the default %r", key, value, CONFIG_FILE, DEFAULTS[key])
    if cfg.get("dev_desktop"):
        log.warning("dev_desktop on: invasor is injected into the desktop client too (development only)")
        cfg["targets"] = [*cfg["targets"], DEV_DESKTOP_TARGET]  # new list: never touch DEFAULTS
    if env_bundle := os.environ.get("INVASOR_BUNDLE"):
        cfg["bundle"] = env_bundle
    # Fresh per run: only code we inject knows it, so other local pages can't call the API.
    cfg["token"] = secrets.token_urlsafe(24)
    return cfg
