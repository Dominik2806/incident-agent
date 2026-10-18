import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from incident_agent.data.cli import main
from incident_agent.data.database import build_database
from incident_agent.data.scenarios import CERT_FAILURE_PROBABILITY, EXPIRY_TIME, SCENARIOS
from incident_agent.data.util import CLIENT_VERSIONS

SEEDS = (42, 7)
DAY = "2026-03-12T"
EXPIRY = DAY + "15:20:00Z"
CERT_ERRORS = "service = 'checkout' AND message LIKE '%certificate has expired%'"
# Seed 42, as quoted in docs/scenarios.md.
TOTAL_FAILED = 1961
TOTAL_AFTER_EXPIRY = 2005


@pytest.fixture(scope="module", params=SEEDS)
def seed(request: pytest.FixtureRequest) -> int:
    return request.param


@pytest.fixture(scope="module")
def db_path(seed: int, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("cert-expired", seed, tmp_path_factory.mktemp("cert") / "incident.db")


@pytest.fixture(scope="module")
def db42(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("cert-expired", 42, tmp_path_factory.mktemp("cert42") / "incident.db")


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def conn42(db42: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db42)
    yield connection
    connection.close()


def ordered_hash(path: Path) -> str:
    total = hashlib.sha256()
    with sqlite3.connect(path) as connection:
        for table in ("logs", "deployments", "errors"):
            digest = hashlib.sha256()
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY id"):
                digest.update(json.dumps(row).encode() + b"\n")
            total.update(table.encode() + digest.digest())
    return total.hexdigest()


def checkout_counts(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    """(all checkout request lines, ERROR lines) in [start, end), times as 'HH:MM'."""
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(level = 'ERROR'), 0) FROM logs WHERE service = 'checkout' "
        "AND timestamp >= ? AND timestamp < ?"
    )
    return conn.execute(sql, (DAY + start + ":00Z", DAY + end + ":00Z")).fetchone()


def minutes(timestamp: str) -> float:
    hour, minute, second = (int(x) for x in timestamp[11:19].split(":"))
    return hour * 60 + minute + second / 60


def test_registered_and_cli_accepts_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "cert-expired" in SCENARIOS
    assert main(["--scenario", "cert-expired", "--output", str(tmp_path / "c.db")]) == 0
    assert "Generated scenario 'cert-expired'" in capsys.readouterr().out


def test_is_deterministic(seed: int, db_path: Path, tmp_path: Path) -> None:
    other = build_database("cert-expired", seed, tmp_path / "again.db")
    assert ordered_hash(db_path) == ordered_hash(other)
    different = build_database("cert-expired", seed + 1, tmp_path / "other.db")
    assert ordered_hash(db_path) != ordered_hash(different)


def test_expiry_is_the_documented_instant() -> None:
    assert EXPIRY_TIME.strftime("%Y-%m-%dT%H:%M:%SZ") == EXPIRY


def test_no_checkout_failure_before_expiry(conn: sqlite3.Connection) -> None:
    errors = conn.execute(
        "SELECT timestamp, message FROM logs WHERE service = 'checkout' AND level = 'ERROR' "
        "AND timestamp < ?",
        (EXPIRY,),
    ).fetchall()
    assert [t for t, _ in errors] == [DAY + "10:48:05Z"]  # only the shared one-off
    assert "certificate" not in errors[0][1]
    assert conn.execute(
        f"SELECT COUNT(*) FROM errors WHERE {CERT_ERRORS} AND timestamp < ?", (EXPIRY,)
    ).fetchone() == (0,)


def test_failures_step_up_at_the_expiry(conn: sqlite3.Connection) -> None:
    first = conn.execute(f"SELECT MIN(timestamp) FROM errors WHERE {CERT_ERRORS}").fetchone()[0]
    assert EXPIRY <= first < DAY + "15:21:00Z"
    # A step, not a ramp: 0% in the hour before, at least 90% in every half hour after.
    assert checkout_counts(conn, "14:20", "15:20")[1] == 0
    for start in range(15 * 60 + 20, 23 * 60 + 20, 30):
        window = [f"{m // 60:02d}:{m % 60:02d}" for m in (start, start + 30)]
        total, errors = checkout_counts(conn, *window)
        assert errors / total >= 0.9, window
    assert (
        conn.execute(
            "SELECT MAX(timestamp) FROM logs WHERE service = 'checkout' AND level = 'ERROR'"
        ).fetchone()[0]
        > DAY + "23:30:00Z"
    )  # no recovery before the end of the day


def test_about_98_percent_of_checkouts_fail(conn: sqlite3.Connection) -> None:
    total, errors = checkout_counts(conn, "15:20", "23:59")
    total_to_end, errors_to_end = conn.execute(
        "SELECT COUNT(*), SUM(level = 'ERROR') FROM logs WHERE service = 'checkout' "
        "AND timestamp >= ?",
        (EXPIRY,),
    ).fetchone()
    assert total_to_end >= total and errors_to_end >= errors
    assert abs(errors_to_end / total_to_end - CERT_FAILURE_PROBABILITY) < 0.03


def test_ground_truth_of_seed_42(conn42: sqlite3.Connection) -> None:
    assert conn42.execute(f"SELECT COUNT(*) FROM errors WHERE {CERT_ERRORS}").fetchone() == (
        TOTAL_FAILED,
    )
    total, errors = conn42.execute(
        "SELECT COUNT(*), SUM(level = 'ERROR') FROM logs WHERE service = 'checkout' "
        "AND timestamp >= ?",
        (EXPIRY,),
    ).fetchone()
    assert (total, errors) == (TOTAL_AFTER_EXPIRY, TOTAL_FAILED)
    first = conn42.execute(f"SELECT MIN(timestamp) FROM errors WHERE {CERT_ERRORS}").fetchone()[0]
    assert first == DAY + "15:20:05Z"


def test_severity_inputs_match_sev_1(conn42: sqlite3.Connection) -> None:
    """severity.md: a core flow with more than 50% failures that is still failing is SEV-1."""
    total, errors = checkout_counts(conn42, "15:20", "16:20")
    assert errors / total > 0.5
    assert checkout_counts(conn42, "23:00", "23:59")[1] > 0  # the last window still fails


def test_every_client_version_fails_equally(conn: sqlite3.Connection) -> None:
    rates = {}
    for client in CLIENT_VERSIONS:
        total, errors = conn.execute(
            "SELECT COUNT(*), SUM(level = 'ERROR') FROM logs WHERE service = 'checkout' "
            "AND timestamp >= ? AND message LIKE ?",
            (EXPIRY, f"%client={client}"),
        ).fetchone()
        assert total > 100, client
        rates[client] = errors / total
    assert min(rates.values()) > 0.9
    assert max(rates.values()) - min(rates.values()) < 0.08


def test_certificate_errors_carry_the_not_after_time(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT service, exception_type, message, stacktrace, request_context FROM errors "
        f"WHERE {CERT_ERRORS}"
    ).fetchall()
    assert len(rows) > 500
    for service, exception_type, message, stacktrace, context in rows:
        parsed = json.loads(context)
        assert service == "checkout" and exception_type == "SSLCertVerificationError"
        assert "certificate has expired" in message
        assert "notAfter=2026-03-12T15:20:00Z" in message  # findable, not to be inferred
        assert stacktrace.startswith("Traceback (most recent call last):")
        assert 'File "/app/checkout/' in stacktrace and "demo_app" not in stacktrace
        assert stacktrace.endswith(message)
        assert parsed["endpoint"] == "POST /checkout" and parsed["http_status"] == 502
        assert set(parsed) == {"request_id", "endpoint", "client_version", "http_status"}
        assert parsed["client_version"] in CLIENT_VERSIONS


def test_failed_requests_have_one_checkout_line_and_no_payment_line(
    conn: sqlite3.Connection,
) -> None:
    rows = conn.execute(f"SELECT id, request_context FROM errors WHERE {CERT_ERRORS}").fetchall()
    for error_id, context in rows[:100] + rows[-100:]:
        request_id = json.loads(context)["request_id"]
        lines = conn.execute(
            "SELECT service, level, error_id FROM logs WHERE request_id = ?", (request_id,)
        ).fetchall()
        assert lines == [("checkout", "ERROR", error_id)]  # no INFO duplicate, no "charge ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_every_checkout_line_still_ends_in_a_client(conn: sqlite3.Connection) -> None:
    lines = conn.execute("SELECT message FROM logs WHERE service = 'checkout'").fetchall()
    assert lines
    for (message,) in lines:
        assert message.rsplit("client=", 1)[-1] in CLIENT_VERSIONS, message


def test_payment_has_no_error_and_stops_receiving_most_charges(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service = 'payment' AND timestamp >= ?",
        (DAY + "14:00:00Z",),
    ).fetchone() == (0,)
    before = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'payment' AND message LIKE 'charge ok%' "
        "AND timestamp >= ? AND timestamp < ?",
        (DAY + "15:00:00Z", EXPIRY),
    ).fetchone()[0]
    after = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'payment' AND message LIKE 'charge ok%' "
        "AND timestamp >= ? AND timestamp < ?",
        (EXPIRY, DAY + "15:40:00Z"),
    ).fetchone()[0]
    assert before > 50 and after < before / 3


