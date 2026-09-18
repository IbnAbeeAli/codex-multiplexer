# Architecture

## Goals

Codex Multiplexer provides four guarantees:

1. Each account has an isolated, persistent authentication cache.
2. Every account sees the same local chats and can resume the same thread ID.
3. Usage data comes from Codex's structured App Server protocol.
4. A fresh installation uses only relative registry paths and can be copied as a
   self-contained data tree.

## Components

```text
codex-as ── selects account ──> CODEX_HOME=accounts/<name>
    │                              └── auth.json (account-private)
    │
    ├── CODEX_SQLITE_HOME ─────> shared/state_*.sqlite
    ├── sessions symlink ──────> shared/sessions
    ├── shell snapshots ───────> shared/shell_snapshots
    └── writer locks ──────────> shared/thread-writer-locks

codex-smi ── JSON-RPC/stdio ──> codex app-server per account
                                ├── account/read
                                ├── account/rateLimits/read
                                └── account/usage/read (optional)
```

`CODEX_HOME` is the credential isolation boundary. The launcher sets
`CODEX_SQLITE_HOME` and additionally passes `sqlite_home` explicitly so an
account's own `config.toml` cannot silently split the shared thread index.

Managed links use relative targets. As a result, moving the entire data root
does not bake the previous machine's home path into `sessions`, shell snapshots,
or writer locks. The layout repair also converts older managed absolute links
and repairs broken absolute links after a move.

## Data ownership

| Path | Owner | Contains secrets | Shared |
|---|---|---:|---:|
| `registry.json` | Multiplexer | No tokens; may contain email | Yes |
| `accounts/<name>/auth.json` | Codex | Yes | No |
| `shared/state_*.sqlite` | Codex | Chat metadata | Yes |
| `shared/sessions/` | Codex | Chat transcripts/tool results | Yes |
| `shared/thread-writer-locks/` | Codex | No | Yes |

The multiplexer never reads or copies `auth.json`. Login and refresh operations
are delegated to the installed Codex CLI.

## Concurrency

Codex's SQLite databases provide multi-process coordination. All account homes
also resolve `thread-writer-locks` to one directory, so Codex sees the same lock
for a thread regardless of which account resumes it. Separate threads may run
concurrently. Starting two turns on one thread remains unsupported.

Registry writes are serialized with `flock`, written to a mode-`0600` temporary
file, `fsync`ed, and atomically replaced.

## Failure behavior

- Missing account: launch is rejected before Codex starts.
- Missing login cache: launch instructs the user to run `codex-as login`.
- Wrong browser identity: when `expectedEmail` is set, the login is cleared.
- App Server timeout/failure: `codex-smi` returns partial per-account results and
  a non-zero status for unavailable accounts.
- Moved rollout files: `codex-as reindex` invokes Codex's rollout scan through
  `thread/list` rather than editing Codex's SQLite schema directly.
- Registry version mismatch: the tool fails closed instead of guessing.

## Trust boundary

This tool selects credentials; it does not add sandboxing. Arguments after the
account name are passed to Codex unchanged. In particular, `--yolo` retains its
full Codex meaning and should only be used in an externally isolated environment.
