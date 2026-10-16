"""Card gateway client. The real call is simulated; it can time out."""

# NOTE: the log generator (src/incident_agent/data/frames.py) and tests/test_demo_app.py
# depend on the line numbers in this file. Change them together.

GATEWAY_TIMEOUT_SECONDS = 5.0


def authorize(card: str, amount: float, *, simulate_timeout: bool = False) -> str:
    """Authorize `amount` on `card` and return an authorization id."""
    if simulate_timeout:
        msg = f"card gateway timeout after {GATEWAY_TIMEOUT_SECONDS}s"
        raise TimeoutError(msg)
    return f"auth-{abs(hash((card, amount))) % 10**8:08d}"
