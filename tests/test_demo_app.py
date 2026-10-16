import re
import sqlite3
import traceback
from pathlib import Path

import pytest

from demo_app.checkout.server import handle_checkout
from demo_app.payment import __version__
from demo_app.payment.server import handle_charge
from incident_agent.data.scenarios import KEYERROR_FRAMES

ROOT = Path(__file__).resolve().parent.parent
FRAME_RE = re.compile(r'^  File "([^"]+)", line (\d+), in (\S+)$')

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


def parse_frames(stacktrace: str) -> list[tuple[str, int, str, str]]:
    lines = stacktrace.splitlines()
    frames = []
    for i, line in enumerate(lines):
        match = FRAME_RE.match(line)
        if match:
            frames.append((match[1], int(match[2]), match[3], lines[i + 1].strip()))
    return frames


def test_generated_frames_point_to_real_demo_app_lines(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT service, stacktrace FROM errors WHERE service IN ('payment', 'checkout')"
    ).fetchall()
    assert rows
    seen: set[str] = set()
    for _service, stacktrace in rows:
        frames = parse_frames(stacktrace)
        assert frames
        for path, line, function, source in frames:
            assert path.startswith("demo_app/") and "\\" not in path, path
            file = ROOT / path
            assert file.is_file(), f"{path} does not exist"
            text = file.read_text(encoding="utf-8").splitlines()
            assert 1 <= line <= len(text), f"{path}:{line} is out of range"
            assert source in text[line - 1], f"{path}:{line} is {text[line - 1]!r}, not {source!r}"
            enclosing = [t for t in text[:line] if t.lstrip().startswith("def ")]
            assert f"def {function}(" in enclosing[-1], f"{path}:{line} is not in {function}"
            seen.add(path)
    assert {"demo_app/payment/charge.py", "demo_app/payment/server.py"} <= seen
    assert 'payload["currency"]' in (ROOT / "demo_app/payment/charge.py").read_text("utf-8")


def test_keyerror_frames_match_the_real_traceback() -> None:
    with pytest.raises(KeyError) as info:
        handle_charge(LEGACY)
    real = [
        (Path(f.filename).resolve().relative_to(ROOT).as_posix(), f.lineno, f.name)
        for f in traceback.extract_tb(info.value.__traceback__)
    ]
    assert real[-len(KEYERROR_FRAMES) :] == [(p, n, fn) for p, n, fn, _ in KEYERROR_FRAMES]
