# -*- coding: utf-8 -*-
"""Xueqiu (xueqiu.com) — stock quotes, search, trending posts & hot stocks."""

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .base import Channel

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_REFERER = "https://xueqiu.com/"
_TIMEOUT = 10
# The bare homepage now only sets ``acw_tc``; /hq (after redirects) also sets
# the anonymous ``xq_a_token`` group that the public quote APIs need (#664).
_XUEQIU_HOME = "https://xueqiu.com/hq"
# HTTP statuses that mean "this session/cookie was rejected".
_AUTH_REJECTED_STATUSES = (400, 401, 403)

# --------------- cookie-aware HTTP helpers --------------- #

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
)
#: True once the session is prepared for the current cookie state (either the
#: stored cookie was injected or the anonymous warm-up ran).
_cookies_initialized = False
#: The stored cookie string currently injected into the jar (None = anonymous).
_injected_cookie: Optional[str] = None
#: A stored cookie Xueqiu rejected in this process; skipped until it changes.
_failed_cookie: Any = None


def _inject_cookie_string(cookie_str: str) -> None:
    """Parse a 'name=value; name2=value2' string and inject into the cookie jar."""
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name.strip(),
            value=value.strip(),
            port=None,
            port_specified=False,
            domain=".xueqiu.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
        _cookie_jar.set_cookie(cookie)


def _reset_session() -> None:
    """Forget every cookie so the next request starts from a clean state."""
    global _cookies_initialized, _injected_cookie
    _cookie_jar.clear()
    _cookies_initialized = False
    _injected_cookie = None


def _configured_cookie(config=None) -> str:
    """Return the saved ``xueqiu_cookie`` string, or "" when none is set."""
    try:
        from ..config import Config

        cfg = config if config is not None else Config(read_only=True)
        value = cfg.get("xueqiu_cookie")
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _load_cookies_from_config(config=None) -> str:
    """Inject the saved Xueqiu cookie (xueqiu_cookie key) into the jar.

    Re-injects whenever the saved value changes (long-lived processes such as
    the MCP server must notice a newly configured cookie) and drops a
    previously injected cookie when the key is removed. Returns the active
    cookie string, or "" when the session should be anonymous.
    """
    global _injected_cookie
    cookie_str = _configured_cookie(config)
    if not cookie_str or cookie_str == _failed_cookie:
        if _injected_cookie is not None:
            _reset_session()
        return ""
    if cookie_str != _injected_cookie or not any(True for _ in _cookie_jar):
        _reset_session()
        _inject_cookie_string(cookie_str)
        _injected_cookie = cookie_str
    return cookie_str


def _has_anonymous_token() -> bool:
    return any(cookie.name == "xq_a_token" for cookie in _cookie_jar)


def _warm_up_anonymous_session() -> None:
    """Visit /hq (following redirects) to receive the anonymous token group."""
    req = urllib.request.Request(_XUEQIU_HOME, headers={"User-Agent": _UA})
    resp = _opener.open(req, timeout=_TIMEOUT)
    close = getattr(resp, "close", None)
    if close is not None:
        close()


