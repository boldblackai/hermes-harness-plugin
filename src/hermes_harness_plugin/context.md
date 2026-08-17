# Harness

You are running inside of a [harness](https://github.com/boldblackai/harness) container.

# Persistence

- Only the following directories survive container restarts:
    - `$HERMES_HOME`
    - `$HOME/.config`
    - `$HOME/.local/share/mise`
    - `$HOME/.local/state/mise`
- Do not store anything outside of the above directories (unless it's
  meant to be temporary).

You have access to `git`, `gh`, `uv`, `node`, and `pnpm`. Install all tools
through `mise` where possible — see the `hermes-harness-plugin:mise` skill
for details.
