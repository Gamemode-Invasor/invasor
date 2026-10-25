# Invasor: changelog

Every change since the first release, newest first. Pre-releases (`-rcN`) are folded into the release they led to;
their dates are listed for reference. The notes of the release being prepared live in `RELEASE_NOTES.md`.

---

## 0.3.0 (unreleased)

Pre-releases: rc1 (2026-10-25).

### New

- **Steam's folders for modules: `ctx.paths`.** `steam_root()`, `libraries()` (every `steamapps` folder, from `libraryfolders.vdf` plus SD cards and USB drives under `/run/media`), `userdata()` (the account in use: `loginusers.vdf`'s `MostRecent` user, else the newest login, else the only account; `all=True` lists every account), `grid_dir()`, and a game's `compatdata(appid)` / `shadercache(appid)` in the library that holds it. Read only; `Unavailable` when Steam's folder or the account can't be found. Needs `min_core` 0.3.0. Demo has a "Steam folders" button
- **Steam's VDF files for modules: `ctx.vdf`.** `loads_text` for text VDF (`libraryfolders.vdf`, `appmanifest_<appid>.acf`, `loginusers.vdf`…), `loads_binary` (`shortcuts.vdf`) and `get(node, *path, default=None)`, which ignores the case of the keys. Read only. `VDFError` is now an `InvalidArgument`. Needs `min_core` 0.3.0
- **Unpacking archives for modules: `ctx.archive`.** `extract(archive, dest, …)` unpacks zip, tar (plain, `.gz`, `.xz`, `.bz2`), 7z and `tar.zst` (these two through `bsdtar`, which SteamOS ships) with the same rules for all: into a temporary folder next to `dest` and renamed into place, nothing outside it, links only with `symlinks="inside"`, no devices or hard links, modes normalised, limits on files (folders included) and bytes really written. `swap_dir(new, target)` replaces a folder in one rename. Needs `min_core` 0.3.0. Demo has an "Unpack sample" button
- **Downloads for modules: `ctx.http`.** `download(url, dest, …)` and `get_json(url)`, with `_async` twins for `async def`. https only, and a redirect that leaves https is refused; with a `checksum` (sha256 or sha512) a file that doesn't match never reaches `dest`; without one the user is asked first, in a dialog and in the Tasks panel (and a Steam notification says so), unless `user_chose=True` for a file the user just picked (at most 100 MB, logged). Written next to `dest` and renamed at the end, never executable; stops when the module is unloaded. Not a sandbox: `urllib` still works, unchecked. Needs `min_core` 0.3.0. Demo has "Download (checked)" and "Download (asks you)" buttons
- **Tasks that ask: `t.ask(question)`.** A task can wait for the user's answer: it shows as waiting (new `"waiting"` state, with its question) with Accept / Deny in the Tasks panel, and the dot on the handle shows it first; no answer in 10 minutes is a no. With the panel open the question also shows as a dialog where the user is (with the module's name, starting on Deny); B puts it off until the panel opens again, it closes by itself when answered in Tasks or ended, and it steps aside for a module's own `ui.confirm` and comes back after it. In the Tasks panel Deny comes first too, and its buttons stay off until the answer is known. The `task` event also fires when a task starts waiting
- **Changing the user's Steam: `ctx.steam`.** `supports(name)`, `open_url(url)` (Steam's browser), `add_to(name, exe, …)` (a non-Steam shortcut: `{"appid", "existed"}`), `remove_shortcut(appid)` (only one this module added, still pointing to the same exe), `launch_options(appid)` and `set_launch_options(appid, line)` (read back: `{"applied", "verified"}`), with `_async` twins for `async def`. Only on a user action; every change is logged and shown as a Steam notification. `add_to` and `open_url` fall back to `steam://` links through gio/xdg-open; removing and launch options have no fallback, so check `supports()` and keep a plan B. Needs `min_core` 0.3.0. Demo has a "Steam (ctx.steam)" section that works on a Demo shortcut of its own
- **Steam's language: `ctx.steam_language(raw=False)`.** A short code (`"en"`, `"es"`…) or, with `raw=True`, Steam's own name, read from `~/.steam/registry.vdf`. Needs `min_core` 0.3.0. Demo has a "Steam language" button
- **Copying and picking files in a module's UI: `ctx.copy` and `ctx.pickPath`.** `ctx.copy(text)` copies to the clipboard (with a fallback that gives Steam its focus back) and answers whether it worked; `ctx.pickPath({...})` opens a file or folder picker window and answers the real path, or `null`. Options: files or a folder, endings (`ext`), where it opens, the choose button's label, hidden entries. Needs `min_core` 0.3.0. Demo has a "Files" section

