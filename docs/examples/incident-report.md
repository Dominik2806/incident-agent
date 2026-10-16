# Checkout 500 errors caused by payment KeyError 'currency' after v1.4.2

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace; 5 stack-trace samples examined (4 `KeyError: 'currency'`: error_id 6, 12, 200, 426; 1 unrelated timeout: error_id 5)

## Summary
About 19% of `checkout` requests have failed with HTTP 500 since 14:03 UTC, because `payment` raises `KeyError: 'currency'` in `charge`. The first error came about 2 minutes after payment v1.4.2 was deployed at 14:01:00, whose commit is "feat(payment): read currency from charge payload". The failures are still ongoing at the end of the data (23:59).

## Severity
**SEV-2 (Major).** Checkout is a core flow and is degraded, not down: 435 failed / about 2,315 requests = about 18.8% from 14:00 to 23:59, which is in the 10-50% band and below the 50% SEV-1 line. The errors are sustained (about 10 hours) and ongoing: the last window (23:00-24:00) still fails at 17/79 = 21.5%. Revenue is affected because those customers cannot complete payment. The triage preliminary severity (SEV-2) agrees.

## Impact
- Roughly 1 in 5 checkout attempts fail with `POST /checkout 500 payment service error: KeyError: 'currency'`. The error is intermittent, not a full outage.
- Affected: `checkout` (`POST /checkout`) as the victim and `payment` (`charge`) as the origin. Payment shows the same rate in every window (e.g. 14:00-14:15 at 12/66 = 18.2%) and 436 ERROR lines, one of which is unrelated (13:12:03 timeout).
- `auth` and `catalog` had 0 errors during the incident.
- Rate is roughly flat at 10-25% with no recovery trend. Peak 15-minute window: 14:45 at 25.0% in checkout (14/56). Peak hour: 20:00 at 23.4% (60/257).
- Status: ongoing. The data ends at 23:59, so recovery after that is unknown.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed (not related) | get_deployments |
| 11:15 | auth v3.1.1 deployed (not related) | get_deployments |
| 13:12:03 | Single payment `TimeoutError: card gateway timeout after 5.0s` (not related) | error_id 5, req-bd7ca21683 |
| 13:55-14:02 | 0 errors in checkout and payment | get_error_stats (1-minute windows) |
| 14:01:00 | payment v1.4.2 deployed by m.keller, "feat(payment): read currency from charge payload" | get_deployments |
| 14:03:13 | First `KeyError: 'currency'` in payment | error_id 6, req-4a7979fda4 |
| 14:03:14 | Checkout logs the same request as 500 "payment service error" | error_id 6, req-4a7979fda4 |
| 14:45 | Peak 15-minute window: checkout 25.0% (14/56), payment 23.6% (13/55) | get_error_stats |
| 23:00-24:00 | Still failing: checkout 17/79 = 21.5% | get_error_stats |

## Root cause
Payment v1.4.2 reads `payload["currency"]` without a default at `/app/payment/charge.py:41`, so charge requests without a `currency` field raise `KeyError: 'currency'` and checkout returns HTTP 500.

**Confidence:** medium-high for the cause and mechanism, because the deployment timing, the identical stack traces and the commit message agree. Only medium on why about 19% of requests fail and not all, because the logs do not show which payloads lack `currency`.

## Evidence
- Deployment: payment v1.4.2 at 14:01:00, about 2 minutes before the first error. No errors in either service in the 1-minute windows 13:55-14:02.
- First failure: payment error_id 6 at 14:03:13, req-4a7979fda4. Checkout logs the same request at 14:03:14 as `POST /checkout 500 payment service error: KeyError: 'currency'`.
- Four sampled stack traces are identical (`server.py:88 handle_charge` -> `charge.py:41 process_charge` -> `currency = payload["currency"]` -> `KeyError: 'currency'`):

  | error_id | Time (UTC) | request_id | client_version | endpoint | status |
  |---|---|---|---|---|---|
  | 6 | 14:03:13 | req-4a7979fda4 | web-2.31.0 | POST /checkout | 500 |
  | 12 | 14:06:23 | req-174165e9f7 | web-2.30.4 | POST /checkout | 500 |
  | 200 | 18:01:53 | req-e20efceb56 | android-5.7.9 | POST /checkout | 500 |
  | 426 | 23:11:18 | req-9142d3b535 | android-5.7.9 | POST /checkout | 500 |

- Request-context comparison: failures span web-2.31.0, web-2.30.4 and android-5.7.9, all on `POST /checkout` and one code path. Successful requests in the same window log "charge ok order=... EUR" (req-dc91aab53e at 14:03:05, req-ac3e620898 at 14:03:57, req-385a81f5cf at 14:04:28). Success lines carry no `client_version` or payload, so failing and succeeding requests could not be compared directly.
- Volume: payment has 243 KeyError matches from 18:00 onward, 17 of them in the 23:00 hour.
- Code check: the production source `/app/payment/charge.py` is not in this repo, so `charge.py:41` was not verified in real code. The only repo hits are in the synthetic data generator (`src/incident_agent/data/scenarios.py`), which is not independent evidence.

## Ruled out
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** deployed hours before the first error, and both services have 0 errors in the incident window.
- **Gateway timeout, error_id 5 (13:12:03):** different exception type and code lines (`charge.py:58`, `gateway.py:90`), happened before the deploy, single line, did not recur.
- **Earlier catalog errors (03:40 and the 12:15 window):** different service, no link to the KeyError.
- **Checkout-side bug:** checkout only wraps the payment error and was not deployed.
- **Client-version regression:** failures occur across web-2.31.0, web-2.30.4 and android-5.7.9.
- **Auth or catalog dependency failure:** neither shows errors in the incident window.

## Immediate actions (proposed)
Nothing has been applied. For the human to decide:
1. Roll back payment to the version before v1.4.2. This is consistent with the evidence (errors began about 2 minutes after the deploy and have not recovered). Risk: the rollback also removes whatever the v1.4.2 change was meant to deliver, and it was not checked what else v1.4.2 contains.
2. Fix forward in `charge.py:41`: read the currency defensively (for example `payload.get("currency", <default>)`) or validate the payload and return a clear 4xx instead of a 500. Risk: a silent default currency could charge in the wrong currency, so the default must be chosen deliberately.

## Follow-up tasks
- [ ] Add a test with a charge payload that lacks `currency`.
- [ ] Find which callers send payloads without `currency` (this explains the about 19% failure rate); fix them or make the new field backward compatible before redeploying.

## Open questions
- Why only about 19% of requests fail. The hypothesis, not shown in the data, is that some callers or payloads omit `currency` (candidates: a checkout code path, payment method, cart type or client flow). Check by logging or inspecting the keys of charge payloads for failing versus succeeding requests.
- Whether checkout was meant to send `currency` and a checkout or client change stopped it. No such deployment appears in the data.
- Only 4 KeyError stack traces were pulled out of 436. All 4 are identical, but not every row was read. Triage also read only the first 6-8 ERROR lines per service, and its 5-minute overview was truncated at 200 windows (it stopped at 04:15), so it used 15-minute and hourly windows for the afternoon and evening.
- The real payment source and the v1.4.2 diff, to confirm `payload["currency"]` at line 41 is the new change.
- Whether the incident recovered after 23:59 (the data ends there).
