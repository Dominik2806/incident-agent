# Roadmap

This document is the source of truth for how `incident-agent` is built. Every phase is one branch and one pull request.
Claude Code reads this file at the start of each phase.

## Project overview

**Incident Agent** is a Claude Code project that mimics an on-call engineer: it investigates a production error,
finds the root cause, proposes a fix and writes an incident report.

No real production environment is needed. A script generates realistic sample logs with a built-in incident,
and a custom MCP server makes this data queryable for Claude. Everything runs locally.

### Demo scenario

The user types into Claude Code:

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

1. **Triage:** affected services, start time, error rate, severity (e.g. SEV-2).
2. **Root cause:** first error, stack traces and deployments are cross-checked
   (e.g. "Deployment v1.4.2 at 14:01, KeyError in the payment service ever since").
3. **Fix proposal:** the affected code is read and a patch or rollback is proposed, but never applied automatically.
4. **Report:** an incident report in the runbook format is written to `reports/`.

The agent proposes, the human decides.

### Building blocks

| Building block | Implementation | Phase |
|---|---|---|
| Subagents | `triage`, `root-cause`, `fix-proposer` with own context and restricted tools | 3, 6 |
| Skill | `incident-runbook`: procedure, severity levels, report template | 4 |
| MCP server | `incident-logs` with four read-only tools on a SQLite DB | 2 |
| Hooks | PreToolUse blocker, PostToolUse audit log, later Notification and Stop | 5, 7 |

### Tech stack

| Component | Choice |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| MCP | Official Python SDK (FastMCP), stdio transport |
| Data | SQLite |
| Quality | pytest, ruff, GitHub Actions |
| License | MIT |

## Architecture

The main agent coordinates, the subagents analyze, the MCP server supplies the data. Every tool call passes through
the hooks: PreToolUse checks beforehand and can block, PostToolUse logs afterwards.

```text
incident-agent/
├── .claude/
│   ├── agents/              # triage.md, root-cause.md, fix-proposer.md
│   ├── skills/incident-runbook/   # SKILL.md, severity.md, template.md
│   ├── hooks/               # block_destructive.py, audit_log.py, ...
│   └── settings.json        # hook configuration (shared in the repo)
├── .github/                 # CI workflow, PR and issue templates
├── src/incident_agent/
│   ├── data/                # log generator, schema, scenarios
│   ├── queries.py           # query logic (testable without MCP)
│   └── mcp_server.py        # MCP server "incident-logs"
├── demo_app/                # mini checkout service with a built-in bug
├── tests/                   # pytest for generator, queries and hooks
├── docs/                    # ROADMAP.md, architecture.md, adr/, examples/
├── reports/                 # generated incident reports (gitignored)
├── .mcp.json                # registers the MCP server project-wide
├── CLAUDE.md                # project memory for Claude Code
└── README.md · CHANGELOG.md · CONTRIBUTING.md · LICENSE
```

### MCP tools (`incident-logs`, all read-only)

| Tool | Parameters | Returns |
|---|---|---|
| `search_logs` | service, level, time range, search text, limit | Matching log lines with timestamp, service, level, message |
| `get_error_stats` | time range, interval (e.g. 5 minutes) | Errors per service and time window, including error rate |
| `get_deployments` | service, time range | Deployments with version, time and author |
| `get_stacktrace` | error_id | Full stack trace and request context of an error |

The query logic lives in plain Python functions that are tested without MCP. The MCP server is a thin layer on top.

## Git workflow

- **`main` is protected.** No direct pushes. Changes arrive via pull request, CI must be green.
- **One branch per phase**, short-lived, created from `main`.
- **Squash merge.** Every PR becomes one clean commit on `main`; the branch is deleted afterwards.
- **Small PRs.** Two manageable pull requests are better than one huge one.

| Prefix | Used for | Example |
|---|---|---|
| `feature/` | New functionality | `feature/mcp-log-server` |
| `fix/` | Bug fix | `fix/stats-timezone` |
| `docs/` | Documentation only | `docs/release-v1` |
| `chore/` | Setup, CI, dependencies | `chore/repo-setup` |

