# -*- coding: utf-8 -*-
"""Adapter contract for research sources.

One adapter per platform. An adapter knows which doctor backends it can drive,
the exact documented command for each, and how to parse that command's output.
It never decides availability itself: the manager passes in the backend that
``agent-reach doctor`` reported as active.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

from agent_reach.research.models import ReadResult, ResearchItem
from agent_reach.research.runner import (
    DEFAULT_TIMEOUT,
    CommandResult,
    Runner,
    SourceFailure,
    display_command,
)


@dataclass
class Context:
    """Per-call execution context handed to an adapter."""

    runner: Runner
    backend: Optional[str] = None
    config: Any = None
    timeout: float = DEFAULT_TIMEOUT
    register: Optional[Callable[[subprocess.Popen], None]] = None
    #: Display strings of every command the call ran, for the UI.
    commands: List[str] = field(default_factory=list)

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        self.commands.append(display_command(argv))
        kwargs.setdefault("timeout", self.timeout)
        return self.runner.run(argv, register=self.register, **kwargs)

    def note(self, description: str) -> None:
        """Record a library call (no subprocess) for the UI."""
        self.commands.append(description)


class Source:
    """Base adapter. Subclasses set the class attributes and override hooks."""

    name: str = ""
    label: str = ""
    #: Registry name of the channel whose doctor result gates this source.
    channel: str = ""
    #: Doctor backend names this adapter can drive, in preference order.
    backends: Tuple[str, ...] = ()
    searchable: bool = True
    #: Whether the chip starts selected when the source is available.
    default_selected: bool = True
    #: Short description shown next to the chip.
    hint: str = ""

    # ── availability ──

    def blocked_reason(self, backend: str, config: Any) -> Optional[str]:
        """Extra precondition beyond doctor's status; a reason string blocks the source."""
        return None

    # ── search ──

    def search_argv(self, query: str, limit: int, backend: str) -> Optional[List[str]]:
        """The documented command for a search, or None for library-backed sources."""
        return None

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        raise SourceFailure(f"{self.label} does not support search")

    # ── read ──

    def can_read(self, url: str) -> bool:
        return False

    def read(self, url: str, ctx: Context) -> ReadResult:
        raise SourceFailure(f"{self.label} cannot read this link")

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        """Shell commands an agent can run to dig deeper into ``url``."""
        return []

    def command_preview(self, query: str, limit: int, backend: str) -> str:
        argv = self.search_argv(query, limit, backend)
        return display_command(argv) if argv else ""


def require_backend(source: Source, backend: Optional[str]) -> str:
    if backend not in source.backends:
        raise SourceFailure(
            f"{source.label} supports {', '.join(source.backends)} here; "
            f"the active backend is {backend or 'none'}"
        )
    return str(backend)
