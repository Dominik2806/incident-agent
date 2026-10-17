# Checkout 500 errors after payment v1.4.2: KeyError 'currency' for legacy clients

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (triage and root-cause agents). Root-cause reports 5 identical stack-trace samples (error_ids 6, 8, 210, 429 listed in its table, plus error_id 7 seen in the log search). fix-proposer read `demo_app/payment/charge.py`, `demo_app/payment/server.py`, `demo_app/checkout/server.py`, `tests/test_demo_app.py`, `docs/scenarios.md` and `docs/examples/incident-report.md`.

## Summary
Since about 14:03 UTC about 18% of `POST /checkout` requests fail with HTTP 500. The cause is most likely payment v1.4.2 (deployed 14:01:00Z), which reads `payload["currency"]` without a default and so fails for the legacy clients web-2.30.4 and android-5.7.9, which do not send it. The incident is ongoing at the end of the data.

## Severity
**SEV-2 (Major).** Checkout is a core flow and is degraded, not down. 433 of 2,402 checkout requests (18.0%) failed from 14:00 to 23:59 UTC, which is in the 10-50% band and below the 50% SEV-1 line. The errors are sustained: every hourly window from 14:00 to 23:00 is affected (14.7% to 25.7%) and the last 15-minute window (23:45) still fails, 6 of 16 (37.5%). Revenue is affected. Triage's preliminary severity was also SEV-2.

## Impact
- **Users:** customers on web-2.30.4 and android-5.7.9 get an HTTP 500 on `POST /checkout`. Checkout log: `POST /checkout 500 payment service error: KeyError: 'currency'`.
- **Services:** checkout 433 failed of 2,402 requests (18.0%) and payment 433 failed of 2,402 (18.0%), 14:00 to 23:59 UTC. Failed counts match in every hour and each failed `request_id` appears in both services. Checkout relays the payment error. Auth and catalog have 0 errors from 14:00 onward.
- **By client (checkout log lines from 14:00Z):** web-2.31.0 1092 ok / 0 failed; ios-5.8.1 856 / 0; web-2.30.4 13 / 275; android-5.7.9 8 / 158. These are log-line counts, not user-visible requests, and cover checkout only.
- **Status:** ongoing at the end of the data.

## Timeline (UTC)
| Time (UTC) | Event | Source |
|---|---|---|
| 14:01:00 | payment v1.4.2 deployed by m.keller ("feat(payment): read currency from charge payload") | get_deployments |
| 14:03:32 | First payment error, `KeyError: 'currency'` (web-2.30.4, req-e814a92f58) | get_stacktrace, error_id 6 |
| 14:03:33 | First checkout 500 for the same request | triage, error_id 6 |
| 14:00-14:05 | 3 of 26 checkout requests failing (11.5%), elevated in every later window | get_error_stats |
| 17:00-18:00 | Highest hour with real volume: 61 of 290 (21.0%) | get_error_stats |
| 23:00-24:00 | Highest hourly rate: 18 of 70 (25.7%), low traffic | get_error_stats |
| 23:45 | Last window still failing: 6 of 16 (37.5%) | get_error_stats |

The peak is not pinned to a 5-minute window (5-minute windows after 14:30 were not checked). No detection time is in the data.

## Root cause
Payment v1.4.2 reads `payload["currency"]` without a default in `process_charge`, which raises `KeyError: 'currency'` for legacy clients (web-2.30.4, android-5.7.9) that do not send it; checkout turns this into HTTP 500.

**Confidence: high.** The deployment timing, the identical stack traces, the matching code comment, and a clean split by client version (legacy versions fail almost every time, current versions never fail) all agree, and the per-version counts reconcile.

## Evidence
- **Deployment:** payment v1.4.2 at 14:01:00Z; first error about 2.5 minutes later (14:03:32Z payment, 14:03:33Z checkout, error_id 6, req-e814a92f58).
- **Stack traces:** all samples identical: `KeyError: 'currency'` at `demo_app/payment/charge.py` line 13 in `process_charge`, called from `demo_app/payment/server.py` line 11 in `handle_charge`.

  | error_id | Time (UTC) | Request | Client |
  |---|---|---|---|
  | 6 | 14:03:32 | req-e814a92f58 | web-2.30.4 |
  | 8 | 14:04:27 | req-225dde05de | android-5.7.9 |
  | 210 | 18:01:32 | req-1269ab9b4c | web-2.30.4 |
  | 429 | 23:34:42 | req-1e23ada914 | android-5.7.9 |

  All sampled requests were `POST /checkout` with HTTP 500. Error_id 7 (req-466b9882ae, web-2.30.4) was also seen in the log search.
