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

It bundles a **`mise` skill** and **three lifecycle hooks**:

1. a `pre_tool_call` hook that transparently activates mise for every
   `terminal()` command when a mise config file is present;
2. an `on_session_start` hook that trusts the nearest mise config on startup; and
3. a `pre_llm_call` hook that injects a bundled [`context.md`](src/hermes_harness_plugin/context.md) as additional
   context into every LLM turn.

The mise activation is modeled on [`pi-mise`](https://github.com/capotej/pi-mise)
(the same idea, for the `pi` coding agent).

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
eval "$(/usr/local/bin/mise activate bash)" && bundle install
```

An `on_session_start` hook also runs `mise trust` on the nearest config so
activation is frictionless on fresh clones. (This auto-trusts whatever config
is in the working tree — appropriate inside a trusted harness image, but
another reason not to run this plugin elsewhere.)

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

## License

MIT
