# Checkout 500 errors after payment v1.4.2 (KeyError: 'currency')

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (triage and root-cause agents); 4 stack-trace samples examined (error_id 6, 8, 100, 205); files read by fix-proposer: `demo_app/payment/charge.py`, `demo_app/payment/server.py`, `tests/test_demo_app.py`

## Summary
Since 14:03 UTC about 18% of checkouts fail with HTTP 500. Payment v1.4.2 (deployed 14:01 UTC) reads `payload["currency"]` without a default, and the legacy clients `web-2.30.4` and `android-5.7.9` do not send that field, so payment raises `KeyError: 'currency'`. The incident is still ongoing at the end of the data (23:59 UTC).

## Severity
**SEV-2 (Major).** Checkout is a core flow and is degraded, not down: 433 of 2,402 requests (18.0%) have failed since 14:03 UTC, which is inside the 10-50% band and below the 50% line of SEV-1. The last window (23:55 UTC, 1 of 6 requests) still fails, so the incident is ongoing after about 10 hours and affects revenue.

## Impact
- Customers get HTTP 500 on `POST /checkout`.
- Checkout: 433 failed of 2,402 requests (18.0%) from 14:00 to 23:59 UTC; hourly rates 14.7% to 25.7%. Payment: the same 433 failed of 2,402, sharing `request_id` with checkout failures.
- Checkout failures by client (log lines from 14:00): web-2.30.4 275/288 (95.5%), android-5.7.9 158/166 (95.2%), web-2.31.0 0/1092, ios-5.8.1 0/856.
- auth and catalog are not affected (0 failed requests from 14:00 onward).
- Ongoing: yes.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed (ruled out) | get_deployments |
| 11:15 | auth v3.1.1 deployed (ruled out) | get_deployments |
| 14:01 | payment v1.4.2 deployed by m.keller, "feat(payment): read currency from charge payload" | get_deployments |
| 14:03:32 | First `KeyError: 'currency'` in payment (req-e814a92f58) | error_id 6 |
| 14:03:33 | First checkout 500 | search_logs |
| 14:24 | Worst 1-minute window, 4 of 5 requests failed (80%) | get_error_stats |
| 17:00 | Highest-volume bad hour, 61 of 290 (21.0%) | get_error_stats |
| 23:00 | Highest hourly rate, 18 of 70 (25.7%) | get_error_stats |
| 23:55 | Last window still failing (1 of 6) | get_error_stats |

## Root cause
Payment v1.4.2 reads `payload["currency"]` with no default, and legacy clients web-2.30.4 and android-5.7.9 do not send `currency`, so their checkouts fail with `KeyError: 'currency'`.

Confidence: **high**. Timing, identical stack traces, the code and the per-client counts all agree. The counts reconcile exactly, and the two legacy versions fail about 95% of the time while the other two never fail.

## Evidence
- **Deployment:** payment v1.4.2 at 14:01:00Z; first error 14:03:32Z (about 2.5 minutes later). The 12:00 and 13:00 hours had 0 errors; the 14:00 and 15:00 hours had 45 errors each (14.7% and 16.2%).
- **Stack traces (4 samples, same exception and frames):** `demo_app/payment/server.py:11 handle_charge`, then `demo_app/payment/charge.py:13 process_charge`, at `currency = payload["currency"]`.

  | error_id | time (UTC) | request_id | client | endpoint |
  |---|---|---|---|---|
  | 6 | 14:03:32 | req-e814a92f58 | web-2.30.4 | POST /checkout |
  | 8 | 14:04:27 | req-225dde05de | android-5.7.9 | POST /checkout |
  | 100 | 16:02:56 | req-9945d0582a | web-2.30.4 | POST /checkout |
  | 205 | 17:55:44 | req-a880d89ec7 | web-2.30.4 | POST /checkout |

- **Code:** `demo_app/payment/charge.py` line 13 is `currency = payload["currency"]`. The comment says older clients do not send "currency" and that since v1.4.2 it is read without a default.
- **Client comparison (checkout log lines, 14:00 to end of day):** the counts are in Impact. INFO totals 13+8+1092+856 = 1969 and ERROR totals 275+158+0+0 = 433, both equal to the `client=` totals, so no client version is missing. Payment, auth and catalog logs have no client field, so only checkout could be split by client.
- **Volume:** checkout requests per hour were 309 (12:00), 291 (13:00), 306 (14:00) and 278 (15:00), so there was no traffic increase at onset.

