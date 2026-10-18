# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The skill's worked example in `severity.md` and the example report name in `SKILL.md` are neutral (a nightly export
  job, SEV-4) instead of `checkout-500`, closes #37. The answer of a scenario is no longer inside the agent under test.
  The canary test (`tests/test_workcopy.py`) no longer pins any exception. The baseline must be measured after this
  change.
- Live evaluation (`run-eval`) is isolated, closes #35 (ADR 0011). In the first live runs the agents worked in the
  repository root and read the generator (`src/incident_agent/data/scenarios.py`) and the docs in nearly every run. Each
  run now executes in a sanitized working copy in the system temp directory (`evaluation/workcopy.py`): `.claude/`,
  `demo_app/*.py`, `tests/test_demo_app.py`, an empty `reports/` and a neutral `CLAUDE.md` built from the
  `## Incident workflow` section of the real one. Reads are scoped with `Read(./**)` (no bare `Read`, `Grep` or `Glob`),
  probed with Claude Code 2.1.302. The real `reports/` is never touched, so the backup and restore logic is gone.
- `run-eval` scans each run's `audit.jsonl` and marks a run **CONTAMINATED** when a Read, Grep or Glob targeted a place
  outside the copy or `docs/`, `src/`, `tests/fixtures` or `evals/`. A contaminated run is never a pass (the scenario is
  labelled `CONTAMINATED`, exit code 1); calls the permission rules denied are only recorded as `blocked_attempts`.
  `record.json` gains `contaminated`, `contamination`, `blocked_attempts`, `workcopy_files`, `workcopy_removed`; the run
  folder gains `workcopy-manifest.json`. A failing removal of the copy is a warning, not an error.
- The generator-coupled frame tests moved from `tests/test_demo_app.py` to `tests/test_demo_frames.py` (the first file is
  copied into the sandbox), and the NOTE comments of `demo_app/` no longer name the generator (same lines, no line moved).
- `docs/evaluation.md`: isolation and threat model, the contaminated verdict, and a caveat that the measured baseline
  predates the isolation and must be re-measured (its numbers are unchanged).

### Fixed

- Scorer: per-client counts accept the prose shapes `client had N errors and M ok` (failed N, ok M) and
  `client had N errors in M` (N failed of M total), closes #41. A live `checkout-500` run wrote
  `web-2.30.4 had 275 errors and 13 ok (95% failed), ... web-2.31.0 had 0 errors in 1092` and `counts-present` and
  `counts-reconciled` failed. Both shapes have their own patterns in `report.py`; `FAILED_WORD` is unchanged, so
  `N ok / M errors` is still not a pair. In `errors in M`, M must end the clause (optionally after `requests`, `lines`
  or `log lines`), so a time such as `3 errors in 14:00 UTC` is not a count; N greater than M is invalid. The real
  report is the fixture `real-checkout-500-prose-errors-and-ok.md`.
- Scorer: per-client counts accept an `(INFO / ERROR)` pair label like `(ok / failed)`, closes #38. The first isolated
  live `checkout-500` run wrote a correct report with `(INFO / ERROR): web-2.30.4 13 / 275; ...; web-2.31.0 1092 / 0`,
  and `counts-present` and `counts-reconciled` failed because the bare pairs were read as failed/total. INFO is ok and
  ERROR is failed (`OK_FAILED_LABEL` in `report.py`); nothing else is loosened (`(ERROR / INFO)` is not a label, prose
  such as `(1092 OK)` is not parsed). The real report is the fixture `real-checkout-500-info-error-labels.md`.
- Scorer: `root-cause-named` no longer fails a report whose Root cause section names the red herring only in its
  confidence statement (a live `api-outage` run wrote "the checkout v2.7.1 deployment cannot be fully excluded" after
  a correct cause). The forbidden patterns are now checked against the cause statement, that is the section without
  its confidence statement (`split_confidence` in `report.py`); the required cause terms are still searched in the
  whole section, and a section without a confidence label is checked as a whole, as before. Naming the red herring in
  the cause statement still fails, negated or not. All 20 saved reports under `evals/results` keep their result
  except that run, which now passes. New regression fixture
  `tests/fixtures/eval/real-api-outage-red-herring-in-confidence.md`.

### Added

- Third scenario `cert-expired` (`uv run generate-data --scenario cert-expired`), part of #19: the TLS server certificate of
  `payment.internal` used by `checkout` expires at 15:20:00 UTC, from then on about 98% of the checkout requests fail with
  HTTP 502 (`SSLCertVerificationError: certificate has expired`, `notAfter` in the message), for all client versions equally,
  until the end of the day. The `auth` v3.3.0 deployment at 15:12 is the red herring. Expected severity SEV-1 (the first one).
