# -*- coding: utf-8 -*-
"""Fork regression tests: web.read/check (A14, #685), url helpers (A17),
V2EX payload guards (A15) and RSS routing (A16). All network is mocked."""

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from agent_reach.channels import v2ex as v2ex_mod
from agent_reach.channels import web as web_mod
from agent_reach.channels.rss import RSSChannel
from agent_reach.channels.twitter import TwitterChannel
from agent_reach.channels.v2ex import V2EXChannel
from agent_reach.channels.web import WebChannel, _probe_jina
from agent_reach.utils.url import host_matches, normalize_public_http_url

_REAL_PROBE = _probe_jina


def _resp(body: bytes, charset=None):
    cm = MagicMock()
    inner = cm.__enter__.return_value
    inner.read.return_value = body
    inner.headers.get_content_charset.return_value = charset
    return cm


# --- A14: non-ASCII URLs and non-UTF-8 bodies ------------------------------ #

def test_read_percent_encodes_non_ascii_path():
    with patch("urllib.request.urlopen", return_value=_resp(b"ok")) as mock_open:
        WebChannel().read("https://zh.wikipedia.org/wiki/中文?q=测试")
    url = mock_open.call_args.args[0].full_url
    assert url.isascii()
    assert url == (
        "https://r.jina.ai/https://zh.wikipedia.org/wiki/%E4%B8%AD%E6%96%87"
        "?q=%E6%B5%8B%E8%AF%95"
    )


def test_read_keeps_existing_percent_escapes():
    with patch("urllib.request.urlopen", return_value=_resp(b"ok")) as mock_open:
        WebChannel().read("https://example.com/a%20b?x=1&y=%2F")
    assert mock_open.call_args.args[0].full_url.endswith("https://example.com/a%20b?x=1&y=%2F")


def test_read_idna_encodes_unicode_host():
    with patch("urllib.request.urlopen", return_value=_resp(b"ok")) as mock_open:
        WebChannel().read("https://bücher.example/katalog")
    assert "https://xn--bcher-kva.example/katalog" in mock_open.call_args.args[0].full_url


def test_read_decodes_declared_charset():
    body = "中文内容".encode("gbk")
    with patch("urllib.request.urlopen", return_value=_resp(body, charset="gbk")):
        assert WebChannel().read("https://example.com") == "中文内容"


def test_read_never_raises_on_invalid_utf8():
    with patch("urllib.request.urlopen", return_value=_resp(b"ok \xff\xfe end")):
        out = WebChannel().read("https://example.com")
    assert out.startswith("ok ") and out.endswith(" end")


def test_read_tolerates_unknown_charset_label():
    with patch("urllib.request.urlopen", return_value=_resp(b"plain", charset="x-bogus")):
        assert WebChannel().read("https://example.com") == "plain"


# --- #685: cheap HEAD probe of Jina Reader --------------------------------- #

def test_check_warns_when_jina_is_unreachable(monkeypatch):
    monkeypatch.setattr(web_mod, "_probe_jina", _REAL_PROBE)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        channel = WebChannel()
        status, message = channel.check()
    assert status == "warn"
    assert "unreachable" in message
    assert channel.active_backend is None


def test_check_counts_any_http_status_as_reachable(monkeypatch):
    monkeypatch.setattr(web_mod, "_probe_jina", _REAL_PROBE)
    error = urllib.error.HTTPError("https://r.jina.ai/", 405, "Method Not Allowed", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        status, _ = WebChannel().check()
    assert status == "ok"


# --- A17: host_matches hardening ------------------------------------------- #

@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example\\.x.com/elonmusk",
        "https://evil.example\\@x.com/",
        "https://x.com\t.evil.test/",
        "https://x .com/",
        "https://x.com\x00/",
    ],
)
def test_host_matches_rejects_backslash_and_whitespace(url):
    assert host_matches(url, "x.com") is False
    assert TwitterChannel().can_handle(url) is False


@pytest.mark.parametrize("url", ["x.com/elonmusk", "www.youtube.com/watch?v=1", " x.com "])
def test_host_matches_accepts_scheme_less_urls(url):
    assert host_matches(url, "x.com", "youtube.com") is True