## Ruled out
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** deployed 2.5 to 4.5 hours before onset; auth and catalog have no errors from 14:00 on, only single baseline errors (auth at 08:00, catalog at 03:00 and 12:00).
- **Load or traffic surge:** volume was flat across 12:00 to 15:00 (309, 291, 306, 278).
- **Stray single errors (checkout at 10:00, payment at 13:00):** isolated and before the deploy; not inspected, treated as noise.
- **Client-independent payment failure:** web-2.31.0 and ios-5.8.1 have 0 failures out of 1,948 log lines, so the failure depends on the client.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides whether to use the patch or the rollback.

### Patch
File `demo_app/payment/charge.py`, line 13: read `currency` with a default instead of raising `KeyError`. For a fix branch only; the `KeyError` is intentional on `main` (see CLAUDE.md), so do not merge it there. The default `"EUR"` is an assumption (from `docs/scenarios.md`) that the business has not confirmed.

```diff
--- a/demo_app/payment/charge.py
+++ b/demo_app/payment/charge.py
@@ -10,7 +10,7 @@
     """Authorize a charge described by `payload` and return a receipt."""
-    # Older clients do not send "currency". Since v1.4.2 it is read without a default.
-    # This is the intentional bug of the demo; it is never fixed on main.
-    currency = payload["currency"]
+    # Older clients do not send "currency"; fall back to a default (assumed EUR).
+    # Fix for the v1.4.2 KeyError incident (fix branch only, not main).
+    currency = payload.get("currency", "EUR")
     card = payload["card"]
     amount = float(payload["amount"])
     simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
```

Tests in `tests/test_demo_app.py` that must change together with this patch (read, not run):
- `test_checkout_without_currency_must_not_fail_with_500` (lines 35-48) would XPASS, which fails under `strict=True`; remove the `xfail` marker.
- `test_keyerror_frames_match_the_real_traceback` (line 83) expects a `KeyError` and would fail.
- `test_generated_frames_point_to_real_demo_app_lines` (line 60) would fail on its source-text check against `src/incident_agent/data/frames.py` (line 80 asserts `payload["currency"]` appears in `charge.py`).

### Rollback
Roll back `payment` from v1.4.2 (deployed 2026-03-12T14:01:00Z by m.keller) to the previous payment version. That version is unknown: it is not in the repository or the deployment data, so look it up in the deployment history. Do not roll back catalog v2.8.0 or auth v3.1.1 (ruled out). The rollback does not fix the code and does not recover the 433 failed checkouts that already happened. It also drops whatever else v1.4.2 shipped, which was not inspected.

### Risks
- **Wrong currency:** the `"EUR"` default may charge legacy clients (web-2.30.4, android-5.7.9) in the wrong currency, a money risk, so the business must confirm the default before shipping. A 4xx rejection would be safer financially, but legacy clients would still not complete a purchase.
- **Unexplained successes:** 21 legacy-client checkouts succeeded and are unexplained; they may have sent `currency`, or may not have been charged.
- **Not verified:** nothing was run or applied and the tests were not run. The real charge payloads were not read, so the claim that legacy clients omit `currency` rests on the code comment and the per-client failure pattern. Other callers of `process_charge` and `demo_app/checkout/server.py` were not checked.
- **Rollback:** depends on knowing the previous version, and could remove other v1.4.2 changes. Whether the previous version handled a missing `currency` is unverified.

## Follow-up tasks
- [ ] Add a test with a charge payload that has no `currency` (proposed by root-cause).
- [ ] Consider forcing an upgrade of web-2.30.4 and android-5.7.9, or making those clients send `currency` (proposed by root-cause).
- [ ] Update the three tests listed under Patch together with any fix (proposed by fix-proposer).

## Open questions
- The 21 successful legacy-client requests were not examined (for example req-4ea6872c8b, web-2.30.4, 14:18:23). Check whether their payloads contain `currency`.
- The ERROR listing covered only error_ids 6 to 205 of 433 (up to 17:55 UTC). The per-client counts do not depend on it, because they use `total_matches`.
- Other (non-checkout) callers of payment `charge`, and how many real users still run the legacy clients, were not checked.
- Payment log lines from 14:01 to 14:03 were not checked to confirm that no `currency` field was logged.
- The previous payment version and the correct default currency (or a 4xx rejection) are unknown.
- How checkout maps the payment error to HTTP 500 (`demo_app/checkout/server.py`) was not read.
- The data ends at 23:59 UTC, so the state after that is unknown.
