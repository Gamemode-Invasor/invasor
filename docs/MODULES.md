# Writing an Invasor module

This is the contract between Invasor and its modules, **module API 1**. A module that follows it works the same
on every install. If it breaks, it breaks only its own tab, never the panel or the other modules.

> **License.** Invasor is GPL-3.0-or-later. The code examples in this document and the `modules/_example/` template
> are MIT-licensed ([`LICENSES/MIT.txt`](../LICENSES/MIT.txt)): copy them into your module freely, whatever license
> you choose for it.

## 1. Structure

```
modules/<id>/
  module.json     required: name, version, settings…
  backend.py      optional: Python methods the UI can call
  *.py            optional: helpers for backend.py (`from . import helpers`)
  ui.ts           optional: the module's own UI
  dist/ui.js      generated from ui.ts by `npm run build` (this is what gets loaded)
  tests/          optional: unittest files (test_*.py) for the module's Python code
```

- **`<id>`** is the folder name: lowercase letters, digits, `-` and `_`. Folders starting with `_` or `.` are
  templates and are never loaded. `core` is reserved.
- What you need depends on the module:

  | Kind of module | Files |
  |---|---|
  | Settings only | `module.json` |
  | Works in the background, no tab | `module.json` (no `settings`) + `backend.py` |
  | Settings that act on something | `module.json` + `backend.py` |
  | Custom UI | add `ui.ts` |

- To start, copy `modules/_example/`.

## 2. `module.json`

```json
{
  "api": 1,
  "name": "FPS Limit",
  "version": "1.0.0",
  "author": "Jane Doe <jane@example.com>",
  "description": "Caps the frame rate per game.",
  "order": 50,
  "tab": "FPS",
  "settings": [ … ]
}
```

| Key | Required | Meaning |
|---|---|---|
| `api` | yes | Module API version. Must be `1`; Invasor refuses modules written for an API it doesn't implement. |
| `name` | yes | Display name. |
| `version` | yes | Your module's version (free text, e.g. `"1.0.0"`). |
| `author` | no | Who made it: free text, one line, up to 128 characters, e.g. `"Jane Doe"` or `"Jane Doe <jane@example.com>"`. Invasor shows only the name (never the email): "Name (version) by Jane Doe" in ⚙ Settings › Modules, and when installing the zip. Default: empty. |
| `description` | no | One line, shown in the hint bar when the module is selected in ⚙ Settings › Modules, and when installing the zip. |
| `order` | no | Integer, default tab position (lower first, then by name). Default 100. The user can reorder the modules in ⚙ Settings › Module order, and what they choose wins over this; a module they never placed goes after the ones they did, by this value. |
| `tab` | no | Short tab label. Default: `name`. |
| `min_core` | no | Oldest Invasor the module works with, as `"0.1.3"` (or `"0.1.3-rc1"`). A module that needs a newer one is refused when installing and, if it's already installed, isn't loaded and ⚙ Settings says why. A release candidate counts as older than its release: `0.1.3-rc2` doesn't meet `"0.1.3"`. Invasor versions that predate this key reject it as an unknown key. Default: any. |
| `no_qam` | no | `true`: not shown in the Quick Access (···) panel, only in the library's (for modules with nothing to do during a game). Default `false`. |
| `settings` | no | The settings form (below). |
| `forms` | no | Named forms whose values the module stores itself (section 3, at the end). |

Any other key is an **error**: a typo never goes unnoticed. An invalid `module.json` means the module isn't
loaded, and ⚙ Settings shows why (e.g. `settings[2].max: must be a number`).

## 3. Settings

Declared once in `module.json`. With that declaration:

- the backend validates every value and fills in the defaults;
- the panel draws the form;
- the gamepad and touch work on it;
- `backend.py` reads the same values.

```json
"settings": [
  { "key": "enabled", "type": "toggle", "label": "Enabled", "default": true },
  { "key": "fps", "type": "slider", "label": "FPS limit", "min": 15, "max": 144, "step": 5, "unit": " fps", "default": 60 },
  { "section": "Advanced", "open": false, "items": [
    { "key": "mode", "type": "select", "label": "Mode", "default": "auto",
      "options": [ { "value": "auto", "label": "Auto" }, { "value": "manual", "label": "Manual" } ] },
    { "key": "note", "type": "text", "label": "Note", "default": "", "max_length": 64 }
  ]}
]
```

