# -*- coding: utf-8 -*-
"""Twitter/X through twitter-cli or OpenCLI (skill/references/social.md).

twitter-cli falls back to reading browser cookies when explicit credentials
are missing, which Agent Reach never allows, so this source only runs it when
Cookie-Editor credentials are configured and passes them to that one child.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Any, Dict, List, Optional

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import find_records, load_structured, pick, records_to_markdown
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")


def _credentials(config: Any) -> Dict[str, str]:
    from agent_reach.channels.twitter import twitter_cli_child_env

    env = twitter_cli_child_env(config)
    for name in ("TWITTER_AUTH_TOKEN", "TWITTER_CT0"):
        if os.environ.get(name):
            env.setdefault(name, os.environ[name])
    return env


def _tweet_url(rec: Dict[str, Any]) -> Optional[str]:
    url = pick(rec, "url", "link", "tweet_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    tweet_id = pick(rec, "id", "id_str", "rest_id")
    handle = pick(rec, "author.screen_name", "author.username", "user.screen_name", "screen_name", "username")
    if tweet_id is not None and re.fullmatch(r"\d{5,25}", str(tweet_id)):
        who = str(handle).lstrip("@") if handle and _HANDLE_RE.match(str(handle)) else "i"
        return f"https://x.com/{who}/status/{tweet_id}"
    return None


class TwitterSource(Source):
    name = "twitter"
    label = "Twitter/X"
    channel = "twitter"
    backends = ("twitter-cli", "OpenCLI")
    hint = "Posts via search"

    def blocked_reason(self, backend: str, config: Any) -> Optional[str]:
        if backend == "twitter-cli":
            env = _credentials(config)
            if not (env.get("TWITTER_AUTH_TOKEN") and env.get("TWITTER_CT0")):
                return "twitter-cli has no saved Cookie-Editor credentials. Save them on the Twitter/X channel page."
        return None

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        if backend == "OpenCLI":
            return ["opencli", "twitter", "search", "-f", "yaml", "--", query]
        return ["twitter", "search", "-n", str(limit), "--yaml", "--", query]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        reason = self.blocked_reason(backend, ctx.config)
        if reason:
            raise SourceFailure(reason)
        env = _credentials(ctx.config) if backend == "twitter-cli" else None
        data = load_structured(ctx.run(self.search_argv(query, limit, backend), env=env).stdout)
        items: List[ResearchItem] = []
        for rec in find_records(data):
            if len(items) >= limit:
                break
            text = pick(rec, "text", "full_text", "content", "legacy.full_text")
            handle = pick(rec, "author.screen_name", "author.username", "user.screen_name", "screen_name", "username", "author")
            item = make_item(
                self.name,
                title=text,
                url=_tweet_url(rec),
                snippet=None,
                author=f"@{str(handle).lstrip('@')}" if isinstance(handle, str) else handle,
                published=pick(rec, "created_at", "createdAt", "time", "date"),
                metrics={
                    "likes": pick(rec, "likes", "favorite_count", "like_count", "metrics.likes"),
                    "replies": pick(rec, "replies", "reply_count", "metrics.replies"),
                    "views": pick(rec, "views", "view_count", "metrics.views"),
                },
                native_id=pick(rec, "id", "id_str", "rest_id"),
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "x.com", "twitter.com") and "/status/" in url

    def read(self, url: str, ctx: Context) -> ReadResult:
        backend = require_backend(self, ctx.backend)
        if backend != "twitter-cli":
            raise SourceFailure("Reading single posts needs twitter-cli. Open the post in your browser instead.")
        reason = self.blocked_reason(backend, ctx.config)
        if reason:
            raise SourceFailure(reason)
        data = load_structured(
            ctx.run(["twitter", "tweet", "--yaml", "--", url], env=_credentials(ctx.config)).stdout
        )
        root = data[0] if isinstance(data, list) and data else data
        handle = pick(root, "author.screen_name", "user.screen_name", "screen_name", "author")
        return make_read(
            title=f"Post by @{str(handle).lstrip('@')}" if isinstance(handle, str) else "Post",
            url=url,
            source=self.name,
            text=records_to_markdown(data),
            via=backend,
            command=ctx.commands[-1],
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        if backend == "OpenCLI":
            return []
        return [f"twitter tweet {shlex.quote(url)}"]
