"""Request handler of the payment service (POST /charge)."""

from demo_app.payment.charge import process_charge

# NOTE: the log generator (src/incident_agent/data/frames.py) and tests/test_demo_app.py
# depend on the line numbers in this file. Change them together.


def handle_charge(payload: dict) -> dict:
    """Handle a charge request. Unhandled errors propagate to the caller."""
    result = process_charge(payload)
    return {"status": "ok", **result}
