# Checkout failures from Payvia card processor outage

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace; 5 stack-trace samples examined (error_id 6, 7, 70, 131, 161); fix-proposer read `demo_app/payment/server.py`, `src/incident_agent/data/scenarios.py` and `docs/scenarios.md` (the failing code was not found in the repository)

## Summary
From 16:05:12Z to 17:19:21Z, about 43% of checkouts (156 of 365) failed with HTTP 502 or 504. The likely cause is an outage of the external card processor Payvia, which returned 503 or timed out until payment's retries ran out. Errors stopped at 17:19:21Z with no rollback or redeploy.

## Severity
**SEV-2 (Major).** 156 of 365 checkout requests (42.7%) failed between 16:05 and 17:20 UTC, about 75 minutes. Checkout and payment are core flows, and they were degraded, not down (below the 50% line of SEV-1). The 10-50% band applies and revenue was affected. The incident is resolved, but it is not rated one level lower: the last triage window (17:15-17:20) still had errors (11 of 24 failed), and the cause is not confirmed gone, because there is no Payvia-side data.

## Impact
- Customers got errors on `POST /checkout` when paying (502 `Payvia upstream 503 Service Unavailable`, 504 `Payvia upstream timeout`). About 4 in 10 checkouts failed and the rest succeeded.
- Affected: checkout (156/365, 42.7%) and payment (156/365, 42.7%, identical per-window counts). Auth and catalog had no errors in the window.
- All client versions were hit at similar rates: ios-5.8.1 62/146, android-5.7.9 15/28, web-2.31.0 61/148, web-2.30.4 18/43 (checkout log lines, 16:05 to 17:20).
- Status: resolved. No error windows from 17:20 onward.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 15:55 | checkout v2.7.1 deployed ("reuse HTTP connections to payment") | get_deployments |
| 16:00-16:05 | 24 checkout requests, 0 errors | get_error_stats |
| 16:05:12 | First error: Payvia 503 (ProviderUnavailableError), req-1024236328 | error_id 6 |
| 16:05:19 | Payvia timeout (ProviderTimeoutError), req-4314b8bd95 | error_id 7 |
| 16:05 | Peak rate: 12 of 18 requests (66.7%) | get_error_stats |
| 16:25 | Most failures in one 5-minute window: 18 of 32 (56.3%) | get_error_stats |
| 17:15-17:20 | Last failing window: 11 of 24 failed | get_error_stats |
| 17:19:21 | Last error, req-0680284f5f | error_id 161 |
| 17:20 | No error windows from here on | get_error_stats |

## Root cause
The external card processor Payvia (`POST https://api.payvia.example/v1/charges`) was unavailable from 16:05:12Z to 17:19:21Z. It returned HTTP 503 or timed out, and payment's 3 attempts ran out, so about 43% of checkouts failed with 502 or 504.

Confidence: **medium**. The evidence consistently points to an upstream provider outage, but there is no Payvia-side data and the code could not be read, so the checkout v2.7.1 deployment cannot be fully excluded.

## Evidence
- First error 2026-03-12T16:05:12Z (error_id 6, req-1024236328, ios-5.8.1). The 16:00-16:05 window had 24 checkout requests and 0 errors.
- Last error 17:19:21Z (error_id 161, req-0680284f5f, ios-5.8.1). A search for checkout ERROR lines from 17:19:22Z onward returned 0.
- 5 stack-trace samples (error_id 6, 7, 70, 131, 161), all from payment, with identical frames: `/app/payment/server.py:29 handle_charge`, then `provider_client.py:64 charge`, then `provider_client.py:97 _post_with_retries`, which does `raise last_error`. Exceptions were ProviderUnavailableError and ProviderTimeoutError. This is the provider's response, not a payment-side defect such as a `KeyError`.
- Payment messages: `Charge failed: ProviderUnavailableError: card processor Payvia returned HTTP 503 Service Unavailable after 3 attempts` and `... ProviderTimeoutError: card processor Payvia timed out after 3 attempts (retries=2, 5.0s each)`.
- Request-context comparison (checkout, 16:05 to 17:20): ios-5.8.1 42.5%, android-5.7.9 53.6% (small sample), web-2.31.0 41.2%, web-2.30.4 41.9%. Sums: 209 OK and 156 failed. Not client-specific.
- Load was flat: 66, 82, 80, 68 and 69 requests per 15 minutes during the incident, against 67 and 78 before it.
- Recovery without any change: the rate fell from 36.2% (17:00 window) to 15.1% (17:15 window), then 0 errors from 17:30. No deployment happened after 15:55.

## Ruled out
- **checkout v2.7.1 (15:55):** a weaker fit. There were no errors for 10 minutes after it, the errors are Payvia 503s and timeouts raised in payment's provider client, and the failures ended at 17:20 with no rollback. Not completely excluded, since a connection problem could in principle surface as provider timeouts.
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** hours earlier, and neither service logged errors in the window.
- **Client-specific bug:** all four client versions fail at 41% to 54%.
- **Traffic surge:** volume was flat.
- **Recurring noise:** the stray ERROR lines (checkout 10:00, payment 13:00, catalog 03:00 and 12:00, auth 08:00) and the catalog and auth WARNs are unrelated.

## Immediate actions (proposed)
Proposals only. **Nothing has been applied.** The incident is already resolved, so no urgent action is pending.

### Patch
No patch is possible. The code in the stack trace (`/app/payment/provider_client.py`, `/app/payment/server.py`) is not in this repository. The only payment code is `demo_app/payment/server.py`, which has no provider call and no retries. The frames appear only as synthetic text in `src/incident_agent/data/scenarios.py` lines 345-347. A patch to retries, timeouts or a circuit breaker would be a guess.

### Rollback
The only change before the incident was checkout v2.7.1, deployed 2026-03-12 at 15:55 UTC. The previous checkout version is not in the data, so it is unknown. Rollback is not recommended on this evidence: v2.7.1 is a weak fit, and the incident has already resolved. It would not fix a Payvia outage.

### Risks
- No patch means no code risk, but also no code-level remedy yet.
- A rollback of a resolved, provider-side incident adds deployment risk, loses the connection-reuse change, and would probably not change the outcome.
- Not verified: the v2.7.1 diff and connection metrics, any Payvia-side data, the real payment code, and 151 of the 156 traces. Nothing was run or tested.

## Follow-up tasks
- [ ] Add a circuit breaker or fast-fail for Payvia, or a fallback processor.
- [ ] Alert on the payment provider 5xx and timeout rate (it would have caught this at 16:05).
- [ ] Review the retry and timeout settings (3 attempts of 5.0s each).
- [ ] Review the checkout v2.7.1 diff and the checkout-to-payment connection metrics.

## Open questions
- No Payvia-side data. Check the Payvia status page or incident report for 16:05 to 17:20 UTC, and whether they changed anything on their side.
- Which repository or deployment holds `/app/payment/provider_client.py`? The code was not found, so lines 64 and 97 were not inspected.
- v2.7.1 diff and connection metrics are unchecked. This is the check that would raise confidence to high.
- Did the 3 attempts with 5s timeouts make the impact worse? Unchecked.
- Sampling: 5 stack traces were read. The other 151 of 156 were not.
- The previous checkout version (rollback target) is unknown.
