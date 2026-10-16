# incident-agent

[![CI](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dominik2806/incident-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Claude Code agent that investigates production incidents using subagents, a skill, a custom MCP server and hooks.

> **Status:** early development. See the [roadmap](docs/ROADMAP.md) for the current phase.

## What it does

The agent mimics an on-call engineer. Given a report such as

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

it triages the incident, finds the root cause in the logs, proposes a fix and writes an incident report.
The agent proposes, the human decides: nothing is applied automatically.

No real production environment is needed. A script generates realistic sample logs with a built-in incident,
and a custom MCP server makes them queryable for Claude. Everything runs locally.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

### Generate the sample data

```bash
uv run generate-data --scenario checkout-500
```

This writes a SQLite database to `data/incident.db` (gitignored, overwritten on every run) with 24 hours of
logs for the services `checkout`, `payment`, `catalog` and `auth`, including a built-in incident, and prints
row counts and an hour-by-hour table of errors per service. Options:

| Option | Default | Meaning |
|---|---|---|
| `--scenario` | `checkout-500` | Scenario to generate |
| `--seed` | `42` | Random seed; the same seed always produces identical data |
| `--output` | `data/incident.db` | Path of the database file |

The scenarios are described in [docs/scenarios.md](docs/scenarios.md).

## MCP server

The MCP server `incident-logs` lets Claude query the sample database without direct database access. It offers
four read-only tools:

| Tool | Use |
|---|---|
| `get_error_stats` | Errors and error rate per service and time window: when did the errors begin? |
| `get_deployments` | What was deployed, and when? |
| `search_logs` | Log lines filtered by service, level, time range and text |
| `get_stacktrace` | Full stack trace and request context of one error |

Results are limited to 200 rows per call. Details and examples: [docs/mcp-tools.md](docs/mcp-tools.md).

**Registration.** The server is registered project-wide in [`.mcp.json`](.mcp.json) and started with
`uv run incident-logs-server` (stdio), so every clone gets it without any setup beyond the quickstart. Generate the
data first (see above); the server reads `data/incident.db`, or the file named in the environment variable
`INCIDENT_DB_PATH`.

**First use in Claude Code.** Open the project in Claude Code. Because `.mcp.json` is a project-scoped server,
Claude Code asks you to approve it once. Then run `/mcp` to check that `incident-logs` is connected with four
tools, and try: *Which services had the most errors today?*

## Subagents

Two subagents in [`.claude/agents/`](.claude/agents/) do the analysis in their own context and return only a
summary. Claude delegates to them based on their `description`, so you do not need to name them.

| Agent | Job | Tools |
|---|---|---|
| `triage` | What is broken, since when, how bad? Affected services, time range, impact, preliminary severity | `get_error_stats`, `search_logs` |
| `root-cause` | Most likely cause with evidence, ruled-out alternatives and confidence | all four MCP tools, `Read`, `Grep` |

Both are read-only (no `Write`, `Edit` or `Bash`): the agent proposes, the human decides. The main agent runs
`triage` first, then `root-cause` with the triage result, as described in the runbook skill below. Try: *Since about 2 pm customers are getting 500 errors at checkout. What is going on?*
Details: [docs/subagents.md](docs/subagents.md).

## Runbook skill

The skill [`incident-runbook`](.claude/skills/incident-runbook/SKILL.md) turns "how we handle incidents" into a
procedure that Claude loads when you report a production problem or ask for an incident report. Flow:

1. `triage` establishes what is broken, since when and how bad.
2. `root-cause` receives the full triage output and names the most likely cause with evidence and confidence.
3. Claude assigns the final severity (SEV-1 to SEV-4) using [`severity.md`](.claude/skills/incident-runbook/severity.md),
   justified with numbers from triage.
4. Claude writes the report from [`template.md`](.claude/skills/incident-runbook/template.md) to
   `reports/YYYY-MM-DD-short-title.md`. The date is the incident date (UTC) from the data. `reports/` is gitignored.
5. Claude replies with a short summary: severity, cause, confidence, proposed next steps and the report path.

Fixes are only proposed in the report, never applied. The report headings are fixed and checked by
`tests/test_skill.py`.

## Safety hooks

Three hooks in [`.claude/hooks/`](.claude/hooks/) apply deterministic rules to every tool call (configured in the
shared [`.claude/settings.json`](.claude/settings.json), standard library only, Windows and Linux):

| Hook | What it does |
|---|---|
| `block_destructive.py` | Blocks `rm -rf`, `Remove-Item -Recurse`, `rd /s`, `DROP`, `DELETE FROM`, `TRUNCATE`, `git push --force`, `git reset --hard`, `git clean -f` (exit code 2 with a reason) |
| `protect_files.py` | Blocks edits to `.env*` (except `.env.example`), `*.db` / `*.sqlite`, `data/` and `.github/workflows/` |
| `audit_log.py` | Appends one JSON line per tool call, and every blocked attempt, to `logs/audit.jsonl` (gitignored) |

If a hook blocks something, Claude explains why and asks you to do it manually. The hooks are a safety net, not a
security boundary: they are pattern based and can be bypassed. Details, manual testing and limitations:
[docs/hooks.md](docs/hooks.md).

More commands will be added as the project grows.

## Project layout

```text
.claude/        subagents, the incident-runbook skill and hooks for Claude Code
.github/        CI workflow, PR and issue templates
src/incident_agent/   Python package (log generator, queries, MCP server)
demo_app/       mini checkout service with a built-in bug
tests/          pytest suite
docs/           roadmap, architecture and examples
```

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Scenarios](docs/scenarios.md)
- [MCP tools reference](docs/mcp-tools.md)
- [Subagents](docs/subagents.md)
- [Safety hooks](docs/hooks.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
