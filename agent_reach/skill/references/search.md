# Search Tools

The Exa AI search engine.

## Exa AI search

A high-quality AI search engine, well suited to technical docs, official examples and related web pages.

> mcporter keeps quotes that reach it as part of the value, so write `key=value` and quote the whole token when the value has spaces (`"keywords=AI engineer"`), never `key="value"`; `--args '{"key": "value"}'` also works.

```bash
mcporter call exa.web_search_exa "query=your search terms" numResults=5
mcporter call exa.web_search_exa "query=library API code example" numResults=5
```

### Use cases

| Scenario | Parameters |
|-----|------|
| Web search | `web_search_exa(query: "...", numResults: 5)` |
| Technical / code material | `web_search_exa(query: "framework name API example", numResults: 5)` |

> The Exa MCP `get_code_context_exa` tool is deprecated and not registered by default. Use
> `web_search_exa` for code questions too; to search repository contents precisely, use the GitHub search in `dev.md`.

### Strengths

- Strong on English content and technical docs
- Queries can locate official docs and code examples
- High result quality

## Comparison with other search tools

| Tool | Source | Best for |
|-----|------|---------|
| Exa | agent-reach | English / technical / code search |
| Zhipu Search | my-mcp-tools | Chinese-language search |
| GitHub search | agent-reach (dev.md) | Repository / code search |
