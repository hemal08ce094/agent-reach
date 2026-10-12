# -*- coding: utf-8 -*-
"""The research runner with fake executables on PATH: no shell, timeouts, caps (offline)."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from agent_reach.research.runner import Runner, SourceFailure, SourceTimeout, ToolMissing
from agent_reach.research.sources import Context, get_source

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake executables use POSIX shebangs")

HOSTILE_QUERIES = ['"; rm -rf ~', "--help", "$(touch pwned)", "`id`", "a && b | c > d"]


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A PATH containing only fake tools written in Python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    def make(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\nimport json, os, sys, time\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    return make, tmp_path


def test_argv_reaches_the_tool_verbatim(fake_bin):
    make, tmp = fake_bin
    record = tmp / "argv.json"
    make("gh", f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:])); print('[]')")
    source = get_source("github")
    for query in HOSTILE_QUERIES:
        source.search(query, 3, Context(Runner(), "gh CLI"))
        argv = json.loads(record.read_text())
        assert argv[-1] == query and argv[-2] == "--"
    assert not (tmp / "pwned").exists()
    assert not any(p.name == "pwned" for p in tmp.iterdir())


def test_stdin_is_closed_and_ansi_is_stripped(fake_bin):
    make, _ = fake_bin
    make("tool", "data = sys.stdin.read(); print('\\x1b[31m' + repr(data) + '\\x1b[0m')")
    result = Runner().run(["tool"], timeout=5)
    assert result.stdout.strip() == "''"


def test_missing_tool(fake_bin):
    with pytest.raises(ToolMissing):
        Runner().run(["definitely-not-installed"], timeout=5)


def test_nonzero_exit_reports_stderr_tail(fake_bin):
    make, _ = fake_bin
    make("tool", "sys.stderr.write('line1\\nauth failed: login required\\n'); sys.exit(3)")
    with pytest.raises(SourceFailure, match="code 3: line1 auth failed: login required"):
        Runner().run(["tool"], timeout=5)


def test_timeout_kills_the_tool_and_its_children(fake_bin):
    make, tmp = fake_bin
    marker = tmp / "child-alive"
    child = tmp / "child.py"
    child.write_text(f"import time\ntime.sleep(3)\nopen({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    make("slow", f"import subprocess\nsubprocess.Popen([sys.executable, {str(child)!r}])\ntime.sleep(30)")
    started = time.monotonic()
    with pytest.raises(SourceTimeout):
        Runner().run(["slow"], timeout=0.5)
    assert time.monotonic() - started < 10
    time.sleep(3.5)
    assert not marker.exists()  # the grandchild died with the process group


def test_output_cap(fake_bin):
    make, _ = fake_bin
    make("chatty", "sys.stdout.write('x' * 300000); sys.stdout.flush(); time.sleep(5)")
    started = time.monotonic()
    with pytest.raises(SourceFailure, match="output was discarded"):
        Runner(max_output=100_000).run(["chatty"], timeout=10)
    assert time.monotonic() - started < 5


def test_register_receives_the_process(fake_bin):
    make, _ = fake_bin
    make("tool", "print('ok')")
    seen = []
    Runner().run(["tool"], timeout=5, register=seen.append)
    assert len(seen) == 1 and seen[0].returncode == 0
