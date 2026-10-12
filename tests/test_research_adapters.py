# -*- coding: utf-8 -*-
"""Research adapters: argv built from the documented commands, fixture output parsed (offline)."""

from __future__ import annotations

import json
from typing import Dict, List

import pytest
import yaml

from agent_reach.research.models import validate_query
from agent_reach.research.runner import CommandResult, SourceFailure
from agent_reach.research.sources import Context, get_source
from agent_reach.research.sources.bilibili import parse_search_api
from agent_reach.research.sources.exa import parse_exa_output
from agent_reach.research.sources.github import parse_github_url
from agent_reach.research.sources.reddit import reddit_post_id
from agent_reach.research.sources.youtube import choose_track, vtt_to_text

HOSTILE = '"; rm -rf ~'


class FakeRunner:
    """Records every argv and answers with canned stdout per command prefix."""

    def __init__(self, outputs: Dict[str, object]):
        self.outputs = outputs
        self.calls: List[List[str]] = []
        self.envs: List[object] = []

    def run(self, argv, *, timeout=None, env=None, cwd=None, register=None, check=True):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self.envs.append(env)
        for prefix, out in self.outputs.items():
            if " ".join(argv).startswith(prefix):
                if isinstance(out, Exception):
                    raise out
                if callable(out):
                    out = out(argv, cwd)
                return CommandResult(argv, 0, str(out), "")
        raise AssertionError(f"unexpected command: {argv}")


def ctx_for(runner, backend, config=None) -> Context:
    return Context(runner, backend, config)


def search(name: str, backend: str, outputs: Dict[str, object], query: str = "local-first notes",
           limit: int = 5, config=None):
    runner = FakeRunner(outputs)
    source = get_source(name)
    items = source.search(query, limit, ctx_for(runner, backend, config))
    return items, runner


# ── GitHub ──

GH_REPOS = [
    {"fullName": "acme/notes", "description": "Local-first notes", "url": "https://github.com/acme/notes",
     "stargazersCount": 1200, "forksCount": 40, "updatedAt": "2026-09-01T10:00:00Z",
     "language": "Rust", "owner": {"login": "acme"}},
    {"fullName": "bad/url", "url": "http://127.0.0.1/evil"},
    "not a dict",
]


def test_github_search_argv_and_parse():
    items, runner = search("github", "gh CLI", {"gh search repos": json.dumps(GH_REPOS)}, query=HOSTILE)
    argv = runner.calls[0]
    assert argv[:3] == ["gh", "search", "repos"]
    assert argv[-2:] == ["--", HOSTILE]  # one argv element after the options terminator
    assert "--json" in argv and "--limit" in argv
    assert len(items) == 1  # the private-address URL and the string are dropped
    item = items[0]
    assert item.title == "acme/notes"
    assert item.metrics == {"stars": 1200, "forks": 40}
    assert item.author == "acme"
    assert item.extra == {"language": "Rust"}
    assert len(item.id) == 16


def test_github_malformed_output_is_a_source_error():
    with pytest.raises(SourceFailure):
        search("github", "gh CLI", {"gh search repos": "{not json"})
    with pytest.raises(SourceFailure):
        search("github", "gh CLI", {"gh search repos": json.dumps({"a": 1})})


def test_github_read_issue_and_url_parsing():
    assert parse_github_url("https://github.com/acme/notes") == ("acme", "notes", None, None)
    assert parse_github_url("https://github.com/acme/notes/issues/12") == ("acme", "notes", "issues", "12")
    assert parse_github_url("https://github.com/acme/notes/blob/main/x.py") is None
    assert parse_github_url("https://gist.github.com/acme/abc") is None
    payload = {"title": "Sync bug", "body": "It breaks", "author": {"login": "pat"},
               "comments": [{"author": {"login": "sam"}, "body": "Same here"}],
               "createdAt": "2026-01-01T00:00:00Z", "url": "u", "state": "OPEN"}
    runner = FakeRunner({"gh issue view": json.dumps(payload)})
    result = get_source("github").read("https://github.com/acme/notes/issues/12", ctx_for(runner, "gh CLI"))
    assert runner.calls[0] == ["gh", "issue", "view", "12", "-R", "acme/notes", "--json",
                               "title,body,author,comments,createdAt,url,state"]
    assert "Same here" in result.text and result.metadata["comments"] == "1"


