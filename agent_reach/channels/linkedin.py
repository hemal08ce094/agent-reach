# -*- coding: utf-8 -*-
"""LinkedIn — check if mcp-server-linkedin is configured."""

import shutil

from .base import Channel
from .mcporter import (
    McporterConfigError,
    inspect_mcporter_config,
    skipped_layers_note,
)

_LINKEDIN_SERVER_NAMES = {
    "linkedin",
    "linkedin-scraper",
    "linkedin-scraper-mcp",
    "mcp-server-linkedin",
}
_LOGIN_COMMAND = "uvx mcp-server-linkedin@latest --login"
_UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"
_CONFIG_COMMAND = (
    "mcporter config add linkedin --command uvx "
    "--arg mcp-server-linkedin@latest --env UV_HTTP_TIMEOUT=300 --scope home"
)


class LinkedInChannel(Channel):
    name = "linkedin"
    description = "LinkedIn professional network"
    backends = ["mcp-server-linkedin", "Jina Reader"]
    tier = 2

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "linkedin.com")

    def check(self, config=None):
        self.active_backend = None
        if not shutil.which("mcporter"):
            return "off", (
                "Basic content is readable via Jina Reader. Full features need:\n"
                f"  Install uv/uvx first: {_UV_INSTALL_URL}\n"
                f"  {_LOGIN_COMMAND}\n"
                f"  {_CONFIG_COMMAND}\n"
                "  See https://github.com/stickerdaniel/linkedin-mcp-server"
            )
        try:
            inspection = inspect_mcporter_config()
        except McporterConfigError as exc:
            return "error", f"mcporter config check failed: {exc}"
        if inspection.server_names & _LINKEDIN_SERVER_NAMES:
            # Configured: this is the backend that serves the channel (B9).
            self.active_backend = self.backends[0]
            if not shutil.which("uvx"):
                return "warn", (
                    "LinkedIn MCP is in the mcporter config, but uvx is not installed, "
                    "so the service cannot start. Install:\n"
                    f"  {_UV_INSTALL_URL}"
                )
            return "warn", (
                "LinkedIn MCP is in the mcporter config, but Doctor did not start the "
                "local service to verify connectivity, so config alone cannot prove full "
                "functionality."
            )
        if inspection.imports_unchecked:
            return "warn", (
                "LinkedIn MCP not found in the local mcporter config; the config also "
                "enables editor imports, which Doctor does not expand to avoid reading "
                "more credentials, so it is unverified."
            )
        note = skipped_layers_note(inspection)
        if note:
            return "warn", note
        return "off", (
            "mcporter is installed but LinkedIn MCP is not configured. Run:\n"
            f"  Install uv/uvx first: {_UV_INSTALL_URL}\n"
            f"  {_LOGIN_COMMAND}\n"
            f"  {_CONFIG_COMMAND}"
        )
