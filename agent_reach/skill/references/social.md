# Social Media & Communities

Xiaohongshu (RedNote), Twitter/X, Bilibili, V2EX, Reddit, Facebook, Instagram.

## Xiaohongshu / RedNote (multi-backend)

Xiaohongshu has three backends. **Run `agent-reach doctor --json` first and check which one xiaohongshu's `active_backend` is**, then use the matching command group.

### Backend A: OpenCLI (preferred on desktop)

```bash
# Search notes
opencli xiaohongshu search "query" -f yaml

# Read note body + engagement stats (use the full URL from search results, including xsec_token)
opencli xiaohongshu note "NOTE_URL" -f yaml

# Comments (nested replies supported)
opencli xiaohongshu comments NOTE_ID -f yaml

# Home recommendation feed
opencli xiaohongshu feed -f yaml

# Public notes on a user's profile
opencli xiaohongshu user USER_ID -f yaml
```

> Requires Chrome to be open with the OpenCLI extension installed. OpenCLI only uses a Chrome
> session that already exists and is explicitly controlled by the user; Agent Reach does not log in for the user or read browser cookies.
> `agent-reach configure xhs-cookies` does not inject cookies into OpenCLI.
> If there is no existing session, do not log in automatically; use backend B/C instead and configure it with the
> manual Cookie-Editor export flow.

### Backend B: xiaohongshu-mcp (server scenario)

> mcporter keeps quotes that reach it as part of the value, so write `key=value` and quote the whole token when the value has spaces (`"keywords=AI engineer"`), never `key="value"`; `--args '{"key": "value"}'` also works.

```bash
# Before authenticating, have the user export cookies manually with Cookie-Editor, then import them explicitly
agent-reach configure xhs-cookies

# Read-only check of the current state
mcporter call xiaohongshu.check_login_status --timeout 120000

# Search
mcporter call xiaohongshu.search_feeds keyword=query --timeout 120000

# Note details + comments (take feed_id and xsec_token from the search results)
mcporter call xiaohongshu.get_feed_detail feed_id=... xsec_token=... --timeout 120000
```

> The first call automatically downloads a headless browser of about 150MB, so always pass `--timeout 120000`.
> Authenticate only through a manual Cookie-Editor export; after importing, run `check_login_status` first.
> This explicit command saves/imports the set of same-domain xiaohongshu.com cookies the user provides, and the user should
> confirm the scope; cookies not on the xiaohongshu.com domain are ignored.

### Backend C: xhs-cli (legacy fallback, upstream unmaintained since 2026-03)

```bash
xhs search "query"          # search
xhs read NOTE_ID_OR_URL     # read a note (must use the URL/ID from search results, not a bare note_id)
xhs comments NOTE_ID_OR_URL # comments
xhs hot                     # trending
xhs feed                    # recommended
```

> Known to be unstable: `xhs user` / `xhs user-posts` / `xhs favorites` may return an API error (upstream is unmaintained and nobody is fixing it). New users should go straight to backend A/B.

### General notes

> **Authentication boundary**: Agent Reach must not perform a Xiaohongshu login for the user, and must not read browser
> cookies. OpenCLI may only use a Chrome session that already exists and is explicitly controlled by the user;
> xiaohongshu-mcp / legacy tools use a manual Cookie-Editor export.
>
> **xsec_token restriction**: Xiaohongshu enforces an xsec_token mechanism, so **a bare note_id cannot be used to read a note directly**. Correct flow: search/feed first to get results, then read using the full URL/ID from those results. This is the same for all three backends.
>
> **Rate limiting**: high-frequency requests (bulk search, deep comment paging) trigger captchas, and the platform limit cannot be bypassed. Leave 2-3 seconds between operations.
>
> **Write operations (posting/commenting/liking)**: read-only is recommended. Write operations in xhs-cli v0.6.x may return 406 because of signing problems.

## Twitter/X (twitter-cli)

### Authentication prerequisites

