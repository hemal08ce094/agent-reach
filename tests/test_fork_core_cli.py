# -*- coding: utf-8 -*-
"""Fork regressions for CLI behaviour (installers, skill, uninstall, update check)."""

import io
import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace

import pytest

import agent_reach.cli as cli


class _MemoryConfig:
    def __init__(self, *args, **kwargs):
        self.data = {}
        self.config_path = "memory://config.yaml"

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["agent-reach", *argv])
    monkeypatch.setattr(cli, "_configure_logging", lambda _verbose=False: None)
    cli.main()


# ── A3: mcporter is invoked through its resolved path ──


MCPORTER_PATH = os.path.join("C:", os.sep, "npm", "mcporter.cmd")


def test_setup_uses_resolved_mcporter_path(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        shutil, "which", lambda name: MCPORTER_PATH if name == "mcporter" else None
    )

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[1:3] == ["config", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"servers": []}), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "")
    monkeypatch.setattr("agent_reach.config.Config", _MemoryConfig)

    cli._cmd_setup()

    assert calls, "setup never queried mcporter"
    assert all(argv[0] == MCPORTER_PATH for argv in calls)
    assert any(argv[1:4] == ["config", "add", "exa"] for argv in calls)
    assert "Exa configured" in capsys.readouterr().out


def test_uninstall_uses_resolved_mcporter_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        shutil, "which", lambda name: MCPORTER_PATH if name == "mcporter" else None
    )

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"servers": []}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli._cmd_uninstall(Namespace(dry_run=True, keep_config=True))

    assert calls and all(argv[0] == MCPORTER_PATH for argv in calls)


# ── A4: configure youtube-cookies validates and normalises ──


def test_configure_youtube_cookies_normalises_value(monkeypatch, capsys):
    config = _MemoryConfig()
    monkeypatch.setattr("agent_reach.config.Config", lambda *a, **k: config)

    _run_main(monkeypatch, "configure", "youtube-cookies", "Chrome + basictext : Profile 2")

    assert config.data["youtube_cookies_from"] == "chrome+BASICTEXT:Profile 2"
    assert "--cookies-from-browser" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value", ["netscape", "chrome+notakeyring", "chrome::container", "cookies.txt"]
)
def test_configure_youtube_cookies_rejects_junk_with_exit_2(monkeypatch, capsys, value):
    config = _MemoryConfig()
    monkeypatch.setattr("agent_reach.config.Config", lambda *a, **k: config)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(monkeypatch, "configure", "youtube-cookies", value)

    assert exc_info.value.code == 2
    assert "youtube_cookies_from" not in config.data
    assert "agent-reach configure: error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value,expected",
    [
        ("firefox", "firefox"),
        ("firefox:default-release::Work", "firefox:default-release::Work"),
        ("EDGE:Profile 1", "edge:Profile 1"),
        ("chromium+kwallet6", "chromium+KWALLET6"),
    ],
)
def test_youtube_cookie_source_grammar(value, expected):
    assert cli._normalize_youtube_cookie_source(value) == expected


# ── A8: an explicit --env wins over auto-detection ──


@pytest.mark.parametrize("channel", ["reddit", "xiaohongshu"])
@pytest.mark.parametrize(
    ("env", "detected_env"),
    [("server", "local"), ("local", "server"), ("auto", "server"), ("auto", "local")],
)
def test_install_channels_use_resolved_environment(
    monkeypatch, capsys, channel, env, detected_env
):
    calls = []
    detect_calls = []
    monkeypatch.setattr(
        cli, "_detect_environment", lambda: detect_calls.append(True) or detected_env
    )
    monkeypatch.setattr(cli, "_install_system_deps", lambda: True)
    monkeypatch.setattr(cli, "_install_mcporter", lambda: True)
    monkeypatch.setattr(cli, "_install_skill", lambda: True)
    monkeypatch.setattr(cli, "_install_opencli_deps", lambda: calls.append("opencli") or True)
    monkeypatch.setattr(cli, "_install_rdt_cli", lambda: calls.append("rdt") or True)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr("agent_reach.doctor.check_all", lambda _config: {})
    monkeypatch.setattr("agent_reach.doctor.format_report", lambda _results: "report")

    resolved_env = detected_env if env == "auto" else env
    args = Namespace(env=env, proxy="", system=True, safe=False, dry_run=False, channels=channel)
    if channel == "xiaohongshu" and resolved_env == "server":
        with pytest.raises(SystemExit) as exc:
            cli._cmd_install(args)
        assert exc.value.code == 1
        assert "xiaohongshu-mcp" in capsys.readouterr().out
        assert calls == []
    else:
        cli._cmd_install(args)
        assert calls == ["rdt" if resolved_env == "server" else "opencli"]

    assert len(detect_calls) == (1 if env == "auto" else 0)


# ── A22: install paths honour HOME via home_dir() ──


