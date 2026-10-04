# -*- coding: utf-8 -*-
"""Xueqiu stock search and quotes through the existing XueqiuChannel library code."""

from __future__ import annotations

import re
import shlex
from typing import List, Optional
from urllib.parse import quote, urlsplit

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.]{1,16}$")


def symbol_from_url(url: str) -> Optional[str]:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("xueqiu.com", "www.xueqiu.com"):
        return None
    match = re.fullmatch(r"/S/([A-Za-z0-9.]{1,16})/?", parts.path)
    return match.group(1) if match else None


class XueqiuSource(Source):
    name = "xueqiu"
    label = "Xueqiu"
    channel = "xueqiu"
    backends = ("Xueqiu API",)
    #: Stock search only matches names and symbols, so it starts unselected.
    default_selected = False
    hint = "Stock names and symbols"

    def command_preview(self, query: str, limit: int, backend: str) -> str:
        return f"XueqiuChannel().search_stock({query!r}, limit={limit})"

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        require_backend(self, ctx.backend)
        from agent_reach.channels.xueqiu import XueqiuChannel

        ctx.note(self.command_preview(query, limit, str(ctx.backend)))
        try:
            stocks = XueqiuChannel().search_stock(query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — library errors become a source error
            raise SourceFailure(f"Xueqiu search failed: {type(exc).__name__}") from None
        items = []
        for s in stocks:
            symbol = str(s.get("symbol") or "")
            if not _SYMBOL_RE.match(symbol):
                continue
            item = make_item(
                self.name,
                title=f"{s.get('name') or symbol} ({symbol})",
                url=f"https://xueqiu.com/S/{quote(symbol)}",
                snippet=s.get("exchange"),
                native_id=symbol,
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        return symbol_from_url(url) is not None

    def read(self, url: str, ctx: Context) -> ReadResult:
        require_backend(self, ctx.backend)
        from agent_reach.channels.xueqiu import XueqiuChannel

        symbol = symbol_from_url(url)
        if symbol is None:
            raise SourceFailure("not a Xueqiu stock link")
        ctx.note(f"XueqiuChannel().get_stock_quote({symbol!r})")
        try:
            quote_data = XueqiuChannel().get_stock_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            raise SourceFailure(f"Xueqiu quote failed: {type(exc).__name__}") from None
        rows = ["| Field | Value |", "| --- | --- |"]
        for key, value in quote_data.items():
            if value not in (None, ""):
                rows.append(f"| {key} | {value} |")
        text = "Quotes may be delayed and are not investment advice.\n\n" + "\n".join(rows)
        return make_read(
            title=f"{quote_data.get('name') or symbol} ({symbol})",
            url=url, source=self.name, text=text, via="Xueqiu API", command=ctx.commands[-1],
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        symbol = symbol_from_url(url)
        if symbol is None:
            return []
        code = (
            "from agent_reach.channels.xueqiu import XueqiuChannel\n"
            f"print(XueqiuChannel().get_stock_quote({symbol!r}))"
        )
        return [f"python3 -c {shlex.quote(code)}"]
