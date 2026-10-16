"""Noise that is not related to the incident: harmless WARNs, old deployments, one-off errors."""

import random
from datetime import datetime, timedelta

from incident_agent.data.constants import DAY_START, at
from incident_agent.data.frames import (
    CHECKOUT_CALL_PAYMENT,
    CHECKOUT_SEND,
    PAYMENT_GATEWAY_CALL,
    PAYMENT_GATEWAY_TIMEOUT,
)
from incident_agent.data.models import DeploymentRow, GeneratedData, LogRow
from incident_agent.data.util import (
    CLIENT_VERSIONS,
    Frame,
    add_error_log,
    make_error,
    new_request_id,
)

# catalog and auth frames are synthetic (no code in the repo); payment and checkout use frames.py.
# (time, service, exception type, message, endpoint, http status, stack frames); none is a KeyError.
OneOff = tuple[datetime, str, str, str, str, int, list[Frame]]

ONE_OFF_ERRORS: list[OneOff] = [
    (
        at(3, 42, 11), "catalog", "TimeoutError", "search backend did not answer within 2.0s",
        "GET /search", 504,
        [
            ("/app/catalog/search.py", 57, "search_products", "hits = backend.query(term, 2.0)"),
            ("/app/catalog/backend.py", 112, "query", "raise TimeoutError(msg)"),
        ],
    ),
    (
        at(8, 17, 40), "auth", "ValueError", "malformed token: expected 3 segments, got 2",
        "POST /auth/validate", 401,
        [
            ("/app/auth/tokens.py", 33, "validate", "header, body, sig = token.split('.')"),
        ],
    ),
    (
        at(10, 48, 5), "checkout", "ConnectionResetError", "[Errno 104] Connection reset by peer",
        "POST /checkout", 502,
        [
            CHECKOUT_CALL_PAYMENT,
            CHECKOUT_SEND,
        ],
    ),
    (
        at(12, 26, 52), "catalog", "IndexError", "list index out of range",
        "GET /products/related", 500,
        [
            ("/app/catalog/related.py", 44, "related_products", "first = candidates[0]"),
        ],
    ),
    (
        at(13, 12, 3), "payment", "TimeoutError", "card gateway timeout after 5.0s",
        "POST /charge", 504,
        [
            PAYMENT_GATEWAY_CALL,
            PAYMENT_GATEWAY_TIMEOUT,
        ],
    ),
]  # fmt: skip


def add_noise(rng: random.Random, data: GeneratedData) -> None:
    _add_recurring_warnings(rng, data)
    _add_old_deployments(data)
    _add_one_off_errors(rng, data)


def _add_recurring_warnings(rng: random.Random, data: GeneratedData) -> None:
    # Slow catalog query roughly every 10 minutes, auth token refresh retry every 15 minutes,
    # and a few payment gateway retries (they succeed, nothing is lost).
    for minute in range(0, 24 * 60, 10):
        ts = DAY_START + timedelta(minutes=minute + rng.randrange(10), seconds=rng.randrange(60))
        took = rng.randint(900, 2500)
        message = f"slow query: SELECT * FROM products WHERE category_id = ? took {took}ms"
        data.logs.append(LogRow(ts, "catalog", "WARN", message))
    for minute in range(0, 24 * 60, 15):
        ts = DAY_START + timedelta(minutes=minute + rng.randrange(15), seconds=rng.randrange(60))
        message = f"token refresh failed, retrying (attempt {rng.randint(1, 2)}/3)"
        data.logs.append(LogRow(ts, "auth", "WARN", message))
    for _ in range(8):
        ts = DAY_START + timedelta(seconds=rng.randrange(24 * 3600))
        message = "gateway request timed out, retrying (attempt 1/3)"
        data.logs.append(LogRow(ts, "payment", "WARN", message))


def _add_old_deployments(data: GeneratedData) -> None:
    data.deployments.append(
        DeploymentRow(
            at(9, 30), "catalog", "v2.8.0", "j.novak", "perf(catalog): add category index cache"
        )
    )
    data.deployments.append(
        DeploymentRow(
            at(11, 15), "auth", "v3.1.1", "a.rossi", "fix(auth): shorten session refresh interval"
        )
    )


def _add_one_off_errors(rng: random.Random, data: GeneratedData) -> None:
    for ts, service, exc_type, message, endpoint, status, frames in ONE_OFF_ERRORS:
        client = rng.choice(CLIENT_VERSIONS)
        error = make_error(
            ts, service, exc_type, message, frames, new_request_id(rng), endpoint, client, status
        )
        log_message = f"Unhandled exception: {exc_type}: {message}"
        if service == "checkout":  # every checkout log line carries the client
            log_message += f" client={client}"
        add_error_log(data, error, log_message)
