# -*- coding: utf-8 -*-
"""Web search through Exa (mcporter), as documented in skill/references/search.md."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from agent_reach.research.models import ResearchItem, make_item
from agent_reach.research.parse import find_records, pick
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_FIELD_RE = re.compile(r"^(Title|URL|Url|Published Date|Published|Author|Text|Highlights|Summary):\s*(.*)$")


def parse_exa_output(text: str) -> List[Dict[str, Any]]:
    """Exa results come back as a JSON document or as ``Title:/URL:`` text blocks."""
    stripped = (text or "").strip()
    for candidate in (stripped, stripped[stripped.find("{"):] if "{" in stripped else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("content"), list):
            # A raw MCP envelope: the payload is inside text content parts.
            inner = "\n".join(
                str(part.get("text", "")) for part in data["content"] if isinstance(part, dict)
            )
            return parse_exa_output(inner)
        records = find_records(data)
        if records:
            return records
    blocks: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    last_key = ""
    for line in stripped.splitlines():
        match = _FIELD_RE.match(line.strip())
        if match:
            key = match.group(1).lower().split()[0]
            if key == "title" and current.get("title"):
                blocks.append(current)
                current = {}
            current[key] = match.group(2).strip()
            last_key = key
        elif last_key in ("text", "highlights", "summary") and line.strip():
            current[last_key] = (current.get(last_key, "") + " " + line.strip()).strip()
    if current.get("title") or current.get("url"):
        blocks.append(current)
    return blocks


class ExaSource(Source):
    name = "web"
    label = "Web"
    channel = "exa_search"
    backends = ("Exa via mcporter",)
    hint = "Exa web search"

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        # mcporter reads key=value tokens; one argv element per pair, no shell quoting.
        return ["mcporter", "call", "exa.web_search_exa", f"query={query}", f"numResults={limit}"]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        result = ctx.run(self.search_argv(query, limit, backend))
        records = parse_exa_output(result.stdout)
        if not records and result.stdout.strip() and "error" in result.stdout.lower()[:200]:
            raise SourceFailure(result.stdout.strip().splitlines()[0][:300])
        items = []
        for rec in records[:limit]:
            item = make_item(
                self.name,
                title=pick(rec, "title"),
                url=pick(rec, "url", "link", "id"),
                snippet=pick(rec, "text", "highlights", "summary", "snippet"),
                author=pick(rec, "author"),
                published=pick(rec, "publishedDate", "published", "published_date"),
            )
            if item is not None:
                items.append(item)
        return items
