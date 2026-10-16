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

## Conventions

- **Language:** code, commits, docs and README are in English.
- **Layout:** package in `src/incident_agent/`, tests in `tests/`. Keep query logic in plain Python functions
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
- **Branches:** one per phase, created from `main`, named `feature/`, `fix/`, `docs/` or `chore/` plus a
  short kebab-case description (e.g. `feature/mcp-log-server`). Never push directly to `main`.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) with types `feat`, `fix`, `docs`,
  `test`, `chore` (e.g. `feat(mcp): add get_error_stats tool`).
- **PRs:** squash-merged; add a CHANGELOG entry under "Unreleased".
- **Never commit** secrets, `.env`, `*.db`, `reports/` or `logs/`.
- Update this file when new commands or conventions are introduced.
