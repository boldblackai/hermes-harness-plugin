"""Mise auto-activation hook.

This is the "automatic" half of the plugin: a ``pre_tool_call`` hook that
rewrites every ``terminal`` tool call so the command runs inside an activated
mise shell. It mirrors the behaviour of `pi-mise
<https://github.com/capotej/pi-mise>`_ (the same idea for the ``pi`` coding
agent): when mise is present *and* a mise config file exists in the working
tree, prepend ``eval "$(mise activate bash)"`` to the command.

Activation is **transparent and idempotent** — it never double-wraps a command
that is already activated, and it silently no-ops when mise is missing or no
config is found.

Environment overrides
---------------------
``HERMES_HARNESS_MISE_ALWAYS=1``
    Force activation on every terminal call even when no config file is
    detected. Useful for harness images that ship a global mise toolchain.
``HERMES_HARNESS_MISE_DISABLE=1``
    Disable the hook entirely (e.g. to debug a command in a raw shell).
``HERMES_HARNESS_MISE_SHELL=bash|zsh|fish``
    Shell passed to ``mise activate``. Defaults to ``bash`` (the harness
    default). Override only if the terminal backend uses a different shell.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("hermes_harness_plugin.mise")

# Config filenames mise recognises, checked in priority order per directory.
_MISE_CONFIG_FILES = ("mise.toml", ".mise.toml", ".tool-versions")

# `mise activate <shell>` exports this, so its presence means the command is
# already running inside an activated mise shell.
_ACTIVATE_MARKER = "__MISE_EXE="


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes", "on")


def _resolve_mise() -> Optional[str]:
    """Return an absolute path to the mise binary, or ``None`` if absent."""
    found = shutil.which("mise")
    if found:
        return found
    # Fallback to well-known install locations when mise isn't on PATH yet
    # (e.g. before any shell rc has been sourced).
    candidates = (
        "/usr/local/bin/mise",
        "/opt/homebrew/bin/mise",
        os.path.expanduser("~/.local/share/mise/bin/mise"),
        os.path.expanduser("~/.local/bin/mise"),
    )
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


# Cache the resolved binary so we don't re-probe on every tool call.
_MISE_BIN: Optional[str] = None
_MISE_PROBED = False


def get_mise_bin() -> Optional[str]:
    """Cached lookup of the mise binary path."""
    global _MISE_BIN, _MISE_PROBED
    if not _MISE_PROBED:
        _MISE_BIN = _resolve_mise()
        _MISE_PROBED = True
        if _MISE_BIN:
            logger.debug("mise resolved to %s", _MISE_BIN)
        else:
            logger.debug("mise not found on PATH or known locations")
    return _MISE_BIN


def find_mise_config(directory) -> Optional[Tuple[str, Path]]:
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
    shell = os.getenv("HERMES_HARNESS_MISE_SHELL", "bash").strip() or "bash"
    return f'eval "$({mise_bin} activate {shell})"'


def compute_prefix(args: dict, mise_bin: Optional[str]) -> Optional[str]:
    """Decide whether to prepend mise activation to this call.

    Returns the prefix string to prepend, or ``None`` to leave the command
    untouched. Pure function (no side effects) so it is trivially testable.
    """
    if mise_bin is None:
        return None
    command = args.get("command")
    if not isinstance(command, str) or not command:
        return None
    # Idempotent: don't wrap a command that is already activating mise.
    if _ACTIVATE_MARKER in command or "mise activate" in command:
        return None

    if not _env_truthy("HERMES_HARNESS_MISE_ALWAYS"):
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
    if _env_truthy("HERMES_HARNESS_MISE_DISABLE"):
        return None
    if tool_name != "terminal":
        return None
    try:
        mise_bin = get_mise_bin()
        prefix = compute_prefix(args, mise_bin)
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
        mise_bin = get_mise_bin()
        if mise_bin is None:
            return None
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
