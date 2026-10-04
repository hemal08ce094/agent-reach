# -*- coding: utf-8 -*-
"""YouTube through yt-dlp (skill/references/video.md).

Transcripts follow the documented track order: manual subtitles first, then
the original-language speech recognition (``*-orig``), then an automatic
track only when it matches the video's own language. Anything else is a
machine translation and is labelled as one.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import find_records, load_structured, pick
from agent_reach.research.runner import READ_TIMEOUT, SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_SUB_LANGS = "en,.*-orig"
_TIMESTAMP_RE = re.compile(r"^(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->")
_CUE_TAG_RE = re.compile(r"<[^>]+>")


def _video_url(rec: Dict[str, Any]) -> Optional[str]:
    url = pick(rec, "webpage_url", "url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    vid = pick(rec, "id")
    if isinstance(vid, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
        return f"https://www.youtube.com/watch?v={vid}"
    return None


def vtt_to_text(vtt: str) -> str:
    """Plain transcript with a ``[mm:ss]`` marker roughly every 30 seconds."""
    paragraphs: List[str] = []
    current: List[str] = []
    marker = ""
    last_line = ""
    next_mark = 0.0
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE", "STYLE")):
            continue
        if _TIMESTAMP_RE.match(line):
            start = line.split("-->")[0].strip().replace(",", ".")
            pieces = [float(p) for p in start.split(":")]
            seconds = pieces[-1] + (pieces[-2] * 60 if len(pieces) > 1 else 0) + (pieces[-3] * 3600 if len(pieces) > 2 else 0)
            if seconds >= next_mark:
                if current:
                    paragraphs.append(f"[{marker}] " + " ".join(current))
                current = []
                minutes, secs = divmod(int(seconds), 60)
                marker = f"{minutes:02d}:{secs:02d}"
                next_mark = seconds + 30
            continue
        if line.isdigit():
            continue
        text = _CUE_TAG_RE.sub("", line).strip()
        if text and text != last_line:
            current.append(text)
            last_line = text
    if current:
        paragraphs.append(f"[{marker}] " + " ".join(current))
    return "\n\n".join(paragraphs)


def choose_track(files: List[str], info: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Pick the subtitle file by the documented order and describe the choice."""
    manual = set((info.get("subtitles") or {}).keys())
    language = str(info.get("language") or "")
    by_lang: Dict[str, str] = {}
    for path in files:
        parts = os.path.basename(path).split(".")
        if len(parts) >= 3:
            by_lang[parts[-2]] = path
    for lang, path in by_lang.items():
        if lang in manual:
            return path, f"Manual subtitles ({lang})"
    for lang, path in by_lang.items():
        if lang.endswith("-orig"):
            return path, f"Original speech recognition ({lang})"
    for lang, path in by_lang.items():
        if language and (lang == language or lang.split("-")[0] == language.split("-")[0]):
            return path, f"Automatic captions in the video's language ({lang})"
    for lang, path in by_lang.items():
        return path, f"Machine-translated automatic captions ({lang}); not the speaker's own words"
    return None, ""


class YouTubeSource(Source):
    name = "youtube"
    label = "YouTube"
    channel = "youtube"
    backends = ("yt-dlp",)
    hint = "Videos via yt-dlp"

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        return ["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", f"ytsearch{limit}:{query}"]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        data = load_structured(ctx.run(self.search_argv(query, limit, backend)).stdout)
        entries = data.get("entries") if isinstance(data, dict) else None
        records = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else find_records(data)
        items = []
        for rec in records[:limit]:
            item = make_item(
                self.name,
                title=pick(rec, "title"),
                url=_video_url(rec),
                snippet=pick(rec, "description"),
                author=pick(rec, "channel", "uploader"),
                published=pick(rec, "timestamp", "release_timestamp", "upload_date"),
                metrics={"views": pick(rec, "view_count"), "duration": pick(rec, "duration")},
                native_id=pick(rec, "id"),
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "youtube.com", "youtu.be")

    def read(self, url: str, ctx: Context) -> ReadResult:
        require_backend(self, ctx.backend)
        with tempfile.TemporaryDirectory(prefix="agent-reach-yt-") as tmp:
            argv = [
                "yt-dlp", "--skip-download", "--write-sub", "--write-auto-sub",
                "--sub-lang", _SUB_LANGS, "--sub-format", "vtt", "--write-info-json",
                "--no-warnings", "-o", os.path.join(tmp, "%(id)s"), "--", url,
            ]
            ctx.run(argv, timeout=max(ctx.timeout, READ_TIMEOUT), cwd=tmp)
            info: Dict[str, Any] = {}
            info_files = glob.glob(os.path.join(tmp, "*.info.json"))
            if info_files:
                try:
                    with open(info_files[0], encoding="utf-8") as fh:
                        loaded = json.loads(fh.read(4 * 1024 * 1024))
                    info = loaded if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    info = {}
            path, track = choose_track(sorted(glob.glob(os.path.join(tmp, "*.vtt"))), info)
            if path is None:
                raise SourceFailure(
                    "No subtitles were downloaded. Retry later, or ask your agent to run "
                    "`agent-reach transcribe` on this URL."
                )
            with open(path, encoding="utf-8", errors="replace") as fh:
                transcript = vtt_to_text(fh.read(8 * 1024 * 1024))
        if not transcript.strip():
            raise SourceFailure("The subtitle file was empty")
        description = str(info.get("description") or "").strip()
        text = transcript if not description else f"{transcript}\n\n## Description\n\n{description[:4000]}"
        return make_read(
            title=info.get("title") or url,
            url=url,
            source=self.name,
            text=text,
            fmt="text",
            via="yt-dlp",
            command=ctx.commands[-1],
            note=track,
            metadata={
                "channel": info.get("channel") or info.get("uploader"),
                "published": info.get("upload_date"),
                "duration": info.get("duration_string"),
            },
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        q = shlex.quote(url)
        return [
            f"yt-dlp --write-sub --write-auto-sub --sub-lang {shlex.quote(_SUB_LANGS)} "
            f"--skip-download -o '/tmp/%(id)s' {q}",
            f"yt-dlp --write-comments --skip-download --write-info-json "
            f"--extractor-args 'youtube:max_comments=20' -o '/tmp/%(id)s' {q}",
        ]