Cookies saved through hidden input by `agent-reach configure twitter-cookies` are used only by
`agent-reach doctor` to check whether the explicit credentials are complete. `doctor` does not run the upstream
`twitter status` and does not modify the current Shell. Before running any `twitter` command below,
you must explicitly provide these in the same Shell or child-process environment:

```bash
export TWITTER_AUTH_TOKEN="..."
export TWITTER_CT0="..."
```

### Stable commands

```bash
# Home timeline (most stable)
twitter feed -n 20

# Read a single tweet (with replies)
twitter tweet URL_OR_ID

# Read a long post / X Article
twitter article URL_OR_ID

# User timeline
twitter user-posts @username -n 20

# User profile
twitter user @username
```

### Possibly unstable commands

```bash
# Search tweets (Twitter changes its GraphQL endpoints often, may 404)
twitter search "query" -n 10

# likes (since 2024 you can only see your own, a platform restriction)
twitter likes
```

### Retry chain when search fails (run in order, stop on success)

1. Retry once directly (occasional failures are common): `twitter search "query" -n 10`
2. Upgrade and try again: `pipx upgrade twitter-cli && twitter search "query" -n 10`
3. Switch to the OpenCLI alternative (desktop, reuses the browser login): `opencli twitter search "query" -f yaml`
4. If none of these work, route around it with stable commands such as `twitter feed` / `twitter user-posts @somebody`

### Important notes

> **Install**: `pipx install twitter-cli` (make sure it is v0.8.5+)
>
> **Authentication**: use only a manual Cookie-Editor export, then explicitly set the environment variables
> `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`; do not rely on automatic browser reading.
>
> **IP risk control**: do not call frequently from VPS/data-center IPs, especially followers/following, as there is a risk of account bans. Use a residential proxy or a local environment.
>
> **OpenCLI alternative**: if OpenCLI is installed on the desktop, the full set `opencli twitter search/article/user-posts -f yaml` works (browser login state, no cookie environment variables needed).
>
> **Output format**: prefer `--yaml` or `--json` for structured output, which is friendlier to AI agents.

## Bilibili

> ⚠️ **Do not use yt-dlp for Bilibili** (risk control now blocks it everywhere with HTTP 412; tested, no workaround). Use bili-cli / OpenCLI.

```bash
# Search / trending / video details (bili-cli, read-only, no login needed)
bili search "query" --type video -n 5
bili hot -n 10
bili video BVxxx

# Subtitles (OpenCLI, needs desktop Chrome)
opencli bilibili subtitle BVxxx
```

> For detailed commands (audio transcription, direct-API fallback) see [references/video.md](video.md).

## V2EX (public API)

No authentication needed; call the public API directly.

### Hot topics

```bash
curl -s "https://www.v2ex.com/api/topics/hot.json" -H "User-Agent: agent-reach/1.0"
```

### Node topics

```bash
# node_name examples: python, tech, jobs, qna, programmers
curl -s "https://www.v2ex.com/api/topics/show.json?node_name=python&page=1" -H "User-Agent: agent-reach/1.0"
```

### Topic details

```bash
# get topic_id from the URL, e.g. https://www.v2ex.com/t/1234567
curl -s "https://www.v2ex.com/api/topics/show.json?id=TOPIC_ID" -H "User-Agent: agent-reach/1.0"
```

### Topic replies

```bash
curl -s "https://www.v2ex.com/api/replies/show.json?topic_id=TOPIC_ID&page=1" -H "User-Agent: agent-reach/1.0"
```

### User info

```bash
curl -s "https://www.v2ex.com/api/members/show.json?username=USERNAME" -H "User-Agent: agent-reach/1.0"
```

### Python usage example

