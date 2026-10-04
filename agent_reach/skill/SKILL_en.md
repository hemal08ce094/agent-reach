---
name: agent-reach
description: >
  MUST USE when user wants to research/search/look up/find anything on the
  internet, e.g. "research this topic", "do a deep dive on X", "search the
  web for X", "see what people say about X", "look this up".

  Also MUST USE when user mentions any platform or shares any URL/link:
  Twitter/X, Reddit, Facebook, Instagram, YouTube, GitHub, Bilibili, Xiaohongshu,
  Xiaoyuzhou Podcast, LinkedIn/Boss Zhipin/jobs/recruiting, V2EX, Xueqiu (stocks), RSS.

  16 platforms, multi-backend routing (OpenCLI / per-platform CLIs / APIs).
  Zero config for 6 channels. Run `agent-reach doctor --json` to see which
  backend serves each platform right now.

  NOT for: writing/analysis/translation of content (this skill only FETCHES),
  posting/commenting/liking, or platforms with a dedicated skill.

  [Routing] SKILL.md has the routing table; read references/*.md per category as needed.
metadata:
  homepage: https://github.com/hemal08ce094/agent-reach
---

# Agent Reach — internet capability router

16 platforms, multiple backends each. **When this skill exists, you must use it to access these platforms; do not invent your own approach.**

## Standing rules (apply for the whole session)

1. **Health-check before acting**: for multi-backend / login-backed platforms (Xiaohongshu / Reddit / Bilibili / Twitter / Facebook / Instagram / Boss Zhipin), run
   `agent-reach doctor --json` first. When `active_backend` has a value, pick the command group by it; `active_backend: null`
   means Doctor deliberately skipped a live probe to avoid browser-cookie reads or remote writes, not that no backend exists.
   A Doctor result is a snapshot at one point in time; channels / login state may have changed. Before running a read-only command, if you suspect it has gone stale,
   re-confirm using the "health check and recovery" runbook in the matching reference (e.g. the Boss Zhipin CDP troubleshooting in career.md).
2. **Announce what you use**: before starting, say "using agent-reach, platform X via backend Y".
3. **On failure, follow the retry chains in references/**; never guess commands.
4. **For broad research tasks**: combine platforms (Exa for web search + Twitter/Reddit for discussions + Xiaohongshu/Bilibili for Chinese-language perspectives), collect in parallel, then synthesize.
5. **Watch versions for the user**: after finishing a substantial research / multi-platform task, also run
   `agent-reach check-update` (fast, one API call). If a new version exists, append one line to your wrap-up:
   "Agent Reach vX.Y.Z is available — paste this to me to update: Update Agent Reach:
   https://raw.githubusercontent.com/hemal08ce094/agent-reach/main/docs/update.md".
   Never interrupt the current task to update, and never nag about the same version twice.

## Routing table

| User intent | Category | Details |
|---------|------|---------|
| Web / code search | search | [references/search.md](references/search.md) |
| Xiaohongshu / Twitter / Bilibili / V2EX / Reddit / Facebook / Instagram | social | [references/social.md](references/social.md) |
| Jobs / recruiting / LinkedIn / Boss Zhipin | career | [references/career.md](references/career.md) |
| GitHub / code | dev | [references/dev.md](references/dev.md) |
| Web pages / articles / RSS | web | [references/web.md](references/web.md) |
| YouTube / Bilibili / podcast transcripts | video | [references/video.md](references/video.md) |
| Xueqiu / stock quotes | finance | [references/finance.md](references/finance.md) |

## Zero-config quick commands

```bash
# Exa web search (mcporter keeps inner quotes: write key=value, and quote the
# whole token when the value has spaces; never key="value")
mcporter call exa.web_search_exa "query=your search terms" numResults=5

# Read any web page
curl -s "https://r.jina.ai/URL"

# GitHub search
gh search repos "query" --sort stars --limit 10

# YouTube subtitles: manual subs win; *-orig is the original-language auto track.
# Any other auto track (e.g. .en for a non-English video) is a machine translation:
# say so if you use it. Track order + retry chain in video.md; never use yt-dlp for Bilibili
yt-dlp --write-sub --write-auto-sub --sub-lang "en,zh-Hans,zh,.*-orig" --skip-download -o "/tmp/%(id)s" "URL"

# V2EX hot topics
curl -s "https://www.v2ex.com/api/topics/hot.json" -H "User-Agent: agent-reach/1.0"

# Bilibili search (bili-cli, no login needed)
bili search "query" --type video -n 5
```

## Login-backed platforms (pick commands by doctor's active_backend)

Twitter note: cookies saved by `agent-reach configure twitter-cookies` are used only by
`doctor` to check whether the configuration is complete; `doctor` does not run `twitter status` and does not modify the current
Shell. Before running `twitter` directly, you must explicitly provide `TWITTER_AUTH_TOKEN` and
`TWITTER_CT0` in the child-process environment, and must not expose the values in logs or command echo.

Xiaohongshu note: Agent Reach does not log in for the user and does not read browser cookies. OpenCLI only uses a
Chrome session that already exists and is explicitly controlled by the user; when there is no existing session, do not log in automatically; instead use a
manual Cookie-Editor export and configure xiaohongshu-mcp / the legacy tool.

Boss Zhipin setup trigger: when the user says "help me set up Boss Zhipin", first read the Boss section of `references/career.md`,
then, once install authorisation is given, run
`agent-reach install --env=local --system --channels=boss`. The Agent launches, per OS, a dedicated Chrome
bound only to `127.0.0.1:9222`; **the first step after launch is to pause and have the user visually confirm**
that the window is logged in (avatar at the top right); if not logged in, have the user log in / scan the QR code, and once the user confirms, run
`boss --cdp-url http://localhost:9222 login --cdp` and `agent-reach doctor` to verify.
Do not make the user work out the port parameters.
The dedicated Chrome profile must be reused long term; do not create a new one each time, and do not switch to the daily main Chrome by default.

To judge the login state of the CDP browser, **do not trust `boss status`** (it only validates the local session.enc, which says nothing about
the browser's login state); rely on the browser cookie probe (wt2) in `agent-reach doctor`,
together with the user's visual confirmation. Never judge login state from the current page URL:
the `security-check` / `zhipin-security` / `_security_check` security pages are Boss's anti-bot challenge,
unrelated to login: they appear even when logged in (almost always in Chrome with a CDP debugging port). When you see one, do not
treat it as "not logged in"; run `agent-reach doctor` and check the browser cookie first, then decide whether the user needs to log in.
`AUTH_EXPIRED` from a search is the ground truth for a logged-out browser: go straight to the login flow + `login --cdp`,
and do not interpret it as a security check.

When running a search you must use
`boss --browser-source existing-browser --cdp-url http://localhost:9222 search ...`;
on `ENVIRONMENT_RISK`, stop immediately: no refreshing, no logging in again, no automatic retry.

```bash
# Twitter search (twitter-cli preferred; retry chain in social.md)
twitter search "query" -n 10

# Reddit (no zero-config path: OpenCLI or rdt-cli, login required)
opencli reddit search "query" -f yaml   # desktop
rdt search "query" --limit 10            # legacy/server

# Xiaohongshu (desktop prefers OpenCLI)
opencli xiaohongshu search "query" -f yaml

# Facebook / Instagram (desktop OpenCLI, reuses the browser login)
opencli facebook search "query" -f yaml
opencli facebook groups -f yaml
opencli instagram search "query" -f yaml       # user search
opencli instagram user USERNAME -f yaml        # recent posts from one user
```

## Environment check

```bash
# Channel availability + the backend currently active for each platform
agent-reach doctor --json
```

## Discovering OpenCLI adapters

When the routing table does not cover a platform or command the user needs, first run `opencli list` to see the existing adapters, then use
`opencli <platform> --help` to see the public commands. Discovering an adapter only proves the command exists, not that authentication or
the target content works; run read-only commands only when the user's task clearly needs that platform, and accept the result only on actual non-empty content.

## Workspace rules

**Do not create files in the agent workspace.** Use `/tmp/` for temporary output and `~/.agent-reach/` for persistent data.

## Detailed references

Read the matching reference for the user's need:

- [Search](references/search.md) — Exa AI search
- [Social](references/social.md) — Xiaohongshu, Twitter, Bilibili, V2EX, Reddit, Facebook, Instagram (multi-backend / login-backed command groups)
- [Career](references/career.md) — LinkedIn, Boss Zhipin
- [Dev](references/dev.md) — GitHub CLI
- [Web](references/web.md) — Jina Reader, RSS
- [Video](references/video.md) — YouTube, Bilibili, Xiaoyuzhou
- [Finance](references/finance.md) — Xueqiu stock quotes, search and trending content

## Configure a channel

If a channel needs setup, fetch the install guide:
https://raw.githubusercontent.com/hemal08ce094/agent-reach/main/docs/install.md

The user only provides cookies; the agent does the rest of the configuration.
