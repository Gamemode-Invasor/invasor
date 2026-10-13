New
- Modules can now choose the sound of a notification: `notify(title, body, icon, sound)` takes the new `sound` parameter (`trophy`, `message`, `toast`, `chat`, `mention`, `friend`, `online`, `ingame` or `none` for silence). It works in Game Mode and on the desktop client; omitted, Steam keeps its usual achievement sound
- Modules can now react to games and Steam with `ctx.events.on(name, cb)` / `ctx.events.off(name, cb)`: `steam_start`, `game_started`, `game_stopped` and `game_selected` (the page of a game was opened), with the game as `{appid, name, shortcut}`. A module can, for example, hold back a heavy download while a game is running and resume when it ends. Games are checked every 3 seconds. `ctx.on_steam_start` keeps working (the module API is still 1) but is deprecated and logs a warning once
- Long operations can now report their progress: `ctx.task(label)` gives modules a task they update (`fraction`, `text`) and end, as a `with` block or by hand, and the core lists them (`core.tasks()`, and a `task` event for modules). Updating Invasor and installing from the Market report theirs
- New Tasks panel: press Select in the Invasor panel (or its **Tasks** button) to see what runs in the background, with its progress, and the last ones that ended. A dot on the "I" handle pulses while something runs and turns red when a task failed and you haven't looked yet. Nothing is polled: Invasor tells the windows when a task changes
- ⚙ Settings is no longer the last tab of the panel: it is a **⚙** button in the panel's header (between **Tasks** and **✕**), and Start opens it with the controller. It covers the Invasor panel the way Tasks does, and B or Start go back to it. Select and Start work only from the panel in front of them
- Module packing (`tools/pack_module.py`) now includes Python sub-packages, `bin/` (as executable) and `assets/`, and checks every hook (`setup`, `teardown`, `upgrade`, `uninstall`)
- A module that was unloaded (disabled, uninstalled, replaced or whose `setup` failed) can no longer save settings or data, or register callbacks, from a thread it left running
- Uninstalling a module also removes it from the module order and the disabled list, so a reinstall starts enabled

Fixes
- A module with an absurd slider/number range in `module.json` could crash the service in a restart loop; it is now reported as an invalid module
- Wrong argument types in the API (e.g. `"false"` for `purge`) are refused instead of being read as true, which could delete a module's data
- Modules: a failure after a successful `setup` now calls `teardown`; a module whose import hangs can no longer stop an uninstall; stuck module calls give their slot back after 10 minutes
- Updates and the Market: redirects out of https are refused, a dead network is no longer asked again on every call, and Market installs work when the cache folder is on another disk
- Installing the same module twice at once, a zip with empty or `.` entries, and a Downloads folder outside your home no longer break the installer or the file picker
- Panel: rejected handlers no longer go unnoticed, stale answers no longer overwrite newer ones (Market, file picker, module order), a pending confirm closes with the panel, grid videos stop when it closes, Quick Access closes when the game ends and F10 only acts when the panel can toggle
- Renamed non-Steam shortcuts show their new name without restarting Invasor
---
