# -*- coding: utf-8 -*-
"""YouTube — check if yt-dlp is available with JS runtime."""

import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from agent_reach.probe import probe_command
from agent_reach.utils.paths import (
    PrivatePathError,
    get_ytdlp_config_path,
    home_dir,
    read_small_text_no_follow,
)

from .base import Channel

_JS_RUNTIMES_SUPPORTED_FROM = (2025, 11, 12)
_YTDLP_UPGRADE_COMMAND = 'python -m pip install -U "yt-dlp[default]"'


def _parse_ytdlp_version(version: str):
    """Return a comparable stable yt-dlp release tuple, if recognised."""
    match = re.fullmatch(r"\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*", version)
    return tuple(map(int, match.groups())) if match else None


def ytdlp_user_config_candidates() -> List[Path]:
    """Return yt-dlp's user config candidates in yt-dlp's own lookup order.

    Mirrors ``yt_dlp.options._load_from_config_dirs`` over
    ``get_user_config_dirs``: for each of ``$XDG_CONFIG_HOME/yt-dlp`` (default
    ``~/.config/yt-dlp``), ``%APPDATA%/yt-dlp`` and ``~/.yt-dlp`` it tries
    ``<parent>/yt-dlp.conf`` (plus ``yt-dlp.conf.txt`` for the dot-dir),
    ``<dir>/config`` and ``<dir>/config.txt``. yt-dlp reads only the FIRST
    existing file, so Doctor must inspect exactly that one.
    """
    home = home_dir()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_dirs = [Path(xdg) / "yt-dlp" if xdg else home / ".config" / "yt-dlp"]
    appdata = os.environ.get("APPDATA") or os.environ.get("appdata")
    if appdata:
        config_dirs.append(Path(appdata) / "yt-dlp")
    config_dirs.append(home / ".yt-dlp")

    candidates: List[Path] = []
    for config_dir in config_dirs:
        parent = config_dir.parent
        candidates.append(parent / "yt-dlp.conf")
        if config_dir.name.startswith("."):
            candidates.append(parent / "yt-dlp.conf.txt")
        candidates.append(config_dir / "config")
        candidates.append(config_dir / "config.txt")
    return candidates


def find_ytdlp_user_config() -> Optional[Path]:
    """Return the user config file yt-dlp actually loads, or None."""
    for candidate in ytdlp_user_config_candidates():
        # lexists: a (possibly dangling) symlink is still the file yt-dlp
        # would try first. Doctor reads it through the link (read-only).
        if os.path.lexists(candidate) and not os.path.isdir(candidate):
            return candidate
    return None


def _has_js_runtime_config(config_path) -> bool:
    """Return whether yt-dlp config enables a JS runtime outside comments.

    yt-dlp parses config files with ``shlex.split(comments=True)``; doing the
    same means ``# --js-runtimes node`` no longer counts (#715). Symlinked
    (dotfile-managed) configs are followed, as yt-dlp itself does.
    """
    try:
        payload = read_small_text_no_follow(
            config_path,
            follow_symlinks=True,
            max_bytes=1024 * 1024,
            encoding="utf-8-sig",
        )
        if payload is None or "--js-runtimes" not in payload:
            return False
        tokens = shlex.split(payload, comments=True)
    except (OSError, UnicodeError, PrivatePathError, ValueError):
        return False
    # Substring per token: quoted alias expansions can carry the flag too.
    return any("--js-runtimes" in token for token in tokens)


def render_ytdlp_fix_command_for(config_path: Path) -> str:
    """Return an OS-appropriate command appending ``--js-runtimes node`` to *config_path*."""
    if sys.platform == "win32":
        return (
            f"$cfg = '{config_path}'\n"
            "New-Item -ItemType Directory -Force -Path (Split-Path $cfg) | Out-Null\n"
            "if (-not (Test-Path $cfg) -or -not (Select-String -Path $cfg -Pattern '--js-runtimes' -Quiet)) {\n"
            "  Add-Content -Path $cfg -Value '--js-runtimes node'\n"
            "}"
        )
    config_dir = shlex.quote(str(config_path.parent))
    config_file = shlex.quote(str(config_path))
    return (
        f"mkdir -p {config_dir} && "
        "{ "
        f"grep -qxF -- '--js-runtimes node' {config_file} 2>/dev/null || "
        f"printf '%s\\n' '--js-runtimes node' >> {config_file}; "
        "}"
    )


