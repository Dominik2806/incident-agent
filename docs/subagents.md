# Subagents

Phase 3 adds two project subagents in `.claude/agents/`. Each runs in its own context window with a restricted
tool set and returns only its result to the main agent.

| Agent | Purpose | Tools |
|---|---|---|
| `triage` | What is broken, since when, how bad? | `get_error_stats`, `search_logs` |
| `root-cause` | Most likely cause, with evidence | all four `incident-logs` tools, `Read`, `Grep` |

MCP tools appear in the `tools` field by their full name, `mcp__incident-logs__<tool>`.

## How they are used

The `description` field decides when Claude delegates. `triage` is written to be used first for any reported
production problem; `root-cause` is used after it. The main agent follows the `incident-runbook` skill
(`.claude/skills/incident-runbook/`, pointed to by "Incident workflow" in `CLAUDE.md`): delegate to `triage`, delegate
to `root-cause` with the **full triage output in the prompt**, assign the final severity, then write the report.
Fixes are only proposed, never applied.

Try it (after `uv run generate-data --scenario checkout-500` and approving the MCP server):

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

## `triage`

Workflow: `get_error_stats` over the whole dataset, then a finer interval around the window where the rate changes,
then `search_logs` for the first ERROR lines of the affected services. It does not look at causes or deployments.

Output sections: **Affected services** (error rate per service), **Time range** (first error, ongoing?, peak),
**Impact**, **Preliminary severity** (SEV-1 to SEV-4, one sentence), **Suspected scope** (victims vs. origin),
**Handoff notes for root-cause** (timestamps, `error_id`, `request_id`).

The severity is preliminary; the final severity is assigned by the main agent using `severity.md` of the
`incident-runbook` skill.

## `root-cause`

Workflow: read the triage handoff, check `get_deployments` before the first error, pull the first error and at least
three more samples via `get_stacktrace`, compare the `request_context` of failing and healthy requests, compare
against the noise before the incident, and look at the failing code with `Read`/`Grep` if it exists in the
repository (otherwise say so). Never concludes from a single sample.

Output sections: **Root cause** (one sentence), **Confidence**, **Evidence**, **Ruled out**,
**Unknowns and next checks**, **Recommended next step** (proposal only).

## Design decisions

- **Least privilege.** Every agent lists its tools explicitly, because omitting `tools` inherits all of them.
  No agent has `Write`, `Edit`, `Bash` or `NotebookEdit`; analysis is read-only and the human applies fixes.
  `triage` gets the minimum (two tools) so it stays fast and cannot wander into causes.
- **Own context.** Raw log output stays in the subagent; the main conversation only receives the summary.
- **Fixed output format.** The main agent and the next agent can rely on the section names. `tests/test_agents.py`
  checks that they are present.
- **Subagents cannot call each other.** A subagent cannot start other subagents, and its context is isolated from the
  conversation. The main agent is therefore the orchestrator and hands the triage output to `root-cause` explicitly
  in the prompt.
- **Tests.** `tests/test_agents.py` parses the frontmatter by hand (no YAML dependency, CRLF tolerant) and checks
  names, descriptions, an allowlist of tools, the forbidden tools and the required sections.
