# 0006: The skill is the single source of truth for workflow and report template

- **Status:** Accepted (PR #5)

## Context

"How we handle incidents" involves a procedure, a severity scale and a report format. If these were repeated in
`CLAUDE.md`, the agent files and the docs, they would drift apart.

## Decision

- The skill `incident-runbook` holds `SKILL.md` (procedure: triage, root cause, fix proposal, severity, report),
  `severity.md` (SEV-1 to SEV-4) and `template.md` (report template).
- `CLAUDE.md` only points to the skill. Claude loads the skill when a problem is reported, and reads `severity.md` and
  `template.md` at the step that needs them.
- The H2 headings of `template.md` are machine-checked by `tests/test_skill.py`, which also checks the example
  reports. The stop hook parses the same file at runtime instead of keeping a copy
  ([0007](0007-stop-hook-via-audit-log.md)).
- The final severity comes from `severity.md`; the triage severity is preliminary. Reports go to
  `reports/YYYY-MM-DD-short-title.md` (incident date in UTC, gitignored).

## Consequences

- Changing the process or the report format means editing one place; tests and the stop hook follow.
- Renaming a template heading requires changing the tests and the example reports together.
- The skill is guidance to the model, not enforcement; the stop hook is what enforces completeness.
