# -*- coding: utf-8 -*-
"""Xiaohongshu through OpenCLI only (skill/references/social.md, backend A).

The MCP and legacy backends need an explicit Cookie-Editor import first; the
workspace leaves those to the agent and reports the source as not supported.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import find_records, load_structured, pick, records_to_markdown
from agent_reach.research.sources.base import Context, Source, require_backend


def _note_url(rec: Dict[str, Any]) -> Optional[str]:
    url = pick(rec, "url", "link", "note_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    note_id = pick(rec, "id", "note_id")
    token = pick(rec, "xsec_token")
    if isinstance(note_id, str) and re.fullmatch(r"[0-9a-f]{16,32}", note_id):
        suffix = f"?xsec_token={token}" if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_=+/-]{1,200}", token) else ""
        return f"https://www.xiaohongshu.com/explore/{note_id}{suffix}"
    return None


class XiaohongshuSource(Source):
    name = "xiaohongshu"
    label = "Xiaohongshu"
    channel = "xiaohongshu"
    backends = ("OpenCLI",)
    hint = "Notes via OpenCLI"

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        return ["opencli", "xiaohongshu", "search", "-f", "yaml", "--", query]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        records = find_records(load_structured(ctx.run(self.search_argv(query, limit, backend)).stdout))
        items: List[ResearchItem] = []
        for rec in records:
            if len(items) >= limit:
                break
            item = make_item(
                self.name,
                title=pick(rec, "title", "display_title", "desc"),
                url=_note_url(rec),
                snippet=pick(rec, "desc", "content"),
                author=pick(rec, "author", "user.nickname", "nickname"),
                published=pick(rec, "time", "date", "published"),
                metrics={"likes": pick(rec, "likes", "liked_count", "interact_info.liked_count")},
                native_id=pick(rec, "id", "note_id"),
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "xiaohongshu.com", "xhslink.com")

    def read(self, url: str, ctx: Context) -> ReadResult:
        backend = require_backend(self, ctx.backend)
        data = load_structured(ctx.run(["opencli", "xiaohongshu", "note", "-f", "yaml", "--", url]).stdout)
        root = data[0] if isinstance(data, list) and data else data
        return make_read(
            title=pick(root, "title") or url,
            url=url,
            source=self.name,
            text=records_to_markdown(data),
            via=backend,
            command=ctx.commands[-1],
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        return [f"opencli xiaohongshu note {shlex.quote(url)} -f yaml"]
