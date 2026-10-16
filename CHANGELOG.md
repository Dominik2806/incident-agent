# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project skeleton: `pyproject.toml` (uv, Python 3.12, src layout), folder structure, placeholder test.
- GitHub Actions workflow running ruff and pytest on pull requests and pushes to `main`.
- README skeleton, `CONTRIBUTING.md`, `CLAUDE.md`, pull request and issue templates.
- `.gitignore` entries for `reports/`, `logs/`, `*.db` and `.env`.
- Sample data generator in `src/incident_agent/data/`: SQLite schema (`logs`, `deployments`, `errors`),
  24 hours of deterministic normal operation on the fixed date 2026-03-12 (seeded, UTC) and a scenario registry.
- Scenario `checkout-500`: `payment` v1.4.2 at 14:01, about 18% of checkouts fail with `KeyError: 'currency'`
  from 14:03, plus noise (recurring WARNs, one-off errors, older deployments).
- Console script `generate-data` (`--scenario`, `--seed`, `--output`) that prints row counts and errors per hour.
- `docs/scenarios.md` describing the scenario, its timeline, expected root cause and red herrings.
- Tests for schema, time range, determinism, incident start, error rate and consistency of failed requests.
- `/data/` in `.gitignore` for the generated database.
- MCP server `incident-logs` (`src/incident_agent/mcp_server.py`, FastMCP over stdio) with the read-only tools
  `search_logs`, `get_error_stats`, `get_deployments` and `get_stacktrace`; console script `incident-logs-server`.
- `src/incident_agent/queries.py`: query logic as plain functions with input validation, a 200-row limit and a
  read-only SQLite connection; database path from `INCIDENT_DB_PATH` (default `data/incident.db`).
- `.mcp.json` registering the server project-wide, and the `mcp` SDK (`>=1.0,<2`) as a runtime dependency.
- `docs/mcp-tools.md` tool reference and an "MCP server" section in the README.
- Tests for all query functions and for the tools exposed by the server.
- Subagent `triage` (`.claude/agents/triage.md`): read-only, only `get_error_stats` and `search_logs`; reports affected
  services, time range, impact, preliminary severity, suspected scope and handoff notes in a fixed format.
- Subagent `root-cause` (`.claude/agents/root-cause.md`): all four `incident-logs` tools plus `Read` and `Grep`;
  reports root cause, confidence, evidence, ruled-out alternatives, unknowns and a proposed next step.
- "Incident workflow" in `CLAUDE.md`: delegate to `triage`, then to `root-cause` with the full triage output, then
  summarize; fixes are only proposed.
- `tests/test_agents.py` (hand-written frontmatter parser, no new dependency): checks names, descriptions, tool
  allowlist, forbidden tools and required output sections.
- `docs/subagents.md` and a "Subagents" section in the README.
- Skill `incident-runbook` (`.claude/skills/incident-runbook/`): `SKILL.md` with the procedure (triage, root cause, final
  severity, report), `severity.md` (SEV-1 to SEV-4 matrix with a checkout-500 example) and `template.md` (incident
  report with fixed headings). Reports go to `reports/YYYY-MM-DD-short-title.md` (gitignored).
- `tests/test_skill.py`: checks skill frontmatter, referenced files, severity levels and template headings (and the
  example report `docs/examples/incident-report.md` once it exists).
- "Runbook skill" section in the README.

### Changed

- "Incident workflow" in `CLAUDE.md` now points to the `incident-runbook` skill; the final severity comes from
  `severity.md`, the triage severity stays preliminary (wording in `triage.md` and `docs/subagents.md`).
