# -*- coding: utf-8 -*-
"""Research workspace: search ready channels, read full text, pin to boards.

This is the one place Agent Reach fetches content itself, and it only runs
the upstream commands and library calls the agent skill documents (see
``skill/SKILL.md`` and ``skill/references/``). Each adapter in ``sources/``
names its exact command; availability always comes from ``agent-reach doctor``.
"""

from agent_reach.research.boards import BoardStore
from agent_reach.research.manager import ResearchManager, SourceState, source_states
from agent_reach.research.models import ReadResult, ResearchError, ResearchItem

__all__ = [
    "BoardStore",
    "ReadResult",
    "ResearchError",
    "ResearchItem",
    "ResearchManager",
    "SourceState",
    "source_states",
]
