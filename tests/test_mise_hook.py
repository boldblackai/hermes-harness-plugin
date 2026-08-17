"""Tests for the mise auto-activation hook logic.

Ports the pi-mise test suite (https://github.com/capotej/pi-mise) — pure
parser tests, hook-contract tests, and an integration suite that drives the
real mise binary — plus Hermes-specific contracts (same-dict mutation,
shadow cwd from terminal results, trust invalidation from write tools).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_harness_plugin import mise
from hermes_harness_plugin.mise import state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_mise():
    """The hardcoded mise path (matches _MISE_BIN in mise.py)."""
    return "/usr/local/bin/mise"


@pytest.fixture
def repo_with_config(tmp_path):
    """A tmp repo that contains a mise.toml at its root."""
    (tmp_path / "mise.toml").write_text("[tools]\nnode = '22'\n")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_state():
    """Isolate session state between tests."""
    state.reset()
    yield
    state.reset()


def mise_available() -> bool:
    return Path(mise._MISE_BIN).is_file() and os.access(mise._MISE_BIN, os.X_OK)


def capture_trust(monkeypatch):
    """Patch _run_mise so `mise trust` invocations are recorded, not run."""
    calls: list[str] = []

    def fake_run(*argv, cwd=None, timeout=15):
        if argv and argv[0] == "trust":
            calls.append(str(cwd))
        return True

    monkeypatch.setattr(mise, "_run_mise", fake_run)
    return calls


# ---------------------------------------------------------------------------
# is_mise_config_name
# ---------------------------------------------------------------------------
class TestIsMiseConfigName:
    def test_recognizes_the_three_filenames(self):
        assert mise.is_mise_config_name("mise.toml")
        assert mise.is_mise_config_name(".mise.toml")
        assert mise.is_mise_config_name(".tool-versions")

    def test_rejects_unrelated_files(self):
        assert not mise.is_mise_config_name("mise.local.toml")
        assert not mise.is_mise_config_name("README.md")
        assert not mise.is_mise_config_name(".mise")


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------
class TestResolvePath:
    def test_passes_absolute_through(self):
        assert mise.resolve_path("/a/b", "/cwd") == "/a/b"

    def test_resolves_relative_against_cwd(self):
        assert mise.resolve_path("a/b", "/cwd") == os.path.join("/cwd", "a/b")

    def test_expands_tilde(self):
        expected = os.path.join(os.path.expanduser("~"), "y")
        assert mise.resolve_path("~/y", "/cwd") == expected

    def test_trims_whitespace(self):
        assert mise.resolve_path("  /a/b  ", "/cwd") == "/a/b"


# ---------------------------------------------------------------------------
# cd_target_from_segment / parse_cd_dirs (ported from pi-mise)
# ---------------------------------------------------------------------------
class TestCdTargetFromSegment:
    def test_null_for_non_cd(self):
        assert mise.cd_target_from_segment("echo hi", "/home") is None
        assert mise.cd_target_from_segment("ls -la", "/home") is None

    def test_resolves_absolute_and_relative(self):
        assert mise.cd_target_from_segment("cd /a/b", "/home") == "/a/b"
        assert mise.cd_target_from_segment("cd b", "/a") == "/a/b"

    def test_quoted_targets_with_spaces(self):
        assert mise.cd_target_from_segment("cd 'a b'", "/x") == "/x/a b"
        assert mise.cd_target_from_segment('cd "a b"', "/x") == "/x/a b"

    def test_bare_cd_targets_home(self):
        assert mise.cd_target_from_segment("cd", "/somewhere") == os.path.expanduser("~")
        assert (
            mise.cd_target_from_segment("cd   ", "/somewhere")
            == os.path.expanduser("~")
        )

    def test_null_for_unresolvable(self):
        assert mise.cd_target_from_segment("cd -", "/h") is None
        assert mise.cd_target_from_segment("cd --foo", "/h") is None
        assert mise.cd_target_from_segment("cd $HOME", "/h") is None
        assert mise.cd_target_from_segment("cd ~/src/*", "/h") is None

    def test_strips_leading_keyword(self):
        assert mise.cd_target_from_segment("then cd /a", "/h") == "/a"
        assert mise.cd_target_from_segment("do cd /a", "/h") == "/a"


class TestParseCdDirs:
    def test_single_absolute(self):
        assert mise.parse_cd_dirs("cd /a && ls", "/home") == ["/a"]

    def test_chains_relative_against_running_cwd(self):
        assert mise.parse_cd_dirs("cd /a && cd b", "/home") == ["/a", "/a/b"]
        assert mise.parse_cd_dirs("cd /a; cd /b", "/home") == ["/a", "/b"]

    def test_does_not_extract_cd_as_argument(self):
        assert mise.parse_cd_dirs('echo "cd /x"', "/home") == []
        assert mise.parse_cd_dirs('git commit -m "cd somewhere"', "/home") == []

    def test_finds_cd_mid_pipeline_or_subshell(self):
        assert mise.parse_cd_dirs("(cd /a)", "/home") == ["/a"]
        assert mise.parse_cd_dirs("grep foo bar && cd /a", "/home") == ["/a"]
        assert mise.parse_cd_dirs("true | cd /a", "/home") == ["/a"]

    def test_skips_unresolvable_keeps_resolvable(self):
        assert mise.parse_cd_dirs("cd $HOME && cd - && cd /a", "/home") == ["/a"]


# ---------------------------------------------------------------------------
# mutates_mise_config (ported from pi-mise)
# ---------------------------------------------------------------------------
class TestMutatesMiseConfig:
    def test_detects_mise_write_subcommands(self):
        assert mise.mutates_mise_config("mise use node@20")
        assert mise.mutates_mise_config("mise unset FOO")
        assert mise.mutates_mise_config("mise set FOO=bar")

    def test_read_only_mise_commands_pass(self):
        assert not mise.mutates_mise_config("mise install node")
        assert not mise.mutates_mise_config("mise trust")
        assert not mise.mutates_mise_config("mise activate bash")
        assert not mise.mutates_mise_config("mise ls")

    def test_detects_redirection_with_space(self):
        assert mise.mutates_mise_config("echo x > mise.toml")
        assert mise.mutates_mise_config("echo x >> .tool-versions")
        assert mise.mutates_mise_config("echo x 1> .mise.toml")
        assert mise.mutates_mise_config(":> mise.toml")

    def test_detects_tee(self):
        assert mise.mutates_mise_config("echo x | tee -a mise.toml")
        assert mise.mutates_mise_config("tee .tool-versions <<< x")

    def test_unrelated_redirection_passes(self):
        assert not mise.mutates_mise_config("echo x > README.md")
        assert not mise.mutates_mise_config("cat mise.toml > /tmp/out")

    def test_documented_false_negative_no_space(self):
        # Known limitation (see mutates_mise_config docstring); the
        # write_file/patch path covers the agent's common case.
        assert not mise.mutates_mise_config("echo x >mise.toml")
        assert not mise.mutates_mise_config("echo x 2>mise.toml")


# ---------------------------------------------------------------------------
# find_mise_config walks up the tree
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# compute_prefix / should-activate logic
# ---------------------------------------------------------------------------
class TestComputePrefix:
    def test_no_command_returns_none(self, fake_mise):
        assert mise.compute_prefix({}, fake_mise) is None
        assert mise.compute_prefix({"command": ""}, fake_mise) is None

    def test_no_config_returns_none(self, fake_mise, tmp_path):
        state.shadow_cwd = str(tmp_path)
        assert mise.compute_prefix({"command": "ls"}, fake_mise) is None

    def test_config_present_activates(self, fake_mise, repo_with_config):
        state.shadow_cwd = str(repo_with_config)
        prefix = mise.compute_prefix({"command": "bundle install"}, fake_mise)
        assert prefix is not None
        assert "mise activate bash" in prefix

    def test_workdir_arg_used_for_config_lookup(
        self, fake_mise, repo_with_config, tmp_path
    ):
        state.shadow_cwd = str(tmp_path)
        args = {"command": "ls", "workdir": str(repo_with_config)}
        assert mise.compute_prefix(args, fake_mise) is not None

    def test_prefix_is_stderr_tolerant(self, fake_mise, repo_with_config):
        """pi-mise parity: activation failure must never fail the command."""
        state.shadow_cwd = str(repo_with_config)
        prefix = mise.compute_prefix({"command": "ls"}, fake_mise)
        assert prefix is not None
        assert "2>/dev/null" in prefix
        assert "|| true" in prefix


class TestIdempotence:
    def test_skips_when_already_activated_marker(self, fake_mise, repo_with_config):
        state.shadow_cwd = str(repo_with_config)
        cmd = 'eval "$(mise activate bash)" && echo hi'
        for already in (cmd, "export __MISE_EXE=/x"):
            assert mise.compute_prefix({"command": already}, fake_mise) is None


# ---------------------------------------------------------------------------
# pre_tool_call: the in-place mutation contract
# ---------------------------------------------------------------------------
class TestPreToolCallHook:
    def _activate(self, repo):
        state.reset(str(repo))
        state.mise_active = True

    def test_mutates_command_in_place(self, fake_mise, repo_with_config):
        """The same dict object passed to the hook must carry the rewrite."""
        self._activate(repo_with_config)
        args = {"command": "bundle install"}
        identity_before = id(args)
        mise.pre_tool_call("terminal", args)
        assert id(args) == identity_before  # same object
        cmd = args["command"]
        assert "mise activate bash 2>/dev/null" in cmd
        assert "|| true &&" in cmd
        assert cmd.endswith("bundle install")

    def test_ignores_non_terminal_tools(self, fake_mise, repo_with_config):
        self._activate(repo_with_config)
        args = {"command": "ls"}
        mise.pre_tool_call("read_file", args)
        assert args == {"command": "ls"}  # untouched

    def test_no_config_is_noop(self, fake_mise, tmp_path):
        self._activate(tmp_path)
        args = {"command": "ls"}
        mise.pre_tool_call("terminal", args)
        assert args["command"] == "ls"

    def test_never_raises_on_bad_args(self, fake_mise, repo_with_config):
        """A broken hook must not break the agent loop."""
        self._activate(repo_with_config)
        mise.pre_tool_call("terminal", {})  # no command key — no raise


# ---------------------------------------------------------------------------
# post_tool_call: trust invalidation + shadow cwd
# ---------------------------------------------------------------------------
class TestPostToolCallHook:
    def test_write_file_to_config_invalidates(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        state.trusted_dirs.add(str(repo_with_config))
        args = {"path": "mise.toml", "content": "[tools]\n"}
        mise.post_tool_call("write_file", args, result="ok")
        assert str(repo_with_config) not in state.trusted_dirs

    def test_patch_to_config_invalidates(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        state.trusted_dirs.add(str(repo_with_config))
        args = {"path": str(repo_with_config / "mise.toml"), "old_string": "a", "new_string": "b"}
        mise.post_tool_call("patch", args, result="ok")
        assert str(repo_with_config) not in state.trusted_dirs

    def test_write_file_unrelated_path_noop(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        state.trusted_dirs.add(str(repo_with_config))
        args = {"path": "README.md", "content": "hi"}
        mise.post_tool_call("write_file", args, result="ok")
        assert str(repo_with_config) in state.trusted_dirs  # untouched

    def test_terminal_result_advances_shadow_cwd(self, repo_with_config, tmp_path, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        result = {"cwd": str(tmp_path), "output": "", "exit_code": 0}
        mise.post_tool_call("terminal", {"command": "cd"}, result=result)
        assert state.shadow_cwd == str(tmp_path)

    def test_terminal_json_string_result_advances_shadow_cwd(
        self, repo_with_config, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        result = json.dumps({"cwd": str(tmp_path), "output": ""})
        mise.post_tool_call("terminal", {"command": "cd"}, result=result)
        assert state.shadow_cwd == str(tmp_path)

    def test_mutating_terminal_invalidates_trust(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        state.trusted_dirs.add(str(repo_with_config))
        # pre_tool_call records the mutation context
        args = {"command": "mise use node@20"}
        mise.pre_tool_call("terminal", args, tool_call_id="t1")
        mise.post_tool_call(
            "terminal", args, result={"cwd": str(repo_with_config)}, tool_call_id="t1"
        )
        assert str(repo_with_config) not in state.trusted_dirs

    def test_never_raises(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        mise.post_tool_call("terminal", None, result=None)  # bad args — no raise


# ---------------------------------------------------------------------------
# _extract_result_cwd
# ---------------------------------------------------------------------------
class TestExtractResultCwd:
    def test_dict_result(self):
        assert mise._extract_result_cwd({"cwd": "/x"}) == "/x"

    def test_dict_result_missing(self):
        assert mise._extract_result_cwd({"output": "hi"}) is None

    def test_json_string_result(self):
        assert mise._extract_result_cwd(json.dumps({"cwd": "/x"})) == "/x"

    def test_plain_string_no_cwd(self):
        assert mise._extract_result_cwd("plain output") is None

    def test_invalid_json_string(self):
        assert mise._extract_result_cwd('{"cwd": "/x" broken') is None

    def test_non_matching_types(self):
        assert mise._extract_result_cwd(None) is None
        assert mise._extract_result_cwd(42) is None


# ---------------------------------------------------------------------------
# on_session_start / on_session_reset
# ---------------------------------------------------------------------------
class TestOnSessionStartHook:
    def test_trusts_nearest_config(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        calls = capture_trust(monkeypatch)
        mise.on_session_start("session-1")
        assert calls == [str(repo_with_config)]
        assert state.mise_active is True

    def test_noop_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        calls = capture_trust(monkeypatch)
        mise.on_session_start("session-1")
        assert calls == []
        assert state.mise_active is False

    def test_never_raises_on_subprocess_error(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)

        def _raise(*argv, **kw):
            raise FileNotFoundError("mise not found")

        monkeypatch.setattr(mise, "_run_mise", _raise)
        mise.on_session_start("session-1")  # should not raise

    def test_reset_clears_state(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        mise.on_session_start("s1")
        assert state.mise_active is True
        mise.on_session_reset()
        assert state.mise_active is False
        assert state.trusted_dirs == set()


# ---------------------------------------------------------------------------
# pre_llm_call_note: the model note (pi-mise before_agent_start parity)
# ---------------------------------------------------------------------------
class TestPreLlmCallNote:
    def test_injects_note_when_active(self, repo_with_config, monkeypatch):
        monkeypatch.chdir(repo_with_config)
        state.reset(str(repo_with_config))
        state.mise_active = True
        result = mise.pre_llm_call_note(session_id="s", user_message="hi")
        assert result is not None
        assert "context" in result
        assert "Do NOT manually prepend" in result["context"]
        assert "even if AGENTS.md" in result["context"]

    def test_no_note_when_inactive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state.reset(str(tmp_path))
        state.mise_active = False
        assert mise.pre_llm_call_note(session_id="s") is None

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            mise.state, "mise_active", object(), raising=False
        )
        # mise_active truthy but .get style access — hook must not raise
        try:
            mise.pre_llm_call_note()
        except Exception:
            pytest.fail("pre_llm_call_note raised")


# ---------------------------------------------------------------------------
# Packaging: the bundled skill is discoverable relative to the package
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# register(): wires up skills + hooks via the ctx object
# ---------------------------------------------------------------------------
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
        assert hook_events == [
            "pre_tool_call",
            "on_session_start",
            "post_tool_call",
            "on_session_reset",
            "pre_llm_call",  # mise note
            "pre_llm_call",  # context.md
        ]

        from hermes_harness_plugin import mise as mise_mod
        assert any(f == mise_mod.pre_llm_call_note for _, f in ctx.hooks)
        assert any(f == context.pre_llm_call for _, f in ctx.hooks)

    def test_continues_on_skill_registration_error(self):
        from hermes_harness_plugin import register

        ctx = _FakeCtx(fail_skill=True)
        register(ctx)  # should not raise
        assert len(ctx.hooks) == 6


# ---------------------------------------------------------------------------
# Integration: drives the hooks against the REAL mise binary (pi-mise parity)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not mise_available(), reason="real mise binary not available")
class TestIntegrationRealMise:
    """End-to-end trust lifecycle against the real mise (like pi-mise's suite)."""

    @pytest.fixture(autouse=True)
    def _repos(self, tmp_path_factory):
        self.root = tmp_path_factory.mktemp("hhp-int-")
        self.session_repo = self.root / "session"
        self.other_repo = self.root / "other"
        for repo in (self.session_repo, self.other_repo):
            repo.mkdir()
            (repo / "mise.toml").write_text('[tools]\nnode = "22"\n')
        self.no_config_dir = self.root / "plain"
        self.no_config_dir.mkdir()
        yield
        state.reset()
        shutil.rmtree(self.root, ignore_errors=True)

    def _start_session(self, cwd):
        monkeypatch_dir = str(cwd)
        old = os.getcwd()
        os.chdir(monkeypatch_dir)
        try:
            mise.on_session_start("int")
        finally:
            os.chdir(old)
        state.shadow_cwd = monkeypatch_dir

    def test_session_start_trusts_config(self):
        self._start_session(self.session_repo)
        assert state.mise_active
        assert str(self.session_repo) in state.trusted_dirs

    def test_trust_actually_succeeded_via_mise(self):
        """mise trust really ran — config dir is trusted by mise itself."""
        self._start_session(self.session_repo)
        proc = subprocess.run(
            [mise._MISE_BIN, "trust"],
            cwd=str(self.session_repo),
            capture_output=True,
            text=True,
            timeout=30,
            input="",
            check=False,
        )
        # re-trust exits 0 either way; a truly untrusted config would prompt
        # (exit non-zero with no stdin). We fed empty stdin: prompt => failure.
        assert proc.returncode == 0, proc.stderr

    def test_cd_target_trusted_before_command(self):
        self._start_session(self.session_repo)
        state.trusted_dirs.clear()
        args = {"command": f"cd {self.other_repo} && node app.js"}
        mise.pre_tool_call("terminal", args, tool_call_id="i1")
        assert str(self.other_repo) in state.trusted_dirs
        assert "mise activate bash" in args["command"]
        mise.post_tool_call(
            "terminal", args, result={"cwd": str(self.other_repo)}, tool_call_id="i1"
        )

    def test_no_retrust_on_cached_dir(self):
        self._start_session(self.session_repo)
        state.trusted_dirs.add(str(self.other_repo))
        recorded = []

        def spy(*argv, cwd=None, timeout=15):
            if argv and argv[0] == "trust":
                recorded.append(str(cwd))
            return True

        original = mise._run_mise
        mise._run_mise = spy
        try:
            args = {"command": f"cd {self.other_repo} && ls"}
            mise.pre_tool_call("terminal", args, tool_call_id="i2")
        finally:
            mise._run_mise = original
        assert recorded == []  # cache hit — no trust call

    def test_mise_use_invalidates_and_retrusts(self):
        self._start_session(self.session_repo)
        assert str(self.session_repo) in state.trusted_dirs
        args = {"command": "mise use node@20"}
        mise.pre_tool_call("terminal", args, tool_call_id="i3")
        assert str(self.session_repo) in state.trusted_dirs  # not invalidated mid-call
        mise.post_tool_call(
            "terminal", args, result={"cwd": str(self.session_repo)}, tool_call_id="i3"
        )
        assert str(self.session_repo) not in state.trusted_dirs  # invalidated after
        # next command re-trusts
        args2 = {"command": "node -v"}
        mise.pre_tool_call("terminal", args2, tool_call_id="i4")
        assert str(self.session_repo) in state.trusted_dirs

    def test_shadow_cwd_drives_activation_in_other_repo(self):
        """cd into other_repo (no activation there initially), then a plain
        command in that dir still gets activated via the shadow cwd."""
        self._start_session(self.session_repo)
        state.shadow_cwd = str(self.other_repo)
        args = {"command": "node -v"}
        mise.pre_tool_call("terminal", args, tool_call_id="i5")
        assert "mise activate bash" in args["command"]

    def test_no_config_session_stays_inactive(self):
        self._start_session(self.no_config_dir)
        assert not state.mise_active
        args = {"command": "ls"}
        mise.pre_tool_call("terminal", args)
        assert args["command"] == "ls"
