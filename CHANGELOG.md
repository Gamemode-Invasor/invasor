# Invasor: changelog

Every change since the first release, newest first. Pre-releases (`-rcN`) are folded into the release they led to;
their dates are listed for reference. The notes of the release being prepared live in `RELEASE_NOTES.md`.

---

## Unreleased (beta branch, after 0.2.0)

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
