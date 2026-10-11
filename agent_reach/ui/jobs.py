# -*- coding: utf-8 -*-
"""Allowlisted background jobs for the dashboard.

Every job is the real CLI, run as ``sys.executable -m agent_reach.cli ...``
with an argv list built here from validated parameters. There is no shell and
no free-form command: unknown actions and parameters are rejected before a
process starts. Only one job runs at a time, so two installers can never race
on the same tools or config file.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_reach.ui.catalog import (
    BROWSER_CHOICES,
    BROWSER_IMPORT_PLATFORMS,
    INSTALLABLE_CHANNELS,
)
from agent_reach.utils.process import utf8_subprocess_env
from agent_reach.utils.text import scrub_url_credentials

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

#: Per-job output cap; older output is dropped from the front beyond this.
MAX_OUTPUT_CHARS = 512 * 1024
#: Finished jobs kept for the log panel.
MAX_FINISHED_JOBS = 20

INSTALL_TIMEOUT = 20 * 60.0
SHORT_TIMEOUT = 120.0


class JobError(Exception):
    """A job request the caller should see as an HTTP error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n")


def cli_argv(*args: str) -> List[str]:
    return [sys.executable, "-m", "agent_reach.cli", *args]


def child_env() -> Dict[str, str]:
    env = utf8_subprocess_env()
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


# ── action builders (the allowlist) ──────────────────────────────────────────


def _bool(params: Dict[str, Any], key: str) -> bool:
    value = params.get(key, False)
    if not isinstance(value, bool):
        raise JobError(400, f"{key} must be true or false")
    return value


def _build_install(params: Dict[str, Any]) -> Tuple[List[str], str, float]:
    channels = params.get("channels", [])
    if not isinstance(channels, list) or not all(isinstance(c, str) for c in channels):
        raise JobError(400, "channels must be a list of channel names")
    if len(channels) > len(INSTALLABLE_CHANNELS):
        raise JobError(400, "too many channels")
    invalid = sorted({c for c in channels if c not in INSTALLABLE_CHANNELS})
    if invalid:
        raise JobError(400, f"unknown or non-installable channel(s): {', '.join(invalid)}")
    dry_run = _bool(params, "dry_run")
    argv = ["install", "--env=auto"]
    argv.append("--dry-run" if dry_run else "--system")
    ordered = sorted(set(channels))
    if ordered:
        argv.append("--channels=" + ",".join(ordered))
    what = ", ".join(ordered) if ordered else "core tools"
    label = f"{'Preview install' if dry_run else 'Install'}: {what}"
    return argv, label, SHORT_TIMEOUT if dry_run else INSTALL_TIMEOUT


def _build_skill(install: bool) -> Callable[[Dict[str, Any]], Tuple[List[str], str, float]]:
    def build(params: Dict[str, Any]) -> Tuple[List[str], str, float]:
        if params:
            raise JobError(400, "this action takes no parameters")
        flag = "--install" if install else "--uninstall"
        label = "Install agent skill" if install else "Remove agent skill"
        return ["skill", flag], label, SHORT_TIMEOUT

    return build


def _build_check_update(params: Dict[str, Any]) -> Tuple[List[str], str, float]:
    if params:
        raise JobError(400, "this action takes no parameters")
    return ["check-update"], "Check for updates", SHORT_TIMEOUT


def _build_browser_import(params: Dict[str, Any]) -> Tuple[List[str], str, float]:
    browser = params.get("browser")
    platform = params.get("platform")
    if browser not in BROWSER_CHOICES:
        raise JobError(400, "unsupported browser")
    if platform not in BROWSER_IMPORT_PLATFORMS:
        raise JobError(400, "unsupported platform for browser import")
    argv = ["configure", "--from-browser", str(browser), "--platform", str(platform)]
    return argv, f"Import {platform} cookies from {browser}", SHORT_TIMEOUT


ACTIONS: Dict[str, Callable[[Dict[str, Any]], Tuple[List[str], str, float]]] = {
    "install": _build_install,
    "skill_install": _build_skill(True),
    "skill_uninstall": _build_skill(False),
    "check_update": _build_check_update,
    "configure_browser": _build_browser_import,
}


