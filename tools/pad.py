#!/usr/bin/env python3
"""Shows, live, which gamepad buttons the invasor service can see, per device.

    python3 tools/pad.py            # 20 seconds
    python3 tools/pad.py 60

Useful when a combo doesn't work on some controller: if its presses don't show
up here, the service can't see them either.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from invasor.gamepad import pads, watch_buttons  # noqa: E402


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    found = list(pads())
    if not found:
        print("No readable controller found.")
        return 1
    for dev, name, kind in found:
        print(f"reading {dev:18} {kind:5} {name}")
        label = f"{name} ({kind})"

        def show(held, label=label):
            print(f"  {time.strftime('%H:%M:%S')} {label}: {' + '.join(sorted(held)) or '(none)'}", flush=True)

        threading.Thread(target=lambda d=dev, k=kind, s=show: _safe(d, k, s), daemon=True).start()
    print(f"Press buttons for {seconds:.0f} s…", flush=True)
    time.sleep(seconds)
    return 0


def _safe(dev, kind, show):
    try:
        watch_buttons(dev, kind, show)
    except OSError as e:
        print(f"  {dev}: {e}")


if __name__ == "__main__":
    sys.exit(main())
