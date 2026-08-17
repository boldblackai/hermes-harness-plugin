# hermes-harness-plugin

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that optimizes
Hermes for use with [harness](https://github.com/boldblackai/harness).

> **Scope.** This plugin is purpose-built for the boldblackai **harness**
> container images. It assumes mise lives at `/usr/local/bin/mise`,
> auto-trusts the nearest mise config on session start, and injects
> harness-specific environment facts into every LLM turn. It is not intended
> for — and will misbehave outside of — those images. If you pip-install it
> elsewhere, expect the hardcoded paths and bundled context to be wrong for
> your environment.

It bundles a **`mise` skill** and **six lifecycle hooks**:

1. a `pre_tool_call` hook that transparently activates mise for every
   `terminal()` command when a mise config file is present;
2. an `on_session_start` hook that trusts the nearest mise config on startup;
3. a `post_tool_call` hook that re-trusts configs after mutations and tracks
   the shell's working directory;
4. an `on_session_reset` hook that clears session bookkeeping; and
5. two `pre_llm_call` hooks — one tells the model mise activation is handled
   (when active), the other injects a bundled
   [`context.md`](src/hermes_harness_plugin/context.md) as additional context
   into every LLM turn.

The mise behavior is modeled on the battle-tested
[`pi-mise`](https://github.com/capotej/pi-mise) (the same idea, for the `pi`
coding agent) and tracks its behavior feature-for-feature.

## What it does

When Hermes starts and this plugin is enabled, every `terminal()` command is
transparently run inside an activated mise shell — but **only** when it
matters:

1. a mise config file (`mise.toml` / `.mise.toml` / `.tool-versions`) exists in
   the working directory or any parent.

Harness images always ship mise at `/usr/local/bin/mise`, so the hook uses that
path directly — it never tries to *detect* or resolve mise from PATH. If no
config is found, the hook no-ops — zero overhead. It is also **idempotent**: it
never double-wraps a command that's already activating mise.

Concretely, a `terminal(command="bundle install")` issued in a repo with a
`mise.toml` becomes:

```bash
eval "$(/usr/local/bin/mise activate bash 2>/dev/null)" 2>/dev/null || true && bundle install
```

The wrapper is **stderr-tolerant**: if mise activation hiccups (an untrusted
config somewhere up the tree, a transient warning), the failure is swallowed
and the real command still runs — non-fatal mise output can never fail the
command.

### Trust lifecycle (pi-mise parity)

mise **revokes trust when a config file changes**. The plugin manages trust
end-to-end:

- **Session start** — the nearest config at/above the working directory is
  trusted once.
- **Ahead of `cd`** — any `cd <target>` in a command is parsed, and the target
  directory's config is trusted *before* the command runs (cached per
  directory).
- **After mutation** — when a config is modified via the `write_file`/`patch`
  tools, or a command runs `mise use`/`unset`/`set` or redirects onto a config
  file, trust is invalidated and re-applied on the next command.

### Shell-cwd shadow tracking (Hermes-specific)

Hermes' persistent shell `cd`s independently of the Python process, so the
plugin tracks the shell's working directory itself: seeded from the process
cwd at session start, advanced by parsed `cd` segments, and corrected from the
authoritative `cwd` field of every terminal result. mise config resolution and
trust decisions use that shadow, not `os.getcwd()`.

### Model note (pi-mise parity)

When mise is active, a `pre_llm_call` hook injects a short note telling the
model that activation and trust are automatic — **even when a project's
AGENTS.md instructs manual activation** — so the model never redundantly
prepends `eval "$(mise activate bash)"` or runs `mise trust`.

It also contributes a **`mise` skill** (`skill_view("hermes-harness-plugin:mise")`)
covering manual `mise exec`, tasks, installs, trust, and the common pitfalls.

## Context injection (`pre_llm_call`)

A `pre_llm_call` hook injects the contents of the bundled **`context.md`**
(shipped inside the package) as additional context into every LLM turn. Edit
`src/hermes_harness_plugin/context.md` in the repo to control what the model
sees on every turn. By design the injection is per-turn: the hook returns the
text for the host to splice into the current turn only. How the host handles
persistence, the system prompt, and prompt caching is a Hermes decision outside
this plugin's control — see the Hermes docs for those semantics.

## Prerequisites

- **Python >= 3.13.**
- **Hermes Agent** — this is a plugin, not a standalone app; it does nothing
  without Hermes loaded as the host.
- **mise** at `/usr/local/bin/mise` (preinstalled on harness images).

## Install

```bash
# via pip (or uv pip)
pip install hermes-harness-plugin

# or from source with uv
uv pip install .
```

Hermes auto-discovers the plugin via the `hermes_agent.plugins` entry point on
next startup. Enable it (plugins are opt-in):

```bash
hermes plugins enable hermes-harness-plugin
```

Confirm with `/plugins` in a session.

## How it works

Hermes' `pre_tool_call` hook receives the **same `args` dict** that is later
dispatched to the `terminal` handler, so mutating `args["command"]` in place
inside the hook changes what actually executes. The plugin prepends the
activation prefix (using the hardcoded mise path), checks for a config file,
and rewrites the command. All failures are caught and logged — a broken hook
never breaks the agent loop.

## Repo layout

```
hermes-harness-plugin/
├── pyproject.toml                      # uv/pip packaging + entry point
├── README.md
├── LICENSE
└── src/hermes_harness_plugin/
    ├── __init__.py                     # register(ctx): skill + hooks
    ├── plugin.yaml                     # manifest (also enables directory-plugin use)
    ├── mise.py                         # mise activation hook logic
    ├── context.py                      # pre_llm_call context-injection hook
    ├── context.md                      # bundled context (injected every turn)
    └── skills/
        └── mise/
            └── SKILL.md                # bundled skill
```

## Develop

```bash
uv sync                                 # create venv + install dev deps
uv run pytest                           # run tests
uv build                                # build sdist + wheel into dist/
```

## Release

Releases are fully automated via trusted publishing — no PyPI tokens stored as secrets.

1. Bump `__version__` in `src/hermes_harness_plugin/__init__.py`
2. Add a `CHANGELOG.md` entry
3. Open a PR titled `release v<version>` from a fork
4. Squash-merge — the `release.yml` workflow tags, builds, publishes to PyPI (OIDC), and creates a GitHub release

The `release v` commit-message sentinel triggers the workflow on `push: main`.

## License

MIT
