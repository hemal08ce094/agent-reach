# -*- coding: utf-8 -*-
"""Research source registry, in display order."""

from __future__ import annotations

from typing import Dict, List, Optional

from agent_reach.research.sources.base import Context, Source
from agent_reach.research.sources.bilibili import BilibiliSource
from agent_reach.research.sources.exa import ExaSource
from agent_reach.research.sources.github import GitHubSource
from agent_reach.research.sources.reddit import RedditSource
from agent_reach.research.sources.twitter import TwitterSource
from agent_reach.research.sources.v2ex import V2EXSource
from agent_reach.research.sources.web import RSSSource, WebPageSource
from agent_reach.research.sources.xiaohongshu import XiaohongshuSource
from agent_reach.research.sources.xueqiu import XueqiuSource
from agent_reach.research.sources.youtube import YouTubeSource

#: Every adapter. Searchable ones appear as chips; readers are tried in this
#: order for a URL, with the plain web page reader last.
ALL_SOURCES: List[Source] = [
    ExaSource(),
    RedditSource(),
    YouTubeSource(),
    GitHubSource(),
    TwitterSource(),
    BilibiliSource(),
    XiaohongshuSource(),
    XueqiuSource(),
    V2EXSource(),
    RSSSource(),
    WebPageSource(),
]

SOURCES_BY_NAME: Dict[str, Source] = {s.name: s for s in ALL_SOURCES}


def search_sources() -> List[Source]:
    return [s for s in ALL_SOURCES if s.searchable]


def get_source(name: str) -> Optional[Source]:
    return SOURCES_BY_NAME.get(name)


__all__ = ["ALL_SOURCES", "Context", "Source", "get_source", "search_sources"]
