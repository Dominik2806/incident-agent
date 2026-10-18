import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from incident_agent.data.cli import main
from incident_agent.data.database import build_database
from incident_agent.data.scenarios import (
    OUTAGE_END,
    OUTAGE_FAILURE_PROBABILITY,
    OUTAGE_START,
    SCENARIOS,
)
from incident_agent.data.util import CLIENT_VERSIONS

SEEDS = (42, 7)
DAY = "2026-03-12T"
START = DAY + "16:05:00Z"
END = DAY + "17:20:00Z"
PROVIDER_ERRORS = "service = 'payment' AND message LIKE '%Payvia%'"
# Seed 42, as quoted in docs/scenarios.md.
TOTAL_FAILED = 156
UNAVAILABLE_FAILED = 105
TIMEOUT_FAILED = 51
TOTAL_IN_WINDOW = 365


@pytest.fixture(scope="module", params=SEEDS)
def seed(request: pytest.FixtureRequest) -> int:
    return request.param


@pytest.fixture(scope="module")
def db_path(seed: int, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("api-outage", seed, tmp_path_factory.mktemp("api") / "incident.db")


@pytest.fixture(scope="module")
def db42(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("api-outage", 42, tmp_path_factory.mktemp("api42") / "incident.db")


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
    assert "api-outage" in SCENARIOS
    assert main(["--scenario", "api-outage", "--output", str(tmp_path / "a.db")]) == 0
    assert "Generated scenario 'api-outage'" in capsys.readouterr().out


def test_is_deterministic(seed: int, db_path: Path, tmp_path: Path) -> None:
    other = build_database("api-outage", seed, tmp_path / "again.db")
    assert ordered_hash(db_path) == ordered_hash(other)
    different = build_database("api-outage", seed + 1, tmp_path / "other.db")
    assert ordered_hash(db_path) != ordered_hash(different)


def test_window_is_the_documented_one() -> None:
    assert OUTAGE_START.strftime("%Y-%m-%dT%H:%M:%SZ") == START
    assert OUTAGE_END.strftime("%Y-%m-%dT%H:%M:%SZ") == END
    assert 60 <= (OUTAGE_END - OUTAGE_START).total_seconds() / 60 <= 90


def test_no_checkout_failure_before_the_start(conn: sqlite3.Connection) -> None:
    errors = conn.execute(
        "SELECT timestamp FROM logs WHERE service = 'checkout' AND level = 'ERROR' "
        "AND timestamp < ?",
        (START,),
    ).fetchall()
    assert errors == [(DAY + "10:48:05Z",)]  # only the shared one-off
    assert conn.execute(
        f"SELECT COUNT(*) FROM errors WHERE {PROVIDER_ERRORS} AND timestamp < ?", (START,)
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE message LIKE '%Payvia%' AND timestamp < ?", (START,)
    ).fetchone() == (0,)


def test_no_failure_from_the_end_on(conn: sqlite3.Connection) -> None:
    last = conn.execute(f"SELECT MAX(timestamp) FROM errors WHERE {PROVIDER_ERRORS}").fetchone()[0]
    assert START <= last < END
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'checkout' AND level = 'ERROR' "
        "AND timestamp >= ?",
        (END,),
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level IN ('ERROR', 'WARN') AND message LIKE '%Payvia%' "
        "AND timestamp >= ?",
        (END,),
    ).fetchone() == (0,)
    # The last window of the day is clean and busy.
    total, errors = checkout_counts(conn, "23:00", "23:59")
    assert total > 40 and errors == 0


def test_a_large_share_but_not_all_fails_in_the_window(conn: sqlite3.Connection) -> None:
    first = conn.execute(f"SELECT MIN(timestamp) FROM errors WHERE {PROVIDER_ERRORS}").fetchone()[0]
    assert START <= first < DAY + "16:06:00Z"
    total, errors = checkout_counts(conn, "16:05", "17:20")
    assert 0.3 < errors / total < 0.5  # degraded (10-50%), neither healthy nor down
    assert abs(errors / total - OUTAGE_FAILURE_PROBABILITY) < 0.08
    # A step, not a ramp: every part of the window is affected, the hour before is clean.
    for window in (("16:05", "16:35"), ("16:35", "17:05"), ("17:05", "17:20")):
        part_total, part_errors = checkout_counts(conn, *window)
        assert 0.2 < part_errors / part_total < 0.65, window
    assert checkout_counts(conn, "15:05", "16:05")[1] == 0
    assert checkout_counts(conn, "17:20", "18:20")[1] == 0


def test_ground_truth_of_seed_42(conn42: sqlite3.Connection) -> None:
    assert conn42.execute(f"SELECT COUNT(*) FROM errors WHERE {PROVIDER_ERRORS}").fetchone() == (
        TOTAL_FAILED,
    )
    assert conn42.execute(
        f"SELECT exception_type, COUNT(*) FROM errors WHERE {PROVIDER_ERRORS} GROUP BY 1 ORDER BY 1"
    ).fetchall() == [
        ("ProviderTimeoutError", TIMEOUT_FAILED),
        ("ProviderUnavailableError", UNAVAILABLE_FAILED),
    ]
    assert checkout_counts(conn42, "16:05", "17:20") == (TOTAL_IN_WINDOW, TOTAL_FAILED)
    first, last = conn42.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM errors WHERE {PROVIDER_ERRORS}"
    ).fetchone()
    assert (first, last) == (DAY + "16:05:12Z", DAY + "17:19:21Z")
    first_id, last_id = conn42.execute(
        f"SELECT MIN(id), MAX(id) FROM errors WHERE {PROVIDER_ERRORS}"
    ).fetchone()
    assert (first_id, last_id) == (6, 161)


