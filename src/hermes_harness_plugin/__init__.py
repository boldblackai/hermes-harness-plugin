"""hermes-harness-plugin — optimizes Hermes Agent for harness environments.

Bundles:
  * a ``mise`` skill (loadable via ``skill_view("hermes-harness-plugin:mise")``)
  * a ``pre_tool_call`` hook that transparently activates mise for every
    ``terminal`` command when a mise config file is present in the working tree
  * a ``pre_llm_call`` hook that injects the contents of ``~/.hermes/context.md``
    as additional context into every LLM turn

The entry point in ``pyproject.toml`` points Hermes at this package; on startup
Hermes imports it and calls :func:`register`.
"""

import logging
from pathlib import Path

from . import context, mise

__version__ = "0.1.0"

logger = logging.getLogger("hermes_harness_plugin")


def register(ctx):
    """Wire up the bundled skill and lifecycle hooks.

    Called exactly once at startup by the Hermes plugin loader.
    """
    _skills_dir = Path(__file__).parent / "skills"
    for child in sorted(_skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            try:
                ctx.register_skill(child.name, skill_md)
                logger.debug("registered skill: %s", child.name)
            except Exception as exc:
                logger.warning("could not register skill %s: %s", child.name, exc)

    # Transparent mise activation for every terminal command.
    ctx.register_hook("pre_tool_call", mise.pre_tool_call)
    # Trust the nearest mise config up front so activation is frictionless.
    ctx.register_hook("on_session_start", mise.on_session_start)
    # Inject persistent context from a user-editable file into every turn.
    ctx.register_hook("pre_llm_call", context.pre_llm_call)

    logger.info(
        "hermes-harness-plugin registered "
        "(skill: mise, hooks: pre_tool_call/on_session_start/pre_llm_call)"
    )
