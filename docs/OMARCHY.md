# Omarchy integration

Codex Multiplexer includes a native Omarchy bar widget that shows every
configured account as a compact card. It uses Omarchy's current theme tokens,
loads cached data immediately, and refreshes account limits in the background.

## Requirements

- Omarchy with the Quickshell plugin commands (`omarchy plugin` and
  `omarchy-shell`).
- Codex Multiplexer installed so `codex-mux` is available to the shell.
- `jq` (included by Omarchy and used by the local installer).
- At least one account configured with `codex-as add`.

## Install from this checkout

Install or update the CLI first, then install the widget:

```bash
./install.sh
./install-omarchy-plugin.sh
```

The widget installer copies only the manifest and QML panel into
`~/.config/omarchy/plugins/ibnabeeali.codex-multiplexer`, validates the staged
plugin, asks the running shell to rescan, and enables it in the right bar
section. It does not restart Omarchy. Re-running the installer updates the
managed copy, and Omarchy's plugin watcher reloads the changed QML live.

The installer refuses to overwrite a plugin installed by another method. If
the target is a Git-managed plugin, update it through Omarchy instead.

## Install from the public repository

After installing the CLI, Omarchy can clone this repository directly because
its root contains the plugin manifest:

```bash
omarchy plugin add https://github.com/IbnAbeeAli/codex-multiplexer.git --enable
```

Omarchy validates, rescans, and enables added plugins over shell IPC, so a
desktop restart is not required. Review third-party plugin code before enabling
it; Omarchy plugins run as the current user inside the long-lived shell.

## Interaction

- Left-click the bar icon to open or close the account overview.
- Middle-click to refresh account data and allow Codex to refresh login tokens.
- Right-click to open a terminal through `codex-lb`.
- Click an account card to open its focused detail view.
- Use `j`/`k` to select cards, Enter to open one, `h` or Escape to go back, and
  `r` to refresh.

Four complete account cards fit in the panel without scrolling. Additional
accounts use the same compact card layout in a vertical scroll area. Each card
shows only the account name, email, plan, status, remaining short-window and
weekly quota, reset countdowns, and default/recommended markers. Token totals,
model breakdowns, and daily charts are intentionally absent from the overview.

## Refresh and cache behavior

The panel starts two non-blocking reads:

1. `codex-mux omarchy --cached` returns the previous display record
   immediately when one exists.
2. `codex-mux omarchy --no-refresh` queries every account concurrently and
   replaces the cards when the live results arrive.

The panel refreshes every five minutes by default and whenever it opens with
data older than 30 seconds. The refresh button and `r` request a full refresh.
Change the background interval without restarting the shell:

```bash
omarchy bar set ibnabeeali.codex-multiplexer refreshIntervalSec 600 --json
```

The private cache is stored at
`$CODEX_MULTIPLEXER_HOME/omarchy-accounts.json`, or under the default
multiplexer data root. It is written atomically with mode `0600`. It contains
account display names, emails, plans, status, quota percentages, and reset
times, but no access or refresh tokens.

## Data boundary

The QML panel never opens an account home or `auth.json`. It invokes the
installed `codex-mux omarchy` command, which reuses the multiplexer registry,
parallel App Server probes, quota normalization, and recommendation logic.
Authentication and token refresh remain owned by Codex.

## Troubleshooting

If the widget reports that `codex-mux` is unavailable, reinstall the CLI and
make sure `~/.local/bin` is in the graphical session's `PATH`:

```bash
./install.sh
```

Inspect the display record independently:

```bash
codex-mux omarchy --no-refresh | jq
```

Force discovery and open the panel without restarting Omarchy:

```bash
omarchy-shell shell rescanPlugins
omarchy plugin enable ibnabeeali.codex-multiplexer --section right
omarchy-shell ibnabeeali.codex-multiplexer open
```