def build_job(action: Any, params: Any) -> Tuple[List[str], str, float]:
    """Validate ``action``/``params`` and return (cli args, label, timeout)."""
    if not isinstance(action, str) or action not in ACTIONS:
        raise JobError(400, "unknown action")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JobError(400, "params must be an object")
    return ACTIONS[action](params)


# ── job runner ───────────────────────────────────────────────────────────────


@dataclass
class Job:
    id: str
    action: str
    label: str
    command: str
    timeout: float
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    status: str = "running"  # running | succeeded | failed | timeout | cancelled
    exit_code: Optional[int] = None
    #: Characters dropped from the front once output exceeded the cap.
    dropped: int = 0
    output: str = ""
    process: Optional[subprocess.Popen] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, text: str) -> None:
        with self.lock:
            self.output += text
            overflow = len(self.output) - MAX_OUTPUT_CHARS
            if overflow > 0:
                self.output = self.output[overflow:]
                self.dropped += overflow

    def snapshot(self, offset: int = 0) -> Dict[str, Any]:
        with self.lock:
            total = self.dropped + len(self.output)
            start = max(0, min(offset, total) - self.dropped)
            return {
                "id": self.id,
                "action": self.action,
                "label": self.label,
                "command": self.command,
                "status": self.status,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "output": self.output[start:],
                "offset": total,
            }


class JobManager:
    """Runs at most one allowlisted CLI job at a time."""

    def __init__(self, argv_factory: Callable[..., List[str]] = cli_argv):
        self._argv_factory = argv_factory
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    def running(self) -> Optional[Job]:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.status == "running":
                    return job
        return None

    def start(self, action: Any, params: Any) -> Job:
        args, label, timeout = build_job(action, params)
        argv = self._argv_factory(*args)
        with self._lock:
            if any(j.status == "running" for j in self._jobs.values()):
                raise JobError(409, "Another job is still running; wait for it to finish")
            job = Job(
                id=secrets.token_hex(8),
                action=str(action),
                label=label,
                command="agent-reach " + " ".join(args),
                timeout=timeout,
            )
            try:
                job.process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=child_env(),
                    shell=False,
                    **_popen_group_kwargs(),
                )
            except OSError as exc:
                raise JobError(500, f"Could not start job: {exc.strerror or exc}") from None
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._prune()
        threading.Thread(target=self._pump, args=(job,), daemon=True,
                         name=f"agent-reach-ui-job-{job.id}").start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = [self._jobs[i] for i in self._order]
        return [{k: v for k, v in j.snapshot(0).items() if k != "output"} for j in jobs]

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status == "running" and job.process is not None:
                _terminate(job.process)

    def _prune(self) -> None:
        finished = [i for i in self._order if self._jobs[i].status != "running"]
        for job_id in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            self._order.remove(job_id)
            del self._jobs[job_id]

    def _pump(self, job: Job) -> None:
        proc = job.process
        assert proc is not None and proc.stdout is not None
        timer = threading.Timer(job.timeout, self._expire, args=(job,))
        timer.daemon = True
        timer.start()
        try:
            decoder_buffer = b""
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                decoder_buffer += chunk
                # Keep an incomplete trailing UTF-8 sequence for the next read.
                text, decoder_buffer = _decode_prefix(decoder_buffer)
                if text:
                    job.append(scrub_url_credentials(strip_ansi(text)))
            if decoder_buffer:
                job.append(strip_ansi(decoder_buffer.decode("utf-8", errors="replace")))
            code = proc.wait()
        finally:
            timer.cancel()
        with job.lock:
            job.exit_code = code
            job.finished_at = time.time()
            if job.status == "running":
                job.status = "succeeded" if code == 0 else "failed"

    def _expire(self, job: Job) -> None:
        if job.status == "running" and job.process is not None:
            with job.lock:
                job.status = "timeout"
            job.append(f"\n[agent-reach ui] Job exceeded {job.timeout:g}s and was stopped.\n")
            _terminate(job.process)


def _decode_prefix(data: bytes) -> Tuple[str, bytes]:
    for cut in range(len(data), max(len(data) - 4, -1), -1):
        try:
            return data[:cut].decode("utf-8"), data[cut:]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), b""


def _popen_group_kwargs() -> Dict[str, Any]:
    """Start jobs in their own process group so a timeout stops child tools too."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if os.name != "nt":
            import signal

            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        pass
