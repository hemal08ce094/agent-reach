# Agent Reach — Installation Guide

## For Humans

Copy this to your AI Agent:

```
Install Agent Reach for me: https://raw.githubusercontent.com/hemal08ce094/agent-reach/main/docs/install.md
```

> 🛡️ **Safe by default:** `agent-reach install` only checks the machine and lists missing dependencies:
> ```
> Safely check and install Agent Reach for me: https://raw.githubusercontent.com/hemal08ce094/agent-reach/main/docs/install.md
> ```
> Only use `--system` after the user explicitly approves system-level installs and configuration writes.

---

## For AI Agents

### Goal

Install Agent Reach and all upstream tools so your user's agent has full internet access. After installation, you'll use the upstream tools directly (OpenCLI, twitter-cli, bili-cli, rdt-cli, yt-dlp, mcporter, gh CLI, etc.) — Agent Reach is the selector, installer, health checker and router, never a wrapper.

### ⚠️ Boundaries

When fixing issues, stay within these limits:

- **DO NOT** run commands with `sudo` unless the user explicitly approved
- **DO NOT** modify system files outside `~/.agent-reach/`
- **DO NOT** install packages not listed in this guide
- **DO NOT** disable firewalls, security settings, or system protections
- **DO NOT** clone repos, create files, or run commands inside the agent workspace / working directory
- If something requires elevated permissions, **tell the user** what's needed and let them decide

### 📁 Directory Rules

All Agent Reach files go in dedicated directories — **never in the agent workspace**:

| Purpose | Directory | Example |
|---------|-----------|---------|
| Config & tokens | `~/.agent-reach/` | `~/.agent-reach/config.json` |
| Upstream tool repos | `~/.agent-reach/tools/` | `~/.agent-reach/tools/xiaoyuzhou/` |
| Temporary files | `/tmp/` | `/tmp/yt-dlp-output/` |
| Skills | `~/.openclaw/skills/agent-reach/` | SKILL.md |

**Why?** If you clone repos or create files in the workspace, it pollutes the user's project directory and can break their agent over time. Keep the workspace clean.

### Step 1: Install the basics

```bash
# Recommended: pipx (least hassle)
pipx install https://github.com/hemal08ce094/agent-reach/archive/main.zip
agent-reach install --env=auto               # Read-only check (default)
# After the user explicitly approves system changes:
agent-reach install --env=auto --system

# If your Python comes from Homebrew / you hit PEP 668 (externally-managed-environment),
# install in a virtual environment:
python3 -m venv ~/.agent-reach-venv
source ~/.agent-reach-venv/bin/activate
pip install https://github.com/hemal08ce094/agent-reach/archive/main.zip
agent-reach install --env=auto               # Read-only check (default)
# After the user explicitly approves system changes:
agent-reach install --env=auto --system
```

> 💡 **Windows / Microsoft Store Python alias?**
> If `python3 --version` opens the Microsoft Store, or `where python3` points to
> `...\AppData\Local\Microsoft\WindowsApps\python3.exe`, then `python3` is the Windows
> Store alias, not a usable Python install. Use the Python Launcher `py -3` instead, or the `python.exe` in the actual install directory.
>
> PowerShell example:
> ```powershell
> py -3 -m venv $env:USERPROFILE\.agent-reach-venv
> $env:USERPROFILE\.agent-reach-venv\Scripts\Activate.ps1
> python -m pip install https://github.com/hemal08ce094/agent-reach/archive/main.zip
> agent-reach install --env=auto
> ```

The default command checks core infrastructure (gh CLI, Node.js, mcporter, Exa search, yt-dlp config) without changing the host. With explicit `--system` approval it installs/configures the missing pieces and activates these zero-config channels:

- Web (Jina Reader), YouTube, GitHub, RSS, Exa Search, V2EX, Bilibili (basic)

> 💡 **macOS / Homebrew Python reports `externally-managed-environment`?**
> This is PEP 668 protection, not an Agent Reach problem. Prefer `pipx install ...`, or create a `venv` first and install into it.

**Install modes:**

```bash
agent-reach install --env=auto             # Check only; safe default
agent-reach install --env=auto --safe      # Same check-only behavior (compatibility)
agent-reach install --env=auto --system    # Explicitly allow external/system installs
agent-reach install --env=auto --dry-run   # Preview what --system would do
```

### Step 2: Ask the user which optional channels they want

After installing the basics, **ask the user** which additional channels they need. Present this list:

