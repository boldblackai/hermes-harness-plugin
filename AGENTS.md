# AGENTS.md — hermes-harness-plugin

Repo-level instructions for AI agents (Hermes, Claude Code, Codex, etc.).
Global rules (git identity, jj-vs-git) live in `~/.hermes/AGENTS.md`; this file
adds what's specific to this repo. When the two disagree, prefer the more
specific file.

## What this is

A **Hermes Agent** plugin (`pip`/`uv` entry-point package) that optimizes Hermes
for **harness environments** — sandboxed agent runtimes where `mise` ships
preinstalled and on PATH. It bundles:

- a `pre_tool_call` hook that transparently runs every `terminal()` command
  inside an activated mise shell (only when a mise config file is present),
- an `on_session_start` hook that runs `mise trust` on the nearest config,
- a `pre_llm_call` hook that injects the bundled `context.md` into every LLM
  turn as additional context,
- a `mise` skill (loadable via `skill_view("hermes-harness-plugin:mise")`).

Docs: https://hermes-agent.nousresearch.com/docs (source of truth for Hermes
internals). Load the `hermes-plugin-development` skill before changing hooks or
packaging.

## Repo layout

```
hermes-harness-plugin/
├── pyproject.toml                          # setuptools + hermes_agent.plugins entry point
├── uv.lock
├── README.md  LICENSE
├── src/hermes_harness_plugin/
│   ├── __init__.py                         # register(ctx): registers skill + hooks
│   ├── plugin.yaml                         # manifest (shipped; NOT parsed for entry-point plugins)
│   ├── mise.py                             # mise pre_tool_call hook + helpers
│   ├── context.py                          # pre_llm_call context-injection hook
│   ├── context.md                          # bundled context (injected every turn)
│   └── skills/mise/SKILL.md                # bundled skill (shipped as package-data)
└── tests/                                  # pytest; mise + context hook tests
```

The package module is `hermes_harness_plugin`; the plugin namespace (entry-point
name) is `hermes-harness-plugin`. Plugin skills are namespaced, so the mise
skill is `hermes-harness-plugin:mise`, never bare `mise`.

## Toolchain

- **Python >=3.10** (developed on 3.13).
- **uv** is the dev tool. No `requirements.txt`; lock is `uv.lock`.
- **pytest** (>=7) is the only dev dependency (optional `[dev]` extra).
- Build backend: setuptools with `src/` layout.

## Common commands

```bash
uv sync                  # create/refresh .venv + install dev deps (incl. the plugin editable)
uv run pytest            # run the test suite (27 tests; fast, ~0.03s)
uv run pytest -q         # same; addopts already sets -q
uv build                 # build sdist + wheel into dist/
```

Use `uv run python ...` to run Python against the project venv. There is no
bare `python`/`pip` on PATH; the environment is PEP 668 (use `uv` or the venv).

## Version control

This repo uses **git** (no `.jj`). Remote is HTTPS:
`https://github.com/boldblackai/hermes-harness-plugin.git`. Default branch is
`main`. Git identity is already set globally (see `~/.hermes/AGENTS.md`); do
**not** re-run `git config --global`. When committing, read
`git config user.name` first.

## Architecture: the one technique that matters

The whole value of the plugin is the **`pre_tool_call` in-place args-mutation**
trick. Hermes passes the *same* `args` dict to `pre_tool_call` that it later
hands to the `terminal` handler, so mutating `args["command"]` inside the hook
changes what actually executes:

```
terminal("bundle install")
  -> pre_tool_call mutates args["command"] in place
  -> handler runs:  eval "$(mise activate bash)" && bundle install
```

`mise.py` is the only file with logic. Its structure:

- `compute_prefix(args, mise_bin) -> Optional[str]` — **pure function**, the
  testable decision core. Returns the activation prefix or `None` to no-op.
  All should-activate logic lives here.
- `get_mise_bin() -> str` — resolves + caches the mise path via
  `shutil.which("mise") or "mise"`. Harness images always have mise, so this is
  a *resolution*, not an install probe. Do not add fallback location probes.
- `find_mise_config(directory)` — walks up from the dir to the nearest of
  `mise.toml` / `.mise.toml` / `.tool-versions` (priority order within a dir).
- `pre_tool_call(...)` / `on_session_start(...)` — the registered hooks.

Activation is **idempotent**: commands already containing the activate marker
(`__MISE_EXE=`) or the literal `mise activate` are left untouched.

### Hooks must never raise

Both hooks wrap their bodies in `try/except` and log on failure — a broken hook
must never break the agent loop. Preserve this when editing.

## Environment overrides

| Variable | Effect |
|---|---|
| `HERMES_HARNESS_MISE_ALWAYS=1` | Activate on every terminal call even with no config file. |
| `HERMES_HARNESS_MISE_DISABLE=1` | Disable the hook entirely (raw shell). |
| `HERMES_HARNESS_MISE_SHELL=zsh` | Shell for `mise activate` (default `bash`). |

Use `HERMES_HARNESS_MISE_DISABLE=1` to debug a command in a raw shell.

## Conventions

- **Pure helpers over integration tests.** New should-activate logic goes into
  `compute_prefix` (or a similarly pure helper), then gets a unit test. The
  hooks stay thin: resolve, call the pure function, mutate in place, catch all.
- **Env-var truthy parsing** is centralized in `_env_truthy`; reuse it rather
  than ad-hoc `os.getenv` checks.
- **Testing resets state.** The autouse `_clean_env` fixture clears the three
  override env vars and resets `mise._MISE_BIN` between tests. If you add new
  cached state, reset it there too.
- **pytest config** is in `pyproject.toml` (`testpaths=["tests"]`, `addopts="-q"`).
  Do not add a separate `pytest.ini`.

## Packaging gotchas

- `plugin.yaml` is shipped but **not parsed** for entry-point plugins (Hermes
  derives the manifest from the entry point + `register(ctx)`). Keep it for
  documentation and to allow directory-plugin reuse. A blank version in Hermes's
  plugin list is expected, not a bug.
- `plugin.yaml` and `skills/**/*.md` are included via
  `[tool.setuptools.package-data]`. **Do not remove that block** — without it the
  wheel silently drops the skill, and an editable install won't catch it.
- When verifying a change, test the **built wheel** (`uv build`), not just the
  editable install — the editable install reads `skills/` from disk and masks
  package-data mistakes.

## Plugins are opt-in

Discovery is automatic via the entry point, but the plugin does nothing until
enabled. To actually exercise it in a real Hermes:

```bash
hermes plugins enable hermes-harness-plugin   # then confirm with /plugins in a session
```

## License

MIT.
