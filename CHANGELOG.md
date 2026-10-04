# Changelog

All notable changes to this project will be documented in this file.

---

## [Unreleased]

### 🐛 Bug Fixes

#### 🔐 Boss Zhipin — login state misdetection (dual credential stores)

- **Root cause:** Boss has two login-state stores that do not reflect each other: the local `~/.boss-agent/auth/session.enc`
  and the browser cookies inside the dedicated Chrome profile. `boss status` / `status --live` **only validate the former**
  (a legacy credential store from the Bridge/httpx era), while search under the strict `existing-browser` CDP mode uses the browser cookies.
  With "stale local credentials + browser not logged in", `boss status` reports `logged_in: true`, misleading the agent
  into skipping login and searching directly, which ends in `AUTH_EXPIRED`. The old runbook also forbade treating `_security_check`
  as "not logged in"; the two rules combined pushed the agent toward the wrong "anti-bot slider" branch.
- **Fix:** `check()` gains a 4th read-only probe layer, `_cdp_zhipin_login_cookie()`, which uses a minimal WebSocket client
  built on the standard library to ask the CDP browser itself (`Storage.getCookies`) whether a zhipin
  `wt2` cookie exists, **treating the browser as the source of truth**, with no new dependency, no browser launch and no search run.
  No wt2 means it clearly reports "browser not logged in, search will return AUTH_EXPIRED" and points to "user logs in +
  `login --cdp`"; a failed probe reports "login state unknown"; the security-check page hint also carries the browser cookie status.
- **Runbook correction:** Removed the misleading rule "only trust `boss status` to judge login state"; the doctor's
  browser cookie probe is now authoritative. After launching the dedicated Chrome, the agent must **pause and let the user visually confirm** the login state;
  `AUTH_EXPIRED` is ground truth (go straight to the login flow, never interpret it as a security check), and
  `_security_check` is treated as a slider only when there is no `AUTH_EXPIRED`.

### ✨ Features

#### 🎯 Boss Zhipin channel

- Added the `boss` channel: search jobs and fetch full JD text through boss-agent-cli + a real Chrome over CDP.
- `check()` runs four read-only probe layers (is boss-agent-cli installed → is port 9222 reachable → is there a zhipin tab
  → is there a `wt2` login cookie in the browser).
- Fetching uses the public API (`search_jobs` + `job_card_browser` + `browser_source="existing-browser"`).
- `agent-reach install --system --channels=boss` can install a backend pinned to a fixed commit of upstream boss-agent-cli
  (#403–#407 merged into master); it will switch back to a version constraint once upstream cuts a release.
- The skill and install guide support "set up Boss Zhipin for me": the agent launches a dedicated Chrome listening only on the loopback address,
  the user only logs in manually, and finally the agent verifies the login state and the CDP link.
- The pinned dependency moved from the fork snapshot `8ff6bd3` to upstream commit `4c991b7` (#403–#407 merged into
  master): a logged-in CDP session can be reused directly, and search can use `--browser-source existing-browser`
  to forbid headless downgrade.
- Code 37 is classified by its original message: an environment anomaly is `ENVIRONMENT_RISK` and stops immediately; only an explicit
  token/stoken expiry allows a single refresh. The dedicated Chrome profile should be reused long-term and used at a lower frequency.

## [1.3.1] - 2026-03-27

### 🐛 Bug Fixes

#### 📈 Xueqiu — comprehensive fix

- **Fixed the root cause of the 400 error:** `_ensure_cookies()` only visited the homepage and so only got `acw_tc` (an anti-DDoS token), while `xq_a_token` is generated dynamically by Xueqiu's frontend JS and cannot be obtained with plain HTTP requests. Added a three-level cookie loading strategy: (1) read the config file (saved by `--from-browser`) → (2) extract automatically from local Chrome (requires browser-cookie3) → (3) homepage fallback
- **Fixed User-Agent:** `"agent-reach/1.0"` was detected and rejected by Xueqiu's anti-scraping system; replaced with a real Chrome UA
- **Fixed missing `Referer` header:** all API requests now send `Referer: https://xueqiu.com/`
- **Fixed the `get_hot_posts()` endpoint:** the old endpoint `/statuses/hot/listV3.json` was deprecated (returns an empty body); switched to `/v4/statuses/public_timeline_by_category.json`, correctly parsing the `item.data` JSON string for author/likes/text
- **Fixed `urllib.request.quote` → `urllib.parse.quote`:** now uses the correct module explicitly
- **Fixed `configure --from-browser` not extracting Xueqiu cookies:** `PLATFORM_SPECS` now includes Xueqiu, and the cookie is saved only if `xq_a_token` is present
- **Corrected misleading docs:** "no configuration needed" / "public API, no login required" in README/SKILL.md → now accurately states that a browser cookie is required
- **Improved error message:** when `check()` fails it now suggests `configure --from-browser chrome` instead of "may need a proxy"

---

## [1.3.0] - 2026-03-12

### 🆕 New Channels

#### 💻 V2EX
- Hot topics, node topics, topic detail + replies, user profile via public JSON API
- Zero config — no auth, no proxy, no API key required
- `get_hot_topics(limit)`, `get_node_topics(node_name, limit)`, `get_topic(id)`, `get_user(username)`

### 📈 Improvements

- Channel count: 14 → 15

---

## [1.1.0] - 2025-02-25

### 🆕 New Channels

#### ~~📷 Instagram~~ (removed — upstream blocked)
- ~~Read public posts and profiles via [instaloader](https://github.com/instaloader/instaloader)~~
- **Removed:** Instagram's aggressive anti-scraping measures broke all available open-source tools (instaloader, etc.). See [instaloader#2585](https://github.com/instaloader/instaloader/issues/2585). Will re-add when upstream recovers.

#### 💼 LinkedIn
- Read person profiles, company pages, and job details via [linkedin-scraper-mcp](https://github.com/stickerdaniel/linkedin-mcp-server)
- Search people and jobs via MCP, with Exa fallback
- Fallback to Jina Reader when MCP is not configured

#### 🏢 Boss Zhipin
- QR code login via [mcp-bosszp](https://github.com/mucsbr/mcp-bosszp)
- Job search and recruiter greeting via MCP
- Fallback to Jina Reader for reading job pages

### 📈 Improvements

- Channel count: 9 → 12
- `agent-reach doctor` now detects all 12 channels
- CLI: added `search-linkedin`, `search-bosszhipin` subcommands
- Updated install guide with setup instructions for new channels

---

## [1.0.0] - 2025-02-24

### 🎉 Initial Release

- 9 channels: Web, Twitter/X, YouTube, Bilibili, GitHub, Reddit, XiaoHongShu, RSS, Exa Search
- CLI with `read`, `search`, `doctor`, `install` commands
- Unified channel interface — each platform is a single pluggable Python file
- Auto-detection of local vs server environments
- Built-in diagnostics via `agent-reach doctor`
- Skill registration for Claude Code / OpenClaw / Cursor