# ── YouTube ──

YT_SEARCH = {"entries": [
    {"id": "abcDEF12345", "title": "I tried 9 note apps", "channel": "Desk Notes",
     "duration": 724, "view_count": 51000},
    {"id": "bad id!", "title": "No usable url"},
]}


def test_youtube_search_argv_and_parse():
    items, runner = search("youtube", "yt-dlp", {"yt-dlp": json.dumps(YT_SEARCH)}, query=HOSTILE, limit=3)
    assert runner.calls[0] == ["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings",
                               f"ytsearch3:{HOSTILE}"]
    assert len(items) == 1
    assert items[0].url == "https://www.youtube.com/watch?v=abcDEF12345"
    assert items[0].metrics == {"views": 51000, "duration": 724}


def test_youtube_query_starting_with_dash_stays_inside_the_ytsearch_token():
    _, runner = search("youtube", "yt-dlp", {"yt-dlp": json.dumps({"entries": []})}, query="--help")
    assert runner.calls[0][-1] == "ytsearch5:--help"


VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Hello <c>there</c>

00:00:01.500 --> 00:00:03.000
Hello there

00:00:31.000 --> 00:00:33.000
Second part
"""


def test_vtt_to_text_dedupes_and_marks_time():
    text = vtt_to_text(VTT)
    assert text == "[00:00] Hello there\n\n[00:31] Second part"


def test_choose_track_follows_the_documented_order():
    files = ["/t/x.en.vtt", "/t/x.ar-orig.vtt"]
    assert choose_track(files, {"subtitles": {"en": []}})[1].startswith("Manual")
    path, label = choose_track(files, {"language": "ar"})
    assert path.endswith("ar-orig.vtt") and label.startswith("Original")
    path, label = choose_track(["/t/x.en.vtt"], {"language": "ar"})
    assert "Machine-translated" in label
    assert choose_track([], {}) == (None, "")


def test_youtube_read_writes_subtitles_to_a_temp_dir(tmp_path):
    seen = {}

    def fake_ytdlp(argv, cwd):
        seen["cwd"] = cwd
        out_dir = cwd
        with open(f"{out_dir}/abcDEF12345.en-orig.vtt", "w", encoding="utf-8") as fh:
            fh.write(VTT)
        with open(f"{out_dir}/abcDEF12345.info.json", "w", encoding="utf-8") as fh:
            json.dump({"title": "Video", "language": "en", "channel": "Desk"}, fh)
        return ""

    runner = FakeRunner({"yt-dlp": fake_ytdlp})
    url = "https://www.youtube.com/watch?v=abcDEF12345"
    result = get_source("youtube").read(url, ctx_for(runner, "yt-dlp"))
    argv = runner.calls[0]
    assert argv[-2:] == ["--", url] and "--skip-download" in argv
    assert result.title == "Video" and "Hello there" in result.text
    assert result.note.startswith("Original speech recognition")
    import os

    assert not os.path.exists(seen["cwd"])  # temp dir cleaned up


# ── Reddit ──

RDT_YAML = yaml.safe_dump({"data": [
    {"title": "Obsidian vs Logseq after a year", "permalink": "/r/selfhosted/comments/abc123/x/",
     "author": "notetaker", "score": 412, "num_comments": 88, "subreddit": "selfhosted",
     "created_utc": 1780000000, "id": "abc123"},
    {"title": "missing link"},
]})


@pytest.mark.parametrize("backend,prefix,expected", [
    ("rdt-cli", "rdt search", ["rdt", "search", "--limit", "5", "--yaml", "--", HOSTILE]),
    ("OpenCLI", "opencli reddit", ["opencli", "reddit", "search", "-f", "yaml", "--", HOSTILE]),
])
def test_reddit_backends(backend, prefix, expected):
    items, runner = search("reddit", backend, {prefix: RDT_YAML}, query=HOSTILE)
    assert runner.calls[0] == expected
    assert len(items) == 1
    item = items[0]
    assert item.url == "https://www.reddit.com/r/selfhosted/comments/abc123/x/"
    assert item.extra["subreddit"] == "selfhosted"
    assert item.metrics == {"score": 412, "comments": 88}
    assert item.published.startswith("2026-")


def test_reddit_read_and_post_ids():
    assert reddit_post_id("https://www.reddit.com/r/x/comments/abc123/title/") == "abc123"
    assert reddit_post_id("https://redd.it/abc123") == "abc123"
    assert reddit_post_id("https://www.reddit.com/r/x/") is None
    payload = {"post": {"title": "T", "selftext": "Body text", "author": "op", "subreddit": "x"},
               "comments": [{"author": "a", "body": "first", "score": 3,
                             "replies": [{"author": "b", "body": "nested"}]}]}
    runner = FakeRunner({"rdt read": yaml.safe_dump(payload)})
    result = get_source("reddit").read("https://redd.it/abc123", ctx_for(runner, "rdt-cli"))
    assert runner.calls[0] == ["rdt", "read", "--yaml", "--", "abc123"]
    assert "Body text" in result.text and "first" in result.text and "> nested" in result.text
    assert result.metadata["subreddit"] == "r/x"


def test_unsupported_backend_is_refused():
    with pytest.raises(SourceFailure, match="active backend"):
        search("reddit", "PRAW", {})


# ── Twitter ──

TWEETS = [{"id": "1790000000000000001", "text": "Local-first is the future of notes",
           "author": {"screen_name": "devnotes"}, "likes": "1.2k", "created_at": "2026-08-01T00:00:00Z"}]


def test_twitter_cli_needs_explicit_credentials(monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    source = get_source("twitter")
    assert source.blocked_reason("twitter-cli", None)
    with pytest.raises(SourceFailure):
        search("twitter", "twitter-cli", {"twitter": json.dumps(TWEETS)})


def test_twitter_cli_passes_credentials_only_to_the_child(monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)

    class Cfg:
        def get(self, key):
            return {"twitter_auth_token": "tok", "twitter_ct0": "ct0"}.get(key)

    items, runner = search("twitter", "twitter-cli", {"twitter search": json.dumps(TWEETS)},
                           query=HOSTILE, config=Cfg())
    assert runner.calls[0] == ["twitter", "search", "-n", "5", "--yaml", "--", HOSTILE]
    assert runner.envs[0] == {"TWITTER_AUTH_TOKEN": "tok", "TWITTER_CT0": "ct0"}
    assert items[0].url == "https://x.com/devnotes/status/1790000000000000001"
    assert items[0].metrics == {"likes": 1200}
    import os

    assert "TWITTER_AUTH_TOKEN" not in os.environ


def test_twitter_opencli_needs_no_credentials():
    assert get_source("twitter").blocked_reason("OpenCLI", None) is None
    items, runner = search("twitter", "OpenCLI", {"opencli twitter": yaml.safe_dump(TWEETS)})
    assert runner.calls[0][:3] == ["opencli", "twitter", "search"] and runner.envs[0] is None
    assert len(items) == 1


# ── Exa ──

EXA_TEXT = """Title: Local-first software
URL: https://example.org/local-first
Published Date: 2026-02-01
Author: Ink Lab
Text: You own your data,
in spite of the cloud.

