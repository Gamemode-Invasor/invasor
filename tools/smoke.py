#!/usr/bin/env python3
"""Smoke test against the real, running Steam: run it after every Steam update.

Drives the overlay in Steam's main window with synthetic gamepad events (the same
vgp_onbuttondown events Steam sends) and checks: panel width (40% of the screen),
module tabs (L1/R1), sub-tabs (L2/R2), foldable sections (X), every demo control
(drawn from Demo's module.json), a disabled control, the select list, the built-in
keyboard, the confirm dialog, scrolling of long content, the expanded view, the
module.json rules (when, disabled_when), backend errors, a custom control, the
Settings tab, installing a module zip and its upgrade/uninstall hooks, backend
validation of settings and the isolation of a broken module UI. Needs Steam running with
CEF debugging, the invasor service installed and the "demo" module enabled.

At the end Demo's values and config.json are restored and the service restarted,
so the overlay is re-injected exactly as it was.

    python3 tools/smoke.py
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from invasor import cef, context  # noqa: E402
from invasor.config import CONFIG_DIR, CONFIG_FILE  # noqa: E402

BTN = {"A": 1, "B": 2, "X": 3, "Y": 4, "L1": 5, "R1": 6, "L2": 7, "R2": 8, "UP": 9, "DOWN": 10, "LEFT": 11, "RIGHT": 12}
DEMO_DIR = CONFIG_DIR / "modules" / "demo"
DEMO_SETTINGS = DEMO_DIR / "settings.json"
SR = "document.getElementById('invasor-root').shadowRoot"
# Everything the checks look at: tabs, the control under the blue ring, open popups.
STATE = f"""(() => {{
  const r = {SR};
  const f = r.querySelector('.nav-focus');
  const list = f?.querySelector('.select-list');
  return {{
    open: !r.querySelector('.panel').hidden,
    tab: r.querySelector('.tabbar.main .tab.on')?.textContent ?? null,
    sub: r.querySelector('.tabbar.sub:not([hidden]) .tab.on')?.textContent ?? null,
    label: f ? (f.querySelector('.ctl-label')?.textContent ?? f.textContent).trim() : null,
    value: f?.querySelector('.ctl-value')?.textContent ?? f?.querySelector('.seg.on')?.textContent
           ?? f?.querySelector('input[type=text]')?.value
           ?? f?.querySelector('input[type=password]')?.value ?? null,
    masked: f?.querySelector('input')?.type === 'password',
    on: f?.classList.contains('on') ?? false,
    listOpen: !!list && !list.hidden,
    keyboard: !!f?.querySelector('.kbd'),
    modal: !!r.querySelector('.modal'),
    toast: r.querySelector('.toast:not([hidden])')?.textContent ?? null,
    about: r.querySelector('.pane:not([hidden]) .section:last-of-type')?.textContent ?? '',
    scroll: Math.round(r.querySelector('.content').scrollTop),
    scrollMax: r.querySelector('.content').scrollHeight - r.querySelector('.content').clientHeight,
    moreAbove: r.querySelector('.content').classList.contains('more-above'),
    moreBelow: r.querySelector('.content').classList.contains('more-below'),
    width: r.querySelector('.panel').getBoundingClientRect().width,
    screen: innerWidth,
    realScreen: screen.width,
    nativeBar: r.querySelector('.content').offsetWidth - r.querySelector('.content').clientWidth,
    ownBar: !r.querySelector('.scrollbar').hidden,
    thumbY: r.querySelector('.thumb').getBoundingClientRect().top,
  }};
}})()"""


WINDOW = f"""(() => {{
  const r = {SR};
  const w = r.querySelector('.iwin');
  if (!w) return null;
  const b = w.querySelector('.iwin-box').getBoundingClientRect();
  const f = w.querySelector('.nav-focus');
  return {{
    w: b.width, h: b.height,
    visibleW: parseFloat(r.host.style.getPropertyValue('--visible-w')) || innerWidth, innerH: innerHeight,
    tab: w.querySelector('.tabbar.main .tab.on')?.textContent ?? null,
    sub: w.querySelector('.tabbar.sub:not([hidden]) .tab.on')?.textContent ?? null,
    focus: f ? (f.querySelector('.ctl-label')?.textContent ?? f.textContent).trim() : null,
    grid: f?.querySelector('.tile.hi .cap')?.textContent ?? null,
    info: w.querySelector('.pane:not([hidden]) .ctl-info')?.textContent ?? '',
  }};
}})()"""


class Smoke:
    def __init__(self, session):
        self.s = session
        self.failures = 0

    async def js(self, expr):
        return await self.s.evaluate(expr)

    async def state(self):
        return await self.js(STATE)

    async def press(self, *buttons, wait=0.15):
        for b in buttons:
            await self.js(
                f"document.body.dispatchEvent(new CustomEvent('vgp_onbuttondown', "
                f"{{detail: {{button: {BTN[b]}}}, bubbles: true}}))"
            )
            await asyncio.sleep(wait)
        return await self.state()

    async def goto(self, label, limit=20, button="DOWN"):
        """Move down (or `button`) until the ring is on the control called `label`."""
        st = await self.state()
        for _ in range(limit):
            if st["label"] == label:
                return st
            st = await self.press(button)
        return st

    async def goto_tab(self, label, limit=30):
        """Switch module tabs (L1/R1) until the one called `label` is active."""
        names = await self.js(f"[...{SR}.querySelectorAll('.tabbar.main .tab')].map(t => t.textContent)")
        st = await self.state()
        if label not in names:
            return st
        for _ in range(limit):
            if st["tab"] == label:
                return st
            button = "R1" if names.index(label) > names.index(st["tab"]) else "L1"
            st = await self.press(button, wait=0.4)
        return st

    async def unfold(self, title):
        """Open a folded section of the current tab (⚙ Settings starts with all of them folded)."""
        st = await self.goto(title)
        opened = await self.js(f"""(() => [...{SR}.querySelectorAll('summary')].find(t => t.textContent === {title!r})
          ?.closest('details').open)()""")
        if not opened:
            st = await self.press("A", wait=0.4)
        return st

    async def goto_sub(self, label, limit=10):
        """Switch the panel's sub-tabs (L2/R2) until the one called `label` is active."""
        names = await self.js(f"[...{SR}.querySelectorAll('.panel .tabbar.sub .tab')].map(t => t.textContent)")
        st = await self.state()
        if label not in names or st["sub"] not in names:
            return st
        for _ in range(limit):
            if st["sub"] == label:
                return st
            st = await self.press("R2" if names.index(label) > names.index(st["sub"]) else "L2", wait=0.4)
        return st

    async def field(self, label):
        """A control of the form that has the ring: shown, disabled, value and hint."""
        return await self.js(f"""(() => {{
          const form = {SR}.querySelector('.nav-focus')?.closest('.settings-form');
          const c = [...(form?.querySelectorAll('.ctl') ?? [])].find(c => c.querySelector('.ctl-label')?.textContent === {json.dumps(label)});
          return c ? {{ shown: c.style.display !== 'none', disabled: c.dataset.disabled !== undefined,
                       value: c.querySelector('.ctl-value')?.textContent ?? null, hint: c.dataset.hint ?? '' }} : null;
        }})()""")

    async def api(self, method, feature="core", **args):
        """[status, body] of an API call made from the page (as the panel does): the core's,
        or a module's methods with feature=<id>."""
        return await self.js(f"""(async () => {{ const c = window.__INVASOR_CFG;
          const r = await fetch(`http://127.0.0.1:${{c.apiPort}}/api/{feature}/{method}`, {{method: 'POST',
            headers: {{'Content-Type': 'application/json', 'X-Invasor-Token': c.token}}, body: JSON.stringify({json.dumps(args)})}});
          return [r.status, await r.json()]; }})()""")

    async def module_zip(self):
        """Pack _example as "smoke-example", install it through the API (as Settings does),
        see its tab appear, uninstall it. Leaves nothing behind."""
        import shutil
        import tempfile
        sys.path.insert(0, str(ROOT / "tools"))
        import pack_module
        work = Path(tempfile.mkdtemp(prefix=".invasor-smoke-", dir=Path.home()))
        try:
            mod = work / "smoke-example"
            shutil.copytree(ROOT / "modules" / "_example", mod)
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                zip_path = pack_module.main([str(mod), "--out", str(work)])
            rel = str(zip_path.relative_to(Path.home()))
            st, body = await self.api("module_inspect", path=rel)
            self.check("zip: inspected by the core, author without email", st == 200 and body["result"]["id"] == "smoke-example"
                       and body["result"]["author"] == "FranjeGueje", body)
            st, body = await self.api("module_install", path=rel)
            self.check("zip: installed", st == 200 and body["result"]["source"] == "user", body)
            await self.js("window.__invasor.setOpen(false)")
            await self.js("window.__invasor.setOpen(true)")
            await asyncio.sleep(2.5)
            tabs = await self.js(f"[...{SR}.querySelectorAll('.tabbar.main .tab')].map(t => t.textContent)")
            self.check("zip: its tab is there without a restart", "Example" in tabs, tabs)
            st, body = await self.api("module_install", path=rel)
            self.check("zip: installing again needs replace", st == 400 and "already installed" in body.get("error", ""), body)
            st, _ = await self.api("module_uninstall", id="smoke-example")
            self.check("zip: uninstalled", st == 200)
        finally:
            await self.api("module_uninstall", id="smoke-example")
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(CONFIG_DIR / "modules" / "smoke-example", ignore_errors=True)

    async def module_hooks(self):
        """Install a copy of Demo as "smoke-demo", replace it with a newer version (its
        upgrade() sees the old one), then uninstall it from ⚙ Settings choosing to delete
        its data (its uninstall() removes the file it left outside). Leaves nothing behind."""
        import contextlib
        import io
        import shutil
        import tempfile
        sys.path.insert(0, str(ROOT / "tools"))
        import pack_module
        work = Path(tempfile.mkdtemp(prefix=".invasor-smoke-", dir=Path.home()))
        outside = CONFIG_DIR.parent / "invasor-smoke-demo"
        try:
            mod = work / "smoke-demo"
            shutil.copytree(ROOT / "modules" / "demo", mod, ignore=shutil.ignore_patterns("__pycache__"))

            async def install(version):
                manifest = json.loads((mod / "module.json").read_text())
                (mod / "module.json").write_text(json.dumps({**manifest, "name": "Smoke Demo", "tab": "Smoke Demo", "version": version}))
                out = work / version
                out.mkdir()
                with contextlib.redirect_stdout(io.StringIO()):
                    zip_path = pack_module.main([str(mod), "--out", str(out)])
                return await self.api("module_install", path=str(zip_path.relative_to(Path.home())), replace=True)

            st, _ = await install("0.2.0")
            self.check("hooks: installed", st == 200)
            st, body = await self.api("whoami", feature="smoke-demo")
            self.check("hooks: no upgrade on the first install", st == 200 and body["result"]["upgraded_from"] is None, body)
            st, body = await self.api("profile_set", feature="smoke-demo", key="vsync", value=False)
            self.check("hooks: it left a file outside its folder", st == 200 and outside.is_dir(), body)
            st, _ = await install("0.2.1")
            st, body = await self.api("whoami", feature="smoke-demo")
            self.check("hooks: replaced, its upgrade() got the old version", st == 200 and body["result"]["upgraded_from"] == "0.2.0", body)

            await self.js("window.__invasor.setOpen(false)")
            await self.js("window.__invasor.setOpen(true)")
            await asyncio.sleep(2.5)
            await self.goto_tab("⚙ Settings")
            # Installed behind the panel's back: Rescan draws the module list again.
            await self.unfold("Manage modules")
            await self.goto("Rescan modules", limit=60)
            await self.press("A", wait=3.0)
            # Reordering (two modules are listed now): grab Smoke Demo, move it up, drop it.
            def saved_order():
                return json.loads(CONFIG_FILE.read_text()).get("module_order") if CONFIG_FILE.exists() else None

            await self.unfold("Module order")
            st = await self.goto("Smoke Demo")
            st = await self.press("A", wait=0.3)
            self.check("reorder: A grabs the row and the hint says how to drop it", "drop" in await self.js(f"{SR}.querySelector('.panel .hints').textContent"), st)
            await self.press("UP", wait=0.3)
            st = await self.state()
            self.check("reorder: the ring follows the moved row", st["label"] == "Smoke Demo", st)
            await self.press("B", wait=0.3)
            self.check("reorder: B puts it back and saves nothing", not saved_order(), saved_order())
            await self.press("A", wait=0.3)
            await self.press("UP", wait=0.3)
            await self.press("A", wait=1.0)
            st, body = await self.api("modules")
            ids = [m["id"] for m in body["result"]] if st == 200 else body
            self.check("reorder: dropping saves it and the module list follows", saved_order() and saved_order()[0] == "smoke-demo" and ids[0] == "smoke-demo", (saved_order(), ids))
            await self.goto("Reset order", limit=20)
            await self.press("A", wait=1.5)
            self.check("reorder: Reset order forgets it", not saved_order(), saved_order())
            await self.unfold("Manage modules")
            await self.goto("Rescan modules", limit=60)
            st = await self.goto("Uninstall Smoke Demo", limit=60, button="UP")  # it's above Rescan
            st = await self.press("A", wait=0.3)
            self.check("uninstall: a dialog that starts on Cancel", st["modal"] and st["label"] == "Cancel", st)
            st = await self.press("RIGHT")
            self.check("uninstall: → keep its settings", st["label"] == "Uninstall, keep its settings", st)
            st = await self.press("RIGHT")
            self.check("uninstall: → delete its data", st["label"] == "Uninstall and delete its data", st)
            await self.press("A", wait=2.0)
            st, body = await self.api("modules")
            ids = [m["id"] for m in body["result"]] if st == 200 else body
            self.check("uninstall: the module is gone", st == 200 and "smoke-demo" not in ids, ids)
            self.check("uninstall: its data is deleted", not (CONFIG_DIR / "modules" / "smoke-demo").exists())
            self.check("uninstall: its uninstall() removed what it left outside", not outside.exists())
        finally:
            await self.api("module_uninstall", id="smoke-demo", purge=True)
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(CONFIG_DIR / "modules" / "smoke-demo", ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)

    async def window(self):
        """State of the open big window (None if there isn't one)."""
        return await self.js(WINDOW)

    async def nudge(self):
        """Change the focused value with → (or ← at the end). Returns (before, after, undo button)."""
        before = await self.state()
        after = await self.press("RIGHT")
        if after["value"] != before["value"]:
            return before, after, "LEFT"
        return before, await self.press("LEFT"), "RIGHT"

    def check(self, what, ok, got=None):
        print(f"  {'OK  ' if ok else 'FAIL'} {what}" + ("" if ok else f"  (got: {got})"))
        self.failures += not ok

    async def run(self):
        version = await self.js("window.__invasor?.version ?? null")
        self.check("overlay injected", version is not None, version)
        if version is None:
            return
        instance = await cef.browser_id()
        self.check("Steam instance id readable (/json/version, for on_steam_start)", instance is not None, instance)
        await self.js("window.__invasor.setOpen(false)")
        await self.js("window.__invasor.setOpen(true)")
        await asyncio.sleep(2.5)  # tabs build
        # The overlay remembers the last tab, and there may be any number of modules.
        st = await self.goto_tab("Demo")
        st = await self.goto_sub("Controls")
        self.check("tab Demo › Controls", st["tab"] == "Demo" and st["sub"] == "Controls", st)
        expected = min(st["realScreen"] * 0.4, st["screen"] - 16)
        self.check("panel takes 40% of the screen (never past its window)", abs(st["width"] - expected) <= 4, (st["width"], expected))
        gaps = await self.js(f"""(() => {{ const bar = {SR}.querySelector('.tabbar.sub');
          const row = bar.querySelector('.tabs').getBoundingClientRect(); const t = bar.querySelectorAll('.tab');
          return [bar.dataset.align, t[0].getBoundingClientRect().left - row.left, row.right - t[t.length - 1].getBoundingClientRect().right]; }})()""")
        self.check("sub-tabs centred as Demo asks (tabsAlign)", gaps[0] == "center" and abs(gaps[1] - gaps[2]) <= 4 and gaps[1] > 0, gaps)

        print(" System buttons")
        # A listener below ours (on document, bubbling) sees what reaches Steam's UI.
        got = await self.js("""(() => {
          const seen = [];
          const spy = (e) => seen.push(e.detail.button);
          document.addEventListener('vgp_onbuttondown', spy);
          for (const b of [27, 28, 4]) document.body.dispatchEvent(new CustomEvent('vgp_onbuttondown', {detail: {button: b}, bubbles: true}));
          document.removeEventListener('vgp_onbuttondown', spy);
          return seen;
        })()""")
        self.check("Steam (27) and ··· (28) reach Steam with the panel open", 27 in got and 28 in got, got)
        # Y (4) is one of the panel's buttons (and is a no-op on the section title here).
        self.check("Y (4) doesn't reach Steam with the panel open", 4 not in got, got)
        st = await self.state()
        self.check("panel still open after those buttons", st["open"], st)

        print(" Controls")
        st = await self.goto("Enabled")
        st2 = await self.press("A")
        self.check("toggle: A flips the value", st2["label"] == "Enabled" and st2["on"] != st["on"], st2)
        await self.press("A")

        await self.goto("Volume")
        before, st, _ = await self.nudge()
        self.check("slider: ←→ adjust", st["label"] == "Volume" and st["value"] != before["value"], st)
        st = await self.press("Y")
        self.check("slider: Y default", st["value"] == "50%", st)
        # Held → (fast repeats): saves must not race, the stored value is the one shown.
        await self.press(*["RIGHT"] * 8, wait=0.02)
        await asyncio.sleep(1.0)
        shown = (await self.state())["value"]
        stored = json.loads(DEMO_SETTINGS.read_text()).get("volume") if DEMO_SETTINGS.exists() else None
        self.check("held slider: what's stored is what's shown", shown == f"{stored}%", (shown, stored))
        await self.press("Y")

        await self.goto("FPS limit")
        before, st, _ = await self.nudge()
        self.check("number: ←→ change", st["value"] != before["value"], st)
        st = await self.press("Y")
        self.check("number: Y default", st["value"] == "60", st)

        await self.goto("Mode")
        before, st, undo = await self.nudge()
        self.check("radio: ←→ change", st["value"] != before["value"], st)
        await self.press(undo)

        await self.goto("Quality")
        before, st, undo = await self.nudge()
        self.check("select: ←→ change without opening", st["value"] != before["value"] and not st["listOpen"], st)
        await self.press(undo)
        st = await self.press("A")
        self.check("select: A opens the list", st["listOpen"], st)
        st = await self.press("DOWN", "B")
        self.check("select: B closes just the list", not st["listOpen"] and st["open"], st)

        print(" Sections (X)")
        await self.press("UP", "UP", "UP", "UP", "UP", "UP", "UP", "UP", "UP", "UP")
        st = await self.goto("Values")
        await self.press("LEFT")
        st = await self.press("DOWN")
        self.check("← doesn't fold sections", st["label"] == "Volume", st)
        st = await self.press("X")
        self.check("X inside a section folds it and moves to its title", st["label"] == "Values", st)
        st = await self.press("DOWN")
        self.check("folded section: its content is skipped", st["label"] == "Choices", st)
        await self.press("UP")
        st = await self.press("X", "DOWN")
        self.check("X on the title unfolds it", st["label"] == "Volume", st)

        print(" Text sub-tab")
        st = await self.press("R2", wait=0.4)
        self.check("R2 switches sub-tab", st["sub"] == "Text", st)
        st = await self.goto("Name")
        text0 = st["value"]
        st = await self.press("A")
        self.check("text: A opens the built-in keyboard", st["keyboard"], st)
        st = await self.press("A")
        self.check("keyboard: A types", st["value"] == text0 + "q", st)
        st = await self.press("X")
        self.check("keyboard: X deletes", st["value"] == text0, st)
        st = await self.press("B")
        self.check("keyboard: B closes it, the panel stays", not st["keyboard"] and st["open"], st)
        # Closing the panel while typing keeps the text and closes the keyboard.
        await self.press("A")
        await self.press("A")
        await self.js("window.__invasor.setOpen(false)")
        await asyncio.sleep(0.6)
        stored = json.loads(DEMO_SETTINGS.read_text()).get("name") if DEMO_SETTINGS.exists() else None
        await self.js("window.__invasor.setOpen(true)")
        await asyncio.sleep(1.5)
        kbd_left = await self.js(f"!!{SR}.querySelector('.kbd')")
        self.check("closing the panel mid-typing saves the text and closes the keyboard",
                   stored == text0 + "q" and not kbd_left, (stored, kbd_left))

        # Password field: same as text, but masked, and the keyboard has a show/hide key.
        st = await self.goto("Secret")
        secret0 = st["value"]
        self.check("password: the input is masked", st["masked"], st)
        st = await self.press("A")
        self.check("password: A opens the keyboard", st["keyboard"], st)
        st = await self.press("A")
        self.check("password: A types", st["value"] == secret0 + "q" and st["masked"], st)
        st = await self.press("B")
        self.check("password: B closes the keyboard, still masked", not st["keyboard"] and st["masked"], st)
        stored = json.loads(DEMO_SETTINGS.read_text()).get("secret") if DEMO_SETTINGS.exists() else None
        self.check("password: the typed value is saved as is", stored == secret0 + "q", stored)
        await self.press("A")
        await self.press("A")
        await self.press("X")
        await self.press("X")
        await self.press("B")

        # showInQam: Demo reads hide_in_qam, but only the Quick Access panel obeys it: the library keeps the tab.
        st = await self.goto("Hide in Quick Access")
        await self.press("A", wait=0.8)
        tabs = await self.js(f"[...{SR}.querySelectorAll('.tabbar.main .tab')].map(t => t.textContent)")
        self.check("showInQam: the library ignores it (the Demo tab stays)", "Demo" in tabs, tabs)
        await self.press("A", wait=0.5)  # off again

        # Control API: "Lock volume" disables Volume in Controls (selectable, but inert).
        await self.goto("Lock volume")
        await self.press("A")
        await self.goto_sub("Controls")
        st = await self.goto("Volume")
        st2 = await self.press("RIGHT")
        hint = await self.js(f"{SR}.querySelector('.panel .hints').textContent")
        self.check("disabled control: selectable, ignores ←→, hint says why",
                   st2["label"] == "Volume" and st2["value"] == st["value"] and "Locked from Demo" in hint, (st2, hint))
        await self.goto_sub("Text")
        await self.goto("Lock volume")
        await self.press("A")

        saved = DEMO_SETTINGS.read_text() if DEMO_SETTINGS.exists() else None
        await self.goto("Reset values")
        st = await self.press("A", wait=0.3)
        self.check("confirm: A opens the dialog on Cancel", st["modal"] and st["label"] == "Cancel", st)
        st = await self.press("RIGHT")
        self.check("confirm: → goes to OK", st["label"] == "Reset", st)
        st = await self.press("R1")
        self.check("confirm: blocks tab switching", st["modal"] and st["tab"] == "Demo", st)
        st = await self.press("B", wait=0.3)
        unchanged = (DEMO_SETTINGS.read_text() if DEMO_SETTINGS.exists() else None) == saved
        self.check("confirm: B cancels without changing anything", not st["modal"] and st["open"] and unchanged, st)

        print(" Scroll (Long list)")
        st = await self.goto_sub("Long list")
        self.check("reaches Long list, with content below", st["sub"] == "Long list" and st["moreBelow"], st)
        self.check("own scrollbar shown, native one hidden", st["ownBar"] and st["nativeBar"] == 0, st)
        thumb_top = st["thumbY"]
        st = await self.goto("First control")
        st = await self.press("DOWN")
        self.check("↓ pages through long text keeping the selection", st["label"] == "First control" and st["scroll"] > 0, st)
        st = await self.goto("Item 30", limit=50)
        st = await self.press("DOWN", "DOWN", "DOWN")
        self.check("↓ reaches the very end", st["label"] == "Item 30" and st["scroll"] >= st["scrollMax"] - 2 and st["moreAbove"] and not st["moreBelow"], st)
        self.check("own scrollbar follows the scroll", st["thumbY"] > thumb_top + 50, (thumb_top, st["thumbY"]))
        st = await self.press("L2", wait=0.5)
        self.check("L2 goes back a sub-tab (scrolled to the top)", st["sub"] == "Windows" and st["scroll"] == 0, st)

        print(" Expanded view")
        st = await self.goto("Open expanded view")
        await self.press("A", wait=0.8)
        w = await self.window()
        self.check("A opens the expanded view", w is not None, w)
        if w:
            expect_w, expect_h = w["visibleW"] - 32, w["innerH"] - 32
            self.check("takes almost the whole screen (16px margin)", abs(w["w"] - expect_w) <= 4 and abs(w["h"] - expect_h) <= 4, w)
            self.check("starts on Gallery, ring on the grid", w["tab"] == "Gallery" and w["grid"] == "Cover 1", w)
            await self.press("RIGHT", "DOWN")
            w = await self.window()
            self.check("grid: → and ↓ move across images", w["grid"] == "Cover 8", w)
            await self.press("A")
            w = await self.window()
            self.check("grid: A opens the image", "img8" in w["info"], w)
            await self.press("DOWN", "DOWN", "DOWN", "DOWN")
            w = await self.window()
            self.check("grid: ↓ on the last row leaves it", w["focus"] == "Button after the gallery", w)
            await self.press("R1", wait=0.5)
            w = await self.window()
            st = await self.state()
            self.check("R1 switches the window's tab, not the panel's", w["tab"] == "Form" and st["sub"] == "Windows", (w, st["sub"]))
            await self.press("R2", wait=0.5)
            w = await self.window()
            self.check("R2 switches the window's sub-tab", w["sub"] == "More", w)
            st = await self.press("B", wait=0.4)
            self.check("B closes the window, ring back on its button", await self.window() is None and st["label"] == "Open expanded view" and st["open"], st)
            # Closing the panel takes the window with it.
            await self.press("A", wait=0.8)
            await self.js("window.__invasor.setOpen(false)")
            closed = await self.js(f"{SR}.querySelectorAll('.iwin').length") == 0
            await self.js("window.__invasor.setOpen(true)")
            await asyncio.sleep(1)
            self.check("closing the panel closes the window too", closed)
        # Not enough room (Quick Access during a game): the button locks itself and says why.
        st = await self.goto("Open expanded view")
        await self.js(f"{SR}.host.style.setProperty('--visible-w', '348px')")
        await asyncio.sleep(1.0)  # next space check (every 500 ms while open)
        lock = await self.js(f"""(() => {{ const b = {SR}.querySelector('.window-button .ctl-button');
          return {{ locked: b.classList.contains('locked'), text: b.textContent, note: !b.parentElement.querySelector('.locked-note').hidden,
                    ring: b.classList.contains('nav-focus'), hint: {SR}.querySelector('.panel .hints').textContent }}; }})()""")
        self.check("no room: the button locks itself (lock and note)", lock["locked"] and lock["text"].startswith("🔒") and lock["note"], lock)
        self.check("locked but selectable, hint explains why", lock["ring"] and "Library only" in lock["hint"], lock)
        st = await self.press("A", wait=0.6)
        self.check("locked: A doesn't open, it explains", await self.window() is None and "Library" in (st["toast"] or ""), st)
        await self.js(f"{SR}.host.style.setProperty('--visible-w', innerWidth + 'px')")
        await asyncio.sleep(1.0)
        unlocked = await self.js(f"!{SR}.querySelector('.window-button .ctl-button').classList.contains('locked')")
        self.check("room again: it unlocks itself", unlocked)

        print(" Rules (when, disabled_when)")
        st = await self.goto_sub("Rules")
        self.check("reaches Rules", st["sub"] == "Rules", st)
        await self.goto("Preset")  # the section's title…
        st = await self.press("DOWN")  # …then its radio
        st = await self.press("RIGHT", wait=1.0)
        fan = await self.field("Fan")
        self.check("disabled_when: a preset disables Fan, with the reason, and the backend sets it",
                   st["label"] == "Preset" and st["value"] == "Quiet" and fan["disabled"] and "Set by Preset" in fan["hint"]
                   and fan["value"] == "30%", (st, fan))
        await self.press("LEFT", wait=1.0)
        fan = await self.field("Fan")
        self.check("disabled_when: Custom enables Fan again", not fan["disabled"], fan)
        st = await self.goto("Adaptive")
        shown = await self.field("Multiplier")
        self.check("when: Multiplier shows while Adaptive is off", not st["on"] and shown["shown"], (st, shown))
        await self.press("A", wait=0.4)
        shown = await self.field("Multiplier")
        self.check("when: Adaptive on hides Multiplier", not shown["shown"], shown)
        section = await self.js(f"""(() => {{ const form = {SR}.querySelector('.nav-focus').closest('.settings-form');
          return [...form.querySelectorAll('summary')].find(t => t.textContent === 'Adaptive')?.closest('details').style.display; }})()""")
        self.check("when: the Adaptive section shows with it", section == "", section)
        await self.press("A", wait=0.4)

        print(" Backend")
        st = await self.goto_sub("Backend")
        await self.goto("Fail: invalid argument")
        st = await self.press("A", wait=1.0)
        self.check("a backend InvalidArgument reaches the UI as a clean error", "InvalidArgument" in (st["toast"] or ""), st)
        await self.goto("Counter")
        st = await self.press("RIGHT", "RIGHT")
        self.check("custom control: invasor:button ←→", st["value"] == "2", st)
        st = await self.press("Y")
        self.check("custom control: Y resets", st["value"] == "0", st)

        print(" Tabs")
        st = await self.goto_sub("Controls")
        self.check("back to Controls", st["sub"] == "Controls", st)
        st = await self.press("R1", wait=0.6)
        self.check("R1 goes to the next tab", st["tab"] != "Demo" and st["open"], st)
        st = await self.press("L1", wait=0.4)
        self.check("L1 goes back to Demo", st["tab"] == "Demo", st)
        st = await self.goto_tab("⚙ Settings")
        self.check("Settings is the last tab", st["tab"] == "⚙ Settings" and st["sub"] is None, st)
        demo = await self.js(f"""(() => {{ const c = [...{SR}.querySelectorAll('.ctl')].find(c => c.querySelector('.ctl-label')?.textContent?.startsWith('Demo ('));
          return c ? [c.querySelector('.ctl-label').textContent, c.dataset.hint ?? ''] : null; }})()""")
        self.check("Modules: Demo's label shows version and author (no email), its hint the description",
                   bool(demo) and demo[0] == "Demo (0.2.0) by FranjeGueje" and "Showcase" in demo[1], demo)

        print(" Settings")
        await self.unfold("Manage Invasor")
        await self.goto("Check for updates")
        await self.press("A", wait=4.0)  # asks GitHub: no release yet, or up to date, are both fine
        updates = await self.js(f"""(() => [...{SR}.querySelectorAll('.section')].find(s => s.textContent.startsWith('Manage Invasor'))
          ?.textContent ?? '')()""")
        self.check("Manage Invasor: shows the installed version, and says it couldn't check or that it's up to date",
                   "Installed:" in updates and ("Couldn't check" in updates or "up to date" in updates), updates[:160])
        st = await self.unfold("Controller")
        st = await self.goto("Open/close panel")
        before_cfg = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else ""
        before, st, undo = await self.nudge()
        await asyncio.sleep(0.4)
        changed = (CONFIG_FILE.read_text() if CONFIG_FILE.exists() else "") != before_cfg
        self.check("shortcut: changing it is saved", st["value"] != before["value"] and changed, st)
        await self.press(undo, wait=0.4)
        # The handle: icon by default, then the letter or nothing from Settings > Panel.
        handle = f"{SR}.querySelector('.handle')"
        st = await self.unfold("Panel")
        st = await self.goto("Handle icon")
        self.check("handle: shows the icon by default", await self.js(f"!!{handle}.querySelector('svg')"), st)
        before_cfg = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else ""
        before, st, undo = await self.nudge()
        await asyncio.sleep(0.4)
        mode = await self.js(f"{handle}.dataset.mode")
        text = await self.js(f"{handle}.textContent.trim()")
        saved = (CONFIG_FILE.read_text() if CONFIG_FILE.exists() else "") != before_cfg
        self.check("handle: changing it is applied at once and saved",
                   mode != "icon" and saved and (text == "I" if mode == "letter" else text == ""), (mode, text, saved))
        await self.press(undo, wait=0.4)
        self.check("handle: undoing brings the icon back", await self.js(f"!!{handle}.querySelector('svg')"), st)
        st = await self.goto("About")
        await self.press("A", wait=1.0)
        st = await self.state()
        self.check("About: lists the controllers", "Controllers detected" in st["about"] and "·" in st["about"], st["about"][:120])
        await self.press("A")

        print(" Highlighted game")
        installed = sorted(p.name[12:-4] for lib in context._library_dirs() for p in (lib / "steamapps").glob("appmanifest_*.acf"))
        if not installed:
            self.check("highlighted: needs an installed Steam game", False, "none found")
        else:
            appid = installed[0]
            # A library tile as Steam draws it (role=link, artwork URL, aria name), focused.
            await self.js(f"""(() => {{
              window.__smokePrev = document.activeElement;
              const tile = document.createElement('div');
              tile.id = 'smoke-tile'; tile.setAttribute('role', 'link'); tile.tabIndex = 0;
              tile.setAttribute('aria-labelledby', 'smoke-tile-name');
              tile.innerHTML = '<img src="/assets/{appid}/library_600x900.jpg"><span id="smoke-tile-name">Smoke</span>';
              tile.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0';
              document.body.appendChild(tile); tile.focus();
            }})()""")
            await asyncio.sleep(3.5)  # next game refresh of the open panel
            res = await self.js(f"""(async () => {{
              const c = window.__INVASOR_CFG;
              const r = await fetch(`http://127.0.0.1:${{c.apiPort}}/api/core/game`, {{method: 'POST',
                headers: {{'Content-Type': 'application/json', 'X-Invasor-Token': c.token}}, body: '{{}}'}});
              return [(await r.json()).result.highlighted, {SR}.querySelector('.game').textContent];
            }})()""")
            await self.js("document.getElementById('smoke-tile')?.remove(); window.__smokePrev?.focus?.()")
            self.check("highlighted: the focused library tile's game, by its artwork",
                       (res[0] or {}).get("appid") == appid and "Highlighted" in res[1], res)

        print(" Module zip")
        await self.module_zip()

        print(" Module upgrade / uninstall hooks")
        await self.module_hooks()

        print(" Contract")
        res = await self.js(f"""(async () => {{
          const c = window.__INVASOR_CFG;
          const set = async (key, value) => {{
            const r = await fetch(`http://127.0.0.1:${{c.apiPort}}/api/core/settings_set`, {{method: 'POST',
              headers: {{'Content-Type': 'application/json', 'X-Invasor-Token': c.token}},
              body: JSON.stringify({{id: 'demo', key, value}})}});
            return [r.status, (await r.json()).result ?? null];
          }};
          return [await set('volume', 101), await set('fps', 17), await set('mode', 'nope'), await set('ghost', 1)];
        }})()""")
        self.check("settings: the backend clamps and snaps numbers", res[0] == [200, 100] and res[1] == [200, 15], res)
        self.check("settings: invalid option and unknown key are rejected", res[2][0] == 400 and res[3][0] == 400, res)
        try:
            await self.js("(function () { throw new Error('smoke: broken module UI'); })()")
        except cef.CDPError:
            pass
        await self.js("window.__invasorKit.register('smoke-ghost', {nonsense: 1})")
        await asyncio.sleep(1.0)
        st = await self.state()
        self.check("a broken module UI leaves the panel working", st["open"] and st["tab"] is not None, st)

        st = await self.press("B")
        self.check("B closes the panel", not st["open"], st)


