---
name: incident-runbook
description: Handle a production incident end to end - triage, root cause, final severity and a written incident report. Use when a user reports a production error, outage or elevated error rate, or asks to investigate an incident or write an incident report. Example phrases - "customers get 500 errors at checkout", "payments are failing since 2 pm", "error rate is spiking", "is the catalog service down?", "something is broken in production", "investigate this incident", "write an incident report for the checkout outage".
---

# Incident runbook

Act as the on-call engineer. You orchestrate; the subagents analyze. You only propose fixes, you never apply them.
Subagents cannot call each other and cannot see this conversation, so pass them everything they need.

## Procedure

1. **Triage.** Delegate to the `triage` subagent with the user's problem description. Keep its full output.
2. **Root cause.** Delegate to the `root-cause` subagent. Put the **full triage output** (all sections, including the
   handoff notes) into its prompt. Keep its full output.
3. **Severity.** Read [severity.md](severity.md) now. Assign the final severity from the matrix. Treat the triage
   "Preliminary severity" as a hint only. Justify the level with concrete numbers from the triage output (error
   rate, failed/total requests, duration, ongoing or resolved, affected flow).
4. **Report.** Read [template.md](template.md) now. Write the report to `reports/YYYY-MM-DD-short-title.md`:
   - The date is the **incident date in UTC taken from the data** (date of the first error), not today's date.
   - `short-title` is 2-5 lowercase words joined by hyphens, e.g. `checkout-500-payment-keyerror`.
   - Create `reports/` if it is missing. The folder is gitignored; never commit reports.
   - Keep the template headings exactly as they are, in the same order. Do not add, rename or remove H2 headings.
   - Fill the sections from the agents' results: Summary, Severity and Impact from triage plus step 3; Timeline,
     Root cause, Evidence and Ruled out from root-cause; Open questions from "Unknowns and next checks";
     Immediate actions from "Recommended next step"; Follow-up tasks only from proposals the agents made.
   - In the header, list the tools used and how many stack-trace samples were examined, as reported by the agents.
5. **Reply.** Answer the user briefly: final severity, root cause, confidence, proposed next steps and the path of
   the report file. No long recap; the report holds the details.

## Rules

- Only propose fixes (rollback, patch, config change). Never apply, edit or run them, and say that nothing was applied.
- Every statement in the report needs evidence from the agents' results (timestamps, `error_id`, `request_id`,
  numbers). If there is none, leave the statement out.
- Carry unknowns and the confidence level over into the report unchanged. Do not raise confidence or drop caveats.
- Never invent data: no made-up timestamps, counts, versions or customer numbers. Write "unknown" instead.
- If a subagent fails or returns incomplete data, say so in the report (Open questions) and continue with what exists.
- If no incident is visible in the data, report that finding instead of constructing one.

## Files

- [severity.md](severity.md) - severity matrix SEV-1 to SEV-4, tie-break rules and a worked example.
- [template.md](template.md) - incident report template with fixed headings.
