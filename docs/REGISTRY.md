# Registry schema

The canonical schema is [`schema/registry.schema.json`](../schema/registry.schema.json).
The live registry defaults to:

```text
~/.local/share/codex-multiplexer/registry.json
```

`CODEX_MULTIPLEXER_HOME` changes the parent data directory. The registry file is
created with mode `0600`; its parent directory is mode `0700`.

## Path resolution

Paths use these rules in order:

1. Environment variables and `~` are expanded.
2. Absolute paths are used as-is.
3. Relative paths resolve from `CODEX_MULTIPLEXER_HOME`.

Fresh accounts use relative paths (`accounts/<name>` and `shared`) so their data
tree is relocatable. Imported legacy configurations may intentionally contain
external paths. `codex-as doctor` reports whether a registry is self-contained.

## Compatibility

The top-level `version` is the data-model version, independent of the program
version. Unknown versions are rejected. Future schema migrations must be
explicit, atomic, and preserve a backup of the original registry.

`expectedEmail` is an identity pin, not a credential. Account names and aliases
must be unique under case-insensitive comparison.
