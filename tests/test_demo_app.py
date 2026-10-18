import pytest

from demo_app.checkout.server import handle_checkout
from demo_app.payment import __version__

VALID = {"card": "tok_visa", "amount": 12.5, "currency": "EUR"}
LEGACY = {"card": "tok_visa", "amount": 12.5}  # older clients do not send currency


def test_payment_version() -> None:
    assert __version__ == "1.4.2"


def test_checkout_with_currency_returns_200() -> None:
    response = handle_checkout(VALID)
    assert response.status == 200


def test_gateway_timeout_is_relayed_as_500() -> None:
    response = handle_checkout({**VALID, "simulate_gateway_timeout": True})
    assert response.status == 500
    assert response.body.startswith("payment service error: TimeoutError")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Known bug, intentionally never fixed on main: payment v1.4.2 reads payload['currency'] "
        "without a default, so requests from older clients end in KeyError and HTTP 500. "
        "Fix it with a default currency or a 4xx validation error, then remove this marker."
    ),
)
def test_checkout_without_currency_must_not_fail_with_500() -> None:
    response = handle_checkout(LEGACY)
    assert response.status < 500
    assert "KeyError" not in response.body
