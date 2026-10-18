"""Request handler of the payment service (POST /charge)."""

from demo_app.payment.charge import process_charge

# NOTE: other code depends on the line numbers in this file.
# Do not move lines here without updating that code.


def handle_charge(payload: dict) -> dict:
    """Handle a charge request. Unhandled errors propagate to the caller."""
    result = process_charge(payload)
    return {"status": "ok", **result}
