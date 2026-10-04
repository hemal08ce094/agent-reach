# -*- coding: utf-8 -*-
"""Boss Zhipin (zhipin.com) — search jobs and fetch JDs via boss-agent-cli + real Chrome over CDP.

The backend is boss-agent-cli, which reuses an already logged-in real Chrome through
its CDP debugging port. Headless is off-limits (it triggers code 36 risk control), so
check() only runs four read-only probes: it never instantiates BossClient or launches
a browser.

Fetching goes through the public boss-agent-cli API (search_jobs + job_card_browser +
browser_source="existing-browser"); see skill/references/career.md for usage. check()
only checks "is it installed + is the CDP link ready + does the browser hold a login
cookie"; it never searches.

There are two login stores, and the health check must tell them apart (learned the
hard way). Both are required, but they authenticate different channels:

- `~/.boss-agent/auth/session.enc` (`boss status` / `status --live` only check this)
  1. It is a hard gate: `_get_browser()` calls `get_token()` unconditionally and raises
     `AuthRequired` if it cannot be read, so do not delete it, or CDP search fails
     before it even connects to the browser;
  2. but it is **not the search credential**: once CDP connects to the real Chrome it
     reuses `contexts[0]`, and its cookies are only injected on the "no context at
     all" branch, which never actually takes effect;
  3. the httpx channel (low-risk ops: status/detail/cities/`job_card_httpx`) really
     does use its cookies + stoken; code 37's `force_refresh()` also writes it back.
- The browser cookies inside the dedicated Chrome profile: the credentials that
  high-risk ops such as search/greet actually carry in CDP mode.

So a valid session.enc + a logged-out browser = `boss status` reports logged in while
search reports `AUTH_EXPIRED`. Layer 4 asks the CDP browser itself
(Storage.getCookies) and treats the browser as the source of truth.
"""

import base64
import hashlib
import ipaddress
import json
import os
import platform
import socket
import struct
import urllib.request
from urllib.parse import urlparse

from agent_reach.probe import probe_command
from agent_reach.utils.url import domain_matches, host_matches

from .base import Channel

_CDP_URL = "http://localhost:9222"
_CDP_TIMEOUT = 5
# Upper bound for one reassembled WebSocket message (Storage.getCookies of a
# busy profile is well under this).
_WS_MAX_MESSAGE_BYTES = 16 * 1024 * 1024


def _chrome_launch_command(system: str | None = None) -> str:
    """Return a dedicated-profile Chrome command for the current OS."""
    system = system or platform.system()
    common = (
        "--remote-debugging-address=127.0.0.1 "
        "--remote-debugging-port=9222 "
    )
    url = '"https://www.zhipin.com/web/geek/job"'
    if system == "Darwin":
        return (
            'open -na "Google Chrome" --args '
            + common
            + '--user-data-dir="$HOME/.boss-chrome-profile" '
            + url
        )
    if system == "Windows":
        return (
            "Start-Process chrome.exe -ArgumentList "
            "'--remote-debugging-address=127.0.0.1',"
            "'--remote-debugging-port=9222',"
            # Start-Process joins ArgumentList items without the outer quotes,
            # so the path keeps embedded quotes for profiles with spaces (#771).
            '"--user-data-dir=`"$env:USERPROFILE\\.boss-chrome-profile`"",'
            "'https://www.zhipin.com/web/geek/job'"
        )
    return (
        "google-chrome "
        + common
        + '--user-data-dir="$HOME/.boss-chrome-profile" '
        + url
    )