```python
from agent_reach.channels.v2ex import V2EXChannel

ch = V2EXChannel()

# Get hot topics
topics = ch.get_hot_topics(limit=10)
for t in topics:
    print(f"[{t['node_title']}] {t['title']} ({t['replies']} replies)")

# Get node topics
node_topics = ch.get_node_topics("python", limit=5)

# Get topic details + replies
topic = ch.get_topic(1234567)
print(topic["title"], "—", topic["author"])

# Get user info
user = ch.get_user("Livid")
```

> **Node list**: https://www.v2ex.com/planes

## Reddit (multi-backend, login required)

**Reddit has no zero-config path**: the anonymous `.json` endpoints are blocked (403), and since 2025-11 the official API requires manual approval that is almost never granted. Both backends rely on a logged-in session; run `agent-reach doctor --json` first and check reddit's `active_backend`. Access from mainland China needs a proxy.

### Backend A: OpenCLI (preferred on desktop, reuses the browser login)

```bash
# Search posts
opencli reddit search "query" -f yaml

# Read the full post + comments
opencli reddit read POST_ID -f yaml

# Browse a subreddit / hot / Popular
opencli reddit subreddit LocalLLaMA -f yaml
opencli reddit hot -f yaml
opencli reddit popular -f yaml

# subreddit metadata (subscriber count, description)
opencli reddit subreddit-info LocalLLaMA -f yaml
```

> Requires Chrome to be open and logged in to reddit.com in the browser.

### Backend B: rdt-cli (legacy/server fallback, upstream unmaintained since 2026-03)

```bash
rdt search "query" --limit 10   # search posts
rdt read POST_ID                # read the full post + comments
rdt sub python --limit 20       # browse a subreddit
rdt popular --limit 10          # browse popular
rdt all --limit 10              # browse /r/all
```

> **Install**: `pipx install 'git+https://github.com/public-clis/rdt-cli.git'` (the PyPI version is outdated; install v0.4.2+ from GitHub). Run `rdt login` first to be able to search and read (on a server without a browser, write the cookie manually; see the doctor hint).
> Prefer `--yaml` output, which is friendlier to AI agents.

### Advanced option: official API + PRAW (only for users who already have credentials)

Users who registered a Reddit script app before 2025-11 (holding a client_id/client_secret) can use PRAW with the official API (100 QPM, free). New applications need manual approval and personal projects are almost never approved, so **do not recommend this route to new users**.

## Facebook (OpenCLI, login required)

Facebook goes through OpenCLI, reusing the user's facebook.com login in Chrome. Run `agent-reach doctor --json` first and check facebook's `active_backend`, which should normally be `OpenCLI`. Do not recommend Jina/Exa/Graph API as the default path.

```bash
# Search users / pages / posts
opencli facebook search "query" -f yaml

# User or page info
opencli facebook profile zuck -f yaml

# Current account's News Feed
opencli facebook feed --limit 10 -f yaml

# Group list / recent activity visible to the current account
opencli facebook groups --limit 20 -f yaml
```

> Requires Chrome to be open with the OpenCLI extension installed and logged in to facebook.com. Facebook Groups currently only promises reading the group list / recent activity visible to the current account, not an API for arbitrary group posts and comments.

## Instagram (OpenCLI, login required)

Instagram goes through OpenCLI, reusing the user's instagram.com login in Chrome. Run `agent-reach doctor --json` first and check instagram's `active_backend`, which should normally be `OpenCLI`. Do not restore instaloader by default; historically its cookies/401/429 behaviour was unstable.

```bash
# Search users (not a site-wide post keyword search)
opencli instagram search "query" -f yaml

# User profile
opencli instagram profile nasa -f yaml

# User's recent posts
opencli instagram user nasa --limit 12 -f yaml

# Explore / Discover
opencli instagram explore --limit 20 -f yaml

# Current account's saved items
opencli instagram saved --limit 20 -f yaml
```

> Requires Chrome to be open with the OpenCLI extension installed and logged in to instagram.com. `instagram search` is a user search; to read posts, first determine the username, then use `instagram user USERNAME`. On 429 / login required, have the user log in to Chrome again and lower the request rate.
