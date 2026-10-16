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
```

## Incident workflow

When the user reports a production problem (errors, outage, elevated error rate):

Use the `incident-runbook` skill (`.claude/skills/incident-runbook/`). It defines the whole procedure: triage,
root cause, final severity and the report in `reports/`. Subagents cannot call each other, so you orchestrate.

Never apply fixes automatically; only propose them.

## Conventions

- **Language:** code, commits, docs and README are in English.
- **Layout:** package in `src/incident_agent/`, tests in `tests/`, agents in `.claude/agents/`, the skill in
  `.claude/skills/incident-runbook/`, generated reports in `reports/` (gitignored). Keep query logic in plain Python functions
  that are testable without MCP.
- **Sample data:** the generator lives in `src/incident_agent/data/`; new scenarios are functions registered in
  `SCENARIOS` (`scenarios.py`) and described in `docs/scenarios.md`. Output goes to `data/` (gitignored, never
  commit it). Data must stay deterministic: fixed reference date, seeded `random.Random`, never `datetime.now()`.
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
- **Skill:** `.claude/skills/incident-runbook/` holds `SKILL.md` (procedure, name = directory name, description
  under 1024 characters), `severity.md` (SEV-1 to SEV-4) and `template.md` (report template). The H2 headings of
  `template.md` are machine-checked by `tests/test_skill.py` (and later by a Stop hook): change them only together
  with the tests. Reports go to `reports/YYYY-MM-DD-short-title.md` (incident date in UTC, gitignored). The final
  severity comes from `severity.md`; the triage agent's severity is preliminary.
- **Branches:** one per phase, created from `main`, named `feature/`, `fix/`, `docs/` or `chore/` plus a
  short kebab-case description (e.g. `feature/mcp-log-server`). Never push directly to `main`.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) with types `feat`, `fix`, `docs`,
  `test`, `chore` (e.g. `feat(mcp): add get_error_stats tool`).
- **PRs:** squash-merged; add a CHANGELOG entry under "Unreleased".
- **Never commit** secrets, `.env`, `*.db`, `reports/` or `logs/`.
- Update this file when new commands or conventions are introduced.