> The basic channels are installed! You can now ask me to search the web, watch YouTube, read GitHub, and more.
>
> These optional channels are also available. Which do you need?
>
> - 🌟 **OpenCLI** (recommended on desktop) — one install gives you Reddit/Facebook/Instagram/Bilibili subtitles/a Twitter fallback, and serves as the desktop Xiaohongshu backend; Xiaohongshu only uses a Chrome session the user already has and explicitly controls
> - 🐦 **Twitter/X** — search tweets, read timelines (login cookies required)
> - 📈 **Xueqiu** — stock quotes, hot posts (login cookies required)
> - 🎙️ **Xiaoyuzhou podcasts** — audio to text (free Groq key required)
> - 📕 **Xiaohongshu / RedNote** — search, read, comments (OpenCLI uses an existing session; MCP/legacy tools use Cookie-Editor)
> - 📖 **Reddit** — search and read posts (login required: desktop OpenCLI, or rdt-cli + cookies)
> - 📘 **Facebook** — search, profiles, feed, group list (desktop uses OpenCLI, reusing the Chrome login)
> - 📷 **Instagram** — user search, profiles, a user's recent posts, Explore (desktop uses OpenCLI, reusing the Chrome login)
> - 📺 **Bilibili (full)** — trending, rankings, search, video details (bili-cli, no login needed)
> - 💼 **LinkedIn** — profiles, job search
> - 🎯 **Boss Zhipin** — job search + full JD text (dedicated local Chrome; the user logs in manually)
>
> Tell me which ones you want, e.g. "install Xiaohongshu and Twitter" or "install Facebook and Instagram". Or say "install everything".

Based on the user's choice, run:

```bash
agent-reach install --env=auto --system --channels=opencli,xiaohongshu   # Desktop user chose XHS
agent-reach install --env=auto --system --channels=facebook,instagram    # Desktop Meta channels
agent-reach install --env=local --system --channels=boss                # Desktop Boss Zhipin
agent-reach install --env=auto --system --channels=all                   # User approved everything
```

Supported channel names: `opencli`, `twitter`, `xiaoyuzhou`, `xueqiu`, `xiaohongshu`, `reddit`, `facebook`, `instagram`, `bilibili`, `linkedin`, `boss`, `all`

### Step 3: Fix what's broken

Run `agent-reach doctor` and check the output.

Try to get as many channels to ✅ as possible. If something failed during install or shows as ❌/⚠️ in doctor, diagnose the problem and try to fix it — but stay within the boundaries above. If a fix requires elevated permissions or system changes, ask the user first.

Only ask the user when you genuinely need their input (credentials, permissions, etc.).

### Step 3: Configure things that need user input

Some channels need credentials only the user can provide. Based on the doctor output, ask for what's missing:

> 🔒 **Security tip:** For platforms that need cookies or browser sessions (Twitter, XiaoHongShu, Reddit, Facebook, Instagram, Boss Zhipin), we recommend using a **dedicated/secondary account** rather than your main account. Cookie/browser-session auth carries two risks:
> 1. **Account ban** — platforms may detect non-browser API calls and restrict or ban the account
> 2. **Credential exposure** — cookies grant full account access; using a secondary account limits the blast radius if credentials are ever compromised

> 🍪 **Cookies / login sessions:**
>
> For platforms where traditional CLIs need cookies (Twitter, Xueqiu, etc.), **prefer importing with Cookie-Editor**. It is the simplest and most reliable way:
> 1. The user logs in to the platform in their own browser
> 2. Install the [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) Chrome extension
> 3. Click the extension → Export → Header String
> 4. Send the exported string to the Agent
>
> Twitter only accepts what the user explicitly exported through Cookie-Editor. Agent Reach does not log in to Xiaohongshu for the user and does not read Xiaohongshu browser cookies; Xiaohongshu OpenCLI only uses a Chrome session the user already has and explicitly controls. If there is no existing session, export with Cookie-Editor and configure xiaohongshu-mcp / a legacy tool instead. Xueqiu and Bilibili can be imported explicitly per platform, e.g. `agent-reach configure --from-browser chrome --platform xueqiu`; the command does not scan or save other platforms.

**Twitter search & posting:**
> "To unlock Twitter search, I need your Twitter cookies. Install the Cookie-Editor Chrome extension, go to x.com/twitter.com, click the extension → Export → Header String, and paste it to me."

```bash
agent-reach configure twitter-cookies
```

This saves `twitter_auth_token` and `twitter_ct0` for Agent Reach's own
`doctor` configuration check. `doctor` does not run the upstream `twitter status` live and does not modify
the current shell. Before running `twitter search/read/...` directly, you must set them explicitly in that process's environment:

```bash
export TWITTER_AUTH_TOKEN="..."
export TWITTER_CT0="..."
twitter search "query" -n 10
```

