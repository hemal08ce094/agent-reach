# -*- coding: utf-8 -*-
"""Runs, reads, availability and SSRF guards of the research manager (offline)."""

from __future__ import annotations

import time

import pytest

from agent_reach.research import manager as manager_mod
from agent_reach.research.manager import ResearchManager, source_states
from agent_reach.research.models import ResearchError, make_item
from agent_reach.research.parse import guarded_get
from agent_reach.research.runner import SourceFailure, SourceTimeout


def doctor(**channels):
    """Doctor results: channel -> (status, active_backend)."""
    return {
        name: {"status": st, "message": f"{name} {st}\nsecond line", "active_backend": backend}
        for name, (st, backend) in channels.items()
    }


READY = doctor(
    exa_search=("warn", "Exa via mcporter"),
    github=("warn", "gh CLI"),
    youtube=("ok", "yt-dlp"),
    reddit=("off", None),
    web=("ok", "Jina Reader"),
)


def wait_done(get, ident, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        obj = get(ident)
        snap = obj.snapshot()
        if snap.get("finished") or snap.get("status") not in (None, "running"):
            if "finished" not in snap or snap["finished"]:
                return snap
        time.sleep(0.02)
    raise AssertionError("did not finish")


def patch_search(monkeypatch, name, fn):
    from agent_reach.research.sources import get_source

    monkeypatch.setattr(get_source(name), "search", fn)


def make(monkeypatch, results=READY, **kw):
    return ResearchManager(lambda: source_states(results), **kw)


def fake_items(source, n=2):
    return [make_item(source, title=f"{source} {i}", url=f"https://example.com/{source}/{i}") for i in range(n)]


# ── availability ──

def test_source_states_reasons():
    states = source_states(READY)
    assert states["github"].available and states["github"].backend == "gh CLI"
    assert not states["reddit"].available and states["reddit"].reason == "reddit off"
    assert not states["twitter"].available  # no doctor result
    assert source_states(None)["github"].reason.startswith("Waiting for doctor")
    unsupported = source_states(doctor(xiaohongshu=("warn", "xiaohongshu-mcp")))
    assert "doctor reports xiaohongshu-mcp" in unsupported["xiaohongshu"].reason
    assert states["xueqiu"].default_selected is False


def test_twitter_without_credentials_is_not_available(monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    states = source_states(doctor(twitter=("warn", "twitter-cli")))
    assert not states["twitter"].available and "Cookie-Editor" in states["twitter"].reason


# ── runs ──

def test_run_streams_per_source_and_skips_unavailable(monkeypatch):
    patch_search(monkeypatch, "github", lambda q, n, ctx: fake_items("github"))

    def slow_youtube(q, n, ctx):
        time.sleep(0.3)
        return fake_items("youtube", 1)

    patch_search(monkeypatch, "youtube", slow_youtube)
    mgr = make(monkeypatch)
    run = mgr.start_run("notes", ["github", "youtube", "reddit"], 5)
    first = run.snapshot()
    assert first["sources"]["reddit"]["status"] == "skipped"
    assert first["sources"]["reddit"]["error"] == "reddit off"
    assert first["sources"]["github"]["commands"][0].startswith("gh search repos")
    time.sleep(0.1)
    partial = run.snapshot()
    assert partial["sources"]["github"]["status"] == "done"
    assert partial["sources"]["youtube"]["status"] == "running"
    assert partial["finished"] is False
    final = wait_done(mgr.get_run, run.id)
    assert final["sources"]["youtube"]["status"] == "done"
    assert len(final["sources"]["github"]["items"]) == 2


def test_errors_and_timeouts_are_per_source(monkeypatch):
    def boom(q, n, ctx):
        raise SourceFailure("gh exited with code 4: auth required")

    def late(q, n, ctx):
        raise SourceTimeout("yt-dlp did not finish within 25s and was stopped")

    patch_search(monkeypatch, "github", boom)
    patch_search(monkeypatch, "youtube", late)
    patch_search(monkeypatch, "web", lambda q, n, ctx: (_ for _ in ()).throw(KeyError("x")))
    mgr = make(monkeypatch)
    snap = wait_done(mgr.get_run, mgr.start_run("q", ["github", "youtube", "web"]).id)
    assert snap["sources"]["github"] == {**snap["sources"]["github"], "status": "error",
                                         "error": "gh exited with code 4: auth required"}
    assert snap["sources"]["youtube"]["status"] == "timeout"
    assert snap["sources"]["web"]["status"] == "error"
    assert "KeyError" in snap["sources"]["web"]["error"]


def test_watchdog_times_out_a_hung_library_source(monkeypatch):
    monkeypatch.setattr(manager_mod, "_WATCHDOG_GRACE", 0.0)
    patch_search(monkeypatch, "github", lambda q, n, ctx: time.sleep(2) or [])
    mgr = make(monkeypatch, timeout=0.2)
    snap = wait_done(mgr.get_run, mgr.start_run("q", ["github"]).id, timeout=3)
    assert snap["sources"]["github"]["status"] == "timeout"


def test_concurrency_limit_and_cancel(monkeypatch):
    patch_search(monkeypatch, "github", lambda q, n, ctx: time.sleep(1) or [])
    mgr = make(monkeypatch, max_runs=2)
    a = mgr.start_run("one", ["github"])
    mgr.start_run("two", ["github"])
    with pytest.raises(ResearchError) as exc:
        mgr.start_run("three", ["github"])
    assert exc.value.status == 429
    cancelled = mgr.cancel_run(a.id).snapshot()
    assert cancelled["sources"]["github"]["status"] == "cancelled" and cancelled["finished"]
    mgr.start_run("three", ["github"])  # a slot is free again


@pytest.mark.parametrize("payload,limit", [
    ([], None), (["nope"], None), (["webpage"], None), ("github", None), (["github"], 0),
    (["github"], 26), (["github"], True), (["github"], "5"),
])
def test_run_input_validation(monkeypatch, payload, limit):
    with pytest.raises(ResearchError) as exc:
        make(monkeypatch).start_run("q", payload, limit)
    assert exc.value.status == 400


# ── reads ──

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "http://１２７.０.０.１/",
    "http://localhost:8080/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "javascript:alert(1)",
    "https://user:pw@example.com/",
    "http://0x7f000001/",
    "not a url",
])
def test_read_rejects_non_public_urls(monkeypatch, url):
    with pytest.raises(ResearchError) as exc:
        make(monkeypatch).start_read(url=url)
    assert exc.value.status == 400


