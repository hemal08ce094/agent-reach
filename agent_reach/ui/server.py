# -*- coding: utf-8 -*-
"""Loopback HTTP server for the Agent Reach dashboard.

Security model (this server can run installers and holds cookies):

* Binds to loopback only; non-loopback hosts are refused.
* A random per-launch token is required on every ``/api/`` request in the
  ``X-Agent-Reach-Token`` header. The page receives it in the launch URL
  fragment (never sent to the server, so it never reaches access logs).
* The Host header must be ``127.0.0.1:<port>`` or ``localhost:<port>``
  (DNS-rebinding defence) and any Origin header must be the same origin.
* Mutating endpoints are POST, PATCH or DELETE; bodies must be
  ``Content-Type: application/json``. The custom token header already forces
  a CORS preflight, which a cross-origin page cannot pass.
* Secrets are never returned: config keys are reported as set / not set.
  Values posted to ``/api/configure`` reach the CLI on stdin only.
* Actions are an allowlist of argv lists built server-side (see ``jobs.py``).
* Strict CSP, nosniff, no-referrer and no-store headers on every response.
* Research routes (``research_api.py``) run only documented upstream commands
  as argv lists and validate every URL as public before anything is fetched.
"""

from __future__ import annotations

import hmac
import json
import platform
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from loguru import logger

from agent_reach import __version__
from agent_reach.research import BoardStore, ResearchError, ResearchManager, source_states
from agent_reach.ui import catalog
from agent_reach.ui.jobs import (
    JobError,
    JobManager,
    child_env,
    cli_argv,
    strip_ansi,
)

TOKEN_HEADER = "X-Agent-Reach-Token"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
MAX_BODY_BYTES = 1024 * 1024 + 4096
CONFIGURE_TIMEOUT = 120.0

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

#: The only static files ever served, mapped to their content types.
STATIC_FILES: Dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
    "research.css": "text/css; charset=utf-8",
    "research.js": "text/javascript; charset=utf-8",
}


class UIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _load_static(name: str) -> bytes:
    return (files("agent_reach.ui") / "static" / name).read_bytes()


def _read_config():
    from agent_reach.config import Config

    return Config(read_only=True)


def _detect_environment() -> str:
    from agent_reach.cli import _detect_environment as detect

    return detect()


class DashboardState:
    """Shared, per-launch server state."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        jobs: Optional[JobManager] = None,
        check_all: Optional[Callable[..., Dict[str, dict]]] = None,
        check_one: Optional[Callable[..., Optional[dict]]] = None,
        configure_runner: Optional[Callable[[str, str], Tuple[int, str]]] = None,
        research: Optional[ResearchManager] = None,
        boards: Optional[BoardStore] = None,
    ):
        self.token = token or secrets.token_urlsafe(32)
        self.jobs = jobs or JobManager()
        self._check_all = check_all
        self._check_one = check_one
        self._configure_runner = configure_runner or run_configure
        self.port = 0
        self.last_doctor: Optional[Dict[str, Any]] = None
        self._doctor_lock = threading.Lock()
        self._env: Optional[str] = None
        self.boards = boards or BoardStore()
        self.research = research or ResearchManager(self.research_states, _read_config)

    # Library calls are resolved lazily so tests can patch agent_reach.doctor.
    def run_doctor(self) -> Dict[str, Any]:
        from agent_reach import doctor

        check_all = self._check_all or doctor.check_all
        with self._doctor_lock:
            results = check_all(_read_config())
            self.last_doctor = {"results": results, "checked_at": time.time()}
            return self.last_doctor

    def ensure_doctor(self) -> Dict[str, Any]:
        """The latest doctor snapshot, running doctor once if there is none yet."""
        from agent_reach import doctor

        with self._doctor_lock:
            if self.last_doctor is None:
                check_all = self._check_all or doctor.check_all
                results = check_all(_read_config())
                self.last_doctor = {"results": results, "checked_at": time.time()}
            return self.last_doctor

    def research_states(self):
        """Which research sources may run, from doctor's snapshot (runs doctor if needed)."""
        return source_states(self.ensure_doctor()["results"], _read_config())

    def research_backends(self) -> Dict[str, Optional[str]]:
        """Active backend per source without starting doctor (used by exports)."""
        if self.last_doctor is None:
            return {}
        states = source_states(self.last_doctor["results"], _read_config())
        return {name: st.backend for name, st in states.items()}

    def run_check(self, name: str) -> Optional[dict]:
        from agent_reach import doctor

        check_one = self._check_one or doctor.check_one
        result = check_one(name, _read_config())
        if result is not None and self.last_doctor is not None:
            self.last_doctor["results"][name] = result
        return result

    def configure(self, cli_key: str, value: str) -> Tuple[int, str]:
        return self._configure_runner(cli_key, value)

    def environment(self) -> str:
        if self._env is None:
            try:
                self._env = _detect_environment()
            except Exception:  # noqa: BLE001 — status must never fail
                self._env = "unknown"
        return self._env


