# Codex Multiplexer

Codex Multiplexer runs multiple persistent Codex accounts on one machine while
keeping one shared local chat history. Select an account when starting Codex,
inspect every account's usage limits with one command, and resume the same local
thread under a different account.

```bash
codex-as acc1
codex-as acc2 resume SESSION_ID
codex-smi
```

Each account has its own authentication cache. Session rollouts, the thread
index, shell snapshots, and writer locks are shared deliberately. The tool never
parses, copies, or prints tokens from `auth.json`.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Five-minute setup](#five-minute-setup)
- [Installation and updates](#installation-and-updates)
- [Command reference](#command-reference)
- [How shared sessions work](#how-shared-sessions-work)
- [Files and directories](#files-and-directories)
- [Remote and headless hosts](#remote-and-headless-hosts)
- [Move chats to another machine](#move-chats-to-another-machine)
- [Import an existing Omarchy setup](#import-an-existing-omarchy-setup)
- [Configuration](#configuration)
- [Security and concurrency](#security-and-concurrency)
- [Troubleshooting](#troubleshooting)
- [Development and verification](#development-and-verification)

## Features

- Any number of named Codex account slots.
- Persistent ChatGPT login per account.
- Optional expected-email pinning to catch the wrong browser login.
- Exact forwarding of Codex CLI arguments.
- Shared session picker and cross-account `resume SESSION_ID`.
- Live email, plan, quota windows, remaining percentage, reset time, and earned
  reset-credit reporting.
- Quota-aware account selection across all configured accounts with `codex-lb`.
- Headless device-code authentication.
- Relocatable paths for clean installations.
- Codex-driven rollout reindexing after moving data.
- Strict, versioned JSON registry schema.
- No third-party Python dependencies.

## Requirements

- Python 3.10 or newer.
- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) available on `PATH`
  (check with `codex --version`).
- A writable, persistent user home directory.
- Linux or macOS.

| Target | Support |
|---|---|
| Linux workstation or SSH host | Supported |
| macOS workstation or SSH host | Supported |
| Windows through WSL | Supported inside WSL |
| Native Windows | Not currently supported |
| Persistent Linux container | Supported with a persistent data volume |

Native Windows is excluded because the implementation uses POSIX file locks and
symbolic links. For WSL, keep the project, Codex CLI, and multiplexer data in the
same WSL distribution.

## Five-minute setup

Install the commands and make sure the install directory is on `PATH`:

```bash
git clone https://github.com/IbnAbeeAli/codex-multiplexer.git
cd codex-multiplexer
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

Choose a short, stable name for each account and log in one account at a time.
Using `--expect` is recommended when several ChatGPT accounts are available in
the same browser because it rejects and clears a login for the wrong email.
Replace the example addresses with the actual address for each account:

```bash
codex-as add personal --expect you@example.com
codex-as add work --expect you@company.example
```

Each command starts Codex's normal browser login and waits until the selected
identity has been verified. If `--expect` is omitted, the verified email reported
by Codex is recorded automatically after the first successful login.

For a remote or headless machine, add `--device-auth` and complete the displayed
device-code flow instead:

```bash
codex-as add personal --expect you@example.com --device-auth
codex-as add work --expect you@company.example --device-auth
```

Verify the setup:

```bash
codex-as accounts
codex-as doctor
codex-smi --no-refresh
```

`accounts` confirms the configured slots, `doctor` checks the installation and
filesystem layout, and `codex-smi` asks Codex for live identity and quota data.

Start Codex with the selected account:

```bash
codex-as personal
codex-as work
```

There is no global “active account”: the selector immediately after `codex-as`
chooses the account for that invocation. Open the shared resume picker, or resume
a known thread under another account:

```bash
codex-as personal resume
codex-as work resume 0199cafe-0000-7000-8000-000000000000
```

`--yolo` is a Codex option that disables approvals and sandboxing. It is not
required by the multiplexer and should only be used in an appropriately isolated
environment.

## Installation and updates

The default installation uses:

| Item | Default path |
|---|---|
| Commands | `~/.local/bin` |
| Python implementation | `~/.local/libexec/codex-multiplexer` |
| Installed documentation | `~/.local/share/doc/codex-multiplexer` |
| Mutable account/chat data | `$XDG_DATA_HOME/codex-multiplexer`, or `~/.local/share/codex-multiplexer` |

Add `~/.local/bin` to the login shell's `PATH` so the commands remain available
after reconnecting or restarting. Put the same `export PATH=...` line in the
appropriate shell profile, such as `~/.bashrc` or `~/.zshrc`.

Install into a different prefix:

```bash
CODEX_MULTIPLEXER_INSTALL_ROOT=/opt/codex-multiplexer ./install.sh
```

Installed wrappers resolve the Python implementation relative to their own
prefix, so custom installations do not depend on `~/.local`.

To update an existing installation, update the checkout and run the installer
again so the installed scripts and documentation are replaced:

```bash
git pull --ff-only
./install.sh
```

For a custom prefix, set `CODEX_MULTIPLEXER_INSTALL_ROOT` again when updating.

## Command reference

### Account management

| Command | Purpose |
|---|---|
| `codex-as add NAME [--expect EMAIL] [--device-auth]` | Create or reuse an account slot and log in |
| `codex-as login NAME [--device-auth]` | Re-authenticate an existing slot |
| `codex-as logout NAME` | Clear that slot's login while retaining the slot |
| `codex-as accounts` | List names, expected emails, homes, and the default (`*`) |
| `codex-as list` | Alias for `codex-as accounts` |
| `codex-as default NAME` | Choose the account used by default for maintenance |
| `codex-as doctor` | Validate installation, state, permissions, and rollouts |
| `codex-as reindex [NAME] [--timeout SECONDS]` | Ask Codex to rescan shared session rollouts |
| `codex-as import-omarchy [--registry PATH]` | Register an existing Omarchy account layout |
| `codex-as --version` | Print the multiplexer version |

Use `codex-as --help` for the top-level summary and `--help` with `add`, `login`,
`reindex`, or `import-omarchy` for their argument details.

Names are 1–64 characters, start with a letter or number, and may contain
letters, numbers, `.`, `_`, and `-`. Avoid the management words `add`, `login`,
`logout`, `accounts`, `list`, `default`, `doctor`, `reindex`, `import-omarchy`,
and `help`, because those are interpreted as commands instead of selectors.

The first account slot created becomes the default, even if its initial login is
interrupted. A failed `add` therefore does not require another slot: retry with
`codex-as login NAME`, optionally adding `--device-auth`. `logout` removes the
selected slot's Codex credentials but does not delete its registry entry or
shared chats. This release has no account-removal command; leaving a logged-out
slot in the registry is safe. To intentionally assign a slot to a different
identity, log it out and run `codex-as add NAME --expect NEW_EMAIL` with the same
name.

The default is used when `codex-as reindex` is run without an account argument.
It does not create a persistent active account, and it does not limit the account
pool considered by `codex-lb`.

### Running Codex

Everything after the account selector is passed to Codex unchanged:

```bash
codex-as acc1
codex-as acc1 resume
codex-as acc1 --yolo
codex-as acc1 exec "Run the tests"
codex-as acc2 --yolo resume SESSION_ID
```

Account selectors are case-insensitive and can also match a configured alias or
expected email.

The installed `codex-mux` command is a unified alternative to the dedicated
wrappers:

```bash
codex-mux as acc1
codex-mux smi --details
codex-mux lb resume SESSION_ID
```

### Automatic account selection

`codex-lb` probes every configured account and launches Codex with the available
account that has the most remaining quota. It ignores accounts that are logged
out, unavailable, exhausted, explicitly excluded, or already locked by another
`codex-lb` process:

```bash
codex-lb
codex-lb resume SESSION_ID
CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS=work codex-lb
codex-lb --help
codex-lb --version
```

Set `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS` to a comma-separated list of account
names, aliases, or expected emails to omit from a particular launch.

Selection uses the most constrained reported quota window and does not refresh
login tokens while probing. `codex-lb` is intended for the per-slot ChatGPT
logins managed by this project; it removes inherited `OPENAI_API_KEY`,
`CODEX_API_KEY`, and `CODEX_ACCESS_TOKEN` values before launching Codex.

### Usage and limits

```bash
codex-smi
codex-smi acc1 acc2
codex-smi --no-refresh
codex-smi --info
codex-smi --details
codex-smi --usage --json
codex-smi --json
codex-smi --timeout 30
codex-smi --version
```

`codex-smi` queries each account concurrently through Codex App Server's
structured account and rate-limit methods. Its default table is a compact,
one-row-per-account summary of the primary Codex quota. Use `--details` to show
the plan and every reported limit window.

Use `codex-smi --info` for a short offline reminder of how to add an account,
start a new session, and resume a session by picker or chat ID.

- `REMAINING USAGE` shows `100 - usedPercent` as a compact bar and percentage
  for the server-reported quota window; the filled portion is the quota still available.
- `RESETS` is the available earned reset-credit count when supplied by the
  backend.
- `--usage` additionally requests token-activity summaries.
- `--no-refresh` avoids requesting a ChatGPT token refresh during inspection.
- `--json` returns the structured response for scripts.
- `--timeout SECONDS` changes the positive per-account App Server timeout from
  its 15-second default.

Token-activity data requested by `--usage` is included in JSON output; the
compact and detailed tables remain quota-oriented. When combined with
`--details`, `--json` takes precedence. Account arguments accept the same names,
aliases, and expected emails as `codex-as`.

Codex does not expose an exact universal “messages remaining” count because
consumption depends on the model, task size, speed mode, and tool activity.

## How shared sessions work

```text
codex-as acc1 ──> CODEX_HOME=accounts/acc1 ──> private auth.json
codex-as acc2 ──> CODEX_HOME=accounts/acc2 ──> private auth.json
                         │
                         ├── CODEX_SQLITE_HOME ──> shared/state_*.sqlite
                         ├── sessions ───────────> shared/sessions
                         ├── shell snapshots ────> shared/shell_snapshots
                         └── writer locks ───────> shared/thread-writer-locks
```

Every account gets an isolated `CODEX_HOME`, which is the credential boundary.
The launcher sets one shared `CODEX_SQLITE_HOME` and also passes the equivalent
`sqlite_home` override. Managed account homes use relative links to the shared
runtime directories.

The multiplexer also forces Codex's file-based credential store so each login is
kept in that account home's `auth.json`. Other files under `CODEX_HOME`, such as
`config.toml`, profiles, logs, and skills, remain per-account unless you manage
them separately. Only the SQLite state and the runtime directories shown above
are shared by this project.

The shared SQLite index records thread IDs and rollout paths. Because every
account sees the same index and rollouts, both the session picker and explicit
`resume SESSION_ID` can continue the same chat under another account.

## Files and directories

A clean managed installation without an `XDG_DATA_HOME` override uses this
layout:

```text
~/.local/share/codex-multiplexer/
├── registry.json                 # non-token account metadata, mode 0600
├── registry.lock
├── account-locks/
├── accounts/
│   ├── acc1/
│   │   ├── auth.json             # sensitive, account-private
│   │   └── sessions -> ../../shared/sessions
│   └── acc2/
│       ├── auth.json             # sensitive, account-private
│       └── sessions -> ../../shared/sessions
└── shared/
    ├── state_*.sqlite
    ├── sessions/
    ├── shell_snapshots/
    └── thread-writer-locks/
```

The registry contract is documented in
[`schema/registry.schema.json`](schema/registry.schema.json). Fresh account and
shared-state paths are relative to the data root, making the complete tree
relocatable.

## Remote and headless hosts

Copy or clone this repository onto the remote host, then connect with an
interactive SSH terminal:

```bash
ssh -t user@remote-host
cd /path/to/codex-multiplexer
./install.sh
export PATH="$HOME/.local/bin:$PATH"
codex-as add acc1 --device-auth
codex-as add acc2 --device-auth
codex-as doctor
codex-smi --no-refresh
```

Device-code login must be enabled in the ChatGPT account's security settings or
by the workspace administrator. If it is unavailable, forward Codex's localhost
callback and use the normal browser flow:

```bash
ssh -L 1455:localhost:1455 user@remote-host
codex-as add acc1
```

Repeat login separately for each account. Codex must be on the remote login
shell's `PATH`. Do not expose Codex App Server or the OAuth callback directly on
a public or shared network; use SSH, a VPN, or an authenticated mesh network.

Official references:

- [Codex authentication and headless login](https://learn.chatgpt.com/docs/auth)
- [Codex remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [Codex environment variables](https://learn.chatgpt.com/docs/config-file/environment-variables)

## Move chats to another machine

For a new machine without old chats, install the repository and run `codex-as
add` for every account. This produces a fresh, self-contained data tree and new
local OAuth caches.

To preserve existing local chats:

1. Stop every Codex process that uses the source data.
2. Securely copy the complete multiplexer data directory.
3. Install this repository on the destination machine.
4. Run `codex-as login NAME` for each account whose authentication cache was not
   copied.
5. Run `codex-as reindex` to repair destination rollout paths.
6. Run `codex-as doctor` and `codex-smi --no-refresh`.

Authentication files and session rollouts are sensitive. Transfer them through
an encrypted channel and preserve restrictive file permissions. Never commit
the data root.

Fresh managed setups are self-contained. Imported legacy setups may reference
paths outside the data root; `codex-as doctor` reports
`uses imported/external paths` when those paths must also be copied.

Do not place one writable SQLite/session tree on multiple machines
simultaneously. Copy it only while all source Codex processes are stopped.

## Import an existing Omarchy setup

Existing Omarchy account homes can be registered without copying credentials:

```bash
codex-as import-omarchy
```

To import a registry from a non-default location:

```bash
codex-as import-omarchy --registry /path/to/codex-accounts.json
```

The default source registry is
`~/.config/omarchy/agents/codex-accounts.json`.

The importer reads the existing Omarchy account registry and:

- maps names such as `account-1` to `acc1`;
- preserves the old ID as an alias;
- keeps the existing account-home paths;
- uses the old default Codex home as shared chat state.

Verify the result:

```bash
codex-as accounts
codex-as doctor
codex-smi --no-refresh
```

An imported setup is intentionally reported as using external paths. A clean
installation created with `codex-as add` is self-contained instead.

## Configuration

| Environment variable | Purpose | Default |
|---|---|---|
| `CODEX_MULTIPLEXER_HOME` | Registry, accounts, and shared state | `$XDG_DATA_HOME/codex-multiplexer`, or `~/.local/share/codex-multiplexer` |
| `CODEX_MULTIPLEXER_CODEX_BIN` | Explicit Codex executable | First `codex` on `PATH` |
| `CODEX_MULTIPLEXER_INSTALL_ROOT` | Installer destination prefix | `~/.local` |
| `CODEX_MULTIPLEXER_LIBEXEC` | Wrapper fallback implementation directory | `~/.local/libexec/codex-multiplexer` |
| `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS` | Accounts omitted by `codex-lb` | Empty |
| `CODEX_MUX_ROUTE_RECORD` | Create a JSON record of a `codex-lb` routing decision | Unset |

Example:

```bash
export CODEX_MULTIPLEXER_HOME=/secure/persistent/codex-multiplexer
export CODEX_MULTIPLEXER_CODEX_BIN=/opt/codex/bin/codex
```

The shorter compatibility variables `CODEX_MUX_HOME` and
`CODEX_MUX_CODEX_BIN` are also recognized. `CODEX_MUX_EXCLUDE_ALIASES` is a
fallback for `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS` when the latter is unset.

`CODEX_MUX_ROUTE_RECORD` is intended for calling automation. The target must not
already exist; `codex-lb` creates it with mode `0600` and records the candidate
statuses, selected account, and selection reason.

## Security and concurrency

- Each account's `auth.json` contains sensitive authentication material.
- Shared rollout files can contain prompts, source excerpts, tool output, and
  local paths.
- The registry contains no access token, but it may contain account emails.
- The registry is mode `0600`; new managed directories are mode `0700`.
- Login and logout operations are serialized per account.
- Registry writes use a lock, `fsync`, and atomic replacement.
- Separate threads can run concurrently under different accounts.
- Never run two active turns against the same thread ID at the same time.

See [SECURITY.md](SECURITY.md) for the full trust boundary.

## Troubleshooting

### `codex-as: command not found`

Add the installed bin directory to the current shell and login-shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Account is not logged in

```bash
codex-as login ACCOUNT
```

Use `--device-auth` on a headless host. If the initial `codex-as add` was
interrupted, the account slot normally already exists, so use `login` rather
than creating a differently named slot.

### Wrong browser account was used

Pin the expected identity when adding the slot:

```bash
codex-as add work --expect you@company.example
```

If the browser returns a different email, the multiplexer logs out that
incorrect identity instead of retaining it.

### A copied chat does not appear

```bash
codex-as reindex
codex-as doctor
```

Reindexing asks Codex to scan the shared rollout tree. It does not edit Codex's
private SQLite schema directly.

### `doctor` reports imported/external paths

This is expected after `import-omarchy`. Those registered account and shared
state paths live outside the multiplexer data directory. Copy them too when
moving that legacy installation, or create fresh managed account slots.

### One account fails in `codex-smi`

`codex-smi` returns per-account partial results and exits nonzero if an account's
App Server is unavailable. Check the login and retry:

```bash
codex-as login ACCOUNT
codex-smi ACCOUNT --no-refresh
```

### `codex-lb` reports that no account is selectable

Inspect every account, then re-authenticate or wait for quota to reset as
appropriate:

```bash
codex-smi --details --no-refresh
codex-as login ACCOUNT
```

Also check `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS`; an excluded, busy, logged-out,
unavailable, or exhausted account is not eligible for automatic selection.

### Repository changes are not reflected in installed commands

The installer copies the implementation and documentation instead of running
them from the checkout. Re-run it after pulling updates:

```bash
./install.sh
```

## Development and verification

Run the complete dependency-free verification suite:

```bash
python3 scripts/verify.py
```

It covers:

- registry validation and restrictive permissions;
- account isolation and shared runtime paths;
- relocation and repair of legacy absolute links;
- exact Codex argument forwarding;
- App Server identity and rate-limit parsing;
- active and archived rollout reindexing;
- custom-prefix installation;
- a clean simulated remote-host installation;
- two sequential headless logins, cross-account resume, and remote diagnostics;
- credential-file and common token-pattern detection in the repository.

Useful alternatives:

```bash
make test
make verify
make install
```

## License

Codex Multiplexer is available under the [MIT License](LICENSE).

## Additional documentation

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Installation and portability](docs/PORTABILITY.md)
- [Registry format](docs/REGISTRY.md)
- [Verification strategy](docs/TESTING.md)
- [Security guidance](SECURITY.md)
- [Registry JSON Schema](schema/registry.schema.json)