async def main():
    pages = [t for t in await cef.list_targets() if t.get("type") == "page" and "useragent=Valve%20Steam" in str(t.get("url") or "")]
    # Prefer Steam's gamepad UI (Game Mode / Big Picture); in dev_desktop mode the
    # desktop client window has the overlay too and can be used instead.
    targets = [t for t in pages if "Valve%20Steam%20Gamepad" in t["url"]]
    dev = False
    if not targets:
        for t in pages:
            s = await cef.CDPSession.connect(t["webSocketDebuggerUrl"])
            try:
                if await s.evaluate("!!window.__invasor"):
                    targets, dev = [t], True
                    break
            finally:
                await s.close()
    if not targets:
        print("FAIL: invasor isn't in any window. It only runs in Steam's gamepad UI:")
        print("      open Big Picture or Game Mode, or to test from the desktop set")
        print('      "dev_desktop": true in ~/.config/invasor/config.json and restart the service.')
        return 1
    # Demo's settings and data (Backend counts visits, per game too) and config.json.
    saved = {p: p.read_bytes() for p in [*DEMO_DIR.rglob("*"), CONFIG_FILE] if p.is_file()}
    session = await cef.CDPSession.connect(targets[0]["webSocketDebuggerUrl"])
    smoke = Smoke(session)
    print(f"Smoke test on \"{targets[0]['title']}\"" + (" (development mode: desktop window)" if dev else ""))
    try:
        await smoke.run()
    finally:
        await session.close()
        for path in DEMO_DIR.rglob("*"):
            if path.is_file() and path not in saved:
                path.unlink()  # only what the test created
        for path, data in saved.items():
            path.write_bytes(data)  # leave everything as we found it...
        # ...and re-inject, so the open UI and the service reload those values.
        subprocess.run(["systemctl", "--user", "restart", "invasor"], check=False)
    print("RESULT:", "OK" if smoke.failures == 0 else f"{smoke.failures} FAILURE(S)")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
