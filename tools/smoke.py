#!/usr/bin/env python3
"""Smoke test against the real, running Steam: run it after every Steam update.

Drives the overlay in Steam's main window with synthetic gamepad events (the same
vgp_onbuttondown events Steam sends) and checks: panel width (40% of the screen),
module tabs (L1/R1), sub-tabs (L2/R2), foldable sections (X), every demo control
(drawn from Demo's module.json), a disabled control, the select list, the built-in
keyboard, the confirm dialog, scrolling of long content, the expanded view, the
module.json rules (when, disabled_when), backend calls and errors (every notification
button and sound, ui.choose, ui.form), the stats line, windows opened with a handle, a
standalone ui.number and Control.get(), the keyboard's show/hide key, a custom control, the
Settings tab, installing a module zip and its upgrade/uninstall hooks, backend
validation of settings and the isolation of a broken module UI, a task that asks (Accept /
Deny in the Tasks panel), core.browse_path, ctx.pickPath, ctx.copy, and Demo's read-only
ctx.steam / ctx.paths calls. Needs Steam running with CEF debugging, the invasor service
installed and the "demo" module enabled.

Nothing changes the user's Steam unless asked: --steam-shortcut also adds Demo's own shortcut,
reads and sets its launch options and removes it again.

At the end Demo's values and config.json are restored and the service restarted,
so the overlay is re-injected exactly as it was.

    python3 tools/smoke.py [--steam-shortcut]
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from invasor import cef, context  # noqa: E402
from invasor.config import CONFIG_DIR, CONFIG_FILE  # noqa: E402

BTN = {"A": 1, "B": 2, "X": 3, "Y": 4, "L1": 5, "R1": 6, "L2": 7, "R2": 8, "UP": 9, "DOWN": 10, "LEFT": 11, "RIGHT": 12, "SELECT": 13, "START": 14}
DEMO_DIR = CONFIG_DIR / "modules" / "demo"
DEMO_SETTINGS = DEMO_DIR / "settings.json"
SOUNDS = ["trophy", "message", "toast", "chat", "mention", "friend", "online", "ingame", "none"]  # Demo › Backend buttons
SR = "document.getElementById('invasor-root').shadowRoot"
# Everything the checks look at: tabs, the control under the blue ring, open popups.
STATE = f"""(() => {{
  const r = {SR};
  const top = r.querySelector('.cover-panel:not([hidden])') ?? r.querySelector('.panel');
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
    about: (top.querySelector('.pane:not([hidden])') ?? top.querySelector('.content'))?.querySelector('.section:last-of-type')?.textContent ?? '',
    scroll: Math.round(top.querySelector('.content').scrollTop),
    scrollMax: top.querySelector('.content').scrollHeight - top.querySelector('.content').clientHeight,
    moreAbove: top.querySelector('.content').classList.contains('more-above'),
    moreBelow: top.querySelector('.content').classList.contains('more-below'),
    width: r.querySelector('.panel').getBoundingClientRect().width,
    screen: innerWidth,
    realScreen: screen.width,
    nativeBar: top.querySelector('.content').offsetWidth - top.querySelector('.content').clientWidth,
    ownBar: !top.querySelector('.scrollbar').hidden,
    thumbY: top.querySelector('.thumb').getBoundingClientRect().top,
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
    def __init__(self, session, steam_shortcut=False):
        self.s = session
        self.failures = 0
        self.steam_shortcut = steam_shortcut

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
        """Move down (or `button`) until the ring is on the control called `label`; if it isn't that way,
        the other way (a list drawn again puts the ring wherever it was, which may be past the control)."""
        st = await self.state()
        for _ in range(limit):
            if st["label"] == label:
                return st
            st = await self.press(button)
        other = {"DOWN": "UP", "UP": "DOWN"}.get(button)
        for _ in range(limit if other else 0):
            if st["label"] == label:
                return st
            st = await self.press(other)
        return st

    async def hints(self):
        """The pad's hint bar of the layer in front: a panel that covers the Invasor one, else the panel."""
        return await self.js(f"""(() => {{ const r = {SR};
          return (r.querySelector('.cover-panel:not([hidden]) .hints') ?? r.querySelector('.panel .hints')).textContent; }})()""")

    async def open_settings(self):
        """Start opens the Settings panel over the Invasor one (it is built the first time: give it a moment)."""
        return await self.press("START", wait=1.5)

    async def close_cover(self):
        """B closes the panel in front (Tasks or Settings) and gives the pad back to Invasor."""
        return await self.press("B", wait=0.4)

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
            sha256 = body.get("result", {}).get("sha256", "")
            st, body = await self.api("module_install", path=rel, sha256="0" * 64)
            self.check("zip: a zip other than the one inspected is refused", st == 400 and "changed" in body.get("error", ""), body)
            st, body = await self.api("module_install", path=rel, sha256=sha256)
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
        order0 = None  # the module order saved before the test, if the test got as far as reading it
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
            await self.open_settings()
            # Installed behind the panel's back: Rescan draws the module list again.
            await self.unfold("Manage modules")
            await self.goto("Rescan modules", limit=60)
            await self.press("A", wait=3.0)
            # Reordering (two modules are listed now): grab Smoke Demo, move it up, drop it.
            def saved_order():
                return json.loads(CONFIG_FILE.read_text()).get("module_order") if CONFIG_FILE.exists() else None

            st, body = await self.api("modules")
            ids0 = [m["id"] for m in body["result"]] if st == 200 else []
            order0 = saved_order()  # the user may have an order of their own: it must come back as it was
            at = ids0.index("smoke-demo") if "smoke-demo" in ids0 else 0
            move = "UP" if at > 0 else "DOWN"  # one row towards the middle of the list
            moved = list(ids0)
            if len(moved) > 1:
                to = at - 1 if move == "UP" else at + 1
                moved[at], moved[to] = moved[to], moved[at]
            await self.unfold("Module order")
            st = await self.goto("Smoke Demo")
            st = await self.press("A", wait=0.3)
            self.check("reorder: A grabs the row and the hint says how to drop it", "drop" in await self.hints(), st)
            await self.press(move, wait=0.3)
            st = await self.state()
            self.check("reorder: the ring follows the moved row", st["label"] == "Smoke Demo", st)
            await self.press("B", wait=0.3)
            self.check("reorder: B puts it back and saves nothing", saved_order() == order0, (saved_order(), order0))
            await self.press("A", wait=0.3)
            await self.press(move, wait=0.3)
            await self.press("A", wait=1.0)
            st, body = await self.api("modules")
            ids = [m["id"] for m in body["result"]] if st == 200 else body
            self.check("reorder: dropping saves it and the module list follows", saved_order() == moved and ids == moved, (saved_order(), ids, moved))
            await self.goto("Reset order", limit=20)
            await self.press("A", wait=1.5)
            self.check("reorder: Reset order forgets it", not saved_order(), saved_order())
            await self.unfold("Manage modules")
            await self.goto("Rescan modules", limit=60)
            st = await self.goto("Uninstall Smoke Demo", limit=60, button="UP")  # above Rescan, or below if the order differs
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
            await self.close_cover()  # Settings goes back to the Invasor panel
        finally:
            await self.api("module_uninstall", id="smoke-demo", purge=True)
            if order0 is not None:
                await self.api("set_pref", key="module_order", value=order0)
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(CONFIG_DIR / "modules" / "smoke-demo", ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)

    async def window(self):
        """State of the open big window (None if there isn't one)."""
        return await self.js(WINDOW)

    async def tasks_state(self, kind="tasks"):
        """Is the `kind` panel (tasks, settings) open, is the Invasor panel covered, and which module tab is on."""
        return await self.js(f"""(() => {{ const r = {SR}; const t = r.querySelector('.cover-panel[data-kind="{kind}"]');
          return {{open: !!t && !t.hidden, covered: r.querySelector('.panel').classList.contains('covered'),
                   height: t ? Math.round(t.getBoundingClientRect().height) : 0, windowHeight: innerHeight,
                   tab: r.querySelector('.tabbar.main .tab.on')?.textContent ?? null,
                   hint: t?.querySelector('.hints')?.textContent ?? '',
                   handle: r.querySelector('.handle').dataset.tasks ?? '', button: r.querySelector('.tasks-btn').dataset.tasks ?? '',
                   rows: [...(t?.querySelectorAll('.task-row') ?? [])].map(e => ({{label: e.querySelector('.task-label').textContent,
                          state: e.dataset.state, pct: e.querySelector('.task-bar').getAttribute('aria-valuenow'),
                          question: e.querySelector('.task-question').textContent,
                          answers: [...e.querySelectorAll('.task-ask:not([hidden]) button')].map(b => b.textContent)}}))}}; }})()""")

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
        # Demo is the module this test drives. If the user uninstalled it (a shipped module can be),
        # bring it back for the run: config.json is restored at the end, which uninstalls it again.
        _, body = await self.api("module_removed")
        if any(m["id"] == "demo" for m in (body.get("result") or [])):
            st, body = await self.api("module_restore", id="demo")
            self.check("Demo was uninstalled: restored for this run", st == 200, body)
        await self.js("window.__invasor.setOpen(false)")
        await self.js("window.__invasor.setOpen(true)")
        await asyncio.sleep(2.5)  # tabs build
        # The overlay remembers the last tab, and there may be any number of modules.
        names = await self.js(f"[...{SR}.querySelectorAll('.tabbar.main .tab')].map(t => t.textContent)")
        if "Demo" not in names:
            self.check("the Demo module is enabled (the smoke test drives its tab)", False,
                       f"tabs in this window: {names}. Enable or restore Demo in ⚙ Settings › Modules, then run it again")
            return
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
        # The keyboard's own show/hide key (bottom row, before OK) reveals the text and hides it again.
        await self.press("DOWN", "DOWN", "DOWN", "RIGHT", "RIGHT", "RIGHT", "RIGHT")
        st = await self.press("A")
        self.check("password: the keyboard's show key reveals the text", not st["masked"] and st["keyboard"], st)
        st = await self.press("A")
        self.check("password: pressing it again hides the text", st["masked"], st)
        await self.press("UP", "UP", "UP", "LEFT", "LEFT", "LEFT", "LEFT")
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
        await self.goto("Preview Volume at 100%")
        await self.press("A")
        await self.goto_sub("Controls")
        st = await self.goto("Volume")
        self.check("Control.set() moves the drawn value", "100" in str(st["value"]), st)
        await self.goto_sub("Text")
        await self.goto_sub("Controls")
        st = await self.goto("Volume")
        st2 = await self.press("RIGHT")
        hint = await self.hints()
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
        # Accepted: OK runs the reset of every form and says so.
        await self.goto("Reset values")
        await self.press("A", wait=0.3)
        await self.press("RIGHT")
        st = await self.press("A", wait=1.5)
        self.check("confirm: OK resets the values", not st["modal"] and st["toast"] == "Values reset", st)
        # ui.number outside a settings form, and Control.get().
        st = await self.goto("Standalone number")
        self.check("ui.number: shows its start value", "5" in str(st["value"]), st)
        st = await self.press("RIGHT")
        self.check("ui.number: → raises it", "6" in str(st["value"]), st)
        await self.goto("Read the number")
        st = await self.press("A", wait=0.4)
        self.check("Control.get() returns what the control shows", st["toast"] == "Standalone number: 6", st)

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
        lock = await self.js(f"""(() => {{ const b = {SR}.querySelector('.pane:not([hidden]) .window-button .ctl-button');
          return {{ locked: b.classList.contains('locked'), text: b.textContent, note: !b.parentElement.querySelector('.locked-note').hidden,
                    ring: b.classList.contains('nav-focus'), hint: {SR}.querySelector('.panel .hints').textContent }}; }})()""")
        self.check("no room: the button locks itself (lock and note)", lock["locked"] and lock["text"].startswith("🔒") and lock["note"], lock)
        self.check("locked but selectable, hint explains why", lock["ring"] and "Library only" in lock["hint"], lock)
        st = await self.press("A", wait=0.6)
        self.check("locked: A doesn't open, it explains", await self.window() is None and "Library" in (st["toast"] or ""), st)
        await self.js(f"{SR}.host.style.setProperty('--visible-w', innerWidth + 'px')")
        await asyncio.sleep(1.0)
        unlocked = await self.js(f"!{SR}.querySelector('.pane:not([hidden]) .window-button .ctl-button').classList.contains('locked')")
        self.check("room again: it unlocks itself", unlocked)

        # ctx.openWindow with a handle: retitle it and close it from code.
        await self.goto("Open with a handle")
        await self.press("A", wait=0.8)
        title_js = f"{SR}.querySelector('.iwin .iwin-title')?.textContent ?? null"
        title = await self.js(title_js)
        self.check("ctx.openWindow: opens a window titled by the module", title == "Window handle", title)
        if title is not None:
            await self.goto("Change the title")
            await self.press("A", wait=0.3)
            title = await self.js(title_js)
            self.check("handle.setTitle: retitles the window", "retitled 1" in str(title), title)
            await self.goto("Close from code")
            st = await self.press("A", wait=0.6)
            self.check("handle.close: closes it and onClose runs", await self.window() is None and "Window closed" in (st["toast"] or ""), st)

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
        if st["on"]:  # the saved settings may have it on: start from off
            await self.press("A", wait=0.4)
            st = await self.state()
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
        await asyncio.sleep(1.5)
        first_info = f"[...{SR}.querySelectorAll('.ctl-info')].map(e => e.textContent).find(t => t.startsWith('Backend thread: up')) ?? ''"
        stats = await self.js(first_info)
        self.check("stats line: backend thread uptime, Python files and visits",
                   "Backend thread: up" in stats and "Python files:" in stats and "Backend tab visits:" in stats, stats)
        await asyncio.sleep(2.0)
        self.check("stats line refreshes every second", await self.js(first_info) != stats, stats)
        # Background tasks: Demo's slow_task runs from the API; core.tasks() lists it afterwards
        # (the registry is in memory: nothing is left on disk).
        st, body = await self.api("slow_task", feature="demo", seconds=1)
        self.check("tasks: Demo's slow task runs", st == 200, (st, body))
        st, body = await self.api("slow_task", feature="demo", seconds=1, fail=True)
        self.check("tasks: a failing task answers 503", st == 503, (st, body))
        st, body = await self.api("tasks")
        listing = body.get("result") if st == 200 and isinstance(body, dict) else None
        recent = [(t["module"], t["state"]) for t in (listing or {}).get("recent", [])]
        self.check("tasks: core.tasks() lists both as ended, the newest first",
                   listing is not None and recent[:2] == [("demo", "failed"), ("demo", "done")] and not listing["active"], (st, body))
        # core.browse_path (what ctx.pickPath lists): home by default, dot entries only with hidden.
        st, body = await self.api("browse_path")
        home = (body.get("result") or {}) if st == 200 else {}
        self.check("browse_path: home by default, with its folders", "path" in home and isinstance(home.get("dirs"), list), (st, body))
        st, body = await self.api("browse_path", hidden=True)
        names = [d["name"] for d in (body.get("result") or {}).get("dirs", [])] if st == 200 else []
        if (Path.home() / ".config").is_dir():
            self.check("browse_path: hidden lists dot folders (~/.config)", ".config" in names, (st, names[:20]))
            self.check("browse_path: without hidden they're left out", ".config" not in [d["name"] for d in home.get("dirs", [])], home)
        st, body = await self.api("browse_path", files="x")
        self.check("browse_path: a bad argument answers 400", st == 400, (st, body))
        # Demo's read-only ctx.steam / ctx.paths / ctx.archive calls (nothing changes, no network).
        st, body = await self.api("steam_supports", feature="demo")
        sup = body.get("result") if st == 200 else None
        self.check("ctx.steam.supports: five features, each a bool",
                   isinstance(sup, dict) and len(sup) == 5 and all(isinstance(v, bool) for v in sup.values()), (st, body))
        st, body = await self.api("steam_language", feature="demo")
        code = (body.get("result") or {}).get("code") if st == 200 else None
        self.check("ctx.steam_language: a 2-letter code", isinstance(code, str) and len(code) == 2, (st, body))
        st, body = await self.api("steam_folders", feature="demo")
        self.check("ctx.paths: Steam's root is found", st == 200 and (body.get("result") or {}).get("root") is not None, (st, body))
        st, body = await self.api("unpack_sample", feature="demo")
        self.check("ctx.archive: unpack_sample gives 3 files", st == 200 and len((body.get("result") or {}).get("files", [])) == 3, (st, body))
        if not self.steam_shortcut:
            print("  skipped: --steam-shortcut not given (Demo's shortcut in Steam)")
        else:
            appid = None
            try:
                st, body = await self.api("steam_shortcut_add", feature="demo")
                appid = (body.get("result") or {}).get("appid") if st == 200 else None
                self.check("ctx.steam.add_to: Demo's shortcut added, with its appid", isinstance(appid, int), (st, body))
                st, body = await self.api("steam_shortcut_options", feature="demo")
                self.check("ctx.steam.launch_options: reads DEMO=1", st == 200 and body["result"]["options"] == "DEMO=1 %command%", (st, body))
                st, body = await self.api("steam_shortcut_options", feature="demo", line="DEMO=2 %command%")
                self.check("ctx.steam.set_launch_options: DEMO=2, verified", st == 200 and body["result"].get("verified") is True
                           and body["result"]["options"] == "DEMO=2 %command%", (st, body))
                st, body = await self.api("steam_shortcut_remove", feature="demo")
                self.check("ctx.steam.remove_shortcut: removed", st == 200 and body["result"].get("removed") is True, (st, body))
                appid = None if st == 200 else appid
            finally:
                if appid is not None:
                    await self.api("steam_shortcut_remove", feature="demo")
        # Every notification button: Steam's own sounds, with the toast of the answer.
        for label in ["Default (Steam decides)", *(f"Sound: {s}" for s in SOUNDS)]:
            await self.goto(label, limit=6)
            st = await self.press("A", wait=1.5)
            self.check(f"notify: {label}", st["toast"] == "Notification sent", st)
        for label, want in (
            ("Who am I", '"folder":"demo"'),
            ("Notify", "Notification sent"),
            ("Notify (async method)", "Notification sent"),
            ("Steam info (backend)", '"available"'),
            ("Steam info (async method)", '"available"'),
        ):
            await self.goto(label, limit=6)
            st = await self.press("A", wait=1.5)
            self.check(f"call: {label}", want in (st["toast"] or ""), st)
        await self.goto("Steam info (from here)", limit=6)
        st = await self.press("A", wait=1.5)
        self.check("call: Steam info (from here) answers, or says it's not available", bool(st["toast"]), st)
        await self.goto("Fail: invalid argument")
        st = await self.press("A", wait=1.0)
        self.check("a backend InvalidArgument reaches the UI as a clean error", "InvalidArgument" in (st["toast"] or ""), st)
        await self.goto("Fail: unavailable", limit=4)
        st = await self.press("A", wait=1.0)
        self.check("a backend Unavailable reaches the UI as a clean error", "Unavailable" in (st["toast"] or ""), st)
        await self.goto("Fail: choose how", limit=4)
        st = await self.press("A", wait=0.5)
        self.check("ui.choose: A opens the dialog on Cancel", st["modal"] and st["label"] == "Cancel", st)
        st = await self.press("B", wait=1.0)
        self.check("ui.choose: B cancels", not st["modal"] and st["toast"] == "Cancelled", st)
        await self.press("A", wait=0.5)
        await self.goto("Unavailable (503)", limit=4, button="RIGHT")
        st = await self.press("A", wait=1.0)
        self.check("ui.choose: the chosen value reaches the backend", not st["modal"] and "Unavailable: 503" in (st["toast"] or ""), st)
        # ctx.copy: CEF may refuse the clipboard; either answer is fine, as long as it answers.
        await self.goto("Copy a line", limit=6)
        st = await self.press("A", wait=1.0)
        self.check("ctx.copy: answers Copied or Couldn't copy it", st["toast"] in ("Copied", "Couldn't copy it"), st)
        st = await self.state()
        self.check("ctx.copy: the ring stays on its button", st["label"] == "Copy a line", st)
        # ctx.pickPath: a big window with the folder browser; B closes it and the module gets null.
        await self.goto("Pick a folder", limit=4)
        await self.press("A", wait=1.0)
        w = await self.window()
        choose = await self.js(f"[...{SR}.querySelectorAll('.iwin .ctl-button')].some(b => b.textContent === 'Choose this folder')")
        self.check("ctx.pickPath: opens a big window with Choose this folder", w is not None and choose, (w, choose))
        st = await self.press("B", wait=0.6)
        self.check("ctx.pickPath: B closes it, the module gets null (Cancelled)",
                   await self.window() is None and st["toast"] == "Cancelled" and st["label"] == "Pick a folder", st)
        # ui.form: the Profile section is stored by the module itself (a TOML file outside Invasor's folders).
        profile = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "invasor-demo" / "profile.toml"
        existed = profile.exists()
        try:
            await self.unfold("Profile (stored by the module)")
            st, body = await self.api("profile_get", feature="demo")
            self.check("ui.form: the backend answers profile_get", st == 200, body)
            scale0 = (body.get("result") or {}).get("scale")
            st = await self.goto("Scale", limit=6)
            st = await self.press("RIGHT", wait=0.8)
            _, body = await self.api("profile_get", feature="demo")
            self.check("ui.form: a change is stored by the module's backend", (body.get("result") or {}).get("scale") not in (None, scale0), (scale0, body))
            await self.press("LEFT", wait=0.8)
        finally:
            if not existed:
                shutil.rmtree(profile.parent, ignore_errors=True)  # only what this run created
        await self.goto("Counter")
        st = await self.press("RIGHT", "RIGHT")
        self.check("custom control: invasor:button ←→", st["value"] == "2", st)
        st = await self.press("Y")
        self.check("custom control: Y resets", st["value"] == "0", st)

        print(" Tabs")
        st = await self.goto_sub("Controls")
        self.check("back to Controls", st["sub"] == "Controls", st)
        names = await self.js(f"[...{SR}.querySelectorAll('.tabbar.main .tab')].map(t => t.textContent)")
        at = names.index("Demo") if "Demo" in names else -1
        if 0 <= at < len(names) - 1:
            st = await self.press("R1", wait=0.6)
            self.check("R1 goes to the next tab", st["tab"] == names[at + 1] and st["open"], (st["tab"], names))
            st = await self.press("L1", wait=0.4)
            self.check("L1 goes back to Demo", st["tab"] == "Demo", st["tab"])
        else:
            # Settings left the row: the last module tab has nothing after it.
            st = await self.press("R1", wait=0.6)
            self.check("R1 on the last tab stays there (Settings isn't in the row)", st["tab"] == "Demo" and st["open"], (st["tab"], names))
            if at > 0:
                st = await self.press("L1", wait=0.6)
                self.check("L1 goes to the previous tab", st["tab"] == names[at - 1], (st["tab"], names))
                st = await self.press("R1", wait=0.6)
                self.check("R1 comes back to Demo", st["tab"] == "Demo", st["tab"])
        self.check("Settings is not a tab of the row any more", "⚙ Settings" not in names and len(names) >= 1, names)
        before_cover = await self.state()
        st = await self.open_settings()
        sp = await self.tasks_state("settings")
        self.check("Settings: Start opens it over the Invasor panel", sp["open"] and sp["covered"], sp)
        self.check("Settings: it takes the whole height of the window", abs(sp["height"] - (sp["windowHeight"] - 16)) <= 2, sp)
        demo = await self.js(f"""(() => {{ const c = [...{SR}.querySelectorAll('.ctl')].find(c => c.querySelector('.ctl-label')?.textContent?.startsWith('Demo ('));
          return c ? [c.querySelector('.ctl-label').textContent, c.dataset.hint ?? ''] : null; }})()""")
        self.check("Modules: Demo's label shows version and author (no email), its hint the description",
                   bool(demo) and demo[0] == "Demo (0.3.0) by FranjeGueje" and "Showcase" in demo[1], demo)

        print(" Settings")
        await self.unfold("Manage Invasor")
        await self.goto("Check for updates")
        await self.press("A", wait=0.5)  # asks GitHub: no release yet, up to date, or a pre-release ahead of stable
        ANSWERS = ("Couldn't check", "up to date", "The stable version is")
        # Its answer redraws the section (the button under the ring is replaced): wait for it, whatever it takes
        # (a fixed wait made the next steps race against it), then let the ring settle.
        for _ in range(40):
            updates = await self.js(f"""(() => [...{SR}.querySelectorAll('.section')].find(s => s.textContent.startsWith('Manage Invasor'))
              ?.textContent ?? '')()""")
            if any(t in updates for t in ANSWERS):
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.6)
        self.check("Manage Invasor: shows the installed version, and says it couldn't check, that it's up to date "
                   "or (on a pre-release) which the stable version is",
                   "Installed:" in updates and any(t in updates for t in ANSWERS), updates[:160])
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
        await self.close_cover()
        st = await self.state()
        self.check("Settings: B goes back to the Invasor panel on the tab it was on", st["open"] and st["tab"] == before_cover["tab"], (st, before_cover))

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

        print(" Tasks panel")
        before = await self.tasks_state()
        self.check("tasks: closed to begin with", not before["open"] and not before["covered"], before)
        await self.press("SELECT", wait=0.4)
        t = await self.tasks_state()
        self.check("tasks: Select shows the Tasks panel over the Invasor one", t["open"] and t["covered"], t)
        self.check("tasks: it takes the whole height of the window, whatever the Invasor panel's", abs(t["height"] - (t["windowHeight"] - 16)) <= 2, t)
        self.check("tasks: its hint bar says how to go back", "Select back" in t["hint"], t)
        await self.press("R1", "L1", "R2", "L2", wait=0.3)
        t = await self.tasks_state()
        self.check("tasks: L1/R1 and L2/R2 don't switch the tabs underneath", t["open"] and t["tab"] == before["tab"], (t, before))
        await self.press("SELECT", wait=0.4)
        t = await self.tasks_state()
        self.check("tasks: Select again gives the pad back to Invasor", not t["open"] and not t["covered"] and t["tab"] == before["tab"], t)
        await self.press("SELECT", "B", wait=0.4)
        t = await self.tasks_state()
        st = await self.state()
        self.check("tasks: B closes Tasks first, the Invasor panel stays open", not t["open"] and st["open"], (t, st))
        await self.js(f"{SR}.querySelector('.tasks-btn').click()")
        t = await self.tasks_state()
        self.check("tasks: the header's Tasks button opens it by touch", t["open"] and t["covered"], t)
        await self.js(f"""{SR}.querySelector('.cover-panel[data-kind="tasks"] .close').click()""")
        t = await self.tasks_state()
        self.check("tasks: its ✕ closes it by touch", not t["open"] and not t["covered"], t)
        print(" Settings panel (header button, exclusivity)")
        await self.js(f"{SR}.querySelector('.settings-btn').click()")
        await asyncio.sleep(1.0)
        s = await self.tasks_state("settings")
        self.check("settings: the ⚙ button in the header opens it by touch", s["open"] and s["covered"], s)
        await self.press("SELECT", wait=0.4)
        t = await self.tasks_state()
        self.check("settings: Select does nothing while Settings is in front", not t["open"], t)
        await self.press("START", wait=0.4)
        s = await self.tasks_state("settings")
        self.check("settings: Start goes back to Invasor", not s["open"] and not s["covered"], s)
        await self.press("SELECT", wait=0.4)
        await self.press("START", wait=0.4)
        s = await self.tasks_state("settings")
        t = await self.tasks_state()
        self.check("settings: Start does nothing while Tasks is in front", not s["open"] and t["open"], (s, t))
        await self.press("B", wait=0.4)

        # A task from Demo, started without waiting: the backend pushes it, nobody polls.
        await self.js(f"""fetch(`http://127.0.0.1:${{window.__INVASOR_CFG.apiPort}}/api/demo/slow_task`, {{method: 'POST',
          headers: {{'Content-Type': 'application/json', 'X-Invasor-Token': window.__INVASOR_CFG.token}},
          body: JSON.stringify({{seconds: 3}})}}).catch(() => null); 0""")
        await asyncio.sleep(1.2)
        t = await self.tasks_state()
        self.check("tasks: the handle and the Tasks button show something runs, with nobody asking", t["handle"] == "running" and t["button"] == "running", t)
        await self.press("SELECT", wait=0.5)
        t = await self.tasks_state()
        self.check("tasks: the running task is in the panel with its bar and text",
                   t["rows"] and t["rows"][0]["label"] == "Demo: slow task" and t["rows"][0]["state"] == "running" and t["rows"][0]["pct"] is not None, t)
        await asyncio.sleep(3.5)
        t = await self.tasks_state()
        self.check("tasks: when it ends it moves to Recent and the dot goes out",
                   t["rows"] and t["rows"][0]["state"] == "done" and t["handle"] == "" and t["button"] == "", t)
        await self.press("SELECT", wait=0.4)
        await self.js(f"""fetch(`http://127.0.0.1:${{window.__INVASOR_CFG.apiPort}}/api/demo/slow_task`, {{method: 'POST',
          headers: {{'Content-Type': 'application/json', 'X-Invasor-Token': window.__INVASOR_CFG.token}},
          body: JSON.stringify({{seconds: 1, fail: true}})}}).catch(() => null); 0""")
        await asyncio.sleep(2.2)
        t = await self.tasks_state()
        self.check("tasks: a failure not seen yet turns the dot red", t["handle"] == "failed" and t["button"] == "failed", t)
        await self.press("SELECT", wait=0.5)
        await self.press("SELECT", wait=0.5)
        t = await self.tasks_state()
        self.check("tasks: looking at it clears the dot", t["handle"] == "" and t["button"] == "", t)
        # A task that asks (Demo's ask_task): it waits for Accept / Deny; the call answers when the user does.
        for accept in (True, False):
            call = asyncio.create_task(self.api("ask_task", feature="demo"))
            waiting = None
            for _ in range(25):
                await asyncio.sleep(0.2)
                _, body = await self.api("tasks")
                waiting = next((x for x in (body.get("result") or {}).get("active", [])
                                if x["label"] == "Demo: a question" and x["state"] == "waiting"), None)
                if waiting:
                    break
            self.check(f"ask: the task waits with its question ({'Accept' if accept else 'Deny'})",
                       waiting is not None and waiting["question"] == "Demo asks: is this fine?", waiting)
            if waiting is None:
                call.cancel()  # nothing to answer: don't wait for its timeout
                continue
            pressed = False
            if accept:
                await asyncio.sleep(0.4)
                t = await self.tasks_state()
                self.check("ask: the handle shows a task is waiting", t["handle"] == "waiting", t)
                await self.press("SELECT", wait=0.5)
                t = await self.tasks_state()
                row = next((r for r in t["rows"] if r["state"] == "waiting"), None)
                self.check("ask: its row waits, shows the question, with Accept and Deny",
                           row is not None and row["question"] == "Demo asks: is this fine?" and row["answers"] == ["Accept", "Deny"], t)
                st = await self.goto("Accept", limit=6)
                if st["label"] == "Accept":
                    await self.press("A", wait=0.5)
                    pressed = True
            if not pressed:
                st, body = await self.api("task_answer", id=waiting["id"], accept=accept)
                self.check("ask: core.task_answer answers it", st == 200 and body["result"]["answered"], (st, body))
            st, body = await call
            self.check(f"ask: the call gets accepted={accept}" + (" (A on Accept)" if pressed else ""),
                       st == 200 and body.get("result") == {"accepted": accept}, (st, body))
            _, body = await self.api("tasks")
            last = next((x for x in (body.get("result") or {}).get("recent", []) if x["label"] == "Demo: a question"), None)
            self.check("ask: the task ends done", (last or {}).get("state") == "done", last)
        if (await self.tasks_state())["open"]:
            await self.press("SELECT", wait=0.4)
        # Clear recent: the ones that ended are in the list (Demo's two tasks); A on the button forgets them.
        await self.press("SELECT", wait=0.5)
        t = await self.tasks_state()
        self.check("tasks: the finished ones are listed under Recent", len(t["rows"]) >= 2 and all(r["state"] != "running" for r in t["rows"]), t)
        st = await self.state()
        self.check("tasks: the ring is on Clear recent (the only control of the panel)", st["label"] == "Clear recent", st)
        await self.press("A", wait=1.0)
        t = await self.tasks_state()
        st, body = await self.api("tasks")
        listing = body.get("result") if st == 200 and isinstance(body, dict) else None
        self.check("tasks: Clear recent empties the list, in the panel and in the backend",
                   not t["rows"] and listing is not None and not listing["recent"] and not listing["active"], (t, listing))
        self.check("tasks: and the Tasks panel is still the one in front", t["open"] and t["covered"], t)
        await self.js(f"{SR}.querySelector('.close').click()")
        t = await self.tasks_state()
        self.check("tasks: closing Invasor closes Tasks too", not t["open"] and not t["covered"], t)
        await self.js("window.__invasor?.setOpen(true)")
        await asyncio.sleep(0.6)

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
    smoke = Smoke(session, steam_shortcut="--steam-shortcut" in sys.argv[1:])
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
