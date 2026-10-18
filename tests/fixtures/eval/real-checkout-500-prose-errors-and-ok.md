# Checkout 500s from payment v1.4.2 KeyError 'currency'

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (triage, root-cause and orchestrator); 4 stack-trace samples examined (error_ids 6, 8, 268, 428); files read by fix-proposer: `demo_app/payment/charge.py`, `demo_app/payment/server.py`

## Summary
Since 14:03:32Z on 2026-03-12, 433 of 2,402 checkout requests (18.0%) failed with HTTP 500 because the payment service raises an unhandled `KeyError: 'currency'`. The failures started 2.5 minutes after payment v1.4.2 was deployed (14:01:00Z) and were still ongoing at the end of the data (23:59Z). Likely cause: v1.4.2 reads `payload["currency"]` with no default, and older clients (web-2.30.4, android-5.7.9) do not send that field.

## Severity
**SEV-2 (Major).** Checkout and payment are core flows. 433 of 2,402 requests failed (18.0%, within the 10-50% band), starting 14:03:32Z, for about 10 hours so far. The last window (23:00-24:00) still fails at 18 of 70 (25.7%), so the incident is ongoing, and it affects revenue. It is not SEV-1: the flow is not unavailable and the failure rate is below 50%. No data loss or security issue is evidenced.

## Impact
- Users on web-2.30.4 and android-5.7.9 get HTTP 500 on `POST /checkout`. Checkout log: `POST /checkout 500 payment service error: KeyError: 'currency'`.
- Affected services: payment (origin) and checkout (relays the error). Both show 433 of 2,402 failed requests (18.0%). Hourly rates ranged from 14.7% to 25.7%.
- Checkout log lines per client, 14:00-24:00: web-2.30.4 had 275 errors and 13 ok (95% failed), android-5.7.9 had 158 errors and 8 ok (95% failed), web-2.31.0 had 0 errors in 1092, ios-5.8.1 had 0 errors in 856.
- auth and catalog are not affected.
- Ongoing at the end of the data.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed (not related, see Ruled out) | get_deployments |
| 11:15 | auth v3.1.1 deployed (not related, see Ruled out) | get_deployments |
| 13:00 | Stray payment error (baseline, 0.3%) | get_error_stats |
| 14:01 | payment v1.4.2 deployed by m.keller, "feat(payment): read currency from charge payload" | get_deployments |
| 14:03:32 | First `KeyError: 'currency'` in payment (req-e814a92f58, web-2.30.4); checkout 500 at 14:03:33 | error_id 6 |
| 14:00-15:00 | 45 payment errors, 14.7% | get_error_stats |
| 17:00 | Peak by count: 61 failures (21.0%) | get_error_stats |
| 23:45 | Peak by rate in 15-min windows: 37.5% (6 of 16, low volume) | get_error_stats |
| 23:31-23:38 | Errors continue (error_ids 428-430) | get_stacktrace |

No rollback or fix appears in the deployment list. Detection time is unknown.

## Root cause
Payment v1.4.2 reads `payload["currency"]` without a default in `process_charge`, and older clients do not send `currency`, so the call raises an unhandled `KeyError` that checkout turns into HTTP 500.

Confidence: **high**. The deployment, the stack trace, the source line and a 95% failure rate for exactly the two old client versions all agree, and request volume did not rise.

## Evidence
- Deployment: payment v1.4.2 at 2026-03-12T14:01:00Z; first error 2.5 minutes later.
- First error: error_id 6 at 14:03:32Z (payment, req-e814a92f58, web-2.30.4); the matching checkout 500 came at 14:03:33Z with the same request_id.
- Other early failures: req-466b9882ae (error_id 7, 14:04:18, web-2.30.4), req-225dde05de (8, 14:04:27, android-5.7.9), req-d60f35d114 (9, 14:05:18, android-5.7.9).
- Stack traces: error_ids 6 (14:03), 8 (14:04, android-5.7.9), 268 (19:00, web-2.30.4) and 428 (23:31, android-5.7.9) are identical: `demo_app/payment/server.py` line 11 in `handle_charge`, then `demo_app/payment/charge.py` line 13 in `process_charge`, at `currency = payload["currency"]`.
- Source: a comment at `charge.py` lines 11-12 says older clients do not send "currency" and that since v1.4.2 it is read without a default.
- Per-client split: the newer clients (web-2.31.0, ios-5.8.1) have 0 failures, which matches the 18% overall failure rate.
- Volume was flat when errors began: payment requests per hour were 309 (12:00), 292 (13:00), 306 (14:00), 278 (15:00).

