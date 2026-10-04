# -*- coding: utf-8 -*-
"""Defensive parsing of upstream JSON/YAML output, plus a guarded HTTP GET."""

from __future__ import annotations

import html
import http.cookiejar
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

import yaml

from agent_reach.research.runner import SourceFailure, SourceTimeout
from agent_reach.utils.url import normalize_public_http_url

_TAG_RE = re.compile(r"<[^>]+>")

#: Keys under which CLIs commonly nest their result list.
_LIST_KEYS = (
    "data", "items", "results", "result", "posts", "entries", "list",
    "tweets", "notes", "videos", "children", "feeds", "records",
)


def load_structured(text: str) -> Any:
    """Parse JSON, falling back to YAML. Raises SourceFailure on garbage."""
    stripped = (text or "").strip()
    if not stripped:
        raise SourceFailure("the command returned no output")
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    # Some tools print a status line before the JSON document.
    for opener in ("[", "{"):
        idx = stripped.find(opener)
        if idx > 0:
            try:
                return json.loads(stripped[idx:])
            except ValueError:
                continue
    try:
        data = yaml.safe_load(stripped)
    except yaml.YAMLError:
        raise SourceFailure("the command output could not be parsed as JSON or YAML") from None
    if isinstance(data, (str, int, float)) or data is None:
        raise SourceFailure("the command output was not structured data")
    return data


def find_records(data: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """The first list of dicts found at the top level or under a common key."""
    if depth > 4:
        return []
    if isinstance(data, list):
        dicts = [d for d in data if isinstance(d, dict)]
        if dicts:
            return dicts
        return []
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if key in data:
                found = find_records(data[key], depth + 1)
                if found:
                    return found
        for value in data.values():
            if isinstance(value, (list, dict)):
                found = find_records(value, depth + 1)
                if found:
                    return found
    return []


def pick(record: Any, *keys: str) -> Any:
    """First non-empty value among dotted ``keys`` (``user.name``)."""
    if not isinstance(record, dict):
        return None
    for key in keys:
        value: Any = record
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                break
        if value not in (None, "", [], {}):
            return value
    return None


def strip_html(text: Any) -> str:
    return html.unescape(_TAG_RE.sub("", str(text or ""))).strip()


def first_dict(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                return entry
    return {}


def records_to_markdown(data: Any, max_comments: int = 80) -> str:
    """Render a post-with-comments payload of unknown shape as readable Markdown."""
    root = first_dict(data)
    post = pick(root, "post", "tweet", "note", "data", "submission") if root else None
    post = post if isinstance(post, dict) else root
    parts: List[str] = []
    body = pick(post, "selftext", "body", "text", "content", "full_text", "desc", "description")
    author = pick(post, "author", "user.screen_name", "user.name", "username", "screen_name")
    if author:
        parts.append(f"*Posted by {strip_html(author)}*")
    if body:
        parts.append(strip_html(body))
    comments = pick(root, "comments", "replies", "comment_list")
    if isinstance(comments, list) and comments:
        parts.append("## Comments")
        count = 0
        for comment in _walk_comments(comments, 0):
            if count >= max_comments:
                parts.append(f"*{max_comments} comments shown.*")
                break
            depth, entry = comment
            who = strip_html(pick(entry, "author", "user.screen_name", "user.name", "username") or "someone")
            text = strip_html(pick(entry, "body", "text", "content", "full_text") or "")
            if not text:
                continue
            score = pick(entry, "score", "ups", "likes", "like_count")
            head = f"**{who}**" + (f" · {score} points" if isinstance(score, (int, float)) else "")
            block = f"{head}\n\n{text}"
            if depth:
                block = "\n".join("> " + line if line else ">" for line in block.splitlines())
            parts.append(block)
            count += 1
    if not parts:
        return "```\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)[:200_000] + "\n```"
    return "\n\n".join(parts)


def _walk_comments(comments: Iterable[Any], depth: int):
    for entry in comments:
        if not isinstance(entry, dict):
            continue
        yield min(depth, 1), entry
        replies = pick(entry, "replies", "children")
        if isinstance(replies, list) and depth < 3:
            yield from _walk_comments(replies, depth + 1)


# ── guarded HTTP ─────────────────────────────────────────────────────────────


def _assert_public_host(url: str) -> None:
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise SourceFailure(f"could not resolve {host}") from None
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not address.is_global:
            raise SourceFailure("refusing to fetch a URL that resolves to a private address")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = normalize_public_http_url(newurl)
        except ValueError:
            raise SourceFailure("the page redirected to a non-public address") from None
        _assert_public_host(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def guarded_get(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 5 * 1024 * 1024,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[http.cookiejar.CookieJar] = None,
) -> bytes:
    """GET a public URL: validated target, public DNS answers, checked redirects, size cap."""
    try:
        target = normalize_public_http_url(url)
    except ValueError:
        raise SourceFailure("only public http(s) URLs can be read") from None
    _assert_public_host(target)
    handlers: List[Any] = [_PublicRedirects()]
    if cookies is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(target, headers=headers or {"User-Agent": "agent-reach/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except SourceFailure:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        raise SourceFailure(f"the server answered HTTP {exc.code}") from None
    except TimeoutError:
        raise SourceTimeout(f"no answer within {timeout:g}s") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise SourceTimeout(f"no answer within {timeout:g}s") from None
        raise SourceFailure(f"network error: {reason}") from None
    if len(body) > max_bytes:
        raise SourceFailure(f"the response is larger than {max_bytes // (1024 * 1024)} MB")
    return body