@pytest.mark.parametrize("url", ["ftp://x.com/a", "javascript:alert(1)", "mailto:a@x.com"])
def test_host_matches_still_rejects_other_schemes(url):
    assert host_matches(url, "x.com") is False


@pytest.mark.parametrize(
    "url",
    ["http://１２７.０.０.１/", "http://ⓛⓞⓒⓐⓛⓗⓞⓢⓣ.ｌｏｃａｌ/", "http://ｍｅｔａｄａｔａ.internal/"],
)
def test_normalize_validates_the_idna_host(url):
    with pytest.raises(ValueError):
        normalize_public_http_url(url)


def test_normalize_returns_ascii_host_for_unicode_domains():
    assert normalize_public_http_url("https://bücher.example/x") == "https://xn--bcher-kva.example/x"


# --- A15: V2EX payload guards ----------------------------------------------- #

_ERROR = {"status": "error", "message": "Object Not Found"}


@pytest.mark.parametrize("method,args", [("get_hot_topics", ()), ("get_node_topics", ("python",))])
def test_v2ex_list_helpers_surface_api_errors(monkeypatch, method, args):
    monkeypatch.setattr(v2ex_mod, "_get_json", lambda _url: _ERROR)
    with pytest.raises(RuntimeError, match="Object Not Found"):
        getattr(V2EXChannel(), method)(*args)


def test_v2ex_list_helpers_skip_non_dict_items(monkeypatch):
    monkeypatch.setattr(v2ex_mod, "_get_json", lambda _url: [None, "x", {"id": 7, "title": "t"}])
    topics = V2EXChannel().get_hot_topics()
    assert [t["id"] for t in topics] == [7]


def test_v2ex_get_topic_tolerates_bad_replies(monkeypatch):
    def fake(url):
        if "replies" in url:
            return {"status": "error", "message": "rate limited"}
        return [{"id": 1, "title": "t", "node": None, "member": None}]

    monkeypatch.setattr(v2ex_mod, "_get_json", fake)
    topic = V2EXChannel().get_topic(1)
    assert topic["title"] == "t"
    assert topic["replies"] == []


def test_v2ex_get_topic_skips_null_replies(monkeypatch):
    def fake(url):
        if "replies" in url:
            return [None, {"content": "hi", "member": {"username": "u"}}]
        return [{"id": 1}]

    monkeypatch.setattr(v2ex_mod, "_get_json", fake)
    assert V2EXChannel().get_topic(1)["replies"] == [
        {"author": "u", "content": "hi", "created": 0}
    ]


def test_v2ex_get_topic_and_user_raise_on_error_object(monkeypatch):
    monkeypatch.setattr(v2ex_mod, "_get_json", lambda _url: _ERROR)
    with pytest.raises(RuntimeError, match="Object Not Found"):
        V2EXChannel().get_topic(1)
    with pytest.raises(RuntimeError, match="Object Not Found"):
        V2EXChannel().get_user("nobody")


def test_v2ex_check_warns_on_error_object(monkeypatch):
    monkeypatch.setattr(v2ex_mod, "_get_json", lambda _url: _ERROR)
    channel = V2EXChannel()
    status, message = channel.check()
    assert status == "warn"
    assert "Object Not Found" in message
    assert channel.active_backend is None


# --- A16: RSS routing ------------------------------------------------------- #

@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/feed",
        "https://example.com/blog/rss/",
        "https://example.com/atom.xml",
        "https://example.com/news.rss",
        "https://example.com/index.xml",
        "https://example.com/feeds/posts/default?alt=rss",
        "http://example.com/podcast-feed.xml",
    ],
)
def test_rss_accepts_feed_urls(url):
    assert RSSChannel().can_handle(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://en.wikipedia.org/wiki/Anatomy",
        "https://github.com/o/r/issues/feedback",
        "https://example.com/sitemap.xml",
        "https://example.com/sitemap-index.xml",
        "file:///tmp/feed.xml",
        "https://example.com/data.xml",
        "https://example.com/",
        "",
    ],
)
def test_rss_rejects_non_feed_urls(url):
    assert RSSChannel().can_handle(url) is False
