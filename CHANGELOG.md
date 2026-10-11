# Changelog

All notable changes to this project will be documented in this file.

---

## Unreleased

### Added

- **Local web dashboard: `agent-reach ui [--port N] [--no-open] [--host 127.0.0.1|localhost]`.**
  Served from the package with the standard library (no new dependencies, no
  CDN). It shows doctor results with a health bar, groups channels into Needs
  attention / Ready / Not set up, renders each channel's setup guide, reports
  config keys as set / not set, saves keys and cookies through
  `configure <key> --stdin`, runs allowlisted jobs (install, skill
  install/uninstall, check-update, Bilibili/Xueqiu browser cookie import) with
  live output, re-tests a single channel, and offers copyable agent commands
  taken from the skill references. Keyboard navigable, light/dark, works at
  375px.
- Dashboard security: loopback-only bind, a per-launch token passed in the URL
  fragment and required on every API call, Host/Origin/Sec-Fetch-Site checks,
  POST + JSON only for mutations, strict CSP with no inline script, secrets
  never returned or put in process arguments, one job at a time (409 otherwise)
  with timeouts.
- `agent_reach.doctor.check_one(name, config)` checks a single channel with the
  same timeout and error handling as `check_all`.
- The wheel-gate CI job asserts the dashboard assets ship in the wheel.

---

## 1.6.0 — English fork

### Translation

- Code, CLI output, doctor messages, tests, skill and guides translated to English.
  `SKILL.md` is now English and is installed for every locale; the `LANG`/`LC_*`
  locale switch is gone (`LANG=C.UTF-8` / unset no longer changes the skill).
  `SKILL_en.md` still ships, identical, for tools that reference it by name.

### Behaviour changes

- **Config errors exit 2 with one line instead of a traceback.** Invalid YAML,
  non-UTF-8 bytes, a non-mapping top level, unreadable files and symlinked
  config paths are reported as `agent-reach: error: …` plus a remedy. YAML errors
  report only line/column, never file content (the file holds secrets).
- **Read-only commands follow symlinks.** `Config(read_only=True)` (doctor,
  watch, MCP) accepts a symlinked `$HOME`, macOS `/var` → `/private/var` and
  dotfile-managed directories; regular-file and size checks still apply. Writes
  keep the strict no-follow rule. `read_small_text_no_follow()` gains
  `follow_symlinks=` for read-only callers.
- **Non-mapping `config.yaml` (`[]`, `false`, `0`, …) is an error**, no longer
  silently treated as `{}` and overwritten by the next save.
- **Doctor runs channel checks concurrently** in daemon threads under one
  shared 30 s budget (`AGENT_REACH_DOCTOR_TIMEOUT` overrides it). Checks still
  running when it expires are reported with the new status `"timeout"`; result
  order still follows the channel registry. A typical run takes about half the
  time it did.
- **`active_backend` contract:** it names the backend that would be used
  whenever a channel reports `ok` *or* `warn`, and is always `null` for `off`,
  `error` and `timeout` (doctor enforces the latter). `Channel.pick_finding()`
  implements the ok-then-warn selection for multi-backend channels.
- **Backend overrides (`<channel>_backend` / `<CHANNEL>_BACKEND`) are
  case-insensitive and accept ASCII slugs:** `opencli`, `bird`, `xhs-cli`,
  `bilibili-api` / `search-api` / `api` (→ "Bilibili search API"). A value that
  matches nothing logs a warning instead of being ignored silently.
- **MCP server:** `get_status` returns plain text (no Rich markup) by default
  and the `doctor --json` payload with `{"format": "json"}`; the doctor runs in
  `asyncio.to_thread` so the event loop stays responsive. New `mcp` extra
  (`mcp[cli]>=1.0,<2`; the `all` extra uses the same pin), and the install hint
  now names an extra that exists.
- **`uninstall` exits 1 when anything could not be removed**, and removes the
  `$OPENCLAW_HOME` skill too (it reuses the same skill-root list as install).
  `skill --uninstall` also exits 1 on failure.
- **`configure youtube-cookies` validates its value**
  (`BROWSER[+KEYRING][:PROFILE][::CONTAINER]`, normalised, exit 2 on junk); the
  saved `youtube_cookies_from` key is what `transcribe` reads.

### Fixes

