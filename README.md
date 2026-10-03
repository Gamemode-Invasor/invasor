# Invasor

**English** · [Español](README.es.md)

A panel of its own inside Steam's gamepad UI (**Game Mode and Big Picture**; not the
desktop client) with modules, in the spirit of Decky Loader, but built to **last
without depending on Steam**:

- It uses none of Steam's internal components, webpack or React. The UI lives in its
  own shadow DOM, with its own on-screen keyboard and its own controls.
- From the Steam client it only uses what can't be avoided: injection through the CEF
  debugger and the `vgp_*` gamepad events. Everything else has its own implementation.
- The backend uses only the **Python standard library**: no `pip`, no venv. It runs as
  a **user** service and never needs root.

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
With no arguments (or opened with a double click) it asks what to do in a dialog, if `zenity` is
installed; otherwise it shows its help.

- **Install / update:** everything is checked first (Python 3.9+, systemd user services, Steam, an up to
  date frontend build), then only what the service runs is copied (no tests, no `_example` template),
  swapped in at once, and the service is restarted and checked. Modules installed from a zip are kept.
- **Uninstall:** the overlay is taken out of Steam right away, with no Steam restart. Each module first
  undoes what it left outside its folder (its `uninstall()`). Everything in
  `~/.local/share/invasor` goes, modules installed from a zip included. The configuration and module
  settings in `~/.config/invasor` are kept unless `--purge` is given (the dialog asks).

After the first install, restart Steam once: the installer creates
`~/.steam/steam/.cef-enable-remote-debugging`, like Decky does. Uninstalling removes it only if the
installer created it and Decky isn't installed.

### Release for other machines
```sh
python3 tools/pack_release.py --out release/   # build, test and pack
```
It writes `invasor-<version>.tar.gz` (and its `.sha256`): already built, with the installer, the core and
the bundled modules. On the target Steam Deck / SteamOS no Node is needed: extract it (Ark, or
`tar -xzf`), then double click `invasor-installation.sh` or run `./invasor-installation.sh --install`.

