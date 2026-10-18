# Severity matrix

Assign exactly one level. Core flows of this shop are **checkout** and **payment**; `catalog` and `auth` are
non-core unless they block checkout or payment. Thresholds are defaults; tune them here.

| Level | Name | Criteria | Expected response |
|---|---|---|---|
| SEV-1 | Critical | A core flow is unavailable, or more than 50% of its requests fail, or there is data loss, a security issue or payment integrity risk | Immediate. Page on-call, all hands |
| SEV-2 | Major | A core flow is degraded, 10-50% of its requests fail, errors are sustained or ongoing and affect revenue | Within 15 minutes. On-call engineer plus service owner |
| SEV-3 | Minor | A non-core service is degraded, or a core flow has under 10% failures or only a short-lived problem, and a workaround exists | Same business day |
| SEV-4 | Low | Cosmetic issues, isolated errors, background noise | Backlog |

## Rules

- If criteria of several levels match, take the **higher** severity (SEV-1 is highest).
- An **ongoing** incident ranks higher than a resolved one. A resolved incident may be rated one level lower only if
  the last triage window shows no errors and the cause is known to be gone; say so in the justification.
- The justification must cite **numbers from the triage output**: error rate, failed / total requests, start time,
  duration, whether the last window still fails, and which flow is affected.
- Use the triage numbers as they are. If a number is missing, write "unknown" and rate on the evidence that exists.
- Triage's "Preliminary severity" is a hint, not the result.

## Worked example: nightly export

Triage reports (illustrative numbers): the nightly `ledger-export` job, an internal job with no customer traffic, failed
in 1 of the last 30 runs (3%). The failed run started at 01:00 UTC with `OSError: [Errno 28] No space left on device`
while writing its output file, and the last window shows no errors.

- No core flow is involved and no customer is affected, so the SEV-1 and SEV-2 criteria do not match.
- It is not a degraded non-core service (SEV-3) either: one run in 30 failed, there is no sustained failure, and the
  only consumer, the finance team, re-ran the export by hand the next morning.
- What remains is an isolated error in a background job, which is low.
- Result: **SEV-4 (Low)**. Justification to write in the report: "1 of 30 nightly runs of the internal ledger-export job
  (3%) failed at 01:00 UTC because the output disk was full; no customer-facing flow was affected and the export was
  re-run by hand."
