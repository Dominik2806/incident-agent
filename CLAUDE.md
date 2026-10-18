# CLAUDE.md

## Project goal

`incident-agent` is a Claude Code agent that mimics an on-call engineer: it investigates a production error from
logs, finds the root cause, proposes a fix and writes an incident report. Sample logs are generated locally and
exposed through a custom MCP server (`incident-logs`). The agent proposes, the human decides: never apply fixes
automatically.

**Read `docs/ROADMAP.md` at the start of each phase.** It is the source of truth for scope, architecture,
git workflow and the Definition of Done. Work on one phase at a time.

## Tech stack

Python 3.12, uv, SQLite, the official MCP Python SDK (FastMCP, stdio), pytest, ruff, GitHub Actions.

## Commands

```bash
uv sync                  # install dependencies
uv run pytest            # run tests
uv run ruff check        # lint
uv run ruff format       # format (CI runs `ruff format --check`)
uv run generate-data --scenario checkout-500   # build data/incident.db (--seed, --output optional)
uv run incident-logs-server                    # start the MCP server (stdio; normally started via .mcp.json)
uv run score-report REPORT.md --scenario db-pool   # score a report against the expected result (no model, no key)
uv run run-eval --scenario db-pool --runs 1    # LIVE: runs claude -p, costs money, local only (--dry-run first)
```

## Incident workflow

When the user reports a production problem (errors, outage, elevated error rate):

Use the `incident-runbook` skill (`.claude/skills/incident-runbook/`). It defines the whole procedure: triage,
root cause, final severity and the report in `reports/`. Subagents cannot call each other, so you orchestrate.

Never apply fixes automatically; only propose them.

**If a hook blocks something, do not work around it** (no rewording, no other tool or shell, no editing the hooks or
`.claude/settings.json`). Tell the user what you wanted to do and why it was blocked, and let them decide or do it
manually.

## Conventions

- **Language:** code, commits, docs and README are in English.
- **Layout:** package in `src/incident_agent/`, tests in `tests/`, agents in `.claude/agents/`, the skill in
  `.claude/skills/incident-runbook/`, hooks in `.claude/hooks/` (configured in `.claude/settings.json`), generated
  reports in `reports/` and the audit log in `logs/` (both gitignored), docs in `docs/` (architecture in
  `docs/architecture.md`, decision records in `docs/adr/`, example reports in `docs/examples/`). Keep query logic in plain Python functions
  that are testable without MCP.
- **Sample data:** the generator lives in `src/incident_agent/data/`; new scenarios are functions registered in
  `SCENARIOS` (`scenarios.py`) and described in `docs/scenarios.md`. Output goes to `data/` (gitignored, never
  commit it). Legacy clients (`LEGACY_CLIENT_VERSIONS` in `data/util.py`) are the only ones the bug fails, and every
  `checkout` log line ends in `client=<version>`. Data must stay deterministic: fixed reference date, seeded
  `random.Random`, never `datetime.now()`. Scenarios: `checkout-500` (default), `db-pool` (catalog connection
  pool exhausted by a `GET /search` spike; the harmless auth v3.2.0 deployment is the red herring; catalog frames
  are synthetic; `uv run generate-data --scenario db-pool`), `cert-expired` (payment TLS certificate expires at
  15:20, all checkouts fail, all clients equally, SEV-1; auth v3.3.0 is the red herring) and `api-outage` (card
  processor Payvia down 16:05 to 17:20, about 40% of checkouts fail with 502/504, resolved, SEV-2; checkout v2.7.1 is
  the red herring). A scenario runs last and must never change another
  scenario's output: draw only from streams derived from its `rng` (as `db_pool` does), put shared-noise opt-outs
  in `NOISE_SKIP_ONE_OFFS` (skipped entries still consume their draws), and keep float math to `+ - * /` with only
  ints and formatted strings reaching the database. `tests/test_scenario_db_pool.py` pins the ordered hash of
  `checkout-500`, `db-pool` and `cert-expired` (seed 42); update it only for an intended change of that scenario.
- **MCP server:** `src/incident_agent/queries.py` holds all query logic (no MCP imports, takes a
  `sqlite3.Connection`); `src/incident_agent/mcp_server.py` is a thin FastMCP layer; `.mcp.json` registers it
  (no absolute paths). The server reads `data/incident.db` or `INCIDENT_DB_PATH`, opens it read-only, uses
  parameterized SQL only and caps results at 200 rows. **Never print to stdout in the server** (stdio carries
  the protocol); log to stderr. The SDK is pinned to `mcp<2` because v2 renamed `FastMCP`. Tool reference:
  `docs/mcp-tools.md`; keep it in sync when tools change.
