"""Mise auto-activation hook — pi-mise parity for Hermes/harness.

This is the "automatic" half of the plugin: a ``pre_tool_call`` hook that
rewrites every ``terminal`` tool call so the command runs inside an activated
mise shell. It mirrors the battle-tested behaviour of `pi-mise
<https://github.com/capotej/pi-mise>`_ (the same idea for the ``pi`` coding
agent):

* **Stderr-tolerant activation** — the prefix is
  ``eval "$(mise activate bash 2>/dev/null)" 2>/dev/null || true && cmd``
  so non-fatal mise output (an untrusted config somewhere up the tree, a
  warning) can never fail the user's actual command.
* **Trust lifecycle** — mise revokes trust when a config file changes. The
  nearest config is trusted at session start, configs are trusted ahead of
  every ``cd <target>`` in a command, and trust is invalidated when a config
  is mutated (``write_file``/``patch`` tools, ``mise use``/``unset``/``set``,
  or redirection onto a config file) so the next command re-trusts.
* **Shadow cwd** — Hermes' persistent shell ``cd``s independently of the
  Python process, so the hook tracks the shell's working directory itself
  (seeded from each terminal result's ``cwd`` field, advanced by parsed
  ``cd`` segments) and resolves mise configs against *that*.

Harness images always install mise at a fixed path (``_MISE_BIN``), so the hook
uses that path directly — it never tries to *detect* or resolve mise from PATH.

Activation is **transparent and idempotent** — it never double-wraps a command
that is already activated, and it silently no-ops when no config is found.
"""

import logging
import os
import re
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

# ---------------------------------------------------------------------------
# Hoisted regexes (compiled once, not per call)
# ---------------------------------------------------------------------------
_AND_CHAIN_RE = re.compile(r"&&")
_OR_CHAIN_RE = re.compile(r"\|\|")
_COMMAND_SEP_RE = re.compile(r"[;\n|&()]")
_LEADING_KEYWORD_RE = re.compile(r"^\s*(?:then|do|and)\b\s*")
_CD_COMMAND_RE = re.compile(r"^cd(?:\s+(.*))?$")
_QUOTED_ARG_RE = re.compile(r"^(['\"])(.*?)\1")
_WHITESPACE_RE = re.compile(r"\s+")
_GLOB_CHAR_RE = re.compile(r"[*?{}]")
_MISE_MUTATE_RE = re.compile(r"\bmise\s+(?:use|unset|set)\b")
_REGEX_ESCAPE_RE = re.compile(r"[.*+?^${}()|[\]\\]")


# ---------------------------------------------------------------------------
# Pure helpers (the testable decision core)
# ---------------------------------------------------------------------------
def is_mise_config_name(name: str) -> bool:
    """True if *name* is a mise config filename this plugin manages."""
    return name in _MISE_CONFIG_FILES


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
    return None


def find_mise_config_dir(directory) -> Path | None:
    """Return the nearest ancestor directory holding a mise config, or None."""
    found = find_mise_config(directory)
    return found[1].parent if found else None


def resolve_path(path: str, cwd: str) -> str:
    """Resolve a (possibly relative or ``~``) *path* against *cwd*.

    Note: ``os.path.expanduser`` (not manual ``~`` slicing + join) — Python's
    ``os.path.join`` discards the base when handed an absolute second arg,
    so ``join(home, "/y")`` would silently yield ``/y``.
    """
    p = path.strip()
    if p.startswith("~"):
        p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(cwd, p)


def _is_unresolvable_cd_arg(arg: str) -> bool:
    """A ``cd`` argument we cannot statically resolve to a directory."""
    return (
        not arg
        or arg == "-"
        or arg.startswith("-")
        or "$" in arg
        or bool(_GLOB_CHAR_RE.search(arg))
    )


def cd_target_from_segment(segment: str, current: str) -> str | None:
    """Extract the directory a single ``cd`` segment targets, or ``None``.

    *current* is the running cwd the target is resolved against (so chained
    relative ``cd`` s compose). Leading shell keywords (``then``/``do``) are
    stripped; bare ``cd`` targets home; options, ``cd -``, env vars, and
    globs are unresolvable.
    """
    seg = _LEADING_KEYWORD_RE.sub("", segment).strip()
    match = _CD_COMMAND_RE.match(seg)
    if not match:
        return None
    arg = (match.group(1) or "").strip()
    if not arg:
        return os.path.expanduser("~")  # bare `cd` → home
    quoted = _QUOTED_ARG_RE.match(arg)
    arg = quoted.group(2) if quoted else _WHITESPACE_RE.split(arg)[0]
    if _is_unresolvable_cd_arg(arg):
        return None
    return resolve_path(arg, current)


