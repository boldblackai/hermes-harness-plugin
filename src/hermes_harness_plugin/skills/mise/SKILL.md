---
name: mise
description: Manage tool versions with mise — activation, trust, tasks, installs, and the pitfalls that bite in coding sessions. When hermes-harness-plugin is active, mise is auto-activated for terminal commands; do NOT manually prepend activation.
version: 0.1.0
author: Hermes Harness Contributors
license: MIT
---

# mise

[mise](https://mise.jdx.dev/) is a polyglot version manager. It activates tool
versions declared in `mise.toml` / `.mise.toml` / `.tool-versions` per directory,
replacing asdf / rbenv / nvm / pyenv and friends.

## Is activation already on?

This plugin's `pre_tool_call` hook **automatically** prepends
`eval "$(mise activate bash)"` to every `terminal()` command when mise is
installed and a config file exists in the working tree (or
`HERMES_HARNESS_MISE_ALWAYS=1` is set).

- **If active**, do NOT manually add activation — just `cd` into the repo and run.

## Single-command execution (`mise exec`)

`mise exec` needs no activation — use it directly:

```bash
cd /path/to/repo && mise exec -- bundle install
mise exec -C /path/to/repo -- npm test   # explicit dir, no cd
mise exec --node@20 -- which node        # ad-hoc tool version
```

## Tasks, installs, trust

```bash
cd /path/to/repo && mise run <task>      # run a task from [tasks]
mise run --list                          # list available tasks
mise install                             # install everything in config
mise install node@22                     # specific tool/version
mise use -g node@22                      # set a global default
mise trust                               # trust an untrusted config file
```

Trust is per-file: editing a config revokes it. The plugin's `on_session_start`
hook trusts the nearest config automatically on fresh sessions.

## Standard pattern (when NOT auto-activated)

```bash
cd /path/to/repo \
  && eval "$(mise activate bash)" \
  && your_command_here
```

## Pitfalls

- **Activation is per-shell.** Each `terminal()` call is a fresh shell, which is
  why this plugin prepends activation to every one. If you bypass the hook,
  chain activation into each command yourself.
- **Working directory matters.** mise resolves versions by walking from cwd
  upward. Always `cd` into the repo first, or use `mise exec -C <dir>`.
- **Prefer binary builds.** Many tools (ruby, python, node, etc.) can compile
  from source, which is slow and often fails on missing headers. Prefer
  prebuilt binaries where available to avoid multi-minute compiles and broken
  native extensions.
- **`bundle`/`bundler` are NOT mise tools.** They ship with Ruby via RubyGems;
  don't add them to `[tools]`.
