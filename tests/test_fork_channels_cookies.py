# -*- coding: utf-8 -*-
"""Fork regression tests for cookie-backed channels.

A6 (#748) undecodable cookie files, A7 saved_at<=0, A20 xhs response leak,
#744 format_xhs_result envelopes, A12/#664 Xueqiu cookie latch + fallback,
A13 empty Twitter env vars. All offline.
"""

import io
import json
import subprocess
import time
import urllib.error

import pytest

from agent_reach.channels import xiaohongshu as xhs
from agent_reach.channels import xueqiu as xq
from agent_reach.channels.reddit import RedditChannel
from agent_reach.channels.twitter import twitter_cli_child_env
from agent_reach.channels.xiaohongshu import XiaoHongShuChannel, format_xhs_result

_COOKIE_CASES = [
    (RedditChannel, "_check_rdt", ".config/rdt-cli/credential.json",
     {"cookies": {"reddit_session": "explicit-test-value"}}),
    (XiaoHongShuChannel, "_check_xhs_cli", ".xiaohongshu-cli/cookies.json",
     {"a1": "explicit-test-value"}),
]


def _run_cookie_check(monkeypatch, isolated_home, channel, method, relative_path, content):
    cookie_file = isolated_home / relative_path
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_bytes(content)
    monkeypatch.setattr("shutil.which", lambda _: "/test/explicitly-installed-tool")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("must not run browser cookie extraction")
    )
    instance = channel()
    return getattr(instance, method)()


# --- A6: undecodable cookie files ----------------------------------------- #

@pytest.mark.parametrize("channel,method,relative_path,_payload", _COOKIE_CASES)
def test_undecodable_cookie_file_is_a_warning_not_a_crash(
    monkeypatch, isolated_home, channel, method, relative_path, _payload
):
    status, message = _run_cookie_check(
        monkeypatch, isolated_home, channel, method, relative_path, b"\xff\xfe\x00"
    )
    assert status == "warn"
    assert "cannot be read safely" in message


# --- A7: saved_at <= 0 means unknown, not stale ------------------------------ #

@pytest.mark.parametrize("saved_at", [0, -1, 0.0])
@pytest.mark.parametrize("channel,method,relative_path,payload", _COOKIE_CASES)
def test_unknown_saved_at_is_not_reported_stale(
    monkeypatch, isolated_home, channel, method, relative_path, payload, saved_at
):
    content = json.dumps({**payload, "saved_at": saved_at}).encode("utf-8")
    status, message = _run_cookie_check(
        monkeypatch, isolated_home, channel, method, relative_path, content
    )
    assert status == "warn"
    assert "over 7 days old" not in message
    assert "not verified live" in message
    assert "explicit-test-value" not in message


@pytest.mark.parametrize("channel,method,relative_path,payload", _COOKIE_CASES)
def test_really_old_saved_at_is_still_stale(
    monkeypatch, isolated_home, channel, method, relative_path, payload
):
    content = json.dumps({**payload, "saved_at": time.time() - 30 * 86400}).encode("utf-8")
    _, message = _run_cookie_check(
        monkeypatch, isolated_home, channel, method, relative_path, content
    )
    assert "over 7 days old" in message


def test_reddit_login_template_is_not_immediately_expired(monkeypatch, isolated_home):
    """The hint's own template (saved_at: 0) must be accepted as written."""
    hint = RedditChannel._rdt_login_hint()
    template = hint[hint.index('{"cookies"'):hint.index("}\n\n") + 1]
    data = json.loads(template.replace("<paste Value>", "explicit-test-value"))
    status, message = _run_cookie_check(
        monkeypatch, isolated_home, RedditChannel, "_check_rdt",
        ".config/rdt-cli/credential.json", json.dumps(data).encode("utf-8"),
    )
    assert status == "warn"
    assert "over 7 days old" not in message


# --- A20: xiaohongshu-mcp probe closes its responses ------------------------ #

class _ClosingResponse:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def close(self):
        self.closed = True


def test_mcp_probe_closes_success_response(monkeypatch):
    response = _ClosingResponse()

    class Opener:
        def open(self, *_a, **_k):
            return response

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_a: Opener())
    assert xhs._mcp_service_reachable() is True
    assert response.closed is True


