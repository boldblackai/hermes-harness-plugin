"""Tests for the pre_llm_call context-injection hook logic."""

from __future__ import annotations

from pathlib import Path

from hermes_harness_plugin import context


# --------------------------------------------------------------------------- #
# resolve_context_path
# --------------------------------------------------------------------------- #
class TestResolveContextPath:
    def test_points_at_bundled_file(self):
        path = context.resolve_context_path()
        assert path.name == "context.md"
        # Lives inside the package directory.
        assert path.parent == Path(context.__file__).parent

    def test_bundled_file_exists(self):
        assert context.resolve_context_path().is_file()


# --------------------------------------------------------------------------- #
# pre_llm_call: the injection contract
# --------------------------------------------------------------------------- #
class TestPreLlmCallHook:
    def test_injects_bundled_context(self):
        """The hook reads the real bundled context.md and returns it."""
        result = context.pre_llm_call(
            session_id="s1",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
        )
        assert result is not None
        assert "context" in result
        assert isinstance(result["context"], str)
        assert len(result["context"]) > 0

    def test_strips_trailing_whitespace(self, tmp_path, monkeypatch):
        fake = tmp_path / "context.md"
        fake.write_text("hello\n\n\n")
        monkeypatch.setattr(context, "resolve_context_path", lambda: fake)
        result = context.pre_llm_call(session_id="s1", user_message="hi")
        assert result == {"context": "hello"}

    def test_never_raises_on_io_error(self, monkeypatch):
        """A broken hook must not break the agent loop."""
        monkeypatch.setattr(
            context,
            "resolve_context_path",
            lambda: Path("/nonexistent/dir/ctx.md"),
        )
        assert context.pre_llm_call(session_id="s1", user_message="hi") is None

    def test_accepts_extra_kwargs(self):
        """Forward-compat: unknown kwargs must not break the hook."""
        result = context.pre_llm_call(
            session_id="s1",
            user_message="hi",
            conversation_history=[],
            is_first_turn=False,
            model="gpt-4",
            platform="cli",
            future_unknown_arg="whatever",
        )
        assert result is not None
