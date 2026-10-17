# Checkout 500 errors after payment v1.4.2 (KeyError: 'currency')

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (via the triage and root-cause agents); 4 stack-trace samples examined (error_id 6, 8, 149, 418); files read by fix-proposer: `demo_app/payment/charge.py`, `demo_app/payment/server.py`, `tests/test_demo_app.py`

## Summary
Since 14:03 UTC about 18% of checkout requests fail with HTTP 500. The cause is payment v1.4.2 (deployed 14:01 UTC), which reads `payload["currency"]` without a default, so requests from legacy clients (web-2.30.4, android-5.7.9) raise `KeyError: 'currency'`. The failures are still ongoing in the last data window.

## Severity
**SEV-2 (Major).** 433 of 2,402 checkout requests (18.0%) failed from 14:00 to 23:59 UTC, and the last window (23:00-24:00) still shows 18 of 70 failing (25.7%). Checkout is a core flow and is degraded, not down: 18% is in the 10-50% band and below the 50% line of SEV-1. The errors are sustained (about 10 hours) and ongoing, so revenue is affected.

## Impact
- Checkout `POST /checkout` returns HTTP 500 with `payment service error: KeyError: 'currency'`. Payment logs `Unhandled exception in charge: KeyError: 'currency'`.
- Affected services: payment (origin) and checkout (victim). Auth and catalog show no errors in the period.
- 433 failed of 2,402 requests (18.0%); hourly rates 14.7% to 25.7%. Ongoing.
- Failures follow the client version: web-2.30.4 failed 275 of 288 checkout log lines (95.5%) and android-5.7.9 158 of 166 (95.2%) from 14:00 UTC. web-2.31.0 and ios-5.8.1 had 0 failures in 1,948 lines. These are checkout log lines, not user-visible requests.
- Number of affected customers and retries or abandoned checkouts: unknown.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed (not related) | get_deployments |
| 11:15 | auth v3.1.1 deployed (not related) | get_deployments |
| 13:55 | Checkout window: 0 of 35 requests failing | get_error_stats |
| 14:01 | payment v1.4.2 deployed by m.keller, "feat(payment): read currency from charge payload" | get_deployments |
| 14:03:32 | First error: payment `KeyError: 'currency'`, req-e814a92f58 | error_id 6 |
| 14:03:33 | Matching checkout 500 for the same request | search_logs |
| 14:00-14:30 | Checkout window: 17 of 142 failing (12.0%) | get_error_stats |
| 14:30-15:00 | Checkout window: 28 of 164 failing (17.1%) | get_error_stats |
| 17:00-18:00 | Peak hour with meaningful volume: 61 of 290 (21.0%) | get_error_stats |
| 23:00-24:00 | Last window still failing: 18 of 70 (25.7%) | get_error_stats |

## Root cause
Payment v1.4.2 reads `payload["currency"]` with no default in `process_charge` (`charge.py:13`), and legacy clients web-2.30.4 and android-5.7.9 do not send `currency`.
Confidence: high. Reason: the deployment timing, four identical stack traces, an all-or-nothing split by client version with reconciled counts, and the matching source line all agree.

## Evidence
- Payment v1.4.2 went out at 14:01:00Z. It was the only deployment in the 30 minutes before the first error at 14:03:32Z.
- Checkout had 0 of 139 failing (13:00 window) and 0 of 152 (13:30); after the deploy 17 of 142 (14:00) and 28 of 164 (14:30).
- Four sampled stack traces show the same `KeyError: 'currency'` at `demo_app/payment/charge.py` line 13 (`currency = payload["currency"]`), called from `server.py` line 11 in `handle_charge`. All are `POST /checkout` with http_status 500:
  - error_id 6, 14:03:32, req-e814a92f58, web-2.30.4
  - error_id 8, 14:04:27, req-225dde05de, android-5.7.9
  - error_id 149, 17:00:42, req-37dbed440f, web-2.30.4
  - error_id 418, 22:52:43, req-33b8330956, web-2.30.4
