# Security

## Sensitive data

Each account home contains Codex-managed authentication material in
`auth.json`. Shared session rollouts can contain prompts, source excerpts, tool
output, and working-directory details. Treat the entire multiplexer data root as
sensitive even though `registry.json` itself contains no access tokens.

The optional `omarchy-accounts.json` cache contains account names, emails,
plans, quota percentages, and reset times. It contains no token material and is
written atomically with mode `0600`, but it should still be treated as private.

Do not commit or attach account homes, session rollouts, SQLite state, or debug
archives to an issue. Transfer them only through an encrypted channel. The tool
creates its data root and account homes with mode `0700` and its registry with
mode `0600`; `codex-as doctor` reports weaker metadata or credential permissions.

## Execution boundary

The account selector changes authentication and state locations only. Every
remaining argument is passed to Codex unchanged. `--yolo` disables Codex
approval and sandbox protections, so use it only where the surrounding machine
or container provides an appropriate isolation boundary.

Never run two active turns against one thread ID simultaneously, even under
different accounts. All accounts deliberately share that thread's rollout and
writer-lock namespace.

## Repository hygiene

The repository ignores local account homes, shared session state, SQLite files,
environment files, and Codex credential filenames. Keep the installed data root
outside the source checkout. Before publishing a change, run
`python3 scripts/verify.py`; it rejects credential files, common API-token and
private-key patterns, and non-example email addresses.

The Omarchy widget runs inside Omarchy's long-lived shell with the current
user's permissions, as all third-party Omarchy plugins do. Its QML invokes only
the installed `codex-mux` status command and the explicit right-click
`codex-lb` launcher; it does not open account homes or credential files. Review
plugin changes before installing or updating them.

## Reporting a problem

When reporting a security issue, provide the tool version, Python and Codex
versions, the command shape, and redacted diagnostic output. Remove emails,
account paths, thread IDs, tokens, prompts, and rollout contents. Never include
`auth.json`.
