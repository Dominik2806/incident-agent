"""Incident scenarios. Each scenario mutates the normal-operation data.

To add a scenario, write a function with the `ScenarioFn` signature and register it in `SCENARIOS`.
"""

import random
from collections.abc import Callable
from datetime import timedelta

from incident_agent.data.constants import DAY_END, at
from incident_agent.data.frames import PAYMENT_HANDLE_CHARGE, PAYMENT_READ_CURRENCY
from incident_agent.data.models import CheckoutRequest, DeploymentRow, GeneratedData
from incident_agent.data.util import LEGACY_CLIENT_VERSIONS, Frame, add_error_log, make_error

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


SCENARIOS: dict[str, ScenarioFn] = {
    "checkout-500": checkout_500,
}
