# Xiaohongshu (RedNote) Setup Guide

## Overview
Read and search Xiaohongshu notes. On desktop, OpenCLI is preferred; on servers, use
[xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp).
xhs-cli is kept only as a legacy fallback for users who already have it installed.

## Prerequisites
- OpenCLI: a Xiaohongshu session in Chrome that already exists and is explicitly controlled by the user
- xiaohongshu-mcp / legacy tools: the Cookie-Editor browser extension

## Authentication boundary

Agent Reach does not log in to Xiaohongshu on the user's behalf and does not read browser cookies.

OpenCLI only uses a Chrome session that already exists and is explicitly controlled by the user.
`agent-reach configure xhs-cookies` does not inject cookies into OpenCLI or Chrome.
If there is no existing session, do not log in automatically; instead export cookies by hand with Cookie-Editor and configure
xiaohongshu-mcp or the legacy tool:

1. Install the [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) extension in Chrome
2. The user prepares the session to export on xiaohongshu.com themselves
3. Click the Cookie-Editor icon → Export → Header String
4. Send the exported string to the agent and run:

```bash
agent-reach configure xhs-cookies
agent-reach doctor
```

This explicit command saves/imports the set of same-domain xiaohongshu.com cookies supplied by the user; before running it, please
confirm the cookie names and scope. Cookies from domains other than xiaohongshu.com are ignored.

If the xiaohongshu-mcp container is running, the configure command imports the cookies into the container; otherwise it writes an
owner-only local file and prints the path for a later manual import.

## Usage examples

First pick the command according to `active_backend` from `agent-reach doctor --json`. Legacy xhs-cli examples:

Search notes:
```bash
xhs search "keyword"
```

Read note details:
```bash
xhs read NOTE_ID
```

View comments:
```bash
xhs comments NOTE_ID
```

## FAQ

**Q: Cookies expired?**
A: Export them by hand again through Cookie-Editor, then run
`agent-reach configure xhs-cookies` and paste them into the hidden input prompt.

**Q: Xiaohongshu reports an IP risk?**
A: A residential proxy is recommended: `export HTTP_PROXY="http://user:pass@ip:port"`.

**Q: xhs-cli does not support my system?**
A: Make sure Python 3.10+ and pipx are installed. Then run `pipx install xiaohongshu-cli`.

## Server option: Docker MCP

If you already use the [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) Docker setup, it works too:

```bash
docker run -d \
  --name xiaohongshu-mcp \
  -p 18060:18060 \
  xpzouying/xiaohongshu-mcp

mcporter config add xiaohongshu http://localhost:18060/mcp --scope home
```

This server backend uses the manual Cookie-Editor export flow described above.
