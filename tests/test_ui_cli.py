# -*- coding: utf-8 -*-
"""CLI wiring, doctor.check_one and catalog parsing for the dashboard."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from agent_reach import doctor
from agent_reach.config import Config
from agent_reach.ui import catalog


def _run_cli(argv):
    from agent_reach.cli import main

    with patch.object(sys, "argv", ["agent-reach", *argv]):
        main()


def test_ui_parses_no_open_port_zero():
    with patch("agent_reach.ui.server.serve", return_value=0) as serve:
        _run_cli(["ui", "--no-open", "--port", "0"])
    serve.assert_called_once_with("127.0.0.1", 0, open_browser=False)


def test_ui_defaults_open_browser():
    with patch("agent_reach.ui.server.serve", return_value=0) as serve:
        _run_cli(["ui"])
    serve.assert_called_once_with("127.0.0.1", 0, open_browser=True)


@pytest.mark.parametrize("argv", [["ui", "--host", "0.0.0.0"], ["ui", "--port", "70000"],
                                  ["ui", "--host", "192.168.1.5"]])
def test_ui_rejects_bad_host_or_port(argv):
    with patch("agent_reach.ui.server.serve") as serve, pytest.raises(SystemExit) as exc:
        _run_cli(argv)
    assert exc.value.code == 2
    serve.assert_not_called()


def test_serve_prints_url_and_stops_on_ctrl_c(capsys):
    from agent_reach.ui import server as ui_server

    def interrupt(self, poll_interval=0.5):
        raise KeyboardInterrupt

    with patch.object(ui_server.DashboardServer, "serve_forever", interrupt), \
            patch.object(ui_server.webbrowser, "open") as opener:
        code = ui_server.serve("127.0.0.1", 0, open_browser=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "http://127.0.0.1:" in out and "/#token=" in out
    assert "Stopping dashboard" in out
    opener.assert_not_called()


def test_check_one_matches_check_all_semantics(monkeypatch):
    from agent_reach.channels.base import Channel

    class Hang(Channel):
        name = "github"
        description = "hang"
        backends = ["x"]

        def can_handle(self, url):
            return False

        def check(self, config=None):
            import time

            time.sleep(2)
            return "ok", "late"

    monkeypatch.setattr(doctor, "get_channel", lambda name: Hang() if name == "github" else None)
    result = doctor.check_one("github", Config(read_only=True), timeout=0.05)
    assert result["status"] == "timeout"
    assert result["active_backend"] is None
    assert doctor.check_one("nope", Config(read_only=True)) is None


def test_check_one_real_channel():
    result = doctor.check_one("rss", Config(read_only=True))
    assert result["status"] == "ok"
    assert set(result) == {"status", "name", "message", "tier", "backends", "active_backend"}


def test_extract_section_and_snippets():
    md = (
        "# Title\n\nintro\n\n## Alpha\n\nAlpha intro.\n\n```bash\n# Do one\ncmd one\n\n"
        "# Do two\ncmd two\n```\n\n## Beta\n\n```bash\n## not a heading\nbeta\n```\n"
    )
    alpha = catalog.extract_section(md, "Alpha")
    assert alpha.startswith("## Alpha") and "Beta" not in alpha
    snippets = catalog.extract_snippets(alpha)
    assert snippets == [
        {"title": "Do one", "code": "cmd one", "lang": "bash"},
        {"title": "Do two", "code": "cmd two", "lang": "bash"},
    ]
    beta = catalog.extract_section(md, "Beta")
    assert "beta" in beta
    assert catalog.extract_section(md, None).startswith("intro")


def test_every_channel_has_metadata_and_snippets():
    from agent_reach.channels import get_all_channels

    entries = {e["name"]: e for e in catalog.channel_catalog(Config(read_only=True))}
    for ch in get_all_channels():
        assert ch.name in catalog.CHANNEL_META
        assert entries[ch.name]["snippets"], f"{ch.name} has no agent snippets"


def test_installable_channels_are_accepted_by_cli():
    """Every channel the dashboard can install is accepted by `install --channels`."""
    import inspect

    from agent_reach import cli

    source = inspect.getsource(cli._cmd_install)
    for name in catalog.INSTALLABLE_CHANNELS:
        assert f'"{name}":' in source


def test_configure_keys_match_cli_choices():
    import inspect

    from agent_reach import cli

    source = inspect.getsource(cli.main)
    for key in catalog.CONFIGURE_FIELDS:
        assert f'"{key}"' in source
