# Careers & Recruiting

LinkedIn, Boss Zhipin.

## LinkedIn

```bash
# Get a person's profile
mcporter call linkedin.get_person_profile linkedin_username="username" sections="experience,education"

# Search people
mcporter call linkedin.search_people keywords="AI engineer" location="Shanghai"

# Get a company profile
mcporter call linkedin.get_company_profile company_name="openai" sections="posts,jobs"

# Search jobs
mcporter call linkedin.search_jobs keywords="software engineer" location="Remote" max_pages=2
```

> **Login required**: before first use, run `uvx mcp-server-linkedin@latest --login` to save a valid login session.

### Fallback

If the MCP is unavailable, use Jina Reader:

```bash
curl -s "https://r.jina.ai/https://linkedin.com/in/username"
```


## Boss Zhipin

When the user says "help me set up Boss Zhipin", follow this section to complete the install, launch the dedicated Chrome, wait for the user to log in manually
and run the final verification. Do not dump implementation details such as port 9222 on the user first, and do not enter credentials,
scan QR codes or solve sliders on the user's behalf.

> **Key distinction: login gate != anti-bot security check.** After landing on zhipin.com you may end up on one of three pages:
> the logged-in `web/geek/job`, the logged-out `web/user/` (QR-code login / phone login), and the
> anti-bot **security-check page** (URL contains `security-check` / `zhipin-security` /
> `_security_check`). The security-check page has nothing to do with login: **it also appears when logged in** (it
> almost always appears in Chrome with a CDP debugging port). Never judge login state from the current page URL.

> **Dual login-state stores (in existing-browser strict CDP mode the browser is authoritative).** There are two credential stores,
> **neither may be deleted**, but they authenticate different channels:
>
> | Store | Role |
> |---|---|
> | `~/.boss-agent/auth/session.enc` | (1) A hard gate: `_get_browser()` unconditionally calls `get_token()`, and if it cannot read it raises `AuthRequired` right away, so a CDP search fails before it even connects to the browser; (2) it is **not** the search's authentication credential: when CDP reuses the real Chrome's `contexts[0]`, its cookies are only injected in the "no context" branch and never actually take effect; (3) the httpx channel (low-risk ops: `status`/`detail`/`cities`/`job_card_httpx`) really does use its cookies + stoken, and code 37's `force_refresh()` also writes back to it |
> | Browser cookies inside the dedicated Chrome profile | The credentials that high-risk ops such as search/greet actually carry in CDP mode |
>
> **`boss status` / `status --live` only validate session.enc**: even if they report
> `logged_in: true`, that does not mean the CDP browser is logged in. Therefore:
> 1. After launching the dedicated Chrome, the first step must be to **pause and have the user visually confirm** that the window is in a logged-in
>    state (avatar at the top right); only after confirmation may a search run;
> 2. The doctor's boss row directly probes the browser for a wt2 cookie, and that is authoritative;
> 3. **`AUTH_EXPIRED` is the ground truth**: if a search reports it, go straight to the login runbook
>    (user logs in within the dedicated window -> `login --cdp`), and do not interpret it as a "security check" any more;
>    a `_security_check` page is treated as a slider challenge only when `AUTH_EXPIRED` is absent.
> 4. Do not delete session.enc to "clean up old credentials"; to refresh it, run `login --cdp`.

