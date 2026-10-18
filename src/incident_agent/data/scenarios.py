"""Incident scenarios. Each scenario mutates the normal-operation data.

To add a scenario, write a function with the `ScenarioFn` signature and register it in `SCENARIOS`.
"""

import random
from collections.abc import Callable
from datetime import datetime, timedelta

from incident_agent.data.constants import DAY_END, DAY_START, at
from incident_agent.data.frames import PAYMENT_HANDLE_CHARGE, PAYMENT_READ_CURRENCY
from incident_agent.data.models import CheckoutRequest, DeploymentRow, GeneratedData, LogRow
from incident_agent.data.normal import HOURLY_WEIGHT
from incident_agent.data.util import (
    CLIENT_VERSIONS,
    CLIENT_WEIGHTS,
    LEGACY_CLIENT_VERSIONS,
    Frame,
    add_error_log,
    make_error,
    new_request_id,
)

ScenarioFn = Callable[[random.Random, GeneratedData], None]

# checkout-500
DEPLOY_TIME = at(14, 1)
INCIDENT_START = at(14, 3)
# Legacy clients are 19% of traffic, so the overall failure rate is 0.19 * 0.95 = ~18%.
LEGACY_FAILURE_PROBABILITY = 0.95

KEYERROR_FRAMES: list[Frame] = [PAYMENT_HANDLE_CHARGE, PAYMENT_READ_CURRENCY]


def checkout_500(rng: random.Random, data: GeneratedData) -> None:
    """Payment v1.4.2 reads payload["currency"] without a default.

    Legacy clients do not send it: ~95% of their requests fail (~18% of all checkouts).
    """
    data.deployments.append(
        DeploymentRow(
            DEPLOY_TIME,
            "payment",
            "v1.4.2",
            "m.keller",
            "feat(payment): read currency from charge payload",
        )
    )
    failed: dict[str, CheckoutRequest] = {}
    for request in data.checkout_requests:
        if request.timestamp < INCIDENT_START:
            continue
        roll = rng.random()  # always drawn, so RNG use does not depend on the client
        if request.client_version in LEGACY_CLIENT_VERSIONS and roll < LEGACY_FAILURE_PROBABILITY:
            failed[request.request_id] = request

    # Payment crashed before charging, so the "charge ok" line of a failed request must not exist.
    data.logs = [
        log for log in data.logs if not (log.service == "payment" and log.request_id in failed)
    ]

    # One errors row per failed request, referenced by both the payment and the checkout ERROR line.
    error_ids: dict[str, int] = {}
    for request in failed.values():
        error = make_error(
            request.timestamp,
            "payment",
            "KeyError",
            "'currency'",
            KEYERROR_FRAMES,
            request.request_id,
            "POST /checkout",
            request.client_version,
        )
        error_ids[request.request_id] = add_error_log(
            data, error, "Unhandled exception in charge: KeyError: 'currency'"
        )

    last_second = DAY_END - timedelta(seconds=1)
    for log in data.logs:
        if log.service == "checkout" and log.request_id in failed:
            log.level = "ERROR"
            client = failed[log.request_id].client_version
            log.message = (
                f"POST /checkout 500 payment service error: KeyError: 'currency' client={client}"
            )
            log.error_id = error_ids[log.request_id]
            log.timestamp = min(log.timestamp + timedelta(seconds=1), last_second)


# db-pool
POOL_SIZE = 20
POOL_TIMEOUT_SECONDS = 5
# Search requests per hour at HOURLY_WEIGHT 1.0 (the normal catalog traffic has no GET /search).
SEARCH_PEAK_REQUESTS = 300
# Load multiplier of the search traffic (1.0 = normal). Minutes since midnight UTC.
PEAK_MULTIPLIER = 3.6
RAMP_START = 14 * 60 + 45
RAMP_END = 15 * 60 + 15
HOLD_END = 16 * 60 + 15
DECAY_END = 16 * 60 + 55
# Pool demand is 10 of 20 connections at normal load, so the pool is full at twice the normal load.
POOL_CAPACITY_MULTIPLIER = 2.0
WARN_FROM_MULTIPLIER = 1.6  # usage warnings start here, before the first timeout
MAX_FAILURE_PROBABILITY = 0.45  # at the peak; zero up to the pool capacity
SERVICE_UNAVAILABLE_SHARE = 0.15  # the other timeouts are returned as HTTP 504
SEARCH_TERMS = ("shoes", "laptop", "headphones", "jacket", "coffee", "backpack", "lamp", "desk")

# Harmless auth deployment about 10 minutes before the first timeout: the red herring.
AUTH_DEPLOY_TIME = at(14, 50)

