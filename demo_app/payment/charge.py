"""Charge processing of the payment service."""

from demo_app.payment import gateway

# NOTE: the log generator (src/incident_agent/data/frames.py) and tests/test_demo_app.py
# depend on the line numbers in this file. Change them together.


def process_charge(payload: dict) -> dict:
    """Authorize a charge described by `payload` and return a receipt."""
    # Older clients do not send "currency". Since v1.4.2 it is read without a default.
    # This is the intentional bug of the demo; it is never fixed on main.
    currency = payload["currency"]
    card = payload["card"]
    amount = float(payload["amount"])
    simulate_timeout = bool(payload.get("simulate_gateway_timeout", False))
    authorization = gateway.authorize(card, amount, simulate_timeout=simulate_timeout)
    return {"authorization": authorization, "amount": amount, "currency": currency}
