"""Steam's CEF debugging flag: Steam exposes DevTools on :8080 only if this empty file exists in its
folder when it starts. The installer creates it; something else (a Steam reset, Decky removing itself)
may delete it later, so the service puts it back each time it starts.

A flag made here is Invasor's own, like one made by the installer: the same marker file tells
`invasor-installation.sh --uninstall` it may remove it (and it still never does when Decky is installed).
"""
import logging
import os
from pathlib import Path

from . import config

log = logging.getLogger("invasor.cef_flag")

HOME = Path.home()
FLAG_NAME = ".cef-enable-remote-debugging"
MARKER_NAME = ".cef-flag-created"  # in config.DATA_DIR, same as the installer's CEF_MARKER


def steam_dir():
    """Steam's folder, looked for in the installer's order, or None."""
    for d in (HOME / ".steam/steam", HOME / ".local/share/Steam"):
        if d.is_dir():
            return d
    return None


def ensure():
    """Create the flag if it is missing. True only if it was created now. Never raises."""
    steam = steam_dir()
    if steam is None:
        log.debug("no Steam folder found, CEF debugging flag not checked")
        return False
    flag = steam / FLAG_NAME
    if os.path.lexists(flag):
        return False
    try:
        flag.touch()
    except OSError as e:
        log.warning("couldn't create Steam's CEF debugging flag %s: %s", flag, e)
        return False
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / MARKER_NAME).touch()
    except OSError as e:
        log.warning("created %s but not its marker (uninstall will leave it): %s", flag, e)
    log.info("recreated Steam's CEF debugging flag %s (it was missing); restart Steam for it to apply", flag)
    return True
