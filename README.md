# Invasor

**English** · [Español](README.es.md)

A panel of its own inside Steam's gamepad UI (**Game Mode and Big Picture**; not the desktop client) with modules, in the spirit of Decky Loader, but built to **last without depending on Steam**:

- It uses none of Steam's internal components, webpack or React. The UI lives in its own shadow DOM, with its own on-screen keyboard and its own controls.
- From the Steam client it only uses what can't be avoided: injection through the CEF debugger and the `vgp_*` gamepad events. Everything else has its own implementation.
- The backend uses only the **Python standard library**: no `pip`, no venv. It runs as a **user** service and never needs root.

## Requirements
- To run it: `python3` (3.9 or newer) and systemd user services.
- To build the frontend: Node.js (`esbuild` and `typescript` as dev dependencies).

## Install / uninstall
```sh
cd frontend && npm install && npm run build && cd ..
./invasor-installation.sh --install              # install or update, then (re)start invasor.service (user)
./invasor-installation.sh --uninstall            # remove the service and ~/.local/share/invasor
./invasor-installation.sh --uninstall --purge    # ... and the configuration (~/.config/invasor) too
```
With no arguments (or opened with a double click) it asks what to do in a dialog, if `zenity` is installed; otherwise it shows its help.

- **Install / update:** everything is checked first (Python 3.9+, systemd user services, Steam, an up to date frontend build), then only what the service runs is copied (no tests, no `_example` template), swapped in at once, and the service is restarted and checked. Modules installed from a zip are kept. If the new version doesn't come up, the previous one is put back.
- **Uninstall:** the overlay is taken out of Steam right away, with no Steam restart. Each module first undoes what it left outside its folder (its `uninstall()`). Everything in `~/.local/share/invasor` goes, modules installed from a zip included. The configuration and module settings in `~/.config/invasor` are kept unless `--purge` is given (the dialog asks).

After the first install, restart Steam once: the installer creates `~/.steam/steam/.cef-enable-remote-debugging`, like Decky does. If something deletes it later, the service puts it back the next time it starts (Steam needs a restart for it to apply). Uninstalling removes it only if Invasor created it, the installer or the service, and Decky isn't installed.

### Release for other machines
```sh
python3 tools/pack_release.py --out release/   # build, test and pack
```
It writes `invasor-<version>.tar.gz` (and its `.sha256`): already built, with the installer, the core and the bundled modules. On the target Steam Deck / SteamOS no Node is needed: extract it (Ark, or `tar -xzf`), then double click `invasor-installation.sh` or run `./invasor-installation.sh --install`.

