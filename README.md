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
- Codex CLI available on `PATH`.
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

Install the commands:

```bash
git clone https://github.com/IbnAbeeAli/codex-multiplexer.git
cd codex-multiplexer
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

Log in one account at a time:

```bash
codex-as add acc1
codex-as add acc2
```

For a remote or headless machine, use device-code login:

```bash
codex-as add acc1 --device-auth
codex-as add acc2 --device-auth
```

Verify the setup:

```bash
codex-as accounts
codex-as doctor
codex-smi --no-refresh
```

Start Codex with the selected account:

```bash
codex-as acc1
codex-as acc2
```

Resume a thread under another account:

```bash
codex-as acc2 resume 0199cafe-0000-7000-8000-000000000000
```

`--yolo` is a Codex option that disables approvals and sandboxing. It is not
required by the multiplexer and should only be used in an appropriately isolated
environment.

## Installation details

The default installation uses:

| Item | Default path |
|---|---|
| Commands | `~/.local/bin` |
| Python implementation | `~/.local/libexec/codex-multiplexer` |
| Installed documentation | `~/.local/share/doc/codex-multiplexer` |
| Mutable account/chat data | `~/.local/share/codex-multiplexer` |

Add `~/.local/bin` to the login shell's `PATH` so the commands remain available
after reconnecting or restarting.

Install into a different prefix:

```bash
CODEX_MULTIPLEXER_INSTALL_ROOT=/opt/codex-multiplexer ./install.sh
```

Installed wrappers resolve the Python implementation relative to their own
prefix, so custom installations do not depend on `~/.local`.

## Command reference

### Account management

| Command | Purpose |
|---|---|
| `codex-as add NAME` | Create an account slot and log in |
| `codex-as add NAME --device-auth` | Add an account on a headless host |
| `codex-as add NAME --expect EMAIL` | Pin the slot to an expected email |
| `codex-as login NAME` | Log in again or replace cached authentication |
| `codex-as logout NAME` | Log out only the selected account |
| `codex-as accounts` | List account names, expected emails, and homes |
| `codex-as default NAME` | Choose the maintenance/default account |
| `codex-as doctor` | Validate installation, state, permissions, and rollouts |
| `codex-as reindex [NAME]` | Ask Codex to rescan shared session rollouts |

The first successfully added account becomes the default account.

### Running Codex

Everything after the account selector is passed to Codex unchanged:

```bash
codex-as acc1
codex-as acc1 --yolo
codex-as acc1 exec "Run the tests"
codex-as acc2 --yolo resume SESSION_ID
```

Account selectors are case-insensitive and can also match a configured alias or
expected email.

### Automatic account selection

`codex-lb` probes every configured account and launches Codex with the available
account that has the most remaining quota:

```bash
codex-lb
codex-lb resume SESSION_ID
```

Set `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS` to a comma-separated list of account
names, aliases, or expected emails to omit from a particular launch.

### Usage and limits

```bash
codex-smi
codex-smi acc1 acc2
codex-smi --no-refresh
codex-smi --info
codex-smi --details
codex-smi --usage
codex-smi --json
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

The shared SQLite index records thread IDs and rollout paths. Because every
account sees the same index and rollouts, both the session picker and explicit
`resume SESSION_ID` can continue the same chat under another account.

## Files and directories

A clean managed installation uses this layout:

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
4. Log in each account again if authentication caches were not copied.
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
| `CODEX_MULTIPLEXER_HOME` | Registry, accounts, and shared state | `~/.local/share/codex-multiplexer` |
| `CODEX_MULTIPLEXER_CODEX_BIN` | Explicit Codex executable | First `codex` on `PATH` |
| `CODEX_MULTIPLEXER_INSTALL_ROOT` | Installer destination prefix | `~/.local` |
| `CODEX_MULTIPLEXER_LIBEXEC` | Wrapper fallback implementation directory | `~/.local/libexec/codex-multiplexer` |
| `CODEX_MULTIPLEXER_EXCLUDE_ACCOUNTS` | Accounts omitted by `codex-lb` | Empty |

Example:

```bash
export CODEX_MULTIPLEXER_HOME=/secure/persistent/codex-multiplexer
export CODEX_MULTIPLEXER_CODEX_BIN=/opt/codex/bin/codex
```

The shorter compatibility variables `CODEX_MUX_HOME` and
`CODEX_MUX_CODEX_BIN` are also recognized.

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

Use `--device-auth` on a headless host.

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
