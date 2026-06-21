"""Mise auto-activation hook.

This is the "automatic" half of the plugin: a ``pre_tool_call`` hook that
rewrites every ``terminal`` tool call so the command runs inside an activated
mise shell. It mirrors the behaviour of `pi-mise
<https://github.com/capotej/pi-mise>`_ (the same idea for the ``pi`` coding
agent): when a mise config file exists in the working tree, prepend
``eval "$(mise activate bash)"`` to the command.

Harness images always install mise at a fixed path (``_MISE_BIN``), so the hook
uses that path directly — it never tries to *detect* or resolve mise from PATH.

Activation is **transparent and idempotent** — it never double-wraps a command
that is already activated, and it silently no-ops when no config is found.
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("hermes_harness_plugin.mise")

# Config filenames mise recognises, checked in priority order per directory.
_MISE_CONFIG_FILES = ("mise.toml", ".mise.toml", ".tool-versions")

# `mise activate <shell>` exports this, so its presence means the command is
# already running inside an activated mise shell.
_ACTIVATE_MARKER = "__MISE_EXE="

# Harness images always install mise at this path.
_MISE_BIN = "/usr/local/bin/mise"


def find_mise_config(directory) -> tuple[str, Path] | None:
    """Walk up from *directory* to the nearest mise config file.

    Returns ``(filename, absolute_path)`` or ``None``. Mirrors mise's own
    resolution: it searches the current directory and every parent.
    """
    start = Path(directory).resolve()
    for cur in (start, *start.parents):
        for fname in _MISE_CONFIG_FILES:
            candidate = cur / fname
            if candidate.is_file():
                return fname, candidate
        if cur == cur.parent:  # reached filesystem root
            break
    return None


def _activation_prefix(mise_bin: str) -> str:
    return f'eval "$({mise_bin} activate bash)"'


def compute_prefix(args: dict, mise_bin: str) -> str | None:
    """Decide whether to prepend mise activation to this call.

    Returns the prefix string to prepend, or ``None`` to leave the command
    untouched. Pure function (no side effects) so it is trivially testable.
    """
    command = args.get("command")
    if not isinstance(command, str) or not command:
        return None
    # Idempotent: don't wrap a command that is already activating mise.
    if _ACTIVATE_MARKER in command or "mise activate" in command:
        return None

    where = args.get("workdir") or os.getcwd()
    if find_mise_config(where) is None:
        return None
    return _activation_prefix(mise_bin)


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #
def pre_tool_call(tool_name: str, args: dict, task_id: str = "", **kwargs):
    """``pre_tool_call`` hook — prepend mise activation to terminal commands.

    Mutates ``args["command"]`` in place. Hermes dispatches the *same* dict
    object that is passed to this hook on to the terminal handler, so the
    rewrite takes effect for the actual command execution.
    """
    if tool_name != "terminal":
        return None
    try:
        prefix = compute_prefix(args, _MISE_BIN)
        if prefix is None:
            return None
        command = args["command"]
        args["command"] = f"{prefix} && {command}"
        logger.debug("mise: activated terminal command (task=%s)", task_id)
    except Exception as exc:  # never break the agent loop
        logger.warning("hermes-harness-plugin mise hook error: %s", exc)
    return None


def on_session_start(session_id: str = "", model: str = "", platform: str = "", **kwargs):
    """Best-effort: trust the nearest mise config so activation won't prompt.

    Untrusted config files cause mise to refuse to load tools until the user
    runs ``mise trust``. We do it up front, idempotently.
    """
    try:
        mise_bin = _MISE_BIN
        found = find_mise_config(os.getcwd())
        if found is None:
            return None
        _fname, path = found
        subprocess.run(
            [mise_bin, "trust", str(path)],
            cwd=str(path.parent),
            capture_output=True,
            timeout=15,
            check=False,
        )
        logger.debug("mise: trusted %s", path)
    except Exception as exc:
        logger.debug("mise trust skipped: %s", exc)
    return None
