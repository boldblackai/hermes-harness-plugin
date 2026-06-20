---
name: mise
description: Manage tool versions with mise. Covers activation, trust, tasks, installs, and the pitfalls that bite in coding sessions. When the hermes-harness-plugin is active, mise is auto-activated for terminal commands — do NOT manually prepend activation.
version: 0.1.0
author: Hermes Harness Contributors
license: MIT
---

# mise

[mise](https://mise.jdx.dev/) is a polyglot version manager. It activates
tool versions declared in `mise.toml` / `.mise.toml` / `.tool-versions` per
directory, replacing asdf / rbenv / nvm / pyenv and friends.

## TL;DR — is activation already on?

This plugin installs a `pre_tool_call` hook that **automatically** prepends
`eval "$(mise activate bash)"` to every `terminal()` command when mise is
installed **and** a mise config file exists in the working tree (or when
`HERMES_HARNESS_MISE_ALWAYS=1` is set).

- **If the hook is active**, do NOT manually add `eval "$(mise activate bash)"`.
  It is already applied to every command. Just `cd` into the repo and run.
- **If you are unsure**, check with: `echo "$MISE_SHELL"` (set when active) or
  run `/plugins` to confirm `hermes-harness-plugin` is loaded.
- **For a guaranteed-clean run** (bypass the hook once), set
  `HERMES_HARNESS_MISE_DISABLE=1` in the command's environment.

## When to use mise

- A repo contains `mise.toml`, `.mise.toml`, or `.tool-versions`.
- You need a pinned version of node, python, ruby, go, bun, java, etc.
- You want per-project tool versions without polluting the system.

## Single-command execution (`mise exec`)

For one-off commands, `mise exec` needs no activation — use it directly:

```bash
cd /path/to/repo && mise exec -- bundle install
cd /path/to/repo && mise exec -- rails test
mise exec --node@20 -- which node      # ad-hoc tool version
mise exec -C /path/to/repo -- npm test # explicit dir, no cd
```

## Tasks

If `mise.toml` defines `[tasks]`, run them with `mise run`:

```bash
cd /path/to/repo && mise run <task_name>
mise run --list        # show available tasks
```

## Installing tools

```bash
cd /path/to/repo && mise install        # install everything in config
mise install node@22                    # specific tool/version
mise use -g node@22                     # set a global default
```

## Trust

mise refuses to load tools from an **untrusted** config (fresh clone, edited
file). The plugin's `on_session_start` hook trusts the nearest config
automatically, but if you hit a trust prompt:

```bash
cd /path/to/repo && mise trust
```

Trust is per-file: editing `mise.toml` revokes it and requires re-trust.

## Standard pattern (when NOT auto-activated)

```bash
cd /path/to/repo \
  && eval "$(mise activate bash)" \
  && your_command_here
```

## Pitfalls

- **Activation is per-shell.** `mise activate` mutates PATH/env for the current
  process only. Each `terminal()` call is a fresh shell, which is why this
  plugin prepends activation to every one. If you ever bypass the hook, you
  must chain activation into each command yourself.
- **Trust is per-file.** Modifying `mise.toml` / `.mise.toml` revokes trust;
  re-run `mise trust`.
- **Working directory matters.** mise resolves versions by walking from cwd
  upward. Always `cd` into the repo first, or use `mise exec -C <dir>`.
- **Ruby compiles from source slowly.** Set `mise settings ruby.compile=false`
  (precompiled binaries) before `mise install` to avoid 5+ min builds.
- **Missing system headers break gems.** e.g. `psych` needs `libyaml-dev`.
  Install `-dev`/`-devel` packages for gems that fail with `missing *.h`.
- **`bundle`/`bundler` are NOT mise tools.** They ship with Ruby via RubyGems;
  don't add them to `[tools]`. Supported tools: ruby, node, python, bun, go,
  java, etc.
- **Config file naming.** mise accepts `mise.toml`, `.mise.toml` (hidden), and
  `.tool-versions` (asdf-compatible). All three are recognized.
