# -*- coding: utf-8 -*-
"""V2EX — public API channel for topics, nodes, users, and replies."""

import json
import shutil
import ssl
import subprocess
import urllib.request
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from agent_reach.utils.process import utf8_subprocess_env
from agent_reach.utils.text import scrub_url_credentials

from .base import Channel

_UA = "agent-reach/1.0"
_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 1024 * 1024
_API_BASE = "https://www.v2ex.com"


def _v2ex_url(path: str, **params: Any) -> str:
    """Build a V2EX URL without letting caller values alter its query."""
    return f"{_API_BASE}{path}?{urlencode(params)}"


def _validate_api_url(url: str) -> None:
    """Allow only the public V2EX HTTPS JSON API."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid V2EX API URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() not in {"v2ex.com", "www.v2ex.com"}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/api/")
    ):
        raise ValueError("only the V2EX HTTPS API is allowed")


def _get_json_with_urllib(url: str) -> Any:
    """Fetch JSON with Python's standard HTTP stack."""
    _validate_api_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("V2EX API response exceeds the 1 MiB safety limit")
    return json.loads(raw.decode("utf-8"))


def _is_unexpected_tls_eof(error: BaseException) -> bool:
    """Return whether an exception chain contains the retryable TLS EOF."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLError) and not isinstance(
            current, ssl.SSLCertVerificationError
        ):
            text = str(current).casefold()
            if (
                "unexpected_eof_while_reading" in text
                or "eof occurred in violation of protocol" in text
            ):
                return True
        for nested in (
            getattr(current, "reason", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _get_json_with_curl(url: str) -> Any:
    """Fetch bounded JSON with the OS curl TLS stack."""
    _validate_api_url(url)
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is unavailable for the V2EX TLS fallback")

    command = [
        curl,
        "--fail",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--connect-timeout",
        "5",
        "--max-time",
        str(_TIMEOUT),
        "--max-filesize",
        str(_MAX_RESPONSE_BYTES),
        "--header",
        f"User-Agent: {_UA}",
        "--url",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT + 2,
            env=utf8_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("curl could not complete the V2EX TLS fallback") from exc
    if result.returncode != 0:
        raise RuntimeError("curl could not complete the V2EX TLS fallback")
    if len(result.stdout.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise ValueError("V2EX API response exceeds the 1 MiB safety limit")
    return json.loads(result.stdout)


def _get_json(url: str) -> Any:
    """Fetch JSON, retrying only Python's known TLS EOF via native curl."""
    try:
        return _get_json_with_urllib(url)
    except Exception as exc:
        if isinstance(exc, ssl.SSLCertVerificationError):
            raise
        if not _is_unexpected_tls_eof(exc):
            raise
        return _get_json_with_curl(url)


def _api_error_message(data: Any) -> str:
    """Extract V2EX's ``{"status": "error", "message": ...}`` text, if any."""
    if isinstance(data, dict):
        message = data.get("message") or data.get("error")
        if message:
            return str(message)
        if data.get("status") == "error":
            return "unknown error"
    return ""


