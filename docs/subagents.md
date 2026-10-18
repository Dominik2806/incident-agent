# Subagents

Phases 3 and 6 add three project subagents in `.claude/agents/`. Each runs in its own context window with a restricted
tool set and returns only its result to the main agent.

| Agent | Purpose | Tools |
|---|---|---|
| `triage` | What is broken, since when, how bad? | `get_error_stats`, `search_logs` |
| `root-cause` | Most likely cause, with evidence | all four `incident-logs` tools, `Read`, `Grep` |
| `fix-proposer` | Patch (diff), rollback and risks | `Read`, `Grep`, `Glob` |

MCP tools appear in the `tools` field by their full name, `mcp__incident-logs__<tool>`.

## How they are used

The `description` field decides when Claude delegates. `triage` is written to be used first for any reported
production problem; `root-cause` is used after it. The main agent follows the `incident-runbook` skill
(`.claude/skills/incident-runbook/`, pointed to by "Incident workflow" in `CLAUDE.md`): delegate to `triage`, delegate
to `root-cause` with the **full triage output in the prompt**, delegate to `fix-proposer` with the **full
root-cause output**, assign the final severity, then write the report.
Fixes are only proposed, never applied.

Try it (after `uv run generate-data --scenario checkout-500` and approving the MCP server):

> Since about 2 pm customers are getting 500 errors at checkout. What is going on?

## `triage`

Workflow: `get_error_stats` over the whole dataset with a coarse interval (60 minutes), then `service` and
`errors_only` with a finer interval around the hours where the rate changes (stray baseline errors also show up, so the
onset is where `error_rate` jumps, not the first listed window), following `next_offset` or saying when windows are
omitted, then `search_logs` for the first ERROR lines of the affected services. It does not look at causes or
deployments.

Output sections: **Affected services** (error rate per service), **Time range** (first error, ongoing?, peak),
**Impact**, **Preliminary severity** (SEV-1 to SEV-4, one sentence), **Suspected scope** (victims vs. origin),
**Handoff notes for root-cause** (timestamps, `error_id`, `request_id`).

The severity is preliminary; the final severity is assigned by the main agent using `severity.md` of the
`incident-runbook` skill.

## `root-cause`

Workflow: read the triage handoff, check `get_deployments` before the first error, pull the first error and at least
three more samples via `get_stacktrace`, compare the `request_context` of failing and healthy requests, compare
against the noise before the incident, and look at the failing code with `Read`/`Grep` if it exists in the
repository (otherwise say so). Never concludes from a single sample. It measures instead of inferring: per-client
counts for every client version present (reconciled against the total) and, for resource-exhaustion or timeout
errors, request volume per window before blaming load. A failed or truncated tool result is reported, not filled in.

The Root cause sentence names the cause only: an alternative that could not be ruled out is never part of it (not even
negated or hedged). It goes into the reason of the Confidence section and into **Ruled out** as "not excluded", with the
evidence for and against it.

Output sections: **Root cause** (one sentence naming the cause only), **Confidence** (exactly `high`, `medium` or `low`, then a one-sentence
reason), **Evidence**, **Ruled out**,
**Unknowns and next checks**, **Recommended next step** (proposal only).

## `fix-proposer`

Workflow: take file and line from the root-cause stack traces, find the code with `Glob`/`Grep`, `Read` it and its
callers, check that the line matches the trace, then write the smallest patch. If the code is not found it says so
and proposes only the rollback. It cannot run `git diff`, so it copies context lines verbatim and checks hunk
headers by hand. It never claims to have applied or run anything. See [demo-app.md](demo-app.md) for the code it
works on.

Output sections: **Code location**, **Proposed patch** (unified diff), **Rollback alternative**,
**Risk assessment** (what could go wrong, what was not verified), **Not applied**. The report puts them under
"Immediate actions (proposed)" as `### Patch`, `### Rollback` and `### Risks`.

## Design decisions

- **Least privilege.** Every agent lists its tools explicitly, because omitting `tools` inherits all of them.
  No agent has `Write`, `Edit`, `Bash` or `NotebookEdit`; analysis is read-only and the human applies fixes.
  `fix-proposer` has only `Read`, `Grep` and `Glob`, not even the MCP tools: it works from the root-cause output.
  `triage` gets the minimum (two tools) so it stays fast and cannot wander into causes.
- **Own context.** Raw log output stays in the subagent; the main conversation only receives the summary.
- **Fixed output format.** The main agent and the next agent can rely on the section names. `tests/test_agents.py`
  checks that they are present.
- **Subagents cannot call each other.** A subagent cannot start other subagents, and its context is isolated from the
  conversation. The main agent is therefore the orchestrator and hands the triage output to `root-cause` explicitly
  in the prompt.
- **Tests.** `tests/test_agents.py` parses the frontmatter by hand (no YAML dependency, CRLF tolerant) and checks
  names, descriptions, an allowlist of tools, the forbidden tools, the required sections and key wording (confidence
values, measure-don't-infer, triage's coarse first call).
