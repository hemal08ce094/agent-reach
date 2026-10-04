# -*- coding: utf-8 -*-
"""V2EX topics through the existing V2EXChannel library code (read only: no search API)."""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlsplit

from agent_reach.research.models import ReadResult, make_read, to_iso
from agent_reach.research.parse import strip_html
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend


def topic_id_from_url(url: str) -> Optional[int]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in ("v2ex.com", "www.v2ex.com"):
        return None
    match = re.fullmatch(r"/t/(\d{1,10})/?", parts.path)
    return int(match.group(1)) if match else None


class V2EXSource(Source):
    name = "v2ex"
    label = "V2EX"
    channel = "v2ex"
    backends = ("V2EX API (public)",)
    searchable = False

    def can_read(self, url: str) -> bool:
        return topic_id_from_url(url) is not None

    def read(self, url: str, ctx: Context) -> ReadResult:
        require_backend(self, ctx.backend)
        from agent_reach.channels.v2ex import V2EXChannel

        topic_id = topic_id_from_url(url)
        if topic_id is None:
            raise SourceFailure("not a V2EX topic link")
        ctx.note(f"V2EXChannel().get_topic({topic_id})")
        try:
            topic = V2EXChannel().get_topic(topic_id)
        except Exception as exc:  # noqa: BLE001
            raise SourceFailure(f"V2EX API failed: {type(exc).__name__}") from None
        parts = [strip_html(topic.get("content")) or "*No body.*"]
        replies = topic.get("replies") or []
        if replies:
            parts.append("## Replies")
            for r in replies[:100]:
                parts.append(f"**{r.get('author') or 'someone'}**\n\n{strip_html(r.get('content'))}")
        return make_read(
            title=topic.get("title") or url,
            url=url,
            source=self.name,
            text="\n\n".join(parts),
            via="V2EX API",
            command=ctx.commands[-1],
            metadata={
                "node": topic.get("node_title"),
                "author": topic.get("author"),
                "created": to_iso(topic.get("created")),
                "replies": str(topic.get("replies_count") or len(replies)),
            },
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        topic_id = topic_id_from_url(url)
        if topic_id is None:
            return []
        ua = '-H "User-Agent: agent-reach/1.0"'
        return [
            f'curl -s "https://www.v2ex.com/api/topics/show.json?id={topic_id}" {ua}',
            f'curl -s "https://www.v2ex.com/api/replies/show.json?topic_id={topic_id}&page=1" {ua}',
        ]