- `install --env=server|local` is honoured by the Reddit and Xiaohongshu
  installers instead of re-running auto-detection (upstream #701 / PR #705).
- Skill refresh is atomic: the packaged skill is read into memory, written to a
  sibling staging directory and swapped in with renames, so a broken package
  or a failed write keeps the previous skill (a symlinked skill dir is replaced
  without touching its target).
- `setup` and `uninstall` call mcporter through its resolved path (Windows
  `.cmd` shims cannot be started by bare name; upstream #590).
- Probes run in their own session/process group; a timeout kills the whole
  tree (`killpg` on POSIX, `taskkill /T /F` on Windows) and drains pipes with a
  bounded wait, so npm shims can no longer hang doctor (upstream #732, PR #751).
- OpenCLI multi-profile daemons (`profileRequired: true`) are recognised as
  connected when any profile is (upstream #740 / PR #739). `opencli_status()` is
  probed once per doctor run instead of six times.
- `check-update`/`watch` cap a `Retry-After` sleep at 30 s and treat non-JSON
  (captive portal) responses as an error instead of crashing.
- `configure xhs-cookies` picks the exact `xiaohongshu-mcp` container (or the
  only match) and refuses ambiguous `docker ps` results.
- Redirected stdin is decoded as UTF-8 on every platform (`format xhs`,
  `configure --stdin` on zh-CN Windows; upstream PR #754).
- `make_private_dir` skips the chmod when the directory is already 0700 and
  downgrades a `PermissionError` to a warning (chmod-denying sandboxes;
  upstream #684).
- `_atomic_write_yaml` no longer leaks the temp file descriptor when
  `fchmod`/`fdopen` fails.
- The install tools directory, skill roots and OpenCLI extension scan resolve
  `~` through `home_dir()` (honours `$HOME` on Windows) like config and uninstall.

### Channel fixes

- YouTube doctor now inspects the config file yt-dlp actually loads (first existing of `yt-dlp.conf`, `yt-dlp/config`, `yt-dlp/config.txt` under XDG/APPDATA/home, in yt-dlp's order), parses it comment-aware like yt-dlp (`# --js-runtimes node` no longer counts, #715), follows dotfile-managed symlinks, and points the fix command at that file.
- YouTube doctor wording no longer claims videos/subtitles work; it says the toolchain is ready and access was not verified live (#766, #729).
- `agent-reach transcribe` passes the saved `youtube_cookies_from` value to yt-dlp as `--cookies-from-browser` for youtube.com/youtu.be URLs only, after validating the `BROWSER[+KEYRING][:PROFILE][::CONTAINER]` grammar (#702, from #723).
- Transcription runs ffmpeg/ffprobe with absolute input/output paths (files named `-x.wav` or `Podcast: x.wav` work), `-nostdin`, and `stdin=DEVNULL`, so it never eats the caller's stdin (#770, #769).
- Transcription's SSRF check now uses the shared `normalize_public_http_url` (blocks `.internal`/`.lan`/`.local`, non-global addresses, IDNA lookalikes).
- `normalize_public_http_url` validates the IDNA-normalised host (`http://１２７.０.０.１/` is rejected) and returns the ASCII host form.
- `host_matches` rejects backslashes, whitespace and control characters (`https://evil.example\.x.com/` no longer routes to Twitter) and accepts scheme-less URLs such as `x.com/user`.
- Reddit and XiaoHongShu checks report undecodable cookie files as "cannot be read safely" instead of crashing (#748), and treat `saved_at <= 0` as unknown rather than stale. Reddit's own login template no longer shows as expired.
- XiaoHongShu: the xiaohongshu-mcp probe closes its HTTP responses, and `format_xhs_result` unwraps the MCP `feeds`/`data` envelopes and camelCase fields instead of returning `{}` (#744).
- Xueqiu: the anonymous warm-up uses `/hq` (which sets `xq_a_token`) and closes its response. A newly configured or changed `xueqiu_cookie` is picked up in long-lived processes. A saved cookie that Xueqiu rejects (400016/401/403) falls back to an anonymous session instead of blocking it, and doctor says so (#664). The warm-up is skipped when a token is already present.
- Twitter: an empty `TWITTER_AUTH_TOKEN=""` / `TWITTER_CT0=""` no longer hides saved credentials.
- Web: `read()` percent-encodes non-ASCII paths and queries, IDNA-encodes hosts, and decodes with the declared charset using replacement. `check()` is a 3 s HEAD probe of r.jina.ai that warns when it can't be reached (#685).
- V2EX helpers handle error objects (`{"status":"error"}`), `null` items and non-list replies without crashing, and show the API message.
- RSS `can_handle` matches feed path segments and suffixes on http(s) URLs only (no more `wiki/Anatomy`, `/issues/feedback`, `sitemap.xml`, `file://`).
- Xiaoyuzhou: `transcribe.sh` passes the Groq key through a 0600 curl config file (`-K`) instead of argv, sends the Chinese Whisper prompt with `-F "prompt=<file"` (#720), skips unrunnable Microsoft Store `python3` stubs (#717), and runs ffmpeg with `-nostdin`. Doctor also requires ffprobe, curl and perl (#753) and finds the script via `home_dir()`.
- mcporter config inspection accepts JSONC (comments, trailing commas) like mcporter (#762). It skips malformed home/project layers (with a warning in the Exa/LinkedIn/XHS rows) instead of failing every channel; an explicit `MCPORTER_CONFIG` still fails closed.
- Boss: the CDP cookie probe only connects to a loopback `ws://` host, reassembles fragmented WebSocket messages up to FIN (with a size cap), and matches the `wt2` domain exactly. The Windows Chrome command quotes profile paths that contain spaces (#771).
- Bilibili doctor stops probing once a backend is ok, so the 2-4 s search-API call is skipped when bili-cli works.
- `active_backend` is now set for warn-level backends too (twitter, reddit, xhs, bilibili, github, OpenCLI sites, Exa, LinkedIn). Read-only doctor reads follow symlinks.
- Docs: the YouTube subtitle recipe prefers manual subs, then `<lang>-orig`, and warns that other automatic tracks are machine translations (#742). mcporter examples use `key=value` / `"key=multi word"` instead of `key="value"` (#679). SKILL.md and SKILL_en.md stay identical.
- Installer now edits the yt-dlp config file yt-dlp actually loads.
- Remaining doctor tests run fully offline.

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
