# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `score-report`: deterministic scoring of an incident report against the expected result of a scenario (root cause
  named, red herring ruled out, severity, confidence, per-client counts reconciled with the database, no inferred
  counts). No model is called; tests run it on saved reports. See `docs/evaluation.md` and ADR 0010 (#20).

## [1.1.0] - 2026-10-17

### Added

- `get_error_stats`: optional parameters `service` (one service), `errors_only` (only windows with `ERROR` lines)
  and `offset` (paging), and the result fields `total_windows`, `offset`, `omitted_windows` and `next_offset`.
  Existing calls and result fields are unchanged (#6).

### Changed

- `get_error_stats`: a truncated result (the default call over the full day has 1145 windows, the cap is 200) now
  states how many windows were returned, how many are omitted, the last window shown and how to get the rest
  (`offset=<next_offset>`, a narrower range, `service`, `errors_only` or a larger interval), instead of a generic hint (#6).
- `root-cause`: confidence is exactly `high`, `medium` or `low`; per-client counts (every version present, reconciled
  against the total) and request volume per window are measured with `search_logs` instead of inferred; failed or
  truncated tool results are reported (#7, #14).
- `triage`: starts with a coarse `get_error_stats` interval and drills down with `service` and `errors_only`, following
  `next_offset`, instead of one truncated full-day call (#6).
- Version bumped to 1.1.0 (`pyproject.toml`, `incident_agent.__version__`, `uv.lock`).

## [1.0.0] - 2026-10-17

First stable release. Builds on the 0.1.0 MVP (data generator, MCP server, two subagents, runbook skill, safety
hooks) with the demo app, the third subagent, the notification and stop hooks, a second scenario and the final docs.

### Added

- **Demo app and fix proposal:** `demo_app/`, a stdlib-only checkout/payment service (payment v1.4.2) with the
  intentional `KeyError: 'currency'` bug (never fixed on `main`; strict `xfail` in `tests/test_demo_app.py`), and the
  subagent `fix-proposer` (`Read`, `Grep`, `Glob`) that proposes a patch as a diff, a rollback and a risk assessment.
  The runbook skill calls it after `root-cause`; the report shows `### Patch`, `### Rollback` and `### Risks`.
  `src/incident_agent/data/frames.py` is the one table of stack frames, checked against the real lines.
- **Second scenario `db-pool`** (`uv run generate-data --scenario db-pool`): a traffic spike on catalog `GET /search`
  (up to 3.6x) exhausts the connection pool (`pool_size=20`); pool `WARN` lines precede `TimeoutError` (HTTP 504/503)
  errors that stop when the load decays. The harmless `auth` v3.2.0 deployment about 9 minutes before the first error
  is the red herring. Expected severity SEV-3. `checkout-500` output is unchanged by it (pinned by a hash test).
- **Notification hook** `.claude/hooks/notify.py`: best-effort desktop notification (Windows, macOS, Linux) and an
  optional Slack message via `SLACK_WEBHOOK_URL` (3 s timeout, URL never printed or logged). Always exits 0 and is
  silent; `INCIDENT_NOTIFY_DESKTOP=0` disables the desktop part. New `.env.example`.
- **Stop hook** `.claude/hooks/check_report.py`: when this session wrote a report to `reports/`, blocks stopping (rule
  `report-incomplete`, exit code 2) until all `##` sections of the runbook `template.md` (parsed at runtime) are
  present and not empty. Honors `stop_hook_active` (one correction round), finds the report through the audit log and
  fails open.
- **Docs:** rewritten README (quickstart in three commands, demo prompt, safety limits, roadmap status),
  `docs/architecture.md` with Mermaid diagrams, `docs/adr/` with an index and nine decision records,
  `docs/scenarios.md` for both scenarios, a status column and a deviations note in `docs/ROADMAP.md`, and example
  reports for both scenarios in `docs/examples/`. `docs/hooks.md` documents the heredoc false positive.
- **Tests:** `tests/test_docs_links.py` (every relative markdown link and anchor in the README and `docs/` resolves),
  `tests/test_scenario_db_pool.py` (including the pinned `checkout-500` hash), `tests/test_demo_app.py`, tests for the
  new hooks in `tests/test_hooks_notify_stop.py`, and a check of the example reports against the template headings.

### Changed

- `checkout-500` data now matches the demo app story: only legacy clients (`web-2.30.4`, `android-5.7.9`,
  `LEGACY_CLIENT_VERSIONS`) fail, about 95% of their requests; `web-2.31.0` and `ios-5.8.1` never fail. Legacy
  clients are 19% of traffic (new `CLIENT_WEIGHTS`), the overall failure rate stays about 18%. All checkout log lines
  end in `client=<version>`. Seed-42 values changed (request ids, latencies, noise timestamps; the first failure is at
  14:03:32).
- Stack frames of `payment` and `checkout` point to real files and lines of `demo_app/`; `catalog` and `auth` frames
  stay synthetic.
- `root-cause` compares failing and healthy checkout requests per client version with `search_logs` and reports the
  counts, and what it could not count, under Evidence.
- `add_noise` takes an optional `skip_one_off_services` (default none); `db-pool` leaves out the auth one-off.
- pytest `pythonpath = ["."]` so tests can import `demo_app`.
- Version bumped to 1.0.0 (`pyproject.toml`, `incident_agent.__version__`, `uv.lock`).

## [0.1.0] - 2026-10-16

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
- `.mcp.json` registering the server project-wide, and the `mcp` SDK (`>=1.30.0,<2`) as a runtime dependency.
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

- Safety hooks in `.claude/hooks/` (Python standard library only, Windows and Linux): `block_destructive.py`
  (PreToolUse / Bash: `rm -rf`, `Remove-Item -Recurse`, `rd /s`, `del /s`, `DROP`, `DELETE FROM`, `TRUNCATE`,
  `git push --force`, `git reset --hard`, `git clean -f`), `protect_files.py` (PreToolUse / file tools: `.env*`,
  `*.db` / `*.sqlite`, `data/`, `.github/workflows/`) and `audit_log.py` (PostToolUse / all tools, JSON lines in
  `logs/audit.jsonl`, `INCIDENT_AUDIT_LOG` override), plus the shared helper `_common.py`. Blocked attempts are also
  written to the audit log. Guards fail closed, the audit hook fails open.
- `.claude/settings.json` configuring the hooks via `$CLAUDE_PROJECT_DIR`; `.claude/settings.local.json` is gitignored.
- `tests/test_hooks.py` running every hook as a subprocess (blocked and allowed commands and paths, audit output,
  failure policy, non-ASCII input, settings.json checks).
- `docs/hooks.md` and a "Safety hooks" section in the README.

### Changed

- "Incident workflow" in `CLAUDE.md` now points to the `incident-runbook` skill; the final severity comes from
  `severity.md`, the triage severity stays preliminary (wording in `triage.md` and `docs/subagents.md`).
- `CLAUDE.md`: rule "if a hook blocks something, do not work around it, tell the user", hooks convention and
  audit log location.
- Version bumped to 0.1.0 (MVP).

[Unreleased]: https://github.com/Dominik2806/incident-agent/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Dominik2806/incident-agent/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Dominik2806/incident-agent/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/Dominik2806/incident-agent/releases/tag/v0.1.0
