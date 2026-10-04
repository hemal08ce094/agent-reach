# -*- coding: utf-8 -*-
"""Data types shared by the research workspace.

Everything here is built from untrusted upstream output (or posted back by the
browser), so every constructor clips and validates its fields.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from agent_reach.utils.url import normalize_public_http_url

MAX_QUERY_CHARS = 300
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 600
MAX_AUTHOR_CHARS = 120
MAX_URL_CHARS = 2048
MAX_EXTRA_KEYS = 8
MAX_EXTRA_VALUE_CHARS = 200

#: Metric keys a card may show. Values are numbers (duration in seconds).
METRIC_KEYS = ("score", "comments", "views", "stars", "forks", "likes", "replies", "duration")

_EXTRA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_WS_RE = re.compile(r"\s+")

Metric = Union[int, float]


class ResearchError(Exception):
    """A research request the API should report with an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _is_control(ch: str) -> bool:
    return unicodedata.category(ch) in ("Cc", "Cf") and ch not in ("‍",)


def clean_line(value: Any, limit: int) -> str:
    """One line of display text: no control characters, collapsed whitespace, clipped."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v not in (None, ""))
    text = str(value)
    text = "".join(" " if _is_control(ch) else ch for ch in text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def clean_block(value: Any, limit: int) -> str:
    """Multi-line text: keeps newlines and tabs, drops other control characters."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or not _is_control(ch))
    return text[:limit]


def validate_query(raw: Any) -> str:
    """Return the trimmed query or raise ResearchError(400)."""
    if not isinstance(raw, str):
        raise ResearchError(400, "query must be a string")
    query = raw.strip()
    if not query:
        raise ResearchError(400, "Type a question or keywords first")
    if len(query) > MAX_QUERY_CHARS:
        raise ResearchError(400, f"Keep the query under {MAX_QUERY_CHARS} characters")
    if any(_is_control(ch) or ch in "  " for ch in query):
        raise ResearchError(400, "The query contains control characters or line breaks")
    return query


def public_url(raw: Any) -> Optional[str]:
    """Normalized public http(s) URL, or None when it is not one."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_URL_CHARS:
        return None
    try:
        return normalize_public_http_url(raw.strip())
    except ValueError:
        return None


def to_iso(value: Any) -> Optional[str]:
    """Best-effort ISO 8601 from timestamps, ``YYYYMMDD`` or ISO-ish strings."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:  # milliseconds
                ts /= 1000.0
            if ts <= 0:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = str(value).strip()
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
            return to_iso(float(text))
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return text[:25]
    except (ValueError, OverflowError, OSError):
        return None
    return None


def to_number(value: Any) -> Optional[Metric]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) < 1e15 else None
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmMwW万]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    scale = {"k": 1e3, "m": 1e6, "w": 1e4, "万": 1e4}.get(match.group(2).lower(), 1)
    number *= scale
    return int(number) if number.is_integer() else number