**Every field** has these keys:

| Key | Required | Meaning |
|---|---|---|
| `key` | yes | Unique; letters, digits and `_`, not starting with a digit. |
| `type` | yes | One of the types below. |
| `label` | yes | Shown next to the control. |
| `default` | yes | Must itself be valid. |
| `hint` | no | Extra text for the hint bar. |
| `when` | no | Show the field only while other fields of the same form have these values (see below). |
| `disabled_when` | no | Keep the field visible but disabled while other fields have these values (see below). |

| `type` | Value | Extra keys | Gamepad |
|---|---|---|---|
| `toggle`, `checkbox` | `true`/`false` | none | A flips |
| `slider` | number | `min`, `max` (required), `step` (default 1), `unit` | ←→ step, Y default |
| `number` | number | same as `slider` | ←→ step, Y default |
| `radio` | one of the options | `options: [{value, label}]` (at least 1; value is a string, number or bool) | ←→ choose |
| `select` | one of the options | same as `radio` | ←→ cycle, A opens the list |
| `text` | string | `max_length` (default 256), `placeholder` | A opens the built-in keyboard |
| `password` | string | same as `text`; `default` must be `""` | like `text`; shown as dots, the keyboard has a show/hide key |

`password` only masks what is on screen: the value is stored as plain text in the module's `settings.json`
and `ctx.settings.get` returns it as is. Don't use it as a condition (`when`/`disabled_when`).

**Sections** have the form `{ "section": "Title", "open": true, "items": [fields…] }`. They:

- can be folded with X;
- can't be nested;
- `open` defaults to true;
- take `when` too.

**`when`** shows a field or a section only while other fields of the same form (the same `settings`, or the same
`forms` entry) have the given values: `{"adaptive": true}`, or several keys, which must all match. A value can also
be a list, matched by any of its values: `{"method": ["ls1", "mako"]}`. Each key must be a field of that form, other
than the field itself, and each value must be one that field can take.

**`disabled_when`** (fields only) works the same way, but the field stays on screen, disabled, and its hint says
which option set it ("Set by Ultra performance"). Use it when another option decides this one's value.

Both only change what's on screen: a hidden or disabled field keeps its value, and the backend stores and validates
it as usual. If one option changes others (e.g. a preset), do that in the module's backend.

```json
{ "key": "multiplier", "type": "number", "label": "Multiplier", "min": 2, "max": 5, "default": 2,
  "when": { "adaptive": false } },
{ "section": "Adaptive", "when": { "adaptive": true }, "items": [ … ] },
{ "key": "flow_scale", "type": "slider", "label": "Flow scale", "min": 0.25, "max": 1, "step": 0.05, "default": 0.8,
  "disabled_when": { "ultra_performance": true } }
```

**How values are normalized:**

- A number is clamped to `min..max` and snapped to the nearest step counted from `min`, with no float noise
  (`0.1 + 0.2` is stored as `0.3`).
- An option must match exactly: `1` and `"1"` are different values.
- A stored value that is no longer valid (the schema changed, or the file was edited by hand) reads as its
  default, and the log says so.

