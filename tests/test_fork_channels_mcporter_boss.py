# -*- coding: utf-8 -*-
"""Fork regression tests: mcporter JSONC/skipped layers (B5, #762), Boss CDP
probe hardening (B8, #771), Bilibili short-circuit, Xiaoyuzhou doctor
(#753, A22) and the warn-level active_backend contract (B9). Offline."""

import json
import shutil
import subprocess
from unittest.mock import Mock, patch

import pytest

from agent_reach.channels import bilibili as bili_mod
from agent_reach.channels import boss as boss_mod
from agent_reach.channels.bilibili import BilibiliChannel
from agent_reach.channels.exa_search import ExaSearchChannel
from agent_reach.channels.mcporter import McporterConfigError, inspect_mcporter_config
from agent_reach.channels.xiaoyuzhou import XiaoyuzhouChannel
from agent_reach.probe import ProbeResult

# --- B5 / #762: JSONC and malformed non-explicit layers -------------------- #

_JSONC = """{
  // accepted by mcporter even with a .json extension
  "mcpServers": {
    "Exa": {"baseUrl": "https://mcp.exa.ai/mcp?label=/*keep*/,//keep",},
  },
  /* metadata only */
  "imports": [],
}"""


@pytest.mark.parametrize("location", ["home", "project", "explicit"])
def test_jsonc_layers_are_understood(monkeypatch, tmp_path, isolated_home, location):
    monkeypatch.chdir(tmp_path)
    if location == "home":
        path = isolated_home / ".mcporter" / "mcporter.jsonc"
    elif location == "project":
        path = tmp_path / "config" / "mcporter.json"
    else:
        path = tmp_path / "explicit.jsonc"
        monkeypatch.setenv("MCPORTER_CONFIG", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_JSONC, encoding="utf-8")

    inspection = inspect_mcporter_config()

    assert inspection.server_names == {"exa"}
    assert inspection.source == location
    assert inspection.imports_unchecked is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"mcpServers": {/* unterminated}',
        '{"mcpServers": {}, "imports": [,,]}',
        '{"mcpServers": {"exa": {"port": 1/*gap*/2}}, "imports": []}',
        "{'mcpServers': {}, 'imports': []}",
    ],
)
def test_jsonc_does_not_relax_other_syntax(monkeypatch, tmp_path, raw):
    path = tmp_path / "explicit.jsonc"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("MCPORTER_CONFIG", str(path))
    with pytest.raises(McporterConfigError, match="UTF-8 JSON"):
        inspect_mcporter_config()