def run_configure(cli_key: str, value: str) -> Tuple[int, str]:
    """Run ``agent-reach configure <key> --stdin`` with ``value`` on stdin."""
    try:
        proc = subprocess.run(
            cli_argv("configure", cli_key, "--stdin"),
            input=value,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONFIGURE_TIMEOUT,
            env=child_env(),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"configure {cli_key} did not finish within {CONFIGURE_TIMEOUT:g}s"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _redact(text: str, secret: str) -> str:
    """Remove any echo of the submitted value (and its parts) from CLI output."""
    from agent_reach.utils.text import scrub_url_credentials

    text = strip_ansi(text)
    pieces = {secret.strip()}
    pieces.update(p for p in secret.replace(";", " ").replace("=", " ").split() if len(p) >= 8)
    for piece in sorted((p for p in pieces if p), key=len, reverse=True):
        text = text.replace(piece, "[redacted]")
    return scrub_url_credentials(text)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AgentReachUI"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    state: DashboardState  # set on the per-server subclass

    # ── plumbing ──

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Never log request bodies or headers; method + path (sans query) only.
        logger.debug(f"ui {self.command} {urlsplit(self.path).path}")

    def _common_headers(self, content_type: str, length: int, *, api: bool) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store" if api else "no-cache")

    def _send(self, status: int, body: bytes, content_type: str, *, api: bool) -> None:
        self.send_response(status)
        self._common_headers(content_type, len(body), api=api)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", api=True)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _allowed_origins(self) -> Tuple[str, ...]:
        port = self.state.port
        return tuple(f"http://{h}:{port}" for h in LOOPBACK_HOSTS)

    def _check_host_and_origin(self) -> None:
        host = (self.headers.get("Host") or "").strip().lower()
        allowed_hosts = {f"{h}:{self.state.port}" for h in LOOPBACK_HOSTS}
        if host not in allowed_hosts:
            raise UIError(HTTPStatus.FORBIDDEN, "Host not allowed")
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in self._allowed_origins():
            raise UIError(HTTPStatus.FORBIDDEN, "Cross-origin request rejected")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            raise UIError(HTTPStatus.FORBIDDEN, "Cross-site request rejected")

    def _check_token(self) -> None:
        supplied = self.headers.get(TOKEN_HEADER) or ""
        if not hmac.compare_digest(supplied.encode("utf-8"), self.state.token.encode("utf-8")):
            raise UIError(HTTPStatus.UNAUTHORIZED, "Missing or invalid token")

    def _read_json(self) -> Dict[str, Any]:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise UIError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise UIError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise UIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UIError(HTTPStatus.BAD_REQUEST, "Body must be valid JSON") from None
        if not isinstance(payload, dict):
            raise UIError(HTTPStatus.BAD_REQUEST, "Body must be a JSON object")
        return payload

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host_and_origin()
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                self._check_token()
                self._api(method, path, parse_qs(parts.query))
            elif method in ("GET", "HEAD"):
                self._static(path)
            else:
                raise UIError(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
        except UIError as exc:
            self._error(exc.status, exc.message)
        except JobError as exc:
            self._error(exc.status, exc.message)
        except ResearchError as exc:
            self._error(exc.status, exc.message)
        except Exception as exc:  # noqa: BLE001 — never leak a traceback to the page
            logger.exception(f"ui handler error: {type(exc).__name__}")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal error")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    # ── static ──

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            name = "index.html"
        elif path.startswith("/static/"):
            name = path[len("/static/"):]
        else:
            raise UIError(HTTPStatus.NOT_FOUND, "Not found")
        # Exact allowlist lookup: no path joining, so no traversal is possible.
        if name not in STATIC_FILES:
            raise UIError(HTTPStatus.NOT_FOUND, "Not found")
        self._send(HTTPStatus.OK, _load_static(name), STATIC_FILES[name], api=False)

    # ── API ──

    def _api(self, method: str, path: str, query: Dict[str, List[str]]) -> None:
        from agent_reach.ui.research_api import PREFIX, ResearchRoutes

        if path.startswith(PREFIX):
            ResearchRoutes(self).dispatch(method, path, query)
            return
        routes_get = {
            "/api/status": self._get_status,
            "/api/channels": self._get_channels,
            "/api/doctor": self._get_doctor,
            "/api/jobs": self._get_jobs,
        }
        routes_post = {
            "/api/doctor": self._post_doctor,
            "/api/jobs": self._post_jobs,
            "/api/configure": self._post_configure,
        }
        if method in ("GET", "HEAD") and path in routes_get:
            routes_get[path]()
            return
        if method == "POST" and path in routes_post:
            routes_post[path](self._read_json())
            return
        if path.startswith("/api/jobs/") and method == "GET":
            self._get_job(path[len("/api/jobs/"):], query)
            return
        if path.startswith("/api/channels/") and path.endswith("/check") and method == "POST":
            self._read_json()
            self._post_check(path[len("/api/channels/"):-len("/check")])
            return
        known = set(routes_get) | set(routes_post)
        if path in known or path.startswith(("/api/jobs/", "/api/channels/")):
            raise UIError(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
        raise UIError(HTTPStatus.NOT_FOUND, "Not found")

    def _get_status(self) -> None:
        from agent_reach.config import Config

        config = _read_config()
        self._json(
            HTTPStatus.OK,
            {
                "version": __version__,
                "platform": sys.platform,
                "os": platform.system() or sys.platform,
                "os_release": platform.release(),
                "python": platform.python_version(),
                "environment": self.state.environment(),
                "config_exists": Config.CONFIG_FILE.exists(),
                "settings": catalog.global_settings(config),
                "skill_actions": ["skill_install", "skill_uninstall"],
                "running_job": (lambda j: j.id if j else None)(self.state.jobs.running()),
            },
        )

    def _get_channels(self) -> None:
        self._json(HTTPStatus.OK, {"channels": catalog.channel_catalog(_read_config())})

    def _get_doctor(self) -> None:
        self._json(HTTPStatus.OK, self.state.last_doctor or {"results": None, "checked_at": None})

    def _post_doctor(self, _payload: Dict[str, Any]) -> None:
        self._json(HTTPStatus.OK, self.state.run_doctor())

    def _post_check(self, name: str) -> None:
        result = self.state.run_check(name)
        if result is None:
            raise UIError(HTTPStatus.NOT_FOUND, "Unknown channel")
        self._json(HTTPStatus.OK, {"name": name, "result": result, "checked_at": time.time()})

    def _get_jobs(self) -> None:
        self._json(HTTPStatus.OK, {"jobs": self.state.jobs.list()})

    def _post_jobs(self, payload: Dict[str, Any]) -> None:
        job = self.state.jobs.start(payload.get("action"), payload.get("params"))
        self._json(HTTPStatus.ACCEPTED, job.snapshot(0))

    def _get_job(self, job_id: str, query: Dict[str, List[str]]) -> None:
        job = self.state.jobs.get(job_id)
        if job is None:
            raise UIError(HTTPStatus.NOT_FOUND, "Unknown job")
        try:
            offset = max(0, int((query.get("offset") or ["0"])[0]))
        except ValueError:
            raise UIError(HTTPStatus.BAD_REQUEST, "offset must be an integer") from None
        self._json(HTTPStatus.OK, job.snapshot(offset))

    def _post_configure(self, payload: Dict[str, Any]) -> None:
        key = payload.get("key")
        value = payload.get("value")
        if not isinstance(key, str) or key not in catalog.CONFIGURE_FIELDS:
            raise UIError(HTTPStatus.BAD_REQUEST, "Key is not configurable from the dashboard")
        if not isinstance(value, str) or not value.strip():
            raise UIError(HTTPStatus.BAD_REQUEST, "Value must be a non-empty string")
        if len(value) > 1024 * 1024:
            raise UIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Value too large")
        spec = catalog.CONFIGURE_FIELDS[key]
        if spec.kind == "select" and value not in spec.options:
            raise UIError(HTTPStatus.BAD_REQUEST, "Unsupported option")
        code, output = self.state.configure(key, value)
        self._json(
            HTTPStatus.OK,
            {
                "ok": code == 0,
                "exit_code": code,
                "output": _redact(output, value).strip(),
                "keys": catalog.config_key_states(_read_config(), spec.writes),
            },
        )


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str, port: int, state: DashboardState):
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"refusing to bind the dashboard to {host!r}: only 127.0.0.1 and "
                "localhost are supported"
            )
        handler = type("BoundDashboardHandler", (DashboardHandler,), {"state": state})
        super().__init__(("127.0.0.1", port), handler)
        self.state = state
        state.port = self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.state.port}/#token={self.state.token}"

    def server_close(self) -> None:
        self.state.jobs.shutdown()
        self.state.research.shutdown()
        super().server_close()


def serve(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    open_browser: bool = True,
    out=None,
) -> int:
    """Run the dashboard until Ctrl+C. Returns a process exit code."""
    out = out or sys.stdout
    try:
        server = DashboardServer(host, port, DashboardState())
    except ValueError as exc:
        print(f"agent-reach ui: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"agent-reach ui: error: could not listen on port {port}: {exc.strerror or exc}",
              file=sys.stderr)
        return 1

    url = server.url
    print(f"Agent Reach dashboard running at {url}", file=out)
    print("The link contains a one-time access token; keep it private. Press Ctrl+C to stop.",
          file=out, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — opening a browser is best-effort
            pass
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping dashboard.", file=out)
    finally:
        server.server_close()
    return 0
