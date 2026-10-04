# -*- coding: utf-8 -*-
"""Bounded subprocess runner for research commands.

Commands are argv lists built by the source adapters; there is never a shell.
The executable is resolved with ``shutil.which``, stdin is closed, the child
runs in its own session so a timeout kills its whole process tree, and output
is capped and stripped of terminal escapes before any parser sees it.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from agent_reach.probe import _kill_process_tree
from agent_reach.utils.process import utf8_subprocess_env
from agent_reach.utils.text import scrub_url_credentials

DEFAULT_TIMEOUT = 25.0
READ_TIMEOUT = 45.0
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_STDERR_BYTES = 64 * 1024

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

#: Read-only, non-interactive settings applied to every research child.
_CHILD_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PYTHONUNBUFFERED": "1",
    "GH_TELEMETRY": "false",
    "DO_NOT_TRACK": "true",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "GH_PROMPT_DISABLED": "1",
}


class SourceFailure(Exception):
    """A source could not produce results; ``message`` is safe to show."""

    status = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SourceTimeout(SourceFailure):
    status = "timeout"


class ToolMissing(SourceFailure):
    pass


@dataclass
class CommandResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n")


def display_command(argv: Sequence[str]) -> str:
    """Copyable shell rendering of an argv list (display only, never executed)."""
    return " ".join(shlex.quote(str(a)) for a in argv)


def child_env(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = utf8_subprocess_env()
    env.update(_CHILD_ENV)
    if extra:
        env.update(extra)
    return env


def _short_error(stderr: str, stdout: str) -> str:
    text = (stderr or stdout or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    tail = " ".join(lines[-3:])
    return scrub_url_credentials(tail[:400])


class Runner:
    """Runs one argv with a timeout, an output cap and process-tree cleanup."""

    def __init__(self, max_output: int = MAX_OUTPUT_BYTES, which: Callable[[str], Optional[str]] = shutil.which):
        self.max_output = max_output
        self._which = which

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
        register: Optional[Callable[[subprocess.Popen], None]] = None,
        check: bool = True,
    ) -> CommandResult:
        args = [str(a) for a in argv]
        if not args:
            raise SourceFailure("empty command")
        executable = self._which(args[0])
        if not executable:
            raise ToolMissing(f"{args[0]} is not installed or not on PATH")
        popen_kwargs: Dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [executable, *args[1:]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env(env),
                cwd=cwd,
                shell=False,
                **popen_kwargs,
            )
        except OSError as exc:
            raise SourceFailure(f"{args[0]} could not start: {exc.strerror or exc}") from None
        if register is not None:
            register(proc)

        out = bytearray()
        err = bytearray()
        overflow = threading.Event()

        def pump(stream, buf: bytearray, cap: int, is_stdout: bool) -> None:
            try:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    room = cap - len(buf)
                    if room > 0:
                        buf.extend(chunk[:room])
                    if len(chunk) > room and is_stdout:
                        overflow.set()
                        _kill_process_tree(proc)
                        break
            except (OSError, ValueError):
                pass

        readers = [
            threading.Thread(target=pump, args=(proc.stdout, out, self.max_output, True), daemon=True),
            threading.Thread(target=pump, args=(proc.stderr, err, _STDERR_BYTES, False), daemon=True),
        ]
        for t in readers:
            t.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            for t in readers:
                t.join(2)
            raise SourceTimeout(f"{args[0]} did not finish within {timeout:g}s and was stopped") from None
        for t in readers:
            t.join(5)
        if overflow.is_set():
            raise SourceFailure(f"{args[0]} returned more than {self.max_output // (1024 * 1024)} MB; output was discarded")
        stdout = strip_ansi(bytes(out).decode("utf-8", errors="replace"))
        stderr = strip_ansi(bytes(err).decode("utf-8", errors="replace"))
        code = proc.returncode if proc.returncode is not None else -1
        if check and code != 0:
            detail = _short_error(stderr, stdout)
            message = f"{args[0]} exited with code {code}"
            raise SourceFailure(f"{message}: {detail}" if detail else message)
        return CommandResult(args, code, stdout, stderr)