**Forms the module stores itself.** Sometimes the values belong to someone else, for example another program's
config file (Patito edits lsfg-vk's `conf.toml`). Declare them under `forms`, with the same field rules:

```json
"forms": {
  "profile": [ { "key": "multiplier", "type": "number", "label": "Multiplier", "min": 2, "max": 20, "default": 2 } ],
  "global":  [ { "key": "allow_fp16", "type": "toggle", "label": "Allow FP16", "default": true } ]
}
```

- **In `backend.py`:** `ctx.forms["profile"].coerce(key, value)` validates one value (clamped, snapped…) or raises
  `InvalidArgument`; `.clean(data)` gives every field a valid value; `.defaults()` gives the defaults. Where the
  values are stored is up to the module.
- **In `ui.ts`:** `await ui.form(ctx, "profile", { get, set })` draws the form exactly like `settingsForm`, with
  `get()` returning every value and `set(key, value)` saving one and returning what was stored.

## 4. `backend.py`

```python
ctx = None

def setup(context):          # optional: once at load (and again if re-enabled)
    global ctx
    ctx = context
    context.settings.on_change(apply)   # the user changed a setting in the panel

def teardown():              # optional: disabled, or the service is stopping
    ...                      # stop threads, timers, subprocesses

def upgrade(context, previous_version):  # optional: a different version than last time loads
    ...                      # migrate context.data, files… (runs before setup)

def uninstall(context, purge):  # optional: the user is uninstalling it
    ...                      # undo what it left outside its folder

def apply(key=None, value=None):
    fps = ctx.settings.get("fps")       # always valid, never missing
    ...
    return {"ok": True}                 # results must be JSON-able

METHODS = {"apply": apply}   # what the UI can call: ctx.call("apply")
```

**What `context` gives:**

| Member | Meaning |
|---|---|
| `settings.get(key=None)` | One setting, or all of them as a dict. |
| `settings.set(key, value)` | Validated like the form. Returns the stored value. It doesn't trigger `on_change`. |
| `settings.on_change(cb)` | `cb(key, value)` after the user changes a setting in the panel. |
| `data` | Free-form JSON store for whatever isn't a setting: `load()`, `save(dict)`, `update(**kw)`. |
| `forms` | `{name: form}` from `module.json` `forms`: `coerce(key, value)`, `clean(data)`, `defaults()`. |
| `toml` | Other programs' TOML config files. `load(path)` returns `(data, mtime)`, or `({}, None)` if the file doesn't exist. `save(path, data, mtime)` writes the file, refusing with `Unavailable` if another program saved it after that `mtime`. It keeps every key and the order, checks the result by reading it back, writes atomically and keeps the original as `*.invasor-backup` the first time. `save(…, validate=fn)` calls `fn(temp_path)` on the new file before it replaces the original (e.g. the program's own config validator); raising there refuses the change and leaves the original untouched. Needs Python 3.11 or newer. |
| `on_steam_start(cb)` | `cb()` every time Steam starts or restarts (a new Steam process, including switching between Desktop and Game Mode), and once soon after you register it if Steam is already running, so it also covers Invasor starting and the module being enabled. It runs in its own thread and may block (scans, file writes…); an exception is logged. Register it in `setup`: it's forgotten when the module is disabled. |
| `game.info(appid)`, `game.shortcut_exe(appid)` | Name and kind of any app; the executable a non-Steam shortcut launches (`shortcuts.vdf`). |
| `game_data(appid)` | The same kind of store, per game. |
| `game.selected`, `game.running` | `{"appid", "name", "shortcut"}`, or `None`. The highlighted game isn't here: it's read from Steam's page only when the UI asks, so it's available in `ctx.game()` in `ui.ts`. |
| `log` | This module's logger (it writes to `journalctl --user -u invasor`). |
| `id`, `path` | The module id and its folder. |
| `notify(title, body="", icon="")` | A notification shown as Steam shows an achievement. Title up to 64 characters, body up to 256 (plain text); `icon` is an `https://` URL or an embedded `data:image/png|jpeg|gif|webp;base64,…` (up to 512 KB). From `async def` methods: `await notify_async(…)`. Raises `InvalidArgument` for bad input and `Unavailable` when Steam can't show it. Steam's own achievement toast setting applies. |
| `steam_call(path, *args)` | `SteamClient.<path>(*args)` (Steam's JS API), evaluated in Steam's `SharedJSContext`. Use it from plain methods; from `async def` methods use `await steam_call_async(path, *args)`. It raises `Unavailable` whenever Steam or that function isn't there, so always have a plan B. |
| `InvalidArgument`, `Unavailable` | Errors to raise for a clean answer, with one log line and no traceback: `raise ctx.InvalidArgument("…")` gives 400 (the caller's mistake), and `raise ctx.Unavailable("…")` gives 503 (no network, a Steam function missing…). The UI receives the message. |

**Rules:**

- **Several files.** `backend.py` is loaded as a package, so it can import its own files with relative
  imports: `from . import helpers` loads `modules/<id>/helpers.py`. Each module's code is separate from the
  others' (two modules can both have a `helpers.py`). Disabling a module drops its code; enabling it again
  loads it afresh.
- **Threads.**
  - Plain functions in `METHODS` run in a worker thread, so they may block (downloads, subprocesses…).
    Several calls can run at the same time.
  - `async def` functions run in the service's event loop and **must not block**.
  - `setup`, `teardown` and `on_change` callbacks may run in any thread and must return quickly.
  - `on_steam_start` callbacks get a thread of their own each time, so they may block. If Steam restarts
    while one is still running, another one starts: guard it with a lock if that matters.
  - For background work, start your own thread (daemon) in `setup` and stop it in `teardown`. `teardown`
    gets 10 seconds (when the module is disabled, rescanned, replaced or the service stops); after that the
    module is considered stopped anyway and the log says so.
- **Arguments.** Methods get keyword arguments from the UI. If the arguments are wrong, the call fails with a
  "bad arguments" error and your function is never called.
- **Errors.** An exception (even `exit()`) becomes a rejected `ctx.call()` with its message. It is logged and
  never takes the service down. If `setup` fails, `teardown` is called to release whatever it started, and the
  module isn't loaded. `METHODS` is checked before `setup` runs.
- **Upgrades.** The core remembers the version of each module it last loaded. When a different one loads (the
  zip was replaced, Invasor updated a module it ships, or it was reinstalled with its data kept), `upgrade(context,
  previous_version)` runs first, then `setup`. The core doesn't compare versions: a downgrade calls it too. If
  `upgrade` raises, the module isn't loaded and the old version stays recorded, so it runs again next time. A
  disabled module upgrades when it's enabled. The first load ever doesn't call it.
- **Uninstall.** `teardown` also runs when the module is disabled, so it must never delete anything the user
  would want back. Undo what the module left outside its folder (another program's config, files in Steam's
  folders…) in `uninstall(context, purge)` instead. It runs only when the module is uninstalled: from ⚙ Settings
  (installed modules only: the ones shipped with Invasor can only be disabled there), or for every module when
  Invasor itself is uninstalled (`invasor-installation.sh --uninstall`, whose `--purge` becomes `purge`). It runs
  after `teardown` and before its files are deleted, even if it's disabled (its code is imported without `setup`). `purge` is `True` when the user also
  asked to delete its settings and data; the core then deletes `~/.config/invasor/modules/<id>/` itself. It has
  10 seconds; an error, `exit()` or running out of time is logged and the module is uninstalled anyway. From a
  terminal (`tools/install_module.py --uninstall`, uninstalling Invasor) the service is stopped and `steam_call`
  raises `Unavailable`, so it needs its plan B here too.
- **Tools.** Only the Python standard library is available. Don't `pip install` anything: the service must
  keep working after system updates.

## 5. `ui.ts`

```ts
import { defineModule, ui } from "invasor";

export default defineModule({
  // Either one page…
  async render(el, ctx) {
    el.append(await ui.settingsForm(ctx), ui.button({ label: "Apply now", onClick: () => ctx.call("apply") }));
  },
  // …or sub-tabs (L2/R2): tabs: [{ label, render(el, ctx), onShow?(ctx), onHide?() }],
  // tabsAlign: "start" | "center" | "end" | "justify",
  onShow(ctx) {}, onHide() {},          // the tab became visible / hidden
  onGameChange(game, ctx) {},           // selected/running game changed
  destroy() {},                         // module disabled or overlay torn down
});
```

- **Building.** Every page and sub-tab is built the first time it's shown, then kept with its state. An
  exception there shows "Error in …" in that page only.
- **What `ctx` gives:**

  | Member | Meaning |
  |---|---|
  | `ctx.call(method, args)` | Calls your backend's `METHODS`. |
  | `ctx.settings.get()`, `ctx.settings.set(key, v)` | The validated settings. `set` resolves to the stored value. |
  | `ctx.settings.schema`, `ctx.forms` | The schemas from `module.json`: `settings`, and each of `forms`. |
  | `currentGame(ctx.game())` | Imported from `"invasor"`: the game to act on and why (`selected`, then `highlighted`, then `running`), or `null`. |
  | `ctx.game()` | The game state, from most to least reliable: `running` (being played), `selected` (its page is open) and `highlighted` (the tile under the cursor in the library, best effort). Each is `{appid, name, shortcut}` or `null`. |
  | `ctx.toast(msg, "ok" \| "error")` | Shows a short message. |
  | `ctx.openWindow(spec)`, `ctx.canOpenWindow()`, `ctx.onSpaceChange(cb)` | Big windows (section 6). |
  | `ctx.steam.safeCall(path, …)`, `ctx.steam.hasApi(path)` | Steam's JS API, guarded. `safeCall` uses this window's `SteamClient` or, when it lacks the function, Steam's `SharedJSContext` through the backend; it rejects (never throws) if it's unavailable. `hasApi` only checks this window. |

- **The `ui` kit.** It works with the gamepad and touch out of the box.

  - **Settings form:** `await ui.settingsForm(ctx, { keys? })`. It returns the form, its `controls` by key,
    `reload()` and `reset()` (which stores every default). `await ui.form(ctx, name, store)` is the same for a
    `forms` entry stored by the module (section 3).
  - **Value controls:** `toggle`, `checkbox`, `slider`, `number`, `radio` (an option with `swatch: "#hex"` shows as a colour dot), `select`, `text` and `password`, for values
    that aren't settings. Each returns a `Control` with:
    - `get()`;
    - `set(v)`: shows a new value without calling `onChange`;
    - `setDisabled(on, reason)`: the control stays selectable and the hint shows `reason`.
  - **Actions:**
    - `button({ label, onClick })` returns `setLabel` and `setDisabled`;
    - `await ui.confirm(message, { ok, cancel })` starts on Cancel;
    - `await ui.choose(message, [{ label, value }…], { cancel })` is the same with several answers: it returns
      the chosen `value`, or `null` on Cancel. More than one answer stacks the buttons;
    - `windowButton(ctx, { open })`.
  - **Layout:** `section(title, children, { open })`, `info(text)`, `separator()`, and `image(src, { alt, maxHeight })`
    for a preview image (grey box while loading, ⚠ if it fails).
  - **Images:** `imageGrid({ items, aspect, columns, onActivate, activateLabel, onSelect })`. `activateLabel` is what A does in the hint bar ("open" by default). An item's `src` may be a short video (`.webm`/`.mp4`, or `video: true`), e.g. an animated thumbnail: it shows paused and plays only while its tile is highlighted.

  The controls throw a clear error if they're misused, e.g. `select` with no options.
  They follow the user's accent colour (⚙ Settings › Panel); a control you draw yourself should use the
  CSS variables `var(--accent)`, `rgba(var(--accent-rgb), 0.15)` and `var(--accent-fg)` (text on the accent).
- **Button roles** (keep custom controls consistent with them):

  | Button | Role |
  |---|---|
  | A | Accept |
  | B | Close/cancel |
  | D-pad | Move; ←→ change a value |
  | L1/R1 | Tabs |
  | L2/R2 | Sub-tabs |
  | X | Fold/unfold the section |
  | Y | The control's special value |

  For a control of your own, set `data-nav` and a `data-hint`, and listen to `invasor:button` on it, calling
  `preventDefault()` for what you handle.
- **Imports.** Import only from `"invasor"`. Everything in `module-api.ts` is the contract; the rest of the
  core can change.

## 6. Big windows ("expanded view")

`ctx.openWindow({ title, render | tabs, onClose })` opens a window over the panel.

- **Layout.** It covers almost the whole screen, with its own tabs (L1/R1) and sub-tabs (L2/R2). Its tabs
  have the same `onShow`/`onHide` hooks as the panel's.
- **Closing.** B closes it.
- **When there's no room.** That happens in Quick Access during a game, where only ~348px are visible.
  `openWindow` returns `null` and tells the user to open it from the Library. `ui.windowButton(ctx, { open })`
  handles this for you: it shows itself locked, with the reason.

## 7. Isolation: what can and can't break

| What goes wrong | What happens |
|---|---|
| Invalid `module.json` | The module isn't loaded; ⚙ Settings shows the error. |
| `backend.py` fails to import, `upgrade` or `setup` throws (or calls `exit()`), or `METHODS` is bad | The tab shows "the module's backend didn't load (see log)". |
| `uninstall` throws, hangs or the code no longer imports | It's logged and the module is uninstalled anyway. |
| `ui.js` has a syntax error or throws at load | It is evaluated on its own: the log says so, and the tab shows "its UI didn't load". |
| A render or hook throws | Only that page shows the error. |
| A slow method | It runs in its own thread; the panel and the gamepad keep working. |
| Corrupt or invalid stored settings | Invalid values read as their defaults and are corrected in the file. A file that isn't JSON is moved to `settings.json.corrupt`. |

## 8. Developing a module in its own repository

A module doesn't have to live inside Invasor. Keep it in its own repository, next to a checkout of the core:

```
~/Projects/invasor/            the core (this repository)
~/Projects/my-module-repo/
  README.md, LICENSE, .gitignore
  <id>/                        the module itself: the folder name is its id
    module.json, backend.py, ui.ts, tests/…
```

- **Build** its UI with the core's kit: `node ~/Projects/invasor/frontend/build.mjs --module <id>`.
- **Test:** the module's tests find the core through `$INVASOR_CORE`, which `check_module.py` sets for them. On
  their own they look for `../invasor` next to the repository. See `patito/tests/test_backend.py` in the invasor-patito repository.
- **Check and pack:** `python3 ~/Projects/invasor/tools/pack_module.py <id>`. It typechecks and builds `ui.ts`,
  runs `check_module.py` (tests included) and writes `<id>-<version>.zip` with only what the console needs:
  `module.json`, the Python files, `dist/ui.js`, README and LICENSE (the one at the root of your repository, if the
  module folder has none). Never the tests.
- **Install:**
  - **On the console:** ⚙ Settings › Install module, then choose the zip.
  - **From a terminal:** `python3 ~/Projects/invasor/tools/install_module.py <id or zip>`.

  Installed modules live in `~/.local/share/invasor/user-modules/<id>/`. Updating Invasor never touches that
  folder. Uninstalling from ⚙ Settings runs the module's `uninstall()` and removes its files. The user chooses
  whether to keep its settings and data (`~/.config/invasor/modules/<id>/`) in case it's installed again
  (`--uninstall <id> --purge` from a terminal deletes them).

**What the core checks before installing a zip**, refusing it with a clear message otherwise:
- **The zip itself:** it is a real zip within the size limits, with no absolute paths, no `..` and no
  symbolic links.
- **Its layout:** exactly one `<id>/` folder with a valid `module.json` for this module API.
- **The id:** it isn't reserved (`core`, or a module shipped with Invasor).
- **The code:**
  - a `ui.ts` comes with its built `dist/ui.js`;
  - `backend.py` imports cleanly, checked in a separate process, without running `setup()`.

These checks keep a broken module from breaking Invasor; they don't make a module safe. A module is trusted code:
its backend runs as your user and its UI runs inside Steam's window. Its `author` is whatever its `module.json`
says. Install only modules from people you trust.

An installed module is loaded and its UI injected right away, with no restart. Reinstalling the same id replaces
it after asking; if that fails half way, the previous version stays installed and loaded.

Module folders are read when the service starts. A folder added, removed, renamed or edited by hand (in
`modules/` or `user-modules/`) is picked up with **⚙ Settings › Modules › Rescan modules**: every module is
torn down and loaded again, and its UI injected, with no restart.

## 9. Before distributing

```sh
cd frontend && npm run build && cd ..
python3 tools/check_module.py modules/<id>
```

`check_module.py` checks four things:

- `module.json`, with the same rules as the service;
- that `ui.ts` is built and up to date;
- that `backend.py` imports and has valid `METHODS`;
- that the module's own tests in `tests/` pass. They run with `python3 -m unittest discover -s modules/<id>`, with
  the module folder on the path: `import helpers` works there. Test `backend.py` itself by loading it as a
  package; see `artwork/tests/test_backend.py` in the invasor-artwork repository.

A module folder with `module.json`, `backend.py` and `dist/ui.js` is self-contained, with no build step left:
`tools/pack_module.py` zips exactly that (section 8).
