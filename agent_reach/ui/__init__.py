# -*- coding: utf-8 -*-
"""Local web dashboard for Agent Reach (``agent-reach ui``).

The dashboard is a thin view over the existing CLI and library: doctor results
come from :func:`agent_reach.doctor.check_all`, and every action runs the real
CLI as a subprocess (``python -m agent_reach.cli ...``) from a server-side
allowlist. It never reimplements upstream tools.
"""

from agent_reach.ui.server import DashboardServer, serve

__all__ = ["DashboardServer", "serve"]