def parse_duration(value: Any) -> Optional[int]:
    """Seconds from a number or an ``h:mm:ss`` / ``mm:ss`` string."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"\d{1,3}(:\d{1,2}){1,2}", value.strip()):
        seconds = 0
        for part in value.strip().split(":"):
            seconds = seconds * 60 + int(part)
        return seconds
    return None


def item_id(source: str, key: str) -> str:
    """Stable id: the same result from the same source always hashes the same."""
    return hashlib.sha256(f"{source}\n{key}".encode("utf-8")).hexdigest()[:16]


@dataclass
class ResearchItem:
    id: str
    source: str
    title: str
    url: str
    snippet: str = ""
    author: str = ""
    published: Optional[str] = None
    metrics: Dict[str, Metric] = field(default_factory=dict)
    extra: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "author": self.author,
            "published": self.published,
            "metrics": dict(self.metrics),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Any, known_sources: Any = None) -> "ResearchItem":
        """Rebuild an item posted by the browser, revalidating every field."""
        if not isinstance(data, dict):
            raise ResearchError(400, "item must be an object")
        source = data.get("source")
        if not isinstance(source, str) or not re.fullmatch(r"[a-z_]{1,32}", source):
            raise ResearchError(400, "item.source is invalid")
        if known_sources is not None and source not in known_sources:
            raise ResearchError(400, "item.source is not a research source")
        url = public_url(data.get("url"))
        if url is None:
            raise ResearchError(400, "item.url must be a public http(s) URL")
        built = make_item(
            source,
            title=data.get("title"),
            url=url,
            snippet=data.get("snippet"),
            author=data.get("author"),
            published=data.get("published"),
            metrics=data.get("metrics"),
            extra=data.get("extra"),
        )
        if built is None:
            raise ResearchError(400, "item is missing a title or URL")
        supplied_id = data.get("id")
        if isinstance(supplied_id, str) and re.fullmatch(r"[0-9a-f]{16}", supplied_id):
            built.id = supplied_id
        return built


def make_item(
    source: str,
    *,
    title: Any,
    url: Any,
    snippet: Any = None,
    author: Any = None,
    published: Any = None,
    metrics: Any = None,
    extra: Any = None,
    native_id: Any = None,
) -> Optional[ResearchItem]:
    """Build a clipped, validated item; None when the URL or title is unusable."""
    clean_url = public_url(url)
    if clean_url is None:
        return None
    clean_title = clean_line(title, MAX_TITLE_CHARS) or clean_line(snippet, 120)
    if not clean_title:
        return None
    clean_metrics: Dict[str, Metric] = {}
    if isinstance(metrics, dict):
        for key in METRIC_KEYS:
            value = (
                parse_duration(metrics.get(key)) if key == "duration" else to_number(metrics.get(key))
            )
            if value is not None:
                clean_metrics[key] = value
    clean_extra: Dict[str, str] = {}
    if isinstance(extra, dict):
        for key, value in list(extra.items())[: MAX_EXTRA_KEYS * 2]:
            if len(clean_extra) >= MAX_EXTRA_KEYS:
                break
            if isinstance(key, str) and _EXTRA_KEY_RE.match(key) and value not in (None, ""):
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    clean_extra[key] = clean_line(value, MAX_EXTRA_VALUE_CHARS)
    key = str(native_id) if native_id not in (None, "") else clean_url
    return ResearchItem(
        id=item_id(source, key),
        source=source,
        title=clean_title,
        url=clean_url,
        snippet=clean_line(snippet, MAX_SNIPPET_CHARS),
        author=clean_line(author, MAX_AUTHOR_CHARS),
        published=to_iso(published),
        metrics=clean_metrics,
        extra=clean_extra,
    )


@dataclass
class ReadResult:
    title: str
    url: str
    source: str
    text: str
    #: "markdown" is rendered with the safe renderer; "text" is shown preformatted.
    format: str = "markdown"
    metadata: Dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    via: str = ""
    command: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "text": self.text,
            "format": self.format,
            "metadata": dict(self.metadata),
            "truncated": self.truncated,
            "via": self.via,
            "command": self.command,
            "note": self.note,
        }


MAX_READ_CHARS = 300_000


def make_read(
    *,
    title: Any,
    url: str,
    source: str,
    text: str,
    fmt: str = "markdown",
    metadata: Optional[Dict[str, Any]] = None,
    via: str = "",
    command: str = "",
    note: str = "",
    truncated: bool = False,
) -> ReadResult:
    body = clean_block(text, MAX_READ_CHARS + 1)
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS]
        truncated = True
    meta = {
        clean_line(k, 40): clean_line(v, 200)
        for k, v in (metadata or {}).items()
        if v not in (None, "")
    }
    return ReadResult(
        title=clean_line(title, MAX_TITLE_CHARS) or url,
        url=url,
        source=source,
        text=body,
        format="text" if fmt == "text" else "markdown",
        metadata=meta,
        truncated=truncated,
        via=via,
        command=command,
        note=clean_line(note, 300),
    )