def test_install_creates_tools_dir_under_home_dir(monkeypatch, isolated_home):
    # Simulate Windows, where expanduser ignores HOME and uses USERPROFILE.
    other_profile = isolated_home.parent / "userprofile"
    monkeypatch.setattr(
        cli.os.path, "expanduser", lambda p: p.replace("~", str(other_profile))
    )
    monkeypatch.setattr(cli, "_detect_environment", lambda: "local")
    monkeypatch.setattr(cli, "_install_system_deps", lambda: True)
    monkeypatch.setattr(cli, "_install_mcporter", lambda: True)
    monkeypatch.setattr(cli, "_install_skill", lambda: True)
    monkeypatch.setattr("agent_reach.doctor.check_all", lambda _config: {})
    monkeypatch.setattr("agent_reach.doctor.format_report", lambda _results: "report")

    cli._cmd_install(
        Namespace(env="auto", proxy="", system=True, safe=False, dry_run=False, channels="")
    )

    assert (isolated_home / ".agent-reach" / "tools").is_dir()
    assert not other_profile.exists()


# ── Skill: one English SKILL.md for every locale ──


@pytest.mark.parametrize("lang", ["C.UTF-8", "", "zh_CN.UTF-8", "en_US.UTF-8"])
def test_skill_install_uses_skill_md_for_every_locale(monkeypatch, isolated_home, lang):
    import importlib.resources

    monkeypatch.setenv("LANG", lang)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("AGENT_REACH_LANG", raising=False)
    (isolated_home / ".claude" / "skills").mkdir(parents=True)

    assert cli._install_skill() is True

    packaged = (
        importlib.resources.files("agent_reach")
        .joinpath("skill", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    installed = isolated_home / ".claude" / "skills" / "agent-reach" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == packaged


# ── A9: skill refresh is atomic ──


def _seed_installed_skill(home):
    target = home / ".agents" / "skills" / "agent-reach"
    (target / "references").mkdir(parents=True)
    (target / "SKILL.md").write_text("OLD SKILL", encoding="utf-8")
    (target / "references" / "old.md").write_text("old ref", encoding="utf-8")
    return target


def test_skill_refresh_keeps_old_skill_when_package_is_unreadable(
    monkeypatch, isolated_home, capsys
):
    target = _seed_installed_skill(isolated_home)

    def broken_loader():
        raise FileNotFoundError("SKILL.md missing from the package")

    monkeypatch.setattr(cli, "_load_skill_files", broken_loader)

    assert cli._install_skill() is False
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "OLD SKILL"
    assert (target / "references" / "old.md").exists()
    assert "Could not install skill" in capsys.readouterr().out


def test_skill_refresh_keeps_old_skill_when_staging_write_fails(monkeypatch, isolated_home):
    target = _seed_installed_skill(isolated_home)
    monkeypatch.setattr(
        cli, "_load_skill_files", lambda: {"SKILL.md": "NEW", "references/a.md": "a"}
    )
    real_open = open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith("a.md"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    assert cli._install_skill() is False
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "OLD SKILL"
    leftovers = [p.name for p in target.parent.iterdir() if p.name != "agent-reach"]
    assert leftovers == []


def test_skill_refresh_replaces_content_and_drops_stale_files(isolated_home):
    target = _seed_installed_skill(isolated_home)

    assert cli._install_skill() is True

    assert (target / "SKILL.md").read_text(encoding="utf-8") != "OLD SKILL"
    assert not (target / "references" / "old.md").exists()
    assert any((target / "references").glob("*.md"))
    assert [p.name for p in target.parent.iterdir()] == ["agent-reach"]


def test_skill_refresh_replaces_symlinked_dir_without_touching_target(
    isolated_home, tmp_path
):
    dotfiles = tmp_path / "dotfiles-skill"
    dotfiles.mkdir()
    (dotfiles / "SKILL.md").write_text("DOTFILES", encoding="utf-8")
    skills_root = isolated_home / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    link = skills_root / "agent-reach"
    try:
        link.symlink_to(dotfiles, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform")

    assert cli._install_skill() is True

    assert not link.is_symlink()
    assert (link / "SKILL.md").read_text(encoding="utf-8") != "DOTFILES"
    assert (dotfiles / "SKILL.md").read_text(encoding="utf-8") == "DOTFILES"


# ── A10: uninstall reuses the skill roots and reports failures ──


def test_uninstall_removes_openclaw_home_skill(monkeypatch, isolated_home, tmp_path, capsys):
    openclaw_home = tmp_path / "openclaw"
    skill = openclaw_home / ".openclaw" / "skills" / "agent-reach"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    cli._cmd_uninstall(Namespace(dry_run=True, keep_config=True))
    assert f"Would remove OpenClaw skill: {skill}" in capsys.readouterr().out
    assert skill.exists()

    cli._cmd_uninstall(Namespace(dry_run=False, keep_config=True))
    assert not skill.exists()
    assert "Agent Reach data removed." in capsys.readouterr().out


def test_uninstall_exits_1_when_a_removal_fails(monkeypatch, isolated_home, capsys):
    skill = isolated_home / ".claude" / "skills" / "agent-reach"
    skill.mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    def failing_remove(path):
        raise PermissionError(f"denied: {path}")

    monkeypatch.setattr(cli, "_remove_path", failing_remove)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_uninstall(Namespace(dry_run=False, keep_config=True))

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Could not remove" in out
    assert "Uninstall incomplete" in out


def test_skill_uninstall_command_exits_1_on_failure(monkeypatch, isolated_home):
    (isolated_home / ".agents" / "skills" / "agent-reach").mkdir(parents=True)

    def failing_remove(path):
        raise PermissionError("denied")

    monkeypatch.setattr(cli, "_remove_path", failing_remove)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_skill(Namespace(install=False, uninstall=True))
    assert exc_info.value.code == 1


# ── B1: redirected stdin is decoded as UTF-8 ──


def test_utf8_text_stream_rewraps_non_utf8_pipe():
    payload = '{"title": "小红书 笔记"}'.encode("utf-8")
    gbk_pipe = io.TextIOWrapper(io.BytesIO(payload), encoding="gbk")

    wrapped = cli._utf8_text_stream(gbk_pipe)

    assert wrapped is not gbk_pipe
    assert json.loads(wrapped.read())["title"] == "小红书 笔记"


def test_utf8_text_stream_leaves_tty_utf8_and_bufferless_streams_alone():
    class _Tty(io.TextIOWrapper):
        def isatty(self):
            return True

    tty = _Tty(io.BytesIO(b""), encoding="gbk")
    utf8_pipe = io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")
    string_stream = io.StringIO("x")

    assert cli._utf8_text_stream(tty) is tty
    assert cli._utf8_text_stream(utf8_pipe) is utf8_pipe
    assert cli._utf8_text_stream(string_stream) is string_stream
    assert cli._utf8_text_stream(None) is None


# ── B3: bounded Retry-After and non-JSON GitHub responses ──


class _Resp:
    def __init__(self, status_code, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError("Expecting value")
        return self._payload


@pytest.mark.parametrize("retry_after", ["3600", "1e9", "inf", "nan", "-5"])
def test_retry_after_sleep_is_capped(monkeypatch, retry_after):
    responses = iter(
        [
            _Resp(429, headers={"Retry-After": retry_after}),
            _Resp(200, payload={"tag_name": "v0.0.1"}),
        ]
    )
    monkeypatch.setattr("requests.get", lambda *_a, **_k: next(responses))
    sleeps = []

    resp, err, _attempts = cli._github_get_with_retry(
        "https://api.github.com/x", retries=3, sleeper=sleeps.append
    )

    assert err is None and resp.status_code == 200
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= cli._MAX_RETRY_DELAY_SECONDS


def test_check_update_handles_html_captive_portal(monkeypatch, capsys):
    monkeypatch.setattr(
        "requests.get", lambda *_a, **_k: _Resp(200, text="<html>login</html>")
    )

    assert cli._cmd_check_update() == "error"
    assert "non-JSON" in capsys.readouterr().out


def test_watch_ignores_html_captive_portal(monkeypatch, capsys):
    monkeypatch.setattr(
        "requests.get", lambda *_a, **_k: _Resp(200, text="<html>login</html>")
    )
    monkeypatch.setattr("agent_reach.doctor.check_all", lambda _config: {})

    cli._cmd_watch()

    assert "All normal" in capsys.readouterr().out


# ── B4: exact docker container selection ──


def _docker_fake(ps_stdout, calls):
    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, ps_stdout, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return fake_run


@pytest.mark.parametrize(
    "ps_stdout,expected",
    [
        ("xiaohongshu-mcp-old\nxiaohongshu-mcp\n", "xiaohongshu-mcp"),
        ("compose-xiaohongshu-mcp-1\n", "compose-xiaohongshu-mcp-1"),
    ],
)
def test_xhs_cookie_import_picks_exact_container(monkeypatch, ps_stdout, expected):
    calls = []
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr(subprocess, "run", _docker_fake(ps_stdout, calls))

    assert cli._configure_xhs_cookies("a1=b; web_session=c") is True

    exec_calls = [argv for argv in calls if argv[1] in ("exec", "cp", "restart")]
    assert exec_calls
    for argv in exec_calls:
        joined = " ".join(argv)
        assert expected in joined
        assert "\n" not in joined


def test_xhs_cookie_import_refuses_ambiguous_containers(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr(subprocess, "run", _docker_fake("a-xiaohongshu-mcp\nb-xiaohongshu-mcp\n", calls))

    assert cli._configure_xhs_cookies("a1=b") is False
    assert [argv[1] for argv in calls] == ["ps"]
    assert "Several running containers" in capsys.readouterr().out


def test_main_wraps_config_errors_raised_inside_commands(monkeypatch, capsys):
    from agent_reach.config import ConfigError

    def boom(_args):
        raise ConfigError("synthetic config failure")

    monkeypatch.setattr(cli, "_cmd_doctor", boom)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(monkeypatch, "doctor")
    assert exc_info.value.code == 2
    assert "synthetic config failure" in capsys.readouterr().err


def test_skill_roots_put_openclaw_home_first(monkeypatch, isolated_home, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path / "oc"))

    roots = cli._skill_roots()

    assert roots[0] == (os.fspath(tmp_path / "oc" / ".openclaw" / "skills"), "OpenClaw")
    assert all(path.startswith(str(isolated_home)) for path, _label in roots[1:])
