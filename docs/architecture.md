# Architecture

`incident-agent` is a Claude Code project, not a service. The "program" is the main Claude agent following a skill,
delegating to three subagents that read sample logs through an MCP server, with hooks guarding every tool call.
Why it is built this way: [adr/](adr/README.md).

## Components

```mermaid
flowchart LR
    user["User"]
    main["Main agent<br/>skill: incident-runbook"]
    triage["triage"]
    rootcause["root-cause"]
    fixprop["fix-proposer"]
    mcp["MCP server<br/>incident-logs (stdio)"]
    db[("SQLite<br/>data/incident.db")]
    gen["Data generator<br/>uv run generate-data"]
    demo["demo_app/<br/>checkout + payment code"]
    reports["reports/*.md"]
    hooks["Hooks<br/>.claude/hooks/"]

    user -->|"reports a problem"| main
    main -->|"1. delegates"| triage
    main -->|"2. delegates"| rootcause
    main -->|"3. delegates"| fixprop
    triage -->|"read-only tools"| mcp
    rootcause -->|"read-only tools"| mcp
    mcp -->|"mode=ro"| db
    gen -->|"writes"| db
    rootcause -.->|"Read, Grep"| demo
    fixprop -.->|"Read, Grep, Glob"| demo
    gen -.->|"stack frames point to real lines"| demo
    main -->|"writes the report"| reports
    main -->|"proposals and report"| user
    hooks -.->|"guard every tool call"| main
```

## One investigation

```mermaid
sequenceDiagram
    actor U as User
    participant M as Main agent (skill)
    participant T as triage
    participant R as root-cause
    participant F as fix-proposer
    participant S as MCP server
    participant H as Hooks

    U->>M: "customers get 500 errors at checkout"
    M->>T: problem description
    T->>S: get_error_stats, search_logs
    S-->>T: error rates, first errors
    T-->>M: summary and handoff notes
    M->>R: full triage output
    R->>S: get_deployments, get_stacktrace, search_logs
    S-->>R: deployment, stack traces, request contexts
    R-->>M: cause, confidence, evidence, ruled out
    M->>F: full root-cause output
    F-->>M: patch (diff), rollback, risks
    M->>M: final severity from severity.md
    M->>H: Write reports/YYYY-MM-DD-title.md
    H-->>M: PreToolUse allows, PostToolUse audits
    M->>H: stop
    H-->>M: Stop hook: report complete, or one correction round
    M-->>U: severity, cause, next steps, report path
```

Every tool call in this flow, including those of subagents, passes through the hooks.

## Hooks

```mermaid
flowchart LR
    pre["PreToolUse"] --> bash["Bash: block_destructive.py"]
    pre --> files["Edit, Write, MultiEdit, NotebookEdit: protect_files.py"]
    post["PostToolUse, all tools"] --> audit["audit_log.py"]
    note["Notification"] --> notify["notify.py"]
    stop["Stop"] --> check["check_report.py"]
    bash -->|"exit 2: blocked"| log["logs/audit.jsonl"]
    files -->|"exit 2: blocked"| log
    audit --> log
    check -->|"reads the log to find the report"| log
    check -->|"exit 2: report incomplete"| keep["Claude keeps working"]
```

## Building blocks

| Block | Role | Lives in |
|---|---|---|
| Main agent and skill | Orchestrates. The skill is the single source of the procedure, the severity matrix and the report template. Subagents cannot call each other, so the main agent passes each full output to the next | [`.claude/skills/incident-runbook/`](../.claude/skills/incident-runbook/SKILL.md), [CLAUDE.md](../CLAUDE.md) |
| `triage` | What is broken, since when, how bad. Only `get_error_stats` and `search_logs` | [`.claude/agents/triage.md`](../.claude/agents/triage.md) |
| `root-cause` | Cross-checks deployments, stack traces and request contexts. All four MCP tools plus `Read`, `Grep` | [`.claude/agents/root-cause.md`](../.claude/agents/root-cause.md) |
| `fix-proposer` | Patch, rollback, risks. Only `Read`, `Grep`, `Glob`: no MCP tools, no write access | [`.claude/agents/fix-proposer.md`](../.claude/agents/fix-proposer.md) |
| MCP server | Thin FastMCP layer over `queries.py`; four read-only tools, max. 200 rows per call, stdio (never prints to stdout) | [`src/incident_agent/mcp_server.py`](../src/incident_agent/mcp_server.py), [`.mcp.json`](../.mcp.json) |
| Query logic | Plain functions on a `sqlite3.Connection`, parameterized SQL, input validation, read-only connection (`mode=ro`) | [`src/incident_agent/queries.py`](../src/incident_agent/queries.py) |
| Database | One day (2026-03-12 UTC) of logs, deployments and errors of `checkout`, `payment`, `catalog`, `auth` | `data/incident.db` (generated, gitignored) |
| Data generator | Deterministic (fixed date, seeded RNG) normal traffic plus a scenario; `checkout-500`, `db-pool`, `cert-expired`, `api-outage` and `memory-leak` | [`src/incident_agent/data/`](../src/incident_agent/data/), [scenarios](scenarios.md) |
| Demo app | Stdlib-only code with the intentional bug of payment v1.4.2; its lines are what the generated stack traces point to | [`demo_app/`](../demo_app/), [demo-app.md](demo-app.md) |
| Hooks | Deterministic rules around the model: two guards, an audit log, a notification and a report check | [`.claude/hooks/`](../.claude/hooks/), [hooks.md](hooks.md) |

## Data flow

1. `uv run generate-data` writes `data/incident.db` (the only writer; the MCP server never writes).
2. Claude Code starts the MCP server from `.mcp.json` (`uv run incident-logs-server`). It opens the database
   read-only for every query, from `data/incident.db` or the path in `INCIDENT_DB_PATH`.
3. `triage` and `root-cause` query it. Raw log output stays in their own context; only summaries return to the main
   agent.
4. `root-cause` and `fix-proposer` read `demo_app/` to confirm the failing line. For `db-pool` there is no catalog
   code in the repo (synthetic frames), and the agents say so.
5. The main agent writes `reports/YYYY-MM-DD-short-title.md` (gitignored). Fixes stay proposals in the report.

## Trust boundaries

| Boundary | What enforces it |
|---|---|
| Log data is read-only for the agent | SQLite `mode=ro` plus `PRAGMA query_only`, parameterized SQL, no write tool in the MCP server |
| Subagents cannot change anything | Explicit `tools` allowlists without `Write`, `Edit`, `Bash`, `NotebookEdit`; checked by `tests/test_agents.py` |
| Destructive shell commands | `block_destructive.py` (PreToolUse, Bash): pattern rules, fail closed |
| Secrets, databases, `data/`, CI workflows | `protect_files.py` (PreToolUse, file tools): fail closed. Does not cover shell writes |
| Trail of what happened | `audit_log.py` (PostToolUse): every call and every blocked attempt, fail open |
| Complete report | `check_report.py` (Stop): one correction round, fail open |
| The human decides | Nothing in the repo applies a fix; the demo bug is never fixed on `main` |

The hooks are a safety net against mistakes, not a security boundary: they are pattern based and can be bypassed
(see [hooks.md](hooks.md#limitations)). The notification hook can send text to Slack if `SLACK_WEBHOOK_URL` is set, so
that variable is a secret.
