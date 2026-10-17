# Checkout 500 errors after payment v1.4.2 (KeyError: 'currency')

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats and search_logs (triage); get_deployments, search_logs, get_stacktrace and Grep (root-cause); 5 stack-trace samples examined (4 in the incident window: error_ids 6, 150, 268, 421; 1 noise sample); fix-proposer read `demo_app/payment/charge.py`, `demo_app/payment/server.py` and grep hits of `tests/test_demo_app.py`.

## Summary
Since 14:03 UTC about 18% of checkout requests fail with HTTP 500, and the failures are still ongoing at the end of the data (23:59 UTC). Payment v1.4.2, deployed at 14:01 UTC, reads `payload["currency"]` without a default and raises `KeyError: 'currency'` for legacy clients (web-2.30.4, android-5.7.9). Checkout only relays the payment error.

## Severity
**SEV-2 (Major).** 433 of 2402 checkout requests failed (18.0%) between 14:00 and 23:59 UTC, hourly rates 14.7% to 25.7%. The last window (23:00) still fails at 18/70 (25.7%), so the incident is ongoing after about 10 hours. Checkout and payment are core flows and are degraded, not down: 18.0% is in the 10-50% band, below the 50% line of SEV-1, and revenue is affected. Triage's preliminary SEV-2 is consistent with this.

## Impact
- `POST /checkout` (checkout) returns HTTP 500: `payment service error: KeyError: 'currency'`. Payment logs `Unhandled exception in charge: KeyError: 'currency'` one second earlier on the same request_id.
- Checkout: 433 / 2402 failed (18.0%). Payment: identical, 433 / 2402 (18.0%). Auth and catalog: no errors from 14:00.
- Affected clients (checkout log lines, 14:00-23:59): web-2.30.4 failed 275 of 288 (95%), android-5.7.9 failed 158 of 166 (95%). web-2.31.0 (0 of 1092) and ios-5.8.1 (0 of 856, queried) are not affected.
- Status: ongoing.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed (unrelated) | get_deployments |
| 11:15 | auth v3.1.1 deployed (unrelated) | get_deployments |
| 13:30-13:55 | No errors in the 5-minute windows | get_error_stats |
| 14:01:00 | payment v1.4.2 deployed by m.keller, commit "feat(payment): read currency from charge payload" | get_deployments |
| 14:03:32 | First payment error, req-e814a92f58, web-2.30.4 | error_id 6 |
| 14:03:33 | First matching checkout 500 | error_id 6 |
| 14:00 window | First failing window, checkout and payment 3/26 (11.5%) | get_error_stats |
| 17:00-17:59 | Highest rate at normal volume: 61/290 (21.0%), also peak error count | get_error_stats |
| 23:00-23:59 | Last window still failing: 18/70 (25.7%), highest rate but low volume | get_error_stats |

## Root cause
Payment v1.4.2 reads `payload["currency"]` without a default in `process_charge`, so every charge from legacy clients that do not send `currency` raises `KeyError: 'currency'`, which checkout relays as HTTP 500.

Confidence: **high**. The deploy lines up with the onset, the stack traces are identical across the window, only two client versions fail, and the code comment in the repo describes this exact behaviour. The real request payloads could not be checked.

## Evidence
- payment v1.4.2 deployed at 14:01:00; first error at 14:03:32, about 2.5 minutes later.
- 4 stack-trace samples in the window are the same trace: `KeyError: 'currency'` at `demo_app/payment/server.py` line 11 (`handle_charge`), then `demo_app/payment/charge.py` line 13 (`process_charge`, `currency = payload["currency"]`). All `POST /checkout`, HTTP 500:
  - error_id 6, 14:03:32, req-e814a92f58, web-2.30.4
  - error_id 150, 17:03:40, req-80c9b11bcc, web-2.30.4
  - error_id 268, 19:00:53, req-1a1e9a9e6e, web-2.30.4
  - error_id 421, 23:00:05, req-bbd61ebed7, android-5.7.9
- Errors 6 to 11 (14:03 to 14:06) show payment then checkout lines on the same request_id; clients are only web-2.30.4 and android-5.7.9.
- Per-client checkout log lines, 14:00-23:59:

  | client | ok | failed |
  |---|---|---|
  | web-2.30.4 | 13 | 275 |
  | android-5.7.9 | 8 | 158 |
  | web-2.31.0 | 1092 | 0 |
  | ios-5.8.1 | 856 | 0 |

  1969 ok + 433 failed = 2402, matching triage.