def test_auth_deployment_is_a_red_herring(conn: sqlite3.Connection) -> None:
    deployment = conn.execute(
        "SELECT timestamp, commit_message FROM deployments "
        "WHERE service = 'auth' AND version = 'v3.3.0'"
    ).fetchone()
    assert deployment is not None
    first = conn.execute(f"SELECT MIN(timestamp) FROM errors WHERE {CERT_ERRORS}").fetchone()[0]
    assert deployment[0] == DAY + "15:12:00Z"
    assert 5 <= minutes(first) - minutes(deployment[0]) <= 15
    assert "JWT" in deployment[1]
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service = 'auth' AND timestamp >= ?",
        (DAY + "14:00:00Z",),
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'auth' AND (level = 'ERROR' "
        "OR message LIKE '%certificate%' OR message LIKE '%x509%') AND timestamp >= ?",
        (DAY + "14:00:00Z",),
    ).fetchone() == (0,)


def test_auth_traffic_is_unchanged_by_the_incident(conn: sqlite3.Connection) -> None:
    def requests(hour: int) -> int:
        sql = (
            "SELECT COUNT(*) FROM logs WHERE service = 'auth' AND level = 'INFO' "
            "AND timestamp LIKE ?"
        )
        return conn.execute(sql, (f"{DAY}{hour:02d}:%",)).fetchone()[0]

    baseline = (requests(13) + requests(14) + requests(17)) / 3
    for hour in (15, 16):
        assert abs(requests(hour) - baseline) < 0.25 * baseline, hour


def test_deployments_are_consistent(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT timestamp, service, version FROM deployments ORDER BY id"
    ).fetchall()
    assert rows == [
        (DAY + "09:30:00Z", "catalog", "v2.8.0"),
        (DAY + "11:15:00Z", "auth", "v3.1.1"),
        (DAY + "15:12:00Z", "auth", "v3.3.0"),
    ]


def test_catalog_is_not_affected(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service = 'catalog' AND timestamp >= ?",
        (DAY + "13:30:00Z",),
    ).fetchone() == (0,)
