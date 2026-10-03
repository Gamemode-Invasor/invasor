#!/usr/bin/env bash
# Installs, updates or uninstalls Invasor (core backend + frontend) as a *user* service,
# no root needed. Run it with no arguments for dialogs (zenity), or with a flag:
#   --install             install, or update an existing install
#   --uninstall           remove the service, the core and every installed module
#   --uninstall --purge   ... and the configuration (~/.config/invasor) too
set -euo pipefail

SELF="$(readlink -f "$0")"
SRC="$(dirname "$SELF")"
# Same locations as backend/invasor/config.py.
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/invasor"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/invasor"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="invasor.service"
PYTHON=/usr/bin/python3  # the one the service runs (write_unit)
# Set when the installer created Steam's CEF debugging flag (so uninstall may remove it).
CEF_MARKER="$DEST/.cef-flag-created"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--install | --uninstall [--purge] | --help]

  --install            Install Invasor in $DEST, or update it, and (re)start its
                       user service. Modules installed from a zip are kept.
  --uninstall          Stop and remove the service and everything in $DEST,
                       modules installed from a zip included (each module's
                       uninstall() runs first).
  --uninstall --purge  Also delete the configuration and module settings ($CONFIG).
  --help               Show this help.

With no arguments and zenity available, it asks what to do in a dialog.
From a source tree, build the frontend first: cd frontend && npm install && npm run build
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

version_of() {  # version_of <backend/invasor dir>
  sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$1/__init__.py" 2>/dev/null
}

steam_dir() {
  local d
  for d in "$HOME/.steam/steam" "$HOME/.local/share/Steam"; do
    [[ -d "$d" ]] && { echo "$d"; return 0; }
  done
  return 1
}

write_unit() {
  mkdir -p "$UNIT_DIR"
  cat >"$UNIT_DIR/$UNIT" <<EOF
[Unit]
Description=Invasor overlay loader for Steam
After=graphical-session.target

[Service]
Type=simple
ExecStart=$PYTHON -m invasor
WorkingDirectory=$DEST/backend
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3
# Modules get teardown() on stop; one that hangs must not hold up a restart.
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF
}

is_installed() {
  [[ -f "$UNIT_DIR/$UNIT" || -d "$DEST/backend" ]]
}

# ---------- install ----------

preflight() {
  [[ $EUID -ne 0 ]] || die "run it as your user, not as root (it installs a user service)"
  [[ -x $PYTHON ]] || die "$PYTHON not found"
  $PYTHON -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Python 3.9 or newer is needed ($($PYTHON -V 2>&1))"
  systemctl --user show-environment >/dev/null 2>&1 || die "systemd user services aren't available in this session"
  steam_dir >/dev/null || die "Steam not found (~/.steam/steam or ~/.local/share/Steam)"

  local bundle="$SRC/frontend/dist/invasor.js"
  [[ -f "$bundle" ]] || die "missing frontend/dist/invasor.js: run 'npm install && npm run build' in frontend/"
  # Only in a source tree: a release (tools/pack_release.py) ships the build without frontend/src.
  [[ ! -d "$SRC/frontend/src" || -z "$(find "$SRC/frontend/src" -type f -newer "$bundle" -print -quit)" ]] ||
    die "frontend/dist/invasor.js is older than frontend/src: run 'npm run build' in frontend/"
  # Each module's UI is built on its own (frontend/build.mjs): refuse to install a stale tree.
  local ui dir
  for ui in "$SRC"/modules/*/ui.ts; do
    [[ -e "$ui" ]] || continue
    dir="$(dirname "$ui")"
    case "$(basename "$dir")" in _* | .*) continue ;; esac
    [[ -f "$dir/dist/ui.js" ]] || die "missing $dir/dist/ui.js: run 'npm run build' in frontend/"
    [[ ! "$ui" -nt "$dir/dist/ui.js" ]] || die "$dir/dist/ui.js is older than ui.ts: run 'npm run build' in frontend/"
  done
}

stage() {  # stage <dir>: only what the service runs
  local stg="$1" d
  mkdir -p "$stg/backend" "$stg/modules" "$stg/frontend/dist"
  cp -r "$SRC/backend/invasor" "$stg/backend/invasor"
  for d in "$SRC"/modules/*/; do
    case "$(basename "$d")" in _* | .*) continue ;; esac  # templates/hidden: never loaded
    cp -r "$d" "$stg/modules/"
    rm -rf "$stg/modules/$(basename "$d")/tests"
  done
  find "$stg" \( -name __pycache__ -o -name node_modules \) -type d -prune -exec rm -rf {} +
  cp "$SRC/frontend/dist/invasor.js" "$stg/frontend/dist/invasor.js"
}