class YouTubeChannel(Channel):
    name = "youtube"
    description = "YouTube videos and subtitles"
    backends = ["yt-dlp"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "youtube.com", "youtu.be")

    def check(self, config=None):
        # Actually run yt-dlp --version to tell apart missing / broken venv / not runnable
        probe = probe_command("yt-dlp", ["--version"], timeout=10, package="yt-dlp")
        if probe.status == "missing":
            self.active_backend = None
            return "off", f"yt-dlp is not installed. Install: {_YTDLP_UPGRADE_COMMAND}"
        if probe.status == "broken":
            self.active_backend = None
            return "error", (
                "yt-dlp is installed but cannot run. Reinstall (with JS support):\n"
                f"  {_YTDLP_UPGRADE_COMMAND}\n{probe.hint}"
            )
        if not probe.ok:  # timeout / error: installed but not runnable
            self.active_backend = None
            detail = probe.hint or probe.output or probe.status
            return "error", f"yt-dlp does not run properly: {detail}"
        # yt-dlp itself works; the JS runtime/transcription checks below only affect
        # ok/warn, not which backend is active
        self.active_backend = "yt-dlp"
        # Check JS runtime
        has_js = shutil.which("deno") or shutil.which("node")
        if not has_js:
            return "warn", (
                "yt-dlp is installed but has no JS runtime (required for YouTube).\n"
                "  Install Node.js or deno, then run: agent-reach install --system"
            )
        # Check yt-dlp config for --js-runtimes
        # Deno works out of the box; Node.js requires explicit config
        has_deno = shutil.which("deno")
        if not has_deno:
            # Inspect the file yt-dlp really loads (first existing candidate);
            # with none, point the fix at the default XDG location.
            ytdlp_config = find_ytdlp_user_config() or get_ytdlp_config_path()
            if not _has_js_runtime_config(ytdlp_config):
                version = _parse_ytdlp_version(probe.output)
                if version is None:
                    return "warn", (
                        "Cannot confirm whether this yt-dlp version supports the JS "
                        "runtime config. Upgrade first, then rerun doctor:\n"
                        f"  {_YTDLP_UPGRADE_COMMAND}"
                    )
                if version < _JS_RUNTIMES_SUPPORTED_FROM:
                    return "warn", (
                        "yt-dlp is too old to support the JS runtime config. Upgrade first, then rerun doctor:\n"
                        f"  {_YTDLP_UPGRADE_COMMAND}"
                    )
                return "warn", (
                    "yt-dlp is installed but no JS runtime is configured "
                    f"(checked {ytdlp_config}). Run:\n"
                    f"  {render_ytdlp_fix_command_for(ytdlp_config)}"
                )
        # Surface transcription readiness so `doctor` reports it.
        # NOTE: this only proves the local toolchain; video access and subtitles
        # are NOT live-verified (a bot/sign-in challenge can still block real
        # fetches), so the wording must not promise more (#766).
        msg = (
            "yt-dlp and its JS runtime are ready; video access and subtitles "
            "were not verified live"
        )
        if config is not None:
            providers = []
            if config.is_configured("groq_whisper"):
                providers.append("groq")
            if config.is_configured("openai_whisper"):
                providers.append("openai")
            if providers:
                missing_media_tools = [
                    tool
                    for tool in ("ffmpeg", "ffprobe")
                    if not shutil.which(tool)
                ]
                if missing_media_tools:
                    msg += (
                        " (audio transcription needs "
                        + ", ".join(missing_media_tools)
                        + ")"
                    )
                else:
                    msg += f", can transcribe audio ({'/'.join(providers)})"
        return "ok", msg

    def transcribe(
        self,
        url: str,
        *,
        provider: str = "auto",
        config=None,
        allow_provider_fallback: bool = False,
    ) -> str:
        """Download a YouTube video's audio and return its transcript.

        Delegates to :func:`agent_reach.transcribe.transcribe`. Imported lazily
        so the channel module stays cheap to import for users who never
        transcribe.
        """
        from agent_reach.transcribe import transcribe as _transcribe

        return _transcribe(
            url,
            provider=provider,
            config=config,
            allow_provider_fallback=allow_provider_fallback,
        )