### Updates
**⚙ Settings › Updates** has a *Check for updates* button and an *Install* one. Invasor asks GitHub
(`api.github.com`, the latest release of [Gamemode-Invasor/invasor](https://github.com/Gamemode-Invasor/invasor))
and, when its tag is a higher version than the installed one, the install button is enabled. Installing
downloads the release package, checks it against the `.sha256` published next to it and runs its installer.
The service restarts and the panel reloads by itself a few seconds later; Steam and any running game are
not restarted.

By default it also checks at startup and then daily, and Steam shows a notification once per new version.
That is the only connection Invasor makes to the Internet; switch it off with *Check for updates
automatically* (`update_check` in `config.json`). The checksum catches a corrupt download, not a
compromised GitHub account. Modules installed from a zip are not touched.

Log: `journalctl --user -u invasor -f`. Optional configuration:
`~/.config/invasor/config.json` (`open_combo`, `panel_side`, `targets`, `disabled_modules`, `dev_desktop`,
`update_check`; the first two and the last can also be changed from the **⚙ Settings** tab).

## Using it with a controller
The "I" handle is always there in the library. In Quick Access (···) it only shows while a
game is running, and the panel there leaves out modules that have nothing to do in-game
(`"no_qam": true`, e.g. Artwork).

| Button | Action |
|---|---|
| L3 + R3 | Open / close the panel (with a game running: press ··· first). Can be changed in ⚙ Settings |
| L1 / R1 | Previous / next tab: each module is a tab, ⚙ Settings is the last one |
| L2 / R2 | Previous / next sub-tab, if the module has any |
| D-pad ↑↓ | Move (folded content is skipped). With a lot of content, pages through it |
| D-pad ←→ | Change the control's value |
| A / B | Select / close (or cancel, depending on the control) |
| X | Fold / unfold the section (from its title or from inside it) |
| Y | The control's special value, e.g. back to its default |

The panel takes 40% of the screen width in the Library (in Quick Access, the visible column). Only its content scrolls; the shadows at the
top and bottom show there's more to see.

While the panel is open, Steam gets none of the panel's buttons (its own Steam and ···
buttons always go through). It can also be opened with the blue "I" tab, by touch, or
with F10.

Both regular controllers (evdev: DualSense, Xbox…) and Steam Deck-protocol ones are
read: a real Steam Deck, or handhelds such as the Legion Go virtualised by
InputPlumber. To see which buttons the service detects: `python3 tools/pad.py`.

**⚙ Settings tab** (always there, even with no modules): enable or disable modules,
the shortcut that opens the panel, the panel side, the accent colour (also of the "I" handle), and "About" (version, status and
detected controllers). Invasor's own UI is in English; each module chooses its own
language.

## Modules
Invasor ships with only `demo` (a showcase, also used by `tools/smoke.py`) and the `_example` template.
Real modules live in their own repositories and install as a zip from **⚙ Settings › Install module**:

- [invasor-artwork](../invasor-artwork): community artwork from steamgriddb.com for your games and shortcuts.
- [invasor-ducky](../invasor-ducky): lsfg-vk frame generation, set up per game.

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

- **Settings are declared in `module.json`.** The backend validates them (types, ranges, steps, options)
  and fills in the defaults; the panel draws the form by itself, and `backend.py` reads the same values with
  `ctx.settings.get()`. A module with settings and no `ui.ts` gets a tab with just its form.
- **`ui.ts`** adds anything else. The kit:
  - `ui.settingsForm(ctx)`;
  - controls with `get`/`set`/`setDisabled`;
  - sections, `confirm`, image grids;
  - expanded views (`ctx.openWindow`, `ui.windowButton`).

  It all works with the gamepad and touch.
- **Isolation.** Every module is built and loaded on its own: an invalid `module.json`, a broken backend or a
  UI that fails only affects that module's tab. Slow backend methods run in their own thread.

To start, copy `modules/_example/` to `modules/<id>/`. Then:

```sh
cd frontend && npm run build && cd ..
python3 tools/check_module.py modules/<id>
./invasor-installation.sh --install
```

## What Invasor relies on in Steam
Everything Invasor takes from the Steam client is listed here. Each point is marked `STEAM TOUCHPOINT` in
the code. After a Steam update, `tools/smoke.py` checks all of them in a minute.
The details, how each one can fail and a comparison with Decky Loader: [docs/API-Steam.md](docs/API-Steam.md).
Modules can also show a notification the way Steam shows an achievement (`ctx.notify`).

| Touchpoint | Used for | If Valve changes it |
|---|---|---|
| CEF DevTools on port 8080 (`.cef-enable-remote-debugging`) | injecting the overlay, as Decky does | nothing is injected; the log says it's waiting for CEF |
| CEF's `/json/version` browser id | telling a Steam (re)start, for modules' `ctx.on_steam_start` | `on_steam_start` never runs; nothing else changes |
| Window URLs: `useragent=Valve%20Steam%20Gamepad`, title `QuickAccess_…` | which windows get the panel | adjustable without code via `targets` in `config.json` |
| `SharedJSContext` URL `/routes/library/app/<id>` | the selected game | the selected game isn't shown; the running game still is (it comes from `/proc`) |
| Library tile under the cursor: `document.activeElement` (`role="link"`), its artwork URL `/assets/<appid>/` or `/customimages/<appid>`, its `aria-labelledby` name (matched against `shortcuts.vdf`) | the highlighted game | `highlighted` is null; running and selected are unaffected |
| `vgp_onbuttondown` events and their button numbers | gamepad navigation in the panel | the panel opens (L3+R3) but isn't navigable with the pad; touch and F10 still work |
| `window.screenX` / `innerWidth` of Steam's windows | fitting the panel in Quick Access | it falls back to the learned width (348px) |
| CSS class `.gpfocus` | returning focus after physical-keyboard typing | focus isn't returned; nothing else changes |
| `SteamClient` (optional), in this window or in `SharedJSContext` | `ctx.steam.safeCall` / `ctx.steam_call` for modules (e.g. Artwork's live refresh) | calls fail cleanly (503); modules fall back (Artwork: the new art shows after restarting Steam) |

## Tests

```sh
python3 -m unittest discover -s backend/tests -t backend   # backend, no Steam needed
(cd frontend && npm run typecheck && npm test)             # frontend types and value helpers
python3 tools/check_module.py modules/demo modules/_example # the module contract
python3 tools/smoke.py                                     # UI against the real Steam
```
The smoke test needs Steam's gamepad UI open (Game Mode, or Big Picture from the
desktop). To test from the desktop client there's a **development mode**:
`"dev_desktop": true` in `~/.config/invasor/config.json` and
`systemctl --user restart invasor`. Invasor is then injected into the desktop window
too. Keep it off in normal use; when it's switched off, the service removes any panel
left in that window by itself.
`tools/smoke.py` drives tabs, sub-tabs, sections, every Demo control, the expanded
view, the keyboard, the confirm dialog and ⚙ Settings with a simulated controller.
**Run it after every Steam update:** in a minute it tells you whether injection and
navigation still work. It leaves everything as it found it.

## License
Invasor is free software under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later).
The module template (`modules/_example/`) and the code examples in [docs/MODULES.md](docs/MODULES.md) are
under the [MIT license](LICENSES/MIT.txt), so you can start your own module from them with any license you like.
