# Payment v1.4.2 KeyError 'currency' causes checkout 500s

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (incident-logs MCP). Stack-trace samples examined: 3 for the incident (error_id 6 and 8 confirmed by trace; a third as reported by root-cause) plus 1 baseline (error_id 5). Error_ids 210, 211, 428, 429 were checked by log line only. Files read by fix-proposer: `demo_app/payment/charge.py`, `tests/test_demo_app.py`. Fix-proposer could not use the incident-logs tools.

## Summary
Since 14:03:32 UTC on 2026-03-12, about 18% of checkouts fail with HTTP 500 (433 of 2,402 requests) and the failures are still ongoing in the last window. The likely cause is payment v1.4.2 (deployed 14:01:00), which reads `payload["currency"]` without a default, so older clients that do not send a currency fail.

## Severity
**SEV-2 (Major).** The checkout/payment core flow is degraded with 433 of 2,402 requests failed (18.0%, within the 10-50% band) from 14:00 UTC. Hourly rates ran from 14.7% to 25.7%. The incident lasted about 10 hours in the data, and the last window (23:00) still fails at 18/70 (25.7%). It is ongoing and affects revenue. It is not SEV-1 because the rate is below 50% and most checkouts succeed.

## Impact
- `POST /checkout` returns 500 ("payment service error: KeyError: 'currency'"). Payment `charge` is the origin, and checkout relays the error. Every checkout failure matches a payment failure on the same `request_id`.
- Failed/total: 433 of 2,402 (18.0%) for both payment and checkout.
- Affected clients (checkout log lines from 14:00): web-2.30.4 13 ok / 275 failed (about 95.5% failing), android-5.7.9 8 ok / 158 failed (about 95.2%). web-2.31.0 (1092 ok) and ios-5.8.1 (856 ok) have 0 failures.
- Auth and catalog are not affected.
- Ongoing. It is unknown whether affected customers succeed on retry.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 13:12:03 | Unrelated single gateway timeout in payment | error_id 5 |
| 14:01:00 | payment v1.4.2 deployed by m.keller ("feat(payment): read currency from charge payload") | get_deployments |
| 14:02:57 | An android-5.7.9 request still returns 200 | search_logs |
| 14:03:32 | First KeyError in payment | error_id 6, req-e814a92f58 |
| 14:03:33 | Checkout returns 500 for the same request | search_logs |
| 17:00 | Highest absolute failure count: 61 of 290 (21.0%) | get_error_stats |
| 23:00 | Last window still failing: 18 of 70 (25.7%) | get_error_stats |

## Root cause
Payment v1.4.2 reads `payload["currency"]` with no default, so charges from older clients that do not send `currency` fail with `KeyError: 'currency'`. Confidence: **high**. The deploy time, code comment, identical stack traces, client-version split and test marker all agree, and only the original request payload is missing from the logs.

## Evidence
- Payment v1.4.2 deployed at 14:01:00; first error 2.5 minutes later at 14:03:32 (error_id 6).
- All 433 payment ERROR lines since 14:00 match the query `KeyError` (total_matches 433, equal to the unfiltered payment ERROR count). Checkout also has 433 matching lines.
- error_id 6 (14:03:32, req-e814a92f58, web-2.30.4) and error_id 8 (14:04:27, req-225dde05de, android-5.7.9) have the same stack trace: `demo_app/payment/charge.py` line 13, `currency = payload["currency"]`, `KeyError: 'currency'`.
- Later ERROR lines carry the same message: error_ids 210 and 211 (18:01:32, 18:02:20) and 428 and 429 (23:31:30, 23:34:42). Stack traces were not opened for these.
- Code comment at `charge.py` line 11: "Older clients do not send currency. Since v1.4.2 it is read without a default."
- `tests/test_demo_app.py` defines a `LEGACY` payload without currency and a strict-xfail test `test_checkout_without_currency_must_not_fail_with_500`.
- Per-client counts reconcile: 13+8+1092+856 = 1969 INFO and 275+158 = 433 ERROR.
- Other first errors: error_id 7 (14:04:18, req-466b9882ae, web-2.30.4), 9 (14:05:18, req-d60f35d114, android-5.7.9), 10 (14:05:25, req-09f30bf192, web-2.30.4), 11 (14:06:32, req-40bd4ee51d, android-5.7.9).

