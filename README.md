# hermes-harness-plugin

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that optimizes
Hermes for use with [harness](https://github.com/boldblackai/harness).

It currently bundles one thing: a **mise auto-activation skill + hook** modeled
on [`pi-mise`](https://github.com/capotej/pi-mise) (the same idea, for the `pi`
coding agent).

## What it does

When Hermes starts and this plugin is enabled, every `terminal()` command is
transparently run inside an activated mise shell — but **only** when it
matters:

1. a mise config file (`mise.toml` / `.mise.toml` / `.tool-versions`) exists in
   the working directory or any parent, **or** `HERMES_HARNESS_MISE_ALWAYS=1`.

mise is assumed to be installed and on PATH — harness images always ship it, so
the hook never tries to *detect* mise, it just resolves the binary once. If the
above condition isn't met, the hook no-ops — zero overhead. It is also
**idempotent**: it never double-wraps a command that's already activating mise.

Concretely, a `terminal(command="bundle install")` issued in a repo with a
`mise.toml` becomes:

```bash
eval "$(/usr/local/bin/mise activate bash)" && bundle install
```

An `on_session_start` hook also runs `mise trust` on the nearest config so
activation is frictionless on fresh clones.

It also contributes a **`mise` skill** (`skill_view("hermes-harness-plugin:mise")`)
covering manual `mise exec`, tasks, installs, trust, and the common pitfalls.

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

## Environment overrides

| Variable | Effect |
|---|---|
| `HERMES_HARNESS_MISE_ALWAYS=1` | Activate on every terminal call, even with no config file. |
| `HERMES_HARNESS_MISE_DISABLE=1` | Disable the hook entirely (raw shell). |
| `HERMES_HARNESS_MISE_SHELL=zsh` | Shell passed to `mise activate` (default `bash`). |

## How it works

Hermes' `pre_tool_call` hook receives the **same `args` dict** that is later
dispatched to the `terminal` handler, so mutating `args["command"]` in place
inside the hook changes what actually executes. The plugin resolves the mise
binary once (cached), checks for a config file, and prepends the activation
prefix. All failures are caught and logged — a broken hook never breaks the
agent loop.

## Repo layout

```
hermes-harness-plugin/
├── pyproject.toml                      # uv/pip packaging + entry point
├── README.md
├── LICENSE
└── src/hermes_harness_plugin/
    ├── __init__.py                     # register(ctx): skill + hooks
    ├── plugin.yaml                     # manifest (also enables directory-plugin use)
    ├── mise.py                         # activation hook logic
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