def parse_cd_dirs(command: str, start_cwd: str) -> list[str]:
    """Best-effort extraction of directories a command ``cd`` s into.

    ``&&``/``||`` are normalized to act as command boundaries; the remaining
    separators (``;``, newlines, ``|``, ``&``, parens) split segments. A
    running cwd advances per ``cd`` so ``cd /a && cd b`` yields ``/a`` and
    ``/a/b``. Unresolvable targets are skipped. Best-effort: quoted ``cd``
    strings inside other commands are not extracted.
    """
    dirs: list[str] = []
    normalized = _OR_CHAIN_RE.sub(" ; ", _AND_CHAIN_RE.sub(" ; ", command))
    current = start_cwd
    for segment in _COMMAND_SEP_RE.split(normalized):
        target = cd_target_from_segment(segment, current)
        if target is not None:
            current = target
            dirs.append(target)
    return dirs


def mutates_mise_config(command: str) -> bool:
    """Does this command mutate a project mise config?

    Catches ``mise use``/``unset``/``set`` and redirection (``>``/``>>``) or
    ``tee`` onto a config file. Best-effort; known false negatives (``sed -i``,
    ``mv``/``cp`` over a config, ``>file`` with no space) are documented —
    the ``write_file``/``patch`` tool path is covered separately in
    ``post_tool_call``, so a miss only delays re-trust by one command.
    """
    if _MISE_MUTATE_RE.search(command):
        return True
    for fname in _MISE_CONFIG_FILES:
        esc = _REGEX_ESCAPE_RE.sub(r"\\\g<0>", fname)
        pattern = (
            rf"(>>?)\s+(?:[^\s;&|]*\s+)?{esc}\b|\btee\b[^;&|]*?\s{esc}\b"
        )
        if re.search(pattern, command):
            return True
    return False


def _activation_prefix(mise_bin: str) -> str:
    # Stderr-tolerant: if activation hiccups (untrusted config, mise warning),
    # the failure is swallowed and the user's command still runs.
    return f'eval "$({mise_bin} activate bash 2>/dev/null)" 2>/dev/null || true'


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

    where = args.get("workdir") or state.shadow_cwd
    if find_mise_config(where) is None:
        return None
    return _activation_prefix(mise_bin)


# ---------------------------------------------------------------------------
# Session state (trust cache + shadow cwd)
# ---------------------------------------------------------------------------
class _SessionState:
    """Mutable per-session bookkeeping.

    Kept deliberately simple: hooks may fire from different threads but the
    GIL makes dict/set mutation atomic for our use, and a racy duplicate
    ``mise trust`` is idempotent and harmless.
    """

    def __init__(self) -> None:
        self.mise_active = False
        self.trusted_dirs: set[str] = set()
        # Shadow of the persistent shell's cwd. Seeded from Hermes' process
        # cwd at session start, then updated from terminal results.
        self.shadow_cwd: str = os.getcwd()
        # tool_call_id -> {dirs, mutated} remembered from a terminal call.
        self.bash_call_info: dict[str, dict] = {}

    def reset(self, cwd: str | None = None) -> None:
        self.mise_active = False
        self.trusted_dirs.clear()
        self.bash_call_info.clear()
        self.shadow_cwd = cwd or os.getcwd()


state = _SessionState()