**Commits** follow [Conventional Commits](https://www.conventionalcommits.org/): `feat`, `fix`, `docs`, `test`, `chore`
(for example `feat(mcp): add get_error_stats tool`).

**Versions** follow [Semantic Versioning](https://semver.org/) and are published as a Git tag with a GitHub release:
`v0.1.0` after the MVP (phase 5), `v1.0.0` after phase 8.

**Language:** code, commits, documentation and the README are in English.

### Routine for each phase

1. Create the branch: `git switch main; git pull; git switch -c <branch>`
2. Explore and plan: start Claude Code in Plan Mode, review the plan before any code is written.
3. Code: implement the plan, review intermediate steps, steer when needed.
4. Verify: `uv run pytest` and `uv run ruff check`, tick off the acceptance criteria.
5. Commit and open a PR: conventional commit, PR description, CHANGELOG entry under "Unreleased".
6. Merge: CI green, squash merge, delete the branch, update local `main`.

### Definition of Done

- **Works:** all acceptance criteria of the phase are met and checked by hand.
- **Tested:** new logic has pytest tests; CI is green.
- **Clean:** ruff reports nothing; no secrets and no generated files in the commit.
- **Documented:** README or `docs/` are updated, the CHANGELOG has an entry.
- **CLAUDE.md current:** new commands and conventions are in the project memory.
- **Merged:** pull request squash-merged into `main`, branch deleted.

## Phases at a glance

| Phase | Branch | Result | Effort | Status |
|---|---|---|---|---|
| 0 | `chore/repo-setup` | Project skeleton, CI, CLAUDE.md, templates | ~1 h | Done (#1) |
| 1 | `feature/sample-data` | Log generator and SQLite DB with incident scenario | ~2 h | Done (#2) |
| 2 | `feature/mcp-log-server` | MCP server with four tools, registered project-wide | ~3 h | Done (#3) |
| 3 | `feature/subagents` | Subagents `triage` and `root-cause` | 1–1.5 h | Done (#4) |
| 4 | `feature/runbook-skill` | Skill `incident-runbook` with report template | ~1 h | Done (#5) |
| 5 | `feature/safety-hooks` | Blocker, protection and audit hooks, **release v0.1.0 (MVP)** | 1–1.5 h | Done (#8), tag `v0.1.0` |
| 6 | `feature/demo-app-fix` | Demo app with bug and subagent `fix-proposer` | 2–3 h | Done (#10), plus 6b (#11) |
| 6b | `feature/legacy-client-correlation` | Checkout failures correlate with legacy client versions | n/a | Done (#11) |
| 7a | `feature/notify-stop-hooks` | Notification and Stop hooks | n/a | Done (#12) |
| 7b | `feature/scenario-db-pool` | Second scenario `db-pool` with the `auth` red herring | n/a | Done (#13) |
| 8 | `docs/release-v1` | Final docs, architecture diagram, **release v1.0.0** | ~2 h | Done: PR #15 merged, release `v1.0.0` published |

Suggested schedule: day 1 morning phases 0–1, afternoon phase 2; day 2 morning phases 3–5 (MVP), afternoon phases 6–8.

### Deviations from the plan

- **Phase 6b:** making the failures depend on the client version (so the agent can prove the correlation from the
  logs) was not planned. It became its own pull request (#11) after the demo app (#10) and changed the seed-42 data of
  `checkout-500`.
- **Phase 7 split:** the notification and stop hooks (7a, #12) and the second scenario (7b, #13) were separate pull
  requests instead of the single branch `feature/notify-scenario-2`.
- **Phase 8:** the roadmap asks for a demo GIF or screenshot; the README has none (no image is referenced).
  Open items as GitHub issues, the repo topics, the tag `v1.0.0` and the GitHub release are not part of this branch.
- **Pull request numbers:** the merged PRs are #1 to #5, #8 and #10 to #13; the numbers 6, 7 and 9 are not merged pull
  requests.

---

## Phase 0: Repo setup

**Branch:** `chore/repo-setup` · **Goal:** a clean public repository in which every later phase only adds content.

Tasks:

- Project skeleton: `pyproject.toml` with uv (Python 3.12), folder structure as in the architecture, package `src/incident_agent`.
- Quality: ruff and pytest, a GitHub Actions workflow that runs on every pull request.
- Docs foundation: README skeleton, `CONTRIBUTING.md`, `CHANGELOG.md`, PR and issue templates.
- `CLAUDE.md`: project goal, commands and conventions (Conventional Commits, branch names).

Done when:

- A test PR triggers CI and it runs green.
- `uv sync` and `uv run pytest` work on a fresh clone.
- README, `CLAUDE.md` and `docs/ROADMAP.md` describe the goal and the process of the project.

## Phase 1: Sample data

**Branch:** `feature/sample-data` · **Goal:** realistic, reproducible log data with a built-in incident the agent has to resolve.

Tasks:

- Schema: SQLite tables `logs`, `deployments` and `errors` (with stack trace and request context).
- Normal operation: the services `checkout`, `payment`, `catalog` and `auth` produce 24 hours of plausible INFO and WARN logs.
- Scenario `checkout-500`: deployment v1.4.2 of `payment` at 14:01; from 14:03 about 18% of checkout requests fail with `KeyError: 'currency'`.
- Noise: harmless warnings and an older, unrelated deployment, so the agent really has to search.
- Reproducible: fixed seed, run with `uv run generate-data --scenario checkout-500`.

Done when:

- The generator creates the DB in a few seconds; two runs with the same seed produce identical data.
- Tests check the schema, the time range and that the incident starts at the expected time.
- The generated `.db` file is gitignored; the scenario is described in `docs/`.

## Phase 2: MCP server

**Branch:** `feature/mcp-log-server` · **Goal:** Claude Code queries the log data through a custom MCP server, without direct database access.

Tasks:

- Query logic: pure Python functions in `queries.py` for search, statistics, deployments and stack traces.
- MCP layer: `mcp_server.py` with FastMCP, four tools with clear descriptions and typed parameters.
- Read-only: open the database in read-only mode, limit results (e.g. max. 200 rows) so the context does not overflow.
- Register project-wide: entry in `.mcp.json` so every clone gets the server automatically.
- Test: query functions with pytest, the server with the MCP Inspector or directly in Claude Code.

Done when:

- `/mcp` in Claude Code shows `incident-logs` as connected, with four tools.
- Asked "Which services had the most errors today?", Claude answers correctly with data from the server.
- Tests cover all four query functions, including empty results and limits.

## Phase 3: Subagents

**Branch:** `feature/subagents` · **Goal:** two specialized subagents handle triage and root-cause analysis in their own context and return only their result.

Tasks:

- `triage`: uses only `get_error_stats` and `search_logs`. Returns affected services, time range, error rate and severity.
- `root-cause`: uses all four MCP tools plus Read and Grep. Returns the most likely cause with evidence (timestamps, deployment, stack trace).
- Clear `description` fields that decide when Claude uses the subagent.
- Fixed output format so the main agent can process the results reliably.

Done when:

- With the demo prompt, Claude calls `triage` first, then `root-cause`, without the user naming them.
- `root-cause` names deployment v1.4.2 and the KeyError as the cause and backs it up with timestamps.
- The agent files live under `.claude/agents/` and are explained in the README.

## Phase 4: Runbook skill

**Branch:** `feature/runbook-skill` · **Goal:** the knowledge of "how we handle incidents" becomes a skill that Claude loads when needed.

Tasks:

- `SKILL.md`: name, description (when the skill applies) and the procedure: triage → cause → action → report.
- `severity.md`: severity matrix SEV-1 to SEV-4 with criteria such as error rate, affected customers and revenue impact.
- `template.md`: report template with summary, impact, timeline, cause, immediate action and follow-up tasks.
- Reports land in `reports/YYYY-MM-DD-short-title.md`; the folder is gitignored.

Done when:

- After the analysis Claude creates a report from the template without further prompting.
- The severity in the report is justified using the matrix.
- An example report is in the repo as `docs/examples/incident-report.md`.

## Phase 5: Safety hooks (MVP)

**Branch:** `feature/safety-hooks` · **Goal:** deterministic rules that apply on every tool call. After this the MVP is done.

| Hook script | Event / matcher | Behavior |
|---|---|---|
| `block_destructive.py` | PreToolUse / Bash | Blocks `rm -rf`, `DROP`, `DELETE FROM`, `TRUNCATE`, `git push --force` with exit code 2 and a reason |
| `protect_files.py` | PreToolUse / Edit, Write | Prevents changes to `.env`, the log database and `.github/workflows` |
| `audit_log.py` | PostToolUse / all | Writes every tool call as a JSON line to `logs/audit.jsonl` |

Configured in `.claude/settings.json`, paths via `$CLAUDE_PROJECT_DIR`. The scripts receive JSON on stdin.

Done when:

- An attempt to run `rm -rf` or `DROP TABLE` is blocked, and Claude states the reason.
- `logs/audit.jsonl` contains all tool calls after an analysis; the folder is gitignored.
- Tests for all hook scripts run in CI.
- Release `v0.1.0` is published (tag and GitHub release "MVP").

## Phase 6: Demo app and fix-proposer

**Branch:** `feature/demo-app-fix` · **Goal:** the error from the logs also exists in the code, so the agent can make a real fix proposal.

Tasks:

- Demo app: small checkout service in `demo_app/` (e.g. FastAPI) with a payment module whose stack traces match the generated logs.
- Built-in bug: v1.4.2 requires a field `currency` that older clients do not send (the KeyError from the logs).
- Test: a test marked `xfail` reproduces the bug as long as it is not fixed.
- `fix-proposer`: subagent with only Read, Grep and Glob (no write permissions). Returns a patch as a diff, a rollback alternative and a risk assessment.

Done when:

- `fix-proposer` finds the spot in the code and proposes a patch that would turn the `xfail` test green.
- The report contains patch and rollback option as immediate actions.
- The stack traces in the generator point to real files and lines of the demo app.

The bug is never fixed on `main`, it is the basis of the demo.

## Phase 7: Notifications and second scenario

**Branch:** `feature/notify-scenario-2` · **Goal:** the agent gets in touch when it needs a human, checks its own report and passes a second, trickier scenario.

Tasks:

- Notification hook: desktop notification, optionally Slack via a webhook in an environment variable (`SLACK_WEBHOOK_URL`).
- Stop hook: checks that the report has all required sections from `template.md`; if not, exit code 2 with a note on what is missing. It must check `stop_hook_active` and only apply after an incident analysis, otherwise it causes an endless loop.
- Scenario `db-pool`: a traffic spike exhausts the connection pool of `catalog`, timeouts rise. A harmless `auth` deployment shortly before is a red herring.
- Both scenarios are described in `docs/scenarios.md`, including the expected cause.

Done when:

- If a required section is missing in the report, Claude keeps working until it is complete.
- In scenario `db-pool`, `root-cause` names the connection pool as the cause, not the `auth` deployment.
- Notifications arrive; without the webhook variable everything keeps running without errors.

## Phase 8: Docs and release

**Branch:** `docs/release-v1` · **Goal:** the repo is understandable to visitors in two minutes and runnable with three commands.

Tasks:

- Final README: badges (CI, license, Python), demo GIF or screenshot, quickstart in three commands, features, roadmap status.
- `docs/architecture.md`: Mermaid architecture diagram, explanation of each building block and of the data flow.
- `docs/adr/`: short Architecture Decision Records, e.g. "Why SQLite", "Why fix-proposer has no write access".
- Clean-up: finish the CHANGELOG for v1.0.0, create open items as GitHub issues, check repo topics.
- Release: tag `v1.0.0` and GitHub release with highlights and a link to the example report.

Done when:

- A fresh clone runs through the quickstart commands without any questions.
- The README explains in under two minutes of reading time what the project is and how to start it.
- Release `v1.0.0` is published; all phase branches are merged and deleted.

## Outlook

- Bundle agents, skill, hooks and MCP server as a Claude Code plugin.
- Connect real data sources, e.g. ready-made MCP servers of monitoring tools such as Sentry.
- Run the agent on a schedule and report only when it finds anomalies.
- More scenarios: memory leak, expired certificate, outage of a third-party API.
- Evaluation: record the expected cause per scenario and automatically check whether the agent finds it.
