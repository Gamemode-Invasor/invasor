# Invasor and Steam: every touchpoint, how it can fail, and how Decky Loader compares

Steam has no public API for extending its gamepad UI. Everything Invasor takes from the Steam client is
listed here: how Invasor gets in (CEF), which windows it uses, what it reads from them, which Steam APIs it
calls, and what it reads from Steam outside its UI. For each one: where it lives in the code, what breaks if
Valve changes it, and what Invasor does then.

Every point below is marked `STEAM TOUCHPOINT` in the code. The README has the short version
("What Invasor relies on in Steam"); this is the long one.

**Design rule:** as few touchpoints as possible, and each one optional. When one breaks, only the feature
that needs it stops working: never the panel, never Steam.

## 1. Overview

```
 Steam client (Game Mode / Big Picture)
 └─ CEF (Chromium), DevTools on 127.0.0.1:8080        ← enabled by ~/.steam/steam/.cef-enable-remote-debugging
    ├─ main gamepad window   (useragent=Valve%20Steam%20Gamepad)   ← overlay injected, role "main"
    ├─ Quick Access window   (title QuickAccess_…)                  ← overlay injected, role "quickaccess"
    └─ SharedJSContext       (owns SteamClient, Steam's router)     ← never injected: only read and called
          ▲  CDP over WebSocket (Runtime.evaluate)
          │
 invasor.service (systemd --user, Python stdlib only)
    ├─ injector   → lists windows, injects frontend/dist/invasor.js + each module's dist/ui.js
    ├─ API server → 127.0.0.1:33801, token per run, called by the injected UI
    ├─ context    → running/selected game: /proc, SharedJSContext URL, Steam's files on disk
    └─ gamepad    → open/close combo (L3+R3 by default) from evdev / hidraw (read-only, non-exclusive)

 Injected UI: its own shadow DOM (#invasor-root), its own controls; no React, no webpack, no Steam components.
```

## 2. Touchpoints

### 2.1 Getting in: CEF remote debugging

