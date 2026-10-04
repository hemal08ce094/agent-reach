# -*- coding: utf-8 -*-
"""Any web page through Jina Reader, and RSS/Atom feeds through feedparser."""

from __future__ import annotations

import shlex
from typing import List, Optional

from agent_reach.research.models import ReadResult, make_read
from agent_reach.research.parse import guarded_get, strip_html
from agent_reach.research.runner import SourceFailure, SourceTimeout
from agent_reach.research.sources.base import Context, Source

_JINA = "https://r.jina.ai/"


def jina_follow_up(url: str) -> List[str]:
    return [f"curl -s {shlex.quote(_JINA + url)}"]


class WebPageSource(Source):
    """Read-only: the fallback reader for every public URL."""

    name = "webpage"
    label = "Web page"
    channel = "web"
    backends = ("Jina Reader",)
    searchable = False

    def can_read(self, url: str) -> bool:
        return True

    def read(self, url: str, ctx: Context) -> ReadResult:
        from agent_reach.channels.web import WebChannel

        ctx.note(f"curl -s {shlex.quote(_JINA + url)}")
        try:
            text = WebChannel().read(url)
        except ValueError as exc:
            raise SourceFailure(str(exc)) from None
        except RuntimeError as exc:
            raise SourceFailure(str(exc)) from None
        except TimeoutError:
            raise SourceTimeout("Jina Reader did not answer in time") from None
        except OSError as exc:
            reason = getattr(exc, "reason", None) or getattr(exc, "code", None) or exc
            raise SourceFailure(f"Jina Reader could not fetch the page: {reason}") from None
        title = ""
        body_lines = []
        in_header = True
        for line in text.splitlines():
            if in_header and line.startswith("Title:"):
                title = line[len("Title:"):].strip()
                continue
            if in_header and line.startswith(("URL Source:", "Published Time:", "Warning:")):
                continue
            if in_header and line.startswith("Markdown Content:"):
                in_header = False
                continue
            if line.strip():
                in_header = False
            body_lines.append(line)
        return make_read(
            title=title or url,
            url=url,
            source=self.name,
            text="\n".join(body_lines).strip(),
            via="Jina Reader",
            command=ctx.commands[-1] if ctx.commands else "",
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        return jina_follow_up(url)


class RSSSource(Source):
    """Read-only: an RSS/Atom feed URL becomes a list of its latest entries."""

    name = "rss"
    label = "RSS"
    channel = "rss"
    backends = ("feedparser",)
    searchable = False

    def can_read(self, url: str) -> bool:
        from agent_reach.channels.rss import RSSChannel

        return RSSChannel().can_handle(url)

    def read(self, url: str, ctx: Context) -> ReadResult:
        import feedparser

        ctx.note(f"feedparser.parse({url!r})")
        body = guarded_get(url, timeout=min(ctx.timeout, 20.0), max_bytes=5 * 1024 * 1024)
        feed = feedparser.parse(body)
        entries = list(getattr(feed, "entries", []) or [])
        if not entries:
            raise SourceFailure("the feed has no entries, or this URL is not a feed")
        lines: List[str] = []
        for entry in entries[:30]:
            title = strip_html(entry.get("title")) or "(untitled)"
            link = entry.get("link") or ""
            date = entry.get("published") or entry.get("updated") or ""
            head = f"### [{title}]({link})" if link.startswith(("http://", "https://")) else f"### {title}"
            lines.append(head)
            if date:
                lines.append(f"*{strip_html(date)}*")
            summary = strip_html(entry.get("summary"))
            if summary:
                lines.append(summary[:1200])
            lines.append("")
        feed_title = strip_html(getattr(feed, "feed", {}).get("title")) or url
        return make_read(
            title=feed_title,
            url=url,
            source=self.name,
            text="\n".join(lines),
            via="feedparser",
            command=ctx.commands[-1],
            metadata={"entries": str(len(entries))},
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        code = (
            "import feedparser\n"
            f"for e in feedparser.parse({url!r}).entries[:5]:\n"
            "    print(e.title, e.link)"
        )
        return [f"python3 -c {shlex.quote(code)}"]