def test_mcp_probe_closes_http_error_response(monkeypatch):
    error = urllib.error.HTTPError(xhs._MCP_ENDPOINT, 405, "Method Not Allowed", {}, io.BytesIO(b""))
    closed = []
    original_close = error.close
    error.close = lambda: (closed.append(True), original_close())

    class Opener:
        def open(self, *_a, **_k):
            raise error

    monkeypatch.setattr(xhs.urllib.request, "build_opener", lambda *_a: Opener())
    assert xhs._mcp_service_reachable() is True
    assert closed == [True]


# --- #744: format_xhs_result envelopes ------------------------------------- #

_MCP_FEED = {
    "id": "note-123",
    "xsecToken": "explicit-test-token",
    "noteCard": {
        "type": "normal",
        "displayTitle": "Search result",
        "user": {"userId": "user-1", "nickname": "Author"},
        "interactInfo": {"likedCount": "12", "commentCount": "3", "sharedCount": "2"},
        "cover": {"urlDefault": "https://example.com/cover.jpg"},
    },
}
_EXPECTED_FEED = {
    "id": "note-123",
    "xsec_token": "explicit-test-token",
    "title": "Search result",
    "type": "normal",
    "user": {"nickname": "Author", "user_id": "user-1"},
    "liked_count": "12",
    "comment_count": "3",
    "share_count": "2",
    "images": ["https://example.com/cover.jpg"],
}


@pytest.mark.parametrize(
    "payload",
    [
        {"feeds": [_MCP_FEED], "count": 1},
        {"success": True, "data": {"feeds": [_MCP_FEED], "count": 1}},
        {"data": {"items": [_MCP_FEED]}},
        {"data": {"notes": [_MCP_FEED]}},
    ],
)
def test_format_xhs_result_unwraps_mcp_envelopes(payload):
    assert format_xhs_result(payload) == [_EXPECTED_FEED]


@pytest.mark.parametrize(
    "payload", [{"feeds": [], "count": 0}, {"items": []}, {"data": {"items": []}}]
)
def test_empty_search_stays_a_list(payload):
    assert format_xhs_result(payload) == []


def test_detail_envelope_keeps_note_and_sibling_comments():
    payload = {
        "data": {
            "note": {
                "noteId": "note-123",
                "title": "Detail",
                "desc": "Body",
                "imageList": [{"urlDefault": "https://example.com/d.jpg"}],
            },
            "comments": {
                "list": [{"content": "Nice", "userInfo": {"nickname": "Reader"}, "likeCount": "2"}],
                "hasMore": False,
            },
        }
    }
    result = format_xhs_result(payload)
    assert result["note_id"] == "note-123"
    assert result["images"] == ["https://example.com/d.jpg"]
    assert result["comments"] == [{"content": "Nice", "user": "Reader", "like_count": "2"}]


# --- A12 / #664: Xueqiu cookie latch and anonymous fallback ---------------- #

class _Cfg:
    def __init__(self, cookie=None):
        self.cookie = cookie

    def get(self, key, default=None):
        return self.cookie if key == "xueqiu_cookie" else default


class _Resp:
    def __init__(self, body=b"{}"):
        self.body = body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, *_):
        return self.body

    def close(self):
        self.closed = True


def _quote_body():
    return json.dumps({"data": {"quote": {"symbol": "SH601138"}}}).encode()


def _jar_names():
    return {c.name: c.value for c in xq._cookie_jar}


def test_configured_cookie_is_injected_after_anonymous_start(monkeypatch):
    requested = []
    monkeypatch.setattr(
        xq._opener, "open", lambda req, timeout=None: requested.append(req.full_url) or _Resp()
    )

    xq._ensure_cookies(_Cfg(None))  # anonymous warm-up first
    assert requested == ["https://xueqiu.com/hq"]

    xq._ensure_cookies(_Cfg("xq_a_token=SAVED; u=1"))  # later: user configures a cookie
    assert _jar_names().get("xq_a_token") == "SAVED"

    xq._ensure_cookies(_Cfg("xq_a_token=NEWER"))  # and changes it again
    assert _jar_names() == {"xq_a_token": "NEWER"}
    assert requested == ["https://xueqiu.com/hq"]  # no extra warm-ups


