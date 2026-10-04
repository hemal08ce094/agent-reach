# -*- coding: utf-8 -*-
"""Environment health checker — powered by channels.

Each channel knows how to check itself. Doctor just collects the results.
"""

import os
import threading
import time
from typing import Dict, Optional

from rich.markup import escape

from agent_reach.backends.opencli import opencli_status_memo
from agent_reach.channels import get_all_channels, get_channel
from agent_reach.config import Config
from agent_reach.utils.text import scrub_url_credentials

#: Wall-clock budget for the whole doctor run, in seconds. All channel checks
#: run concurrently and share this budget; it is not multiplied per channel.
DEFAULT_CHECK_TIMEOUT = 30.0

#: Statuses for which ``active_backend`` names the backend that would serve
#: the channel. For every other status it is None.
_USABLE_STATUSES = ("ok", "warn")


def _resolve_timeout(timeout: Optional[float]) -> float:
    if timeout is not None:
        return max(0.0, float(timeout))
    raw = os.environ.get("AGENT_REACH_DOCTOR_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_CHECK_TIMEOUT
        if value > 0:
            return value
    return DEFAULT_CHECK_TIMEOUT


def _run_check(ch, config, slot: dict) -> None:
    """Thread body: run one channel check and store (status, message, active)."""
    try:
        status, message = ch.check(config)
        active = getattr(ch, "active_backend", None)
    except Exception as e:  # noqa: BLE001 — doctor must survive any channel
        # Channels are registry singletons: a stale active_backend from a
        # previous check must not leak into an errored result.
        status = "error"
        message = f"Health check failed: {e}"
        active = None
    slot["result"] = (status, message, active)


def _normalize_active_backend(status: str, active: Optional[str]) -> Optional[str]:
    """Enforce the status side of the ``active_backend`` contract.

    Channels set ``active_backend`` to the backend that would be used whenever
    they report ok or warn (see ``Channel.pick_finding``). Doctor cannot infer
    it — "warn" also covers "not installed, but optional" — so it only
    guarantees that off/error/timeout results never carry a backend.
    """
    if status not in _USABLE_STATUSES:
        return None
    return active


def _check_channels(channels, config: Config, budget: float) -> Dict[str, dict]:
    """Run ``channels`` checks concurrently under one shared ``budget``."""
    slots: list = [{} for _ in channels]
    threads = []

    with opencli_status_memo():
        for ch, slot in zip(channels, slots):
            thread = threading.Thread(
                target=_run_check,
                args=(ch, config, slot),
                name=f"agent-reach-doctor-{ch.name}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        deadline = time.monotonic() + budget
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    results = {}
    for ch, slot, thread in zip(channels, slots, threads):
        result = slot.get("result")
        if result is None and thread.is_alive():
            status, message, active = (
                "timeout",
                f"Health check did not finish within {budget:g}s; "
                "an upstream tool may be hanging",
                None,
            )
        elif result is None:
            status, message, active = ("error", "Health check aborted", None)
        else:
            status, message, active = result
        active = _normalize_active_backend(status, active)
        # Doctor is the final output boundary for both expected channel
        # messages and unexpected exceptions. Upstream probe output can echo a
        # configured URL, so scrub every path before JSON/text rendering.
        message = scrub_url_credentials(message)
        results[ch.name] = {
            "status": status,
            "name": ch.description,
            "message": message,
            "tier": ch.tier,
            "backends": ch.backends,
            "active_backend": active,
        }
    return results


def check_all(config: Config, *, timeout: Optional[float] = None) -> Dict[str, dict]:
    """Check all channels concurrently and return the status dict.

    A single misbehaving channel must never take the whole report down:
    per-channel exceptions degrade to status="error", and checks still running
    when the shared ``timeout`` budget (default 30 s, env
    ``AGENT_REACH_DOCTOR_TIMEOUT``) expires are reported as status="timeout".
    Checks run in daemon threads so a hung upstream tool can never keep the
    interpreter alive after the report is printed. Result order always follows
    the channel registry, independent of completion order.
    """
    return _check_channels(list(get_all_channels()), config, _resolve_timeout(timeout))


def check_one(
    name: str, config: Config, *, timeout: Optional[float] = None
) -> Optional[dict]:
    """Check a single channel by registry name with the same rules as check_all.

    Returns the same per-channel dict ``check_all`` produces (including the
    timeout/error degradation), or None when no channel has that name.
    """
    channel = get_channel(name)
    if channel is None:
        return None
    return _check_channels([channel], config, _resolve_timeout(timeout))[channel.name]


def _name_msg(r: dict, escape) -> str:
    """Render one channel line; show the active backend when there is a choice."""
    text = f"[bold]{escape(r['name'])}[/bold] — {escape(r['message'])}"
    active = r.get("active_backend")
    if active and len(r.get("backends", [])) > 1:
        text += f" [dim](Current backend: {escape(active)})[/dim]"
    return text


def format_report(results: Dict[str, dict]) -> str:
    """Format results as a readable text report (with Rich markup)."""
    lines = []
    lines.append("[bold cyan]Agent Reach status[/bold cyan]")
    lines.append("[cyan]" + "=" * 40 + "[/cyan]")
    lines.append(
        "Legend: [green]✅[/green] available  "
        "[yellow][!][/yellow] installed but needs config/login  "
        "[red][X][/red] not installed"
    )

    ok_count = sum(1 for r in results.values() if r["status"] == "ok")
    total = len(results)

    # Tier 0 — zero config
    lines.append("")
    lines.append("[bold]✅ Ready out of the box:[/bold]")
    for key, r in results.items():
        if r["tier"] == 0:
            name_msg = _name_msg(r, escape)
            if r["status"] == "ok":
                lines.append(f"  [green]✅[/green] {name_msg}")
            elif r["status"] == "warn":
                lines.append(f"  [yellow][!][/yellow]  {name_msg}")
            elif r["status"] in ("off", "error", "timeout"):
                lines.append(f"  [red][X][/red]  {name_msg}")

    # Tier 1 — needs free key / login
    tier1 = {k: r for k, r in results.items() if r["tier"] == 1}
    tier1_active = {k: r for k, r in tier1.items() if r["status"] == "ok"}
    tier1_inactive = {k: r for k, r in tier1.items() if r["status"] != "ok"}
    if tier1_active:
        lines.append("")
        lines.append("[bold]Optional channels (installed):[/bold]")
        for key, r in tier1_active.items():
            lines.append(f"  [green]✅[/green] {_name_msg(r, escape)}")

    # Tier 2 — optional complex setup
    tier2 = {k: r for k, r in results.items() if r["tier"] == 2}
    tier2_active = {k: r for k, r in tier2.items() if r["status"] == "ok"}
    tier2_inactive = {k: r for k, r in tier2.items() if r["status"] != "ok"}
    if tier2_active:
        if not tier1_active:
            lines.append("")
            lines.append("[bold]Optional channels (installed):[/bold]")
        for key, r in tier2_active.items():
            lines.append(f"  [green]✅[/green] {_name_msg(r, escape)}")

    lines.append("")
    status_color = "green" if ok_count == total else ("yellow" if ok_count > 0 else "red")
    lines.append(
        f"Status: [{status_color}]{ok_count}/{total}[/{status_color}] channels available"
    )

    # Summarize inactive optional channels in one line instead of listing each
    all_inactive = list(tier1_inactive.values()) + list(tier2_inactive.values())
    if all_inactive:
        names = [r["name"] for r in all_inactive]
        lines.append(
            f"{len(names)} more optional channels can be unlocked ({', '.join(names)}); "
            "just tell your agent \"install XXX for me\""
        )

    # Security check: config file permissions (Unix only)
    import stat
    import sys

    config_path = Config.CONFIG_DIR / "config.yaml"
    if config_path.exists() and sys.platform != "win32":
        try:
            mode = config_path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                lines.append("")
                lines.append(
                    "[bold red][!]  Security warning: config.yaml permissions are too open "
                    "(readable by other users)[/bold red]"
                )
                lines.append("   Fix: chmod 600 ~/.agent-reach/config.yaml")
        except OSError:
            pass

    return "\n".join(lines)
