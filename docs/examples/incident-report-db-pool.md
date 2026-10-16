# Catalog search failures: DB connection pool exhausted

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (4 stack-trace samples examined: error_id 5, 9, 390, 551; root-cause reported "five" but listed four). fix-proposer read no catalog source: it is not in this repository (only synthetic frames in `src/incident_agent/data/scenarios.py`).

## Summary
`GET /search` in the catalog service failed with 504/503 from 14:58 UTC until about 16:35 UTC. The catalog DB connection pool (pool_size=20) was exhausted and requests timed out after 5.0s waiting for a connection. The likely trigger is a surge in `/search` traffic from about 14:45 UTC; no deployment explains it.

## Severity
**SEV-3 (Minor).** Catalog is a non-core service and it did not block checkout or payment (0 failed requests in 14:45-17:00). About 553 of about 2,871 catalog requests failed (about 19%) between 15:00 and 16:30, with a peak of 30.1% in the 15:50 five-minute window. The incident started at 14:58:36Z and lasted about 1h40m. It is resolved: the rate fell to 5.6% (6/108) at 16:30, 0.9% (1/113) at 16:35 and 0% from 16:40 to 19:00. The percentage is within the SEV-2 band, but SEV-2 requires a core flow, so SEV-3 applies.

## Impact
- Product search (`GET /search`) in catalog failed with HTTP 504 (most) and 503 (e.g. error_id 9). Roughly one in four search requests failed at the peak. Successful searches were slower (250-830ms at 16:25-16:28 versus about 45-100ms at baseline).
- Checkout, payment and auth: no errors in the window.
- Resolved: no errors from 16:40.
- Latency was not measured by triage. The 503/504 split over the whole window is not verified.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed, "add category index cache" | get_deployments |
| 11:15 | auth v3.1.1 deployed | get_deployments |
| 14:49 | Search latency starts rising (90-270ms) | search_logs |
| 14:50 | auth v3.2.0 deployed, "extend refresh token TTL to 30 days" | get_deployments |
| 14:52:31 | Pool warning in_use=16/20 | search_logs |
| 14:57:56 | Pool exhausted: in_use=20/20, waiting=2 | search_logs |
| 14:58:36 | First error, req-f1a04b122c, 504, ios-5.8.1 | error_id 5 |
| 15:03:00 | First 503, req-065fc3534a, web-2.30.4 | error_id 9 |
| 15:19 | Waiting count reaches 21 | search_logs |
| 15:50 | Peak: 30.1% (43/143) in the 5-minute window | get_error_stats |
| 16:00:00 | 504, web-2.30.4 | error_id 390 |
| 16:27:33 | Slow query: `SELECT * FROM products WHERE category_id = ?` took 1945ms | search_logs |
| 16:27:45 | 504, android-5.7.9 | error_id 551 |
| 16:30 | Error rate 5.6% (6/108) | get_error_stats |
| 16:35 | Error rate 0.9% (1/113) | get_error_stats |
| 16:40 | Error rate 0%, stays 0 until 19:00 | get_error_stats |

## Root cause
The catalog DB connection pool (pool_size=20) was exhausted by a surge of `GET /search` traffic that began around 14:45 UTC, so searches waited 5.0s for a connection and returned 504 or 503. No deployment or code change explains it.

**Confidence: medium.** Pool exhaustion is certain (identical stack traces, direct WARN lines). The traffic spike as the trigger is inferred from `/search` volume and latency; per-minute volume was not measured, and the catalog source is not in the repo, so the code could not be checked.

