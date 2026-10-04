# -*- coding: utf-8 -*-
"""Reddit through OpenCLI or rdt-cli (skill/references/social.md)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import find_records, load_structured, pick, records_to_markdown
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_POST_ID_RE = re.compile(r"/comments/([a-z0-9]{3,12})(?:/|$)", re.IGNORECASE)


def reddit_post_id(url: str) -> Optional[str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "redd.it":
        candidate = parts.path.strip("/")
        return candidate if re.fullmatch(r"[a-z0-9]{3,12}", candidate, re.IGNORECASE) else None
    match = _POST_ID_RE.search(parts.path)
    return match.group(1) if match else None


def _permalink(rec: Dict[str, Any]) -> Optional[str]:
    link = pick(rec, "permalink", "url", "link")
    if isinstance(link, str):
        if link.startswith("/r/"):
            return "https://www.reddit.com" + link
        if link.startswith(("http://", "https://")):
            return link
    post_id = pick(rec, "id")
    if isinstance(post_id, str) and re.fullmatch(r"(t3_)?[a-z0-9]{3,12}", post_id, re.IGNORECASE):
        return f"https://www.reddit.com/comments/{post_id.removeprefix('t3_')}/"
    return None


class RedditSource(Source):
    name = "reddit"
    label = "Reddit"
    channel = "reddit"
    backends = ("OpenCLI", "rdt-cli")
    hint = "Posts and threads"

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        if backend == "OpenCLI":
            return ["opencli", "reddit", "search", "-f", "yaml", "--", query]
        return ["rdt", "search", "--limit", str(limit), "--yaml", "--", query]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        records = find_records(load_structured(ctx.run(self.search_argv(query, limit, backend)).stdout))
        items: List[ResearchItem] = []
        for rec in records:
            if len(items) >= limit:
                break
            inner = rec.get("data")
            if isinstance(inner, dict):
                rec = inner
            subreddit = pick(rec, "subreddit", "subreddit_name_prefixed", "community")
            item = make_item(
                self.name,
                title=pick(rec, "title"),
                url=_permalink(rec),
                snippet=pick(rec, "selftext", "body", "text", "snippet"),
                author=pick(rec, "author", "user"),
                published=pick(rec, "created_utc", "created", "created_at", "time", "date"),
                metrics={
                    "score": pick(rec, "score", "ups", "upvotes"),
                    "comments": pick(rec, "num_comments", "comments", "comment_count"),
                },
                extra={"subreddit": str(subreddit).removeprefix("r/") if subreddit else None},
                native_id=pick(rec, "id", "name"),
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        return reddit_post_id(url) is not None

    def read_argv(self, post_id: str, backend: str) -> List[str]:
        if backend == "OpenCLI":
            return ["opencli", "reddit", "read", "-f", "yaml", "--", post_id]
        return ["rdt", "read", "--yaml", "--", post_id]

    def read(self, url: str, ctx: Context) -> ReadResult:
        backend = require_backend(self, ctx.backend)
        post_id = reddit_post_id(url)
        if post_id is None:
            raise SourceFailure("not a Reddit post link")
        data = load_structured(ctx.run(self.read_argv(post_id, backend)).stdout)
        root = data[0] if isinstance(data, list) and data else data
        post = pick(root, "post") if isinstance(root, dict) else None
        post = post if isinstance(post, dict) else (root if isinstance(root, dict) else {})
        subreddit = pick(post, "subreddit")
        return make_read(
            title=pick(post, "title") or url,
            url=url,
            source=self.name,
            text=records_to_markdown(data),
            via=backend,
            command=ctx.commands[-1],
            metadata={
                "subreddit": f"r/{str(subreddit).removeprefix('r/')}" if subreddit else None,
                "author": pick(post, "author"),
                "score": pick(post, "score"),
            },
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        post_id = reddit_post_id(url)
        if post_id is None:
            return []
        if backend == "OpenCLI":
            return [f"opencli reddit read {post_id} -f yaml"]
        return [f"rdt read {post_id}"]
