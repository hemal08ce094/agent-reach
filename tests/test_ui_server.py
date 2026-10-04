# -*- coding: utf-8 -*-
"""Security and API tests for the local dashboard server (all offline)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time

import pytest
import yaml
from loguru import logger

from agent_reach.config import Config
from agent_reach.ui import server as ui_server
from agent_reach.ui.jobs import JobManager
from agent_reach.ui.server import DashboardServer, DashboardState

TOKEN = "test-token-0123456789abcdefghijklmnop"
FAKE_SECRETS = {
    "github_token": "ghp_FAKESECRETVALUE1234567890",
    "twitter_auth_token": "authtokenFAKE0987654321abcdef",
    "twitter_ct0": "ct0FAKEvalue5555555555555555",
    "groq_api_key": "gsk_FAKEGROQKEY4444444444",
    "proxy": "http://user:hunter2pass@10.0.0.1:8080",
    "xueqiu_cookie": "xq_a_token=FAKEXUEQIUCOOKIE777",
}


def fake_doctor_result(status="ok"):
    return {
        "status": status,
        "name": "Fake channel",
        "message": f"fake {status}",
        "tier": 0,
        "backends": ["fake"],
        "active_backend": "fake" if status in ("ok", "warn") else None,
    }


def sleeper_argv(seconds: float):
    script = (
        "import sys, time\n"
        "print('\\x1b[32mstarting\\x1b[0m job', flush=True)\n"
        f"time.sleep({seconds})\n"
        "print('done', flush=True)\n"
    )

    def factory(*args):
        return [sys.executable, "-c", script]

    return factory


class Client:
    def __init__(self, port: int):
        self.port = port

    def request(self, method, path, *, body=None, headers=None, token=TOKEN, host=None,
                content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            hdrs["X-Agent-Reach-Token"] = token
        payload = None
        if body is not None:
            payload = body if isinstance(body, (bytes, str)) else json.dumps(body)
            if content_type:
                hdrs["Content-Type"] = content_type
        hdrs.update(headers or {})
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def json(self, method, path, **kw):
        resp, data = self.request(method, path, **kw)
        return resp.status, (json.loads(data) if data else None), resp


@pytest.fixture
def make_server():
    servers = []

    def _make(**state_kwargs):
        state_kwargs.setdefault("check_all", lambda config: {"github": fake_doctor_result("ok")})
        state_kwargs.setdefault("check_one", lambda name, config: fake_doctor_result("warn")
                                if name == "github" else None)
        state = DashboardState(TOKEN, **state_kwargs)
        srv = DashboardServer("127.0.0.1", 0, state)
        thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                                  daemon=True)
        thread.start()
        servers.append(srv)
        return srv, Client(state.port)

    yield _make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def client(make_server):
    return make_server()[1]


@pytest.fixture
def seeded_secrets(isolated_home):
    config = Config()
    for key, value in FAKE_SECRETS.items():
        config.data[key] = value
    config.save()
    return FAKE_SECRETS


# ── auth, host and origin ────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/status", "/api/channels", "/api/doctor", "/api/jobs"])
def test_api_requires_token(client, path):
    status, body, _ = client.json("GET", path, token=None)
    assert status == 401
    status, body, _ = client.json("GET", path, token="wrong-token")
    assert status == 401
    status, _, _ = client.json("GET", path)
    assert status == 200


def test_post_endpoints_require_token(client):
    for path in ("/api/doctor", "/api/jobs", "/api/configure", "/api/channels/github/check"):
        status, _, _ = client.json("POST", path, body={}, token=None)
        assert status == 401, path


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:80", "127.0.0.1:1", "0.0.0.0"])
def test_rejects_foreign_host_header(client, host):
    resp, _ = client.request("GET", "/api/status", host=host)
    assert resp.status == 403
    resp, _ = client.request("GET", "/", host=host)
    assert resp.status == 403


def test_accepts_localhost_host_header(client):
    resp, _ = client.request("GET", "/api/status", host=f"localhost:{client.port}")
    assert resp.status == 200


def test_rejects_cross_origin(client):
    status, _, _ = client.json("POST", "/api/doctor", body={},
                               headers={"Origin": "http://evil.example.com"})
    assert status == 403
    status, _, _ = client.json("GET", "/api/status", headers={"Origin": "null"})
    assert status == 403
    status, _, _ = client.json("GET", "/api/status",
                               headers={"Origin": f"http://127.0.0.1:{client.port}"})
    assert status == 200


def test_rejects_cross_site_fetch_metadata(client):
    status, _, _ = client.json("GET", "/api/status", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_mutating_endpoints_require_json_and_post(client):
    status, _, _ = client.json("POST", "/api/doctor", body="a=1",
                               content_type="application/x-www-form-urlencoded")
    assert status == 415
    status, _, _ = client.json("POST", "/api/configure", body="key=proxy", content_type="text/plain")
    assert status == 415
    status, _, _ = client.json("GET", "/api/configure")
    assert status == 405
    status, _, _ = client.json("PUT", "/api/jobs", body={})
    assert status == 405
    status, _, _ = client.json("POST", "/api/doctor", body="{not json")
    assert status == 400


def test_refuses_non_loopback_bind():
    with pytest.raises(ValueError):
        DashboardServer("0.0.0.0", 0, DashboardState(TOKEN))


# ── headers and static files ─────────────────────────────────────────────────


def test_security_headers_on_api_and_static(client):
    for path, token in (("/api/status", TOKEN), ("/", None), ("/static/app.js", None)):
        resp, _ = client.request("GET", path, token=token)
        assert resp.status == 200
        csp = resp.getheader("Content-Security-Policy")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert resp.getheader("Referrer-Policy") == "no-referrer"
        assert resp.getheader("X-Frame-Options") == "DENY"
    resp, _ = client.request("GET", "/api/status")
    assert resp.getheader("Cache-Control") == "no-store"


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/", "text/html"),
        ("/index.html", "text/html"),
        ("/static/app.css", "text/css"),
        ("/static/app.js", "text/javascript"),
        ("/static/favicon.svg", "image/svg+xml"),
    ],
)
def test_static_files_served_with_content_type(client, path, content_type):
    resp, data = client.request("GET", path, token=None)
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith(content_type)
    assert data


def test_index_has_no_inline_script(client):
    _, data = client.request("GET", "/", token=None)
    html = data.decode("utf-8")
    assert "<script src=\"/static/app.js\"" in html
    assert "<script>" not in html
    assert " style=" not in html
    assert "onclick=" not in html


@pytest.mark.parametrize(
    "path",
    [
        "/static/../cli.py",
        "/static/..%2fcli.py",
        "/static/%2e%2e/cli.py",
        "/static/../../pyproject.toml",
        "/static//etc/passwd",
        "/static/",
        "/cli.py",
        "/static/app.js/../../server.py",
        "/agent_reach/cli.py",
    ],
)
def test_no_path_traversal(client, path):
    resp, data = client.request("GET", path, token=None)
    assert resp.status == 404
    assert b"import" not in data


# ── channels never leak secrets ──────────────────────────────────────────────


def test_channels_never_contain_secret_values(client, seeded_secrets):
    for path in ("/api/channels", "/api/status"):
        resp, data = client.request("GET", path)
        assert resp.status == 200
        text = data.decode("utf-8")
        for value in seeded_secrets.values():
            assert value not in text
        assert "hunter2pass" not in text
        assert "FAKESECRET" not in text


def test_channels_report_keys_as_set(client, seeded_secrets):
    _, body, _ = client.json("GET", "/api/channels")
    channels = {c["name"]: c for c in body["channels"]}
    github_keys = {k["key"]: k for k in channels["github"]["config_keys"]}
    assert github_keys["github_token"] == {"key": "github_token", "set": True, "source": "config"}
    assert set(github_keys["github_token"]) == {"key", "set", "source"}
    twitter_keys = {k["key"]: k["set"] for k in channels["twitter"]["config_keys"]}
    assert twitter_keys == {"twitter_auth_token": True, "twitter_ct0": True}
    bili = {k["key"]: k["set"] for k in channels["bilibili"]["config_keys"]}
    assert bili == {"bilibili_sessdata": False, "bilibili_csrf": False}


def test_channels_metadata_shape(client):
    _, body, _ = client.json("GET", "/api/channels")
    from agent_reach.channels import get_all_channels

    names = [c["name"] for c in body["channels"]]
    assert names == [ch.name for ch in get_all_channels()]
    twitter = next(c for c in body["channels"] if c["name"] == "twitter")
    assert twitter["display_name"] == "Twitter/X"
    assert twitter["guide"].startswith("# Twitter")
    assert twitter["install"]["channels"] == ["twitter"]
    assert any("twitter feed" in s["code"] for s in twitter["snippets"])
    assert twitter["configure"][0]["cli_key"] == "twitter-cookies"


def test_env_only_key_reported_as_env(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ENVONLYSECRET99999")
    resp, data = client.request("GET", "/api/channels")
    assert b"ghp_ENVONLYSECRET99999" not in data
    channels = {c["name"]: c for c in json.loads(data)["channels"]}
    key = channels["github"]["config_keys"][0]
    assert key == {"key": "github_token", "set": True, "source": "env"}


# ── doctor and single-channel test ───────────────────────────────────────────


def test_doctor_uses_check_all(make_server):
    calls = []

    def fake_check_all(config):
        calls.append(config)
        return {"github": fake_doctor_result("ok"), "reddit": fake_doctor_result("timeout")}

    _, client = make_server(check_all=fake_check_all)
    status, body, _ = client.json("GET", "/api/doctor")
    assert body == {"results": None, "checked_at": None}
    status, body, _ = client.json("POST", "/api/doctor", body={})
    assert status == 200
    assert len(calls) == 1 and calls[0].read_only
    assert body["results"]["reddit"]["status"] == "timeout"
    assert isinstance(body["checked_at"], float)
    status, cached, _ = client.json("GET", "/api/doctor")
    assert cached["results"] == body["results"]


def test_doctor_endpoint_patches_library(client, monkeypatch):
    """Without an injected checker the endpoint calls agent_reach.doctor.check_all."""
    from agent_reach import doctor

    state = DashboardState(TOKEN)
    monkeypatch.setattr(doctor, "check_all", lambda config: {"x": fake_doctor_result("off")})
    assert state.run_doctor()["results"] == {"x": fake_doctor_result("off")}


def test_channel_check(client):
    status, body, _ = client.json("POST", "/api/channels/github/check", body={})
    assert status == 200
    assert body["name"] == "github"
    assert body["result"]["status"] == "warn"
    status, _, _ = client.json("POST", "/api/channels/nope/check", body={})
    assert status == 404


# ── configure ────────────────────────────────────────────────────────────────


def test_configure_rejects_non_allowlisted_keys(make_server):
    calls = []
    _, client = make_server(configure_runner=lambda k, v: calls.append((k, v)) or (0, ""))
    for key in ("github_token", "../etc", "proxy; rm -rf /", "--from-browser", "", None, 5):
        status, body, _ = client.json("POST", "/api/configure", body={"key": key, "value": "x"})
        assert status == 400, key
    status, _, _ = client.json("POST", "/api/configure", body={"key": "proxy", "value": ""})
    assert status == 400
    status, _, _ = client.json("POST", "/api/configure",
                               body={"key": "youtube-cookies", "value": "netscape"})
    assert status == 400
    assert calls == []


def test_configure_passes_secret_on_stdin_and_redacts(make_server, monkeypatch, capsys):
    secret = "ghp_SUPERSECRETTOKEN_abcdef123456"
    seen = {}

    class Done:
        returncode = 0
        stdout = f"✅ GitHub token configured! echo {secret}\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Done()

    monkeypatch.setattr(ui_server.subprocess, "run", fake_run)
    logs = []
    sink = logger.add(lambda m: logs.append(str(m)), level="DEBUG")
    try:
        _, client = make_server()
        status, body, _ = client.json("POST", "/api/configure",
                                      body={"key": "github-token", "value": secret})
    finally:
        logger.remove(sink)
    assert status == 200 and body["ok"] is True
    assert seen["argv"][-3:] == ["configure", "github-token", "--stdin"]
    assert secret not in " ".join(seen["argv"])
    assert seen["kwargs"]["input"] == secret
    assert seen["kwargs"].get("shell", False) is False
    assert secret not in body["output"] and "[redacted]" in body["output"]
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert all(secret not in line for line in logs)


def test_configure_end_to_end_with_real_cli(client, isolated_home):
    """The real CLI subprocess writes the key; the API reports it set, never the value."""
    secret = "ghp_E2ESECRET_0000111122223333"
    status, body, _ = client.json("POST", "/api/configure",
                                  body={"key": "github-token", "value": secret})
    assert status == 200, body
    assert body["ok"] is True, body
    assert body["keys"] == [{"key": "github_token", "set": True, "source": "config"}]
    data = yaml.safe_load((isolated_home / ".agent-reach" / "config.yaml").read_text())
    assert data["github_token"] == secret
    resp, raw = client.request("GET", "/api/channels")
    assert secret.encode() not in raw


# ── jobs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "rm", "params": {}},
        {"action": "shell", "params": {"cmd": "ls"}},
        {"action": None},
        {"action": "install", "params": {"channels": ["twitter; rm -rf ~"]}},
        {"action": "install", "params": {"channels": ["github"]}},
        {"action": "install", "params": {"channels": ["--system"]}},
        {"action": "install", "params": {"channels": "twitter"}},
        {"action": "install", "params": {"channels": ["twitter"], "dry_run": "yes"}},
        {"action": "install", "params": []},
        {"action": "skill_install", "params": {"extra": "--force"}},
        {"action": "configure_browser", "params": {"browser": "safari", "platform": "bilibili"}},
        {"action": "configure_browser", "params": {"browser": "chrome", "platform": "twitter"}},
    ],
)
def test_job_allowlist_rejects_invalid(make_server, payload):
    started = []
    _, client = make_server(jobs=JobManager(argv_factory=lambda *a: started.append(a) or ["x"]))
    status, body, _ = client.json("POST", "/api/jobs", body=payload)
    assert status == 400, body
    assert started == []


def test_job_builds_expected_argv():
    from agent_reach.ui.jobs import build_job

    assert build_job("install", {"channels": ["twitter", "reddit"]})[0] == [
        "install", "--env=auto", "--system", "--channels=reddit,twitter"]
    assert build_job("install", {"channels": ["twitter"], "dry_run": True})[0] == [
        "install", "--env=auto", "--dry-run", "--channels=twitter"]
    assert build_job("install", {})[0] == ["install", "--env=auto", "--system"]
    assert build_job("skill_install", {})[0] == ["skill", "--install"]
    assert build_job("skill_uninstall", None)[0] == ["skill", "--uninstall"]
    assert build_job("check_update", {})[0] == ["check-update"]
    assert build_job("configure_browser", {"browser": "firefox", "platform": "xueqiu"})[0] == [
        "configure", "--from-browser", "firefox", "--platform", "xueqiu"]


def test_job_output_is_streamed_and_ansi_stripped(make_server):
    _, client = make_server(jobs=JobManager(argv_factory=sleeper_argv(0.1)))
    status, job, _ = client.json("POST", "/api/jobs",
                                 body={"action": "install", "params": {"channels": ["twitter"]}})
    assert status == 202
    assert job["command"] == "agent-reach install --env=auto --system --channels=twitter"
    deadline = time.time() + 15
    offset, text = 0, ""
    while time.time() < deadline:
        _, snap, _ = client.json("GET", f"/api/jobs/{job['id']}?offset={offset}")
        text += snap["output"]
        offset = snap["offset"]
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "succeeded" and snap["exit_code"] == 0
    assert "starting job" in text and "done" in text
    assert "\x1b" not in text
    _, again, _ = client.json("GET", f"/api/jobs/{job['id']}?offset={offset}")
    assert again["output"] == ""
    _, listing, _ = client.json("GET", "/api/jobs")
    assert listing["jobs"][0]["id"] == job["id"] and "output" not in listing["jobs"][0]


def test_only_one_job_at_a_time(make_server):
    srv, client = make_server(jobs=JobManager(argv_factory=sleeper_argv(30)))
    status, first, _ = client.json("POST", "/api/jobs",
                                   body={"action": "install", "params": {"channels": ["twitter"]}})
    assert status == 202
    status, body, _ = client.json("POST", "/api/jobs",
                                  body={"action": "install", "params": {"channels": ["reddit"]}})
    assert status == 409
    status, body, _ = client.json("POST", "/api/jobs", body={"action": "check_update"})
    assert status == 409
    srv.state.jobs.shutdown()
    deadline = time.time() + 10
    while time.time() < deadline:
        _, snap, _ = client.json("GET", f"/api/jobs/{first['id']}")
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] != "running"


def test_job_timeout_stops_process(make_server):
    manager = JobManager(argv_factory=sleeper_argv(30))
    _, client = make_server(jobs=manager)
    from agent_reach.ui import jobs as jobs_mod

    original = jobs_mod.SHORT_TIMEOUT
    jobs_mod.SHORT_TIMEOUT = 0.3
    try:
        status, job, _ = client.json("POST", "/api/jobs", body={"action": "check_update"})
    finally:
        jobs_mod.SHORT_TIMEOUT = original
    assert status == 202
    deadline = time.time() + 10
    while time.time() < deadline:
        _, snap, _ = client.json("GET", f"/api/jobs/{job['id']}")
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "timeout"


def test_unknown_job_and_bad_offset(client):
    status, _, _ = client.json("GET", "/api/jobs/doesnotexist")
    assert status == 404


def test_strip_ansi():
    from agent_reach.ui.jobs import strip_ansi

    assert strip_ansi("\x1b[1;31mred\x1b[0m\r\nnext\x1b]0;title\x07") == "red\nnext"