wait_healthy() {
  local port i
  port="$(cd "$DEST/backend" && $PYTHON -c 'from invasor import config; print(config.load()["api_port"])' 2>/dev/null)" || port=33801
  for ((i = 0; i < 20; i++)); do
    if systemctl --user is-active --quiet "$UNIT" &&
      $PYTHON -c 'import sys, urllib.request; urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=1)' "$port" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

do_install() {
  preflight
  local version old="" stg="$DEST/.staging" part flag
  version="$(version_of "$SRC/backend/invasor")"
  [[ -d "$DEST/backend/invasor" ]] && old="$(version_of "$DEST/backend/invasor")"

  # Built aside first, then swapped in: a failed copy never leaves a half install.
  mkdir -p "$DEST"
  rm -rf "$stg" "$DEST"/.old-*
  trap "rm -rf '$stg'" EXIT
  stage "$stg"
  for part in backend modules frontend; do
    [[ -e "$DEST/$part" ]] && mv "$DEST/$part" "$DEST/.old-$part"
    mv "$stg/$part" "$DEST/$part"
  done
  rm -rf "$DEST"/.old-* "$stg"

  write_unit
  systemctl --user daemon-reload
  systemctl --user enable --quiet "$UNIT"
  systemctl --user restart "$UNIT"

  # Same entry point as Decky: Steam exposes CEF DevTools on :8080 when this file exists.
  flag="$(steam_dir)/.cef-enable-remote-debugging"
  local restart_steam=""
  if [[ ! -e "$flag" ]]; then
    touch "$flag"
    touch "$CEF_MARKER"
    restart_steam=1
  fi

  if ! wait_healthy; then
    echo "The service didn't come up. Its last log lines:" >&2
    journalctl --user -u "$UNIT" -n 20 --no-pager >&2 || true
    exit 1
  fi
  if [[ -n $old ]]; then
    echo "Invasor updated: $old -> $version."
  else
    echo "Invasor $version installed."
  fi
  [[ -z $restart_steam ]] || echo "Restart Steam once so Invasor can reach it (CEF debugging was just enabled)."
}

# ---------- uninstall ----------

do_uninstall() {
  local purge="$1"
  [[ $EUID -ne 0 ]] || die "run it as your user, not as root"
  if ! is_installed; then
    echo "Invasor isn't installed."
  else
    systemctl --user disable --now --quiet "$UNIT" 2>/dev/null || true
    systemctl --user reset-failed "$UNIT" 2>/dev/null || true
    # With the service stopped nothing re-injects it: take the overlay out of Steam now.
    if [[ -d "$DEST/backend/invasor" ]]; then
      (cd "$DEST/backend" && timeout 20 $PYTHON -B -m invasor --remove-overlay) || true
      # Each module's uninstall() undoes what it left outside its folder (as ⚙ Settings
      # does); a failing one never stops the uninstall.
      (cd "$DEST/backend" && timeout 120 $PYTHON -B -m invasor --uninstall-modules ${purge:+--purge}) || true
    fi
    rm -f "$UNIT_DIR/$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true

    # Only if the installer created it and Decky (which uses it too) isn't there.
    local steam
    if [[ -f "$CEF_MARKER" && ! -d "$HOME/homebrew/services" ]] && steam="$(steam_dir)"; then
      rm -f "$steam/.cef-enable-remote-debugging"
      echo "Steam's CEF debugging flag removed (it was created by the installer)."
    fi
    rm -rf "$DEST"
    echo "Invasor uninstalled."
  fi
  if [[ -n $purge ]]; then
    rm -rf "$CONFIG"
    echo "Configuration deleted ($CONFIG)."
  elif [[ -d "$CONFIG" ]]; then
    echo "Configuration kept in $CONFIG (--uninstall --purge deletes it)."
  fi
}

# ---------- dialogs ----------

run_with_progress() {  # run_with_progress <title> <args...>: re-runs this script without dialogs
  local title="$1" log status
  shift
  log="$(mktemp)"
  bash "$SELF" "$@" >"$log" 2>&1 &
  local pid=$!
  (while kill -0 "$pid" 2>/dev/null; do sleep 0.3; done; echo 100) |
    zenity --progress --pulsate --auto-close --no-cancel --title="$title" --text="Working…" 2>/dev/null || true
  status=0
  wait "$pid" || status=$?
  if ((status == 0)); then
    zenity --info --no-markup --title="$title" --text="$(cat "$log")" 2>/dev/null || true
  else
    zenity --error --no-markup --title="$title" --text="$(tail -n 25 "$log")" 2>/dev/null || true
  fi
  rm -f "$log"
  return "$status"
}

gui() {
  local version installed choice args
  version="$(version_of "$SRC/backend/invasor")"
  if ! is_installed; then
    zenity --question --no-markup --title="Invasor" --ok-label="Install" --cancel-label="Cancel" \
      --text="Install Invasor $version in $DEST?" 2>/dev/null || return 0
    run_with_progress "Install Invasor" --install
    return
  fi
  installed="$(version_of "$DEST/backend/invasor")"
  choice="$(zenity --list --title="Invasor" --text="Invasor ${installed:-?} is installed. This folder has $version." \
    --column="Action" "Update / reinstall" "Uninstall" --height=220 2>/dev/null)" || return 0
  case "$choice" in
    "Update / reinstall") run_with_progress "Update Invasor" --install ;;
    "Uninstall")
      zenity --question --no-markup --title="Uninstall Invasor" --ok-label="Uninstall" --cancel-label="Cancel" \
        --text="Uninstall Invasor? This removes the service, the core and every installed module ($DEST)." \
        2>/dev/null || return 0
      args=(--uninstall)
      if [[ -d "$CONFIG" ]] && zenity --question --no-markup --title="Uninstall Invasor" \
        --ok-label="Delete" --cancel-label="Keep" \
        --text="Also delete your settings and module data ($CONFIG)?" 2>/dev/null; then
        args+=(--purge)
      fi
      run_with_progress "Uninstall Invasor" "${args[@]}"
      ;;
  esac
}

# ---------- main ----------

if (($# == 0)); then
  if command -v zenity >/dev/null && [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then
    gui
  else
    usage
  fi
  exit
fi

action="" purge=""
for arg in "$@"; do
  case "$arg" in
    --install | --uninstall)
      [[ -z $action ]] || { usage >&2; exit 2; }
      action="$arg"
      ;;
    --purge) purge=1 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $action ]] || { usage >&2; exit 2; }
[[ -z $purge || $action == --uninstall ]] || { usage >&2; exit 2; }

case "$action" in
  --install) do_install ;;
  --uninstall) do_uninstall "$purge" ;;
esac
