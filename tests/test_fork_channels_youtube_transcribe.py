# -*- coding: utf-8 -*-
"""Fork regression tests: yt-dlp config discovery (A2/#715/#766) and
transcription hardening (A4 youtube_cookies_from, A19 ffmpeg/SSRF).

Everything is offline: ``probe_command`` / ``shutil.which`` / ``_run`` /
``subprocess.run`` are stubbed.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_reach import transcribe as tr
from agent_reach.channels import youtube as yt
from agent_reach.channels.youtube import (
    YouTubeChannel,
    _has_js_runtime_config,
    find_ytdlp_user_config,
    ytdlp_user_config_candidates,
)
from agent_reach.probe import ProbeResult


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _node_only_check():
    ch = YouTubeChannel()
    with patch.object(yt, "probe_command", return_value=ProbeResult("ok", output="2026.07.04")), \
         patch("shutil.which", side_effect=_which("node")):
        status, message = ch.check()
    return ch, status, message


# --- A2: yt-dlp config discovery ---------------------------------------- #

def test_candidates_follow_ytdlp_lookup_order(isolated_home):
    candidates = ytdlp_user_config_candidates()
    config = isolated_home / ".config"
    appdata = isolated_home / "AppData" / "Roaming"
    assert candidates == [
        config / "yt-dlp.conf",
        config / "yt-dlp" / "config",
        config / "yt-dlp" / "config.txt",
        appdata / "yt-dlp.conf",
        appdata / "yt-dlp" / "config",
        appdata / "yt-dlp" / "config.txt",
        isolated_home / "yt-dlp.conf",
        isolated_home / "yt-dlp.conf.txt",
        isolated_home / ".yt-dlp" / "config",
        isolated_home / ".yt-dlp" / "config.txt",
    ]


def test_first_existing_config_wins(isolated_home):
    later = isolated_home / ".yt-dlp" / "config"
    later.parent.mkdir()
    later.write_text("--js-runtimes node\n", encoding="utf-8")
    first = isolated_home / ".config" / "yt-dlp.conf"
    first.parent.mkdir()
    first.write_text("--no-mtime\n", encoding="utf-8")

    assert find_ytdlp_user_config() == first


def test_doctor_reads_the_file_ytdlp_actually_loads(isolated_home):
    """~/.config/yt-dlp.conf shadows ~/.config/yt-dlp/config for yt-dlp."""
    shadowed = isolated_home / ".config" / "yt-dlp" / "config"
    shadowed.parent.mkdir(parents=True)
    shadowed.write_text("--js-runtimes node\n", encoding="utf-8")
    effective = isolated_home / ".config" / "yt-dlp.conf"
    effective.write_text("--no-mtime\n", encoding="utf-8")

    _, status, message = _node_only_check()

    assert status == "warn"
    assert str(effective) in message  # the fix command targets the real file


def test_runtime_in_effective_config_is_ok(isolated_home):
    cfg = isolated_home / "yt-dlp.conf"
    cfg.write_text("--js-runtimes node\n", encoding="utf-8")

    ch, status, _ = _node_only_check()

    assert status == "ok"
    assert ch.active_backend == "yt-dlp"


def test_directory_named_like_a_config_is_skipped(isolated_home):
    (isolated_home / ".config" / "yt-dlp.conf").mkdir(parents=True)
    real = isolated_home / ".config" / "yt-dlp" / "config"
    real.parent.mkdir(parents=True)
    real.write_text("--js-runtimes node\n", encoding="utf-8")

    assert find_ytdlp_user_config() == real


def test_no_config_points_fix_at_default_xdg_location(isolated_home):
    _, status, message = _node_only_check()

    assert status == "warn"
    assert str(isolated_home / ".config" / "yt-dlp" / "config") in message


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("# --js-runtimes node\n", False),
        ("--no-mtime # --js-runtimes node\n", False),
        ("--js-runtimes=node\n", True),
        ('--js-runtimes "node:/tools/Node JS/node"\n', True),
        ("--js-runtimes node # enabled\n", True),
        ('--js-runtimes "node\n', False),  # unparseable, yt-dlp would fail too
        ("﻿--js-runtimes node\n", True),
        ("﻿# --js-runtimes node\n", False),
        ("--js-runtimes node\r\n", True),
    ],
)
def test_runtime_detection_is_comment_aware(tmp_path, payload, expected):
    cfg = tmp_path / "config"
    cfg.write_text(payload, encoding="utf-8")
    assert _has_js_runtime_config(cfg) is expected


def test_commented_flag_is_reported_as_unconfigured(isolated_home):
    cfg = isolated_home / ".config" / "yt-dlp" / "config"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# --js-runtimes node\n", encoding="utf-8")

    _, status, message = _node_only_check()

    assert status == "warn"
    assert "--js-runtimes node" in message


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_config_is_followed_like_ytdlp_does(isolated_home, tmp_path):
    """Dotfile-managed configs used to be reported as "not configured"."""
    target = tmp_path / "dotfiles" / "yt-dlp.conf"
    target.parent.mkdir()
    target.write_text("--js-runtimes node\n", encoding="utf-8")
    link = isolated_home / ".config" / "yt-dlp.conf"
    link.parent.mkdir()
    link.symlink_to(target)

    ch, status, _ = _node_only_check()

    assert status == "ok"
    assert ch.active_backend == "yt-dlp"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_config_directory_is_followed(isolated_home, tmp_path):
    real_dir = tmp_path / "dotfiles" / "yt-dlp"
    real_dir.mkdir(parents=True)
    (real_dir / "config").write_text("# --js-runtimes node\n", encoding="utf-8")
    (isolated_home / ".config").mkdir()
    (isolated_home / ".config" / "yt-dlp").symlink_to(real_dir, target_is_directory=True)

    _, status, message = _node_only_check()

    assert status == "warn"
    assert str(isolated_home / ".config" / "yt-dlp" / "config") in message


def test_ok_message_does_not_claim_live_video_access():
    ch = YouTubeChannel()
    with patch.object(yt, "probe_command", return_value=ProbeResult("ok")), \
         patch("shutil.which", side_effect=_which("deno")):
        status, message = ch.check()
    assert status == "ok"
    assert "not verified live" in message
    assert "Can extract" not in message


# --- A4: youtube_cookies_from reaches yt-dlp for YouTube hosts only ------ #

class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _capture_download(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, timeout=600):
        captured["cmd"] = cmd
        (tmp_path / "source.m4a").write_bytes(b"audio")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", fake_run)
    return captured


@pytest.mark.parametrize(
    "url", ["https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "youtu.be/abc"]
)
def test_download_passes_cookie_browser_for_youtube(monkeypatch, tmp_path, url):
    captured = _capture_download(monkeypatch, tmp_path)

    tr.download_audio(url, tmp_path, cookies_from_browser="chrome")

    cmd = captured["cmd"]
    index = cmd.index("--cookies-from-browser")
    assert cmd[index + 1] == "chrome"
    assert index < cmd.index("--")  # an option, never after end-of-options
    assert cmd[-1] == url


def test_download_never_sends_browser_cookies_to_other_hosts(monkeypatch, tmp_path):
    captured = _capture_download(monkeypatch, tmp_path)

    tr.download_audio("https://example.com/a.mp3", tmp_path, cookies_from_browser="chrome")

    assert "--cookies-from-browser" not in captured["cmd"]


def test_youtube_cookie_source_is_canonicalised():
    assert tr.youtube_cookie_source("Chrome + basictext : Profile 2") == "chrome+BASICTEXT:Profile 2"
    assert tr.youtube_cookie_source("firefox::Work") == "firefox::Work"
    assert tr.youtube_cookie_source("") is None
    assert tr.youtube_cookie_source(None) is None


@pytest.mark.parametrize(
    "value", ["netscape", "chrome+NOPE", "chrome\n--exec", "--exec=evil", 42]
)
def test_youtube_cookie_source_rejects_junk(value):
    with pytest.raises(tr.TranscribeError):
        tr.youtube_cookie_source(value)


def test_transcribe_reads_config_key_for_youtube(monkeypatch, tmp_path):
    seen = {}

    def fake_download(url, out_dir, *, cookies_from_browser=None):
        seen["cookies"] = cookies_from_browser
        raise tr.TranscribeError("stop after download")

    monkeypatch.setattr(tr, "download_audio", fake_download)
    cfg = _Cfg(groq_api_key="gsk_test", youtube_cookies_from="firefox")

    with pytest.raises(tr.TranscribeError, match="stop after download"):
        tr.transcribe("https://youtu.be/abc", config=cfg, out_dir=tmp_path / "w")

    assert seen["cookies"] == "firefox"


def test_transcribe_ignores_cookie_key_for_non_youtube(monkeypatch, tmp_path):
    seen = {}

    def fake_download(url, out_dir, **kwargs):
        seen["kwargs"] = kwargs
        raise tr.TranscribeError("stop after download")

    monkeypatch.setattr(tr, "download_audio", fake_download)
    cfg = _Cfg(groq_api_key="gsk_test", youtube_cookies_from="firefox")

    with pytest.raises(tr.TranscribeError):
        tr.transcribe("https://example.com/a.mp3", config=cfg, out_dir=tmp_path / "w")

    assert seen["kwargs"] == {}


def test_invalid_configured_browser_fails_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(
        tr, "_run", lambda *_a, **_k: pytest.fail("yt-dlp must not run with a bad browser value")
    )
    with pytest.raises(tr.TranscribeError, match="unsupported YouTube cookie browser"):
        tr.download_audio("https://youtu.be/abc", tmp_path, cookies_from_browser="netscape")


# --- A19: ffmpeg/ffprobe hardening ------------------------------------- #

def test_run_never_inherits_caller_stdin(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    tr._run(["ffmpeg", "-version"], timeout=5)
    assert seen["stdin"] is subprocess.DEVNULL


def test_ffprobe_gets_absolute_path_and_no_stdin(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "12.5\n", "")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    assert tr._probe_audio_duration(Path("-x.wav")) == 12.5
    assert seen["cmd"][-1] == str(tmp_path / "-x.wav")
    assert seen["stdin"] is subprocess.DEVNULL


@pytest.mark.parametrize("helper", ["compress_audio", "chunk_audio"])
def test_ffmpeg_gets_nostdin_and_absolute_paths(monkeypatch, tmp_path, helper):
    captured = {}
    monkeypatch.chdir(tmp_path)
    out_dir = Path("-work")
    out_dir.mkdir()

    def fake_run(cmd, timeout=600):
        captured["cmd"] = cmd
        (out_dir / "compressed.m4a").write_bytes(b"x")
        (out_dir / "chunk_000.m4a").write_bytes(b"x")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", fake_run)

    getattr(tr, helper)(Path("Podcast: x.wav"), out_dir)

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert cmd[1] == "-nostdin"
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "Podcast: x.wav")
    assert Path(cmd[-1]).is_absolute()
    assert cmd[-1].startswith(str(tmp_path / "-work"))


@pytest.mark.parametrize(
    "url",
    [
        "http://foo.internal/a.mp3",
        "http://printer.lan/a.mp3",
        "http://box.local/a.mp3",
        "http://100.64.0.1/a.mp3",  # CGNAT: not global
        "http://１２７.０.０.１/a.mp3",
        "https://user:pw@example.com/a.mp3",
        "http://example.com /a.mp3",
    ],
)
def test_transcribe_uses_shared_public_url_policy(monkeypatch, tmp_path, url):
    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", lambda *_a, **_k: pytest.fail("yt-dlp must not run"))
    with pytest.raises(tr.TranscribeError, match="SSRF"):
        tr.download_audio(url, tmp_path)
