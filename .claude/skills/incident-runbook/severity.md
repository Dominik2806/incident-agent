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

## Worked example: checkout-500

Triage reports (illustrative numbers): about 19% of `checkout` requests fail with HTTP 500 from 14:03 UTC, the last
window still fails, `payment` logs `KeyError: 'currency'`.

- Core flow (checkout) is degraded, not unavailable: 19% is below the 50% line of SEV-1.
- 19% falls into the 10-50% band, and the errors are sustained and ongoing, so revenue is affected.
- Result: **SEV-2 (Major)**. Justification to write in the report: "About 19% of checkout requests have failed since
  14:03 UTC and the last window is still failing; checkout is a core flow and is degraded, not down."
