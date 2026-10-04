# Exa Search Setup Guide

## Overview
Exa is an AI semantic search engine. It connects through MCP and is **free, with no API key required**. Once configured it unlocks:
- Web-wide semantic search
- Reddit search (via site:reddit.com)
- Twitter search (via site:x.com)

## Steps the agent can complete automatically

With the user's explicit authorization, `agent-reach install --env=auto --system` performs the steps below.
The default command without `--system` only runs read-only checks.

### 1. Install mcporter
```bash
npm install -g mcporter
```

### 2. Register the Exa MCP
```bash
mcporter config add exa https://mcp.exa.ai/mcp --scope home
```

### 3. Verify
```bash
agent-reach doctor | grep "Search"
mcporter call exa.web_search_exa query="test" numResults=1
```

## Steps the user must do manually

**None.** Exa connects through MCP: free, no sign-up, no API key.

If `agent-reach install --system` did not configure Exa because of a network problem, just run the two commands above manually.

## FAQ

**Q: Is there a search limit?**
A: The MCP endpoint is provided by Exa itself (mcp.exa.ai) and is currently free and unlimited. If that changes, Agent Reach will adapt in a later update.

**Q: What is mcporter?**
A: A command-line bridge for the MCP protocol, used to call MCP servers. Agent Reach uses it to connect to Exa and Xiaohongshu.