Title: Second
URL: https://example.net/2
"""


def test_exa_parses_text_blocks_and_builds_key_value_argv():
    items, runner = search("web", "Exa via mcporter", {"mcporter": EXA_TEXT}, query=HOSTILE, limit=4)
    assert runner.calls[0] == ["mcporter", "call", "exa.web_search_exa", f"query={HOSTILE}", "numResults=4"]
    assert [i.title for i in items] == ["Local-first software", "Second"]
    assert items[0].snippet == "You own your data, in spite of the cloud."
    assert items[0].published == "2026-02-01"


def test_exa_parses_json_and_mcp_envelopes():
    doc = {"results": [{"title": "A", "url": "https://a.example.com", "text": "t"}]}
    assert parse_exa_output(json.dumps(doc))[0]["title"] == "A"
    envelope = {"content": [{"type": "text", "text": json.dumps(doc)}]}
    assert parse_exa_output(json.dumps(envelope))[0]["url"] == "https://a.example.com"
    assert parse_exa_output("garbage with no fields") == []


# ── Bilibili ──

BILI_API = {"code": 0, "data": {"result": [
    {"result_type": "user", "data": [{"uname": "x"}]},
    {"result_type": "video", "data": [{"title": "<em class=\"keyword\">笔记</em>软件", "arcurl": "http://www.bilibili.com/video/BV1xx411c7mD",
                                         "author": "up", "play": 1000, "duration": "12:04", "bvid": "BV1xx411c7mD"}]},
]}}


def test_bilibili_search_api_parse():
    records = parse_search_api(BILI_API)
    assert len(records) == 1
    with pytest.raises(SourceFailure):
        parse_search_api({"code": -412, "message": "blocked"})


def test_bilibili_cli_output_and_api_fallback(monkeypatch):
    from agent_reach.research.sources import bilibili

    items, runner = search("bilibili", "bili-cli",
                           {"bili search": yaml.safe_dump(BILI_API["data"]["result"][1]["data"])},
                           query=HOSTILE)
    assert runner.calls[0] == ["bili", "search", "--type", "video", "-n", "5", "--yaml", "--", HOSTILE]
    assert items[0].title == "笔记软件"
    assert items[0].url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert items[0].metrics["duration"] == 724

    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return json.dumps(BILI_API).encode()

    monkeypatch.setattr(bilibili, "guarded_get", fake_get)
    items, _ = search("bilibili", "bili-cli", {"bili search": SourceFailure("bili exited 2")})
    assert len(items) == 1 and "keyword=local-first%20notes" in calls[-1]


# ── Xiaohongshu, Xueqiu ──

def test_xiaohongshu_opencli_only():
    notes = [{"title": "笔记", "url": "https://www.xiaohongshu.com/explore/0123456789abcdef?xsec_token=t",
              "author": "u", "likes": "3万"}]
    items, runner = search("xiaohongshu", "OpenCLI", {"opencli xiaohongshu": yaml.safe_dump(notes)})
    assert runner.calls[0][-2] == "--"
    assert items[0].metrics == {"likes": 30000}
    with pytest.raises(SourceFailure):
        search("xiaohongshu", "xiaohongshu-mcp", {})


def test_xueqiu_uses_library_code(monkeypatch):
    from agent_reach.channels import xueqiu

    monkeypatch.setattr(xueqiu.XueqiuChannel, "search_stock",
                        lambda self, q, limit=10: [{"symbol": "SH600519", "name": "Moutai", "exchange": "SH"},
                                                   {"symbol": "bad symbol!", "name": "x"}])
    runner = FakeRunner({})
    ctx = ctx_for(runner, "Xueqiu API")
    items = get_source("xueqiu").search("moutai", 5, ctx)
    assert [i.url for i in items] == ["https://xueqiu.com/S/SH600519"]
    assert runner.calls == [] and ctx.commands[0].startswith("XueqiuChannel().search_stock")


# ── query validation ──

@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "tab\there", "x" * 301, "nul\x00", 42, None])
def test_validate_query_rejects(bad):
    from agent_reach.research.models import ResearchError

    with pytest.raises(ResearchError):
        validate_query(bad)


def test_validate_query_keeps_shell_metacharacters_literal():
    assert validate_query(f"  {HOSTILE}  ") == HOSTILE
    assert validate_query("--help") == "--help"