- `demo_app/payment/charge.py` line 11 comment: "Older clients do not send "currency". Since v1.4.2 it is read without a default."

## Ruled out
- catalog v2.8.0 (09:30) and auth v3.1.1 (11:15): hours before the onset, no errors from 14:00, not in the failing stack.
- Pre-incident noise, error_ids 1 to 5, each a different exception on a single request, none involving `currency`, none repeating: id 1 03:42 catalog TimeoutError; id 2 08:17 auth ValueError; id 3 10:48 checkout ConnectionResetError (web-2.31.0); id 4 12:26 catalog IndexError; id 5 13:12 payment TimeoutError.
- Checkout as origin: it only relays the payment error ("payment service error"), payment logs first.
- General payment outage or load problem: current clients (web-2.31.0, ios-5.8.1) have 0 failures over 1948 lines.

## Immediate actions (proposed)
**Nothing has been applied.** The human decides whether to roll back or ship a fix.

### Patch
`demo_app/payment/charge.py`: read `currency` with a default so legacy clients no longer raise. The default "EUR" is an assumption; the business default was not confirmed. The patch is for a fix branch, not `main`: the bug is intentional on `main` (see Risks).

```diff
--- a/demo_app/payment/charge.py
+++ b/demo_app/payment/charge.py
@@ -8,11 +8,11 @@
 
 def process_charge(payload: dict) -> dict:
     """Authorize a charge described by `payload` and return a receipt."""
-    # Older clients do not send "currency". Since v1.4.2 it is read without a default.
-    # This is the intentional bug of the demo; it is never fixed on main.
-    currency = payload["currency"]
+    # Older clients do not send "currency"; fall back to a default (assumed EUR).
+    # Fix for the v1.4.2 KeyError incident.
+    currency = payload.get("currency", "EUR")
     card = payload["card"]
     amount = float(payload["amount"])
     simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
     authorization = gateway.authorize(card, amount, simulate_timeout=simulate_timeout)
```

### Rollback
Roll back payment from v1.4.2 (deployed 2026-03-12 14:01:00 UTC by m.keller) to the previous payment version. The previous version is unknown (no deployment data for it); look it up before rolling back. Leave catalog v2.8.0 and auth v3.1.1 alone. A rollback should stop the failures for web-2.30.4 and android-5.7.9 only if the old version tolerated a missing currency, which is unconfirmed. It does not fix the code, so the bug returns on the next deploy of that change, and it removes the "read currency from charge payload" feature for all clients.

### Risks
- Patch: the default currency is an assumption. Charging a legacy client in the wrong currency is a money risk, so the business must confirm it. A 4xx rejection is safer financially but legacy clients would still not complete a purchase; it was not proposed because checkout's error mapping was not read.
- Patch: `tests/test_demo_app.py` has a strict xfail (marker at lines 35-43) that would turn into an unexpected pass, and line 80 asserts that `payload["currency"]` appears in `charge.py`, which would fail. Both must change together with the patch; their full context was not read.
- Patch: other callers of `process_charge`, gateway behaviour and real payloads were not checked. Not run, not applied, tests not run. The line count is unchanged so the frames in `frames.py` should stay valid (to be verified).
- Rollback: the previous version and its compatibility with current data or schema are unverified.

## Follow-up tasks
- [ ] Add a test for payloads that omit `currency` (proposed by root-cause).
- [ ] Remove the strict xfail marker and update the line 80 assertion in `tests/test_demo_app.py` together with any fix (proposed by fix-proposer).
- [ ] Confirm the business default currency before shipping a default (proposed by fix-proposer).

## Open questions
- Real charge payloads were not read; "legacy clients omit `currency`" rests on the code comment and the per-client pattern. Look at the payload of a failing request.
- Did v1.4.1 handle a missing currency? The v1.4.2 diff beyond the commit message was not checked.
- 21 ok log lines from the two legacy clients (13 + 8) were not examined; they may be requests that sent a currency or were not charged.
- Other endpoints and services have no client field, so no breakdown was possible.
- The 13:00 payment error (id 5) was classed as noise from its stack trace; error_ids 1 to 5 were not examined further.
- Reporter's "about 2 pm" is assumed to be UTC; not confirmed.
- The previous payment version for a rollback is unknown.
