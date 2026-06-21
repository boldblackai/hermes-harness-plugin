"""Tests for the mise auto-activation hook logic.

These exercise the pure helpers (prefix computation) and the end-to-end
in-place mutation contract that the real Hermes dispatcher relies on: the same
``args`` dict passed to ``pre_tool_call`` is what the terminal handler later
reads.
"""

from pathlib import Path

import pytest

from hermes_harness_plugin import mise


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env overrides don't leak between tests."""
    monkeypatch.delenv("HERMES_HARNESS_MISE_ALWAYS", raising=False)
    # Reset the cached binary path so tests are hermetic.
    mise._MISE_BIN = None
    yield


@pytest.fixture
def fake_mise(monkeypatch):
    """Pretend mise lives at a fixed path."""
    monkeypatch.setattr(mise, "get_mise_bin", lambda: "/usr/local/bin/mise")
    return "/usr/local/bin/mise"


@pytest.fixture
def repo_with_config(tmp_path):
    """A tmp repo that contains a mise.toml at its root."""
    (tmp_path / "mise.toml").write_text("[tools]\nnode = '22'\n")
    return tmp_path


# --------------------------------------------------------------------------- #
# compute_prefix / should-activate logic
# --------------------------------------------------------------------------- #
class TestComputePrefix:
    def test_no_command_returns_none(self, fake_mise):
        assert mise.compute_prefix({}, fake_mise) is None
        assert mise.compute_prefix({"command": ""}, fake_mise) is None

    def test_no_config_no_force_returns_none(self, fake_mise, tmp_path, monkeypatch):
        # cwd has no config file; not forced
        monkeypatch.chdir(tmp_path)
        assert mise.compute_prefix({"command": "ls"}, fake_mise) is None

    def test_force_always_activates_without_config(self, fake_mise, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HERMES_HARNESS_MISE_ALWAYS", "1")
        prefix = mise.compute_prefix({"command": "ls"}, fake_mise)
        assert prefix is not None
        assert "mise activate bash" in prefix
        assert fake_mise in prefix

    def test_config_present_activates(self, fake_mise, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        prefix = mise.compute_prefix({"command": "bundle install"}, fake_mise)
        assert prefix is not None
        assert "mise activate bash" in prefix

    def test_workdir_arg_used_for_config_lookup(
        self, fake_mise, repo_with_config, tmp_path, monkeypatch
    ):
        # cwd has no config, but workdir points at a repo that does
        monkeypatch.chdir(tmp_path)
        args = {"command": "ls", "workdir": str(repo_with_config)}
        assert mise.compute_prefix(args, fake_mise) is not None


class TestIdempotence:
    def test_skips_when_already_activated_marker(self, fake_mise, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        cmd = 'eval "$(mise activate bash)" && echo hi'
        # both the marker substring and the literal phrase should be detected
        for already in (cmd, "export __MISE_EXE=/x"):
            assert mise.compute_prefix({"command": already}, fake_mise) is None


# --------------------------------------------------------------------------- #
# find_mise_config walks up the tree
# --------------------------------------------------------------------------- #
class TestFindMiseConfig:
    def test_finds_in_parent(self, repo_with_config):
        sub = repo_with_config / "src" / "deep"
        sub.mkdir(parents=True)
        fname, path = mise.find_mise_config(sub)
        assert fname == "mise.toml"
        assert path == repo_with_config / "mise.toml"

    def test_prefers_mise_toml(self, tmp_path):
        (tmp_path / ".tool-versions").write_text("node 22\n")
        (tmp_path / "mise.toml").write_text("[tools]\n")
        fname, _ = mise.find_mise_config(tmp_path)
        assert fname == "mise.toml"

    def test_returns_none_when_absent(self, tmp_path):
        assert mise.find_mise_config(tmp_path) is None


# --------------------------------------------------------------------------- #
# pre_tool_call: the in-place mutation contract
# --------------------------------------------------------------------------- #
class TestPreToolCallHook:
    def _capture_handler_args(self, args_after):
        """Mimic what the real terminal handler would see."""
        return args_after["command"]

    def test_mutates_command_in_place(self, fake_mise, repo_with_config, monkeypatch):
        """The same dict object passed to the hook must carry the rewrite."""
        monkeypatch.chdir(repo_with_config)
        args = {"command": "bundle install"}
        identity_before = id(args)
        mise.pre_tool_call("terminal", args)
        assert id(args) == identity_before  # same object
        cmd = self._capture_handler_args(args)
        assert cmd.startswith('eval "$(/usr/local/bin/mise activate bash)"')
        assert cmd.endswith("bundle install")

    def test_ignores_non_terminal_tools(self, fake_mise, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        args = {"command": "ls"}
        mise.pre_tool_call("read_file", args)
        assert args == {"command": "ls"}  # untouched

    def test_no_config_is_noop(self, fake_mise, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = {"command": "ls"}
        mise.pre_tool_call("terminal", args)
        assert args["command"] == "ls"

    def test_never_raises_on_bad_args(self, fake_mise, monkeypatch):
        """A broken hook must not break the agent loop."""
        monkeypatch.setenv("HERMES_HARNESS_MISE_ALWAYS", "1")
        # args without a command key — should not raise
        mise.pre_tool_call("terminal", {})


# --------------------------------------------------------------------------- #
# on_session_start: auto-trust the nearest config
# --------------------------------------------------------------------------- #
class TestOnSessionStartHook:
    def _capture_run(self, monkeypatch):
        calls = []

        def _fake_run(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(mise.subprocess, "run", _fake_run)
        return calls

    def test_trusts_nearest_config(self, fake_mise, repo_with_config, monkeypatch):
        """Runs `mise trust` on the nearest config when one is present."""
        monkeypatch.chdir(repo_with_config)
        calls = self._capture_run(monkeypatch)
        mise.on_session_start("session-1")
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == ["/usr/local/bin/mise", "trust", str(repo_with_config / "mise.toml")]
        assert kwargs["cwd"] == str(repo_with_config)
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False

    def test_noop_when_no_config(self, fake_mise, tmp_path, monkeypatch):
        """Does not invoke subprocess when no config is found."""
        monkeypatch.chdir(tmp_path)
        calls = self._capture_run(monkeypatch)
        mise.on_session_start("session-1")
        assert calls == []

    def test_never_raises_on_subprocess_error(self, fake_mise, repo_with_config, monkeypatch):
        """A failing subprocess must not break the agent loop."""
        monkeypatch.chdir(repo_with_config)

        def _raise(*a, **kw):
            raise FileNotFoundError("mise not found")

        monkeypatch.setattr(mise.subprocess, "run", _raise)
        mise.on_session_start("session-1")  # should not raise


# --------------------------------------------------------------------------- #
# get_mise_bin: resolution is a PATH lookup, not an install probe
# --------------------------------------------------------------------------- #
class TestGetMiseBin:
    def test_resolves_via_which(self, monkeypatch):
        monkeypatch.setattr(mise.shutil, "which", lambda name: "/usr/local/bin/mise")
        assert mise.get_mise_bin() == "/usr/local/bin/mise"

    def test_falls_back_to_bare_name(self, monkeypatch):
        monkeypatch.setattr(mise.shutil, "which", lambda name: None)
        assert mise.get_mise_bin() == "mise"

    def test_cached(self, monkeypatch):
        calls = {"n": 0}

        def _which(name):
            calls["n"] += 1
            return "/usr/local/bin/mise"

        monkeypatch.setattr(mise.shutil, "which", _which)
        mise.get_mise_bin()
        mise.get_mise_bin()
        assert calls["n"] == 1  # only resolved once


# --------------------------------------------------------------------------- #
# Packaging: the bundled skill is discoverable relative to the package
# --------------------------------------------------------------------------- #
class TestPackaging:
    def test_skill_md_present_in_package(self):
        import hermes_harness_plugin as pkg

        skill_md = Path(pkg.__file__).parent / "skills" / "mise" / "SKILL.md"
        assert skill_md.is_file(), f"missing bundled skill at {skill_md}"
        text = skill_md.read_text()
        assert text.lstrip().startswith("---")
        assert "name: mise" in text

    def test_plugin_yaml_present(self):
        import hermes_harness_plugin as pkg

        assert (Path(pkg.__file__).parent / "plugin.yaml").is_file()


# --------------------------------------------------------------------------- #
# register(): wires up skills + hooks via the ctx object
# --------------------------------------------------------------------------- #
class _FakeCtx:
    def __init__(self, fail_skill=False):
        self.skills = []
        self.hooks = []
        self._fail_skill = fail_skill

    def register_skill(self, name, path):
        if self._fail_skill:
            raise RuntimeError("boom")
        self.skills.append((name, str(path)))

    def register_hook(self, event, fn):
        self.hooks.append((event, fn))


class TestRegister:
    def test_registers_skill_and_hooks(self):
        from hermes_harness_plugin import context, register

        ctx = _FakeCtx()
        register(ctx)

        skill_names = [name for name, _ in ctx.skills]
        assert "mise" in skill_names

        hook_events = [event for event, _ in ctx.hooks]
        assert hook_events == ["pre_tool_call", "on_session_start", "pre_llm_call"]

        hook_fns = {event: fn for event, fn in ctx.hooks}
        from hermes_harness_plugin import mise as mise_mod
        assert hook_fns["pre_tool_call"] == mise_mod.pre_tool_call
        assert hook_fns["pre_llm_call"] == context.pre_llm_call

    def test_continues_on_skill_registration_error(self):
        """A broken skill registration must not prevent hooks from registering."""
        from hermes_harness_plugin import register

        ctx = _FakeCtx(fail_skill=True)
        register(ctx)  # should not raise
        assert len(ctx.hooks) == 3
