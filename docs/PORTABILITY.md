# Installation and portability

## Supported remote targets

| Target | Status | Requirement |
|---|---|---|
| Linux over SSH | Supported | Python 3.10+, Codex CLI, writable persistent home |
| macOS over SSH | Supported | Python 3.10+, Codex CLI, writable persistent home |
| Windows with WSL | Supported in WSL | Install and run everything inside one WSL distribution |
| Native Windows | Not currently supported | Uses POSIX file locks and symbolic links |
| Persistent container | Supported | Persist the multiplexer data root between container runs |

Keep the data root on a filesystem that supports SQLite locking and symbolic
links. Do not mount one writable data root into multiple remote hosts at the same
time; copy it only while every Codex process using it is stopped.

## Fresh machine

```bash
./install.sh
codex-as add acc1
codex-as add acc2
codex-as doctor
codex-smi
```

Use `--device-auth` for a machine where the normal localhost browser callback is
not convenient.

## Remote SSH host

The remote host needs Python 3.10 or newer and the Codex CLI. Copy or clone this
repository onto that host, then run the installation and logins in an SSH
terminal:

```bash
./install.sh
export PATH="$HOME/.local/bin:$PATH"
codex-as add acc1 --device-auth
codex-as add acc2 --device-auth
codex-as doctor
codex-smi --no-refresh
```

Add `~/.local/bin` to the remote account's login-shell `PATH` so commands also
work in later SSH sessions and through Codex Remote. If device-code login is not
enabled for the account or workspace, connect with local callback forwarding
and use the normal login flow:

```bash
ssh -L 1455:localhost:1455 user@remote-host
codex-as add acc1
```

Repeat the login one account at a time. Do not expose Codex App Server or any
callback listener to a public interface; the supported remote boundary is SSH.

## Custom installation root

```bash
CODEX_MULTIPLEXER_INSTALL_ROOT=/opt/codex-multiplexer ./install.sh
```

The installed wrappers resolve the implementation relative to their own `bin`
directory, so a non-default prefix does not depend on `~/.local`.

## Copying data to another machine

For a fresh setup, logging in again is preferred. To preserve local chats:

1. Stop every Codex process using the data tree.
2. Securely copy the full multiplexer data directory.
3. Install this project on the destination.
4. Log in each account again if its local OAuth cache was not copied.
5. Run `codex-as reindex` to let Codex scan the shared rollouts and repair its
   index for the destination paths.
6. Run `codex-as doctor` and `codex-smi`.

Fresh managed registries use relative paths and managed runtime symlinks use
relative targets, so the copied tree may live under a different username or
absolute data path. Codex rollout records can still contain old absolute paths;
that is why the reindex step is required.

Authentication caches and chat rollouts are sensitive. Use an encrypted channel
and preserve restrictive permissions. Never commit the data directory.

Imported legacy installations can reference paths outside the multiplexer data
root. `codex-as doctor` labels these as `uses imported/external paths`; copy those
paths too or create fresh managed accounts on the destination.