- **Code:** `charge.py` line 11 comment: "Older clients do not send "currency". Since v1.4.2 it is read without a default." Line 13 is `currency = payload["currency"]`. `demo_app/checkout/server.py` lines 26-27 catch every exception and return 500 with `payment service error: ...`.
- **Per-client split:** the INFO and ERROR sums (1969 ok, 433 failed) match the totals. The 433 equals the count of ERROR lines containing `KeyError`, so every failure in this window is this bug.
- **Before the deploy:** checkout had 0 failures in the 13:00 and 13:30 windows (139 and 152 requests), against 17 of 142 (14:00 window) and 28 of 164 (14:30 window). web-2.30.4 had 34 checkout log lines between 13:00Z and 14:00Z. Volume did not rise.
- **Still failing at the end:** 229 checkout ERROR lines from 18:00Z onward; the 11 errors from 23:30Z onward are still `KeyError: 'currency'` (error_ids 428 to 430 seen).
- **Legacy clients are not 100% failing:** 21 INFO lines for the two legacy versions (13 + 8, e.g. req-4ea6872c8b at 14:18:23, req-7a405b7809 at 14:02:57) against 433 failed, about 95% failing.

## Ruled out
- **Catalog v2.8.0 (09:30Z) and auth v3.1.1 (11:15Z):** deployed more than 2.5 hours before onset; auth and catalog have 0 errors from 14:00Z onward.
- **Traffic surge:** checkout volume per 30 minutes is flat (139, 152, then 142, 164); a load problem would not give a stack trace at a specific line or split by client version.
- **Baseline noise:** stray single errors (checkout 10:00, payment 13:00, catalog 03:00 and 12:00, auth 08:00) predate the incident and are isolated. No payment ERROR lines were found between 13:30Z and 14:03Z. Error_ids 1 to 5 were not read.
- **Checkout as origin:** it only relays the payment error, and payment logged first.
- **A particular endpoint:** all sampled errors are `POST /checkout`; other endpoints could not be tested, because only checkout logs carry a client version.

## Immediate actions (proposed)
**Nothing has been applied, edited or run.** The human decides whether to use the patch, the rollback or neither.

### Patch
Proposal for a fix branch only; the bug is intentional on `main` and must not be fixed there. Changing only line 13 of `demo_app/payment/charge.py` keeps all line numbers stable.

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

The default `"EUR"` is an assumption: nothing in `demo_app/` names the correct currency (it only appears in `docs/scenarios.md` line 101 and `docs/examples/incident-report.md`, which also treat it as unconfirmed). The business must confirm it. Alternative: reject a missing currency with a 4xx, which avoids guessing but legacy clients still cannot buy, and checkout's catch-all would need a change too (not designed).

### Rollback
Roll payment back from v1.4.2 (deployed 2026-03-12T14:01:00Z by m.keller) to the previous payment version. The previous version is unknown (not in the data given to fix-proposer); look it up in the deployment history first. Expected to stop new `KeyError: 'currency'` failures for web-2.30.4 and android-5.7.9, since those clients worked before the deploy. It does not fix the code on any branch, the checkout 500 mapping, or explain the 21 successful legacy requests, and it reverts whatever else v1.4.2 contained (unknown).

### Risks
- **Wrong currency (patch):** charging a legacy client in an assumed currency is a money risk; confirm the default first.
- **Masked bad payloads (patch):** a silent default can hide incomplete payloads; consider logging when it is used.
- **Test breakage (patch):** `tests/test_demo_app.py` line 80 asserts `payload["currency"]` appears in `charge.py`, and `test_keyerror_frames_match_the_real_traceback` expects a `KeyError`; both would fail. `test_checkout_without_currency_must_not_fail_with_500` is `xfail(strict=True)` and would XPASS, failing the run until the marker is removed. How to update them was not worked out.
- **Rollback:** loses the other v1.4.2 changes, which were not read.
- **Not verified:** the patch and tests were not run or applied; the v1.4.2 diff and the real client payload code were not read; callers outside `demo_app/` and `src/incident_agent/data/frames.py` were not checked.

## Follow-up tasks
- [ ] Confirm the correct default currency with the business, or choose the 4xx validation alternative.
- [ ] On the fix branch, update the demo-app tests that pin the intentional bug and remove the `xfail` marker in the same change.
- [ ] Consider logging when a default currency is used.
- [ ] Consider a forced-upgrade or compatibility plan for web-2.30.4 and android-5.7.9.
- [ ] Add a regression test with a payload that has no `currency`; redeploy only after it passes.

## Open questions
- The payment v1.4.2 diff and the checkout client payload code were not read. The "legacy clients omit currency" mechanism comes from the code comment and the failure pattern by client version. Check what web-2.30.4 and android-5.7.9 actually send in the charge payload.
- The 21 successful legacy-client requests are unexplained; compare their payloads with the failing ones.
- Payment-side per-client counts were not measured (payment logs have no client field); matching was by `request_id` for samples only.
- 5-minute onset granularity beyond the first 6 errors was not re-verified; the 30-minute stats and first errors are consistent with a step change at the 14:01 deploy.
- The peak is not pinned to a 5-minute window, and the late-evening peaks rest on low traffic.
- Which payment version preceded v1.4.2, and what else v1.4.2 contained, is unknown.
- Triage did not read error_ids 1 to 5 and did not check for other client versions; root-cause covered client versions. Root-cause states 5 identical samples but its table lists 4 (plus error_id 7 seen in the log search).
