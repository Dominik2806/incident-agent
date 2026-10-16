"""Incident scenarios. Each scenario mutates the normal-operation data.

To add a scenario, write a function with the `ScenarioFn` signature and register it in `SCENARIOS`.
"""

import random
from collections.abc import Callable
from datetime import timedelta

from incident_agent.data.constants import DAY_END, at
from incident_agent.data.models import DeploymentRow, GeneratedData
from incident_agent.data.util import Frame, add_error_log, make_error

ScenarioFn = Callable[[random.Random, GeneratedData], None]

# checkout-500
DEPLOY_TIME = at(14, 1)
INCIDENT_START = at(14, 3)
FAILURE_RATE = 0.18

KEYERROR_FRAMES: list[Frame] = [
    ("/app/payment/server.py", 88, "handle_charge", "result = process_charge(payload)"),
    ("/app/payment/charge.py", 41, "process_charge", 'currency = payload["currency"]'),
]


def checkout_500(rng: random.Random, data: GeneratedData) -> None:
    """Payment v1.4.2 reads payload["currency"] without a default; ~18% of checkouts fail."""
    data.deployments.append(
        DeploymentRow(
            DEPLOY_TIME,
            "payment",
            "v1.4.2",
            "m.keller",
            "feat(payment): read currency from charge payload",
        )
    )
    failed = {
        r.request_id: r
        for r in data.checkout_requests
        if r.timestamp >= INCIDENT_START and rng.random() < FAILURE_RATE
    }

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
            log.message = "POST /checkout 500 payment service error: KeyError: 'currency'"
            log.error_id = error_ids[log.request_id]
            log.timestamp = min(log.timestamp + timedelta(seconds=1), last_second)


SCENARIOS: dict[str, ScenarioFn] = {
    "checkout-500": checkout_500,
}
