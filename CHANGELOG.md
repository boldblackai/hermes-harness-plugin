# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- pi-mise parity (https://github.com/capotej/pi-mise):
  - Stderr-tolerant activation — `eval "$(mise activate bash 2>/dev/null)" 2>/dev/null || true && cmd` — so mise noise can never fail the actual command.
  - Trust lifecycle: re-trust after config mutations (`write_file`/`patch`, `mise use`/`unset`/`set`, redirection onto a config) via a new `post_tool_call` hook; mise revokes trust on config change.
  - `cd`-following: parse `cd <target>` from commands and pre-trust target configs (cached per directory).
  - Live model note (`pre_llm_call`): tells the model activation/trust are automatic, even when AGENTS.md instructs manual activation.
  - Shell-cwd shadow tracking: config resolution follows the persistent shell's cwd (from terminal results' `cwd` field + parsed `cd`s), not the Python process cwd.
- `on_session_reset` hook to clear session bookkeeping.
- Integration test suite driving the hooks against the real mise binary (marked `integration`).

## [0.1.0] - 2026-08-13

### Added

- `pre_tool_call` hook: transparently activates mise for every `terminal()` command when a mise config file is present.
- `on_session_start` hook: trusts the nearest mise config on startup for frictionless activation.
- `pre_llm_call` hook: injects bundled `context.md` as additional context into every LLM turn.
- Bundled `mise` skill covering manual activation, `mise exec`, tasks, installs, and trust.
- CI workflow (lint via ruff + ty, test via pytest) with SHA-pinned actions.
- Release workflow: tag-on-merge pipeline with PyPI trusted publishing (OIDC, no tokens).
- CHANGELOG.md.
