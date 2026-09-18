# Verification strategy

Run the complete local verification suite:

```bash
python3 scripts/verify.py
```

The suite checks:

- Python syntax and unit tests;
- registry validation and file permissions;
- shared sessions, snapshots, and writer-lock layout;
- custom-prefix installation and wrapper self-resolution;
- clean remote-home installation with separate installed documentation and
  mutable data;
- sequential headless account login, persistent homes, remote launch, and
  remote `codex-smi` behavior;
- exact argument forwarding to a fake Codex binary;
- App Server handshake and structured `codex-smi` parsing;
- display-focused Omarchy records, recommendations, private cache permissions,
  manifest structure, and repeatable live installation;
- JSON schema and example registry syntax;
- absence of credential files and common token patterns in the repository.

Tests use disposable directories and fake credentials only. The real-account
smoke checks are intentionally separate:

```bash
codex-as doctor
codex-smi --no-refresh
codex-as ACCOUNT --version
```