> **Dependency status**: the public strict-CDP APIs needed come from the boss-agent-cli follow-up split PRs #403-#407
> (#402/#382 were split at the maintainer's request), all now merged into upstream master. Agent Reach's installer pins
> a fixed upstream commit
> `4c991b77086a203173bf08a4cb64a23af6514fe6` rather than a moving branch; once upstream publishes a release,
> the installer should switch back to a version constraint.

Health check (no side effects, no search):

```bash
agent-reach doctor          # boss row: off = not installed or CDP unreachable; warn = chain ready,
                            # the message states whether the browser has a wt2 login cookie (the browser is authoritative)
```

Search + JD use the public API (`browser_source` / `job_card_browser` / `JobItem.lid`).
Because pipx/uv tool environments are isolated, plain `python` may not be able to import the installed tool;
use `uv run --with` to keep the script and the pinned dependency in the same interpreter environment:

```bash
uv run --isolated --no-project \
  --with 'git+https://github.com/can4hou6joeng4/boss-agent-cli.git@4c991b77086a203173bf08a4cb64a23af6514fe6' \
  python - <<'PY'
from pathlib import Path

from boss_agent_cli.api.client import AccountRiskError, BossClient, EnvironmentRiskError
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.platforms.zhipin import BossPlatform

auth = AuthManager(Path.home() / ".boss-agent")

# Strict CDP mode: reuse the logged-in browser, raise immediately on CDP failure, never headless
with BossClient(
    auth,
    cdp_url="http://localhost:9222",
    browser_source="existing-browser",
) as boss:
    raw = boss.search_jobs("large language model", city="Shenzhen", page=1)
    if raw.get("code") != 0:
        code, message = BossPlatform(boss).parse_error(raw)
        raise RuntimeError(f"{code}: {message}")
    items = raw.get("zpData", {}).get("jobList", [])
    for item in items:
        card = boss.job_card_browser(item["securityId"], item["lid"])
        post_desc = card.get("zpData", {}).get("jobCard", {}).get(
            "postDescription", ""
        )
        print(item.get("jobName"), post_desc)

# AccountRiskError / EnvironmentRiskError -> stop immediately, no automatic retry;
# a code 37 that clearly means an expired token/stoken is refreshed and retried at most once by BossClient.
PY
```

### Environment health check and recovery (required before scraping)

If `agent-reach doctor` reports boss as `off` or `warn` before a search, follow the runbook below; do not read the source and guess:

1. **Is the CDP port reachable**:
   ```bash
   curl -s http://localhost:9222/json/version   # a Browser field = port reachable
   ```

2. **Debug Chrome not open / already closed**: launch the dedicated Chrome for your OS (separate login state, does not pollute the daily browser):
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
   Start-Process chrome.exe -ArgumentList '--remote-debugging-address=127.0.0.1','--remote-debugging-port=9222',"--user-data-dir=$env:USERPROFILE\.boss-chrome-profile",'https://www.zhipin.com/web/geek/job'
   ```

   Bind to the loopback address only. Any process that can reach 9222 has full control of that Chrome; do not listen on a public interface.
   This dedicated profile must be reused long term to keep a stable login state; do not delete or recreate it on every run,
   and do not switch to the daily main Chrome by default. Close this dedicated window when not in use.

   **First step after launch: pause and have the user visually confirm the window is logged in (avatar at the top right).**
   Do not use `boss status` in place of this step; it only validates the local session.enc and does not represent the browser.

3. **User logs in manually (when the browser is not logged in)**: the judgement rests on the doctor's browser cookie probe
   (no wt2 = browser not logged in), and only secondarily on the user's visual confirmation; `boss status` is for reference only.
   Have the user log in or scan the QR code in this dedicated window. Once the user confirms they are done, save the CDP login state:
   ```bash
   boss --cdp-url http://localhost:9222 login --cdp
   ```

   If the window stays on a security-check page (`security-check` / `zhipin-security`), that is an anti-bot challenge,
   not a login page: wait for it to clear automatically or have the user pass the slider manually; do not treat it as "not logged in" and
   scan to log in again.

4. **Whether the login state is valid** (browser cookie probe + whether the stoken has expired):
   ```bash
   agent-reach doctor     # see the browser wt2 cookie probe result in the boss row message
   boss status            # reflects only the local session.enc, for reference only
   ```

5. **Error-code handling** (when searching / fetching a JD):
   - `AUTH_EXPIRED` (user not logged in) -> **ground truth**: the CDP browser is not logged in (whatever
     `boss status` says); go straight to the step 3 login flow + `login --cdp`, and do not interpret it as a
     "security check";
   - code 36 (ACCOUNT_RISK) -> stop immediately, handle it manually on the BOSS page, never retry automatically;
   - code 9 (RATE_LIMITED) -> retry after a cooldown;
   - code 37 + an "abnormal environment" message -> `ENVIRONMENT_RISK`: stop immediately, do not refresh the token, do not log in again, do not retry automatically;
   - only a code 37 whose message clearly says the token/stoken expired is `TOKEN_REFRESH_FAILED`; the client automatically refreshes and retries at most once, and only if that still fails do you log in again.

When the user asks to start searching, the Agent must specify strict CDP mode (global options go before the subcommand):

```bash
boss --browser-source existing-browser --cdp-url http://localhost:9222 search "large language model" --city Guangzhou --page 1
```

Do not page through results continuously without prompting. boss-agent-cli PR #383 adds a persistent
5-10 second list budget for ordinary searches across CLI processes; until that PR is merged and released, the Agent should still proactively call serially and at a low rate.

> **Waiting is expected, not a hang**: when consecutive searches hit the throttle, boss-agent-cli silently waits 5-10 seconds
> (on a TTY it shows a "throttle wait Ns..." notice; Agent Reach calls it with `--json` and cannot see that notice).
> Do not retry, launch a new browser or switch profiles during the wait window.