# Synthetic frames: the demo app has no catalog code.
POOL_TIMEOUT_FRAMES: list[Frame] = [
    ("/app/catalog/search.py", 71, "search_products", "with db_pool.connection() as conn:"),
    ("/app/catalog/db_pool.py", 138, "connection", "conn = self._acquire(self.timeout)"),
    ("/app/catalog/db_pool.py", 164, "_acquire", "raise TimeoutError(msg)"),
]
POOL_TIMEOUT_MESSAGE = (
    f"timed out after {POOL_TIMEOUT_SECONDS}.0s waiting for a connection from the pool "
    f"(pool_size={POOL_SIZE})"
)

# Scenario name -> services whose shared one-off error is left out (auth is error-free in db-pool).
NOISE_SKIP_ONE_OFFS: dict[str, frozenset[str]] = {"db-pool": frozenset({"auth"})}


def load_multiplier(minute: float) -> float:
    """Search traffic relative to normal at `minute` (since midnight UTC). Only + - * / are used."""
    extra = PEAK_MULTIPLIER - 1.0
    if minute <= RAMP_START or minute >= DECAY_END:
        return 1.0
    if minute < RAMP_END:
        return 1.0 + extra * (minute - RAMP_START) / (RAMP_END - RAMP_START)
    if minute <= HOLD_END:
        return PEAK_MULTIPLIER
    return PEAK_MULTIPLIER - extra * (minute - HOLD_END) / (DECAY_END - HOLD_END)


def failure_probability(multiplier: float) -> float:
    """No timeouts up to the pool capacity, then rising linearly with the load."""
    if multiplier <= POOL_CAPACITY_MULTIPLIER:
        return 0.0
    share = (multiplier - POOL_CAPACITY_MULTIPLIER) / (PEAK_MULTIPLIER - POOL_CAPACITY_MULTIPLIER)
    return MAX_FAILURE_PROBABILITY * share


def _minute_of(moment: datetime) -> float:
    return (moment - DAY_START).total_seconds() / 60


def _search_times(stream: random.Random, spike: bool) -> list[datetime]:
    """Request times of the baseline (spike=False) or of the extra spike traffic (spike=True)."""
    times: list[datetime] = []
    for minute in range(24 * 60):
        scale = load_multiplier(minute + 0.5) - 1.0 if spike else 1.0
        expected = SEARCH_PEAK_REQUESTS * HOURLY_WEIGHT[minute // 60] / 60 * scale
        count = int(expected) + (1 if stream.random() < expected - int(expected) else 0)
        for _ in range(count):
            times.append(DAY_START + timedelta(minutes=minute, seconds=stream.randrange(60)))
    return times


def db_pool(rng: random.Random, data: GeneratedData) -> None:
    """A traffic spike on catalog GET /search exhausts the connection pool (size 20).

    The scenario runs after normal operation and noise and draws only from streams derived from
    `rng`, so it cannot change any value of another scenario.
    """
    base_stream, extra_stream, outcome_stream, warn_stream = (
        random.Random(rng.getrandbits(64)) for _ in range(4)
    )
    data.deployments.append(
        DeploymentRow(
            AUTH_DEPLOY_TIME,
            "auth",
            "v3.2.0",
            "a.rossi",
            "chore(auth): extend refresh token TTL to 30 days",
        )
    )

    times = sorted(_search_times(base_stream, False) + _search_times(extra_stream, True))
    for ts in times:
        multiplier = load_multiplier(_minute_of(ts))
        # Every random value is drawn for every request, so the stream does not depend on outcomes.
        request_id = new_request_id(outcome_stream)
        client = outcome_stream.choices(CLIENT_VERSIONS, weights=CLIENT_WEIGHTS)[0]
        term = outcome_stream.choice(SEARCH_TERMS)
        fail_roll = outcome_stream.random()
        status_roll = outcome_stream.random()
        base_latency = outcome_stream.randint(40, 120)
        wait_roll = outcome_stream.random()
        if fail_roll < failure_probability(multiplier):
            status = 503 if status_roll < SERVICE_UNAVAILABLE_SHARE else 504
            error = make_error(
                ts + timedelta(seconds=POOL_TIMEOUT_SECONDS),  # logged when the wait runs out
                "catalog",
                "TimeoutError",
                POOL_TIMEOUT_MESSAGE,
                POOL_TIMEOUT_FRAMES,
                request_id,
                "GET /search",
                client,
                status,
            )
            add_error_log(data, error, f"GET /search {status} TimeoutError: {POOL_TIMEOUT_MESSAGE}")
            continue
        # Successful requests slow down while connections are scarce.
        pressure = min(1.0, (multiplier - 1.0) / (PEAK_MULTIPLIER - 1.0))
        latency = base_latency + int(pressure * wait_roll * 1000)
        message = f"GET /search?q={term} 200 {latency}ms"
        data.logs.append(LogRow(ts, "catalog", "INFO", message, request_id))

    _add_pool_warnings(warn_stream, data)


def _add_pool_warnings(stream: random.Random, data: GeneratedData) -> None:
    """About one pool WARN per minute while the pool is nearly or completely in use."""
    back_to_normal_logged = False
    for minute in range(RAMP_START, DECAY_END + 1):
        ts = DAY_START + timedelta(minutes=minute, seconds=stream.randrange(60))
        multiplier = load_multiplier(_minute_of(ts))
        in_use = min(POOL_SIZE, int(POOL_SIZE * multiplier / POOL_CAPACITY_MULTIPLIER))
        waiting = int(max(0.0, multiplier - POOL_CAPACITY_MULTIPLIER) * 12) + stream.randrange(3)
        if multiplier >= POOL_CAPACITY_MULTIPLIER:
            message = f"connection pool exhausted: in_use={in_use}/{POOL_SIZE}, waiting={waiting}"
        elif multiplier >= WARN_FROM_MULTIPLIER:
            message = f"connection pool usage high: in_use={in_use}/{POOL_SIZE}, waiting=0"
        elif minute > HOLD_END and not back_to_normal_logged:
            back_to_normal_logged = True
            message = f"connection pool back to normal: in_use={in_use}/{POOL_SIZE}, waiting=0"
        else:
            continue
        data.logs.append(LogRow(ts, "catalog", "WARN", message))


# cert-expired
# The server certificate of payment (CN=payment.internal) used by checkout expires at one instant.
EXPIRY_TIME = at(15, 20)
# About 2% of the requests still ride on a keep-alive connection opened before the expiry.
CERT_FAILURE_PROBABILITY = 0.98
CERT_HTTP_STATUS = 502
CERT_NOT_AFTER = "2026-03-12T15:20:00Z"
CERT_ERROR_TYPE = "SSLCertVerificationError"
CERT_ERROR_MESSAGE = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired "
    f"(_ssl.c:1006); payment.internal:8443 notAfter={CERT_NOT_AFTER}"
)