### Fixes

- **A module's `ctx` follows the same rules everywhere once it's retired.** `steam_call` and `notify` raise `Unavailable` after the module is unloaded, like everything new; `game_data(appid)` refuses a bad appid with `InvalidArgument` (it was a bare `ValueError`, and accepted `0`)
- **No module can pass for the core.** A `module.json` whose `name` or `tab` is `Invasor` (in any case) is refused: that's the name the Tasks panel and a question's dialog show for the core itself.
- **What the user reads can't be bent.** Control characters and bidi overrides in a task's label, text or question become spaces, so a question can't break into lines or pass for another; URLs with control characters are refused by `ctx.http` too (one check for `ctx.http` and `ctx.steam.open_url`); `ctx.steam.add_to` refuses an `exe` or `start_dir` with control characters (they could add lines to the fallback's `.desktop`)

### Internal

- The game context finds Steam's libraries with `paths.py` (`libraryfolders.vdf` read with the text VDF reader instead of a regex), so games in a library on an SD card or USB drive under `/run/media` have their name too
- The updater unpacks its package with `archive.py`, the same rules for every format; the module zip installer is unchanged
- The updater, the Market and `tools/gen_market.py` download through `http.py`: a redirect that leaves https is refused before it's followed (it was only checked after), a failed download leaves no partial file, and `gen_market`'s GitHub token is no longer sent on a redirect to another host
- `steam.notify_later` shows a notification from a thread of its own, never fatal: shared by task toasts, the download question and `ctx.steam`
- `core.browse_path` lists one folder for `ctx.pickPath`: real paths, folders first, game names next to numbered `compatdata` / `shadercache` folders, at most 1000 entries
- One reader of `shortcuts.vdf` and one appmanifest lookup (`paths.py`) for the game context and `ctx.steam`; game names come from the text VDF reader instead of a regex

---

## 0.2.2 (2026-10-22)

### Fixes

- **Choosing a module zip runs none of its code.** In ⚙ Settings › Install module, `core.module_inspect` used to
  import the module's `backend.py` to check it before the "Install?" dialog, so its top-level code ran even if you then
  pressed Cancel. Now inspecting does every check but that import; `install()` imports it after you confirm and before
  anything installed is touched, so a broken new version still leaves the old one loaded. Both work on a copy of the
  zip made while hashing it: `inspect()` returns its `sha256` and `install(..., sha256=...)` refuses a zip that changed
  meanwhile ("the zip changed since it was checked: choose it again"). The size limit also holds while copying. A zip
  whose `backend.py` doesn't import is now refused when you press Install, with the same "Not installed: …" toast
- The notification of a `ctx.task` that Steam can't show is logged ("task notification not shown") instead of
  failing silently in its thread
- The game context finds Steam's folder as `cef_flag` does: `~/.steam/steam`, then `~/.local/share/Steam`
- The service refuses a module whose `setup`/`teardown`/`upgrade`/`uninstall` isn't callable, as the install check
  already did
- Each `/proc/<pid>/environ` is read once and cached by pid, start time and command name: about 18 ms → 5 ms per poll
  with ~170 processes

### Internal

- Core cleanup with no change to the module API or the 34 `core` methods: one sha256 check for updates and Market
  downloads (`updater.verify_sha256`), `config.save(cfg, **changes)` for every write of `config.json`,
  `checkmod.check_module()` with the same rules when installing and loading, `set_pref` validated against
  `config.VALID`, `SteamBridge.call_sync` through `_sync`, `core.default_market()`, and `core.make_methods` split into
  overlay, manage, module and update sections. Unused imports removed
- Tests: 7 tests in `test_updater.py` sat under a `__main__` guard and never ran; they run now. The `steam_start` test
  no longer waits a real second. New tests for the process cache, the Steam folder lookup and zip inspection
- Docs: every module listed (Popy and Cometa replace Noty), method queue limits, the Market catalog refreshes every
  12 hours, Deckico and GE-RR use the `steam_start` event; the separate check process catches broken modules, it
  isn't a sandbox

---

## 0.2.1 (2026-10-14)

- A task started with `ctx.task(label)` shows a notification when it starts and when it ends (with the final text as
  its body), with the `toast` sound unless the module picks another one: `ctx.task(label, notify=True, sound="toast")`.
  A module can turn it off with `notify=False`
- When Steam starts, Invasor looks for a new Invasor version (as before, then daily) and for newer versions of your
  installed modules in the Market. A new Invasor version shows its own notification; module updates show a single
  generic one. A version older than the installed one is never announced