> **Proxy note (for networks that require a proxy, e.g. mainland China):**
>
> twitter-cli and rdt-cli are Python tools; on networks that need a proxy you can configure one through environment variables.
>
> **What you (the Agent) need to do:**
> 1. Confirm the user has configured a proxy: `agent-reach configure proxy` (hidden input)
> 2. Set the environment variables: `export HTTP_PROXY="..." HTTPS_PROXY="..."`
> 3. Agent Reach handles the rest automatically; the user needs to do nothing more
>
> If the user reports "fetch failed", see [troubleshooting.md](troubleshooting.md)

**Reddit (login is mandatory — no zero-config path):**
> Reddit's anonymous endpoints are blocked and the official API needs manual approval. Desktop users should prefer OpenCLI (works once you are logged in to reddit.com in the browser); server/legacy users use rdt-cli:

```bash
# PyPI lags behind; install from GitHub (same pinned version as _RDT_GIT_SOURCE in the code)
pipx install 'git+https://github.com/public-clis/rdt-cli.git@5e4fb3720d5c174e976cd425ccc3b879d52cac66'
rdt login   # extracts browser cookies automatically; on a server without a browser, write cookies manually as doctor suggests
```

> Reddit may need a proxy from mainland China; if a server IP is flagged, configure a residential proxy (e.g. https://webshare.io, about $1/month):
> ```bash
> agent-reach configure proxy
> ```

**XiaoHongShu / Xiaohongshu (RedNote) (multiple backends, choose by environment):**

> **Authentication boundary:** Agent Reach does not log in to Xiaohongshu for the user and does not read browser
> cookies. OpenCLI only uses a Chrome session that already exists and is explicitly controlled by the user;
> `agent-reach configure xhs-cookies` does not inject cookies into OpenCLI or Chrome.
> If there is no existing session, do not log in automatically; export manually with Cookie-Editor and configure
> xiaohongshu-mcp or a legacy tool:
>
> ```bash
> agent-reach configure xhs-cookies
> ```
>
> This explicit command saves/imports the user-supplied same-domain cookie set for xiaohongshu.com; confirm
> the cookie names and scope first. Cookies from domains other than xiaohongshu.com are ignored.
>
> **Desktop (OpenCLI recommended):**

```bash
agent-reach install --system --channels opencli
```

> After installing, guide the user through the one manual step (a Chrome security restriction means you can't do it for them):
> 1. Open https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk
> 2. Click "Add to Chrome"
> 3. Run `opencli doctor` to verify (success shows Extension: connected)
>
> On AUTH_REQUIRED, if the user has no existing session, do not log in for them; use the
> xiaohongshu-mcp / legacy-tool Cookie-Editor route below instead.
>
> **Server / no desktop environment (xiaohongshu-mcp):**
> 1. Download the binary for your platform from https://github.com/xpzouying/xiaohongshu-mcp/releases into `~/.agent-reach/tools/`
> 2. Start the service (the first run automatically downloads a ~150MB headless browser; wait for it to finish)
> 3. Import cookies manually using the Cookie-Editor flow above
> 4. Connect it: `mcporter config add xiaohongshu http://localhost:18060/mcp --scope home`
> 5. Always pass `--timeout 120000` when calling
>
> **Legacy users (xhs-cli):** an already-installed xhs-cli keeps working as a fallback backend
> (upstream has been unmaintained since 2026-03, so new installs are not recommended); authentication still uses the Cookie-Editor
> manual export flow above.

**Facebook / Instagram (desktop OpenCLI):**
> Both platforms go through OpenCLI: it reuses the user's own Chrome login, stores no account passwords, and skips the Meta Graph API approval flow. Servers / no-desktop environments are not recommended.

```bash
agent-reach install --system --channels facebook,instagram
```

> After installing:
> 1. Confirm Chrome has the OpenCLI extension and `opencli doctor` passes
> 2. Log in to facebook.com / instagram.com in Chrome
> 3. The Agent calls directly:
>    ```bash
>    opencli facebook search "query" -f yaml
>    opencli facebook profile zuck -f yaml
>    opencli facebook groups -f yaml
>    opencli instagram search "query" -f yaml     # user search
>    opencli instagram profile nasa -f yaml
>    opencli instagram user nasa -f yaml          # a given user's recent posts
>    ```
>
> Facebook Groups currently only promises reading the group list / recent activity visible to the logged-in user, not an API for arbitrary group posts and comments. Instagram search is user search, not a site-wide post keyword search; on a 429 / login error, have the user log in again in Chrome and lower the request rate.

**Xueqiu (stock quotes + hot posts):**
> "Xueqiu needs logged-in cookies. Please log in to xueqiu.com in Chrome first, then run:"

```bash
agent-reach configure --from-browser chrome --platform xueqiu
```

> Only the minimum cookies Xueqiu needs are read and saved; other platforms are not read.

**Xiaoyuzhou Podcast (Groq Whisper):**
> "Xiaoyuzhou podcast transcription is installed by default; all you need is a free Groq API key."

The script is installed automatically with Agent Reach; the user only needs to provide a key:

```bash
agent-reach configure groq-key
```

> **Get a Groq API key (free, no credit card, 30 seconds):**
> 1. Open https://console.groq.com
> 2. Sign in (or sign up) with a Google/GitHub account
> 3. Left menu → API Keys → Create API Key
> 4. Copy the key (starts with `gsk_`) and send it to the Agent
>
> **Usage:**
> The user sends a Xiaoyuzhou link to the Agent, and the Agent calls:
> ```bash
> bash ~/.agent-reach/tools/xiaoyuzhou/transcribe.sh https://www.xiaoyuzhoufm.com/episode/xxxxx
> ```
>
> It downloads the audio automatically → transcodes and slices it → transcribes with Groq Whisper → outputs a full Chinese transcript.
>
> **Free quota and limits:**
> - About 2 hours of audio per hour (7200 seconds); after that, wait 15 minutes for it to reset automatically
> - More than enough for listening to a few episodes day to day
> - High transcription quality (Whisper large-v3), but no speaker separation
> - Process podcasts longer than 2 hours in batches

**LinkedIn (optional — mcp-server-linkedin):**
> "Basic LinkedIn content can be read through Jina Reader. Full functionality (profile details, people and job search) needs mcp-server-linkedin."

> **Configuration (stdio recommended):**
> First install `uv` (which also provides `uvx`) following the official instructions:
> https://docs.astral.sh/uv/getting-started/installation/
>
> ```bash
> mcporter config add linkedin --command uvx --arg mcp-server-linkedin@latest --env UV_HTTP_TIMEOUT=300 --scope home
> ```
>
> `uvx` fetches and starts the latest version of the service on demand; no separate Python package or resident HTTP service is needed.
>
> **First login (needs a browser UI):**
> ```bash
> uvx mcp-server-linkedin@latest --login
> ```
> When the browser pops up, log in to LinkedIn manually; the session is saved to `~/.linkedin-mcp/profile/`. A server without a desktop needs to run the same login command in a visible desktop such as VNC.
>
> See https://github.com/stickerdaniel/linkedin-mcp-server

**Boss Zhipin (desktop only — boss-agent-cli + CDP):**

When the user says "set up Boss Zhipin for me", the Agent does everything that can be automated and leaves only the website login to the user:

1. First explain that an upstream CLI will be installed and a separate Chrome profile directory launched, and ask for system-install approval.
2. After the user agrees, run:
   ```bash
   agent-reach install --env=local --system --channels=boss
   ```
3. Launch a dedicated Chrome bound only to the local loopback address, depending on the OS:
   ```bash
   # macOS
   open -na "Google Chrome" --args --remote-debugging-address=127.0.0.1 \
     --remote-debugging-port=9222 --user-data-dir="$HOME/.boss-chrome-profile" \
     "https://www.zhipin.com/web/geek/job"

   # Linux
   google-chrome --remote-debugging-address=127.0.0.1 \
     --remote-debugging-port=9222 --user-data-dir="$HOME/.boss-chrome-profile" \
     "https://www.zhipin.com/web/geek/job"
   ```
   Windows PowerShell:
   ```powershell
   Start-Process chrome.exe -ArgumentList '--remote-debugging-address=127.0.0.1','--remote-debugging-port=9222',"--user-data-dir=`"$env:USERPROFILE\.boss-chrome-profile`"",'https://www.zhipin.com/web/geek/job'
   ```
4. Pause and have the **user visually confirm** the login state in the window (an avatar appears at the top right); if not logged in, have the **user log in manually**, scan the QR code, or solve the slider. The Agent never asks for account passwords, never logs in on the user's behalf, and must not use `boss status`
   as a substitute for this step; it only validates the local `session.enc` and does not mean this Chrome is logged in.
5. After the user confirms the login is complete, run:
   ```bash
   boss --cdp-url http://localhost:9222 login --cdp
   agent-reach doctor    # check the browser wt2 cookie probe result in the boss row message
   ```

> Security boundary: any local process that can reach debug port 9222 has full control of this Chrome.
> You must use `--remote-debugging-address=127.0.0.1` and must not expose it to the LAN or public internet; use a
> separate profile and reuse it long-term (do not delete or recreate it each time, and do not default to switching to the everyday main Chrome);
> close the dedicated Chrome when not in use. Boss does not support servers / no-desktop environments. Search commands must include
> `--browser-source existing-browser --cdp-url http://localhost:9222`.
>
> Dependency: the follow-up split PRs #403–#407 of boss-agent-cli have been merged into upstream master, and the installer pins the upstream
> fixed commit
> `4c991b77086a203173bf08a4cb64a23af6514fe6` rather than following a moving branch. That commit contains
> `browser_source="existing-browser"` (strict CDP), `JobItem.lid` and `job_card_browser()`. Once upstream publishes a formal release,
> Agent Reach should switch to a version constraint.

### Step 4: Final check

Run `agent-reach doctor` one final time and report the results to your user.

### Step 5: Set up daily monitoring (OpenClaw only)

If you are running inside **OpenClaw**, ask your user:

> "Agent Reach is installed. Want me to set up a daily automatic check? It will watch whether these channels are working and whether there is a new version. It only notifies you when there is a problem, and stays quiet otherwise."

If the user agrees, create a **cron job** (daily, `sessionTarget: "isolated"`, `delivery: "announce"`) with this task:

```
Run the agent-reach watch command.
If the output contains "All normal", there is no need to notify the user; finish silently.
If the output contains problems (❌ ⚠️) or a new version (🆕), send the full report to the user and suggest fixes.
If a new version is available, ask the user whether to upgrade (sending this sentence to the user's Agent does a complete update: Update Agent Reach for me: https://raw.githubusercontent.com/hemal08ce094/agent-reach/main/docs/update.md）。
```

If the user wants a different agent to handle it, let them choose.

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `agent-reach install --env=auto` | Read-only dependency and channel check (default) |
| `agent-reach install --env=auto --system` | Explicitly install/configure core external tools |
| `agent-reach install --env=auto --system --channels=twitter,xiaohongshu` | Install approved optional channels |
| `agent-reach install --env=local --system --channels=boss` | Install the desktop Boss Zhipin strict-CDP backend |
| `agent-reach install --env=auto --system --channels=all` | Install everything after explicit approval |
| `agent-reach install --env=auto --safe` | Compatibility alias for the safe default |
| `agent-reach install --env=auto --dry-run` | Preview what would be done |
| `agent-reach doctor` | Show channel status |
| `agent-reach watch` | Quick health + update check (for scheduled tasks) |
| `agent-reach check-update` | Check for new versions |
| `agent-reach configure twitter-cookies` | Save Twitter cookies via hidden input; direct calls still need explicit environment variables |
| `agent-reach configure proxy` | Save the proxy address via hidden input; not an automatic unlock switch |
| `agent-reach configure groq-key` | Configure the Xiaoyuzhou transcription key via hidden input |

After installation, use upstream tools directly. See SKILL.md for the full command reference:

| Platform | Upstream Tool | Example |
|----------|--------------|---------|
| Twitter/X | `twitter` (fallback `opencli`) | After setting `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`, run `twitter search "query" -n 10` |
| YouTube | `yt-dlp` | `yt-dlp --dump-json URL` |
| Bilibili | `bili` (subtitles via `opencli`) | `bili search "query" --type video` / `opencli bilibili subtitle BVxxx` |
| Reddit | `opencli` (fallback `rdt`) | `opencli reddit search "query" -f yaml` / `rdt read POST_ID` |
| Facebook | `opencli` | `opencli facebook search "query" -f yaml` |
| Instagram | `opencli` | `opencli instagram user nasa -f yaml` |
| GitHub | `gh` | `gh search repos "query"` |
| Web | `curl` + Jina | `curl -s "https://r.jina.ai/URL"` |
| Exa Search | `mcporter` | `mcporter call exa.web_search_exa query=... numResults=5` |
| Xiaohongshu | `opencli` (server: `mcporter`) | `opencli xiaohongshu search "query" -f yaml` |
| Xiaoyuzhou Podcast | `transcribe.sh` | `bash ~/.agent-reach/tools/xiaoyuzhou/transcribe.sh <URL>` |
| LinkedIn | `mcporter` | `mcporter call linkedin.get_person_profile linkedin_username=...` |
| Boss Zhipin | `boss` / Python public API | `agent-reach doctor` (browser wt2 probe; `boss status` only reflects the local session.enc); for search and JD see `references/career.md` |
| RSS | `feedparser` | `python3 -c "import feedparser; ..."` |

> For multi-backend platforms, `active_backend` in `agent-reach doctor --json` is authoritative.
