#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
plugin_id=ibnabeeali.codex-multiplexer
config_root=${XDG_CONFIG_HOME:-"$HOME/.config"}
plugins_dir="$config_root/omarchy/plugins"
target="$plugins_dir/$plugin_id"
stage="$plugins_dir/.$plugin_id.install.$$"
marker="$target/.managed-by-codex-multiplexer"

cleanup() {
  if [ -d "$stage" ]; then
    find "$stage" -type f -delete 2>/dev/null || true
    find "$stage" -depth -type d -empty -delete 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

if ! command -v omarchy >/dev/null 2>&1; then
  printf 'error: Omarchy is not installed or the omarchy command is not on PATH.\n' >&2
  exit 1
fi
if ! command -v omarchy-shell >/dev/null 2>&1; then
  printf 'error: the running Omarchy shell command is not on PATH.\n' >&2
  exit 1
fi
if ! command -v codex-mux >/dev/null 2>&1; then
  printf 'error: install Codex Multiplexer first with ./install.sh.\n' >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  printf 'error: jq is required to confirm that Omarchy discovered the plugin.\n' >&2
  exit 1
fi

if [ -e "$target" ] && [ ! -f "$marker" ]; then
  printf 'error: %s already exists and is not managed by this installer.\n' "$target" >&2
  printf 'Use `omarchy plugin update %s` for a git-installed copy, or remove it explicitly first.\n' "$plugin_id" >&2
  exit 1
fi

install -d -m 755 "$plugins_dir" "$stage/omarchy"
install -m 644 "$repo_dir/manifest.json" "$stage/manifest.json"
install -m 644 "$repo_dir/omarchy/Panel.qml" "$stage/omarchy/Panel.qml"
: >"$stage/.managed-by-codex-multiplexer"

omarchy plugin validate "$stage"

if [ -d "$target" ]; then
  install -d -m 755 "$target/omarchy"
  install -m 644 "$stage/manifest.json" "$target/.manifest.json.new"
  install -m 644 "$stage/omarchy/Panel.qml" "$target/omarchy/.Panel.qml.new"
  mv "$target/.manifest.json.new" "$target/manifest.json"
  mv "$target/omarchy/.Panel.qml.new" "$target/omarchy/Panel.qml"
else
  mv "$stage" "$target"
fi

omarchy-shell shell rescanPlugins >/dev/null

attempt=0
while [ "$attempt" -lt 40 ]; do
  if omarchy plugin list --json 2>/dev/null | jq -e --arg id "$plugin_id" \
    'any(.[]; .id == $id)' >/dev/null 2>&1; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 0.05
done

if [ "$attempt" -ge 40 ]; then
  printf 'error: Omarchy did not discover %s after rescanning.\n' "$plugin_id" >&2
  exit 1
fi

omarchy plugin enable "$plugin_id" --section right
printf 'Installed and live-reloaded the Codex Accounts widget; no shell restart is required.\n'
