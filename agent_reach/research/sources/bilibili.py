# -*- coding: utf-8 -*-
"""Bilibili through bili-cli, OpenCLI or the public search API (references/video.md)."""

from __future__ import annotations

import http.cookiejar
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import find_records, guarded_get, load_structured, pick, strip_html
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all/v2?keyword={q}&page=1"
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def bvid_from_url(url: str) -> Optional[str]:
    match = _BV_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _video_url(rec: Dict[str, Any]) -> Optional[str]:
    url = pick(rec, "arcurl", "url", "link")
    if isinstance(url, str):
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith(("http://", "https://")):
            return url.replace("http://", "https://", 1)
    bvid = pick(rec, "bvid", "bv", "id")
    if isinstance(bvid, str) and _BV_RE.fullmatch(bvid):
        return f"https://www.bilibili.com/video/{bvid}"
    return None


def _to_items(records: List[Dict[str, Any]], limit: int) -> List[ResearchItem]:
    items: List[ResearchItem] = []
    for rec in records:
        if len(items) >= limit:
            break
        item = make_item(
            "bilibili",
            title=strip_html(pick(rec, "title", "name")),
            url=_video_url(rec),
            snippet=strip_html(pick(rec, "description", "desc")),
            author=pick(rec, "author", "owner.name", "up", "uploader"),
            published=pick(rec, "pubdate", "created", "pub_date", "date"),
            metrics={
                "views": pick(rec, "play", "views", "view", "stat.view"),
                "comments": pick(rec, "review", "danmaku", "comments"),
                "duration": pick(rec, "duration"),
            },
            native_id=pick(rec, "bvid"),
        )
        if item is not None:
            items.append(item)
    return items


def parse_search_api(payload: Any) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise SourceFailure(f"Bilibili search API error: {message or 'unexpected response'}")
    groups = data.get("result") if isinstance(data, dict) else None
    records: List[Dict[str, Any]] = []
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and group.get("result_type") == "video":
                records.extend(r for r in group.get("data") or [] if isinstance(r, dict))
    return records


class BilibiliSource(Source):
    name = "bilibili"
    label = "Bilibili"
    channel = "bilibili"
    backends = ("bili-cli", "OpenCLI", "Bilibili search API")
    hint = "Chinese-language videos"

    def search_argv(self, query: str, limit: int, backend: str) -> Optional[List[str]]:
        if backend == "bili-cli":
            return ["bili", "search", "--type", "video", "-n", str(limit), "--yaml", "--", query]
        if backend == "OpenCLI":
            return ["opencli", "bilibili", "search", "-f", "yaml", "--", query]
        return None

    def command_preview(self, query: str, limit: int, backend: str) -> str:
        if backend == "Bilibili search API":
            return "curl -s " + _SEARCH_API.format(q=quote(query))
        return super().command_preview(query, limit, backend)

    def _search_api(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        url = _SEARCH_API.format(q=quote(query))
        ctx.note(f"curl -s {url}")
        jar = http.cookiejar.CookieJar()
        headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
        guarded_get("https://www.bilibili.com/", timeout=10, max_bytes=2 * 1024 * 1024, headers=headers, cookies=jar)
        body = guarded_get(url, timeout=min(ctx.timeout, 15.0), headers=headers, cookies=jar)
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise SourceFailure("Bilibili search API returned non-JSON output") from None
        return _to_items(parse_search_api(payload), limit)

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        argv = self.search_argv(query, limit, backend)
        if argv is None:
            return self._search_api(query, limit, ctx)
        try:
            data = load_structured(ctx.run(argv).stdout)
        except SourceFailure:
            if backend != "bili-cli":
                raise
            # The documented zero-config API is bili-cli's fallback in video.md.
            return self._search_api(query, limit, ctx)
        return _to_items(find_records(data), limit)

    def can_read(self, url: str) -> bool:
        return bvid_from_url(url) is not None

    def read(self, url: str, ctx: Context) -> ReadResult:
        backend = require_backend(self, ctx.backend)
        bvid = bvid_from_url(url)
        if bvid is None:
            raise SourceFailure("not a Bilibili video link")
        if backend != "bili-cli":
            raise SourceFailure("Reading video details needs bili-cli")
        out = ctx.run(["bili", "video", bvid]).stdout
        return make_read(
            title=bvid, url=url, source=self.name, text=out, fmt="text",
            via=backend, command=ctx.commands[-1],
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        bvid = bvid_from_url(url)
        if bvid is None:
            return []
        return [f"bili video {bvid}", f"opencli bilibili subtitle {bvid}"]