| What | Where | Used for |
|---|---|---|
| `~/.steam/steam/.cef-enable-remote-debugging` (empty file) | `invasor-installation.sh`, and `backend/invasor/cef_flag.py` each time the service starts | Steam opens CEF DevTools on port 8080 at its next start. The same entry point Decky uses. |
| `http://127.0.0.1:8080/json` (window list) | `backend/invasor/cef.py` `list_targets` | Finding Steam's windows. Polled by `injector.Injector.run`, a loop that logs and retries on any error and never stops. |
| `http://127.0.0.1:8080/json/version` (the browser's WebSocket URL, `…/devtools/browser/<id>`) | `cef.browser_id`, polled by `injector._round` | Telling Steam instances apart: the id is new each time Steam starts, so a new one is a Steam (re)start and modules' `on_steam_start` callbacks run. A moment without an answer keeps the last id, so it never counts as a restart. |
| CDP over WebSocket: `Runtime.enable`, `Runtime.evaluate`, `Runtime.executionContextCreated` | `cef.CDPSession`, `injector._handle` / `_inject` | Evaluating the bundle in a window, and evaluating it again when the window reloads (a new default execution context). The WebSocket client is written with the stdlib only. |

How it can fail:
- **The file is removed** (a Steam reset or reinstall): the service puts it back the next time it starts (it logs
  `recreated Steam's CEF debugging flag`), but Steam only reads it when it starts, so restart Steam. It isn't
  checked while the service runs: if the file goes while Steam is closed, restart the service (or log in again)
  before starting Steam. Nothing is injected meanwhile and the log says `waiting for Steam CEF on 127.0.0.1:8080`.
- **The port changes:** same symptom. Fix: change `CEF_PORT` in `cef.py`.
- **Steam restarts or crashes:** the window list is empty for a while, then the windows come back with new
  ids and the next round injects them again. Nothing to do.
- **A window reloads** (Steam does it after some updates or when it changes mode): `executionContextCreated`
  fires and the bundle is evaluated again. The bundle can be injected twice safely: it tears down the
  previous copy first (`window.__invasor.destroy()` in `frontend/src/main.ts`).

### 2.2 Choosing windows

| What | Where | Used for |
|---|---|---|
| URL contains `useragent=Valve%20Steam%20Gamepad` | `config.py` `DEFAULTS["targets"]`, `injector._role` | The main window of the gamepad UI (role `main`). Only the useragent is matched: its `browserType` has been seen as both 3 and 4. |
| Title starts with `QuickAccess_` | same | The Quick Access (···) window that gamescope shows over a running game (role `quickaccess`). |
| Title prefix `notificationtoasts` excluded | same | Steam's toast window shares the main window's useragent. |
| Desktop client window (`Valve Steam Client`) | `injector._is_main_window`, `_clean` | Left alone, except in development mode (`dev_desktop`). When that's switched off, a panel left there is removed. |

Window titles are localised by Steam, so Invasor matches URL parameters and internal names, not visible titles.

How it can fail:
- **The useragent or title changes:** no panel in that window (or in Quick Access only). Fix without code:
  `targets` in `~/.config/invasor/config.json`. Run the service with `log_level: DEBUG` to see every window's
  title and URL, logged whenever the list changes.
- **Steam merges or splits windows** (e.g. Quick Access becomes part of the main window): the `quickaccess`
  role stops matching. The panel still works in the main window.

### 2.3 SharedJSContext

Steam's hidden page, which holds `SteamClient` and Steam's router. Invasor never injects UI into it.

| What | Where | Used for |
|---|---|---|
| Its URL in the `/json` list: `…/routes/library/app/<appid>` | `backend/invasor/context.py` | The **selected** game (the game page that's open), with no code inside Steam. |
| `SteamClient.<path>(…)` evaluated there | `backend/invasor/steam.py` `SteamBridge`, `injector.evaluate_shared` | `ctx.steam_call` / `steam_call_async` for module backends, and `core.steam_call` for UIs whose window has no `SteamClient`. |
| `SteamClient.User.StartRestart(false)` (Steam's own *Restart now* call; without the argument it fails with `requires 1 arguments`) | `backend/invasor/core.py` `restart_steam` | ⚙ Settings › Manage Invasor › Restart Steam. If this Steam has no such function the button says so (503); nothing is killed from outside. |
| `window.NotificationStore.OnNotification(id, 5, bytes)` | `steam.py` `SteamBridge.notify` (a fixed script) | `ctx.notify` / `core.notify`: a notification shown as Steam shows an achievement, with our title, text and icon. Type 5 and the message (`CAchievementNotification`) are Steam's public protobuf definitions (`steammessages_clientnotificationtypes.proto`), encoded by Invasor. Steam's achievement toast setting applies. |

The bridge only accepts dotted names (`Apps.SetCustomArtworkForApp`), never `constructor` or `prototype`.
Arguments must be JSON, at most 176 MB, and each call has a 15 s timeout by default. Connecting to a page (here
or any window) gives up after 5 s if it accepts the connection but never answers. A missing page, function or
answer, or a timeout, becomes `Unavailable` (HTTP 503), also for the blocking `ctx.steam_call` / `ctx.notify`
used from module threads: never an exception that reaches the service, and always what a module's plan B catches.

How it can fail:
- **The page hangs** (accepts the connection, never answers): `steam_call` / `notify` answer 503 after the
  connection or call timeout; the rest of the service keeps running.
- **The page is renamed** (it's matched by the exact title `SharedJSContext`): `steam_call` answers 503 and the
  selected game isn't known. The running game still is (2.5), and modules fall back (2.6).
- **The route format changes:** `selected` is null. Nothing else changes.
- **A `SteamClient` function is renamed, removed or changes its arguments:** `Unavailable`, or Steam's own
  error passed through. Each caller must have a plan B (`docs/MODULES.md`).
- **`NotificationStore` or its `OnNotification` is renamed:** `notify` is `Unavailable` (503) and no notification
  shows. If the achievement message changes, the notification may show without some text. Nothing else is affected.

### 2.4 Inside the injected windows

All in `frontend/src`. Plain DOM reads; Invasor never takes Steam's focus and never changes Steam's DOM outside
its own shadow root.

| What | Where | Used for | If Valve changes it |
|---|---|---|---|
| `window.SteamClient`, or `window.opener.SteamClient` | `steam.ts` (`steamAvailable`, `hasApi`, `safeCall`) | `ctx.steam.safeCall` for module UIs. | `safeCall` rejects; the status line says "SteamClient unavailable"; the backend bridge (2.3) is the alternative. |
| `vgp_onbuttondown` / `vgp_onbuttonup` events, `detail.button` numbers | `ui/gamepad-nav.ts` | Controller navigation inside the panel. While the panel is open these events are stopped in the capture phase, so Steam's UI underneath doesn't move. | The panel still opens (the combo, read by the backend) but can't be driven with the pad; touch and F10 still work. Unknown numbers reach controls as `BTN_<n>` (`invasor:button` events). |
| CSS class `.gpfocus` | `ui/controls.ts` | Giving focus back to Steam's selected element after typing on a physical keyboard. | Focus isn't given back; nothing else changes. |
| `document.activeElement` with `role="link"`, its `<img>` URL `/assets/<appid>/…` or `/customimages/<appid>…`, its `aria-labelledby` name | `library.ts` | The **highlighted** game (the library tile under the cursor). The name is matched against `shortcuts.vdf` for shortcuts without custom art. | `highlighted` is null; running and selected are unaffected. |
| `window.screenX`, `innerWidth`, `screen.width` | `ui/overlay.ts` `fitToScreen` | Sizing the panel to the visible part of the Quick Access window (wider than what gamescope shows; it reports x=0 while sliding in). | The learned width (`qam_visible_w`, 348 px measured on a Legion Go) is used. |
| `document.hidden`, `document.hasFocus()`, `blur` / `visibilitychange` | `ui/overlay.ts`, `main.ts` `__invasor.state()` | Closing a panel whose window left the screen, and deciding which window the open/close combo opens in (`backend/invasor/combo.py`). Quick Access shows the "I" only while a game runs (`core.game`), and reports to the backend (`core.set_qam_shown`) whether it shows it (`available` and not `document.hidden`): the library window hides its own "I" meanwhile, so two don't show with the panel on the left. | If focus is never reported, the combo ignores the window instead of opening an invisible panel. Touch and F10 still work. If `document.hidden` never turns true in Quick Access, the library's "I" stays hidden while a game runs; the combo and F10 still open the panel. |

### 2.5 Steam outside its UI

Read from disk and `/proc`, as the same user, without root. These are not part of Steam's UI, so they change
far less often.

| What | Where | Used for |
|---|---|---|
| `SteamAppId` / `SteamGameId` in `/proc/<pid>/environ` | `context.py` `_running_appid` | The **running** game. For a non-Steam shortcut the game id is 64-bit: `(appid << 32) \| 0x02000000`. |
| `steamapps/libraryfolders.vdf`, `steamapps/appmanifest_<appid>.acf` | `context.py` | Names of installed Steam games. |
| `userdata/*/config/shortcuts.vdf` (binary VDF) | `vdf.py`, `context.py` | Names and executables of non-Steam shortcuts; re-read only when the files change. |
| evdev `/dev/input/event*`; hidraw with Valve's vendor id `28de` | `gamepad.py` | The open/close combo. Read-only and non-exclusive: Steam and games still get every input. The hidraw layout is Valve's hardware protocol (as in the kernel's `hid-steam.c`), not Steam client code. |

How it can fail:
- **Steam stops putting `SteamAppId`/`SteamGameId` in the game's environment:** `running` is null; selected
  and highlighted still work.
- **The binary VDF format or the shortcut id formula changes:** shortcut names are missing (ids still show).
- **Several Steam accounts in `userdata/`:** shortcut names are read from all of them. Artwork has to pick one
  account (the only one, or the most recent login in `loginusers.vdf`) and says so when it can't tell.
- **Steam grabs a hidraw device exclusively, or InputPlumber changes the virtual device:** the combo doesn't
  fire. The "I" handle and F10 still open the panel.

### 2.6 What modules add

| Module | Touchpoint | Plan B |
|---|---|---|
| Artwork | Writes Steam's own custom art files in `userdata/<account>/config/grid/`. | The files are the source of truth. |
| Artwork | `SteamClient.Apps.SetCustomArtworkForApp` / `ClearCustomArtworkForApp`, through `ctx.steam_call`, so the new art shows at once. | If the call fails, the art shows after restarting Steam. |
| Patito, Pescao | The game's Steam id (or shortcut id) in lsfg-vk's / MAKO's `active_in`. lsfg-vk and MAKO match it themselves when the game runs. | None needed from Steam. |
| Pescao | MAKO only runs in games started with `mako-launch %command%` in their launch options. | Pescao shows the line and the user pastes it. Pescao never writes launch options. |
| Deckico | Reads custom art in `userdata/<account>/config/grid/` and Steam's icons in `appcache/librarycache/<appid>/`; writes a `.directory` file in each `steamapps/compatdata/<appid>/` and `steamapps/shadercache/<appid>/` of every library. Runs on `on_steam_start` (2.1). | Folders without an image are left alone; a file manager simply shows a plain folder. |
| Noty | `ctx.notify` (2.3) for notifications sent by local scripts. | If Steam can't show them, the script's request gets a 503. |
| GE-RR | Installs GE-Proton as `compatibilitytools.d/GE-Proton` with its own `compatibilitytool.vdf` (Steam's documented format for custom compatibility tools). Checks on `on_steam_start`; `ctx.notify` when a download starts and ends. | Steam reads the folder only when it starts: the module asks for a restart. Notifications are optional (the log has the same). |

### 2.7 The local API

The injected UI talks to the service over `http://127.0.0.1:33801` (`backend/invasor/server.py`). It listens
on loopback only. Every call needs the per-run token that's injected with the bundle (`X-Invasor-Token`,
compared in constant time). If the port is taken, the service stops with an error in the log and systemd
restarts it every 3 s, so no panel is injected until the port is free; it can be changed with `api_port` in
`config.json`.

**Security note**, the same as for Decky: while CEF remote debugging is on, any local process of any user can
drive Steam's pages through port 8080. That's a property of Steam's debugging port, not of Invasor.

## 3. Failure catalogue

| Steam change | What you see | What Invasor does | What to check |
|---|---|---|---|
| Debugging file gone / CEF port moved | No "I" anywhere | Keeps polling, logs "waiting for Steam CEF"; puts the file back when the service next starts | Restart the service, then Steam; `CEF_PORT` |
| Steam restarts or crashes | Panel gone for a moment | Re-injects on the next round | Nothing |
| `/json/version` without a browser id | Modules' `on_steam_start` never runs (e.g. Deckico) | Nothing else changes | `cef.browser_id`; the smoke test's "Steam instance id" line |
| A window reloads | Panel gone for a moment | Re-injects on `executionContextCreated` | Nothing |
| Window useragent/title renamed | No "I" in that window | Nothing matches that window | `log_level: DEBUG`, then `targets` in `config.json` |
| `SharedJSContext` renamed | No "Selected" game; module calls 503 | Plan B in each caller | Window titles in the debug log; `injector.evaluate_shared` |
| `SharedJSContext` hangs | Module calls and notifications fail after a few seconds | 503 after the 5 s connect / 15 s call timeout; the panel and the rest keep working | `cef.CONNECT_TIMEOUT`; Steam itself (restart it) |
| Route URL format changed | No "Selected" game | Running and highlighted still work | `context.py` route parsing |
| `SteamClient` API renamed or re-signed | A module's live action fails, with its fallback; ⚙ Settings › Manage Invasor › Restart Steam says it's unavailable (`User.StartRestart`) | `Unavailable` (503), one log line per call | The module's plan B; the new name in Steam's JS |
| `NotificationStore` changed | No notifications (e.g. from Noty) | `notify` answers 503 | `window.NotificationStore` in `SharedJSContext`; `SteamBridge.notify` |
| `vgp_` events or button numbers changed | The pad doesn't move in the panel | Touch, F10 and the backend combo still work | `detail.button` of Steam's `vgp_onbuttondown` events (button numbers in `gamepad-nav.ts`) |
| Library tile DOM changed | No "Highlighted" game | `highlighted` is null | `library.ts` (`appidFromArt` has unit tests) |
| `.gpfocus` renamed | Focus not returned after keyboard typing | Nothing else | `controls.ts` |
| Quick Access geometry changed | Panel too wide or narrow in ··· | Learned width (348 px) | `report` lines (`refit`) in the log; `fitToScreen` |
| Focus/visibility semantics changed | The combo does nothing, or the panel closes by itself | Never opens a panel in a window that's off screen | `__invasor.state()` in each window; `combo.py` |
| `SteamAppId` no longer in game env | No "Playing" game; no "I" in Quick Access | Selected and highlighted still work | `_running_appid` in `context.py` |
| Binary VDF / shortcut id changed | Shortcuts show ids, not names | Names are optional | `vdf.py` (unit tests) |
| Exclusive input grab | Combo doesn't open the panel | "I" handle and F10 still work | `watching …` lines in the log (`gamepad.py`) |
| API port taken | No "I" anywhere | Service exits; systemd restarts it every 3 s | The error in the log; `api_port` in `config.json` |

**After every Steam update:** run `python3 tools/smoke.py` with the gamepad UI open (or with `dev_desktop` on).
In about a minute it checks injection, the panel, tabs, every control, the keyboard, dialogs and module
installs. Then `journalctl --user -u invasor -f` for anything new.

## 4. Comparison with Decky Loader

A light comparison, from Decky Loader's own source as of October 2026
([github.com/SteamDeckHomebrew/decky-loader](https://github.com/SteamDeckHomebrew/decky-loader), files
`backend/decky_loader/injector.py`, `frontend/src/tabs-hook.tsx`, `frontend/src/router-hook.tsx` and
`backend/decky_loader/plugin/sandboxed_plugin.py` there).

| | Decky Loader | Invasor |
|---|---|---|
| Entry point | `.cef-enable-remote-debugging`, CEF on `localhost:8080` | The same |
| Where its code runs | Inside **SharedJSContext** (also tried under the names "Steam Shared Context presented by Valve™", "Steam", "SP") | Its own shadow DOM in the gamepad windows (main and Quick Access); SharedJSContext only for reads and `SteamClient` calls |
| How it finds Steam's code | Searches Steam's webpack modules by their (minified) exports: `findModuleByExport`, e.g. code containing `QuickAccessMenuBrowserView` or the string `router-backstack` | Doesn't. No webpack, no Steam internals in the UI |
| How it changes Steam's UI | Patches React components and the fiber tree (`afterPatch`, `findInReactTree`): adds routes to Steam's router and a real tab to Steam's Quick Access menu | Doesn't. A floating "I" handle and its own panel over Steam's windows |
| UI components | Steam's own (native look, shared focus and navigation) | Its own kit (`ui.*`), with its own controller navigation from `vgp_` events |
| Opening it | Steam's Quick Access menu (···) | A controller combo (L3+R3 by default, or L4+R4 / L5+R5; read by the backend from the pad itself), the "I" handle, or F10 |
| Notifications | Its own toast component: `NotificationStore.ProcessNotification` plus a patch of Steam's toast renderer (`findModuleExport` + `replacePatch`, `frontend/src/toaster.tsx`) | Steam's own achievement toast, fed through `NotificationStore.OnNotification` with Steam's public message format: no patch, but the look is Steam's |
| Steam's windows | Closes some blank CEF tabs it considers stray (`CLOSEABLE_URLS`; its code warns closing others "really likes to crash Steam") | Never closes or reloads a Steam window |
| Privileges | Loader service runs as root; plugin backends drop to the user unless they declare the `root` flag | `systemd --user` service; no root at any point |
| Backend dependencies | Its own bundled Python packages | Python standard library only |
| Typical break after a Steam update | Webpack/React patches miss after Valve rebuilds its UI: the Quick Access tab or plugin pages disappear until Decky (and sometimes each plugin) is updated | Only the feature on the changed touchpoint degrades (see the catalogue); the panel keeps working |
| What it can do that the other can't | Deep UI integration: change Steam's own pages (e.g. a game's page), use Steam's components and focus | Survive Steam UI rebuilds without updates; modules that never touch Steam's UI code |

**In short:** both get in the same way. Decky lives inside Steam's UI: it patches React and finds code by
webpack exports, which looks native and integrates deeply, but depends on Steam's internal build. Invasor
lives next to Steam's UI: it reads a handful of stable signals (window URLs, a few DOM attributes, Steam's
files, `/proc`) and draws its own panel. It integrates less, and has far fewer ways to break.
