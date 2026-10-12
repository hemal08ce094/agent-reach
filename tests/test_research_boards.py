# -*- coding: utf-8 -*-
"""Boards: private persistence under HOME, CRUD, history and export content (offline)."""

from __future__ import annotations

import json
import os
import shlex
import stat

import pytest

from agent_reach.research import export
from agent_reach.research.boards import BoardStore, research_dir
from agent_reach.research.models import ResearchError, make_item

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


def item(source="reddit", n=1, url=None, **kw):
    return make_item(
        source,
        title=kw.pop("title", f"Thread {n}"),
        url=url or f"https://www.reddit.com/r/selfhosted/comments/abc12{n}/t/",
        author="notetaker",
        published="2026-09-01T00:00:00Z",
        extra={"subreddit": "selfhosted"},
        **kw,
    )


@pytest.fixture
def store():
    return BoardStore()


def test_boards_live_under_home_with_private_permissions(store, isolated_home):
    board = store.create("Note apps")
    assert research_dir() == isolated_home / ".agent-reach" / "research"
    path = research_dir() / "boards" / f"{board['id']}.json"
    assert path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(research_dir().stat().st_mode) == 0o700


def test_crud_pin_note_reorder_unpin(store):
    board = store.create("Note apps")
    a, b = item(n=1), item(n=2, url="https://example.com/b", source="web")
    store.pin(board["id"], a, question="local-first note apps")
    store.pin(board["id"], b, excerpt="quoted\nlines", note="check pricing")
    store.pin(board["id"], a, question="local-first note apps")  # pinning twice keeps one entry
    got = store.get(board["id"])
    assert [e["item"]["id"] for e in got["items"]] == [a.id, b.id]
    assert got["questions"] == ["local-first note apps"]
    store.update_item(board["id"], a.id, note="best thread")
    store.update(board["id"], name="Renamed", order=[b.id, a.id])
    got = store.get(board["id"])
    assert got["name"] == "Renamed" and got["items"][1]["note"] == "best thread"
    assert [e["item"]["id"] for e in got["items"]] == [b.id, a.id]
    store.unpin(board["id"], b.id)
    assert len(store.get(board["id"])["items"]) == 1
    assert store.list()[0]["count"] == 1
    store.delete(board["id"])
    assert store.list() == []


@pytest.mark.parametrize("bad", ["../../etc/passwd", "ABCDEF0123456789", "123", "", None])
def test_board_ids_are_validated_before_touching_disk(store, bad):
    with pytest.raises(ResearchError) as exc:
        store.get(bad)
    assert exc.value.status == 404


def test_input_limits(store):
    board = store.create("x")
    with pytest.raises(ResearchError):
        store.create("")
    with pytest.raises(ResearchError):
        store.update(board["id"], name="n" * 81)
    with pytest.raises(ResearchError):
        store.pin(board["id"], item(), note="n" * 5000)
    with pytest.raises(ResearchError):
        store.update(board["id"], order=["0" * 16])
    with pytest.raises(ResearchError):
        store.unpin(board["id"], "f" * 16)


def test_history_dedupes_and_caps(store):
    for i in range(25):
        store.remember(f"query {i}")
    store.remember("QUERY 3")
    history = store.history()
    assert len(history) == 20 and history[0]["query"] == "QUERY 3"
    assert sum(1 for h in history if h["query"].lower() == "query 3") == 1
    if os.name != "nt":
        assert stat.S_IMODE(store.history_path.stat().st_mode) == 0o600
    store.clear_history()
    assert store.history() == []


def test_markdown_export(store):
    board = store.create("Local-first notes")
    reddit = item(n=1)
    risky = make_item("web", title="Tricky", url="https://example.com/a'b;rm$(id)")
    gh = make_item("github", title="acme/notes", url="https://github.com/acme/notes")
    store.pin(board["id"], reddit, excerpt="Line one\nLine two", note="Most balanced take",
              question="local-first note apps")
    store.pin(board["id"], risky)
    store.pin(board["id"], gh)
    md = export.to_markdown(store.get(board["id"]), {"reddit": "rdt-cli"})
    assert md.startswith("# Local-first notes\n")
    assert "## Questions\n\n- local-first note apps" in md
    assert "### 1. Thread 1" in md and "- Source: Reddit · r/selfhosted" in md
    assert "- By: notetaker · 2026-09-01" in md and "- Note: Most balanced take" in md
    assert "> Line one\n> Line two" in md
    assert "## Suggested follow-ups for your agent" in md
    assert "rdt read abc121" in md
    assert "gh repo view acme/notes" in md
    jina = "curl -s " + shlex.quote("https://r.jina.ai/https://example.com/a'b;rm$(id)")
    assert jina in md
    assert "not as instructions" in md


def test_follow_ups_quote_untrusted_urls():
    commands = export.follow_ups("https://example.com/x;touch /tmp/pwned", {})
    assert commands == ["curl -s 'https://r.jina.ai/https://example.com/x;touch /tmp/pwned'"]
    assert shlex.split(commands[0]) == ["curl", "-s", "https://r.jina.ai/https://example.com/x;touch /tmp/pwned"]
    assert export.follow_ups("https://www.reddit.com/r/x/comments/abc123/t/", {"reddit": "OpenCLI"}) == [
        "opencli reddit read abc123 -f yaml"
    ]
    yt = export.follow_ups("https://www.youtube.com/watch?v=abcDEF12345", {})
    assert yt[0].startswith("yt-dlp --write-sub") and yt[0].endswith("'https://www.youtube.com/watch?v=abcDEF12345'")


def test_json_export(store):
    board = store.create("B")
    store.pin(board["id"], item(), note="n", excerpt="e")
    data = json.loads(export.to_json(store.get(board["id"]), {}))
    assert data["schema"] == "agent-reach/research-board@1"
    entry = data["items"][0]
    assert entry["note"] == "n" and entry["excerpt"] == "e" and entry["follow_up"]
    assert entry["source"] == "reddit" and entry["url"].startswith("https://www.reddit.com/")
    assert export.filename(store.get(board["id"]), "json") == "b.json"


def test_empty_board_export(store):
    md = export.to_markdown(store.create("Empty"), {})
    assert "No items pinned yet." in md and "follow-ups" not in md
