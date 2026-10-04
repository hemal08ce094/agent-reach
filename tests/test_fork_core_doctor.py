# -*- coding: utf-8 -*-
"""Fork regressions: concurrent doctor, OpenCLI memo/multi-profile, MCP output,
backend overrides and bounded probes."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import agent_reach.backends.opencli as opencli
import agent_reach.doctor as doctor
import agent_reach.integrations.mcp_server as mcp_server
import agent_reach.probe as probe
from agent_reach.channels import get_all_channels
from agent_reach.channels.base import Channel
from agent_reach.probe import ProbeResult


class _Stub(Channel):
    def __init__(self, name, status="ok", message="fine", backends=("b",),
                 active=None, delay=0.0, event=None, tier=0):
        self.name = name
        self.description = name.title()
        self.backends = list(backends)
        self.tier = tier
        self._status = status
        self._message = message
        self._active = active
        self._delay = delay
        self._event = event
        self.thread_ids = []

    def can_handle(self, url):
        return False

    def check(self, config=None):
        self.thread_ids.append(threading.get_ident())
        if self._event is not None:
            self._event.wait(30)
        if self._delay:
            time.sleep(self._delay)
        self.active_backend = self._active
        return self._status, self._message


# ── C: concurrent checks under one shared budget ──


def test_checks_run_concurrently_and_keep_registry_order(monkeypatch):
    channels = [_Stub(f"ch{i}", delay=0.4, active="b") for i in range(6)]
    monkeypatch.setattr(doctor, "get_all_channels", lambda: channels)

    started = time.monotonic()
    results = doctor.check_all(None)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"checks ran serially ({elapsed:.2f}s)"
    assert list(results) == [f"ch{i}" for i in range(6)]
    assert all(r["status"] == "ok" and r["active_backend"] == "b" for r in results.values())
    assert len({ch.thread_ids[0] for ch in channels}) == 6


def test_straggler_reports_timeout_without_blocking(monkeypatch):
    release = threading.Event()
    hung = _Stub("hung", event=release, active="b")
    fast_before = _Stub("first", active="b")
    fast_after = _Stub("last", status="warn", active="b")
    monkeypatch.setattr(doctor, "get_all_channels", lambda: [fast_before, hung, fast_after])

    try:
        started = time.monotonic()
        results = doctor.check_all(None, timeout=0.3)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < 2.0
    assert list(results) == ["first", "hung", "last"]
    assert results["hung"]["status"] == "timeout"
    assert results["hung"]["active_backend"] is None
    assert "0.3s" in results["hung"]["message"]
    assert results["first"]["status"] == "ok"
    assert results["last"]["status"] == "warn"


def test_budget_is_shared_not_per_channel(monkeypatch):
    release = threading.Event()
    channels = [_Stub(f"hung{i}", event=release) for i in range(4)]
    monkeypatch.setattr(doctor, "get_all_channels", lambda: channels)

    try:
        started = time.monotonic()
        results = doctor.check_all(None, timeout=0.3)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < 1.0, "per-channel budgets were added up"
    assert {r["status"] for r in results.values()} == {"timeout"}


def test_doctor_threads_are_daemons(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(doctor, "get_all_channels", lambda: [_Stub("hung", event=release)])
    try:
        doctor.check_all(None, timeout=0.05)
        stragglers = [t for t in threading.enumerate() if t.name == "agent-reach-doctor-hung"]
        assert stragglers and all(t.daemon for t in stragglers)
    finally:
        release.set()


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_REACH_DOCTOR_TIMEOUT", "0.2")
    assert doctor._resolve_timeout(None) == 0.2
    monkeypatch.setenv("AGENT_REACH_DOCTOR_TIMEOUT", "junk")
    assert doctor._resolve_timeout(None) == doctor.DEFAULT_CHECK_TIMEOUT
    assert doctor._resolve_timeout(5) == 5.0


def test_timeout_status_renders_in_report():
    results = {
        "slow": {"status": "timeout", "name": "Slow", "message": "did not finish",
                 "tier": 0, "backends": ["x"], "active_backend": None},
    }
    report = doctor.format_report(results)
    assert "Slow" in report and "did not finish" in report


# ── B9: active_backend is None unless the status is ok/warn ──


@pytest.mark.parametrize("status", ["off", "error"])
def test_unusable_status_never_reports_active_backend(monkeypatch, status):
    monkeypatch.setattr(
        doctor, "get_all_channels", lambda: [_Stub("x", status=status, active="b")]
    )
    assert doctor.check_all(None)["x"]["active_backend"] is None


def test_warn_keeps_channel_reported_backend(monkeypatch):
    monkeypatch.setattr(
        doctor, "get_all_channels", lambda: [_Stub("x", status="warn", active="b")]
    )
    assert doctor.check_all(None)["x"]["active_backend"] == "b"


def test_pick_finding_sets_backend_for_ok_and_warn():
    ch = _Stub("x", backends=("one", "two", "three"))
    assert ch.pick_finding([("one", "error", "e"), ("two", "warn", "w"),
                            ("three", "ok", "o")]) == ("ok", "o")
    assert ch.active_backend == "three"
    assert ch.pick_finding([("one", "warn", "w1"), ("two", "warn", "w2")]) == ("warn", "w1")
    assert ch.active_backend == "one"
    ch.active_backend = None
    assert ch.pick_finding([("one", "error", "e")]) is None
    assert ch.active_backend is None


# ── C: opencli_status() memoised per doctor run ──


def test_opencli_status_probed_once_per_doctor_run(monkeypatch):
    calls = []

    def fake_probe(timeout):
        calls.append(timeout)
        time.sleep(0.05)
        return opencli.OpenCLIStatus(installed=True, version="1.8.8")

    monkeypatch.setattr(opencli, "_probe_opencli_status", fake_probe)

    class _OpenCLIUser(_Stub):
        def check(self, config=None):
            from agent_reach.backends import opencli_status

            st = opencli_status()
            st.hint = f"mutated by {self.name}"  # must not leak to other channels
            self.active_backend = "OpenCLI"
            return "warn", st.version

    channels = [_OpenCLIUser(f"site{i}", backends=("OpenCLI",)) for i in range(6)]
    monkeypatch.setattr(doctor, "get_all_channels", lambda: channels)

    results = doctor.check_all(None)
    assert calls == [10]
    assert {r["message"] for r in results.values()} == {"1.8.8"}

    doctor.check_all(None)
    assert calls == [10, 10], "memo must be cleared between doctor runs"

    opencli.opencli_status()
    opencli.opencli_status()
    assert len(calls) == 4, "outside a doctor run every call probes"


def test_memo_returns_copies():
    with opencli.opencli_status_memo():
        opencli._memo[10] = opencli.OpenCLIStatus(installed=True, version="1")
        first = opencli.opencli_status()
        first.version = "changed"
        assert opencli.opencli_status().version == "1"
    assert opencli._memo is None


# ── D: multi-profile OpenCLI daemon status (#739) ──


def _status_with(daemon_status, monkeypatch):
    monkeypatch.setattr(
        opencli, "probe_command", lambda *a, **k: ProbeResult("ok", output="1.8.8")
    )
    monkeypatch.setattr(opencli, "_fetch_daemon_status", lambda _t=2: daemon_status)
    monkeypatch.setattr(opencli, "_extension_installed_on_disk", lambda: True)
    monkeypatch.setattr(opencli, "_unpacked_extension_files_present", lambda: False)
    return opencli.opencli_status()


def test_multi_profile_daemon_with_connected_profile_is_ready(monkeypatch):
    st = _status_with(
        {
            "ok": True,
            "extensionConnected": False,
            "profileRequired": True,
            "profiles": [
                {"contextId": "a", "extensionConnected": True},
                {"contextId": "b", "extensionConnected": False},
            ],
        },
        monkeypatch,
    )
    assert st.daemon_running and st.extension_connected and st.ready
    assert st.hint == ""


def test_multi_profile_daemon_without_connected_profile_is_not_ready(monkeypatch):
    st = _status_with(
        {
            "ok": True,
            "extensionConnected": False,
            "profileRequired": True,
            "profiles": [
                {"contextId": "a", "extensionConnected": False},
                "malformed",
                {"contextId": "b", "extensionConnected": "true"},
            ],
        },
        monkeypatch,
    )
    assert st.daemon_running and not st.extension_connected and not st.ready


def test_extension_scan_uses_home_dir(monkeypatch, isolated_home):
    ext = (isolated_home / ".config" / "google-chrome" / "Default" / "Extensions"
           / opencli.OPENCLI_EXTENSION_ID)
    ext.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", "/nonexistent"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert opencli._extension_installed_on_disk()


# ── A11: case-insensitive backend override with ASCII aliases ──


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.mark.parametrize(
    "channel_name,override,expected",
    [
        ("twitter", "opencli", "OpenCLI"),
        ("twitter", "OPENCLI", "OpenCLI"),
        ("twitter", "bird", "bird CLI (legacy)"),
        ("twitter", "Twitter-CLI", "twitter-cli"),
        ("bilibili", "bilibili-api", "Bilibili search API"),
        ("bilibili", "api", "Bilibili search API"),
        ("bilibili", "search-api", "Bilibili search API"),
        ("bilibili", "Bilibili Search API", "Bilibili search API"),
        ("bilibili", "bilicli", "bili-cli"),
        ("xiaohongshu", "xhs-cli", "xhs-cli (xiaohongshu-cli)"),
        ("xiaohongshu", "mcp", "xiaohongshu-mcp"),
        ("reddit", "rdt", "rdt-cli"),
        ("linkedin", "jina", "Jina Reader"),
    ],
)
def test_backend_override_matches_case_insensitively(channel_name, override, expected):
    channel = next(ch for ch in get_all_channels() if ch.name == channel_name)
    ordered = channel.ordered_backends(_Config({f"{channel_name}_backend": override}))
    assert ordered[0] == expected
    assert sorted(ordered) == sorted(channel.backends)


def test_unmatched_backend_override_warns_and_keeps_order():
    from loguru import logger

    channel = next(ch for ch in get_all_channels() if ch.name == "bilibili")
    messages = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        ordered = channel.ordered_backends(_Config({"bilibili_backend": "nonsense"}))
    finally:
        logger.remove(handler_id)
    assert ordered == channel.backends
    assert any("bilibili_backend" in m and "matches no" in m for m in messages)


def test_explicit_backend_alias():
    ch = _Stub("x", backends=("Bilibili search API", "bili-cli"))
    ch.backend_aliases = {"B站": "Bilibili search API"}
    assert ch.match_backend("b站") == "Bilibili search API"


# ── A5: MCP server returns plain text / JSON off the event loop ──


class _FakeServer:
    def __init__(self, name):
        self.name = name

    def list_tools(self):
        def register(handler):
            self.list_tools_handler = handler
            return handler
        return register

    def call_tool(self):
        def register(handler):
            self.call_tool_handler = handler
            return handler
        return register


def _fake_mcp(monkeypatch, eyes_cls):
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeServer, raising=False)
    monkeypatch.setattr(mcp_server, "Tool", lambda **k: SimpleNamespace(**k), raising=False)
    monkeypatch.setattr(mcp_server, "TextContent", lambda **k: SimpleNamespace(**k), raising=False)
    monkeypatch.setattr(mcp_server, "Config", lambda read_only=False: None)
    monkeypatch.setattr(mcp_server, "AgentReach", eyes_cls)
    return mcp_server.create_server()


def test_mcp_get_status_is_plain_text_and_runs_off_loop(monkeypatch):
    threads = {}

    class _Eyes:
        def __init__(self, config):
            pass

        def doctor_report(self):
            threads["worker"] = threading.get_ident()
            return "[bold cyan]Agent Reach status[/bold cyan]\n[green]✅[/green] [bold]Web[/bold]"

    server = _fake_mcp(monkeypatch, _Eyes)

    async def call():
        threads["loop"] = threading.get_ident()
        return await server.call_tool_handler("get_status", {})

    text = asyncio.run(call())[0].text
    assert text == "Agent Reach status\n✅ Web"
    assert "[bold" not in text
    assert threads["worker"] != threads["loop"]


def test_mcp_get_status_json(monkeypatch):
    payload = {"web": {"status": "ok", "active_backend": "Jina Reader"}}

    class _Eyes:
        def __init__(self, config):
            pass

        def doctor(self):
            return payload

    server = _fake_mcp(monkeypatch, _Eyes)
    result = asyncio.run(server.call_tool_handler("get_status", {"format": "json"}))
    assert json.loads(result[0].text) == payload

    tools = asyncio.run(server.list_tools_handler())
    assert tools[0].inputSchema["properties"]["format"]["enum"] == ["text", "json"]

    bad = asyncio.run(server.call_tool_handler("get_status", {"format": "xml"}))
    assert "Unknown format" in bad[0].text


def test_mcp_install_hint_names_existing_extra(monkeypatch, capsys):
    import re
    from pathlib import Path

    monkeypatch.setattr(mcp_server, "HAS_MCP", False)
    with pytest.raises(SystemExit):
        mcp_server.create_server()
    err = capsys.readouterr().err
    assert "agent-reach[mcp]" in err

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert re.search(r'^mcp = \["mcp\[cli\]>=1\.0,<2"\]$', pyproject, re.M)
    all_extra = re.search(r"^all = \[(.*)\]$", pyproject, re.M)
    assert all_extra and '"mcp[cli]>=1.0,<2"' in all_extra.group(1)


def test_core_doctor_report_plain(monkeypatch):
    from agent_reach.core import AgentReach

    monkeypatch.setattr("agent_reach.doctor.check_all", lambda _c: {})
    monkeypatch.setattr("agent_reach.doctor.format_report", lambda _r: "[bold]x[/bold]")
    eyes = AgentReach(config=SimpleNamespace())
    assert eyes.doctor_report() == "[bold]x[/bold]"
    assert eyes.doctor_report(plain=True) == "x"


# ── B2: probes kill their whole process tree on timeout ──


def _write_shim(tmp_path, body):
    shim = tmp_path / "slow-tool"
    shim.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_probe_timeout_kills_descendants_holding_pipes(tmp_path, parent_exits):
    pid_file = tmp_path / "child.pid"
    body = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        + ("" if parent_exits else "time.sleep(60)\n")
    )
    shim = _write_shim(tmp_path, body)

    started = time.monotonic()
    result = probe.probe_command(str(shim), timeout=1)
    elapsed = time.monotonic() - started

    assert result.status == "timeout"
    assert elapsed < 6, f"probe blocked for {elapsed:.1f}s"
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while _pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child_pid), "descendant survived the probe timeout"


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable shim")
def test_probe_success_path_still_captures_output(tmp_path):
    shim = _write_shim(tmp_path, "import sys\nprint('tool 1.2.3')\nprint('warn', file=sys.stderr)\n")
    result = probe.probe_command(str(shim), timeout=10)
    assert result.status == "ok"
    assert "tool 1.2.3" in result.output and "warn" in result.output


def test_windows_tree_kill_uses_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        probe.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    killed = []
    fake_process = SimpleNamespace(pid=4242, kill=lambda: killed.append(True))

    probe._kill_process_tree(fake_process)

    assert calls == [["taskkill", "/T", "/F", "/PID", "4242"]]
    assert killed == [True]


def test_probe_delegates_to_replaced_subprocess_run(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, "v1", "")

    monkeypatch.setattr(probe.shutil, "which", lambda _c: "/opt/tool")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert probe.probe_command("tool", timeout=3).output == "v1"
    assert seen == [(["/opt/tool", "--version"], 3)]