- `EXPECTED["cert-expired"]` for the scorer, `TOTAL_FAILED_SQL` in `expected.py` (generic ground truth of the scenarios
  without a client split; `db-pool` keeps its query), its demo prompt in `PROMPTS`, the fixture
  `tests/fixtures/eval/good-cert-expired.md` and `tests/test_scenario_cert_expired.py`.
- `tests/test_scenario_db_pool.py` pins the ordered hash of `db-pool` (seed 42) next to the one of `checkout-500`.
- Fourth scenario `api-outage` (`uv run generate-data --scenario api-outage`), part of #19: the external card
  processor `Payvia` called by `payment` is down from 16:05:00 to 17:20:00 UTC. About 40% of the checkout requests of the
  window fail (HTTP 502 after provider 503 responses, HTTP 504 after timeouts, 3 attempts each), for all client versions
  alike, and everything recovers with no deployment. The `checkout` v2.7.1 deployment at 15:55 is the red herring.
  The first resolved incident of a core flow; expected severity SEV-2 (no downgrade for a resolved incident, see
  `docs/scenarios.md`).
- `EXPECTED["api-outage"]` and its `TOTAL_FAILED_SQL` query, its demo prompt in `PROMPTS`, the fixture
  `tests/fixtures/eval/good-api-outage.md`, `tests/test_scenario_api_outage.py` and scorer tests.
- `tests/test_scenario_db_pool.py` also pins the ordered hash of `cert-expired` (seed 42).

### Changed

- `docs/scenarios.md`, `docs/evaluation.md`, README, CLAUDE.md and `docs/architecture.md` list the new scenario. The dry-run
  test of the runner expects three scenarios for `--all`.
- `docs/evaluation.md` states the budget rule of `run-eval` as a formula (a run starts only if the spent total plus the
  per-run cap is within the total cap) and lists `api-outage`; the dry-run test now expects four scenarios for `--all`.

## [1.2.2] - 2026-10-17

### Changed

- `docs/evaluation.md`: measured baseline of the 2026-10-17 calibration run (v1.2.1, 3 runs per scenario, $2.61 in
  total), the `--total-budget-usd 4.00` rule for six runs and the known limits of the scorer. Docs only.
- Version bumped to 1.2.2 (`pyproject.toml`, `incident_agent.__version__`, `uv.lock`).

### Fixed

- `score-report`: a third false negative on a correct report. An explicit per-pair label `client N ok / M failed`
  (also `failures`) is now read as ok/failed and declares that order for the bare `N / M` pairs after it on the same
  line (`web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; web-2.30.4 13 / 275`). Bare pairs before it, on other lines
  or without any explicit label keep the failed/total reading. A line with explicit pairs of both orders is
  unreadable and fails the count checks with a clear detail.

## [1.2.1] - 2026-10-17

### Fixed

- `score-report`: two false negatives on a correct report. Per-client counts written as an inline list under an
  explicit `(ok / failed)` label (`web-2.31.0 1092 / 0`) are now read as ok/failed, zeros included; without the label
  the failed/total reading is unchanged. The checkout-500 root cause accepts "no default" / "without a default" in
  place of the literal `KeyError`; `payment`, `v1.4.2` and `currency` are still required there.

### Changed

- Version bumped to 1.2.1 (`pyproject.toml`, `incident_agent.__version__`, `uv.lock`).

## [1.2.0] - 2026-10-17

### Added

- `score-report`: deterministic scoring of an incident report against the expected result of a scenario (root cause
  named, red herring ruled out, severity, confidence, per-client counts reconciled with the database, no inferred
  counts). No model is called; tests run it on saved reports. See `docs/evaluation.md` and ADR 0010 (#20).
- `run-eval`: local live runner. Runs the full `incident-runbook` flow with `claude -p` N times per scenario (default 1)
  against a fresh database and a generated MCP config, scores every report with the scorer and prints per-check pass
  rates, totals (cost, turns, duration) and a verdict. Has per-run and total budget caps, a timeout, `--dry-run` and a
  confirmation; restores existing files in `reports/`; results go to the gitignored `evals/results/`. Not used in CI (#20).

### Changed

- Version bumped to 1.2.0 (`pyproject.toml`, `incident_agent.__version__`, `uv.lock`).

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

[Unreleased]: https://github.com/Dominik2806/incident-agent/compare/v1.2.2...HEAD
[1.2.2]: https://github.com/Dominik2806/incident-agent/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/Dominik2806/incident-agent/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Dominik2806/incident-agent/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Dominik2806/incident-agent/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Dominik2806/incident-agent/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/Dominik2806/incident-agent/releases/tag/v0.1.0
