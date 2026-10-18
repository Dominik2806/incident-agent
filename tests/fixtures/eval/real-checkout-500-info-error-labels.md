# Checkout 500 errors caused by payment v1.4.2 KeyError 'currency'

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (triage and root-cause agents); 4 stack-trace samples examined (error_id 5, 6, 8, 401; 3 of them are the KeyError); fix-proposer read `demo_app/payment/charge.py`, `demo_app/payment/server.py` and `tests/test_demo_app.py`

## Summary
Since 14:03 UTC, `POST /checkout` has returned HTTP 500 for customers on older clients (web-2.30.4 and android-5.7.9). Payment v1.4.2, deployed at 14:01:00Z, reads `payload["currency"]` without a default, and these clients don't send `currency`. The failure is ongoing: 433 of 2,402 checkout requests (18.0%) have failed.

## Severity
**SEV-2 (Major).** Checkout is a core flow and is degraded, not down. 433 of 2,402 requests (18.0%) have failed since 14:03 UTC, which is in the 10-50% band and below the 50% line of SEV-1. The errors are sustained (about 10 hours) and ongoing: the last window (23:55-24:00) still shows 1 failed request of 6, and the 23:00 hour has 18 failures of 70 requests. Revenue is affected because customers cannot complete the purchase.

## Impact
- Customers on web-2.30.4 and android-5.7.9 get HTTP 500 on `POST /checkout` and cannot complete the purchase.
- Affected services: checkout (relays the error as a 500) and payment (origin, `charge`). Both show 433 failed of 2,402 requests (18.0%); hourly rates ran 14.7% to 25.7%.
- By client in checkout logs from 14:00Z: web-2.30.4 failed 275 of 288 lines (95%); android-5.7.9 failed 158 of 166 (95%); web-2.31.0 (1092 OK) and ios-5.8.1 (856 OK) had 0 failures.
- Auth and catalog are not affected.
- Ongoing at the end of the data (23:59 UTC).

## Timeline (UTC)
| Time (UTC) | Event | Source |
|---|---|---|
| 13:12 | Baseline payment gateway TimeoutError, unrelated | get_stacktrace error_id 5 |
| 14:01 | payment v1.4.2 deployed by m.keller ("feat(payment): read currency from charge payload") | get_deployments |
| 14:03:32 | First KeyError: 'currency' in payment (req-e814a92f58, web-2.30.4); checkout 500 about 1s later | error_id 6 |
| 14:00-14:05 window | 3 failed requests of 26 (11.5%) in checkout and payment | get_error_stats |
| 17:00-18:00 | Worst full-traffic hour: 61 of 290 (21.0%) | get_error_stats |
| 22:10:14 | Same KeyError still occurring (req-e7d76fec10, web-2.30.4) | error_id 401 |
| 23:00-24:00 | Highest hourly rate, 25.7% (18 of 70, low traffic) | get_error_stats |
| 23:55-24:00 | Last window still failing: 1 of 6 | get_error_stats |

Detection time and rollback/mitigation time: unknown (not in the data).

## Root cause
Payment v1.4.2 reads `currency = payload["currency"]` with no default (`demo_app/payment/charge.py` line 13), so requests from clients that don't send `currency` raise `KeyError: 'currency'`, and checkout returns 500.

Confidence: **high**. The deploy time, the identical stack trace across samples, the source line, and a clean split by client version all point to the same cause.

## Evidence
- Deployment: payment v1.4.2 at 2026-03-12T14:01:00Z. First KeyError at 14:03:32Z, about 2.5 minutes later. It is the only payment deployment of the day.
- Stack traces for error_id 6 (14:03:32Z, req-e814a92f58, web-2.30.4), error_id 8 (14:04:27Z, req-225dde05de, android-5.7.9) and error_id 401 (22:10:14Z, req-e7d76fec10, web-2.30.4) are identical: `demo_app/payment/server.py` line 11 (`handle_charge` calls `process_charge(payload)`) into `demo_app/payment/charge.py` line 13, `KeyError: 'currency'`. Endpoint POST /checkout, status 500.
- Source: comment on `charge.py` lines 11-12 says older clients don't send `currency` and that it has been read without a default since v1.4.2. Other optional fields use `.get` (line 16).
- Client split in checkout logs from 14:00Z (INFO / ERROR): web-2.30.4 13 / 275; android-5.7.9 8 / 158; web-2.31.0 1092 / 0; ios-5.8.1 856 / 0. Totals 1969 / 433 match the query totals.
- The `KeyError` query with level ERROR also returns 433, so all 433 checkout errors in the window are this KeyError.
- Payment KeyErrors continue after 22:00Z (40 matches; error_id 399-401 read).
- Request-context comparison of payloads between failing and succeeding requests from the same client version was not done.

