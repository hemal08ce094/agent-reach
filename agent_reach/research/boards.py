# -*- coding: utf-8 -*-
"""Research boards and recent searches, stored under ~/.agent-reach/research/.

Each board is one JSON file written atomically with owner-only permissions
(file 0600, directories 0700). Board ids are random hex, validated before any
path is built, so a request can never name a file outside the boards folder.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_reach.research.models import ResearchError, ResearchItem, clean_block, clean_line
from agent_reach.utils.paths import (
    PrivatePathError,
    atomic_write_private_text,
    home_dir,
    make_private_dir,
    read_small_text_no_follow,
)

MAX_BOARDS = 50
MAX_ITEMS = 200
MAX_NAME_CHARS = 80
MAX_NOTE_CHARS = 4000
MAX_EXCERPT_CHARS = 20_000
MAX_QUESTIONS = 30
MAX_HISTORY = 20
_MAX_FILE_BYTES = 8 * 1024 * 1024
_BOARD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_ITEM_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def research_dir() -> Path:
    return home_dir() / ".agent-reach" / "research"


def _now() -> float:
    return round(time.time(), 3)


def check_board_id(board_id: Any) -> str:
    if not isinstance(board_id, str) or not _BOARD_ID_RE.match(board_id):
        raise ResearchError(404, "Unknown board")
    return board_id


def check_item_id(item_id: Any) -> str:
    if not isinstance(item_id, str) or not _ITEM_ID_RE.match(item_id):
        raise ResearchError(404, "Unknown item")
    return item_id


def _clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ResearchError(400, "name must be a string")
    name = clean_line(raw, MAX_NAME_CHARS + 1)
    if not name:
        raise ResearchError(400, "Give the board a name")
    if len(name) > MAX_NAME_CHARS:
        raise ResearchError(400, f"Keep the name under {MAX_NAME_CHARS} characters")
    return name


def _optional_text(raw: Any, limit: int, label: str) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ResearchError(400, f"{label} must be a string")
    if len(raw) > limit:
        raise ResearchError(400, f"{label} is longer than {limit} characters")
    return clean_block(raw, limit).strip()


class BoardStore:
    """Thread-safe CRUD over board files."""

    def __init__(self, root: Optional[Path] = None):
        self._root = root
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root or research_dir()

    @property
    def boards_dir(self) -> Path:
        return self.root / "boards"

    def _ensure_dirs(self) -> None:
        make_private_dir(self.root.parent)
        make_private_dir(self.root)
        make_private_dir(self.boards_dir)

    def _path(self, board_id: str) -> Path:
        return self.boards_dir / f"{check_board_id(board_id)}.json"

    def _load(self, board_id: str) -> Dict[str, Any]:
        try:
            raw = read_small_text_no_follow(self._path(board_id), max_bytes=_MAX_FILE_BYTES)
        except PrivatePathError:
            raise ResearchError(500, "The board file cannot be read safely") from None
        if raw is None:
            raise ResearchError(404, "Unknown board")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ResearchError(500, "The board file is not valid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ResearchError(500, "The board file has an unexpected shape")
        data["id"] = board_id
        return data

    def _save(self, board: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_dirs()
        board["updated_at"] = _now()
        atomic_write_private_text(
            self._path(board["id"]), json.dumps(board, ensure_ascii=False, indent=2)
        )
        return board

    # ── boards ──

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.boards_dir.is_dir():
                return []
            summaries = []
            for path in self.boards_dir.glob("*.json"):
                board_id = path.stem
                if not _BOARD_ID_RE.match(board_id) or path.is_symlink():
                    continue
                try:
                    board = self._load(board_id)
                except ResearchError:
                    continue
                summaries.append(
                    {
                        "id": board_id,
                        "name": str(board.get("name") or "Untitled board"),
                        "count": len(board["items"]),
                        "updated_at": board.get("updated_at"),
                    }
                )
            summaries.sort(key=lambda b: b.get("updated_at") or 0, reverse=True)
            return summaries

    def create(self, name: Any = None) -> Dict[str, Any]:
        with self._lock:
            if len(self.list()) >= MAX_BOARDS:
                raise ResearchError(409, f"You already have {MAX_BOARDS} boards; delete one first")
            board = {
                "id": secrets.token_hex(8),
                "name": _clean_name(name if name is not None else "Research board"),
                "created_at": _now(),
                "questions": [],
                "items": [],
            }
            return self._save(board)

    def get(self, board_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._load(board_id)

    def update(self, board_id: str, *, name: Any = None, order: Any = None) -> Dict[str, Any]:
        with self._lock:
            board = self._load(board_id)
            if name is not None:
                board["name"] = _clean_name(name)
            if order is not None:
                if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
                    raise ResearchError(400, "order must be a list of item ids")
                by_id = {entry["item"]["id"]: entry for entry in board["items"]}
                if sorted(order) != sorted(by_id):
                    raise ResearchError(400, "order must list every item on the board exactly once")
                board["items"] = [by_id[i] for i in order]
            return self._save(board)

    def delete(self, board_id: str) -> None:
        with self._lock:
            path = self._path(board_id)
            if not path.exists():
                raise ResearchError(404, "Unknown board")
            path.unlink()

    # ── items ──

    def pin(
        self,
        board_id: str,
        item: ResearchItem,
        *,
        excerpt: Any = None,
        note: Any = None,
        question: Any = None,
    ) -> Dict[str, Any]:
        with self._lock:
            board = self._load(board_id)
            clean_excerpt = _optional_text(excerpt, MAX_EXCERPT_CHARS, "excerpt")
            clean_note = _optional_text(note, MAX_NOTE_CHARS, "note")
            existing = next((e for e in board["items"] if e["item"]["id"] == item.id), None)
            if existing is not None:
                if clean_excerpt:
                    existing["excerpt"] = clean_excerpt
                if clean_note:
                    existing["note"] = clean_note
            else:
                if len(board["items"]) >= MAX_ITEMS:
                    raise ResearchError(409, f"A board holds up to {MAX_ITEMS} items")
                board["items"].append(
                    {
                        "item": item.to_dict(),
                        "excerpt": clean_excerpt,
                        "note": clean_note,
                        "pinned_at": _now(),
                    }
                )
            if isinstance(question, str):
                q = clean_line(question, 300)
                questions = board.setdefault("questions", [])
                if q and q not in questions and len(questions) < MAX_QUESTIONS:
                    questions.append(q)
            return self._save(board)

    def unpin(self, board_id: str, item_id: str) -> Dict[str, Any]:
        with self._lock:
            check_item_id(item_id)
            board = self._load(board_id)
            before = len(board["items"])
            board["items"] = [e for e in board["items"] if e["item"]["id"] != item_id]
            if len(board["items"]) == before:
                raise ResearchError(404, "That item is not on this board")
            return self._save(board)

    def update_item(self, board_id: str, item_id: str, *, note: Any = None, excerpt: Any = None) -> Dict[str, Any]:
        with self._lock:
            check_item_id(item_id)
            board = self._load(board_id)
            entry = next((e for e in board["items"] if e["item"]["id"] == item_id), None)
            if entry is None:
                raise ResearchError(404, "That item is not on this board")
            if note is not None:
                entry["note"] = _optional_text(note, MAX_NOTE_CHARS, "note")
            if excerpt is not None:
                entry["excerpt"] = _optional_text(excerpt, MAX_EXCERPT_CHARS, "excerpt")
            return self._save(board)

    # ── recent searches ──

    @property
    def history_path(self) -> Path:
        return self.root / "history.json"

    def history(self) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                raw = read_small_text_no_follow(self.history_path, max_bytes=256 * 1024)
                data = json.loads(raw) if raw else []
            except (PrivatePathError, ValueError, OSError):
                return []
            if not isinstance(data, list):
                return []
            return [
                {"query": str(h["query"]), "at": h.get("at")}
                for h in data[:MAX_HISTORY]
                if isinstance(h, dict) and isinstance(h.get("query"), str)
            ]

    def remember(self, query: str) -> None:
        with self._lock:
            entries = [h for h in self.history() if h["query"].casefold() != query.casefold()]
            entries.insert(0, {"query": query, "at": _now()})
            self._ensure_dirs()
            atomic_write_private_text(
                self.history_path, json.dumps(entries[:MAX_HISTORY], ensure_ascii=False)
            )

    def clear_history(self) -> None:
        with self._lock:
            if self.history_path.exists():
                self.history_path.unlink()
