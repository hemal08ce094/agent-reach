# -*- coding: utf-8 -*-
"""Fork regressions: config loading errors, symlink policy and private writes."""

import os
import stat
import sys

import pytest

import agent_reach.cli as cli
import agent_reach.config as config_module
from agent_reach.config import Config, ConfigError, ConfigSecurityError
from agent_reach.utils import paths
from agent_reach.utils.paths import PrivatePathError, read_small_text_no_follow


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["agent-reach", *argv])
    monkeypatch.setattr(cli, "_configure_logging", lambda _verbose=False: None)
    cli.main()


def _symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform")


# ── A1: invalid YAML / non-mapping → clean message, exit 2 ──


def test_invalid_yaml_raises_config_error_without_echoing_content(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("token: s3cr3t-value\nbad: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        Config(config_path=config_file)

    message = str(exc_info.value)
    assert "not valid YAML" in message
    assert "line" in message
    # The YAML snippet would contain secrets; only positions may be reported.
    assert "s3cr3t-value" not in message


@pytest.mark.parametrize("command", [["doctor"], ["doctor", "--json"], ["watch"]])
def test_cli_reports_invalid_config_in_one_line_and_exits_2(
    monkeypatch, capsys, isolated_home, command
):
    config_dir = isolated_home / ".agent-reach"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr("agent_reach.doctor.check_all", lambda _config: {})

    with pytest.raises(SystemExit) as exc_info:
        _run_main(monkeypatch, *command)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "agent-reach: error:" in captured.err
    assert "not valid YAML" in captured.err
    assert "Fix or remove" in captured.err


def test_cli_reports_symlinked_config_dir_for_writes_and_exits_2(
    monkeypatch, capsys, tmp_path
):
    real_dir = tmp_path / "real-config"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked-config"
    _symlink_or_skip(linked_dir, real_dir, directory=True)
    monkeypatch.setattr(Config, "CONFIG_DIR", linked_dir)
    monkeypatch.setattr(Config, "CONFIG_FILE", linked_dir / "config.yaml")

    with pytest.raises(SystemExit) as exc_info:
        _run_main(monkeypatch, "configure", "github-token", "ghp_example")

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "symlink" in err
    assert "never writes credentials through a symlink" in err
    assert list(real_dir.iterdir()) == []


# ── A1: read-only paths follow symlinks, writes stay no-follow ──


def test_read_only_config_follows_symlinked_home(monkeypatch, tmp_path):
    real_home = tmp_path / "real-home"
    (real_home / ".agent-reach").mkdir(parents=True)
    (real_home / ".agent-reach" / "config.yaml").write_text(
        "github_token: from-real-home\n", encoding="utf-8"
    )
    linked_home = tmp_path / "linked-home"
    _symlink_or_skip(linked_home, real_home, directory=True)
    config_file = linked_home / ".agent-reach" / "config.yaml"

    read_only = Config(config_path=config_file, read_only=True)
    assert read_only.get("github_token") == "from-real-home"

    with pytest.raises(ConfigSecurityError, match="symlink"):
        Config(config_path=config_file)


def test_doctor_runs_with_symlinked_home(monkeypatch, capsys, tmp_path):
    real_home = tmp_path / "real-home"
    (real_home / ".agent-reach").mkdir(parents=True)
    (real_home / ".agent-reach" / "config.yaml").write_text(
        "github_token: x\n", encoding="utf-8"
    )
    linked_home = tmp_path / "linked-home"
    _symlink_or_skip(linked_home, real_home, directory=True)
    monkeypatch.setattr(Config, "CONFIG_DIR", linked_home / ".agent-reach")
    monkeypatch.setattr(Config, "CONFIG_FILE", linked_home / ".agent-reach" / "config.yaml")
    seen = []
    monkeypatch.setattr(
        "agent_reach.doctor.check_all", lambda config: seen.append(config) or {}
    )

    _run_main(monkeypatch, "doctor", "--json")

    assert capsys.readouterr().out.strip() == "{}"
    assert seen[0].get("github_token") == "x"


def test_read_small_text_follow_symlinks_keeps_regular_file_and_size_checks(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.txt"
    _symlink_or_skip(link, target)

    with pytest.raises(PrivatePathError, match="symlink"):
        read_small_text_no_follow(link, max_bytes=100)
    assert read_small_text_no_follow(link, max_bytes=100, follow_symlinks=True) == "hello"
    with pytest.raises(PrivatePathError, match="size limit"):
        read_small_text_no_follow(link, max_bytes=2, follow_symlinks=True)

    dir_link = tmp_path / "dir-link"
    _symlink_or_skip(dir_link, tmp_path, directory=True)
    with pytest.raises(PrivatePathError, match="regular file"):
        read_small_text_no_follow(dir_link, max_bytes=100, follow_symlinks=True)


# ── B6: non-mapping YAML is an error, never silently {} ──


@pytest.mark.parametrize("payload", ["[]\n", "false\n", "0\n", "- a\n- b\n", "just text\n"])
def test_non_mapping_yaml_is_rejected(tmp_path, payload):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(payload, encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a mapping"):
        Config(config_path=config_file)
    assert config_file.read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("payload", ["", "# only a comment\n", "~\n"])
def test_empty_yaml_is_an_empty_config(tmp_path, payload):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(payload, encoding="utf-8")

    assert Config(config_path=config_file).data == {}


def test_invalid_utf8_config_is_a_config_error(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_bytes(b"key: \xff\xfe\n")

    with pytest.raises(ConfigError, match="UTF-8"):
        Config(config_path=config_file)


# ── A21: no fd leak when fchmod/fdopen fails ──


def test_atomic_write_yaml_closes_fd_when_fdopen_fails(monkeypatch, tmp_path):
    created = []
    real_mkstemp = config_module.tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append((fd, name))
        return fd, name

    def failing_fdopen(*_args, **_kwargs):
        raise OSError("fdopen failed")

    monkeypatch.setattr(config_module.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(config_module.os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="fdopen failed"):
        config_module._atomic_write_yaml(tmp_path / "config.yaml", {"a": 1})

    fd, name = created[0]
    with pytest.raises(OSError):
        os.fstat(fd)  # descriptor was closed
    assert not os.path.exists(name)


# ── B7: make_private_dir tolerates chmod-denying sandboxes ──


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_make_private_dir_skips_fchmod_when_already_private(monkeypatch, tmp_path):
    target = tmp_path / "private"
    target.mkdir(mode=0o700)
    os.chmod(target, 0o700)

    def denied(*_args):
        raise PermissionError("chmod denied by sandbox")

    monkeypatch.setattr(paths.os, "fchmod", denied)

    assert paths.make_private_dir(target) == target


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_make_private_dir_warns_instead_of_failing_on_permission_error(
    monkeypatch, tmp_path
):
    from loguru import logger

    target = tmp_path / "shared"
    target.mkdir()
    os.chmod(target, 0o755)
    messages = []
    handler_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")

    def denied(*_args):
        raise PermissionError("chmod denied by sandbox")

    monkeypatch.setattr(paths.os, "fchmod", denied)
    try:
        assert paths.make_private_dir(target) == target
    finally:
        logger.remove(handler_id)

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert any("owner-only" in message for message in messages)
