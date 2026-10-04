# -*- coding: utf-8 -*-
"""RSS — check if feedparser is available."""

from urllib.parse import urlsplit

from .base import Channel

# Path segments that conventionally serve a feed (e.g. /feed, /blog/rss).
_FEED_SEGMENTS = {"feed", "feeds", "rss", "atom", "rss2"}
# ``<stem>.xml`` names that conventionally denote a feed document.
_FEED_STEMS = {"feed", "feeds", "rss", "atom", "index", "rss2"}


class RSSChannel(Channel):
    name = "rss"
    description = "RSS/Atom feeds"
    backends = ["feedparser"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        """Match feed-shaped HTTP(S) URLs by path segment / suffix, not substring.

        ``/wiki/Anatomy``, ``/issues/feedback`` and ``sitemap.xml`` are not
        feeds; ``/feed``, ``/blog/rss/``, ``/atom.xml`` and ``/news.rss`` are.
        """
        try:
            parsed = urlsplit(str(url or "").strip())
        except ValueError:
            return False
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        segments = [seg.lower() for seg in parsed.path.split("/") if seg]
        if not segments:
            return False
        last = segments[-1]
        if "sitemap" in last:
            return False
        if any(seg in _FEED_SEGMENTS for seg in segments):
            return True
        if last.endswith((".rss", ".atom", ".rdf")):
            return True
        if last.endswith(".xml"):
            stem = last[: -len(".xml")]
            return stem in _FEED_STEMS or any(
                token in stem for token in ("rss", "atom", "feed")
            )
        return False

    def check(self, config=None):
        try:
            import feedparser  # noqa: F401
        except ImportError:
            self.active_backend = None
            return "off", "feedparser is not installed. Install: pip install feedparser"
        except Exception as e:
            # Installed but crashes on import (partial install/version conflict) → reinstall fix
            self.active_backend = None
            return "error", f"feedparser import failed: {e}\nFix: pip install --force-reinstall feedparser"
        self.active_backend = self.backends[0]
        return "ok", "Can read RSS/Atom feeds"
