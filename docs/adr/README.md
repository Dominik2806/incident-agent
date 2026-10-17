# Architecture decision records

Short records of the decisions that shape this project. Each one has a status, the context, the decision and its
consequences. The pull request numbers refer to where the decision landed. For the overall picture see
[architecture.md](../architecture.md).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-python-uv-src-layout.md) | Python with uv and a `src` layout | Accepted |
| [0002](0002-synthetic-deterministic-data-in-sqlite.md) | Synthetic, deterministic data in SQLite instead of a real log backend | Accepted |
| [0003](0003-custom-read-only-stdio-mcp-server.md) | A custom, read-only MCP server over stdio | Accepted |
| [0004](0004-three-focused-subagents.md) | Three focused subagents; `fix-proposer` has only Read, Grep and Glob | Accepted |
| [0005](0005-hooks-as-guardrails.md) | Hooks as deterministic guardrails, a safety net and not a security boundary | Accepted |
| [0006](0006-skill-as-single-source-of-truth.md) | The skill is the single source of truth for workflow and report template | Accepted |
| [0007](0007-stop-hook-via-audit-log.md) | The stop hook finds the report through the audit log and allows one correction round | Accepted |
| [0008](0008-demo-app-with-intentional-bug.md) | A demo app with an intentional bug that is never fixed on `main` | Accepted |
| [0009](0009-scenario-isolation-and-pinned-hash.md) | Scenarios never change each other's output; `checkout-500` is pinned by a hash | Accepted |
