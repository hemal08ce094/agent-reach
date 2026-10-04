# Finance and Market Data

Xueqiu stock quotes, search and trending content. Quotes may be delayed and are not investment advice.

## Check status first

```bash
agent-reach doctor --json
```

When `xueqiu.active_backend` has a value, use that backend; `null` only means Doctor did not complete
a live content check. Xueqiu needs a logged-in session or a minimal cookie; do not treat an HTTP 400 as the stock not existing.

## OpenCLI (preferred when a desktop Chrome login already exists)

```bash
# Verify the current login state
opencli xueqiu whoami -f yaml

# Stock search and live quotes
opencli xueqiu search "NVIDIA" -f yaml
opencli xueqiu stock NVDA -f yaml

# Trending content and trending stocks
opencli xueqiu hot -f yaml
opencli xueqiu hot-stock -f yaml

# List all read-only commands
opencli xueqiu --help
```

OpenCLI only reuses a browser session that already exists and is explicitly controlled by the user. Do not run
`opencli xueqiu login` automatically; when there is no existing login, have the user log in to Chrome first, or explicitly import
the minimal cookie Xueqiu needs:

```bash
agent-reach configure --from-browser chrome --platform xueqiu
```

This configuration reads and saves only `xq_a_token`; it does not collect cookies for other platforms.

## Acceptance and failure handling

- Success means a returned stock name, symbol, price or a non-empty content list; exit code 0 with empty fields is not success.
- HTTP 400 is usually a session/cookie problem and does not mean the ticker does not exist.
- If `whoami` succeeds but `stock`/`hot` fail, report it as an adapter parsing or platform API problem; do not misdiagnose it as logged out.
