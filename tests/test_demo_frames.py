"""The generator's stack frames must point at real lines of demo_app/.

Split from test_demo_app.py: that file is copied into the sandbox of a live evaluation run and must
not mention the generator.
"""

import re
import sqlite3
import traceback
from pathlib import Path

import pytest

from demo_app.payment.server import handle_charge
from incident_agent.data.scenarios import KEYERROR_FRAMES

ROOT = Path(__file__).resolve().parent.parent
FRAME_RE = re.compile(r'^  File "([^"]+)", line (\d+), in (\S+)$')

LEGACY = {"card": "tok_visa", "amount": 12.5}  # older clients do not send currency


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
