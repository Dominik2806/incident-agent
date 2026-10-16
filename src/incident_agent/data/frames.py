"""Stack frames of the payment and checkout services, pointing at the real demo app.

Every frame is (repo-relative path, line, function, source line). The lines are real: they must
stay in sync with `demo_app/`, and `tests/test_demo_app.py` checks that. Frames of `catalog` and
`auth` stay synthetic (`/app/...` paths) because the demo app has no code for those services;
they live in `noise.py`.
"""

from incident_agent.data.util import Frame

PAYMENT_HANDLE_CHARGE: Frame = (
    "demo_app/payment/server.py",
    11,
    "handle_charge",
    "result = process_charge(payload)",
)
PAYMENT_READ_CURRENCY: Frame = (
    "demo_app/payment/charge.py",
    13,
    "process_charge",
    'currency = payload["currency"]',
)
PAYMENT_GATEWAY_CALL: Frame = (
    "demo_app/payment/charge.py",
    17,
    "process_charge",
    "authorization = gateway.authorize(card, amount, simulate_timeout=simulate_timeout)",
)
PAYMENT_GATEWAY_TIMEOUT: Frame = (
    "demo_app/payment/gateway.py",
    13,
    "authorize",
    "raise TimeoutError(msg)",
)
CHECKOUT_CALL_PAYMENT: Frame = (
    "demo_app/checkout/server.py",
    25,
    "handle_checkout",
    "charge = _call_payment(payload)",
)
CHECKOUT_SEND: Frame = (
    "demo_app/checkout/server.py",
    19,
    "_call_payment",
    "return payment_server.handle_charge(payload)",
)
