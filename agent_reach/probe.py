# -*- coding: utf-8 -*-
"""Lightweight upstream command probing.

Distinguishes the three failure modes that look identical to shutil.which():
  - missing: command not on PATH
  - broken: command exists but cannot execute — most commonly a stale venv
    shebang after a system Python upgrade (pipx/uv tool installs break this
    way: which() finds the shim, but exec fails with FileNotFoundError
    pointing at the shim itself)
  - timeout/error: command runs but misbehaves

Channels use probe_command() inside check() so doctor reports real health,
not just file existence.
"""

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from agent_reach.utils.process import utf8_subprocess_env

#: Exit codes shells use for "found but not executable" / "not found".
_BROKEN_EXIT_CODES = (126, 127)

#: Seconds to wait for pipes to drain after the probe's process tree was killed.
_POST_KILL_DRAIN_SECONDS = 2

#: The genuine stdlib runner. When something has replaced ``subprocess.run``
#: (test doubles, instrumentation), probes delegate to the replacement instead
#: of spawning real processes.
_STDLIB_SUBPROCESS_RUN = subprocess.run


@dataclass
class ProbeResult:
    status: str  # "ok" | "missing" | "broken" | "timeout" | "error"
    output: str = ""
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def reinstall_hint(package: str) -> str:
    """Prescription for a broken (stale-venv) CLI install."""
    return (
        "The command exists but cannot run, usually because a system Python upgrade "
        "removed the venv interpreter. Reinstalling fixes it:\n"
        f"  uv tool install --force {package}\n"
        f"or: pipx reinstall {package}"
    )


def probe_command(
    cmd: str,
    args: Sequence[str] = ("--version",),
    timeout: int = 10,
    retries: int = 0,
    package: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    remove_env: Sequence[str] = (),
) -> ProbeResult:
    """Actually execute `cmd *args` and classify the result.

    Intended for SIDE-EFFECT-FREE health probes only (version/status
    commands): retries re-run the command verbatim with no backoff, so a
    non-idempotent command would repeat its effect.

    package: pip/pipx package name used in the broken-install hint
             (defaults to cmd).
    env: values added only to the probed child process.
    remove_env: inherited variables removed only from the child process.
    """
    path = shutil.which(cmd)
    if not path:
        return ProbeResult("missing")

    last: Optional[ProbeResult] = None
    for _ in range(retries + 1):
        last = _run_once(path, args, timeout, package or cmd, env, remove_env)
        if last.ok:
            return last
        # missing/broken won't heal between retries — only transient
        # failures (timeout/error) are worth a second attempt
        if last.status in ("missing", "broken"):
            return last
    assert last is not None  # retries + 1 always executes at least once
    return last


def _run_once(
    path: str,
    args: Sequence[str],
    timeout: int,
    package: str,
    env: Optional[Mapping[str, str]] = None,
    remove_env: Sequence[str] = (),
) -> ProbeResult:
    try:
        subprocess_env = utf8_subprocess_env()
        for key in remove_env:
            subprocess_env.pop(key, None)
        if env:
            subprocess_env.update(env)
        r = _run_bounded([path, *args], timeout=timeout, env=subprocess_env)
    except FileNotFoundError:
        # which() found it but exec failed: the shebang interpreter is gone
        return ProbeResult("broken", hint=reinstall_hint(package))
    except OSError:
        return ProbeResult("broken", hint=reinstall_hint(package))
    except subprocess.TimeoutExpired:
        return ProbeResult("timeout", hint=f"`{path}` timed out (>{timeout}s)")

    if r.returncode in _BROKEN_EXIT_CODES:
        return ProbeResult("broken", hint=reinstall_hint(package))

    output = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return ProbeResult("error", output=output.strip())
    return ProbeResult("ok", output=output.strip())


def _kill_process_tree(process: "subprocess.Popen[str]") -> None:
    """Kill the probe and every descendant it started.

    npm ``.cmd`` shims and Python launchers run the real CLI as a child.
    Killing only the wrapper leaves that child holding the output pipes, so
    ``communicate()`` would block long past the timeout.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            # The probe runs in its own session, so its group id is its pid
            # and the group contains only this probe's descendants.
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _run_bounded(
    command: Sequence[str],
    *,
    timeout: int,
    env: Mapping[str, str],
) -> "subprocess.CompletedProcess[str]":
    """Run a probe with a hard wall-clock bound over its whole process tree.

    Behaves like ``subprocess.run(capture_output=True, timeout=...)`` but the
    child gets its own session (POSIX) or process group (Windows), and on a
    timeout or interrupt the entire tree is killed before the pipes are
    drained with a short, bounded wait.
    """
    if subprocess.run is not _STDLIB_SUBPROCESS_RUN:
        return subprocess.run(
            list(command),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(env),
        )

    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=dict(env),
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        _kill_process_tree(process)
        try:
            process.communicate(timeout=_POST_KILL_DRAIN_SECONDS)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            # A descendant escaped the group (e.g. it called setsid) and still
            # holds the pipes. Abandon them rather than block the doctor.
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=_POST_KILL_DRAIN_SECONDS)
            except (subprocess.TimeoutExpired, OSError):
                pass
        raise
    return subprocess.CompletedProcess(
        list(command), process.returncode, stdout, stderr
    )
