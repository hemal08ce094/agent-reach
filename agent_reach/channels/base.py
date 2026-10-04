# -*- coding: utf-8 -*-
"""
Channel base class — platform availability checking.

Each channel represents a platform (YouTube, Twitter, GitHub, etc.)
and provides:
  - can_handle(url) → does this URL belong to this platform?
  - check(config) → is the upstream tool installed and configured?

After installation, agents call upstream tools directly.

Backend routing semantics:
  - `backends` is an ORDERED candidate list: backends[0] is the preferred
    backend, the rest are fallbacks. "Switching backends" for a platform
    means reordering this list (or a user override) — not rewriting code.
  - check() must set `self.active_backend` to the backend that is actually
    serving the channel right now (None when nothing usable is found).
    shutil.which() alone is NOT proof of health — a stale venv shim passes
    which() but cannot execute (see agent_reach.probe). Channels should
    really execute a lightweight command before claiming a backend active.
  - Users can force a backend with config key `<channel>_backend`
    (or env var `<CHANNEL>_BACKEND`); ordered_backends() applies it.
    Matching is case-insensitive and accepts ASCII slugs/aliases: for
    "Bilibili search API" any of `bilibili-search-api`, `bilibili-api`,
    `search-api` or `api` works; `opencli` selects "OpenCLI".
  - active_backend contract (doctor --json): whenever check() returns "ok" or
    "warn", active_backend names the backend that would be used; for "off",
    "error" and "timeout" it is None. pick_finding() implements this for
    multi-backend channels.
"""

import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger


def _backend_tokens(value: str) -> List[str]:
    """Lower-case ASCII word tokens: "xhs-cli (xiaohongshu-cli)" -> [xhs, cli, ...]."""
    return [token for token in re.split(r"[^0-9a-z]+", value.casefold()) if token]


class Channel(ABC):
    """Base class for all channels."""

    name: str = ""                    # e.g. "youtube"
    description: str = ""             # e.g. "YouTube videos and subtitles"
    backends: List[str] = []          # ordered candidates — backends[0] = preferred
    tier: int = 0                     # 0=zero-config, 1=needs free key, 2=needs setup

    #: Backend currently serving this channel; set by check(), None = unavailable.
    active_backend: Optional[str] = None

    #: Extra override spellings, alias -> exact backend name. Matching is
    #: already case-insensitive and slug-based, so this is only needed for
    #: aliases that share no word with the backend name.
    backend_aliases: Dict[str, str] = {}

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this channel can handle this URL."""
        ...

    def match_backend(self, override: str) -> Optional[str]:
        """Return the backend an override value names, or None.

        Tried in order, first hit wins: an explicit alias, the exact name
        (case-insensitive), the compact slug ("opencli", "bilicli"), a prefix
        of the slug, then the override's words being a subset of the backend's
        words in order ("bilibili-api" -> "Bilibili search API").
        """
        wanted = str(override).strip()
        if not wanted:
            return None
        folded = wanted.casefold()
        wanted_tokens = _backend_tokens(wanted)
        compact = "".join(wanted_tokens)

        aliases = {k.casefold(): v for k, v in self.backend_aliases.items()}
        if folded in aliases and aliases[folded] in self.backends:
            return aliases[folded]
        if compact in aliases and aliases[compact] in self.backends:
            return aliases[compact]

        for backend in self.backends:
            if backend.casefold() == folded:
                return backend
        if not compact:
            return None
        for backend in self.backends:
            if "".join(_backend_tokens(backend)) == compact:
                return backend
        for backend in self.backends:
            slug = "-".join(_backend_tokens(backend))
            if slug.startswith("-".join(wanted_tokens)):
                return backend
        for backend in self.backends:
            if _is_ordered_subset(wanted_tokens, _backend_tokens(backend)):
                return backend
        return None

    def ordered_backends(self, config=None) -> List[str]:
        """Candidate backends in probe order, honoring the user override.

        The config key `<channel>_backend` (env `<CHANNEL>_BACKEND`) moves the
        named backend to the front of the list; unknown values are ignored
        (with a warning) so a stale override can never hide working backends.
        """
        candidates = list(self.backends)
        override = config.get(f"{self.name}_backend") if config else None
        if override:
            match = self.match_backend(str(override))
            if match is None:
                logger.warning(
                    f"{self.name}_backend={override!r} matches no {self.name} backend "
                    f"(choices: {', '.join(self.backends)}); using the default order"
                )
            else:
                candidates.remove(match)
                candidates.insert(0, match)
        return candidates

    def pick_finding(
        self, findings: Sequence[Tuple[str, str, str]]
    ) -> Optional[Tuple[str, str]]:
        """Pick the result to report from ``(backend, status, message)`` findings.

        The first "ok" wins; failing that, the first "warn". Sets
        ``active_backend`` to that backend (for ok and warn alike) and returns
        ``(status, message)``; returns None, leaving active_backend untouched,
        when no finding is usable.
        """
        for wanted in ("ok", "warn"):
            for backend, status, message in findings:
                if status == wanted:
                    self.active_backend = backend
                    return status, message
        return None

    def check(self, config=None) -> Tuple[str, str]:
        """
        Check if this channel's upstream tool is available.
        Returns (status, message) where status is 'ok'/'warn'/'off'/'error'.

        Subclasses with external backends must really probe them (see
        agent_reach.probe.probe_command) and set self.active_backend.
        """
        self.active_backend = self.backends[0] if self.backends else "built-in"
        return "ok", f"{', '.join(self.backends) if self.backends else 'built-in'}"


def _is_ordered_subset(needle: List[str], haystack: List[str]) -> bool:
    """True if every needle token appears in haystack, in the same order."""
    if not needle:
        return False
    position = 0
    for token in needle:
        try:
            position = haystack.index(token, position) + 1
        except ValueError:
            return False
    return True
