# AGENTS.md — hermes-harness-plugin

Repo-level instructions for AI agents (Hermes, Claude Code, Codex, etc.).
Global rules (git identity, jj-vs-git) live in `~/.hermes/AGENTS.md`; this file
adds what's specific to this repo. When the two disagree, prefer the more
specific file.

For what the plugin does, install, env vars, and repo layout, see **README.md**.
This file covers only what an agent needs to edit the code safely.

Load the `hermes-plugin-development` skill before changing hooks or packaging.
Hermes docs: https://hermes-agent.nousresearch.com/docs

## Toolchain

- **Python >=3.13** (developed on 3.13).
- **uv** is the dev tool. No `requirements.txt`; lock is `uv.lock`.
- Dev dependencies (optional `[dev]` extra): **pytest** (tests), **ruff**
  (linter/formatter), **ty** (type checker).
- Build backend: setuptools with `src/` layout.
- There is no bare `python`/`pip` on PATH; the environment is PEP 668 (use `uv` or the venv). Run Python via `uv run python ...`.

## Lint, type-check, test

CI (`.github/workflows/ci.yml`) runs these on every push/PR — run them locally
before committing:

```
uv run ruff check .     # linter (config in [tool.ruff])
uv run ty check src     # type checker
uv run pytest           # tests
```

Dependency pinning: `uv.lock` holds exact versions + hashes, and
`[tool.uv] exclude-newer = "7d"` applies a rolling 7-day cutoff that ignores
any distribution published in the last week. CI installs with
`uv sync --locked`, so a stale lock fails CI rather than silently re-resolving.
After changing dependencies, re-lock with `uv lock`.

## Version control

This repo uses **git** (no `.jj`). Remote is HTTPS; default branch `main`. Git
identity is already set globally (see `~/.hermes/AGENTS.md`) — do **not**
re-run `git config --global`. When committing, read `git config user.name` first.

## Architecture

### The one technique that matters (mise)

The whole value of the mise hook is the **`pre_tool_call` in-place args-mutation**
trick. Hermes passes the *same* `args` dict to `pre_tool_call` that it later
hands to the `terminal` handler, so mutating `args["command"]` inside the hook
changes what actually executes:

```
terminal("bundle install")
  -> pre_tool_call mutates args["command"] in place
  -> handler runs:  eval "$(mise activate bash 2>/dev/null)" 2>/dev/null || true && bundle install
```

`mise.py` is the only file with mise logic. Its structure:

- **Pure parser helpers** (unit-tested, no side effects):
  `is_mise_config_name`, `find_mise_config` / `find_mise_config_dir` (walk-up),
  `resolve_path`, `cd_target_from_segment`, `parse_cd_dirs`,
  `mutates_mise_config`, `compute_prefix` (the should-activate decision core).
- `_MISE_BIN = "/usr/local/bin/mise"` — hardcoded; harness images always
  install mise at this path. No resolution or caching needed.
- `_SessionState` (`state`) — per-session bookkeeping: `mise_active` flag,
  `trusted_dirs` cache, `shadow_cwd` (the persistent shell's cwd), and
  `bash_call_info` (tool_call_id → mutation context for post_tool_call).
- Hooks: `pre_tool_call` (trust + rewrite), `post_tool_call` (trust
  invalidation + shadow-cwd advance), `on_session_start` / `on_session_reset`
  (trust once / clear state), `pre_llm_call_note` (tell the model activation
  is automatic).

The behavior is **pi-mise parity** (https://github.com/capotej/pi-mise —
battle-tested over many sessions):

- **Stderr-tolerant activation**: the prefix ends with `2>/dev/null || true`
  so mise noise (untrusted config up the tree, warnings) can never fail the
  user's command.
- **Trust lifecycle**: mise revokes trust when a config changes, so trust is
  re-applied: at session start, ahead of every `cd <target>` (parsed from the
  command), and re-done after mutations (`write_file`/`patch` to a config,
  `mise use`/`unset`/`set`, redirection onto a config).
- **Shadow cwd** (Hermes-specific): the persistent shell `cd`s independently
  of the Python process. `shadow_cwd` is seeded from process cwd at session
  start, advanced by parsed `cd` segments, and corrected from each terminal
  result's `cwd` field. Config resolution uses the shadow, not `os.getcwd()`.
- **Model note**: while active, a `pre_llm_call` hook injects a note telling
  the model NOT to manually activate/trust — even when AGENTS.md says to.
  Hermes merges multiple `pre_llm_call` `{"context": ...}` returns into the
  user message, so this composes with `context.py`'s injection.

Activation is **idempotent**: commands already containing the activate marker
(`__MISE_EXE=`) or the literal `mise activate` are left untouched.

### Context injection (pre_llm_call)

`context.py` reads the bundled `context.md` and returns `{"context": text}`.
Simple by design — the file is the surface, not the code. Note `mise.py`
registers a second `pre_llm_call` hook (`pre_llm_call_note`); Hermes runs
both and joins their context parts.

### Hooks must never raise

All hooks (`mise`, `context`) wrap their bodies in `try/except` and log on
failure — a broken hook must never break the agent loop. Preserve this when editing.

## Conventions

- **Pure helpers over integration tests.** New should-activate logic goes into
  a pure helper (`compute_prefix`, `parse_cd_dirs`, `mutates_mise_config`, …),
  then gets a unit test. The hooks stay thin: call pure helpers, mutate in
  place, catch all.
- **pytest config** is in `pyproject.toml` (`testpaths=["tests"]`, `addopts="-q"`,
  `markers=["integration", ...]`). Do not add a separate `pytest.ini`. Run
  `uv run pytest -m "not integration"` to skip the real-mise suite.

## Packaging gotchas

- `plugin.yaml` is shipped but **not parsed** for entry-point plugins (Hermes
  derives the manifest from the entry point + `register(ctx)`). Keep it for
  documentation and to allow directory-plugin reuse. A blank version in Hermes's
  plugin list is expected, not a bug.
- `plugin.yaml`, `skills/**/*.md`, and `context.md` are included via
  `[tool.setuptools.package-data]`. **Do not remove that block** — without it the
  wheel silently drops those files, and an editable install won't catch it.
- When verifying a change, test the **built wheel** (`uv build`), not just the
  editable install — the editable install reads files from disk and masks
  package-data mistakes.

## Namespacing

The package module is `hermes_harness_plugin`; the plugin namespace (entry-point
name) is `hermes-harness-plugin`. Plugin skills are namespaced, so the mise
skill is `hermes-harness-plugin:mise`, never bare `mise`.

## Plugins are opt-in

Discovery is automatic via the entry point, but the plugin does nothing until
enabled: `hermes plugins enable hermes-harness-plugin` (confirm with `/plugins`).