def _expect_list(data: Any) -> list:
    """Return the dict items of a list payload, raising on API error objects."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    message = _api_error_message(data)
    if message:
        raise RuntimeError(f"V2EX API error: {message}")
    raise RuntimeError(
        f"Unexpected V2EX API response (expected a list, got {type(data).__name__})"
    )


class V2EXChannel(Channel):
    name = "v2ex"
    description = "V2EX nodes, topics and replies"
    backends = ["V2EX API (public)"]
    tier = 0

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "v2ex.com")

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def check(self, config=None):
        try:
            _expect_list(
                _get_json(
                    "https://www.v2ex.com/api/topics/show.json?node_name=python&page=1"
                )
            )
            self.active_backend = self.backends[0]
            return "ok", "Public API available (hot topics, node browsing, topic details, user info)"
        except Exception as e:
            self.active_backend = None
            return (
                "warn",
                f"V2EX API connection failed (a proxy may be needed): {scrub_url_credentials(e)}",
            )

    # ------------------------------------------------------------------ #
    # Data-fetching methods
    # ------------------------------------------------------------------ #

    def get_hot_topics(self, limit: int = 20) -> list:
        """Get the list of hot topics.

        Returns a list of dicts with keys:
          title, url, replies, node_name, node_title, content
        """
        data = _expect_list(_get_json("https://www.v2ex.com/api/topics/hot.json"))
        results = []
        for item in data[:limit]:
            node = item.get("node") or {}
            content = item.get("content", "") or ""
            results.append(
                {
                    "id": item.get("id", 0),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "replies": item.get("replies", 0),
                    "node_name": node.get("name", ""),
                    "node_title": node.get("title", ""),
                    "content": content[:200],
                    "created": item.get("created", 0),
                }
            )
        return results

    def get_node_topics(self, node_name: str, limit: int = 20) -> list:
        """Get the latest topics in a node.

        Args:
            node_name: Node name, e.g. "python", "tech", "jobs"
            limit:     Maximum number of results

        Returns a list of dicts with keys:
          title, url, replies, node_name, node_title, content
        """
        url = _v2ex_url(
            "/api/topics/show.json",
            node_name=node_name,
            page=1,
        )
        data = _expect_list(_get_json(url))
        results = []
        for item in data[:limit]:
            node = item.get("node") or {}
            content = item.get("content", "") or ""
            results.append(
                {
                    "id": item.get("id", 0),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "replies": item.get("replies", 0),
                    "node_name": node.get("name", node_name),
                    "node_title": node.get("title", ""),
                    "content": content[:200],
                    "created": item.get("created", 0),
                }
            )
        return results

    def get_topic(self, topic_id: int) -> dict:
        """Get a single topic's details and its replies.

        Args:
            topic_id: Topic ID (taken from the URL https://www.v2ex.com/t/<id>)

        Returns a dict with keys:
          id, title, url, content, replies_count, node_name, node_title,
          author, created, replies (list of dicts with: author, content, created)
        """
        topic_data = _get_json(
            _v2ex_url("/api/topics/show.json", id=topic_id)
        )
        # API returns a list even for single-ID queries
        if isinstance(topic_data, list):
            topic = topic_data[0] if topic_data else {}
        else:
            message = _api_error_message(topic_data)
            if message:
                raise RuntimeError(f"V2EX API error: {message}")
            topic = topic_data
        if not isinstance(topic, dict):
            topic = {}

        node = topic.get("node") or {}
        member = topic.get("member") or {}

        # Fetch replies (first page)
        try:
            replies_raw = _get_json(
                _v2ex_url(
                    "/api/replies/show.json",
                    topic_id=topic_id,
                    page=1,
                )
            )
        except Exception:
            replies_raw = []

        replies = [
            {
                "author": (r.get("member") or {}).get("username", ""),
                "content": r.get("content", ""),
                "created": r.get("created", 0),
            }
            for r in (replies_raw if isinstance(replies_raw, list) else [])
            if isinstance(r, dict)
        ]

        return {
            "id": topic.get("id", topic_id),
            "title": topic.get("title", ""),
            "url": topic.get(
                "url",
                f"{_API_BASE}/t/{quote(str(topic_id), safe='')}",
            ),
            "content": topic.get("content", ""),
            "replies_count": topic.get("replies", 0),
            "node_name": node.get("name", ""),
            "node_title": node.get("title", ""),
            "author": member.get("username", ""),
            "created": topic.get("created", 0),
            "replies": replies,
        }

    def get_user(self, username: str) -> dict:
        """Get user info.

        Args:
            username: V2EX username

        Returns a dict with keys:
          id, username, url, website, twitter, psn, github, btc,
          location, bio, avatar, created
        """
        data = _get_json(
            _v2ex_url("/api/members/show.json", username=username)
        )
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"V2EX API error: {_api_error_message(data)}")
        if not isinstance(data, dict):
            raise RuntimeError(
                f"Unexpected V2EX API response (expected an object, got {type(data).__name__})"
            )
        return {
            "id": data.get("id", 0),
            "username": data.get("username", username),
            "url": data.get(
                "url",
                f"{_API_BASE}/member/{quote(str(username), safe='')}",
            ),
            "website": data.get("website", ""),
            "twitter": data.get("twitter", ""),
            "psn": data.get("psn", ""),
            "github": data.get("github", ""),
            "btc": data.get("btc", ""),
            "location": data.get("location", ""),
            "bio": data.get("bio", ""),
            "avatar": data.get("avatar_large", data.get("avatar_normal", "")),
            "created": data.get("created", 0),
        }

    def search(self, query: str, limit: int = 10) -> list:
        """Search topics.

        Note: the public V2EX API has no full-text search endpoint
        (/api/search.json is unavailable), so this method does not fetch results.
        It returns a pointer to the V2EX on-site search page instead.

        For search, visit https://www.v2ex.com/?q=<query> directly or use a
        site:v2ex.com search through the Exa channel.

        Returns:
            list of dicts with keys: title, url, snippet
            When search is unavailable, a list with a single {"error": str} entry.
        """
        search_url = _v2ex_url("/", q=query)
        return [
            {
                "error": (
                    "The public V2EX API has no search endpoint. "
                    f"Use {search_url} instead, "
                    "or search site:v2ex.com through the Exa channel."
                )
            }
        ]