def _ensure_cookies(config=None):
    """Populate session cookies without touching browser credential stores.

    Priority order:
    1. Saved cookie string in ~/.agent-reach/config.yaml  (set by configure --from-browser)
    2. Anonymous warm-up via https://xueqiu.com/hq, which sets ``xq_a_token``
       (enough for the public quote/search/hot endpoints). Skipped when the
       jar already holds a token, so a warm session costs one request.

    Returns the stored cookie in use (truthy) or "" for an anonymous session.
    """
    global _cookies_initialized
    stored = _load_cookies_from_config(config)
    if stored:
        _cookies_initialized = True
        return stored
    if _cookies_initialized or _has_anonymous_token():
        _cookies_initialized = True
        return ""
    _warm_up_anonymous_session()
    _cookies_initialized = True
    return ""


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Referer": _REFERER}
    )
    with _opener.open(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_session_rejection(exc: urllib.error.HTTPError) -> bool:
    """True for 401/403, or a 400 carrying Xueqiu's 400016 session error."""
    if exc.code not in _AUTH_REJECTED_STATUSES:
        return False
    if exc.code != 400:
        return True
    try:
        body = exc.read(4096)
    except Exception:
        return False
    return b"400016" in (body or b"")


def _get_json(url: str, config=None) -> Any:
    """Fetch *url* with Xueqiu session cookies and return parsed JSON.

    A saved cookie that Xueqiu rejects (typically an expired ``xq_a_token``)
    no longer suppresses the anonymous fallback: the request is retried once
    with a fresh anonymous session and the bad cookie is skipped for the rest
    of the process (until the saved value changes).
    """
    global _failed_cookie
    stored = _ensure_cookies(config)
    try:
        return _fetch_json(url)
    except urllib.error.HTTPError as exc:
        try:
            rejected = bool(stored) and _is_session_rejection(exc)
        finally:
            exc.close()
        if not rejected:
            raise
    _failed_cookie = stored
    _reset_session()
    _ensure_cookies(config)
    return _fetch_json(url)


def stored_cookie_rejected(config=None) -> bool:
    """Whether the saved xueqiu_cookie was rejected during this process."""
    cookie_str = _configured_cookie(config)
    return bool(cookie_str) and cookie_str == _failed_cookie


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        text = text.replace(entity, char)
    return text.strip()


class XueqiuChannel(Channel):
    name = "xueqiu"
    description = "Xueqiu stock quotes and community posts"
    backends = ["Xueqiu API"]
    tier = 1

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "xueqiu.com")

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def check(self, config=None):
        self.active_backend = None
        try:
            data = _get_json(
                "https://stock.xueqiu.com/v5/stock/quote.json"
                "?symbol=SH601138&extend=detail",
                config,
            )
            quote = (data.get("data") or {}).get("quote") or {}
            if quote:
                self.active_backend = self.backends[0]
                message = "Public API available (quotes, search, hot posts, hot stocks)"
                if stored_cookie_rejected(config):
                    message += (
                        "; the saved xueqiu_cookie was rejected (probably expired), "
                        "so an anonymous session was used. Remove xueqiu_cookie from "
                        "~/.agent-reach/config.yaml or save a fresh one."
                    )
                return "ok", message
            return "warn", "Unexpected API response (empty data)"
        except Exception as e:
            from agent_reach.utils.text import scrub_url_credentials

            detail = scrub_url_credentials(e).rstrip(": ")
            return "warn", (
                f"Xueqiu API connection failed: {detail}. "
                "The public endpoints work anonymously; if you saved a "
                "xueqiu_cookie it may have expired (remove it from "
                "~/.agent-reach/config.yaml). To save a fresh login cookie run: "
                "agent-reach configure --from-browser chrome --platform xueqiu; "
                "doctor never reads browser cookies automatically."
            )

    # ------------------------------------------------------------------ #
    # Data-fetching methods
    # ------------------------------------------------------------------ #

    def get_stock_quote(self, symbol: str) -> dict:
        """Get a real-time stock quote.

        Args:
            symbol: Stock symbol, e.g. SH600519 (Shanghai), SZ000858 (Shenzhen),
                AAPL (US), 00700 (Hong Kong)

        Returns a dict with keys:
          symbol, name, current, percent, chg, high, low, open, last_close,
          volume, amount, market_capital, turnover_rate, pe_ttm, pe_forecast,
          pb, eps, timestamp
        """
        encoded_symbol = urllib.parse.quote(symbol, safe="")
        data = _get_json(
            "https://stock.xueqiu.com/v5/stock/quote.json"
            f"?symbol={encoded_symbol}&extend=detail"
        )
        q = (data.get("data") or {}).get("quote") or {}
        return {
            "symbol": q.get("symbol", symbol),
            "name": q.get("name", ""),
            "current": q.get("current"),
            "percent": q.get("percent"),
            "chg": q.get("chg"),
            "high": q.get("high"),
            "low": q.get("low"),
            "open": q.get("open"),
            "last_close": q.get("last_close"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            "market_capital": q.get("market_capital"),
            "turnover_rate": q.get("turnover_rate"),
            "pe_ttm": q.get("pe_ttm"),
            "pe_forecast": q.get("pe_forecast"),
            "pb": q.get("pb"),
            "eps": q.get("eps"),
            "timestamp": q.get("timestamp"),
        }

    def search_stock(self, query: str, limit: int = 10) -> list:
        """Search stocks.

        Args:
            query: Stock symbol or Chinese name, e.g. "Moutai" or "茅台", "600519"
            limit: Maximum number of results

        Returns a list of dicts with keys:
          symbol, name, exchange
        """
        data = _get_json(
            f"https://xueqiu.com/stock/search.json"
            f"?code={urllib.parse.quote(query)}&size={limit}"
        )
        stocks = data.get("stocks") or []
        results = []
        for s in stocks[:limit]:
            results.append(
                {
                    "symbol": s.get("code", ""),
                    "name": s.get("name", ""),
                    "exchange": s.get("exchange", ""),
                }
            )
        return results

    def get_hot_posts(self, limit: int = 20) -> list:
        """Get trending Xueqiu posts.

        Uses the v4 public timeline endpoint which returns posts in a `list`
        array.  Each item carries a JSON-encoded `data` field containing the
        actual post payload (title, description, user, like_count, target).

        Args:
            limit: Maximum number of results (capped at 50)

        Returns a list of dicts with keys:
          id, title, text, author, likes, url
        """
        if limit < 0:
            raise ValueError("limit must be non-negative")
        limit = min(limit, 50)
        if limit == 0:
            return []
        data = _get_json(
            "https://xueqiu.com/v4/statuses/public_timeline_by_category.json"
            f"?since_id=-1&max_id=-1&count={limit}&category=-1"
        )
        items = data.get("list") or []
        results = []
        for item in items[:limit]:
            # Each item.data is a JSON string containing the real post payload
            try:
                post = (
                    json.loads(item["data"])
                    if isinstance(item.get("data"), str)
                    else {}
                )
            except (json.JSONDecodeError, KeyError):
                post = {}
            user = post.get("user") or {}
            text = _strip_html(
                post.get("text") or post.get("description") or ""
            )
            target = post.get("target", "")
            results.append(
                {
                    "id": post.get("id", 0),
                    "title": post.get("title") or "",
                    "text": text[:200],
                    "author": user.get("screen_name", ""),
                    "likes": post.get("like_count", 0),
                    "url": f"https://xueqiu.com{target}" if target else "",
                }
            )
        return results

    def get_hot_stocks(self, limit: int = 10, stock_type: int = 10) -> list:
        """Get the hot stock rankings.

        Args:
            limit:      Maximum number of results (capped at 50)
            stock_type: 10 = popularity ranking (default), 12 = most-followed ranking

        Returns a list of dicts with keys:
          symbol, name, current, percent, rank
        """
        data = _get_json(
            f"https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
            f"?size={limit}&type={stock_type}"
        )
        items = (data.get("data") or {}).get("items") or []
        results = []
        for idx, item in enumerate(items[:limit], 1):
            results.append(
                {
                    "symbol": item.get("code") or item.get("symbol", ""),
                    "name": item.get("name", ""),
                    "current": item.get("current"),
                    "percent": item.get("percent"),
                    "rank": idx,
                }
            )
        return results