# Harmless auth deployment 8 minutes before the expiry: the red herring.
KEY_ROTATION_DEPLOY_TIME = at(15, 12)

# Synthetic frames: the demo app has no TLS code.
CERT_EXPIRED_FRAMES: list[Frame] = [
    ("/app/checkout/handler.py", 52, "handle_checkout", "charge = self.payment.charge(payload)"),
    ("/app/checkout/payment_client.py", 38, "charge", "conn.connect()"),
    ("/usr/local/lib/python3.12/ssl.py", 1104, "do_handshake", "self._sslobj.do_handshake()"),
]


def cert_expired(rng: random.Random, data: GeneratedData) -> None:
    """From 15:20:00 the TLS handshake of checkout -> payment fails for ~98% of the requests.

    Every client version is affected equally. The scenario draws only from streams derived from
    `rng`, so it cannot change any value of another scenario.
    """
    outcome_stream = random.Random(rng.getrandbits(64))
    data.deployments.append(
        DeploymentRow(
            KEY_ROTATION_DEPLOY_TIME,
            "auth",
            "v3.3.0",
            "a.rossi",
            "chore(auth): rotate JWT signing keys",
        )
    )
    failed: dict[str, CheckoutRequest] = {}
    for request in data.checkout_requests:
        roll = outcome_stream.random()  # drawn for every request, whatever the outcome
        if request.timestamp >= EXPIRY_TIME and roll < CERT_FAILURE_PROBABILITY:
            failed[request.request_id] = request

    # The handshake failed, so payment never saw the request: no "charge ok" line.
    data.logs = [
        log for log in data.logs if not (log.service == "payment" and log.request_id in failed)
    ]
    error_ids: dict[str, int] = {}
    for request in failed.values():
        error = make_error(
            request.timestamp,
            "checkout",
            CERT_ERROR_TYPE,
            CERT_ERROR_MESSAGE,
            CERT_EXPIRED_FRAMES,
            request.request_id,
            "POST /checkout",
            request.client_version,
            CERT_HTTP_STATUS,
        )
        # The checkout INFO line of the request is turned into this ERROR line below.
        data.add_error(error)
        error_ids[request.request_id] = len(data.errors)
    for log in data.logs:
        if log.service == "checkout" and log.request_id in failed:
            client = failed[log.request_id].client_version
            log.level = "ERROR"
            log.message = (
                f"POST /checkout {CERT_HTTP_STATUS} payment unreachable: "
                f"{CERT_ERROR_TYPE}: certificate has expired (notAfter={CERT_NOT_AFTER}) "
                f"client={client}"
            )
            log.error_id = error_ids[log.request_id]


SCENARIOS: dict[str, ScenarioFn] = {
    "checkout-500": checkout_500,
    "db-pool": db_pool,
    "cert-expired": cert_expired,
}