def test_removing_the_cookie_drops_it_and_warms_up_anonymously(monkeypatch):
    requested = []
    monkeypatch.setattr(
        xq._opener, "open", lambda req, timeout=None: requested.append(req.full_url) or _Resp()
    )
    xq._ensure_cookies(_Cfg("xq_a_token=SAVED"))
    xq._ensure_cookies(_Cfg(None))

    assert "SAVED" not in _jar_names().values()
    assert requested == ["https://xueqiu.com/hq"]


def test_warm_up_response_is_closed(monkeypatch):
    responses = []
    monkeypatch.setattr(
        xq._opener, "open", lambda req, timeout=None: responses.append(_Resp()) or responses[-1]
    )
    xq._ensure_cookies(_Cfg(None))
    assert responses and all(r.closed for r in responses)


def test_warm_up_skipped_when_jar_already_has_token(monkeypatch):
    xq._inject_cookie_string("xq_a_token=ANON")
    requested = []
    monkeypatch.setattr(
        xq._opener,
        "open",
        lambda req, timeout=None: requested.append(req.full_url) or _Resp(_quote_body()),
    )
    xq._get_json("https://stock.xueqiu.com/v5/stock/quote.json?symbol=SH601138", _Cfg(None))
    assert requested == ["https://stock.xueqiu.com/v5/stock/quote.json?symbol=SH601138"]


def _session_error(url, code=400, body=b'{"error_code":"400016"}'):
    return urllib.error.HTTPError(url, code, "Bad Request", {}, io.BytesIO(body))


def test_rejected_stored_cookie_falls_back_to_anonymous_session(monkeypatch):
    calls = []

    def fake_open(req, timeout=None):
        url = req.full_url
        calls.append((url, dict(_jar_names())))
        if url.endswith("/hq"):
            xq._inject_cookie_string("xq_a_token=ANON")
            return _Resp()
        if _jar_names().get("xq_a_token") == "EXPIRED":
            raise _session_error(url)
        return _Resp(_quote_body())

    monkeypatch.setattr(xq._opener, "open", fake_open)
    cfg = _Cfg("xq_a_token=EXPIRED")

    status, message = xq.XueqiuChannel().check(cfg)

    assert status == "ok"
    assert "xueqiu_cookie was rejected" in message
    assert [url for url, _ in calls][-2:] == [
        "https://xueqiu.com/hq",
        "https://stock.xueqiu.com/v5/stock/quote.json?symbol=SH601138&extend=detail",
    ]
    # The bad cookie stays skipped for the rest of the process.
    calls.clear()
    xq._get_json("https://stock.xueqiu.com/v5/stock/quote.json?symbol=X", cfg)
    assert [url for url, _ in calls] == ["https://stock.xueqiu.com/v5/stock/quote.json?symbol=X"]
    assert _jar_names().get("xq_a_token") == "ANON"


def test_unrelated_400_does_not_discard_the_stored_cookie(monkeypatch):
    def fake_open(req, timeout=None):
        raise _session_error(req.full_url, body=b'{"error_code":"10001"}')

    monkeypatch.setattr(xq._opener, "open", fake_open)
    with pytest.raises(urllib.error.HTTPError):
        xq._get_json("https://stock.xueqiu.com/x.json", _Cfg("xq_a_token=GOOD"))
    assert xq._failed_cookie is None or xq._failed_cookie != "xq_a_token=GOOD"


def test_failure_message_mentions_expired_saved_cookie(monkeypatch):
    def fake_open(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(xq._opener, "open", fake_open)
    status, message = xq.XueqiuChannel().check(_Cfg(None))
    assert status == "warn"
    assert "anonymously" in message
    assert "xueqiu_cookie" in message


@pytest.fixture(autouse=True)
def _reset_xueqiu_state(monkeypatch):
    monkeypatch.setattr(xq, "_injected_cookie", None)
    monkeypatch.setattr(xq, "_failed_cookie", None)


# --- A13: empty Twitter env vars do not hide saved credentials ------------- #

class _TwitterCfg:
    def get(self, key, default=None):
        return {"twitter_auth_token": "saved-token", "twitter_ct0": "saved-ct0"}.get(key, default)


def test_empty_env_var_does_not_hide_saved_credentials(monkeypatch):
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "")
    monkeypatch.setenv("TWITTER_CT0", "shell-ct0")

    env = twitter_cli_child_env(_TwitterCfg())

    assert env == {"TWITTER_AUTH_TOKEN": "saved-token"}
