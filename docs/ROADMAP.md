# Roadmap

This document collects possible future improvements. It is directional rather
than a release commitment; security, compatibility with Codex, and preservation
of the project's small trust boundary take priority over feature count.

## Omarchy integration follow-ups

The native Omarchy account dashboard, cached-first refresh, quota-aware
recommendation, and live installer are implemented. Possible follow-ups are:

- optionally launch a selected account from its detail card;
- coordinate with the built-in `omarchy.agents` Codex provider to avoid a
  duplicate Codex indicator when both widgets are enabled;
- expose supported Omarchy agent-launch hooks when Omarchy provides a stable
  per-provider launcher contract; and
- add screenshot regression coverage across representative dark and light
  Omarchy themes.

## Account lifecycle

- Add safe account removal with explicit handling for credentials, aliases,
  external imported paths, and shared chats.
- Add account rename and alias management without rewriting authentication
  caches.
- Add an interactive account picker while keeping every command fully
  scriptable.

## Routing and automation

- Make account-selection policies configurable, including preferred accounts,
  minimum remaining quota, cooldowns, and deterministic tie-breaking.
- Record optional local routing history and health information for diagnostics.
- Investigate safe retry or failover when a launch fails before a turn starts.
  Mid-turn failover should only be considered if Codex provides a supported
  mechanism that preserves thread consistency.
- Provide a documented, stable JSON interface for integrations and automation.

## Configuration sharing

- Allow users to opt specific configuration, skills, or instruction files into
  shared storage while leaving account credentials isolated.
- Detect conflicting per-account configuration that could split shared state or
  change routing behavior unexpectedly.

## Installation and platform support

- Add shell completions for Bash, Zsh, and Fish.
- Publish versioned releases with checksums and a straightforward upgrade path.
- Add package-manager installation where it can preserve the current
  dependency-free design.
- Investigate native Windows support; retain WSL as the supported Windows route
  until locking and link behavior have equivalent native implementations.

## Security and observability

- Investigate optional operating-system credential-store support without
  weakening per-account isolation or taking ownership of OAuth token parsing.
- Add machine-readable health checks and clearer diagnostics for App Server
  compatibility changes.
- Expand the platform and Codex-version test matrix, including migration and
  rollback tests for registry changes.

## Contribution guidance

Roadmap items should begin with a focused proposal describing the user problem,
CLI or integration contract, security implications, compatibility risks, and
verification plan. Features that require reading or copying Codex credentials
need especially strong justification and should prefer a Codex-supported
interface whenever one exists.
