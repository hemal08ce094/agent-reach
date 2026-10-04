# -*- coding: utf-8 -*-
"""Web — any URL via Jina Reader. Always available."""

import codecs
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit

from agent_reach.utils.url import normalize_public_http_url

from .base import Channel

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ANTIBOT_SCAN_BYTES = 4096
_JINA_ROOT = "https://r.jina.ai/"
_CHECK_TIMEOUT_SECONDS = 3
# Characters RFC 3986 allows unescaped in a path / query (plus "%" so that
# already-encoded sequences are preserved instead of double-encoded).
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"


def _to_ascii_url(url: str) -> str:
    """Return *url* with an IDNA host and percent-encoded path/query/fragment.

    ``http.client`` refuses non-ASCII request targets, so URLs such as
    ``https://zh.wikipedia.org/wiki/中文`` must be encoded before they are
    appended to the Jina Reader prefix.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        raise ValueError("only public HTTP(S) URLs are allowed") from None
    if ":" in ascii_host:  # IPv6 literal
        ascii_host = f"[{ascii_host}]"
    netloc = ascii_host if parts.port is None else f"{ascii_host}:{parts.port}"
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            quote(parts.path, safe=_PATH_SAFE),
            quote(parts.query, safe=_QUERY_SAFE),
            quote(parts.fragment, safe=_QUERY_SAFE),
        )
    )


def _decode_body(body: bytes, charset) -> str:
    """Decode with the declared charset when valid, never raising on bad bytes."""
    encoding = "utf-8"
    if isinstance(charset, str) and charset:
        try:
            encoding = codecs.lookup(charset).name
        except LookupError:
            encoding = "utf-8"
    return body.decode(encoding, errors="replace")


def _is_antibot_page(body: bytes) -> bool:
    """Recognize high-confidence Jina/Cloudflare challenge responses."""
    sample = body[:_ANTIBOT_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()

    jina_captcha_warning = "warning:" in sample and "requiring captcha" in sample
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = "title: attention required! | cloudflare" in sample and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (jina_captcha_warning and challenge_structure) or cloudflare_block


def _probe_jina() -> Optional[BaseException]:
    """HEAD the Jina Reader root; return the failure, or None when reachable."""
    req = urllib.request.Request(_JINA_ROOT, method="HEAD", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as exc:
        exc.close()  # any HTTP status means the service answered
    except Exception as exc:
        return exc
    return None


class WebChannel(Channel):
    name = "web"
    description = "Any web page"
    backends = ["Jina Reader"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return True  # Fallback — handles any URL

    def check(self, config=None):
        # Cheap reachability probe: a HEAD to the Jina Reader root with a short
        # timeout. Any HTTP answer proves the service is reachable; a DNS/TCP
        # failure (e.g. r.jina.ai blocked without a proxy, #685) is a warning.
        failure = _probe_jina()
        if failure is not None:
            from agent_reach.utils.text import scrub_url_credentials

            self.active_backend = None
            detail = scrub_url_credentials(failure).rstrip(": ")
            return "warn", (
                f"Jina Reader (r.jina.ai) is unreachable: {detail}. "
                "Reading arbitrary web pages will fail until it is reachable "
                "(configure a proxy if you are on a restricted network)."
            )
        self.active_backend = self.backends[0]
        return "ok", "Reads any web page via Jina Reader (curl https://r.jina.ai/URL)"

    def read(self, url: str) -> str:
        """Read a web page via Jina Reader and return the full Markdown."""
        url = _to_ascii_url(normalize_public_http_url(url))
        jina_url = f"{_JINA_ROOT}{url}"
        req = urllib.request.Request(
            jina_url,
            headers={"User-Agent": _UA, "Accept": "text/plain"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(_MAX_RESPONSE_BYTES + 1)
            headers = getattr(resp, "headers", None)
            get_charset = getattr(headers, "get_content_charset", None)
            charset = get_charset() if callable(get_charset) else None
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Jina Reader response exceeds {_MAX_RESPONSE_BYTES} byte limit"
            )
        if _is_antibot_page(body):
            raise RuntimeError(
                "Jina Reader returned an anti-bot verification page instead of the "
                "target content; use a site-specific tool or a browser instead"
            )
        return _decode_body(body, charset)
