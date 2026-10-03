"""Watches gamepads for the "open overlay" button combo.

Two kinds of source, both read-only and non-exclusive (Steam and games keep
receiving every input), both readable without root because logind/udev grant the
seat user access:

- evdev gamepads (/dev/input/event*): DualSense, Xbox pads, Steam's own virtual pad.
- Steam Deck protocol over hidraw (Valve vendor id 28de): a real Steam Deck, or a
  handheld virtualised as one by InputPlumber (e.g. Legion Go -> "deck-uhid").
  These never show up as evdev gamepads while Steam owns them. The report layout
  is Valve's hardware protocol (see the kernel's drivers/hid/hid-steam.c), not
  Steam client internals, so it doesn't change with Steam updates.
"""
import asyncio
import logging
import os
import struct
import threading
import time
from pathlib import Path

log = logging.getLogger("invasor.gamepad")

EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit
EV_KEY = 0x01
EVDEV_BUTTONS = {
    0x130: "A", 0x131: "B", 0x133: "X", 0x134: "Y", 0x136: "LB", 0x137: "RB",
    0x13A: "BACK", 0x13B: "START", 0x13C: "GUIDE", 0x13D: "L3", 0x13E: "R3",
}
BTN_SOUTH = 0x130

VALVE_VENDOR = 0x28DE
DECK_STATE = 0x09  # report type byte (data[2]) of the Deck's controller state
# (byte, bit) of each button in a Deck state report, as in hid-steam.c.
DECK_BUTTONS = {
    "RT": (8, 0), "LT": (8, 1), "RB": (8, 2), "LB": (8, 3),
    "Y": (8, 4), "B": (8, 5), "X": (8, 6), "A": (8, 7),
    "UP": (9, 0), "RIGHT": (9, 1), "LEFT": (9, 2), "DOWN": (9, 3),
    "BACK": (9, 4), "GUIDE": (9, 5), "START": (9, 6), "L5": (9, 7),
    "R5": (10, 0), "L3": (10, 6), "R3": (11, 2),
    "L4": (13, 1), "R4": (13, 2), "QAM": (14, 2),
}

BUTTONS = sorted(set(EVDEV_BUTTONS.values()) | set(DECK_BUTTONS))
RESCAN_SECONDS = 5
# One physical pad often shows up twice (a DualSense plus Steam's virtual X-Box pad,
# a Deck hidraw plus Steam's pad…), so a combo seen on several devices fires once.
COMBO_COOLDOWN = 0.5
DEFAULT_COMBO = ("L3", "R3")


def parse_combo(combo):
    """Button names for a combo from config; a bad config falls back to L3+R3 instead of crashing."""
    try:
        names = {str(b).upper() for b in combo}
        if names and names <= set(BUTTONS):
            return names
    except TypeError:
        pass
    log.error("invalid open_combo %r (valid: %s); using %s", combo, ", ".join(BUTTONS), "+".join(DEFAULT_COMBO))
    return set(DEFAULT_COMBO)


def deck_pressed(report):
    """Names of the buttons held in a Steam Deck state report, or None if it isn't one."""
    if len(report) < 15 or report[0] != 0x01 or report[2] != DECK_STATE:
        return None
    return {name for name, (byte, bit) in DECK_BUTTONS.items() if report[byte] >> bit & 1}


def _evdev_pads():
    for d in Path("/sys/class/input").glob("event*"):
        try:
            name = (d / "device/name").read_text().strip()
            keys = (d / "device/capabilities/key").read_text().split()
            # Capability bitmap: hex words, most significant first. Gamepads report BTN_SOUTH.
            bits = int("".join(w.zfill(16) for w in keys) or "0", 16)
        except (OSError, ValueError):
            continue
        dev = f"/dev/input/{d.name}"
        if bits >> BTN_SOUTH & 1 and os.access(dev, os.R_OK):
            yield dev, name, "evdev"


def _deck_pads():
    for d in Path("/sys/class/hidraw").glob("hidraw*"):
        try:
            uevent = dict(line.split("=", 1) for line in (d / "device/uevent").read_text().splitlines() if "=" in line)
            vendor = int(uevent.get("HID_ID", "0:0:0").split(":")[1], 16)
        except (OSError, ValueError, IndexError):
            continue
        dev = f"/dev/{d.name}"
        if vendor == VALVE_VENDOR and os.access(dev, os.R_OK):
            yield dev, uevent.get("HID_NAME", d.name), "deck"


def pads():
    yield from _evdev_pads()
    yield from _deck_pads()


def watch_buttons(dev, kind, on_change):
    """Blocking: call on_change(set_of_held_names) whenever the held buttons change."""
    held = set()
    with open(dev, "rb", buffering=0) as f:
        if kind == "evdev":
            while data := f.read(EVENT.size):
                _, _, etype, code, value = EVENT.unpack(data)
                name = EVDEV_BUTTONS.get(code)
                if etype != EV_KEY or name is None or value == 2:
                    continue
                (held.add if value else held.discard)(name)
                on_change(set(held))
        else:
            # Deck pads stream state reports continuously (~250/s); only changes matter,
            # so skip decoding unless the button bytes (8..14) differ from last time.
            last = None
            while data := f.read(64):
                if data[8:15] == last:
                    continue
                now = deck_pressed(data)
                if now is None:
                    continue
                last = data[8:15]
                if now != held:
                    held = now
                    on_change(set(held))


class ComboWatcher:
    """Calls `on_combo()` (in the event loop) when all combo buttons are held together."""

    def __init__(self, combo, on_combo):
        self.combo = parse_combo(combo)
        self.on_combo = on_combo
        self._loop = None
        self._last_fire = 0.0
        self._lock = threading.Lock()

    def set_combo(self, combo):
        """Change the combo live (readers check self.combo on every change)."""
        self.combo = parse_combo(combo)
        log.info("open combo: %s", "+".join(sorted(self.combo)))

    async def run(self):
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._watch, daemon=True, name="gamepad").start()
        await asyncio.Event().wait()  # runs for the life of the service, like the other services

    def _watch(self):
        readers = {}
        while True:
            # Never let one odd device (or sysfs file) end this thread: the combo would be
            # gone until the service restarts.
            try:
                for dev, name, kind in pads():
                    if dev not in readers or not readers[dev].is_alive():
                        readers[dev] = threading.Thread(target=self._read, args=(dev, name, kind), daemon=True)
                        readers[dev].start()
            except Exception:
                log.exception("gamepad scan failed")
            time.sleep(RESCAN_SECONDS)

    def _read(self, dev, name, kind):
        log.info("watching %s (%s, %s)", dev, name, kind)
        state = {"fired": False}

        def on_change(held):
            if self.combo <= held and not state["fired"]:
                state["fired"] = True  # once per press; re-arms when released
                self._fire()
            elif not (self.combo & held):
                state["fired"] = False

        try:
            watch_buttons(dev, kind, on_change)
        except OSError as e:
            log.info("%s closed: %s", dev, e)
        except Exception:
            log.exception("reading %s failed", dev)  # the next scan starts a fresh reader

    def _fire(self):
        with self._lock:
            now = time.monotonic()
            if now - self._last_fire < COMBO_COOLDOWN:
                return
            self._last_fire = now
        self._loop.call_soon_threadsafe(self.on_combo)