- **Subagents:** `.claude/agents/<name>.md` (YAML frontmatter `name` = filename, `description`, explicit `tools`,
  `color`). MCP tools are referenced as `mcp__incident-logs__<tool>`. Least privilege: never `Write`, `Edit`, `Bash`
  or `NotebookEdit`; the output sections are fixed and checked by `tests/test_agents.py`. Reference:
  `docs/subagents.md`; keep it in sync when agents change.
  `fix-proposer` has no MCP tools: its tools are exactly `Read`, `Grep`, `Glob` (checked by tests).
- **Demo app:** `demo_app/` (top level, stdlib only, not packaged into the wheel; pytest finds it via
  `pythonpath = ["."]`) holds the intentional `KeyError: 'currency'` bug of payment v1.4.2. **Never fix it on
  `main`**; `tests/test_demo_app.py` has a strict `xfail` for it. The stack frames of payment and checkout in the
  generator come from one table, `src/incident_agent/data/frames.py`, and must point to real lines of `demo_app/`
  (checked by tests): change them together. Frames of catalog and auth stay synthetic. Reference: `docs/demo-app.md`.
- **Skill:** `.claude/skills/incident-runbook/` holds `SKILL.md` (procedure, name = directory name, description
  under 1024 characters), `severity.md` (SEV-1 to SEV-4) and `template.md` (report template). The H2 headings of
  `template.md` are machine-checked by `tests/test_skill.py` (and later by a Stop hook): change them only together
  with the tests. Reports go to `reports/YYYY-MM-DD-short-title.md` (incident date in UTC, gitignored). The final
  severity comes from `severity.md`; the triage agent's severity is preliminary.
- **Hooks:** `.claude/hooks/` holds `block_destructive.py` (PreToolUse, Bash), `protect_files.py` (PreToolUse,
  Edit/Write/MultiEdit/NotebookEdit), `audit_log.py` (PostToolUse, all tools) and the shared `_common.py`. Python
  standard library only, no absolute or user-specific paths (use `$CLAUDE_PROJECT_DIR` in `.claude/settings.json`),
  fast. Guards exit 2 with an ASCII-only message and fail closed on invalid input; the audit hook always exits 0.
  Rules live in the `RULES` table of `block_destructive.py`. Read stdin as UTF-8 bytes. Audit log:
  `logs/audit.jsonl` (override with `INCIDENT_AUDIT_LOG`); it also records blocked attempts.
  `notify.py` (Notification) is best effort: desktop notification plus Slack if `SLACK_WEBHOOK_URL` is set; it is
  silent and always exits 0, and the webhook URL is a secret (never print, log or audit it). `check_report.py`
  (Stop) blocks with rule `report-incomplete` while a report written this session (found via the audit log) lacks a
  `##` section of `template.md` (parsed at runtime, never copied) or has an empty one; it returns 0 if
  `stop_hook_active` is true or on any error (fail open). Environment variables: `INCIDENT_AUDIT_LOG`,
  `SLACK_WEBHOOK_URL`, `INCIDENT_NOTIFY_DESKTOP=0` (tests and CI; see `.env.example`). Tests:
  `tests/test_hooks.py`, `tests/test_hooks_notify_stop.py`. Reference: `docs/hooks.md`; keep it in sync when hooks
  change. Personal overrides go in
  `.claude/settings.local.json` (gitignored).
- **Evaluation:** `src/incident_agent/evaluation/` scores a finished report against `EXPECTED` (one `ExpectedResult` per
  scenario, enforced by a test) with plain Python; ground truth comes from the generated database. Fixtures in
  `tests/fixtures/eval/`; `docs/examples/` stays unchanged as a historical oracle. A new scenario needs an
  `ExpectedResult` and a good fixture. The live runner `run-eval` (`runplan.py` pure logic, `runner.py` the only
  subprocess seam, `run_cli.py`) is local only and costs money: never call it from CI, tests or the agent; tests use
  `tests/fake_claude.py`. It never keeps changes in `reports/` (existing files are restored); results go to
  `evals/results/` (gitignored). Reference: `docs/evaluation.md`, ADR 0010.
- **Docs:** every relative markdown link in `README.md` and `docs/**/*.md` (and its `#anchor`) must resolve;
  `tests/test_docs_links.py` checks it. Decisions go into `docs/adr/` (one short ADR per decision, listed in its
  `README.md`). The README quickstart must work on a fresh clone with no file that is gitignored.
- **Branches:** one per phase, created from `main`, named `feature/`, `fix/`, `docs/` or `chore/` plus a
  short kebab-case description (e.g. `feature/mcp-log-server`). Never push directly to `main`.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) with types `feat`, `fix`, `docs`,
  `test`, `chore` (e.g. `feat(mcp): add get_error_stats tool`).
- **PRs:** squash-merged; add a CHANGELOG entry under "Unreleased".
- **Never commit** secrets, `.env`, `*.db`, `reports/` or `logs/`.
- Update this file when new commands or conventions are introduced.
