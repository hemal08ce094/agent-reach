# -*- coding: utf-8 -*-
"""
AgentReach — installer, doctor, and configuration tool.

Agent Reach helps AI agents install and configure upstream platform tools
(twitter-cli, yt-dlp, mcporter, gh CLI, etc.). After installation, agents
call the upstream tools directly — no wrapper layer needed.

Usage:
    from agent_reach.doctor import check_all, format_report
    from agent_reach.config import Config

    config = Config()
    results = check_all(config)
    print(format_report(results))
"""

from typing import Dict, Optional

from agent_reach.config import Config


class AgentReach:
    """Give your AI Agent eyes to see the entire internet.

    This class provides health-check functionality.
    For reading/searching, use the upstream tools directly
    (see SKILL.md for commands).
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def doctor(self) -> Dict[str, dict]:
        """Check all channel availability."""
        from agent_reach.doctor import check_all
        return check_all(self.config)

    def doctor_report(self, *, plain: bool = False) -> str:
        """Get formatted health report.

        By default the report carries Rich markup for terminal rendering;
        ``plain=True`` strips it for consumers that show raw text (MCP clients,
        logs, chat transcripts).
        """
        from agent_reach.doctor import check_all, format_report
        report = format_report(check_all(self.config))
        return strip_markup(report) if plain else report


def strip_markup(text: str) -> str:
    """Render Rich console markup to plain text (``[bold]x[/bold]`` -> ``x``)."""
    from rich.text import Text

    return Text.from_markup(text).plain