---

## 0.2.0 (2026-10-14)

Pre-releases: rc1 (2026-10-12), rc2 (2026-10-13), rc4 (2026-10-13), rc5 (2026-10-13).
This release also contains everything planned as 0.1.5 (its rc1 was renamed to 0.2.0-rc1).

### New

- **Notification sounds.** `notify(title, body, icon, sound)` takes the new `sound` parameter (`trophy`, `message`,
  `toast`, `chat`, `mention`, `friend`, `online`, `ingame` or `none` for silence). It works in Game Mode and on the
  desktop client; omitted, Steam keeps its usual achievement sound. Demo has a button for each sound
- **Event bus for modules (phase 1).** `ctx.events.on(name, cb)` / `ctx.events.off(name, cb)` with `steam_start`,
  `game_started`, `game_stopped` and `game_selected` (the page of a game was opened); the game is passed as
  `{appid, name, shortcut}`. Games are checked every 3 seconds. `ctx.on_steam_start` keeps working (the module API is
  still 1) but is deprecated and logs a warning once
- **Background tasks with progress.** `ctx.task(label)` gives modules a task they update (`fraction`, `text`) and end,
  as a `with` block or by hand; the core lists them (`core.tasks()`, and a `task` event for modules). Updating Invasor
  and installing from the Market report theirs
- **Tasks panel.** Press Select in the Invasor panel (or its **Tasks** button) to see what runs in the background, with
  its progress, and the last ones that ended (**Clear recent** forgets them). A dot on the "I" handle pulses while
  something runs and turns red when a task failed and you haven't looked yet. Nothing is polled: Invasor tells the
  windows when a task changes. Ages refresh by themselves; uninstalling a module leaves no task open
- **Settings is a header button.** ⚙ Settings is no longer the last tab: it is a **⚙** button in the panel's header
  (between **Tasks** and **✕**), and Start opens it with the controller. It covers the Invasor panel the way Tasks
  does, and B or Start go back to it. Select and Start work only from the panel in front of them. Tasks and Settings
  take the whole height of the window
- **Module packing** (`tools/pack_module.py`) includes Python sub-packages, `bin/` (as executable) and `assets/`, and
  checks every hook (`setup`, `teardown`, `upgrade`, `uninstall`); the `notify` timeout is documented
- A module that was unloaded (disabled, uninstalled, replaced or whose `setup` failed) can no longer save settings or
  data, or register callbacks, from a thread it left running
- Uninstalling a module also removes it from the module order and the disabled list, so a reinstall starts enabled
- Docs: what a module's `ctx` can do after unload and inside `uninstall()`. Demo shows every module feature
  (`ctx.path`, notify timeout, `ui.number`, `Control.get()`, notification sounds, events, tasks…) and the smoke test
  covers them

### Fixes

- A module with an absurd slider/number range in `module.json` could crash the service in a restart loop; it is now
  reported as an invalid module
- Wrong argument types in the API (e.g. `"false"` for `purge`) are refused instead of being read as true, which could
  delete a module's data
- Modules: a failure after a successful `setup` now calls `teardown`; a module whose import hangs can no longer stop an
  uninstall; stuck module calls give their slot back after 10 minutes; the socket of cancelled requests is closed;
  the Steam-start thread list is locked; unloading a module can't trip over an event that was just starting; event
  callbacks no longer share (and corrupt) one payload; a huge task progress value is cut to 0..1 instead of failing
- Updates and the Market: redirects out of https are refused, a dead network is no longer asked again on every call,
  and Market installs work when the cache folder is on another disk (downloads stay inside home whatever
  `XDG_CACHE_HOME` is). The updater keeps the package per check and extracts with a filter; the TOML reader refuses a
  change during validation; the token is no longer left on `window`; write errors are no longer masked
- Installing the same module twice at once (now one module at a time), a zip with empty or `.` entries, and a
  Downloads folder outside your home no longer break the installer or the file picker
- Panel: rejected handlers no longer go unnoticed, stale answers no longer overwrite newer ones (Market, file picker,
  module order), a pending confirm closes with the panel, grid videos stop when it closes, Quick Access closes when the
  game ends, F10 only acts when the panel can toggle, the last tile is kept when focus falls to nowhere, and a replaced
  module is destroyed only after its tab is gone. The frontend API gives clear errors for non-JSON or result-less
  answers
- A dialog drawn in the Tasks or Settings panel no longer stays behind when the panel closes, the pad's ring lands on
  the first control when Tasks opens, and A no longer presses a hidden control
