# -*- coding: utf-8 -*-
"""Concurrent search runs and reads, polled by id.

A run starts one daemon thread per selected source. Each source's state moves
queued -> running -> done | error | timeout | cancelled, or is ``skipped``
up front when doctor does not report its channel as usable. The browser polls
the run and renders each source as soon as it finishes.
"""

from __future__ import annotations

import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from agent_reach.probe import _kill_process_tree
from agent_reach.research.models import (
    ReadResult,
    ResearchError,
    ResearchItem,
    public_url,
    validate_query,
)
from agent_reach.research.runner import (
    DEFAULT_TIMEOUT,
    READ_TIMEOUT,
    Runner,
    SourceFailure,
    SourceTimeout,
)
from agent_reach.research.sources import ALL_SOURCES, Context, Source, get_source

MAX_LIMIT = 25
DEFAULT_LIMIT = 10
MAX_CONCURRENT_RUNS = 2
MAX_CONCURRENT_READS = 3
_KEEP_FINISHED = 30
_WATCHDOG_GRACE = 5.0
_TERMINAL = ("done", "error", "timeout", "skipped", "cancelled")
_USABLE = ("ok", "warn")


@dataclass
class SourceState:
    name: str
    label: str
    channel: str
    searchable: bool
    default_selected: bool
    hint: str
    status: str = "unknown"
    backend: Optional[str] = None
    available: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "channel": self.channel,
            "searchable": self.searchable,
            "default_selected": self.default_selected,
            "hint": self.hint,
            "status": self.status,
            "backend": self.backend,
            "available": self.available,
            "reason": self.reason,
        }


def _summary(message: Any) -> str:
    lines = [ln.strip() for ln in str(message or "").splitlines() if ln.strip()]
    text = lines[0] if lines else "Not set up"
    return text if len(text) <= 180 else text[:179].rstrip() + "…"


def source_states(doctor_results: Optional[Dict[str, dict]], config: Any = None) -> Dict[str, SourceState]:
    """Decide, from doctor's snapshot, which adapters may run and why the rest may not."""
    states: Dict[str, SourceState] = {}
    for source in ALL_SOURCES:
        state = SourceState(
            name=source.name,
            label=source.label,
            channel=source.channel,
            searchable=source.searchable,
            default_selected=source.default_selected,
            hint=source.hint,
        )
        result = (doctor_results or {}).get(source.channel) if doctor_results is not None else None
        if doctor_results is None:
            state.reason = "Waiting for doctor to check this channel"
        elif not isinstance(result, dict):
            state.reason = "Doctor has no result for this channel yet"
        else:
            state.status = str(result.get("status") or "unknown")
            backend = result.get("active_backend")
            state.backend = backend if isinstance(backend, str) else None
            if state.status not in _USABLE or not state.backend:
                state.reason = _summary(result.get("message"))
            elif state.backend not in source.backends:
                state.reason = (
                    f"The workspace runs {source.label} through {' or '.join(source.backends)}; "
                    f"doctor reports {state.backend} as active. Your agent can still use it."
                )
            else:
                blocked = source.blocked_reason(state.backend, config)
                if blocked:
                    state.reason = blocked
                else:
                    state.available = True
        states[source.name] = state
    return states


# ── runs ─────────────────────────────────────────────────────────────────────


@dataclass
class SourceRun:
    name: str
    label: str
    channel: str
    backend: Optional[str]
    status: str = "queued"
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    commands: List[str] = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    procs: List[subprocess.Popen] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "channel": self.channel,
            "backend": self.backend,
            "status": self.status,
            "items": list(self.items),
            "error": self.error,
            "commands": list(self.commands),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Run:
    id: str
    query: str
    limit: int
    created_at: float
    sources: Dict[str, SourceRun]
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def finished(self) -> bool:
        return all(s.status in _TERMINAL for s in self.sources.values())

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "query": self.query,
                "limit": self.limit,
                "created_at": self.created_at,
                "finished": self.finished,
                "order": list(self.sources),
                "sources": {k: s.snapshot() for k, s in self.sources.items()},
            }


@dataclass
class ReadTask:
    id: str
    url: str
    source: str
    label: str
    backend: Optional[str]
    item: Optional[Dict[str, Any]] = None
    status: str = "running"
    result: Optional[ReadResult] = None
    error: str = ""
    note: str = ""
    commands: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    procs: List[subprocess.Popen] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "url": self.url,
                "source": self.source,
                "label": self.label,
                "backend": self.backend,
                "item": self.item,
                "status": self.status,
                "error": self.error,
                "note": self.note,
                "commands": list(self.commands),
                "result": self.result.to_dict() if self.result else None,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


