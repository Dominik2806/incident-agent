# incident-agent

[![CI](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)

A Claude Code agent that works like an on-call engineer: it investigates a production error from logs, finds the
root cause, proposes a fix and writes an incident report. The agent proposes, the human decides.

## What it does

You report a problem, for example *"Since about 2 pm customers are getting 500 errors at checkout. What is going
on?"*. Claude follows the `incident-runbook` skill: three read-only subagents triage the incident, find the root
cause and propose a patch plus a rollback, Claude assigns a severity (SEV-1 to SEV-4) and writes a report to
`reports/`. Nothing is applied automatically.

No production system is needed. A script generates a day of deterministic sample logs with a built-in incident into
SQLite, and a custom MCP server makes them queryable. Everything runs locally.

## Requirements

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and the dependencies on first use)
- [Claude Code](https://claude.com/claude-code)

## Quickstart

```bash
git clone https://github.com/Dominik2806/incident-agent.git && cd incident-agent
uv run generate-data
claude
```

Then paste the demo prompt:

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

On first start Claude Code asks you to **trust the workspace** (required for the hooks to run) and to **approve the
project MCP server** `incident-logs` from [`.mcp.json`](.mcp.json) (one time). `/mcp` should then list it with four
tools. The expected result: `payment` v1.4.2 (deployed 14:01) raises `KeyError: 'currency'` for legacy clients,
severity SEV-2, a patch and a rollback as proposals, and a report in `reports/`.

## What is inside

| Part | What it does | Where |
|---|---|---|
| Subagents | `triage` (what, since when, how bad), `root-cause` (cause with evidence), `fix-proposer` (patch as diff, rollback, risks; only `Read`, `Grep`, `Glob`) | [`.claude/agents/`](.claude/agents/), [docs](docs/subagents.md) |
| Skill | `incident-runbook`: the procedure, the severity matrix and the report template | [`.claude/skills/incident-runbook/`](.claude/skills/incident-runbook/SKILL.md) |
| MCP server | `incident-logs`, read-only: `get_error_stats`, `get_deployments`, `search_logs`, `get_stacktrace` (max. 200 rows per call) | [`src/incident_agent/`](src/incident_agent/), [docs](docs/mcp-tools.md) |
| Safety hooks | `block_destructive.py` (blocks `rm -rf`, `DROP`, `git push --force`, ...), `protect_files.py` (`.env`, `*.db`, `data/`, CI workflows), `audit_log.py` (every tool call to `logs/audit.jsonl`) | [`.claude/hooks/`](.claude/hooks/), [docs](docs/hooks.md) |
| Notification hook | `notify.py`: desktop notification and optional Slack message (`SLACK_WEBHOOK_URL`) | same |
| Stop hook | `check_report.py`: keeps Claude working until the report has every section of the template (one correction round) | same |
| Demo app | `demo_app/`: tiny service with the intentional bug, so the fix proposal points at real code | [docs](docs/demo-app.md) |

How the parts fit together: [docs/architecture.md](docs/architecture.md). Why they are built this way:
[docs/adr/](docs/adr/README.md).

## Scenarios

| Scenario | Story | Demo prompt |
|---|---|---|
| `checkout-500` (default) | Bad `payment` deployment: `KeyError: 'currency'` breaks checkout for legacy clients. Expected: SEV-2 | Since about 2 pm customers are getting 500 errors at checkout. What is going on? |
| `db-pool` | Traffic spike exhausts the `catalog` connection pool; the `auth` deployment is a red herring. Expected: SEV-3 | Since mid-afternoon (around 3 pm UTC) product searches have been slow or failing for some customers. What is going on? |

Switch with `uv run generate-data --scenario db-pool` (each run overwrites `data/incident.db`; no restart needed;
`uv run generate-data` returns to the default). Details, timelines and expected causes:
[docs/scenarios.md](docs/scenarios.md).

Example reports written by the agent: [checkout-500](docs/examples/incident-report.md) and
[db-pool](docs/examples/incident-report-db-pool.md).

## Safety

The agent only proposes fixes, and its subagents have no `Write`, `Edit` or `Bash` tool. The hooks add deterministic
rules on top, but be clear about their limits: **they are a safety net, not a security boundary.**

- Guards are pattern based. Obfuscated or generated commands get through, and `protect_files.py` does not see writes
  made through the shell. Harmless commands that merely contain text like `rm -rf` (for example in a heredoc) are
  blocked too.
- The audit, notification and stop hooks fail open on purpose; the stop hook blocks only once per stop.
- Hooks only run after you trust the workspace, and anyone who can edit `.claude/settings.json` can change them.

Details: [docs/hooks.md](docs/hooks.md).

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
```

`main` is protected: one branch per change (`feature/`, `fix/`, `docs/`, `chore/`), Conventional Commits, squash-merged
pull requests with a CHANGELOG entry. See [CONTRIBUTING.md](CONTRIBUTING.md). Further reading:
[roadmap](docs/ROADMAP.md), [changelog](CHANGELOG.md).

## Roadmap status

| Phase | Result | Status |
|---|---|---|
| 0 | Project skeleton, CI, templates | Done |
| 1 | Log generator and `checkout-500` scenario | Done |
| 2 | MCP server `incident-logs` | Done |
| 3 | Subagents `triage` and `root-cause` | Done |
| 4 | `incident-runbook` skill | Done |
| 5 | Safety hooks (v0.1.0) | Done |
| 6 | Demo app and `fix-proposer` | Done |
| 7 | Notification and stop hooks, `db-pool` scenario | Done |
| 8 | Docs and release v1.0.0 | Done (v1.0.0) |

Outlook (not started): bundle everything as a Claude Code plugin, connect real data sources such as Sentry, run on a
schedule, more scenarios (memory leak, expired certificate, third-party outage), automatic evaluation of the expected
cause per scenario.

## License

[MIT](LICENSE)
