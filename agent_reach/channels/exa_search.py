# -*- coding: utf-8 -*-
"""Exa Search — check if mcporter + Exa MCP is available."""

import shutil

from .base import Channel
from .mcporter import McporterConfigError, inspect_mcporter_config


class ExaSearchChannel(Channel):
    name = "exa_search"
    description = "Web semantic search"
    backends = ["Exa via mcporter"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return False  # Search-only channel

    def check(self, config=None):
        self.active_backend = None
        if not shutil.which("mcporter"):
            return "off", (
                "Needs mcporter + Exa MCP. Install:\n"
                "  npm install -g mcporter\n"
                "  mcporter config add exa https://mcp.exa.ai/mcp --scope home"
            )
        try:
            inspection = inspect_mcporter_config()
        except McporterConfigError as exc:
            return "error", f"mcporter config check failed: {exc}"
        if "exa" in inspection.server_names:
            return "warn", (
                "Exa is in the mcporter config, but Doctor did not start the remote "
                "service to verify connectivity, so config alone cannot prove it works."
            )
        if inspection.imports_unchecked:
            return "warn", (
                "Exa not found in the local mcporter config; the config also enables "
                "editor imports, which Doctor does not expand to avoid reading more "
                "credentials, so it is unverified."
            )
        return "off", (
            "mcporter is installed but Exa is not configured. Run:\n"
            "  mcporter config add exa https://mcp.exa.ai/mcp --scope home"
        )