## Ruled out
- Catalog v2.8.0 (09:30) and auth v3.1.1 (11:15): deployed hours before the incident, and neither service has errors in the window.
- Baseline errors (error_id 1-5): each is a single occurrence with a different exception type. The 13:12 payment error (error_id 5) is a gateway TimeoutError, not a KeyError.
- Gateway or timeout problem: no timeouts appear in the incident errors.
- Traffic surge: request volume per window was not measured, so this is not measured. The clean split by client version and the matching code make a load cause unlikely.

## Immediate actions (proposed)
**Nothing has been applied, edited or executed.** These are proposals for a human to decide.

### Patch
One-line change in `demo_app/payment/charge.py` that reads `currency` with a default and keeps all line numbers. The default `"EUR"` is an assumption (the only hint is the `EUR` in the test fixture `VALID`) and needs a decision from the payment owner.

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

Test: `tests/test_demo_app.py::test_checkout_without_currency_must_not_fail_with_500` (line 34) covers this case with the `LEGACY` payload. It is marked `xfail(strict=True)` (lines 25-33), so after the fix it would XPASS and fail in strict mode. The marker must be removed in the same change; it is not part of the diff above. The alternative named in that marker's reason is a 4xx validation error, which needs more code, and old clients would still fail to check out.

### Rollback
Roll back service `payment` from v1.4.2 (deployed 2026-03-12T14:01:00Z by m.keller) to the previous version. The previous version is **unknown**: the repo has no git history or version list, and the deployment data for 2026-03-12 contains only v1.4.2 for payment. It must come from release tooling. Rolling back should stop the KeyError failures for web-2.30.4 and android-5.7.9, assuming the previous version didn't read `currency` strictly (not verified). It also undoes the feature "read currency from charge payload", and what depends on it is unknown. It does not address other errors, such as the baseline gateway TimeoutError.

### Risks
- Wrong default currency: old clients would be charged in `EUR` even if the user expected something else. This is a financial risk, so confirm the default with the payment owner before shipping. Rejecting with a 4xx avoids a wrong charge but keeps the old clients unable to pay.
- The patch and the tests were not run or applied. The hunk header was checked by hand against the lines read.
- Not examined: other callers of `process_charge`; checkout payload-building code; whether the receipt's `currency` field is consumed downstream; whether `gateway.authorize` needs a currency.
- Rollback may break clients that now rely on v1.4.2 behavior (unknown).

## Follow-up tasks
- [ ] Remove the `xfail(strict=True)` marker from `test_checkout_without_currency_must_not_fail_with_500` together with the fix (proposed by fix-proposer).
- [ ] Add a test with a payload that has no `currency` (proposed by root-cause).
- [ ] Confirm the default currency (or a 4xx rejection) with the payment owners (proposed by root-cause and fix-proposer).

## Open questions
- Payment-side lines were not read for all 433 requests; the checkout side is fully accounted for by the `KeyError` query.
- It is not confirmed that the old-client successes (13 for web-2.30.4, 8 for android-5.7.9) sent `currency`.
- The checkout code that builds the charge payload was not examined. To raise confidence, compare the payload of a failing request with that of a succeeding one from the same client version, and check whether checkout's request builder sets `currency` for the newer clients.
- The previous payment version for a rollback is unknown.
- Request volume per window was not measured.
- Triage read only the first 6 of 433 error lines; root-cause later confirmed the KeyError via the full-day query (433 matches).