def test_string_contents_survive_comment_stripping(monkeypatch, tmp_path):
    names = ["slash//name", "block/*name*/", "comma,}", 'escaped"//name']
    path = tmp_path / "explicit.json"
    path.write_text(
        json.dumps({"mcpServers": {n: {"command": "x"} for n in names}, "imports": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCPORTER_CONFIG", str(path))
    assert inspect_mcporter_config().server_names == set(names)


def test_malformed_home_layer_is_skipped_and_project_still_counts(
    monkeypatch, tmp_path, isolated_home
):
    monkeypatch.chdir(tmp_path)
    home = isolated_home / ".mcporter" / "mcporter.json"
    home.parent.mkdir(parents=True)
    home.write_text("{broken", encoding="utf-8")
    project = tmp_path / "config" / "mcporter.json"
    project.parent.mkdir()
    project.write_text('{"mcpServers": {"exa": {"command": "x"}}, "imports": []}', "utf-8")

    inspection = inspect_mcporter_config()

    assert inspection.server_names == {"exa"}
    assert inspection.source == "project"
    assert len(inspection.skipped_layers) == 1
    assert inspection.skipped_layers[0].startswith("home")


def test_schema_error_in_project_layer_is_skipped(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "config" / "mcporter.json"
    project.parent.mkdir()
    project.write_text('{"servers": []}', encoding="utf-8")

    inspection = inspect_mcporter_config()

    assert inspection.server_names == frozenset()
    assert inspection.source is None
    assert "mcpServers" in inspection.skipped_layers[0]


def test_explicit_layer_errors_still_fail_closed(monkeypatch, tmp_path):
    path = tmp_path / "explicit.json"
    path.write_text('{"servers": []}', encoding="utf-8")
    monkeypatch.setenv("MCPORTER_CONFIG", str(path))
    with pytest.raises(McporterConfigError, match="mcpServers"):
        inspect_mcporter_config()


def test_exa_channel_warns_about_skipped_layer(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "config" / "mcporter.json"
    project.parent.mkdir()
    project.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/mcporter")

    channel = ExaSearchChannel()
    status, message = channel.check()

    assert status == "warn"
    assert "ignored" in message
    assert channel.active_backend is None


def test_exa_configured_sets_warn_level_backend(monkeypatch, tmp_path):
    """B9: a configured-but-unverified backend is reported as active."""
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "config" / "mcporter.json"
    project.parent.mkdir()
    project.write_text('{"mcpServers": {"exa": {"command": "x"}}, "imports": []}', "utf-8")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/mcporter")

    channel = ExaSearchChannel()
    status, _ = channel.check()

    assert status == "warn"
    assert channel.active_backend == "Exa via mcporter"


# --- B8 / #771: Boss CDP probe -------------------------------------------- #

def _frame(payload: bytes, opcode: int, fin: bool = True, mask: bytes = b"") -> bytes:
    first = (0x80 if fin else 0) | opcode
    n = len(payload)
    second = 0x80 if mask else 0
    if n < 126:
        header = bytes([first, second | n])
    else:
        header = bytes([first, second | 126]) + n.to_bytes(2, "big")
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + payload


class _Sock:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, _n):
        return self.chunks.pop(0) if self.chunks else b""


def test_fragmented_message_is_reassembled_to_fin():
    body = json.dumps({"id": 1, "result": {"cookies": []}}).encode()
    stream = (
        _frame(body[:10], 0x1, fin=False)
        + _frame(b"", 0x9)  # interleaved ping
        + _frame(body[10:20], 0x0, fin=False)
        + _frame(body[20:], 0x0, fin=True)
    )
    payload, leftover = boss_mod._read_ws_text_frame(_Sock([stream[:7], stream[7:]]))
    assert payload == body
    assert leftover == b""


def test_masked_server_frame_is_unmasked():
    payload, _ = boss_mod._read_ws_text_frame(_Sock([_frame(b"hello", 0x1, mask=b"\x01\x02\x03\x04")]))
    assert payload == b"hello"


def test_continuation_without_start_is_rejected():
    payload, _ = boss_mod._read_ws_text_frame(_Sock([_frame(b"x", 0x0)]))
    assert payload is None


def test_oversized_message_is_rejected(monkeypatch):
    monkeypatch.setattr(boss_mod, "_WS_MAX_MESSAGE_BYTES", 8)
    payload, _ = boss_mod._read_ws_text_frame(_Sock([_frame(b"0123456789", 0x1)]))
    assert payload is None


@pytest.mark.parametrize(
    "ws_url",
    [
        "ws://evil.example:9222/devtools/browser/x",
        "ws://10.0.0.5:9222/devtools/browser/x",
        "wss://127.0.0.1:9222/devtools/browser/x",
        "http://127.0.0.1:9222/devtools/browser/x",
        "ws://127.0.0.1:99999/x",
    ],
)
def test_cdp_probe_only_connects_to_loopback(monkeypatch, ws_url):
    monkeypatch.setattr(boss_mod, "_cdp_json", lambda _path: {"webSocketDebuggerUrl": ws_url})
    monkeypatch.setattr(
        boss_mod.socket,
        "create_connection",
        lambda *a, **k: pytest.fail("must not connect to a non-loopback CDP host"),
    )
    assert boss_mod._cdp_zhipin_login_cookie() is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "127.0.0.2"])
def test_loopback_hosts_are_allowed(host):
    assert boss_mod._is_loopback_host(host.strip("[]")) is True


def _probe_with_cookies(monkeypatch, cookies):
    import base64
    import hashlib

    key16 = b"\x01" * 16
    key = base64.b64encode(key16).decode()
    accept = base64.b64encode(
        hashlib.sha1((key + boss_mod._WS_ACCEPT_GUID).encode()).digest()
    ).decode()
    handshake = f"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
    body = json.dumps({"id": 1, "result": {"cookies": cookies}}).encode()

    class Sock(_Sock):
        def settimeout(self, *_):
            pass

        def sendall(self, _data):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    sock = Sock([handshake.encode(), _frame(body, 0x1)])
    monkeypatch.setattr(boss_mod.os, "urandom", lambda n: key16)
    monkeypatch.setattr(boss_mod.socket, "create_connection", lambda *a, **k: sock)
    monkeypatch.setattr(
        boss_mod, "_cdp_json",
        lambda _p: {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/a"},
    )
    return boss_mod._cdp_zhipin_login_cookie()


@pytest.mark.parametrize("domain", ["zhipin.com.evil.test", "notzhipin.com", "zhipin.evil"])
def test_wt2_on_lookalike_domains_does_not_count(monkeypatch, domain):
    assert _probe_with_cookies(monkeypatch, [{"name": "wt2", "domain": domain}]) is False


@pytest.mark.parametrize("domain", [".zhipin.com", "www.zhipin.com", "zhipin.com"])
def test_wt2_on_zhipin_counts(monkeypatch, domain):
    assert _probe_with_cookies(monkeypatch, [None, {"name": "wt2", "domain": domain}]) is True


def test_windows_launch_command_quotes_profile_path():
    command = boss_mod._chrome_launch_command("Windows")
    assert '"--user-data-dir=`"$env:USERPROFILE\\.boss-chrome-profile`""' in command


# --- Bilibili: stop probing once a backend is ok ---------------------------- #

def test_bilibili_skips_search_api_once_bili_cli_is_ok(monkeypatch):
    monkeypatch.setattr(
        bili_mod, "probe_command", lambda *a, **k: ProbeResult("ok", output="bili 1.0")
    )
    monkeypatch.setattr(
        bili_mod, "_search_api_ok", lambda: pytest.fail("search API must not be probed")
    )
    with patch(
        "agent_reach.backends.opencli_status",
        side_effect=AssertionError("OpenCLI must not be probed"),
    ):
        channel = BilibiliChannel()
        status, _ = channel.check()
    assert status == "ok"
    assert channel.active_backend == "bili-cli"


def test_bilibili_unverified_opencli_is_reported_as_warn_backend(monkeypatch):
    monkeypatch.setattr(bili_mod, "probe_command", lambda *a, **k: ProbeResult("missing"))
    monkeypatch.setattr(bili_mod, "_search_api_ok", lambda: False)
    st = Mock(installed=True, broken=False, ready=True, hint="")
    with patch("agent_reach.backends.opencli_status", return_value=st):
        channel = BilibiliChannel()
        status, _ = channel.check()
    assert status == "warn"
    assert channel.active_backend == "OpenCLI"


# --- #753 / A22: Xiaoyuzhou doctor ------------------------------------------ #

def _xyz_probe(missing=(), broken=()):
    def probe(tool, *_a, **_k):
        if tool in missing:
            return ProbeResult("missing")
        if tool in broken:
            return ProbeResult("broken", hint="relink")
        return ProbeResult("ok", output="v1")

    return probe


@pytest.mark.parametrize("tool", ["ffprobe", "curl", "perl"])
def test_xiaoyuzhou_requires_every_script_tool(monkeypatch, tool):
    monkeypatch.setattr("agent_reach.channels.xiaoyuzhou.probe_command", _xyz_probe(missing={tool}))
    channel = XiaoyuzhouChannel()
    status, message = channel.check()
    assert status == "off"
    assert tool in message
    assert channel.active_backend is None


def test_xiaoyuzhou_broken_tool_is_error(monkeypatch):
    monkeypatch.setattr("agent_reach.channels.xiaoyuzhou.probe_command", _xyz_probe(broken={"perl"}))
    status, message = XiaoyuzhouChannel().check()
    assert status == "error"
    assert "perl" in message


def test_xiaoyuzhou_finds_script_under_explicit_home(monkeypatch, isolated_home):
    monkeypatch.setattr("agent_reach.channels.xiaoyuzhou.probe_command", _xyz_probe())
    monkeypatch.setattr("os.path.expanduser", lambda p: "/nonexistent-userprofile")
    script = isolated_home / ".agent-reach" / "tools" / "xiaoyuzhou" / "transcribe.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no subprocess"))

    channel = XiaoyuzhouChannel()
    status, _ = channel.check()

    assert status == "ok"
    assert channel.active_backend == "groq-whisper"
