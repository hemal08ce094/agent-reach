# -*- coding: utf-8 -*-
"""Twitter/X — check if twitter-cli or bird CLI is available."""

import os
import shutil

from agent_reach.utils.url import host_matches

from .base import Channel


def twitter_cli_child_env(config=None) -> dict[str, str]:
    """Return saved credentials missing from the current process environment.

    The returned mapping is meant for a single child process.  Existing shell
    variables remain authoritative and ``os.environ`` is never mutated.
    """
    if config is None:
        return {}

    child_env = {}
    for env_name, config_key in (
        ("TWITTER_AUTH_TOKEN", "twitter_auth_token"),
        ("TWITTER_CT0", "twitter_ct0"),
    ):
        # An empty shell variable (TWITTER_AUTH_TOKEN="") must not hide the
        # saved credential; only a non-empty value is authoritative.
        if os.environ.get(env_name):
            continue
        value = config.get(config_key)
        if value:
            child_env[env_name] = str(value)
    return child_env


class TwitterChannel(Channel):
    name = "twitter"
    description = "Twitter/X posts"
    backends = ["twitter-cli", "OpenCLI", "bird CLI (legacy)"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "x.com", "twitter.com")

    def check(self, config=None):
        """Probe candidates in order; first fully-usable backend wins.

        Same two-phase approach as the other multi-backend channels: collect every
        candidate's status first, and the first ok wins; only when nothing is ok does
        the first warn win. Otherwise an "installed but logged out" twitter-cli would
        lock out a fully usable OpenCLI that comes later in the list.
        """
        self.active_backend = None
        findings = []

        for backend in self.ordered_backends(config):
            if backend == "twitter-cli":
                result = self._check_twitter_cli(config)
            elif backend == "OpenCLI":
                result = self._check_opencli()
            elif backend == "bird CLI (legacy)":
                result = self._check_bird()
            else:
                continue

            if result is None:
                continue  # not installed: not a candidate
            findings.append((backend, *result))

        # First ok wins, else first warn; active_backend is set for both (B9).
        picked = self.pick_finding(findings)
        if picked is not None:
            return picked

        if findings:  # only broken/timeout candidates left
            return "error", "\n".join(m for _, _, m in findings)

        return "warn", (
            "Twitter CLI is not installed. Install with:\n"
            "  pipx install twitter-cli\n"
            "or:\n"
            "  uv tool install twitter-cli"
        )

    def _check_twitter_cli(self, config=None):
        """Inspect explicit credentials without starting twitter-cli.

        Upstream ``twitter status`` automatically reads browser cookies when
        credentials are missing *or invalid*. Doctor cannot disable that
        fallback, so executing it would violate the Cookie-Editor-only policy.
        """
        if not shutil.which("twitter"):
            return None

        child_env = twitter_cli_child_env(config)
        auth_token = os.environ.get("TWITTER_AUTH_TOKEN") or child_env.get(
            "TWITTER_AUTH_TOKEN"
        )
        ct0 = os.environ.get("TWITTER_CT0") or child_env.get("TWITTER_CT0")
        if auth_token and ct0:
            return "warn", (
                "twitter-cli is installed and Cookie-Editor credentials are configured; "
                "Doctor does not run `twitter status` because upstream automatically "
                "reads browser cookies when verification fails. Verify manually only "
                "when you explicitly agree to that."
            )
        return "warn", (
            "twitter-cli is installed but has no complete explicit credentials. Export "
            "them from x.com with Cookie-Editor, then run:\n"
            "  agent-reach configure twitter-cookies\n"
            "Doctor never reads browser cookies automatically."
        )

    def _check_opencli(self):
        """OpenCLI candidate. None = not installed."""
        from agent_reach.backends import opencli_status

        st = opencli_status()
        if not st.installed:
            return None
        if st.broken:
            return "error", st.hint
        if st.ready:
            return "warn", (
                "OpenCLI bridge connected, but the Twitter/X login and real commands "
                "were not verified live; Doctor does not run platform commands, so it "
                "is not marked available."
            )
        return "warn", st.hint

    def _check_bird(self):
        """Inspect legacy bird credentials without launching browser fallback."""
        for cmd in ("bird", "birdx"):
            if not shutil.which(cmd):
                continue
            if os.environ.get("AUTH_TOKEN") and os.environ.get("CT0"):
                return (
                    "warn",
                    f"{cmd} is installed and explicit environment credentials exist; "
                    "to avoid upstream falling back to browser cookies, Doctor does not "
                    "run `check`, so they were not verified live.",
                )
            return "warn", (
                f"{cmd} is installed but no explicit AUTH_TOKEN/CT0 was found; "
                "only credentials exported manually with Cookie-Editor are used."
            )
        return None
