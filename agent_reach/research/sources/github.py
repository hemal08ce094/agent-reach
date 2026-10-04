# -*- coding: utf-8 -*-
"""GitHub through the gh CLI (skill/references/dev.md)."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from agent_reach.research.models import ReadResult, ResearchItem, make_item, make_read
from agent_reach.research.parse import load_structured, pick
from agent_reach.research.runner import SourceFailure
from agent_reach.research.sources.base import Context, Source, require_backend

_REPO_FIELDS = "fullName,description,url,stargazersCount,forksCount,updatedAt,language,owner"
_ISSUE_FIELDS = "title,body,author,comments,createdAt,url,state"
_NAME = r"[A-Za-z0-9_.-]{1,100}"
_PATH_RE = re.compile(rf"^/({_NAME})/({_NAME})(?:/(issues|pull)/(\d{{1,9}}))?/?$")


def parse_github_url(url: str) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """(owner, repo, kind, number) for repo, issue and pull request URLs."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    match = _PATH_RE.match(parts.path)
    if not match:
        return None
    owner, repo, kind, number = match.groups()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if owner in (".", "..") or repo in (".", ".."):
        return None
    return owner, repo, kind, number


class GitHubSource(Source):
    name = "github"
    label = "GitHub"
    channel = "github"
    backends = ("gh CLI",)
    hint = "Repositories via gh search"

    def search_argv(self, query: str, limit: int, backend: str) -> List[str]:
        return [
            "gh", "search", "repos", "--sort", "stars", "--limit", str(limit),
            "--json", _REPO_FIELDS, "--", query,
        ]

    def search(self, query: str, limit: int, ctx: Context) -> List[ResearchItem]:
        backend = require_backend(self, ctx.backend)
        data = load_structured(ctx.run(self.search_argv(query, limit, backend)).stdout)
        if not isinstance(data, list):
            raise SourceFailure("gh returned an unexpected shape (expected a list)")
        items = []
        for rec in data[:limit]:
            if not isinstance(rec, dict):
                continue
            item = make_item(
                self.name,
                title=pick(rec, "fullName", "name"),
                url=pick(rec, "url"),
                snippet=pick(rec, "description"),
                author=pick(rec, "owner.login"),
                published=pick(rec, "updatedAt"),
                metrics={"stars": pick(rec, "stargazersCount"), "forks": pick(rec, "forksCount")},
                extra={"language": pick(rec, "language")},
                native_id=pick(rec, "fullName"),
            )
            if item is not None:
                items.append(item)
        return items

    def can_read(self, url: str) -> bool:
        return parse_github_url(url) is not None

    def read(self, url: str, ctx: Context) -> ReadResult:
        require_backend(self, ctx.backend)
        parsed = parse_github_url(url)
        if parsed is None:
            raise SourceFailure("not a GitHub repository, issue or pull request link")
        owner, repo, kind, number = parsed
        slug = f"{owner}/{repo}"
        if kind is None:
            out = ctx.run(["gh", "repo", "view", "--", slug]).stdout
            return make_read(
                title=slug, url=url, source=self.name, text=out, fmt="text",
                via="gh CLI", command=ctx.commands[-1],
            )
        verb = "issue" if kind == "issues" else "pr"
        data = load_structured(
            ctx.run(["gh", verb, "view", str(number), "-R", slug, "--json", _ISSUE_FIELDS]).stdout
        )
        if not isinstance(data, dict):
            raise SourceFailure("gh returned an unexpected shape")
        parts = [str(data.get("body") or "*No description.*")]
        raw_comments = data.get("comments")
        comments: list = raw_comments if isinstance(raw_comments, list) else []
        if comments:
            parts.append("## Comments")
            for c in comments[:100]:
                if isinstance(c, dict):
                    who = pick(c, "author.login") or "someone"
                    parts.append(f"**{who}**\n\n{c.get('body') or ''}")
        return make_read(
            title=f"{pick(data, 'title') or slug} ({slug}#{number})",
            url=url,
            source=self.name,
            text="\n\n".join(parts),
            via="gh CLI",
            command=ctx.commands[-1],
            metadata={
                "author": pick(data, "author.login"),
                "state": pick(data, "state"),
                "created": pick(data, "createdAt"),
                "comments": str(len(comments)),
            },
        )

    def follow_up(self, url: str, backend: Optional[str]) -> List[str]:
        parsed = parse_github_url(url)
        if parsed is None:
            return []
        owner, repo, kind, number = parsed
        slug = f"{owner}/{repo}"
        if kind is None:
            return [f"gh repo view {slug}", f"gh issue list -R {slug} --state open"]
        verb = "issue" if kind == "issues" else "pr"
        return [f"gh {verb} view {number} -R {slug} --comments"]
