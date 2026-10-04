# -*- coding: utf-8 -*-
"""Shared channel helper for OpenCLI browser-session-only platforms."""

from agent_reach.utils.url import host_matches

from .base import Channel


class OpenCLISiteChannel(Channel):
    """A platform served directly by OpenCLI.

    These channels are intentionally thin: Agent Reach only installs,
    health-checks, and routes. Agents call `opencli <site> ...` directly.
    """

    site: str = ""
    domains: tuple[str, ...] = ()
    usage: str = ""
    login_hint: str = ""

    backends = ["OpenCLI"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        return host_matches(url, *self.domains)

    def check(self, config=None):
        from agent_reach.backends import opencli_status

        self.active_backend = None
        st = opencli_status()
        if not st.installed:
            return "off", (
                f"{self.description}: no backend installed. Install:\n"
                "  agent-reach install --system --channels opencli\n"
                f"then log in to {self.login_hint} in Chrome"
            )
        if st.broken:
            return "error", st.hint

        # Installed and runnable: OpenCLI is the backend serving this channel,
        # even though Doctor cannot verify the login (B9: set on warn too).
        self.active_backend = self.backends[0]
        if st.ready:
            return "warn", (
                f"OpenCLI bridge connected, but the {self.description} login and real "
                "commands were not verified live; Doctor does not run platform commands, "
                "so it is not marked available. "
                f"When needed, log in to {self.login_hint} in Chrome first"
            )
        return "warn", st.hint
