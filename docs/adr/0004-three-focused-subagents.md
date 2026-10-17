# 0004: Three focused subagents; fix-proposer has only Read, Grep and Glob

- **Status:** Accepted (PR #4 for `triage` and `root-cause`, PR #10 for `fix-proposer`)

## Context

An investigation produces a lot of raw output (log lines, stack traces). The main conversation should only get
summaries. A subagent runs in its own context, cannot see the conversation and cannot start other subagents.

## Decision

- Three agents with one job each: `triage` (what, since when, how bad; `get_error_stats` and `search_logs` only),
  `root-cause` (all four MCP tools plus `Read` and `Grep`) and `fix-proposer`.
- `fix-proposer` has exactly `Read`, `Grep` and `Glob`: it works from the root-cause output and the code, has no MCP
  tools and no way to write or execute. Tests check this.
- Every agent lists its tools explicitly, because omitting `tools` inherits all of them. No agent gets `Write`, `Edit`,
  `Bash` or `NotebookEdit`.
- Each has a fixed output format (checked by `tests/test_agents.py`) so the next step can rely on the sections. The
  main agent orchestrates and pastes the full output of one agent into the next prompt.

## Consequences

- The agent can only propose; applying a fix is a human step, as intended.
- `fix-proposer` cannot run `git diff` or the tests, so patches are checked by reading only. This is stated in its
  risk assessment.
- The main agent carries the hand-off; a lost section would break the chain, hence the fixed formats.
- Triage's severity is only preliminary; the final one comes from the skill.
