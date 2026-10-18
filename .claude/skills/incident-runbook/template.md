# <Incident title>

- **Incident date (UTC):** YYYY-MM-DD
- **Status:** Proposed actions only, nothing applied
- **Sources:** <tools used, e.g. get_error_stats, search_logs, get_deployments, get_stacktrace; number of stack-trace samples examined; files read by fix-proposer>

## Summary
<!-- Two to three sentences: what broke, since when, how bad, the likely cause. -->

## Severity
<!-- SEV-1 to SEV-4 from severity.md, with a justification citing numbers from triage. -->

## Impact
<!-- What fails for users or revenue, affected services and endpoints, error rate and failed/total requests, ongoing or resolved. -->

## Timeline (UTC)
<!-- One row per event with a timestamp from the data: deployment, first error, peak, detection. No guessed times. -->

| Time (UTC) | Event | Source |
|---|---|---|
| HH:MM | <event> | <tool, error_id or deployment> |

## Root cause
<!-- One sentence naming the cause only. An alternative that was considered but not excluded does not belong here; it goes under Ruled out and Open questions. Then the confidence (high, medium or low) with the agent's reason, unchanged. -->

## Evidence
<!-- Bullets with timestamps, error_id and request_id values, sampled stack traces and the request-context comparison. -->

## Ruled out
<!-- Each alternative that was considered and why it is not the cause. -->

## Immediate actions (proposed)
<!-- Proposals only. State that nothing has been applied. Use these H3 subsections, taken from fix-proposer unchanged: -->

### Patch
<!-- The unified diff in a ```diff block, with the file path and a one-sentence explanation. If no patch is possible, say why. -->

### Rollback
<!-- The rollback alternative: service, versions and times from the deployment data, and what it does not fix. -->

### Risks
<!-- What could go wrong with the patch and the rollback, and what was not verified (not run, not applied). -->

## Follow-up tasks
<!-- Preventive work such as tests, alerts or reviews, as a checklist. Only items the agents proposed. -->

## Open questions
<!-- Unknowns and checks that would raise confidence, carried over from the agents unchanged. -->
