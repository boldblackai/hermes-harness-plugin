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
  -> handler runs:  eval "$(mise activate bash)" && bundle install
```

`mise.py` is the only file with mise logic. Its structure:

- `compute_prefix(args, mise_bin) -> Optional[str]` — **pure function**, the
  testable decision core. Returns the activation prefix or `None` to no-op.
  All should-activate logic lives here.
- `_MISE_BIN = "/usr/local/bin/mise"` — hardcoded; harness images always
  install mise at this path. No resolution or caching needed.
- `find_mise_config(directory)` — walks up from the dir to the nearest of
  `mise.toml` / `.mise.toml` / `.tool-versions` (priority order within a dir).
- `pre_tool_call(...)` / `on_session_start(...)` — the registered hooks.

Activation is **idempotent**: commands already containing the activate marker
(`__MISE_EXE=`) or the literal `mise activate` are left untouched.

### Context injection (pre_llm_call)

`context.py` reads the bundled `context.md` and returns `{"context": text}`.
Simple by design — the file is the surface, not the code.

### Hooks must never raise

All hooks (`mise`, `context`) wrap their bodies in `try/except` and log on
failure — a broken hook must never break the agent loop. Preserve this when editing.

## Conventions

- **Pure helpers over integration tests.** New should-activate logic goes into
  `compute_prefix` (or a similarly pure helper), then gets a unit test. The
  hooks stay thin: call the pure function, mutate in place, catch all.
- **pytest config** is in `pyproject.toml` (`testpaths=["tests"]`, `addopts="-q"`).
  Do not add a separate `pytest.ini`.

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
