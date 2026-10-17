# 0007: The stop hook finds the report through the audit log and allows one correction round

- **Status:** Accepted (PR #12)

## Context

The report should be complete before Claude stops, but the Stop event fires in every conversation and must never
cause an endless loop. The hook receives only a session id and does not know which file is the report.

## Decision

- `check_report.py` looks in the audit log (`logs/audit.jsonl`) for the latest `Write`, `Edit` or `MultiEdit` of this
  session on `reports/*.md`. No such record means no analysis in this session, so Claude may stop (exit 0).
- The required `##` headings are parsed at runtime from `template.md`; a section that is blank or equals the template
  placeholder counts as empty. Missing or empty sections give exit 2 with a list (rule `report-incomplete`).
- If `stop_hook_active` is true, the hook exits 0 at once: **one correction round**, then Claude may stop.
- Any error exits 0 (fail open).

## Consequences

- No extra state: the audit log already exists, and detection is limited to the session that wrote the report.
- The hook depends on `audit_log.py` running. Reports written through the shell (`>`, `tee`) are not detected.
- It checks structure only, not the quality of the content, and may let an incomplete report through after one round.
- The template is the only list of sections, so tests, skill and hook stay in sync.
