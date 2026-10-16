import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from incident_agent.data.cli import main
from incident_agent.data.database import build_database

DAY_START = "2026-03-12T00:00:00Z"
DAY_END = "2026-03-13T00:00:00Z"
INCIDENT_START = "2026-03-12T14:03:00Z"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("checkout-500", 42, tmp_path_factory.mktemp("data") / "incident.db")


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def dump(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("logs", "deployments", "errors")
        }


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_schema(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"logs", "deployments", "errors"}
    assert columns(conn, "logs") == [
        "id", "timestamp", "service", "level", "message", "request_id", "error_id",
    ]  # fmt: skip
    assert columns(conn, "deployments") == [
        "id", "timestamp", "service", "version", "author", "commit_message",
    ]  # fmt: skip
    assert columns(conn, "errors") == [
        "id", "timestamp", "service", "exception_type", "message", "stacktrace", "request_context",
    ]  # fmt: skip
    foreign_keys = conn.execute("PRAGMA foreign_key_list(logs)").fetchall()
    assert [(fk[2], fk[3], fk[4]) for fk in foreign_keys] == [("errors", "error_id", "id")]


def test_time_range_is_24h_on_fixed_date(conn: sqlite3.Connection) -> None:
    for table in ("logs", "deployments", "errors"):
        low, high = conn.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM {table}").fetchone()
        assert DAY_START <= low and high < DAY_END, table
    first, last = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM logs").fetchone()
    assert first.startswith("2026-03-12T00:")
    assert last.startswith("2026-03-12T23:")
    assert conn.execute("SELECT COUNT(*) FROM logs WHERE timestamp NOT LIKE '%Z'").fetchone() == (
        0,
    )


def test_traffic_is_quieter_at_night(conn: sqlite3.Connection) -> None:
    def count(prefix: str) -> int:
        sql = "SELECT COUNT(*) FROM logs WHERE service = 'checkout' AND timestamp LIKE ?"
        return conn.execute(sql, (prefix + "%",)).fetchone()[0]

    assert count("2026-03-12T03") * 3 < count("2026-03-12T12")


def test_same_seed_is_identical(db_path: Path, tmp_path: Path) -> None:
    other = build_database("checkout-500", 42, tmp_path / "again.db")
    assert dump(db_path) == dump(other)


def test_different_seed_differs(db_path: Path, tmp_path: Path) -> None:
    other = build_database("checkout-500", 7, tmp_path / "other.db")
    assert dump(db_path)["logs"] != dump(other)["logs"]


def test_incident_starts_at_expected_time(conn: sqlite3.Connection) -> None:
    deployment = conn.execute(
        "SELECT timestamp FROM deployments WHERE service = 'payment' AND version = 'v1.4.2'"
    ).fetchone()
    assert deployment == ("2026-03-12T14:01:00Z",)

    sql = "SELECT MIN(timestamp) FROM errors WHERE exception_type = 'KeyError'"
    first_keyerror = conn.execute(sql).fetchone()[0]
    assert first_keyerror >= INCIDENT_START
    assert first_keyerror < "2026-03-12T14:05:00Z"  # starts right away, ~18% of ~5 req/min

    before = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE timestamp < ? AND service IN ('checkout', 'payment') "
        "AND (message LIKE '%KeyError%' OR message LIKE 'POST /checkout 500%')",
        (INCIDENT_START,),
    ).fetchone()
    assert before == (0,)


def test_noise_deployments_and_early_errors(conn: sqlite3.Connection) -> None:
    deployments = conn.execute("SELECT timestamp, service, version FROM deployments ORDER BY id")
    assert deployments.fetchall() == [
        ("2026-03-12T09:30:00Z", "catalog", "v2.8.0"),
        ("2026-03-12T11:15:00Z", "auth", "v3.1.1"),
        ("2026-03-12T14:01:00Z", "payment", "v1.4.2"),
    ]
    early = conn.execute("SELECT COUNT(*) FROM errors WHERE timestamp < '2026-03-12T14:00:00Z'")
    assert 3 <= early.fetchone()[0] <= 10


def test_error_rate_in_incident_window(conn: sqlite3.Connection) -> None:
    total, failed = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(level = 'ERROR'), 0) FROM logs "
        "WHERE service = 'checkout' AND message LIKE 'POST /checkout%' AND timestamp >= ?",
        (INCIDENT_START,),
    ).fetchone()
    assert 0.15 <= failed / total <= 0.21


def test_error_ids_in_logs_exist(conn: sqlite3.Connection) -> None:
    orphans = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE error_id IS NOT NULL "
        "AND error_id NOT IN (SELECT id FROM errors)"
    ).fetchone()
    assert orphans == (0,)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_keyerror_rows_have_stacktrace_and_context(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT stacktrace, request_context FROM errors WHERE exception_type = 'KeyError'"
    ).fetchall()
    assert rows
    for stacktrace, context in rows:
        assert stacktrace.startswith("Traceback (most recent call last):")
        assert stacktrace.endswith("KeyError: 'currency'")
        parsed = json.loads(context)
        assert set(parsed) == {"request_id", "endpoint", "client_version", "http_status"}
        assert parsed["http_status"] == 500


def test_failed_requests_are_consistent_across_services(conn: sqlite3.Connection) -> None:
    failed_ids = [
        r[0]
        for r in conn.execute(
            "SELECT request_id FROM logs "
            "WHERE service = 'checkout' AND level = 'ERROR' AND message LIKE 'POST /checkout 500%'"
        )
    ]
    assert failed_ids
    for request_id in failed_ids:
        lines = conn.execute(
            "SELECT service, level, message, error_id FROM logs WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        # Payment crashed before charging: no success line may exist for this request.
        assert not [x for x in lines if x[0] == "payment" and x[1] == "INFO"]
        checkout_errors = [x for x in lines if x[0] == "checkout" and x[1] == "ERROR"]
        payment_errors = [x for x in lines if x[0] == "payment" and x[1] == "ERROR"]
        assert len(checkout_errors) == 1
        assert len(payment_errors) == 1
        assert checkout_errors[0][3] is not None
        assert checkout_errors[0][3] == payment_errors[0][3]


def test_cli_overwrites_existing_file_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "nested" / "incident.db"
    output.parent.mkdir()
    output.write_text("not a database")

    assert main(["--scenario", "checkout-500", "--seed", "42", "--output", str(output)]) == 0

    out = capsys.readouterr().out
    for word in ("logs", "deployments", "errors", "checkout", "payment", "14:00"):
        assert word in out
    with sqlite3.connect(output) as connection:
        assert connection.execute("SELECT COUNT(*) FROM deployments").fetchone() == (3,)


def test_cli_rejects_unknown_scenario(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--scenario", "nope", "--output", str(tmp_path / "x.db")])
