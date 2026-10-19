# Catalog restarts every three hours: memory leak since catalog v2.9.0

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (4 stack-trace samples examined: error_id 6, 34, 70, 95). fix-proposer read no source: the catalog code is not in this repository.

## Summary
Since 13:16 UTC the `catalog` container was killed by the platform four times (13:16, 16:14, 19:12, 22:10) because it hit its 512 MiB memory limit (`OOMKilled`). Each restart takes 120 seconds and every catalog request in that time fails with a 503, 102 requests in total. The memory of catalog has risen in a straight line since the deployment of catalog v2.9.0 at 10:20 and is still rising: the next kill is due around 01:08.

## Severity
**SEV-3 (Minor).** `catalog` is a non-core service and it is degraded: 102 of 10,076 catalog requests (1.0%) since 10:20 failed, in four outages of 2 minutes each (8 minutes in total). Checkout and payment are not affected (no new error since 13:15), so the SEV-1 and SEV-2 criteria for core flows do not match. A workaround exists: the requests succeed again after about 2 minutes. The incident is not resolved: memory is at 381 MiB (74%) at 23:55 and rising, so the same outage will repeat. Because no core flow is involved, that does not raise the level.

## Impact
- `GET /products/<id>` failed with HTTP 503 (`ConnectionRefusedError: app container of catalog is restarting`) for 28 requests at 13:16, 36 at 16:14, 25 at 19:12 and 13 at 22:10: 102 in total. In the five-minute window 13:15 to 13:20, 28 of 73 catalog requests (38%) failed.
- Every catalog request that arrived during a restart failed; none outside a restart did (apart from one unrelated `IndexError` at 12:26).
- Checkout, payment and auth: no new errors. Their memory is flat (auth 115 to 121 MiB, payment 257 to 263 MiB).
- Ongoing: the leak continues at the end of the data.

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed, "add category index cache"; memory stays flat (about 205 MiB) | get_deployments, search_logs |
| 10:20 | catalog v2.9.0 deployed, "add product comparison endpoint"; memory starts to rise | get_deployments, search_logs |
| 12:35 | First `memory usage high` warning, rss=440MiB (85%) | search_logs |
| 13:04 | auth v3.4.0 deployed, "cache validated sessions in memory"; auth memory stays flat | get_deployments, search_logs |
| 13:16:00 | catalog killed: OOMKilled, rss=512MiB; first failed request 13:16:01 | search_logs, error_id 6 |
| 13:18:00 | restart 1 ready, rss back to about 204 MiB | search_logs |
| 16:14:00 | OOMKilled again, 176 minutes after the restart; first failure error_id 34 | search_logs |
| 19:12:00 | OOMKilled (third), first failure error_id 70 | search_logs |
| 22:10:00 | OOMKilled (fourth), first failure error_id 95 | search_logs |
| 23:55:15 | rss=381MiB (74%), still rising | search_logs |

## Root cause
Catalog v2.9.0, deployed at 10:20 UTC, leaks memory: the resident memory of the container grows by about 1.75 MiB per minute until it reaches the 512 MiB limit, the platform kills it (OOMKilled) and every request fails while it restarts.

**Confidence: high.** The memory of catalog is flat at about 205 MiB until 10:20 and rises in a straight line after it, the same slope after every restart (176 minutes from restart to kill), and the first kill at 13:16 minus those 176 minutes is exactly the deployment time of 10:20.

## Evidence
- OOMKilled lines (search_logs, `OOMKilled`): 13:16:00, 16:14:00, 19:12:00, 22:10:00, each `container killed by the platform: OOMKilled (rss=512MiB limit=512MiB exit_code=137)`, followed 120 seconds later by `catalog v2.9.0 started (restart N after OOMKilled)`.
- Memory of catalog (search_logs, `memory usage`, service catalog): 202 MiB at 10:00, 207 at 10:20, 237 at 10:40, 272 at 11:00, 379 at 12:00, 440 (85%, first warning) at 12:35, 481 at 13:00, 507 at 13:15, 204 at 13:20 after the restart, 381 (74%) at 23:55. The slope is the same in all four cycles.
- Control group: auth (115 to 121 MiB, also after its deployment at 13:04) and payment (257 to 263 MiB) are flat all day.
- error_id 6 (13:16:01, req-ec8773ae8a), 34 (16:14:04, req-61558d4025), 70 (19:12:03, req-f9ddee6a13) and 95 (22:10:10, req-e3b285890e): `ConnectionRefusedError: [Errno 111] Connection refused: app container of catalog is restarting`, HTTP 503, raised in `/app/catalog/ingress.py`.
- Failed requests: 28 (13:16 to 13:18), 36 (16:14 to 16:16), 25 (19:12 to 19:14) and 13 (22:10 to 22:12). The total of failed requests is 102 (search_logs on catalog ERROR lines with "restarting"); there are 4 restarts. All catalog ERROR lines are 108, because they also contain the four kill lines and two one-off errors.

## Ruled out
- **auth v3.4.0 (13:04):** deployed 12 minutes before the first kill and described as an in-memory cache, so a plausible suspect. Auth memory is flat before and after it, auth has no error, and the container that was killed is catalog's. The later kills happen three hours after the last deployment of the day.
- **catalog v2.8.0 (09:30):** also a cache. Catalog memory stays flat at about 205 MiB for the 50 minutes after it; the rise starts at 10:20.
- **auth v3.1.1 (11:15):** memory of auth is flat.
- **A traffic spike:** the fourth kill happens at 22:10, when traffic is low, with the same 176-minute cadence as in the busy hours.
- **The one-off errors** (catalog `IndexError` at 12:26, payment timeout at 13:12): single, and neither mentions memory or a restart.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides.

### Patch
None in code. The catalog source is not in this repository, so no file could be examined. Once the code is available, look for what v2.9.0 adds (the comparison endpoint) that keeps objects alive, for example an unbounded cache or list.

### Rollback
Roll `catalog` back from v2.9.0 to v2.8.0 (the previous version, memory flat). This removes the leak if the leak is in v2.9.0 and it is the only change in it. Until then, restart catalog before it reaches 512 MiB (about every two and a half hours) so that the kill does not drop requests.

### Risks
- Rolling back removes the comparison endpoint.
- Not verified: the cause inside the v2.9.0 code, and whether v2.8.0 stays flat under load. Nothing was run or tested.

## Follow-up tasks
- [ ] Find the allocation in v2.9.0 that is never released and fix it, with a test that watches memory.
- [ ] Alert when the memory of a service passes 80% of its limit (the first warning was at 12:35, 40 minutes before the first kill).
- [ ] Alert on `OOMKilled` events.

## Open questions
- Which object in v2.9.0 grows without limit?
- Is the leak per request or per time? The constant slope across busy and quiet hours points to time.
- Will the next kill, due around 01:08, happen before the rollback?