## Ruled out
- **Catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** deployed hours before onset, and neither service logged errors during the incident.
- **Gateway timeout (error_id 5, 13:12):** a different exception at a different endpoint (`gateway.py`, `POST /charge`, 504), a single event before the deployment.
- **Isolated baseline errors (catalog 03:00 and 12:00, auth 08:00):** one-off events in other services.
- **Bad checkout deploy:** there was no checkout deployment; checkout only relays the payment error.
- **Load or traffic surge:** not measured, so neither supported nor excluded. The per-client split already explains the failures.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides whether to use the patch or the rollback.

### Patch
File `demo_app/payment/charge.py`, line 13: use a default currency when the client sends none. "EUR" is only an assumption (the tests' `VALID` payload uses it) and must be confirmed as a business decision. If no default is acceptable, return a 4xx validation error instead.

```diff
--- a/demo_app/payment/charge.py
+++ b/demo_app/payment/charge.py
@@ -10,7 +10,7 @@
     """Authorize a charge described by `payload` and return a receipt."""
     # Older clients do not send "currency". Since v1.4.2 it is read without a default.
     # This is the intentional bug of the demo; it is never fixed on main.
-    currency = payload["currency"]
+    currency = payload.get("currency", "EUR")  # assumed default, to be confirmed
     card = payload["card"]
     amount = float(payload["amount"])
     simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
```

The xfail marker on the test is `strict=True`, so once the fix is in the test will be reported as a failure (XPASS strict) unless the marker is removed in `tests/test_demo_app.py` lines 25-33. That change is not in the diff. The comments on lines 11-12 would also become stale.

### Rollback
Roll back payment from v1.4.2 (deployed 2026-03-12 14:01:00 UTC by m.keller) to the previous payment version. The previous version number is unknown; look it up in `get_deployments` before rolling back. Catalog v2.8.0 and auth v3.1.1 are unrelated and stay as they are. It should stop the KeyError for web-2.30.4 and android-5.7.9, but it also removes the "read currency from charge payload" feature, and what depends on that is unknown.

### Risks
- **Patch:** old clients would be charged in the assumed default currency. A wrong default could mean a wrong-currency charge, which is worse than a failed request; a 4xx is safer if the currency cannot be inferred. Other callers of `process_charge` and the gateway code were not checked, and neither was the pre-v1.4.2 behavior for a missing currency.
- **Rollback:** side effects and target version unknown; it needs a deploy.
- **Not verified:** nothing was run or applied and the tests were not run. Raw payloads were not inspected. The roughly 4.5% of old-client requests that succeeded are unexplained and may mean some old-client requests do send currency.

## Follow-up tasks
- [ ] Remove the strict-xfail marker on `test_checkout_without_currency_must_not_fail_with_500` in the same change as the fix.
- [ ] Update the stale comments on `charge.py` lines 11-12 after the fix.
- [ ] Decide the business rule for a missing currency (default value, or 4xx validation error).

## Open questions
- The raw request payload was not inspected. The claim that old clients omit `currency` rests on the code comment and the failure split by client version, not on a logged payload.
- About 4.5% of web-2.30.4 and android-5.7.9 lines succeeded (13 and 8), including an android-5.7.9 request at 14:02:57 after the deploy. This could be requests that did include currency or a rollout lag; not investigated.
- Only the first two ERROR lines of each late window were checked, not all 433 individually (the 433 count match was confirmed by query).
- Next check: look at the payload of one successful old-client request, and compare the pre-14:00 pattern for old clients.
- Previous payment version number: unknown.
- Which default currency is acceptable, if any: unknown.
- Whether affected customers succeed on retry: unknown.
