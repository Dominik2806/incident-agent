"""Request handler of the checkout service (POST /checkout)."""

from dataclasses import dataclass

from demo_app.payment import server as payment_server

# NOTE: other code depends on the line numbers in this file.
# Do not move lines here without updating that code.


@dataclass(frozen=True)
class Response:
    status: int
    body: str


def _call_payment(payload: dict) -> dict:
    """Call the payment service (in-process in this demo)."""
    return payment_server.handle_charge(payload)


def handle_checkout(payload: dict) -> Response:
    """Charge the customer. Payment failures are relayed as HTTP 500."""
    try:
        charge = _call_payment(payload)
    except Exception as exc:
        return Response(500, f"payment service error: {type(exc).__name__}: {exc}")
    return Response(200, f"order paid: {charge['authorization']}")