def test_severity_inputs_match_sev_2_and_a_resolved_incident(conn42: sqlite3.Connection) -> None:
    """severity.md: a core flow at 10-50% failures, sustained, is SEV-2; the last window is clean"""
    total, errors = checkout_counts(conn42, "16:05", "17:20")
    assert 0.10 <= errors / total <= 0.50  # not above the SEV-1 line
    assert errors / total > 0.10 * 3  # well inside the band, not near the SEV-3 edge
    assert minutes(DAY + "17:20:00Z") - minutes(DAY + "16:05:00Z") >= 60  # sustained
    assert checkout_counts(conn42, "17:20", "23:59")[1] == 0  # resolved: the last windows are clean
    assert checkout_counts(conn42, "23:00", "23:59")[1] == 0


def test_every_client_version_is_affected_alike(conn: sqlite3.Connection) -> None:
    total, errors = checkout_counts(conn, "16:05", "17:20")
    overall = errors / total
    for client in CLIENT_VERSIONS:
        client_total, client_errors = conn.execute(
            "SELECT COUNT(*), SUM(level = 'ERROR') FROM logs WHERE service = 'checkout' "
            "AND timestamp >= ? AND timestamp < ? AND message LIKE ?",
            (START, END, f"%client={client}"),
        ).fetchone()
        assert client_total > 20, client
        assert abs(client_errors / client_total - overall) < 0.2, client  # also small clients
        assert client_errors > 5, client  # no client is spared


def test_provider_errors_carry_provider_status_and_retries(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT service, exception_type, message, stacktrace, request_context FROM errors "
        f"WHERE {PROVIDER_ERRORS}"
    ).fetchall()
    assert len(rows) > 100
    kinds = set()
    for service, exception_type, message, stacktrace, context in rows:
        parsed = json.loads(context)
        kinds.add(exception_type)
        assert service == "payment"
        assert "card processor Payvia" in message and "retries=2" in message
        assert "https://api.payvia.example/v1/charges" in message
        if exception_type == "ProviderUnavailableError":
            assert "HTTP 503 Service Unavailable" in message and parsed["http_status"] == 502
        else:
            assert exception_type == "ProviderTimeoutError"
            assert "timed out after 3 attempts" in message and parsed["http_status"] == 504
        assert stacktrace.startswith("Traceback (most recent call last):")
        assert "/app/payment/provider_client.py" in stacktrace and "demo_app" not in stacktrace
        assert stacktrace.endswith(f"{exception_type}: {message}")
        assert set(parsed) == {"request_id", "endpoint", "client_version", "http_status"}
        assert parsed["endpoint"] == "POST /charge" and parsed["client_version"] in CLIENT_VERSIONS
    assert kinds == {"ProviderUnavailableError", "ProviderTimeoutError"}


def test_failed_requests_have_a_payment_and_a_checkout_error_and_no_charge_ok(
    conn: sqlite3.Connection,
) -> None:
    rows = conn.execute(
        f"SELECT id, exception_type, request_context FROM errors WHERE {PROVIDER_ERRORS}"
    ).fetchall()
    for error_id, exception_type, context in rows[:60] + rows[-60:]:
        request_id = json.loads(context)["request_id"]
        lines = conn.execute(
            "SELECT service, level, error_id, message FROM logs WHERE request_id = ? "
            "ORDER BY service",
            (request_id,),
        ).fetchall()
        assert [(s, lvl, e) for s, lvl, e, _ in lines] == [
            ("checkout", "ERROR", error_id),
            ("payment", "ERROR", error_id),
        ]  # no INFO duplicate, no "charge ok"
        checkout_line = lines[0][3]
        expected = "502 payment failed: Payvia upstream 503 Service Unavailable"
        if exception_type == "ProviderTimeoutError":
            expected = "504 payment failed: Payvia upstream timeout"
        assert f"POST /checkout {expected} (retries=2) client=" in checkout_line
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_every_checkout_line_still_ends_in_a_client(conn: sqlite3.Connection) -> None:
    lines = conn.execute("SELECT message FROM logs WHERE service = 'checkout'").fetchall()
    assert lines
    for (message,) in lines:
        assert message.rsplit("client=", 1)[-1] in CLIENT_VERSIONS, message


