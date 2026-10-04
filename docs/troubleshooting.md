# Troubleshooting

## Xueqiu: API returns 400

**Symptom:** `agent-reach doctor` shows Xueqiu as ⚠️ with `HTTP Error 400`

**Cause:** The Xueqiu API needs login cookies and cannot be reached anonymously.

**Fix:** Log in to xueqiu.com in Chrome, then run:

```bash
agent-reach configure --from-browser chrome --platform xueqiu
```

Run `agent-reach doctor` again to confirm it is back to ✅. When the cookies expire, just run the command again.

---

## Boss Zhipin: `boss status` says logged in, but search reports `AUTH_EXPIRED`

**Symptom:** `boss status` / `status --live` returns `logged_in: true` (even with a username),
but `boss ... search` immediately fails with `{"code": "AUTH_EXPIRED", "message": "User not logged in"}`.
The dedicated Chrome may also be sitting on a URL containing `_security_check`, which looks like an anti-bot slider.

**Cause:** Boss has two login-state stores, and they authenticate different channels:

| Store | Who uses it |
|---|---|
| `~/.boss-agent/auth/session.enc` | `boss status` / `status --live`; low-risk httpx-channel operations (`detail` / `cities` / `job_card_httpx`). CDP search also reads it (if it cannot be read, search fails right away with "not logged in"), but when reusing the real Chrome context its cookies never actually take effect |
| Browser cookies inside `~/.boss-chrome-profile` | The credentials that high-risk operations such as search / greet actually carry in strict CDP `existing-browser` mode |

`boss status` only validates the local session.enc. If the local store holds stale credentials from a few days ago while the dedicated Chrome
profile itself is not logged in, it still reports `logged_in: true`; that is not proof the login is valid.
Also, the `_security_check` page is an anti-bot challenge that **appears even when logged in**, so it cannot be used to judge login state;
combining the two makes it very easy to misread "browser not logged in" as "stuck on a slider".

> Do not delete either store. A missing session.enc makes CDP search fail before it even connects to the browser;
> to refresh it, run `login --cdp` rather than deleting the file by hand.

**Order of judgment:**

1. `AUTH_EXPIRED` is the ground truth: when it appears the browser is not logged in, whatever `boss status` says;
2. The boss row in `agent-reach doctor` probes directly for a `wt2` cookie inside the browser; trust it;
3. `boss status` is for reference only; the page URL is never used as a criterion.

**Fix:** In the dedicated Chrome window, visually confirm and log in to zhipin.com manually, then sync the login state:

```bash
boss --cdp-url http://localhost:9222 login --cdp
agent-reach doctor    # the boss row message should show "login cookie (wt2) present in browser"
```

> After launching the dedicated Chrome, the first step is always to have the user visually confirm the login state; never use `boss status` as a substitute.

---

## Twitter/X: twitter-cli connection fails

**Symptom:** `twitter search` or other commands return an error

**Cause:** twitter-cli needs the `TWITTER_AUTH_TOKEN` and `TWITTER_CT0`
environment variables to reach the Twitter API. The values saved by `agent-reach configure twitter-cookies`
are only used by doctor to check that the configuration is complete; doctor does not run upstream authentication and does not set the current
shell. If your network needs a proxy to reach x.com, you also need to configure a proxy.

**Fixes:**

### Option 1: Set environment variables and a proxy

```bash
export TWITTER_AUTH_TOKEN="..."
export TWITTER_CT0="..."
export HTTP_PROXY="http://user:pass@host:port"
export HTTPS_PROXY="http://user:pass@host:port"
twitter search "test" -n 1
```

### Option 2: Use a global proxy tool

Let the proxy tool take over all network traffic so twitter-cli's requests also go through the proxy:

```bash
# macOS — enable "Enhanced Mode" in ClashX / Surge
# Linux — proxychains or tun2socks
proxychains twitter search "test" -n 1
```

### Option 3: Skip twitter-cli and search with Exa instead

When twitter-cli is unavailable, you can search Twitter content directly with Exa:

```bash
mcporter call exa.web_search_exa "query=site:x.com search terms" numResults=5
```

### Option 4: Check authentication

```bash
twitter check
```

> If it returns "Missing credentials", you need to set
> `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` in the environment of the process running the command.
>
> **Fallback:** If you already have the bird CLI installed (`npm install -g @steipete/bird`), it works too. Agent Reach detects installed tools automatically.
