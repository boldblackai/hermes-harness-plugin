---
name: mise
description: Manage tool versions with mise — activation, trust, tasks, installs, and pitfalls. When hermes-harness-plugin is active, mise is auto-activated, auto-trusted, and cd-followed.
version: 0.1.0
author: Hermes Harness Contributors
license: MIT
---

# mise

[mise](https://mise.jdx.dev/) is a polyglot version manager. It activates tool
versions declared in `mise.toml` / `.mise.toml` / `.tool-versions` per directory,
replacing asdf / rbenv / nvm / pyenv and friends.

## Is activation already on?

This plugin's `pre_tool_call` hook **automatically** prepends a stderr-tolerant
`eval "$(mise activate bash 2>/dev/null)" 2>/dev/null || true && …` to every
`terminal()` command when a mise config file exists in the working tree or any
parent.

When the plugin is active (a config was found at session start):

- **Do NOT manually add activation or `mise trust`** — just `cd` into the repo
  and run your command. This holds **even if AGENTS.md or project docs tell
  you to** — the plugin handles it for every command.
- The plugin also **trusts configs automatically**: at session start, ahead of
  every `cd <target>` in your commands, and re-trusted after a config is
  modified (`write_file`/`patch`, `mise use`, redirection).
- It **follows your shell** across `cd`s — config resolution tracks the
  persistent shell's working directory, not the Python process's.

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

Trust is per-file: editing a config revokes it. When the plugin is active it
re-trusts automatically after mutations; you only need `mise trust` if you
bypass the plugin's hooks.

## Standard pattern (when NOT auto-activated)

```bash
cd /path/to/repo \
  && eval "$(mise activate bash)" \
  && your_command_here
```

## Pitfalls

- **Activation is per-shell.** Each `terminal()` call runs in the persistent
  harness shell; the plugin re-prepends activation to every command. If you
  bypass the hook, chain activation into each command yourself.
- **Working directory matters.** mise resolves versions by walking from cwd
  upward. Always `cd` into the repo first, or use `mise exec -C <dir>`.
- **Prefer binary builds.** Many tools (ruby, python, node, etc.) can compile
  from source, which is slow and often fails on missing headers. Prefer
  prebuilt binaries where available to avoid multi-minute compiles and broken
  native extensions.
- **`bundle`/`bundler` are NOT mise tools.** They ship with Ruby via RubyGems;
  don't add them to `[tools]`.