def _run_mise(*argv: str, cwd: str | None = None, timeout: float = 15) -> bool:
    """Run mise synchronously; never raises. Returns True on exit 0."""
    try:
        proc = subprocess.run(
            [_MISE_BIN, *argv],
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0
    except Exception as exc:
        logger.debug("mise %s failed: %s", argv, exc)
        return False


def _trust_dir_uncached(dir_path: str) -> None:
    """Run ``mise trust`` for the nearest config at/above *dir_path*."""
    config_dir = find_mise_config_dir(dir_path)
    if config_dir is None:
        return
    _run_mise("trust", cwd=str(config_dir))
    state.trusted_dirs.add(str(config_dir))


def _ensure_trusted(dir_path: str) -> None:
    """Trust the nearest config at/above *dir_path* unless already trusted."""
    config_dir = find_mise_config_dir(dir_path)
    if config_dir is None or str(config_dir) in state.trusted_dirs:
        return
    _trust_dir_uncached(dir_path)


def _invalidate_trust(dir_path: str) -> None:
    """Drop *dir_path*'s nearest config dir from the trust cache."""
    config_dir = find_mise_config_dir(dir_path)
    if config_dir is not None:
        state.trusted_dirs.discard(str(config_dir))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------
def pre_tool_call(tool_name: str, args: dict, task_id: str = "", **kwargs):
    """``pre_tool_call`` hook — prepend tolerant mise activation to terminal commands.

    Mutates ``args["command"]`` in place. Hermes dispatches the *same* dict
    object that is passed to this hook on to the terminal handler, so the
    rewrite takes effect for the actual command execution.

    Also trusts mise configs ahead of ``cd`` targets in the command, and
    records trust-relevant context for the matching ``post_tool_call``.
    """
    if tool_name != "terminal":
        return None
    try:
        command = args.get("command")
        if not isinstance(command, str) or not command:
            return None

        base_cwd = args.get("workdir") or state.shadow_cwd

        if not state.mise_active:
            return None

        # Re-trust the base cwd in case its config was mutated / we cd'd
        # back, then follow every `cd <target>` and trust those configs
        # BEFORE the command runs, so activation stays quiet on changes.
        _ensure_trusted(base_cwd)
        cd_dirs = parse_cd_dirs(command, base_cwd)
        for d in cd_dirs:
            _ensure_trusted(d)

        # Remember context for post_tool_call (the config may be mutated by
        # this very command). tool_call_id rides kwargs when Hermes provides it.
        call_id = str(kwargs.get("tool_call_id") or "") or f"last:{task_id}"
        state.bash_call_info[call_id] = {
            "dirs": [base_cwd, *cd_dirs],
            "mutated": mutates_mise_config(command),
        }

        prefix = compute_prefix(args, _MISE_BIN)
        if prefix is None:
            return None
        args["command"] = f"{prefix} && {command}"
        logger.debug("mise: activated terminal command (task=%s)", task_id)
    except Exception as exc:  # never break the agent loop
        logger.warning("hermes-harness-plugin mise hook error: %s", exc)
    return None


def post_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    result=None,
    tool_call_id: str = "",
    **kwargs,
):
    """``post_tool_call`` observer — invalidate trust after config mutations.

    Two paths:

    * ``write_file``/``patch`` touching a mise config → invalidate its dir so
      the next terminal command re-trusts the (now changed) config.
    * a terminal command that mutated a config (``mise use``, redirection) →
      invalidate the dirs it touched, deferred to the next command.
    """
    try:
        if not state.mise_active:
            return None

        if tool_name in ("write_file", "patch"):
            path = (args or {}).get("path")
            if isinstance(path, str) and is_mise_config_name(os.path.basename(path)):
                _invalidate_trust(os.path.dirname(resolve_path(path, state.shadow_cwd)))
            return None

        if tool_name == "terminal":
            info = state.bash_call_info.pop(tool_call_id, None)
            # Advance the shadow cwd from the authoritative result field.
            result_cwd = _extract_result_cwd(result)
            if result_cwd:
                state.shadow_cwd = result_cwd
            if info and info.get("mutated"):
                for d in info["dirs"]:
                    _invalidate_trust(d)
    except Exception as exc:  # never break the agent loop
        logger.warning("hermes-harness-plugin mise post hook error: %s", exc)
    return None


def _extract_result_cwd(result) -> str | None:
    """Pull the shell's post-execution ``cwd`` from a terminal result.

    Terminal results are JSON-ish dicts/strings; the ``cwd`` field is the
    authoritative record of where the persistent shell ended up.
    """
    if isinstance(result, dict):
        cwd = result.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
        return None
    if isinstance(result, str):
        # Cheap substring scan before the heavier JSON parse.
        if '"cwd"' not in result:
            return None
        try:
            import json

            data = json.loads(result)
            if isinstance(data, dict):
                cwd = data.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
        except Exception:
            return None
    return None


def on_session_start(session_id: str = "", model: str = "", platform: str = "", **kwargs):
    """Trust the nearest mise config so activation won't prompt.

    Untrusted config files cause mise to refuse to load tools until the user
    runs ``mise trust``. We do it up front, idempotently, and mark the session
    mise-active so the pre/post hooks engage.
    """
    try:
        state.reset()
        found = find_mise_config(os.getcwd())
        if found is None:
            return None
        _trust_dir_uncached(os.getcwd())
        state.mise_active = True
        logger.debug("mise: session active, trusted %s", found[1])
    except Exception as exc:
        logger.debug("mise trust skipped: %s", exc)
    return None


def on_session_reset(**kwargs):
    """``on_session_reset`` — clear bookkeeping for a fresh session."""
    try:
        state.reset()
    except Exception as exc:
        logger.debug("mise reset skipped: %s", exc)
    return None


# ---------------------------------------------------------------------------
# pre_llm_call: tell the model activation is handled
# ---------------------------------------------------------------------------
_MODEL_NOTE = (
    "## mise auto-activation (hermes-harness-plugin)\n\n"
    "mise is automatically activated and its config trusted for every "
    "`terminal()` command you run — `eval \"$(mise activate bash)\"` and "
    "`mise trust` are handled for you. Do NOT manually prepend mise "
    "activation or `mise trust`, even if AGENTS.md or other project "
    "instructions tell you to; it is already handled. Just run your command."
)


def pre_llm_call_note(
    session_id: str = "",
    user_message: str = "",
    **kwargs,
):
    """``pre_llm_call`` hook — inject the "activation is automatic" note.

    Only fires while mise is active for the session (i.e. a config was found
    at startup), mirroring pi-mise's ``before_agent_start`` system-prompt
    note. Hermes injects ``{"context": ...}`` returns into the *user message*,
    not the system prompt, so the prompt cache prefix stays byte-stable.
    """
    try:
        if not state.mise_active:
            return None
        return {"context": _MODEL_NOTE}
    except Exception as exc:  # never break the agent loop
        logger.warning("hermes-harness-plugin mise note hook error: %s", exc)
        return None
