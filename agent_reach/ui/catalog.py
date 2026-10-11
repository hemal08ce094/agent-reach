# -*- coding: utf-8 -*-
"""Static channel metadata for the dashboard.

Everything an agent-facing snippet or setup guide says is read from the
packaged ``guides/*.md`` and ``skill/references/*.md`` files, so the
dashboard never invents commands. This module only adds what those files do
not encode: display names, which config keys a channel reads, which
``configure`` keys can set them, and which ``install --channels`` value (if
any) installs the channel's backend.

Secret values never leave this module: config keys are reported as
set / not set only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any, Dict, List, Optional, Tuple

from agent_reach.channels import get_all_channels
from agent_reach.config import Config

# ── configure keys ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigureField:
    """One ``agent-reach configure <cli_key>`` entry the dashboard can submit."""

    cli_key: str
    label: str
    help: str
    #: "secret" renders a password input; "select" a browser picker.
    kind: str = "secret"
    options: Tuple[str, ...] = ()
    #: Config keys this configure command writes (shown as set / not set).
    writes: Tuple[str, ...] = ()


#: The configure keys the dashboard may set. Mirrors the ``configure`` CLI
#: choices; values are always passed to the CLI on stdin (``--stdin``).
CONFIGURE_FIELDS: Dict[str, ConfigureField] = {
    "github-token": ConfigureField(
        "github-token",
        "GitHub token",
        "Optional. A personal access token raises gh API rate limits.",
        writes=("github_token",),
    ),
    "twitter-cookies": ConfigureField(
        "twitter-cookies",
        "Twitter cookies",
        "Paste the Cookie-Editor Header String exported from x.com "
        "(or AUTH_TOKEN and CT0 separated by a space).",
        writes=("twitter_auth_token", "twitter_ct0"),
    ),
    "youtube-cookies": ConfigureField(
        "youtube-cookies",
        "YouTube cookie browser",
        "Browser yt-dlp reads cookies from for age-restricted or member videos.",
        kind="select",
        options=(
            "brave", "chrome", "chromium", "edge", "firefox",
            "opera", "safari", "vivaldi", "whale",
        ),
        writes=("youtube_cookies_from",),
    ),
    "xhs-cookies": ConfigureField(
        "xhs-cookies",
        "Xiaohongshu cookies",
        "Paste the Cookie-Editor export (Header String or JSON) from xiaohongshu.com. "
        "Imported into the xiaohongshu-mcp container when it is running.",
    ),
    "groq-key": ConfigureField(
        "groq-key",
        "Groq API key",
        "Free key from console.groq.com, used for Whisper transcription.",
        writes=("groq_api_key",),
    ),
    "openai-key": ConfigureField(
        "openai-key",
        "OpenAI API key",
        "Optional Whisper provider for agent-reach transcribe.",
        writes=("openai_api_key",),
    ),
    "proxy": ConfigureField(
        "proxy",
        "Network proxy",
        "http://user:pass@host:port. Agents export it as HTTP(S)_PROXY on restricted networks.",
        writes=("proxy",),
    ),
}

#: Keys shown under Settings rather than on a single channel.
GLOBAL_CONFIGURE_KEYS: Tuple[str, ...] = ("proxy", "openai-key", "groq-key")

#: ``configure --from-browser`` choices the dashboard offers. Twitter and
#: Xiaohongshu are Cookie-Editor only (the CLI refuses them), so they are absent.
BROWSER_IMPORT_PLATFORMS: Tuple[str, ...] = ("bilibili", "xueqiu")
BROWSER_CHOICES: Tuple[str, ...] = ("chrome", "firefox", "edge", "brave", "opera")

#: Values accepted by ``install --channels`` that run a real installer.
#: (xueqiu and linkedin are accepted by the CLI but have no install step.)
INSTALLABLE_CHANNELS: Tuple[str, ...] = (
    "bilibili", "boss", "facebook", "instagram",
    "reddit", "twitter", "xiaohongshu", "xiaoyuzhou",
)

#: Channels whose backend comes from the core install (gh, Node, mcporter + Exa).
CORE_INSTALL_CHANNELS: Tuple[str, ...] = ("github", "exa_search")


# ── per-channel metadata ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelMeta:
    display_name: str
    #: (reference file, "## heading" prefix); prefix None = the whole file.
    reference: Optional[Tuple[str, Optional[str]]] = None
    guide: Optional[str] = None
    configure: Tuple[str, ...] = ()
    #: Extra config keys the channel reads that no configure key writes.
    reads: Tuple[str, ...] = ()
    browser_import: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


CHANNEL_META: Dict[str, ChannelMeta] = {
    "github": ChannelMeta("GitHub", ("dev.md", "GitHub"), configure=("github-token",)),
    "twitter": ChannelMeta(
        "Twitter/X", ("social.md", "Twitter/X"), "setup-twitter.md",
        configure=("twitter-cookies",),
    ),
    "youtube": ChannelMeta(
        "YouTube", ("video.md", "YouTube"), configure=("youtube-cookies",),
    ),
    "reddit": ChannelMeta("Reddit", ("social.md", "Reddit"), "setup-reddit.md"),
    "facebook": ChannelMeta("Facebook", ("social.md", "Facebook")),
    "instagram": ChannelMeta("Instagram", ("social.md", "Instagram")),
    "bilibili": ChannelMeta(
        "Bilibili", ("video.md", "Bilibili"),
        reads=("bilibili_sessdata", "bilibili_csrf"), browser_import=True,
    ),
    "xiaohongshu": ChannelMeta(
        "Xiaohongshu", ("social.md", "Xiaohongshu"), "setup-xiaohongshu.md",
        configure=("xhs-cookies",),
    ),
    "linkedin": ChannelMeta("LinkedIn", ("career.md", "LinkedIn")),
    "boss": ChannelMeta("Boss Zhipin", ("career.md", "Boss Zhipin")),
    "xiaoyuzhou": ChannelMeta(
        "Xiaoyuzhou", ("video.md", "Xiaoyuzhou"), "setup-groq.md",
        configure=("groq-key",),
    ),
    "v2ex": ChannelMeta("V2EX", ("social.md", "V2EX")),
    "xueqiu": ChannelMeta(
        "Xueqiu", ("finance.md", None), reads=("xueqiu_cookie",), browser_import=True,
    ),
    "rss": ChannelMeta("RSS", ("web.md", "RSS")),
    "exa_search": ChannelMeta("Exa search", ("search.md", "Exa"), "setup-exa.md"),
    "web": ChannelMeta("Web pages", ("web.md", "General web pages")),
}

_TIER_LABELS = {0: "Zero config", 1: "Needs login or key", 2: "Advanced setup"}

_MAX_SNIPPETS = 8


# ── packaged markdown ────────────────────────────────────────────────────────


@lru_cache(maxsize=None)
def _read_package_text(*parts: str) -> Optional[str]:
    try:
        resource = files("agent_reach")
        for part in parts:
            resource = resource / part
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def extract_section(markdown: str, heading_prefix: Optional[str]) -> str:
    """Return the ``## heading_prefix...`` section of ``markdown``.

    The section runs until the next level-2 heading outside a code fence.
    ``heading_prefix=None`` returns everything after the level-1 title.
    """
    lines = markdown.splitlines()
    out: List[str] = []
    in_fence = False
    capturing = heading_prefix is None
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        if not in_fence and not stripped.startswith("```"):
            if heading_prefix is None and line.startswith("# "):
                continue
            if line.startswith("## "):
                if capturing and heading_prefix is not None:
                    break
                if heading_prefix is not None and line[3:].startswith(heading_prefix):
                    capturing = True
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


_FENCE_RE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")


def extract_snippets(section: str, limit: int = _MAX_SNIPPETS) -> List[Dict[str, str]]:
    """Split the fenced code blocks of a reference section into copyable snippets.

    Shell blocks are split on blank lines; a group's leading ``# comment``
    lines become its title and the remaining lines its command. Other
    languages are kept as one snippet. Text is copied verbatim.
    """
    snippets: List[Dict[str, str]] = []
    heading = ""
    lines = section.splitlines()
    i = 0
    while i < len(lines) and len(snippets) < limit:
        line = lines[i]
        match = _FENCE_RE.match(line.strip())
        if not match:
            if line.startswith("#"):
                heading = line.lstrip("#").strip()
            i += 1
            continue
        lang = match.group(1) or "bash"
        body: List[str] = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("```"):
            body.append(lines[i])
            i += 1
        i += 1  # closing fence
        if lang in ("bash", "sh", "shell"):
            groups: List[List[str]] = [[]]
            for row in body:
                if row.strip():
                    groups[-1].append(row)
                elif groups[-1]:
                    groups.append([])
            for group in groups:
                comments: List[str] = []
                while group and group[0].lstrip().startswith("#"):
                    comments.append(group.pop(0).lstrip()[1:].strip())
                code = "\n".join(group).strip("\n")
                if not code:
                    continue
                title = " ".join(comments) or heading
                snippets.append({"title": title, "code": code, "lang": "bash"})
                if len(snippets) >= limit:
                    break
        else:
            code = "\n".join(body).strip("\n")
            if code:
                snippets.append({"title": heading, "code": code, "lang": lang})
    return snippets


def _section_intro(section: str) -> str:
    """The prose paragraph directly under the section heading, if any.

    Stops at the first sub-heading or code fence so a later, unrelated
    paragraph is never promoted to the channel summary.
    """
    paragraph: List[str] = []
    for line in section.splitlines()[1:] if section.startswith("#") else section.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "```")):
            break
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith((">", "|", "- ", "* ")) or re.match(r"\d+\.\s", stripped):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return " ".join(paragraph)


# ── public API ───────────────────────────────────────────────────────────────


def config_key_states(config: Config, keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """Report each key as set / not set. Never returns the value itself."""
    states = []
    for key in keys:
        in_file = bool(config.data.get(key))
        in_env = not in_file and bool(config.get(key))
        states.append(
            {
                "key": key,
                "set": in_file or in_env,
                "source": "config" if in_file else ("env" if in_env else None),
            }
        )
    return states


def _configure_entry(cli_key: str, config: Config) -> Dict[str, Any]:
    spec = CONFIGURE_FIELDS[cli_key]
    return {
        "cli_key": spec.cli_key,
        "label": spec.label,
        "help": spec.help,
        "kind": spec.kind,
        "options": list(spec.options),
        "keys": config_key_states(config, spec.writes),
        "command": f"agent-reach configure {spec.cli_key}",
    }


def _install_entry(name: str) -> Optional[Dict[str, Any]]:
    if name in INSTALLABLE_CHANNELS:
        return {
            "channels": [name],
            "command": f"agent-reach install --env=auto --system --channels={name}",
        }
    if name in CORE_INSTALL_CHANNELS:
        return {"channels": [], "command": "agent-reach install --env=auto --system"}
    return None


def channel_catalog(config: Config) -> List[Dict[str, Any]]:
    """Static metadata for every registered channel, in registry order."""
    entries = []
    for ch in get_all_channels():
        meta = CHANNEL_META.get(ch.name) or ChannelMeta(ch.name)
        section = ""
        if meta.reference:
            ref_file, prefix = meta.reference
            text = _read_package_text("skill", "references", ref_file) or ""
            section = extract_section(text, prefix)
        guide = _read_package_text("guides", meta.guide) if meta.guide else None

        keys: Tuple[str, ...] = ()
        for cli_key in meta.configure:
            keys += CONFIGURE_FIELDS[cli_key].writes
        keys += meta.reads

        browser_import = None
        if meta.browser_import:
            browser_import = {
                "platform": ch.name,
                "browsers": list(BROWSER_CHOICES),
                "command": f"agent-reach configure --from-browser BROWSER --platform {ch.name}",
            }

        entries.append(
            {
                "name": ch.name,
                "display_name": meta.display_name,
                "description": ch.description,
                "about": _section_intro(section),
                "tier": ch.tier,
                "tier_label": _TIER_LABELS.get(ch.tier, f"Tier {ch.tier}"),
                "needs_login": ch.tier > 0,
                "backends": list(ch.backends),
                "config_keys": config_key_states(config, keys),
                "configure": [_configure_entry(k, config) for k in meta.configure],
                "browser_import": browser_import,
                "install": _install_entry(ch.name),
                "guide": guide,
                "reference": section or None,
                "snippets": extract_snippets(section),
            }
        )
    return entries


def global_settings(config: Config) -> List[Dict[str, Any]]:
    return [_configure_entry(k, config) for k in GLOBAL_CONFIGURE_KEYS]
