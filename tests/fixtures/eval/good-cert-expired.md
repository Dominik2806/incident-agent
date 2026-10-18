# Checkout down: expired TLS certificate of payment.internal

- **Incident date (UTC):** 2026-03-12
- **Status:** Proposed actions only, nothing applied
- **Sources:** get_error_stats, search_logs, get_deployments, get_stacktrace (3 stack-trace samples examined: error_id 6, 7, 1966). fix-proposer read no source: the TLS client and certificate handling are not in this repository.

## Summary
Since 15:20:05 UTC almost every `POST /checkout` fails with HTTP 502. The TLS server certificate of `payment.internal`, which checkout uses to call payment, expired at 15:20:00 UTC (`notAfter=2026-03-12T15:20:00Z`), so the handshake fails with `certificate has expired`. The failure is ongoing and has no relation to a deployment.

## Severity
**SEV-1 (Critical).** Checkout is a core flow and it is effectively unavailable: 1,961 of 2,005 checkout requests failed (97.8%) between 15:20:05 and 23:59:44 UTC, and the last window still fails (66 of 70 requests between 23:00 and 24:00). More than 50% of the requests of a core flow fail, so SEV-1 applies; the SEV-2 band (10-50%) is exceeded. The incident is ongoing and no customer can reliably pay.

## Impact
- `POST /checkout` fails for every client version: web-2.31.0, ios-5.8.1, web-2.30.4 and android-5.7.9 all show the same error, so no client is spared and none is the cause.
- About 2% of the requests succeeded (44 of 2,005), most likely on keep-alive connections that were opened before the expiry. Payment received no other traffic: its `charge ok` lines stop at 15:19:57 except for those 44.
- Payment itself logs no error; the failure happens in the TLS handshake before payment sees the request.
- Catalog and auth are not affected.
- Ongoing at the end of the data (23:59:44).

## Timeline (UTC)

| Time (UTC) | Event | Source |
|---|---|---|
| 09:30 | catalog v2.8.0 deployed | get_deployments |
| 11:15 | auth v3.1.1 deployed | get_deployments |
| 15:12 | auth v3.3.0 deployed, "rotate JWT signing keys" | get_deployments |
| 15:19:57 | Last successful checkout before the incident (HTTP 200) | search_logs |
| 15:20:00 | Certificate `notAfter` of payment.internal:8443 | error_id 6 message |
| 15:20:05 | First failure, req-037b4bbf27, web-2.31.0, HTTP 502 | error_id 6 |
| 15:20 to 15:30 | 42 of 42 checkout requests fail | search_logs |
| 23:59:44 | Last failure in the data, req-e14ddc86ed | error_id 1966 |

## Root cause
The TLS certificate of `payment.internal` that checkout uses to call payment expired at 15:20:00 UTC (`notAfter=2026-03-12T15:20:00Z`), so the TLS handshake fails with `SSLCertVerificationError: certificate verify failed: certificate has expired` and checkout returns 502.

**Confidence: high.** Every sampled stack trace ends in `ssl.py do_handshake` with the same message and the same `notAfter`, the first failure is 5 seconds after that time, and the failure rate steps from 0% to about 100% at once.

## Evidence
- error_id 6 (15:20:05, req-037b4bbf27), error_id 7 (15:20:24, req-996f7369a2) and error_id 1966 (23:59:44, req-e14ddc86ed) have the same trace: `/app/checkout/handler.py:52 handle_checkout`, then `/app/checkout/payment_client.py:38 charge`, then `ssl.py:1104 do_handshake`, then `SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1006); payment.internal:8443 notAfter=2026-03-12T15:20:00Z`.
- No checkout error between 10:48 (a one-off connection reset) and 15:20:05. 91 checkout requests between 15:00 and 15:20, none failed.
- Step, not a ramp: 42 of 42 requests fail in 15:20-15:30, 143 of 145 in 15:30-16:00.
- The affected clients are web-2.31.0, ios-5.8.1, web-2.30.4 and android-5.7.9; the request context of each sampled error shows the same endpoint and status 502.
- Payment: 0 errors since 14:00. Auth: 0 errors since 14:00 and normal volume (351 token validations in the 14:00 hour, 362 at 15:00, 370 at 16:00).
- The total of failed requests is 1,961 certificate errors between 15:20:05 and 23:59:44.

## Ruled out
- **auth v3.3.0 (15:12):** the closest deployment, 8 minutes before the first error. Auth logs no error all day, token validations continue at the normal rate, and no trace contains an auth frame. Timing coincidence.
- **catalog v2.8.0 (09:30) and auth v3.1.1 (11:15):** hours earlier, and checkout was healthy until 15:19:57.
- **A bad client version:** all four client versions fail the same way.
- **Payment application bug:** payment logs no error; the request never reaches it.
- **Network outage between checkout and payment:** the error is a certificate verification failure with an explicit `notAfter`, not a timeout or reset.

## Immediate actions (proposed)
Nothing has been applied, edited or executed. The human decides.

### Patch
None in code. The TLS client and the certificate are not in this repository. Proposed operational fix: issue a new certificate for `payment.internal`, install it on the payment service, reload it and check a handshake from checkout.

### Rollback
Not applicable. No deployment correlates with the onset, and rolling back auth v3.3.0 or any other service would not renew the certificate.

### Risks
- A replaced certificate must chain to a CA that checkout trusts, and the name must match `payment.internal`, or the handshake still fails.
- Not verified: who issues and renews this certificate, whether other services share it, and whether other certificates expire soon. Nothing was run or tested.

## Follow-up tasks
- [ ] Add an alert on certificates that expire within 30 days.
- [ ] Automate certificate renewal and reload.
- [ ] Inventory all internal TLS certificates and their `notAfter` dates.

## Open questions
- Why was the certificate not renewed, and was there an expiry warning that was ignored?
- Do other internal certificates expire at the same time?
- Why did 44 requests succeed after the expiry (keep-alive connections are the likely, unverified explanation)?