## Ruled out
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** deployed hours before the first error; the 11:00 and 12:00 windows had 0% errors, and neither service shows the incident error pattern.
- **Baseline strays:** catalog TimeoutError (error_id 1, 03:42, `/search`), plus single errors in auth (08:00), catalog (12:00), checkout (10:00) and payment (13:00), all at 1.4% of a window or less. Only error_id 1 was inspected.
- **Traffic surge:** hourly volume was flat.
- **Checkout as origin:** checkout only relays the payment error; payment fails 1 second earlier with matching request_ids.

## Immediate actions (proposed)
**Nothing has been applied.** The human decides whether to use the patch or the rollback.

### Patch
File `demo_app/payment/charge.py`, line 13: read `currency` with a default instead of raising. The default `"USD"` is a placeholder; the correct value is a business decision and is unknown. Only `currency` is changed; `card` and `amount` stay strict.

```diff
--- a/demo_app/payment/charge.py
+++ b/demo_app/payment/charge.py
@@ -11,6 +11,6 @@
     # Older clients do not send "currency". Since v1.4.2 it is read without a default.
     # This is the intentional bug of the demo; it is never fixed on main.
-    currency = payload["currency"]
+    currency = payload.get("currency", "USD")  # default for older clients; value needs business sign-off
     card = payload["card"]
     amount = float(payload["amount"])
     simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
```

Suggested test (not written or run): call `process_charge` with `card` and `amount` but no `currency` and expect a receipt with the default currency. No test files were found under `demo_app/`.

### Rollback
Roll payment back from v1.4.2 (deployed 2026-03-12T14:01:00Z by m.keller) to the previous version. The previous version is **unknown**: `get_deployments` shows only v1.4.2 for payment on this day. The rollback is meant to restore the pre-v1.4.2 tolerance of a missing currency, which is not confirmed. It would also revert the feature "read currency from charge payload". It does not address anything unrelated to this cause.

### Risks
- A fixed default currency may charge old clients (web-2.30.4, android-5.7.9) in the wrong currency; this is a financial risk until the business confirms the value.
- A silent default could mask other malformed payloads. Card and amount remain strict.
- The comment at `charge.py` lines 11-12 says "This is the intentional bug of the demo; it is never fixed on main." Check whether a fix on main is wanted before merging.
- Not verified: nothing was run, tested or applied. `demo_app/payment/gateway.py`, other callers of `process_charge`, consumers of `currency` in the receipt and any checkout-side handling were not checked.
- The rollback is untested and depends on the unknown previous version being compatible with current data and config.

## Follow-up tasks
- [ ] Add a test that sends a payload without `currency` to `process_charge`.
- [ ] Treat a missing `currency` as a handled validation case (default or 4xx) instead of an unhandled exception.
- [ ] Confirm the correct default currency for old clients with the business.

## Open questions
- 13 web-2.30.4 and 8 android-5.7.9 checkout "ok" lines are unexplained. They may carry `currency` or predate 14:01 (one android line is at 14:02:57Z, before the first error). Not verified.
- Payloads are not logged, so "old clients omit currency" rests on the code comment and the client split.
- Per-client failure rates by hour and the 14:00-14:03 window were not examined.
- Whether other endpoints or clients are affected is unknown, because only checkout logs carry a client field.
- Only 4 of 433 stack traces were sampled; all were identical.
- The share of users still on web-2.30.4 and android-5.7.9 is unknown.
- The previous payment version (rollback target) is unknown.
- Error_ids 2-5 and the other stray errors were not examined.
- Detection time of the incident is unknown.