## Evidence
- Stack traces for error_id 5 (14:58:36, 504), 9 (15:03:00, 503), 390 (16:00:00, 504) and 551 (16:27:45, 504) all trace to `/app/catalog/search.py:71 search_products`, then `db_pool.py:138 connection`, then `db_pool.py:164 _acquire`, which raises `TimeoutError: timed out after 5.0s waiting for a connection from the pool (pool_size=20)`. The 503 and 504 are the same exception and trace.
- The samples come from ios, web and android clients, so one client is not the cause.
- Pool saturation built up before the first error: 16/20 at 14:52:31, 18/20 at 14:54:52, 19/20 at 14:56:21, exhausted at 14:57:56 (waiting=2), 40 seconds before error_id 5 (req-f1a04b122c). Waiting grew: 4 at 15:00, 7 at 15:02, 15 at 15:10, 20 at 15:14, 21 at 15:19. Exhaustion warnings were still present at 16:25-16:27 (in_use=20/20, waiting=10-11) together with error_ids 538-551.
- Search latency: about 40-120ms at 14:40-14:45, 90-270ms from 14:49, 150-190ms by 14:51.
- Search volume roughly doubled: 143 successful `/search` INFO lines at 14:00-14:30 versus 277 at 15:30-16:00 (plus failed searches that logged only ERROR). `/products` INFO lines fell from 458 to 368, so the extra load is on `/search`. This is a coarse 30-minute comparison.
- Catalog requests rose from 116 (14:45 window) to 160 (15:10) and 411-445 per 15 minutes, against about 270-330 at baseline.
- 555 ERROR lines were found in total from 14:30 to 17:00, consistent with triage's 553.

## Ruled out
- **auth v3.2.0 (14:50):** the nearest deployment to the first error, but auth logged no errors all day and no trace contains an auth exception. Timing coincidence.
- **catalog v2.8.0 (09:30):** 5.5 hours earlier; catalog was healthy at 14:30-14:45. It does not explain the onset at about 14:50.
- **auth v3.1.1 (11:15):** long before the incident and unrelated.
- **Checkout, payment and auth:** 0 failed requests in 14:45-17:00. The isolated earlier errors (catalog 03:40, payment 13:00-13:15) are not part of this pattern.
- **A single bad client version:** ios, web and android all fail the same way.
- **Pool misconfiguration or connection leak:** no deployment or log line shows a config change, and a leak would not recover by itself at 16:35. Not fully excluded: no pool config or metrics were available.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides.

### Patch
None. The catalog service code is not in this repository, so no code patch can be proposed. The only matches are the synthetic frames in `src/incident_agent/data/scenarios.py` (lines 114-116), which are generator strings, not real code. Config-level options (all assumptions, since the catalog config was not seen):
- Raise `pool_size` above 20, if the database can take more connections.
- Shorten the acquire timeout from 5.0s so requests fail fast, paired with load shedding.
- Rate-limit `GET /search`.
- Cache popular queries.
- Check the query `SELECT * FROM products WHERE category_id = ?` (1945ms at 16:27:33); it may be missing an index.

### Rollback
Not recommended. No deployment correlates with the onset at about 14:45 UTC. Candidates: catalog v2.8.0 (09:30), whose previous version number is unknown, and auth v3.2.0 (14:50) and v3.1.1 (11:15), which had no errors. A rollback would not fix the traffic surge or pool exhaustion under load, and rolling back catalog could remove the category cache and slow the service further.

### Risks
- A larger `pool_size` may push load onto the database and worsen the slow query.
- A shorter timeout or load shedding turns waits into more fast failures (more 503s).
- Rate limiting can reject legitimate clients.
- Not verified: the catalog source and config, the traffic source, what ended the incident at about 16:30-16:40, and whether a leak or config change is involved. Nothing was run or tested.

## Follow-up tasks
- [ ] Add an alert on the `connection pool usage high` WARN lines (they started at 14:52, about 6 minutes before the first error).
- [ ] Consider rate limiting on `/search`.
- [ ] Consider a larger pool or a shorter acquire timeout with load shedding.
- [ ] Consider caching popular search queries.
- [ ] Check the search handler for connection hold time and the pool settings (size 20, timeout 5s, no queue limit).

## Open questions
- What drove the `/search` increase (bot or crawler, marketing campaign, client retry loop) and why did it stop around 16:30-16:40? Check per-minute `/search` volume from 14:40 to 16:45 against the failure curve, and the load balancer or WAF for top clients or IPs on `/search`.
- Traffic or slow queries: these could not be separated (latency rose from 14:49; slow query of 1945ms at 16:27:33). Check the DB query plan and the cache or index state; the v2.8.0 category index cache could contribute if it was cold or evicted (hypothesis only).
- The catalog source (`/app/catalog/search.py`, `db_pool.py`) was not available, so nothing was checked in code.
- Gaps: error_ids 1-4 and the 04:15-13:00 period were not read; only the first 200 of 555 ERROR lines plus sampled error_ids were viewed, so the 503/504 split over the full window is unverified.