def test_checkout_and_payment_lines_stay_consistent(conn: sqlite3.Connection) -> None:
    """Every successful checkout still has its charge ok line, every failed one has none."""
    missing = conn.execute(
        "SELECT COUNT(*) FROM logs c WHERE c.service = 'checkout' AND c.level = 'INFO' "
        "AND NOT EXISTS (SELECT 1 FROM logs p WHERE p.request_id = c.request_id "
        "AND p.service = 'payment' AND p.message LIKE 'charge ok%')"
    ).fetchone()
    assert missing == (0,)
    orphans = conn.execute(
        "SELECT COUNT(*) FROM logs p WHERE p.service = 'payment' AND p.message LIKE 'charge ok%' "
        "AND NOT EXISTS (SELECT 1 FROM logs c WHERE c.request_id = p.request_id "
        "AND c.service = 'checkout' AND c.level = 'INFO')"
    ).fetchone()
    assert orphans == (0,)


def test_recovery_is_visible_without_a_deployment(conn: sqlite3.Connection) -> None:
    failed_checks = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM logs WHERE level = 'WARN' "
        "AND message = 'card processor Payvia health check failed: HTTP 503'"
    ).fetchone()
    assert START <= failed_checks[0] and failed_checks[1] < END
    assert failed_checks[2] == 75  # one per minute
    ok = conn.execute(
        "SELECT timestamp FROM logs WHERE message = 'card processor Payvia health check ok'"
    ).fetchall()
    assert len(ok) == 1 and END <= ok[0][0] < DAY + "17:21:00Z"
    assert conn.execute(
        "SELECT COUNT(*) FROM deployments WHERE timestamp >= ?", (DAY + "15:56:00Z",)
    ).fetchone() == (0,)  # nothing deployed from the red herring to the end of the day


def test_payment_charges_drop_in_the_window_and_come_back(conn: sqlite3.Connection) -> None:
    def charges(start: str, end: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM logs WHERE service = 'payment' AND message LIKE 'charge ok%' "
            "AND timestamp >= ? AND timestamp < ?",
            (DAY + start + ":00Z", DAY + end + ":00Z"),
        ).fetchone()[0]

    before, during, after = (
        charges("15:05", "16:05"),
        charges("16:05", "17:05"),
        charges("17:20", "18:20"),
    )
    assert during < 0.8 * before and during > 0.3 * before  # reduced, not stopped
    assert after > 0.8 * before


def test_checkout_deployment_is_a_red_herring(conn: sqlite3.Connection) -> None:
    deployment = conn.execute(
        "SELECT timestamp, author, commit_message FROM deployments "
        "WHERE service = 'checkout' AND version = 'v2.7.1'"
    ).fetchone()
    assert deployment is not None
    assert deployment[0] == DAY + "15:55:00Z"
    first = conn.execute(f"SELECT MIN(timestamp) FROM errors WHERE {PROVIDER_ERRORS}").fetchone()[0]
    assert 5 <= minutes(first) - minutes(deployment[0]) <= 15
    assert "reuse HTTP connections" in deployment[2]
    # The pattern does not change at the deployment: healthy for the 10 minutes after it ...
    assert checkout_counts(conn, "15:55", "16:05")[1] == 0
    # ... and the error text names the provider's status, the same way from first to last failure.
    messages = conn.execute(
        f"SELECT DISTINCT substr(message, 1, 60) FROM errors WHERE {PROVIDER_ERRORS} "
        "AND message LIKE '%503%'"
    ).fetchall()
    assert len(messages) == 1
    # Recovery needs no deployment, and the other services are untouched.
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service IN ('auth', 'catalog') AND timestamp >= ?",
        (DAY + "14:00:00Z",),
    ).fetchone() == (0,)


def test_deployments_are_consistent(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT timestamp, service, version FROM deployments ORDER BY id"
    ).fetchall()
    assert rows == [
        (DAY + "09:30:00Z", "catalog", "v2.8.0"),
        (DAY + "11:15:00Z", "auth", "v3.1.1"),
        (DAY + "15:55:00Z", "checkout", "v2.7.1"),
    ]


def test_payment_noise_before_the_outage_has_no_provider_name(conn: sqlite3.Connection) -> None:
    """The shared 13:12 gateway timeout and retry WARNs stay, but do not look like the outage."""
    rows = conn.execute(
        "SELECT message FROM errors WHERE service = 'payment' AND timestamp < ?", (START,)
    ).fetchall()
    assert rows == [("card gateway timeout after 5.0s",)]
    assert "Payvia" not in rows[0][0]


def test_no_forbidden_versions_of_other_scenarios(conn: sqlite3.Connection) -> None:
    versions = {v for (v,) in conn.execute("SELECT version FROM deployments")}
    assert versions.isdisjoint({"v1.4.2", "v3.2.0", "v3.3.0"})
