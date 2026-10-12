# -*- coding: utf-8 -*-
"""Research API: auth, validation, 429, boards over HTTP and static files (offline)."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from agent_reach.research import ResearchManager, source_states
from agent_reach.research.models import make_item
from agent_reach.research.sources import get_source
from agent_reach.ui.server import STATIC_FILES, DashboardServer, DashboardState

TOKEN = "research-token-0123456789abcdefghijkl"

DOCTOR = {
    "github": {"status": "warn", "message": "gh ok", "active_backend": "gh CLI"},
    "youtube": {"status": "ok", "message": "yt ok", "active_backend": "yt-dlp"},
    "reddit": {"status": "off", "message": "No Reddit backend installed.\nmore", "active_backend": None},
    "web": {"status": "ok", "message": "jina", "active_backend": "Jina Reader"},
}


class Client:
    def __init__(self, port: int):
        self.port = port

    def call(self, method, path, body=None, *, token=TOKEN, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            hdrs["X-Agent-Reach-Token"] = token
        payload = raw
        if body is not None:
            payload = json.dumps(body)
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = data
        return resp.status, parsed, resp


@pytest.fixture
def api(monkeypatch):
    doctor_runs = []

    def check_all(config):
        doctor_runs.append(1)
        return DOCTOR

    state = DashboardState(TOKEN, check_all=check_all, check_one=lambda n, c: None)
    srv = DashboardServer("127.0.0.1", 0, state)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    client = Client(state.port)
    client.state = state
    client.doctor_runs = doctor_runs
    yield client
    srv.shutdown()
    srv.server_close()


def poll(client, path, done):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, data, _ = client.call("GET", path)
        assert status == 200
        if done(data):
            return data
        time.sleep(0.03)
    raise AssertionError("timed out polling " + path)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/research/sources"),
    ("POST", "/api/research/search"),
    ("GET", "/api/research/boards"),
    ("DELETE", "/api/research/boards/0123456789abcdef"),
    ("PATCH", "/api/research/boards/0123456789abcdef"),
])
def test_token_required(api, method, path):
    status, data, _ = api.call(method, path, {} if method in ("POST", "PATCH") else None, token="wrong")
    assert status == 401


def test_cross_origin_and_bad_host_rejected(api):
    status, _, _ = api.call("GET", "/api/research/sources", headers={"Origin": "https://evil.example"})
    assert status == 403
    status, _, _ = api.call("GET", "/api/research/sources", headers={"Host": "evil.example:80"})
    assert status == 403


def test_sources_runs_doctor_once_and_explains_unavailable(api):
    status, data, resp = api.call("GET", "/api/research/sources")
    assert status == 200 and "default-src 'self'" in resp.getheader("Content-Security-Policy")
    by_name = {s["name"]: s for s in data["sources"]}
    assert by_name["github"]["available"] and by_name["github"]["backend"] == "gh CLI"
    assert not by_name["reddit"]["available"] and by_name["reddit"]["reason"] == "No Reddit backend installed."
    assert by_name["rss"]["searchable"] is False
    api.call("GET", "/api/research/sources")
    assert len(api.doctor_runs) == 1


def test_search_flow_and_history(api, monkeypatch):
    monkeypatch.setattr(get_source("github"), "search", lambda q, n, ctx: [
        make_item("github", title="acme/notes", url="https://github.com/acme/notes")])
    status, run, _ = api.call("POST", "/api/research/search",
                              {"query": "local-first notes", "sources": ["github", "reddit"], "limit": 5})
    assert status == 202 and run["sources"]["reddit"]["status"] == "skipped"
    final = poll(api, f"/api/research/runs/{run['id']}", lambda d: d["finished"])
    assert final["sources"]["github"]["items"][0]["title"] == "acme/notes"
    status, hist, _ = api.call("GET", "/api/research/history")
    assert hist["history"][0]["query"] == "local-first notes"
    assert api.call("DELETE", "/api/research/history")[0] == 200
    assert api.call("GET", "/api/research/history")[1]["history"] == []


@pytest.mark.parametrize("body,code", [
    ({"query": "", "sources": ["github"]}, 400),
    ({"query": "a\nb", "sources": ["github"]}, 400),
    ({"query": "ok", "sources": ["../../x"]}, 400),
    ({"query": "ok", "sources": ["github"], "limit": 999}, 400),
    ({"query": "ok", "sources": ["github"], "shell": "rm"}, 400),
])
def test_search_validation(api, body, code):
    assert api.call("POST", "/api/research/search", body)[0] == code


def test_search_requires_json_content_type(api):
    status, _, _ = api.call("POST", "/api/research/search", raw=b"query=x",
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 415


def test_concurrent_run_limit_returns_429(api, monkeypatch):
    monkeypatch.setattr(get_source("github"), "search", lambda q, n, ctx: time.sleep(1) or [])
    body = {"query": "q", "sources": ["github"]}
    first = api.call("POST", "/api/research/search", body)[1]
    assert api.call("POST", "/api/research/search", body)[0] == 202
    status, data, _ = api.call("POST", "/api/research/search", body)
    assert status == 429 and "already running" in data["error"]
    status, cancelled, _ = api.call("POST", f"/api/research/runs/{first['id']}/cancel", {})
    assert status == 200 and cancelled["sources"]["github"]["status"] == "cancelled"
    assert api.call("POST", "/api/research/search", body)[0] == 202


def test_unknown_run_and_bad_ids(api):
    assert api.call("GET", "/api/research/runs/0123456789abcdef")[0] == 404
    assert api.call("GET", "/api/research/runs/../../etc")[0] == 404
    assert api.call("PUT", "/api/research/boards")[0] == 405


@pytest.mark.parametrize("body", [
    {"url": "http://127.0.0.1:8080/"},
    {"url": "http://169.254.169.254/latest/meta-data/"},
    {"url": "file:///etc/passwd"},
    {"url": "http://１２７.０.０.１/"},
    {"item": {"source": "web", "title": "x", "url": "http://10.0.0.1/"}},
    {"url": "https://example.com", "item": {}},
    {},
])
def test_read_rejects_unsafe_targets(api, body):
    assert api.call("POST", "/api/research/read", body)[0] == 400


def test_read_flow(api, monkeypatch):
    from agent_reach.research.models import make_read

    monkeypatch.setattr(get_source("webpage"), "read",
                        lambda url, ctx: make_read(title="Doc", url=url, source="webpage", text="# Hi\n<script>x</script>"))
    status, task, _ = api.call("POST", "/api/research/read", {"url": "https://example.com/post"})
    assert status == 202
    done = poll(api, f"/api/research/reads/{task['id']}", lambda d: d["status"] != "running")
    assert done["result"]["title"] == "Doc"
    assert "<script>" in done["result"]["text"]  # returned as data; the page escapes it


def test_boards_over_http(api):
    status, board, _ = api.call("POST", "/api/research/boards", {"name": "Note apps"})
    assert status == 201
    bid = board["id"]
    item = make_item("reddit", title="Thread", url="https://www.reddit.com/r/x/comments/abc123/t/").to_dict()
    status, board, _ = api.call("POST", f"/api/research/boards/{bid}/items",
                                {"item": item, "excerpt": "quote", "question": "note apps"})
    assert status == 200 and board["items"][0]["excerpt"] == "quote"
    iid = item["id"]
    status, board, _ = api.call("PATCH", f"/api/research/boards/{bid}/items/{iid}", {"note": "good"})
    assert board["items"][0]["note"] == "good"
    status, board, _ = api.call("PATCH", f"/api/research/boards/{bid}", {"name": "Renamed"})
    assert board["name"] == "Renamed"
    status, md, _ = api.call("GET", f"/api/research/boards/{bid}/export?format=md")
    assert status == 200 and md["filename"] == "renamed.md" and md["mime"] == "text/markdown"
    assert "reddit read abc123" in md["content"]
    status, js, _ = api.call("GET", f"/api/research/boards/{bid}/export?format=json")
    assert json.loads(js["content"])["items"][0]["note"] == "good"
    assert api.call("GET", f"/api/research/boards/{bid}/export?format=html")[0] == 400
    status, board, _ = api.call("DELETE", f"/api/research/boards/{bid}/items/{iid}")
    assert board["items"] == []
    assert api.call("GET", "/api/research/boards")[1]["boards"][0]["count"] == 0
    assert api.call("DELETE", f"/api/research/boards/{bid}")[0] == 200
    assert api.call("GET", f"/api/research/boards/{bid}")[0] == 404


def test_pin_rejects_bad_items(api):
    bid = api.call("POST", "/api/research/boards", {"name": "B"})[1]["id"]
    bad = [
        {"item": {"source": "evil", "title": "x", "url": "https://example.com"}},
        {"item": {"source": "web", "title": "x", "url": "javascript:alert(1)"}},
        {"item": {"source": "web", "title": "x", "url": "https://example.com"}, "note": 5},
        {"item": "nope"},
    ]
    for body in bad:
        assert api.call("POST", f"/api/research/boards/{bid}/items", body)[0] == 400


@pytest.mark.parametrize("name", ["research.js", "research.css"])
def test_research_static_files_are_served(api, name):
    assert name in STATIC_FILES
    status, body, resp = api.call("GET", f"/static/{name}", token=None)
    assert status == 200 and resp.getheader("X-Content-Type-Options") == "nosniff"


def test_index_links_the_research_assets(api):
    status, body, _ = api.call("GET", "/", token=None)
    html = body.decode() if isinstance(body, bytes) else str(body)
    assert '/static/research.js' in html and '/static/research.css' in html


def test_state_accepts_injected_research_manager():
    mgr = ResearchManager(lambda: source_states(DOCTOR))
    state = DashboardState(TOKEN, research=mgr)
    assert state.research is mgr
