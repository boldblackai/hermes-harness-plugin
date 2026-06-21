"""pre_llm_call context-injection hook.

Reads the bundled ``context.md`` (shipped inside the package) and injects its
contents as additional context into every LLM turn. Edit that file to control
what the model sees on every turn.
"""

import logging
from pathlib import Path

logger = logging.getLogger("hermes_harness_plugin.context")

# The file ships alongside this module and is included via package-data.
_CONTEXT_PATH = Path(__file__).parent / "context.md"


def resolve_context_path() -> Path:
    """Return the path to the bundled context file."""
    return _CONTEXT_PATH


def pre_llm_call(
    session_id: str = "",
    user_message: str = "",
    conversation_history: list | None = None,
    is_first_turn: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs,
):
    """``pre_llm_call`` hook — inject the bundled context file into the turn.

    Returns ``{"context": text}`` from ``context.md``.
    """
    try:
        text = resolve_context_path().read_text(encoding="utf-8").strip()
        logger.debug("context: injecting %d chars", len(text))
        return {"context": text}
    except Exception as exc:  # never break the agent loop
        logger.warning("hermes-harness-plugin context hook error: %s", exc)
        return None