- `charge.py` line 11 comment: "Older clients do not send "currency". Since v1.4.2 it is read without a default."
- Checkout log lines from 14:00Z by client (ok / failed): web-2.30.4 13 / 275; android-5.7.9 8 / 158; web-2.31.0 1092 / 0; ios-5.8.1 856 / 0. Totals 1,969 / 433 match the `client=` query totals, and all 433 errors are `KeyError`.
- The payment error for req-e814a92f58 (14:03:32Z) precedes the checkout error (14:03:33Z) by one second, so payment is the origin.
- Request volume rose about 5% (291 to 306 requests) across the onset.

## Ruled out
- **catalog v2.8.0 (09:30Z) and auth v3.1.1 (11:15Z):** deployed hours before the first error; no auth or catalog errors in the incident period.
- **Traffic or load:** requests rose only about 5% (291 to 306) across the onset.
- **Stray baseline errors** (checkout 10:00, payment 13:00, catalog 03:00 and 12:00, auth 08:00): one-off errors before the incident; none has the `KeyError`. They were not read in detail.
- **Request payload as a separate cause:** failures follow client version almost completely (95% legacy, 0% current). Payload contents were not inspected because the logs do not include them.

## Immediate actions (proposed)
Nothing has been applied, edited or run. The human decides.

### Patch
For a fix or hotfix branch only (`CLAUDE.md` forbids fixing the intentional bug on `main`). In `demo_app/payment/charge.py`, read `currency` with a default instead of failing. The `"EUR"` default is an assumption the business has not confirmed.

```diff
--- a/demo_app/payment/charge.py
+++ b/demo_app/payment/charge.py
@@ -10,7 +10,7 @@
     """Authorize a charge described by `payload` and return a receipt."""
     # Older clients do not send "currency". Since v1.4.2 it is read without a default.
     # This is the intentional bug of the demo; it is never fixed on main.
-    currency = payload["currency"]
+    currency = payload.get("currency", "EUR")
     card = payload["card"]
     amount = float(payload["amount"])
     simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
```

Tests that must change together (read, not run): remove the strict `xfail` on `test_checkout_without_currency_must_not_fail_with_500` (line 44); `test_generated_frames_point_to_real_demo_app_lines` would fail on lines 75 and 80; `test_keyerror_frames_match_the_real_traceback` (line 83) expects the `KeyError`; `src/incident_agent/data/frames.py` would also have to change (not read). A 4xx rejection is an alternative to a default; it was not proposed because checkout's error mapping was not read.

### Rollback
Roll back payment from v1.4.2 (deployed 2026-03-12T14:01:00Z by m.keller) to the previous version. The previous version is unknown; take it from the deployment history. This stops new failures from the first error at 14:03:32Z onward. It does not fix the code on `main`, does not recover the roughly 433 already failed checkouts, and removes anything else shipped in v1.4.2.

### Risks
- **Wrong currency:** a default of `"EUR"` could charge legacy-client customers in the wrong currency (money risk). The business must confirm the default, or choose a 4xx rejection, before shipping.
- **Test and frames coupling:** the tests and `frames.py` must change in the same commit, or CI fails.
- **Rollback:** the target version is unknown, and it may lack other v1.4.2 changes. It is unverified whether the previous version handled a missing `currency`.
- **Not verified:** nothing was run and no tests were run; other callers of `process_charge` and `demo_app/checkout/server.py` were not checked; the v1.4.2 diff beyond the commit message was not checked.

## Follow-up tasks
- [ ] Add a regression test with a legacy payload that lacks `currency`.
- [ ] Make payload-contract changes backward compatible, or require clients to be upgraded first.
- [ ] Decide with the business whether to default the currency or reject with a clear 4xx instead of a 500.

## Open questions
- Why did 21 legacy-client lines succeed (web-2.30.4: 13, android-5.7.9: 8)? Possible causes are a different payment code path, or some legacy requests that did send `currency`. Check the payload of one, such as req-4ea6872c8b (web-2.30.4, 14:18:23Z).
- Payloads are not logged, so "legacy clients omit `currency`" rests on the code comment and the failure split. A payment-side payload sample would confirm it.
- Pre-14:00 failures per client version were not counted (only 0 failed in the 13:00 and 13:30 windows).
- The 14:00-14:30 and post-14:30 windows were not read at 5-minute resolution, so the peak is not confirmed at finer resolution.
- Business impact (retries, abandoned checkouts, affected customers) is unknown.
- Which version precedes payment v1.4.2 is unknown.