def _cdp_json(path: str):
    """GET a local CDP endpoint (system proxy disabled); return parsed JSON, or None on failure.

    CDP only binds the loopback address (127.0.0.1), so a direct connection is enough.
    An empty ProxyHandler explicitly bypasses any configured system/global proxy:
    sending a localhost probe through a proxy is pointless and may be blocked. This is a
    deliberate localhost-only assumption that the config proxy must not override.
    """
    req = urllib.request.Request(f"{_CDP_URL}{path}", method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=_CDP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _has_zhipin_page(pages) -> bool:
    """Whether the CDP /json tab list has a reusable zhipin.com tab (exact hostname check)."""
    for page in pages or []:
        if page.get("type") == "page" and host_matches(page.get("url", ""), "zhipin.com"):
            return True
    return False


_SECURITY_CHECK_MARKERS = ("security-check", "zhipin-security", "_security_check")


def _security_check_blocks_all(pages) -> bool:
    """Whether every open zhipin tab is stuck on the anti-bot security check page (not login)."""
    zhipin_urls = [
        page.get("url", "")
        for page in (pages or [])
        if page.get("type") == "page" and host_matches(page.get("url", ""), "zhipin.com")
    ]
    if not zhipin_urls:
        return False
    return all(
        any(marker in url.lower() for marker in _SECURITY_CHECK_MARKERS)
        for url in zhipin_urls
    )


_WS_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exact(sock: socket.socket, buf: bytes, size: int):
    """Grow *buf* to at least *size* bytes; None when the peer disconnects."""
    while len(buf) < size:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_ws_text_frame(sock: socket.socket, initial: bytes = b""):
    """Read the next complete data message and return (payload, leftover).

    Fragmented messages (a text/binary frame without FIN followed by
    continuation frames) are reassembled up to the final FIN frame. leftover
    holds extra bytes this recv picked up that belong to later frames; the
    caller should pass it back as ``initial`` for the next message (one recv
    can return several frames). Returns (None, leftover) on a close frame,
    disconnect, protocol error or an oversized message. Ping/pong frames are
    ignored, including when interleaved between fragments.
    """
    buf = initial
    message = b""
    in_message = False
    while True:
        grown = _recv_exact(sock, buf, 2)
        if grown is None:
            return None, buf
        buf = grown
        fin = bool(buf[0] & 0x80)
        opcode = buf[0] & 0x0F
        masked = bool(buf[1] & 0x80)
        length = buf[1] & 0x7F
        header_len = 2
        if length == 126:
            grown = _recv_exact(sock, buf, header_len + 2)
            if grown is None:
                return None, buf
            buf = grown
            length = struct.unpack(">H", buf[header_len:header_len + 2])[0]
            header_len += 2
        elif length == 127:
            grown = _recv_exact(sock, buf, header_len + 8)
            if grown is None:
                return None, buf
            buf = grown
            length = struct.unpack(">Q", buf[header_len:header_len + 8])[0]
            header_len += 8
        mask = b""
        if masked:
            grown = _recv_exact(sock, buf, header_len + 4)
            if grown is None:
                return None, buf
            buf = grown
            mask = buf[header_len:header_len + 4]
            header_len += 4
        if len(message) + length > _WS_MAX_MESSAGE_BYTES:
            return None, b""
        grown = _recv_exact(sock, buf, header_len + length)
        if grown is None:
            return None, buf
        buf = grown
        payload = buf[header_len:header_len + length]
        buf = buf[header_len + length:]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            return None, buf
        if opcode in (0x1, 0x2):  # first frame of a text / binary message
            if in_message:
                return None, buf  # protocol error: new message inside a fragment
            message = payload
            in_message = True
        elif opcode == 0x0:  # continuation
            if not in_message:
                return None, buf  # protocol error: continuation without a start
            message += payload
        else:
            continue  # ping(0x9)/pong(0xA) etc.: ignore and read the next frame
        if fin:
            return message, buf


def _is_loopback_host(host: str) -> bool:
    """Only localhost / loopback literals may carry the CDP WebSocket."""
    normalized = host.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _send_ws_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytes([0x81])  # FIN + text
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(header + mask + masked)


def _cdp_zhipin_login_cookie() -> bool | None:
    """Read-only probe for the zhipin.com login cookie (wt2) in the dedicated Chrome.

    True = present; False = absent (browser logged out, CDP search will report
    AUTH_EXPIRED); None = probe failed (e.g. CDP WebSocket unreachable), login state
    unknown. It only proves the browser profile has logged in; it does not verify the
    cookie is still valid server-side.
    """
    version = _cdp_json("/json/version")
    ws_url = (version or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    try:
        parsed = urlparse(ws_url)
        host = parsed.hostname or ""
        port = parsed.port or 80
    except (TypeError, ValueError):
        return None
    # /json/version is attacker-influenced data from whatever answers on the
    # port: never let it point the probe (and the browser's cookies) at a
    # non-loopback host.
    if parsed.scheme.lower() != "ws" or not _is_loopback_host(host):
        return None
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        with socket.create_connection((host, port), timeout=_CDP_TIMEOUT) as sock:
            sock.settimeout(_CDP_TIMEOUT)
            key = base64.b64encode(os.urandom(16)).decode()
            # IPv6 literals need brackets in the Host header (urlparse().hostname strips them)
            host_header = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            )
            sock.sendall(handshake.encode())
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    return None
                response += chunk
            head, _, rest = response.partition(b"\r\n\r\n")
            status_line = head.split(b"\r\n", 1)[0]
            # Parse the status token exactly: accept "HTTP/1.1 101" (empty reason phrase allowed),
            # reject bogus codes such as 1019
            if status_line.split()[1:2] != [b"101"]:
                return None
            accept = base64.b64encode(
                hashlib.sha1((key + _WS_ACCEPT_GUID).encode()).digest()
            ).decode()
            if accept not in head.decode("latin-1"):
                return None
            _send_ws_text(sock, json.dumps({"id": 1, "method": "Storage.getCookies"}))
            # Chrome may push event frames (no id) first; keep reading until the id==1
            # response arrives (capped to avoid an endless loop). leftover carries extra
            # bytes from the previous recv so nothing is lost when one recv gets several
            # frames.
            buf = rest
            for _ in range(16):
                payload, buf = _read_ws_text_frame(sock, initial=buf)
                if payload is None:
                    return None
                data = json.loads(payload.decode("utf-8"))
                if data.get("id") != 1:
                    continue  # event frame etc.: skip and read the next one
                if "result" not in data:
                    return None
                for cookie in data["result"].get("cookies", []):
                    if not isinstance(cookie, dict):
                        continue
                    if cookie.get("name") == "wt2" and domain_matches(
                        str(cookie.get("domain") or ""), "zhipin.com"
                    ):
                        return True
                return False
            return None
    except Exception:
        return None


class BossChannel(Channel):
    name = "boss"
    description = "Boss Zhipin job search and JDs"
    backends = ["boss-agent-cli (CDP)"]
    tier = 2

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "zhipin.com")

    def check(self, config=None):
        self.active_backend = None

        # Layer 1: is boss-agent-cli installed?
        probe = probe_command("boss", ["--version"], timeout=10)
        if probe.status == "missing":
            return "off", (
                "boss-agent-cli is not installed. Get the user's permission first, then run:\n"
                "  agent-reach install --system --channels=boss\n"
                "After installing, the user logs in to zhipin.com manually in a dedicated Chrome."
            )
        if probe.status == "broken":
            return "error", (
                "The boss command exists but cannot run; the install is broken. Reinstall:\n"
                "  agent-reach install --system --channels=boss"
            )
        if not probe.ok:
            return "warn", f"boss command probe failed ({probe.status}); check the install"

        # Layer 2: is the CDP port reachable?
        if _cdp_json("/json/version") is None:
            return "off", (
                "CDP debugging port unreachable. Start the debug Chrome first:\n"
                f"  {_chrome_launch_command()}\n"
                "  then have the user log in to zhipin.com manually in that window.\n"
                "It binds 127.0.0.1 only; any process that can reach port 9222 can fully "
                "control this Chrome."
            )

        # Layer 3: is there a reusable BOSS tab?
        pages = _cdp_json("/json")
        if pages is None:
            return "warn", "CDP port reachable but listing tabs via /json failed"
        if not _has_zhipin_page(pages):
            return "warn", (
                "CDP reachable but no open zhipin.com tab found (this does not mean logged "
                "out: cookies may still be there and boss-agent-cli opens its own tab). "
                "Logging in to zhipin.com in Chrome first is recommended."
            )

        # Layer 4: login cookie (wt2) inside the browser. The browser is the source of
        # truth: `boss status` only checks the local session.enc, and neither state
        # implies the other.
        browser_cookie = _cdp_zhipin_login_cookie()
        if browser_cookie is False:
            return "warn", (
                "CDP link ready, but the dedicated Chrome has no zhipin.com login cookie "
                "(wt2): the browser is logged out and search will report AUTH_EXPIRED. Note "
                "that logged_in from `boss status` only reflects the local session.enc "
                "credential, not the browser login. Have the user check that Chrome window "
                "and log in to zhipin.com (do this first after launching the CDP Chrome), "
                "then run `boss --cdp-url http://localhost:9222 login --cdp` to sync the "
                "login state."
            )

        cookie_note = (
            "login cookie (wt2) present in browser"
            if browser_cookie
            else "browser login cookie probe failed, login state unknown"
        )

        if _security_check_blocks_all(pages):
            return "warn", (
                "CDP link ready, but every open zhipin tab is stuck on the security check "
                "page (security-check / zhipin-security). This is a Boss anti-bot challenge "
                "unrelated to login: it also appears when logged in and does not mean "
                "logged out. Do not ask the user to log in again because of it; just have "
                "them solve the slider manually. "
                f"Browser login state for reference: {cookie_note}. "
                "Do not use `boss status` to judge the CDP browser login state (it only "
                "checks the local session.enc)."
            )

        # All four probes passed: the CDP link is ready, so record the backend in use
        # (base contract)
        self.active_backend = self.backends[0]
        return "warn", (
            f"CDP link ready (port 9222 open + reusable zhipin tab, {cookie_note}). "
            "Doctor does not run a real search, verify the cookie server-side, or check the "
            "upstream boss-agent-cli #403-#407 APIs. "
            "First run `boss --cdp-url http://localhost:9222 login --cdp` to sync the "
            "existing login; "
            "search with `boss --browser-source existing-browser --cdp-url "
            "http://localhost:9222 search ...` so it stops immediately when CDP is "
            "unavailable instead of falling back to headless. "
            "If search reports AUTH_EXPIRED, follow the login runbook (user logs in in the "
            "dedicated window + login --cdp); do not treat it as a security check issue."
        )
