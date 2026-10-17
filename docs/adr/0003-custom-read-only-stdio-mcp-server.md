# 0003: A custom, read-only MCP server over stdio

- **Status:** Accepted (PR #3)

## Context

Claude should query the logs through a defined interface instead of reading the database file or running SQL. The
roadmap wants a custom MCP server as one of the building blocks.

## Decision

- `incident-logs` is a FastMCP server over stdio, registered project-wide in `.mcp.json` with no absolute paths, so
  every clone gets it after one approval.
- Four tools: `search_logs`, `get_error_stats`, `get_deployments`, `get_stacktrace`. None writes.
- All logic is in `queries.py` as plain functions on a `sqlite3.Connection` (no MCP imports), so it is tested
  without MCP; `mcp_server.py` is a thin layer.
- The database is opened read-only (`mode=ro` plus `PRAGMA query_only`), SQL is parameterized, results are capped at
  200 rows and reported as `truncated`, errors are short and actionable.
- The server never prints to stdout, because stdio carries the protocol. The SDK is pinned to `mcp<2`.

## Consequences

- The agent cannot damage or even see the raw database; the interface is the boundary.
- A row cap keeps the context small, but forces the agent to narrow queries or use `total_matches`.
- Tools cannot split by arbitrary fields (for example client version); the agent uses `search_logs` counts instead.
- Pinning `mcp<2` avoids the rename of `FastMCP` in v2, at the price of a later migration.