- Renamed non-Steam shortcuts show their new name without restarting Invasor
- Tests: expected errors, logs and warnings are kept out of the run's output; the zip test skips the empty entry where
  Python 3.9 can't write it; the smoke test says clearly when the Demo tab isn't there and restores Demo if it was
  uninstalled

---

## 0.1.4 (2026-10-09)

Pre-releases: rc1 (2026-10-06), rc2 (2026-10-09), rc3 (2026-10-09).

### New

- **Module Market** (Settings > Install module > Open the Market): browse the modules of the Invasor organization's
  repositories in a big window and install or update them with one press; downloads are checked before installing.
  The catalog is read from `market.json`, generated hourly by the workspace; when GitHub's request limit is reached it
  says so instead of "GitHub answered 403"
- **Settings > Manage Invasor:** updates, restart Invasor or Steam, view the log, reset the configuration and
  uninstall. It is the first section of Settings
- Settings: "Modules" is now **Manage modules**; Demo can be uninstalled and restored from there
- The L1/R1 and L2/R2 labels beside the tabs can be tapped to switch tab or section (without an outline)
- Handle: the library's own handle is hidden while Quick Access shows its own

### Fixes

- Security: a module zip can no longer write files outside its install folder; the installer trusts the checker's exit
  status
- Robustness: slow or hung modules (setup runs bounded and off the event loop, listing never waits, Steam-start
  callbacks stop with the module, module calls run in their own daemon threads), power cuts (durable writes), failed
  updates (the installer rolls back on any failure after the swap and keeps an interrupted install's backup) and
  unresponsive Steam windows (bounded CDP sends) can no longer freeze Invasor, empty your configuration or leave a
  broken install
- Quick Access always shows the panel and its "I" on the left; "Panel side" now only applies to the Library
- The panel gets a fresh module list on every open; tab and Settings state can't go stale
- Halfway rounding agrees everywhere (number settings, sliders)
- Minor fixes: the library handle, module lists, tabs, forms, number settings and notifications stay in sync
- Internal: DOM tests with jsdom for the frontend (tabhost, Settings, window button)

---

## 0.1.3 (2026-10-05)

Pre-releases: rc1 (2026-10-03), rc2 (2026-10-04).

- Each line of the update notes shows on its own line in the panel
- You can change from the beta to the stable channel and Invasor is downgraded; on a release candidate, the stable
  channel offers the way back
- Rebranding of the example modules: ducky → **patito** and fishy → **pescao** (docs updated)
- New `password` setting type for modules: what you type shows as dots (for API keys)
- Modules can declare `min_core`, the oldest Invasor they need; an older one refuses to install them
- Settings > Panel: choose what the handle shows: the icon, the letter I or nothing
- Settings > Module order: reorder the module tabs with the controller or by touch
- A module can decide for itself whether it shows in Quick Access
- The service puts Steam's CEF debugging flag file back when it starts, if something deleted it
- CI also runs on pushes to `beta`

---

## 0.1.2 (2026-10-03)

- The update panel shows a short summary of what changed
- New update channel in Settings > Updates: **Stable** (default) or **Beta**, which also offers pre-releases to try

---

## 0.1.1 (2026-10-03)

- Installer: if the new version doesn't start, the previous one is put back

---

## 0.1.0 (2026-10-03): first release

- A panel of its own inside Steam's gamepad UI (Game Mode and Big Picture), with modules, in the spirit of Decky Loader
  but built to last without depending on Steam: no Steam internal components, webpack or React; own shadow DOM, own
  on-screen keyboard and own controls. From Steam it only uses the CEF debugger injection and the `vgp_*` gamepad
  events
- Backend in the Python standard library only (3.9+): no `pip`, no venv; runs as a user systemd service, never needs
  root
- Controller support (evdev and Steam Deck-protocol pads, InputPlumber-virtualised handhelds), touch, and F10; L3 + R3
  opens the panel; the blue "I" handle; Quick Access (···) integration
- Modules (API 1): settings declared in `module.json` (validated by the backend, form drawn by the panel), optional
  `backend.py` and `ui.ts`, per-module isolation, install from a zip. Ships with `demo` and the `_example` template
- ⚙ Settings tab: enable or disable modules, the open shortcut, panel side, accent colour, About
- Installer (`invasor-installation.sh`): install / update / uninstall (`--purge`), with checks before and a restart
  check after; release packaging (`tools/pack_release.py`) with `.sha256`
- Updater: checks GitHub for new releases (at startup and daily), verifies the checksum and installs, without
  restarting Steam
