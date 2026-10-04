# -*- coding: utf-8 -*-
"""XiaoHongShu — multi-backend: OpenCLI / xiaohongshu-mcp / xhs-cli.

Backend order encodes the recommendation, and probing order makes the
environment split automatic: OpenCLI needs a desktop Chrome so it simply
never probes alive on a server, where xiaohongshu-mcp (self-contained
headless browser) takes over after an explicit Cookie-Editor import.
xhs-cli (upstream unmaintained since
2026-03) keeps working for existing installs as the last candidate.
"""

import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from agent_reach.utils.paths import (
    PrivatePathError,
    read_small_text_no_follow,
)

from .base import Channel
from .mcporter import (
    McporterConfigError,
    inspect_mcporter_config,
    skipped_layers_note,
)

_MCP_ENDPOINT = "http://localhost:18060/mcp"
_MCP_INSTALL_URL = "https://github.com/xpzouying/xiaohongshu-mcp"
_XHS_COOKIE_TTL_SECONDS = 7 * 86400
_MAX_XHS_COOKIE_BYTES = 1024 * 1024


def _mcp_service_reachable(timeout: int = 3) -> bool:
    """True if the xiaohongshu-mcp HTTP service answers on localhost.

    Any HTTP response counts (the MCP endpoint replies 405 to GET) —
    we only care that the service is up. Proxies are bypassed explicitly:
    localhost must never be routed through HTTP_PROXY.
    """
    req = urllib.request.Request(_MCP_ENDPOINT, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        exc.close()
        return True  # 405/404 etc. — service is alive
    except Exception:
        return False


def format_xhs_result(data):
    """Clean XHS API response, keeping only useful fields.

    Handles both single note objects and lists of notes (search results).
    Drastically reduces token usage by stripping structural redundancy (#134).
    """
    if isinstance(data, list):
        return [_clean_note(item) for item in data]
    if isinstance(data, dict):
        # The MCP CLI emits {"feeds": [...]}; HTTP adds a "data" envelope.
        payload = data.get("data")
        if not isinstance(payload, dict):
            payload = data
        for key in ("items", "notes", "feeds"):
            if key in payload and isinstance(payload[key], list):
                return [_clean_note(item) for item in payload[key]]
        return _clean_note(payload)
    return data


# Public xiaohongshu-mcp uses camelCase; keep the formatter's existing
# snake_case output contract and support older snake_case backends as well.
_XHS_FIELD_ALIASES = {
    "noteCard": "note_card", "noteId": "note_id", "xsecToken": "xsec_token",
    "displayTitle": "title", "userId": "user_id", "nickName": "nick_name",
    "interactInfo": "interact_info", "likedCount": "liked_count",
    "collectedCount": "collected_count", "commentCount": "comment_count",
    "sharedCount": "share_count", "imageList": "image_list",
    "urlDefault": "url_default", "userInfo": "user_info",
    "likeCount": "like_count", "subCommentCount": "sub_comment_count",
}


def _normalize_xhs_fields(value):
    """Add known aliases without mutating input or replacing canonical fields."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for alias, canonical in _XHS_FIELD_ALIASES.items():
        if alias in value and canonical not in result:
            result[canonical] = value[alias]
    return result


def _clean_note(note):
    """Extract useful fields from a single XHS note/feed item."""
    if not isinstance(note, dict):
        return note

    note = _normalize_xhs_fields(note)
    # Search identity is on the feed, while content is inside noteCard.
    inner = _normalize_xhs_fields(note.get("note_card") or note.get("note") or note)
    if not isinstance(inner, dict):
        return {}

    result = {}

    # Basic info
    for key in ("id", "note_id", "xsec_token", "title", "desc", "type", "time"):
        if key in inner:
            result[key] = inner[key]
        elif key in ("id", "note_id", "xsec_token") and key in note:
            result[key] = note[key]

    # Content (may be in desc or content)
    if "content" in inner and "desc" not in result:
        result["content"] = inner["content"]

    # Author
    user = _normalize_xhs_fields(inner.get("user") or inner.get("author"))
    if isinstance(user, dict):
        result["user"] = {
            k: user[k] for k in ("nickname", "user_id", "nick_name") if k in user
        }

    # Engagement metrics
    interact = _normalize_xhs_fields(
        inner.get("interact_info") or inner.get("note_interact_info") or {}
    )
    if isinstance(interact, dict):
        for key in ("liked_count", "collected_count", "comment_count", "share_count"):
            if key in interact:
                result[key] = interact[key]
    # Also check top-level (some API formats)
    for key in ("liked_count", "collected_count", "comment_count", "share_count"):
        if key in inner and key not in result:
            result[key] = inner[key]

    # Images — just URLs
    images = inner.get("image_list") or inner.get("images_list") or []
    if not images and isinstance(inner.get("cover"), dict):
        images = [inner["cover"]]
    if isinstance(images, list):
        urls = []
        for img in images:
            img = _normalize_xhs_fields(img)
            if isinstance(img, dict):
                url = img.get("url") or img.get("url_default") or img.get("original")
                if url:
                    urls.append(url)
            elif isinstance(img, str):
                urls.append(img)
        if urls:
            result["images"] = urls

    # Tags
    tags = inner.get("tag_list") or inner.get("tags") or []
    if isinstance(tags, list):
        tag_names = []
        for t in tags:
            if isinstance(t, dict) and "name" in t:
                tag_names.append(t["name"])
            elif isinstance(t, str):
                tag_names.append(t)
        if tag_names:
            result["tags"] = tag_names

    # Comments (if present, e.g. from get_feed_detail with comments)
    comments = inner.get("comments") or note.get("comments") or []
    if isinstance(comments, dict):
        comments = comments.get("list", [])
    if isinstance(comments, list) and comments:
        result["comments"] = [_clean_comment(c) for c in comments]

    return result


def _clean_comment(comment):
    """Extract useful fields from a comment."""
    if not isinstance(comment, dict):
        return comment
    comment = _normalize_xhs_fields(comment)
    result = {}
    if "content" in comment:
        result["content"] = comment["content"]
    user = _normalize_xhs_fields(comment.get("user_info") or comment.get("user"))
    if isinstance(user, dict):
        result["user"] = user.get("nickname") or user.get("nick_name", "")
    for key in ("like_count", "sub_comment_count"):
        if key in comment:
            result[key] = comment[key]
    return result


class XiaoHongShuChannel(Channel):
    name = "xiaohongshu"
    description = "XiaoHongShu (RED) notes"
    backends = ["OpenCLI", "xiaohongshu-mcp", "xhs-cli (xiaohongshu-cli)"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "xiaohongshu.com", "xhslink.com")

    def check(self, config=None):
        """Probe candidates in order; first fully-usable backend wins.

        If none is fully usable, the first fixable candidate (warn) is
        reported, so the user gets one actionable prescription instead
        of three half-relevant ones.
        """
        self.active_backend = None
        findings = []  # (backend, status, message)

        for backend in self.ordered_backends(config):
            if backend == "OpenCLI":
                result = self._check_opencli()
            elif backend == "xiaohongshu-mcp":
                result = self._check_mcp()
            else:
                result = self._check_xhs_cli()
            if result is None:
                continue  # not installed — not a candidate right now
            findings.append((backend, *result))

        # First ok wins, else first warn; active_backend is set for both (B9).
        picked = self.pick_finding(findings)
        if picked is not None:
            return picked

        if findings:  # only broken candidates left
            return "error", "\n".join(m for _, _, m in findings)

        return "off", (
            "No XiaoHongShu backend installed. Recommended:\n"
            "  Desktop: agent-reach install --system --channels opencli\n"
            "       (reuses the Chrome login; zero config once you have browsed XiaoHongShu)\n"
            f"  Server: xiaohongshu-mcp: {_MCP_INSTALL_URL}\n"
            "       Log in only with an explicit Cookie-Editor export:\n"
            "       agent-reach configure xhs-cookies (hidden input)"
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
                "OpenCLI bridge connected, but the XiaoHongShu login and real commands "
                "were not verified live; Doctor does not run platform commands, so it "
                "is not marked available."
            )
        return "warn", st.hint

    def _check_mcp(self):
        """xiaohongshu-mcp candidate. None = service not running."""
        if not _mcp_service_reachable():
            return None
        if not shutil.which("mcporter"):
            return "warn", (
                "xiaohongshu-mcp service reachable, but mcporter is not installed, so "
                "Doctor has not connected to it. Install first: npm install -g mcporter"
            )
        try:
            inspection = inspect_mcporter_config()
        except McporterConfigError as exc:
            return "error", f"mcporter config check failed: {exc}"
        if "xiaohongshu" in inspection.server_names:
            return "warn", (
                "xiaohongshu-mcp service reachable and connected to mcporter, but Doctor "
                "has not verified the login, so notes cannot be claimed available. If "
                "not logged in, export with Cookie-Editor and run "
                "agent-reach configure xhs-cookies"
            )
        if inspection.imports_unchecked:
            return "warn", (
                "xiaohongshu-mcp service reachable; the local mcporter config has no "
                "xiaohongshu entry and editor imports were not expanded, so Doctor has "
                "not verified the connection."
            )
        note = skipped_layers_note(inspection)
        if note:
            return "warn", note
        return "warn", (
            "xiaohongshu-mcp service is running but not connected to mcporter. Run:\n"
            f"  mcporter config add xiaohongshu {_MCP_ENDPOINT} --scope home"
        )

    def _check_xhs_cli(self):
        """Inspect saved xhs-cli cookies without invoking browser extraction."""
        if not shutil.which("xhs"):
            return None
        cookie_path = Path.home() / ".xiaohongshu-cli" / "cookies.json"
        try:
            payload = read_small_text_no_follow(
                cookie_path,
                follow_symlinks=True,
                max_bytes=_MAX_XHS_COOKIE_BYTES,
            )
        except PrivatePathError as exc:
            return "warn", (
                f"xhs-cli is installed, but cookies.json cannot be read safely: {exc}."
            )
        except (OSError, UnicodeError):
            return "warn", (
                "xhs-cli is installed, but cookies.json cannot be read safely; "
                "Doctor did not run `xhs status`, which auto-extracts browser cookies."
            )
        if payload is None:
            return self._xhs_cookie_hint()
        try:
            data = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return "warn", (
                "xhs-cli is installed, but the saved cookies.json cannot be parsed "
                "safely; Doctor did not run `xhs status`, which auto-extracts browser "
                "cookies."
            )
        if not isinstance(data, dict) or not data.get("a1"):
            return self._xhs_cookie_hint()
        saved_at = data.get("saved_at")
        # saved_at <= 0 means "unknown", not stale.
        if (
            isinstance(saved_at, (int, float))
            and not isinstance(saved_at, bool)
            and saved_at > 0
            and time.time() - saved_at > _XHS_COOKIE_TTL_SECONDS
        ):
            return "warn", (
                "xhs-cli is installed, but the saved cookies are over 7 days old; Doctor "
                "will not let upstream read the browser or refresh the file automatically. "
                "Update them explicitly with Cookie-Editor."
            )
        return "warn", (
            "xhs-cli is installed and explicitly saved cookies were found; to keep "
            "upstream from reading the browser or rewriting cookies, Doctor does not run "
            "`xhs status`, so they were not verified live."
        )

    @staticmethod
    def _xhs_cookie_hint():
        return "warn", (
            "xhs-cli is installed but has no usable explicit cookies. Do not run "
            "`xhs login/status`, which reads the browser automatically; migrate to "
            "xiaohongshu-mcp, then export with Cookie-Editor and run "
            "agent-reach configure xhs-cookies."
        )