def _validate_limit(raw: Any) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_LIMIT:
        raise ResearchError(400, f"limit must be a whole number from 1 to {MAX_LIMIT}")
    return raw


def _validate_sources(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        raise ResearchError(400, "Pick at least one source")
    if len(raw) > len(ALL_SOURCES) or not all(isinstance(n, str) for n in raw):
        raise ResearchError(400, "sources must be a list of source names")
    names: List[str] = []
    for name in raw:
        source = get_source(name)
        if source is None or not source.searchable:
            raise ResearchError(400, f"Unknown source: {name[:40]}")
        if name not in names:
            names.append(name)
    return names


class ResearchManager:
    """Owns live runs and reads. Thread-safe; every worker is a daemon thread."""

    def __init__(
        self,
        states: Callable[[], Dict[str, SourceState]],
        config: Callable[[], Any] = lambda: None,
        *,
        runner: Optional[Runner] = None,
        timeout: float = DEFAULT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        max_runs: int = MAX_CONCURRENT_RUNS,
        max_reads: int = MAX_CONCURRENT_READS,
    ):
        self._states = states
        self._config = config
        self.runner = runner or Runner()
        self.timeout = timeout
        self.read_timeout = read_timeout
        self.max_runs = max_runs
        self.max_reads = max_reads
        self._runs: Dict[str, Run] = {}
        self._reads: Dict[str, ReadTask] = {}
        self._lock = threading.Lock()

    # ── runs ──

    def start_run(self, query: Any, sources: Any, limit: Any = None) -> Run:
        clean_query = validate_query(query)
        names = _validate_sources(sources)
        clean_limit = _validate_limit(limit)
        with self._lock:
            active = sum(1 for r in self._runs.values() if not r.finished)
            if active >= self.max_runs:
                raise ResearchError(
                    429, f"{active} searches are already running. Wait for one to finish or stop it."
                )
        states = self._states()
        config = self._config()
        run = Run(
            id=secrets.token_hex(8),
            query=clean_query,
            limit=clean_limit,
            created_at=time.time(),
            sources={},
        )
        for name in names:
            source = get_source(name)
            assert source is not None
            state = states.get(name)
            sr = SourceRun(name, source.label, source.channel, state.backend if state else None)
            if state is None or not state.available:
                sr.status = "skipped"
                sr.error = state.reason if state else "Not set up"
            else:
                preview = source.command_preview(clean_query, clean_limit, str(state.backend))
                sr.commands = [preview] if preview else []
            run.sources[name] = sr
        with self._lock:
            self._runs[run.id] = run
            self._prune(self._runs)
        for name, sr in run.sources.items():
            if sr.status == "queued":
                source = get_source(name)
                assert source is not None
                self._spawn(run, sr, source, config)
        return run

    def _spawn(self, run: Run, sr: SourceRun, source: Source, config: Any) -> None:
        def register(proc: subprocess.Popen) -> None:
            with run.lock:
                sr.procs.append(proc)
                cancelled = sr.status in _TERMINAL
            if cancelled:
                _kill_process_tree(proc)

        ctx = Context(self.runner, sr.backend, config, self.timeout, register)

        def work() -> None:
            with run.lock:
                if sr.status != "queued":
                    return
                sr.status = "running"
                sr.started_at = time.time()
            status, error, items = "done", "", []
            try:
                seen = set()
                for item in source.search(run.query, run.limit, ctx):
                    if item.id not in seen:
                        seen.add(item.id)
                        items.append(item.to_dict())
            except SourceTimeout as exc:
                status, error = "timeout", exc.message
            except SourceFailure as exc:
                status, error = "error", exc.message
            except Exception as exc:  # noqa: BLE001 — one source must never sink the run
                logger.exception(f"research source {source.name} failed")
                status, error = "error", f"Unexpected error in the {source.label} adapter ({type(exc).__name__})"
            with run.lock:
                if sr.status != "running":
                    return  # already timed out or cancelled
                sr.status, sr.error, sr.items = status, error, items
                if ctx.commands:
                    sr.commands = list(ctx.commands)
                sr.finished_at = time.time()

        def expire() -> None:
            with run.lock:
                if sr.status not in ("queued", "running"):
                    return
                sr.status = "timeout"
                sr.error = f"{source.label} did not answer within {self.timeout:g}s"
                sr.finished_at = time.time()
                procs = list(sr.procs)
            for proc in procs:
                _kill_process_tree(proc)

        watchdog = threading.Timer(self.timeout + _WATCHDOG_GRACE, expire)
        watchdog.daemon = True
        watchdog.start()
        threading.Thread(target=work, daemon=True, name=f"agent-reach-research-{source.name}").start()

    def get_run(self, run_id: str) -> Optional[Run]:
        with self._lock:
            return self._runs.get(run_id)

    def cancel_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run is None:
            raise ResearchError(404, "Unknown search")
        procs: List[subprocess.Popen] = []
        with run.lock:
            for sr in run.sources.values():
                if sr.status in ("queued", "running"):
                    sr.status = "cancelled"
                    sr.error = "Stopped"
                    sr.finished_at = time.time()
                    procs.extend(sr.procs)
        for proc in procs:
            _kill_process_tree(proc)
        return run

    # ── reads ──

    def route(self, url: str, states: Dict[str, SourceState]) -> tuple:
        """(source, note) for ``url``: the platform adapter if usable, else Jina Reader."""
        note = ""
        for source in ALL_SOURCES:
            if source.name == "webpage" or not source.can_read(url):
                continue
            state = states.get(source.name)
            if state is not None and state.available:
                return source, ""
            note = f"{source.label} is not set up here, so the page was read through Jina Reader."
            break
        web = get_source("webpage")
        assert web is not None
        return web, note

    def start_read(self, url: Any = None, item: Any = None) -> ReadTask:
        item_dict: Optional[Dict[str, Any]] = None
        if item is not None:
            known = {s.name for s in ALL_SOURCES}
            parsed_item = ResearchItem.from_dict(item, known)
            item_dict = parsed_item.to_dict()
            target = parsed_item.url
        else:
            checked = public_url(url) if isinstance(url, str) else None
            if checked is None:
                raise ResearchError(400, "Paste a public http(s) link to read")
            target = checked
        with self._lock:
            active = sum(1 for r in self._reads.values() if r.status == "running")
            if active >= self.max_reads:
                raise ResearchError(429, f"{active} pages are already loading. Wait for one to finish.")
        states = self._states()
        source, note = self.route(target, states)
        state = states.get(source.name)
        task = ReadTask(
            id=secrets.token_hex(8),
            url=target,
            source=source.name,
            label=source.label,
            backend=state.backend if state else None,
            item=item_dict,
            note=note,
        )
        if state is None or not state.available:
            task.status = "error"
            task.error = (
                f"No reader is available for this link. {state.reason if state else ''}".strip()
            )
            task.finished_at = time.time()
        with self._lock:
            self._reads[task.id] = task
            self._prune_reads()
        if task.status == "running":
            self._spawn_read(task, source)
        return task

    def _spawn_read(self, task: ReadTask, source: Source) -> None:
        def register(proc: subprocess.Popen) -> None:
            with task.lock:
                task.procs.append(proc)

        ctx = Context(self.runner, task.backend, self._config(), self.read_timeout, register)

        def work() -> None:
            status, error, result = "done", "", None
            try:
                result = source.read(task.url, ctx)
                if not result.text.strip():
                    status, error, result = "error", "The page came back empty.", None
            except SourceTimeout as exc:
                status, error = "timeout", exc.message
            except SourceFailure as exc:
                status, error = "error", exc.message
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"research read via {source.name} failed")
                status, error = "error", f"Unexpected error in the {source.label} reader ({type(exc).__name__})"
            with task.lock:
                if task.status != "running":
                    return
                task.status, task.error, task.result = status, error, result
                task.commands = list(ctx.commands)
                task.finished_at = time.time()

        def expire() -> None:
            with task.lock:
                if task.status != "running":
                    return
                task.status = "timeout"
                task.error = f"Reading took longer than {self.read_timeout:g}s and was stopped"
                task.finished_at = time.time()
                procs = list(task.procs)
            for proc in procs:
                _kill_process_tree(proc)

        watchdog = threading.Timer(self.read_timeout + _WATCHDOG_GRACE, expire)
        watchdog.daemon = True
        watchdog.start()
        threading.Thread(target=work, daemon=True, name=f"agent-reach-read-{source.name}").start()

    def get_read(self, read_id: str) -> Optional[ReadTask]:
        with self._lock:
            return self._reads.get(read_id)

    # ── housekeeping ──

    def _prune(self, runs: Dict[str, Run]) -> None:
        finished = [rid for rid, r in runs.items() if r.finished]
        for rid in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
            del runs[rid]

    def _prune_reads(self) -> None:
        finished = [rid for rid, r in self._reads.items() if r.status != "running"]
        for rid in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
            del self._reads[rid]

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
            reads = list(self._reads.values())
        for run in runs:
            if not run.finished:
                self.cancel_run(run.id)
        for task in reads:
            with task.lock:
                procs = list(task.procs) if task.status == "running" else []
                if procs:
                    task.status = "cancelled"
            for proc in procs:
                _kill_process_tree(proc)