def test_read_rejects_items_with_private_urls(monkeypatch):
    item = {"source": "web", "title": "x", "url": "http://169.254.169.254/"}
    with pytest.raises(ResearchError):
        make(monkeypatch).start_read(item=item)


def test_read_routes_to_platform_or_falls_back_to_jina(monkeypatch):
    from agent_reach.research.models import make_read
    from agent_reach.research.sources import get_source

    calls = []

    def fake_read(name):
        def read(url, ctx):
            calls.append((name, url))
            return make_read(title="T", url=url, source=name, text="full text")
        return read

    for name in ("github", "webpage", "reddit"):
        monkeypatch.setattr(get_source(name), "read", fake_read(name))
    mgr = make(monkeypatch)
    task = mgr.start_read(url="https://github.com/acme/notes")
    assert wait_done(mgr.get_read, task.id)["result"]["text"] == "full text"
    reddit = mgr.start_read(url="https://www.reddit.com/r/x/comments/abc123/t/")
    snap = wait_done(mgr.get_read, reddit.id)
    assert snap["source"] == "webpage" and "Reddit is not set up" in snap["note"]
    assert calls == [("github", "https://github.com/acme/notes"),
                     ("webpage", "https://www.reddit.com/r/x/comments/abc123/t/")]


def test_read_failure_and_unavailable_reader(monkeypatch):
    from agent_reach.research.sources import get_source

    monkeypatch.setattr(get_source("webpage"), "read",
                        lambda url, ctx: (_ for _ in ()).throw(SourceFailure("anti-bot page")))
    mgr = make(monkeypatch)
    snap = wait_done(mgr.get_read, mgr.start_read(url="https://example.com/a").id)
    assert snap["status"] == "error" and snap["error"] == "anti-bot page"
    offline = ResearchManager(lambda: source_states(doctor(web=("warn", None))))
    task = offline.start_read(url="https://example.com/a")
    assert task.status == "error" and "No reader" in task.error


# ── guarded fetch ──

def test_guarded_get_refuses_hosts_resolving_to_private_addresses(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(SourceFailure, match="private address"):
        guarded_get("https://internal-looking.example.com/feed")


def test_guarded_get_validates_redirect_targets(monkeypatch):
    import socket

    from agent_reach.research import parse

    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    handler = parse._PublicRedirects()
    with pytest.raises(SourceFailure, match="non-public"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/admin")
