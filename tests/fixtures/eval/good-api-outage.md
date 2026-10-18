# Checkout failures: outage of the external card processor Payvia (resolved)

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (4 stack-trace samples examined: error_id 6, 7, 100, 161). fix-proposer read no source: the provider client is not in this repository.

## Summary
Between 16:05:12 and 17:19:21 UTC the external card processor Payvia, which payment calls, failed charge attempts with HTTP 503 or timeouts after 3 attempts, and checkout answered 502 or 504 for 156 of 365 requests (42.7%). The outage has recovered on its own: no checkout error since 17:19:21, and no deployment or rollback was involved.

## Severity
**SEV-2 (Major).** Checkout is a core flow and was degraded, not unavailable: 156 of 365 checkout requests (42.7%) failed between 16:05 and 17:20 UTC, which is inside the 10-50% band and below the 50% line for SEV-1. The errors were sustained for 75 minutes and affected revenue. The incident is resolved (the last window, 23:00 to 24:00, shows 0 errors in 70 requests), but I did not rate it one level lower: the cause is an outage at a third party, and there is no confirmation from the provider that it is fixed, only that its answers returned at 17:20. A flow with about 4 in 10 failures for 75 minutes is not the short-lived minor case of the lower level.

## Impact
- `POST /checkout` failed for 156 of 365 requests in the window: 105 with HTTP 502 (provider answered 503 Service Unavailable) and 51 with HTTP 504 (provider timeout). The other 209 requests succeeded.
- All four client versions were affected alike: web-2.31.0 61 of 148 failed, ios-5.8.1 62 of 146, web-2.30.4 18 of 43, android-5.7.9 15 of 28. No client is the cause.
- Payment logged one error per failed charge (service `payment`, endpoint `POST /charge`); catalog and auth are not affected.
- Resolved: 0 checkout errors from 17:20 to the end of the data (23:59), 300 requests between 17:20 and 18:20 without a failure.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed | get_deployments |
| 11:15 | auth v3.1.1 deployed | get_deployments |
| 15:55 | checkout v2.7.1 deployed, "reuse HTTP connections to payment" | get_deployments |
| 16:04:50 | Last successful checkout before the incident | search_logs |
| 16:05:00 | First failed provider health check ("card processor Payvia health check failed: HTTP 503") | search_logs |
| 16:05:12 | First failure, req-1024236328, ios-5.8.1, HTTP 502 (provider 503) | error_id 6 |
| 16:05:19 | First timeout, req-4314b8bd95, android-5.7.9, HTTP 504 | error_id 7 |
| 17:19:21 | Last failure, req-0680284f5f, ios-5.8.1 | error_id 161 |
| 17:20:42 | "card processor Payvia health check ok" | search_logs |

## Root cause
The external card processor Payvia, called by payment at `https://api.payvia.example/v1/charges`, had an outage from 16:05 to 17:20 UTC: it answered HTTP 503 Service Unavailable or timed out on about 4 in 10 charge attempts even after 3 attempts, so checkout returned 502 or 504. The provider recovered on its own.

**Confidence: high.** The error messages name the provider, its upstream status and the retry count, the first failure matches the first failed health check at 16:05, and the failures stop at 17:20 together with the first successful health check, without any deployment.

## Evidence
- error_id 6 (16:05:12, req-1024236328) and error_id 161 (17:19:21, req-0680284f5f): `ProviderUnavailableError: card processor Payvia returned HTTP 503 Service Unavailable after 3 attempts (retries=2) POST https://api.payvia.example/v1/charges`. error_id 7 (16:05:19, req-4314b8bd95) and error_id 100 (16:46:50, req-c473552401): `ProviderTimeoutError: card processor Payvia timed out after 3 attempts (retries=2, 5.0s each)`. All four traces end in `/app/payment/provider_client.py _post_with_retries`.
- Checkout lines say it too: `POST /checkout 502 payment failed: Payvia upstream 503 Service Unavailable (retries=2)` and `POST /checkout 504 payment failed: Payvia upstream timeout (retries=2)`.
- Bounded window: 0 checkout errors in 15:00-16:05 (302 requests) and 0 in 17:20-18:20 (300 requests); inside the window 71 of 156 failed in 16:05-16:35, 59 of 140 in 16:35-17:05 and 26 of 69 in 17:05-17:20. The rate is the same across the window.
- Payment WARN lines "card processor Payvia health check failed: HTTP 503" appear every minute from 16:05:00 to 17:19:52, and "card processor Payvia health check ok" at 17:20:42.
- Per client (failed / checkout requests in 16:05-17:20): web-2.31.0 61/148, ios-5.8.1 62/146, web-2.30.4 18/43, android-5.7.9 15/28.
- The total of failed requests is 156 provider errors between 16:05:12 and 17:19:21.

## Ruled out
- **checkout v2.7.1 (15:55):** deployed 10 minutes before the first error and a plausible suspect, because it changes how checkout reuses connections to payment. Checkout stayed healthy for those 10 minutes (50 requests, 0 failures), the errors name Payvia and its 503 or timeout, the failure rate did not change at the deployment, and the failures stopped at 17:20 with no rollback or other deployment. Timing coincidence.
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** hours earlier, and checkout was healthy until 16:04:50.
- **A bad client version:** all four client versions fail at similar rates.
- **A payment application bug:** payment reports the provider's 503 and timeouts, and the one-off `card gateway timeout after 5.0s` at 13:12 is older, single and does not name the provider.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides.

### Patch
None in code. The incident has recovered and the provider is outside this repository. Optionally review the retry and timeout policy of the provider client (3 attempts, 5.0s each) and add a circuit breaker or a fallback processor.

### Rollback
Not recommended and not applicable. No deployment correlates with the onset or with the recovery, and rolling back checkout v2.7.1 would not have changed the outage.

### Risks
- A shorter timeout or a fallback processor changes payment behaviour (duplicate charges, different fees) and needs review.
- Not verified: the provider's own incident report, whether the outage is fully over, and whether the retries caused duplicate authorizations. Nothing was run or tested.

## Follow-up tasks
- [ ] Ask Payvia for its incident report and the exact outage window.
- [ ] Alert on the provider health check and on the rate of provider 503 and timeout errors.
- [ ] Check customers whose charge failed 16:05-17:20 and verify there are no duplicate charges.
- [ ] Review the retry and timeout policy and consider a circuit breaker or fallback processor.

## Open questions
- Was the outage on the provider side only, and will it return?
- Did any retry succeed at the provider after checkout gave up (duplicate charges)?
- Why did about 6 in 10 charge attempts still succeed during the outage?