### Updates
**⚙ Settings › Manage Invasor** has a *Check for updates* button and an *Install* one. Invasor asks GitHub (`api.github.com`, the latest release of [Gamemode-Invasor/invasor](https://github.com/Gamemode-Invasor/invasor)) and, when its tag is a higher version than the installed one, the install button is enabled. Installing downloads the release package, checks it against the `.sha256` published next to it and runs its installer. The service restarts and the panel reloads by itself a few seconds later; Steam and any running game are not restarted.

By default it also checks when Steam starts and then daily, and Steam shows a notification once per new version. At the same time it looks in the Market (the module catalog on GitHub) for newer versions of the modules you have installed: if there are any, one generic notification says so (never for an older version). Besides what you open yourself (the Market), that is the only connection Invasor makes to the Internet; switch it off with *Check for updates automatically* (`update_check` in `config.json`). The checksum catches a corrupt download, not a compromised GitHub account. Modules installed from a zip are not touched.

**Update channel:** *Stable* (the default) only offers final releases (`X.Y.Z`). *Beta (pre-releases)* (`update_channel`: `"beta"`) also offers release candidates (`X.Y.Z-rcN`, for example `0.1.3-rc1`), which may be unstable. Changing the channel looks for an update right away. A final release is newer than its candidates (`0.1.3-rc1` < `0.1.3`) and versions never go down, with one exception: on a release candidate, choosing *Stable* offers the stable version even if it is older (*Go back to 0.1.2*); you confirm it first. If it is the same version, nothing is offered.

Log: `journalctl --user -u invasor -f`. Optional configuration: `~/.config/invasor/config.json` (`open_combo`, `panel_side`, `targets`, `disabled_modules`, `removed_modules`, `dev_desktop`, `handle_icon`, `module_order`, `update_check`, `update_channel`; most of them can also be changed from the **⚙ Settings** panel).

## Using it with a controller
The Invasor handle (a coloured tab with the icon, or the letter "I") is always there in the library. In Quick Access (···) it only shows while a game is running, and the panel there leaves out modules that have nothing to do in-game (`"no_qam": true`, e.g. Artwork).

| Button | Action |
|---|---|
| L3 + R3 | Open / close the panel (with a game running: press ··· first). Can be changed in ⚙ Settings |
| L1 / R1 | Previous / next tab: each module is a tab |
| L2 / R2 | Previous / next sub-tab, if the module has any |
| D-pad ↑↓ | Move (folded content is skipped). With a lot of content, pages through it |
| D-pad ←→ | Change the control's value |
| A / B | Select / close (or cancel, depending on the control) |
| X | Fold / unfold the section (from its title or from inside it) |
| Y | The control's special value, e.g. back to its default |
| Start | Show / hide the ⚙ Settings panel (also its ⚙ button in the header). It covers the Invasor panel like Tasks does; B or Start go back |
| Select | Show / hide the Tasks panel (also the **Tasks** button in the panel's header): what runs in the background, with its progress, and the last ones that ended. B closes it too, and a **Clear recent** button forgets the ones that ended. A dot on the "I" handle and on that button pulses while something runs and turns red if a task failed and you haven't looked yet |

The panel takes 40% of the screen width in the Library (in Quick Access, the visible column). Only its content scrolls; the shadows at the top and bottom show there's more to see.

While the panel is open, Steam gets none of the panel's buttons (its own Steam and ··· buttons always go through). It can also be opened with the blue tab, by touch, or with F10.

Regular controllers (evdev: DualSense, Xbox…), Steam Deck-protocol ones (a real Steam Deck, or handhelds such as the Legion Go virtualised by InputPlumber) and the new Steam Controller (Bluetooth, USB cable or its USB receiver) are read. To see which buttons the service detects: `python3 tools/pad.py`.

**⚙ Settings panel** (the ⚙ button in the panel's header, or Start; it works even with no modules): enable or disable modules (Manage modules), install one from a zip or from the Market (Library only), the shortcut that opens the panel, the panel side (Library only: Quick Access is always on the left), the order of the modules' tabs (grab a module with A, move it with the D-pad), what the handle shows (the icon, the letter "I" or nothing), the accent colour (also of the handle), "Manage Invasor" (updates, restart Invasor or Steam, view the log, reset the configuration, uninstall) and "About" (version, status and detected controllers). Invasor's own UI is in English; each module chooses its own language.

## Modules
Invasor ships with only `demo` (a showcase, also used by `tools/smoke.py`) and the `_example` template. Demo can be uninstalled from ⚙ Settings › Manage modules (it stays uninstalled after updates; the same section has a *Restore* button). Real modules live in their own repositories and install as a zip from **⚙ Settings › Install module**:

- [invasor-artwork](https://github.com/Gamemode-Invasor/invasor-artwork): community artwork from steamgriddb.com for your games and shortcuts.
- [invasor-patito](https://github.com/Gamemode-Invasor/invasor-patito): lsfg-vk frame generation, set up per game.
- [invasor-companion](https://github.com/Gamemode-Invasor/invasor-companion): builds a game's launch options, adds non-Steam games, shows what Steam knows about the current game and cleans leftovers.
- [invasor-deckico](https://github.com/Gamemode-Invasor/invasor-deckico): puts each game's icon on its compatdata and shadercache folders.
- [invasor-ge-rr](https://github.com/Gamemode-Invasor/invasor-ge-rr): keeps the latest GE-Proton installed as a single compatibility tool.
- [invasor-pescao](https://github.com/Gamemode-Invasor/invasor-pescao): MAKO Renderer frame generation and scaling profiles, one per game.
- [invasor-rakun](https://github.com/Gamemode-Invasor/invasor-rakun): download your games from third-party stores via the Rakun engine.
- [invasor-popy](https://github.com/Gamemode-Invasor/invasor-popy): show your own notifications on Steam, sent over the web to a local service.
- [invasor-cometa](https://github.com/Gamemode-Invasor/invasor-cometa): shows the achievements of GOG games run through Comet as Steam notifications.

Installed modules live in `~/.local/share/invasor/user-modules/`. Updating Invasor never touches them.

## Writing a module
The full contract is in **[docs/MODULES.md](docs/MODULES.md)** (module API 1). In short:

```
modules/<id>/
  module.json     required: api, name, version, and the settings form
  backend.py      optional: METHODS, setup(ctx), teardown()
  ui.ts           optional: defineModule({ render | tabs, hooks })
  dist/ui.js      generated by `npm run build`
```

- **Settings are declared in `module.json`.** The backend validates them (types, ranges, steps, options) and fills in the defaults; the panel draws the form by itself, and `backend.py` reads the same values with `ctx.settings.get()`. A module with settings and no `ui.ts` gets a tab with just its form.
- **`ui.ts`** adds anything else. The kit:
  - `ui.settingsForm(ctx)`;
  - controls with `get`/`set`/`setDisabled`;
  - sections, `confirm`, image grids;
  - expanded views (`ctx.openWindow`, `ui.windowButton`);
  - copying to the clipboard and a file or folder picker (`ctx.copy`, `ctx.pickPath`).

  It all works with the gamepad and touch.
- **The backend's `ctx`** gives modules what they would otherwise each write by hand: a notification the way Steam shows an achievement (`ctx.notify`), games and Steam starting and stopping (`ctx.events`), the progress of long operations, which can also ask the user a question (`ctx.task`, shown in the Tasks panel), Steam's folders and VDF files (`ctx.paths`, `ctx.vdf`) and its language (`ctx.steam_language`), unpacking archives safely (`ctx.archive`), downloading files, asking the user first when there is no checksum (`ctx.http`: a dialog over the panel, and a notification saying a question waits), and adding shortcuts and setting launch options in Steam on a user action (`ctx.steam`).
- **Isolation.** Every module is built and loaded on its own: an invalid `module.json`, a broken backend or a UI that fails only affects that module's tab. Slow backend methods run in their own thread.

To start, copy `modules/_example/` to `modules/<id>/`. Then:

```sh
cd frontend && npm run build && cd ..
python3 tools/check_module.py modules/<id>
./invasor-installation.sh --install
```

## What Invasor relies on in Steam
Everything Invasor takes from the Steam client is listed here. Each point is marked `STEAM TOUCHPOINT` in the code. After a Steam update, `tools/smoke.py` checks all of them in a minute. The details, how each one can fail and a comparison with Decky Loader: [docs/API-Steam.md](docs/API-Steam.md).

| Touchpoint | Used for | If Valve changes it |
|---|---|---|
| CEF DevTools on port 8080 (`.cef-enable-remote-debugging`) | injecting the overlay, as Decky does | nothing is injected; the log says it's waiting for CEF |
| CEF's `/json/version` browser id | telling a Steam (re)start, for modules' `steam_start` event (`ctx.events`, and the deprecated `ctx.on_steam_start`) | the event never runs; nothing else changes |
| Window URLs: `useragent=Valve%20Steam%20Gamepad`, title `QuickAccess_…` | which windows get the panel | adjustable without code via `targets` in `config.json` |
| `SharedJSContext` URL `/routes/library/app/<id>` | the selected game (and modules' `game_selected` event) | the selected game isn't shown and the event never runs; the running game still is (it comes from `/proc`) |
| Library tile under the cursor: `document.activeElement` (`role="link"`), its artwork URL `/assets/<appid>/` or `/customimages/<appid>`, its `aria-labelledby` name (matched against `shortcuts.vdf`) | the highlighted game | `highlighted` is null; running and selected are unaffected |
| `vgp_onbuttondown` events and their button numbers (Select is 13, Start 14) | gamepad navigation in the panel, and Select / Start for the Tasks / Settings panels | the panel opens (L3+R3) but isn't navigable with the pad (Tasks and Settings open with their header buttons); touch and F10 still work |
| `window.screenX` / `innerWidth` of Steam's windows | fitting the panel in Quick Access | it falls back to the learned width (348px) |
| CSS class `.gpfocus` | returning focus after physical-keyboard typing and after `ctx.copy`'s fallback | focus isn't returned; nothing else changes |
| `SteamClient` (optional), in this window or in `SharedJSContext` | the UI's `ctx.steam.safeCall` / the backend's `ctx.steam_call` for modules (e.g. Artwork's live refresh), and `User.StartRestart` for ⚙ Settings › Manage Invasor › Restart Steam | calls fail cleanly (503); modules fall back (Artwork: the new art shows after restarting Steam) |
| `SteamClient.Apps`: `AddShortcut`, `SetShortcutName` / `StartDir` / `LaunchOptions`, `RemoveShortcut`, `SetAppLaunchOptions`, `RegisterForAppDetails` | the backend's `ctx.steam`: adding and removing non-Steam shortcuts, reading and setting launch options | `ctx.steam.supports()` says `False` and the module shows its plan B; adding falls back to the `steam://addnonsteamgame` link |
| `SteamClient.URL.ExecuteSteamURL` (`steam://openurl/…`, `steam://addnonsteamgame/…`, which needs `/tmp/addnonsteamgamefile`) | `ctx.steam.open_url`, and adding a shortcut without `AddShortcut` | it opens the link with `gio` / `xdg-open` in the user's session; if that fails too, `Unavailable` (503) |
| Files on disk: `userdata/*/config/shortcuts.vdf`, `libraryfolders.vdf`, `loginusers.vdf`, `~/.steam/registry.vdf` | shortcut names, and checking what `ctx.steam` did; Steam's libraries and account folder (`ctx.paths`); Steam's language (`ctx.steam_language`) | names or folders are missing, `ctx.paths` raises `Unavailable`, the language is `"en"` |

## Tests

```sh
python3 -m unittest discover -s backend/tests -t backend   # backend, no Steam needed
(cd frontend && npm run typecheck && npm test)             # frontend types and value helpers
python3 tools/check_module.py modules/demo modules/_example # the module contract
python3 tools/smoke.py                                     # UI against the real Steam
```
The smoke test needs Steam's gamepad UI open (Game Mode, or Big Picture from the desktop). To test from the desktop client there's a **development mode**: `"dev_desktop": true` in `~/.config/invasor/config.json` and `systemctl --user restart invasor`. Invasor is then injected into the desktop window too. Keep it off in normal use; when it's switched off, the service removes any panel left in that window by itself. `tools/smoke.py` drives tabs, sub-tabs, sections, every Demo control, the expanded view, the keyboard, the confirm dialog and ⚙ Settings with a simulated controller. **Run it after every Steam update:** in a minute it tells you whether injection and navigation still work. It leaves everything as it found it.

## License
Invasor is free software under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later). The module template (`modules/_example/`) and the code examples in [docs/MODULES.md](docs/MODULES.md) are under the [MIT license](LICENSES/MIT.txt), so you can start your own module from them with any license you like.
