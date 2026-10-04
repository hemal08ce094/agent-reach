# -*- coding: utf-8 -*-
"""``/api/research/*`` routes for the dashboard.

These run behind the same token, Host, Origin and CSP checks as every other
dashboard route (see ``server.py``). Handlers validate their inputs here and
leave the work to :mod:`agent_reach.research`.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Tuple

from agent_reach.research import export
from agent_reach.research.boards import check_board_id, check_item_id
from agent_reach.research.models import ResearchError, ResearchItem
from agent_reach.research.sources import ALL_SOURCES

if TYPE_CHECKING:
    from agent_reach.ui.server import DashboardHandler

PREFIX = "/api/research/"
_ID = r"[0-9a-f]{16}"

Query = Dict[str, List[str]]


def _board_payload(board: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": board["id"],
        "name": board.get("name"),
        "questions": board.get("questions") or [],
        "items": board.get("items") or [],
        "created_at": board.get("created_at"),
        "updated_at": board.get("updated_at"),
    }


class ResearchRoutes:
    """Dispatches one research request for a handler instance."""

    def __init__(self, handler: "DashboardHandler"):
        self.h = handler
        self.state = handler.state

    # (method, regex) -> callable(match groups, query); POST/PATCH get the JSON body.
    def _table(self) -> List[Tuple[str, "re.Pattern[str]", Callable[..., None]]]:
        rows: List[Tuple[str, str, Callable[..., None]]] = [
            ("GET", r"sources", self.get_sources),
            ("GET", r"history", self.get_history),
            ("DELETE", r"history", self.delete_history),
            ("POST", r"search", self.post_search),
            ("GET", rf"runs/({_ID})", self.get_run),
            ("POST", rf"runs/({_ID})/cancel", self.post_cancel),
            ("POST", r"read", self.post_read),
            ("GET", rf"reads/({_ID})", self.get_read),
            ("GET", r"boards", self.get_boards),
            ("POST", r"boards", self.post_board),
            ("GET", rf"boards/({_ID})", self.get_board),
            ("PATCH", rf"boards/({_ID})", self.patch_board),
            ("DELETE", rf"boards/({_ID})", self.delete_board),
            ("POST", rf"boards/({_ID})/items", self.post_item),
            ("PATCH", rf"boards/({_ID})/items/({_ID})", self.patch_item),
            ("DELETE", rf"boards/({_ID})/items/({_ID})", self.delete_item),
            ("GET", rf"boards/({_ID})/export", self.get_export),
        ]
        return [(m, re.compile(rf"^{p}$"), fn) for m, p, fn in rows]

    def dispatch(self, method: str, path: str, query: Query) -> None:
        from agent_reach.ui.server import UIError

        tail = path[len(PREFIX):]
        method = "GET" if method == "HEAD" else method
        path_known = False
        for row_method, pattern, fn in self._table():
            match = pattern.match(tail)
            if not match:
                continue
            path_known = True
            if row_method != method:
                continue
            args: List[Any] = list(match.groups())
            if method in ("POST", "PATCH"):
                args.append(self.h._read_json())
            if fn in (self.get_export,):
                args.append(query)
            fn(*args)
            return
        if path_known:
            raise UIError(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
        raise UIError(HTTPStatus.NOT_FOUND, "Not found")

    # ── sources, history ──

    def get_sources(self) -> None:
        states = self.state.research_states()
        doctor = self.state.last_doctor or {}
        self.h._json(
            HTTPStatus.OK,
            {
                "checked_at": doctor.get("checked_at"),
                "sources": [states[s.name].to_dict() for s in ALL_SOURCES],
            },
        )

    def get_history(self) -> None:
        self.h._json(HTTPStatus.OK, {"history": self.state.boards.history()})

    def delete_history(self) -> None:
        self.state.boards.clear_history()
        self.h._json(HTTPStatus.OK, {"history": []})

    # ── search runs ──

    def post_search(self, body: Dict[str, Any]) -> None:
        _only(body, "query", "sources", "limit")
        run = self.state.research.start_run(body.get("query"), body.get("sources"), body.get("limit"))
        try:
            self.state.boards.remember(run.query)
        except Exception:  # noqa: BLE001 — history is a convenience; never fail the search
            pass
        self.h._json(HTTPStatus.ACCEPTED, run.snapshot())

    def get_run(self, run_id: str) -> None:
        run = self.state.research.get_run(run_id)
        if run is None:
            raise ResearchError(404, "Unknown search")
        self.h._json(HTTPStatus.OK, run.snapshot())

    def post_cancel(self, run_id: str, body: Dict[str, Any]) -> None:
        _only(body)
        self.h._json(HTTPStatus.OK, self.state.research.cancel_run(run_id).snapshot())

    # ── reads ──

    def post_read(self, body: Dict[str, Any]) -> None:
        _only(body, "url", "item")
        if ("url" in body) == ("item" in body):
            raise ResearchError(400, "Send either url or item")
        task = self.state.research.start_read(url=body.get("url"), item=body.get("item"))
        self.h._json(HTTPStatus.ACCEPTED, task.snapshot())

    def get_read(self, read_id: str) -> None:
        task = self.state.research.get_read(read_id)
        if task is None:
            raise ResearchError(404, "Unknown read")
        self.h._json(HTTPStatus.OK, task.snapshot())

    # ── boards ──

    def get_boards(self) -> None:
        self.h._json(HTTPStatus.OK, {"boards": self.state.boards.list()})

    def post_board(self, body: Dict[str, Any]) -> None:
        _only(body, "name")
        board = self.state.boards.create(body.get("name"))
        self.h._json(HTTPStatus.CREATED, _board_payload(board))

    def get_board(self, board_id: str) -> None:
        self.h._json(HTTPStatus.OK, _board_payload(self.state.boards.get(check_board_id(board_id))))

    def patch_board(self, board_id: str, body: Dict[str, Any]) -> None:
        _only(body, "name", "order")
        board = self.state.boards.update(
            check_board_id(board_id), name=body.get("name"), order=body.get("order")
        )
        self.h._json(HTTPStatus.OK, _board_payload(board))

    def delete_board(self, board_id: str) -> None:
        self.state.boards.delete(check_board_id(board_id))
        self.h._json(HTTPStatus.OK, {"deleted": board_id})

    def post_item(self, board_id: str, body: Dict[str, Any]) -> None:
        _only(body, "item", "excerpt", "note", "question")
        known = {s.name for s in ALL_SOURCES}
        item = ResearchItem.from_dict(body.get("item"), known)
        board = self.state.boards.pin(
            check_board_id(board_id),
            item,
            excerpt=body.get("excerpt"),
            note=body.get("note"),
            question=body.get("question"),
        )
        self.h._json(HTTPStatus.OK, _board_payload(board))

    def patch_item(self, board_id: str, item_id: str, body: Dict[str, Any]) -> None:
        _only(body, "note", "excerpt")
        board = self.state.boards.update_item(
            check_board_id(board_id), check_item_id(item_id),
            note=body.get("note"), excerpt=body.get("excerpt"),
        )
        self.h._json(HTTPStatus.OK, _board_payload(board))

    def delete_item(self, board_id: str, item_id: str) -> None:
        board = self.state.boards.unpin(check_board_id(board_id), check_item_id(item_id))
        self.h._json(HTTPStatus.OK, _board_payload(board))

    def get_export(self, board_id: str, query: Query) -> None:
        fmt = (query.get("format") or ["md"])[0]
        if fmt not in ("md", "json"):
            raise ResearchError(400, "format must be md or json")
        board = self.state.boards.get(check_board_id(board_id))
        backends = self.state.research_backends()
        if fmt == "md":
            content = export.to_markdown(board, backends)
            mime = "text/markdown"
        else:
            content = export.to_json(board, backends)
            mime = "application/json"
        self.h._json(
            HTTPStatus.OK,
            {"filename": export.filename(board, fmt), "mime": mime, "content": content},
        )


def _only(body: Dict[str, Any], *allowed: str) -> None:
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise ResearchError(400, f"Unexpected field(s): {', '.join(k[:30] for k in unknown[:5])}")
