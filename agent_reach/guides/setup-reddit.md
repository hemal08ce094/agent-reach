# Reddit Setup Guide

## Overview

Reddit blocks almost all direct non-browser access (including datacenter and ISP proxy IPs), and its JSON API returns 403.

Agent Reach provides Reddit search and reading through **rdt-cli**:
- **Search**: `rdt search "keyword"`
- **Read a full post plus comments**: `rdt read POST_ID`

Free, no proxy, no API key. Login authentication is required (`rdt login`, which extracts cookies from the browser automatically).

## Steps the agent can complete automatically

1. Check whether rdt-cli is available:
```bash
which rdt && echo "installed" || echo "not installed"
```

2. If it is not installed, install it (the PyPI version is currently behind, so install the latest from GitHub):
```bash
pipx install 'git+https://github.com/public-clis/rdt-cli.git'
```

Or install in one step:
```bash
agent-reach install --env=auto --system --channels=reddit
```

## Usage examples

Search Reddit:
```bash
rdt search "python best practices" -n 5
```

Read a full post and its comments:
```bash
rdt read POST_ID
```

## Steps the user must do manually

None. With the user's explicit authorization, rdt-cli is installed through
`agent-reach install --env=auto --system --channels=reddit`.

## Fallback: Exa search

If you have already configured Exa (via mcporter), you can also search Reddit content through Exa:

```bash
mcporter call exa.web_search_exa query="site:reddit.com python best practices" numResults=5
```

rdt-cli is the currently recommended option and works without extra configuration.
