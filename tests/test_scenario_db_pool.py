import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from incident_agent.data.cli import main
from incident_agent.data.database import build_database
from incident_agent.data.scenarios import PEAK_MULTIPLIER, POOL_SIZE, SCENARIOS

SEEDS = (42, 7)
DAY = "2026-03-12T"
POOL_ERRORS = "service = 'catalog' AND message LIKE '%waiting for a connection from the pool%'"

# Ordered hash of checkout-500 (seed 42), recorded before db-pool was added. If this fails, the
# checkout-500 output changed: that must never happen by accident (see CLAUDE.md, sample data).
CHECKOUT_500_SEED_42_HASH = "fd1ca6be0036166827770aedd732e391929f6d08453b97a09a214292bb253554"
# Same for db-pool (seed 42), recorded before cert-expired was added.
DB_POOL_SEED_42_HASH = "2efbd751d03d29c655d3cb4f03c2be208a9fbe5d26f5b165385dd264526ea381"


@pytest.fixture(scope="module", params=SEEDS)
def seed(request: pytest.FixtureRequest) -> int:
    return request.param


@pytest.fixture(scope="module")
def db_path(seed: int, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("db-pool", seed, tmp_path_factory.mktemp("dbpool") / "incident.db")


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def ordered_hash(path: Path) -> str:
    """Hash of every row of every table in id order. Only integers, strings and NULL are stored
    (no REAL columns), so the value is the same on every platform."""
    total = hashlib.sha256()
    with sqlite3.connect(path) as connection:
        for table in ("logs", "deployments", "errors"):
            digest = hashlib.sha256()
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY id"):
                assert all(v is None or isinstance(v, int | str) for v in row), (table, row)
                digest.update(json.dumps(row).encode() + b"\n")
            total.update(table.encode() + digest.digest())
    return total.hexdigest()


def search_counts(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    """(all GET /search lines, ERROR lines) in [start, end), like search_logs total_matches."""
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(level = 'ERROR'), 0) FROM logs WHERE service = 'catalog' "
        "AND message LIKE 'GET /search%' AND timestamp >= ? AND timestamp < ?"
    )
    return conn.execute(sql, (DAY + start + ":00Z", DAY + end + ":00Z")).fetchone()


def first_pool_error(conn: sqlite3.Connection) -> str:
    return conn.execute(f"SELECT MIN(timestamp) FROM errors WHERE {POOL_ERRORS}").fetchone()[0]


def minutes(timestamp: str) -> float:
    hour, minute, second = (int(x) for x in timestamp[11:19].split(":"))
    return hour * 60 + minute + second / 60


def test_registered_and_cli_accepts_both_scenarios(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert {"checkout-500", "db-pool"} <= set(SCENARIOS)
    for name in ("checkout-500", "db-pool"):
        output = tmp_path / f"{name}.db"
        assert main(["--scenario", name, "--output", str(output)]) == 0
        assert f"Generated scenario '{name}'" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--scenario", "nope", "--output", str(tmp_path / "x.db")])


def test_db_pool_is_deterministic(seed: int, db_path: Path, tmp_path: Path) -> None:
    other = build_database("db-pool", seed, tmp_path / "again.db")
    assert ordered_hash(db_path) == ordered_hash(other)
    different = build_database("db-pool", seed + 1, tmp_path / "other.db")
    assert ordered_hash(db_path) != ordered_hash(different)


def test_checkout_500_output_is_unchanged(tmp_path: Path) -> None:
    path = build_database("checkout-500", 42, tmp_path / "checkout.db")
    assert ordered_hash(path) == CHECKOUT_500_SEED_42_HASH


def test_db_pool_output_is_unchanged(tmp_path: Path) -> None:
    path = build_database("db-pool", 42, tmp_path / "dbpool.db")
    assert ordered_hash(path) == DB_POOL_SEED_42_HASH


def test_peak_search_volume_is_at_least_3x_baseline(conn: sqlite3.Connection) -> None:
    baseline, _ = search_counts(conn, "13:00", "14:00")
    assert baseline > 200
    # Busiest 60 minutes (5 minute steps) of the whole day.
    peak = max(
        search_counts(
            conn,
            f"{start // 60:02d}:{start % 60:02d}",
            f"{(start + 60) // 60:02d}:{start % 60:02d}",
        )[0]
        for start in range(0, 23 * 60, 5)
    )
    assert peak >= 3 * baseline
    assert peak <= (PEAK_MULTIPLIER + 0.5) * baseline  # a spike, not a different baseline


def test_load_ramps_up_holds_and_decays(conn: sqlite3.Connection) -> None:
    quarters = {
        start: search_counts(conn, start, end)[0]
        for start, end in [
            ("14:00", "14:15"), ("14:45", "15:00"), ("15:00", "15:15"), ("15:30", "15:45"),
            ("16:15", "16:30"), ("16:30", "16:45"), ("16:45", "17:00"), ("17:00", "17:15"),
        ]
    }  # fmt: skip
    assert quarters["14:00"] < quarters["14:45"] < quarters["15:00"] < quarters["15:30"]
    assert quarters["15:30"] > quarters["16:15"] > quarters["16:30"] > quarters["16:45"]
    assert quarters["17:00"] < quarters["15:30"] / 3
    assert abs(quarters["17:00"] - quarters["14:00"]) < 0.25 * quarters["14:00"]


def test_all_incident_errors_are_catalog_pool_timeouts(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT service, exception_type, message, request_context FROM errors WHERE timestamp >= ?",
        (DAY + "14:00:00Z",),
    ).fetchall()
    assert len(rows) > 100
    for service, exception_type, message, context in rows:
        parsed = json.loads(context)
        assert service == "catalog" and exception_type == "TimeoutError"
        assert f"waiting for a connection from the pool (pool_size={POOL_SIZE})" in message
        assert parsed["endpoint"] == "GET /search"
        assert parsed["http_status"] in (503, 504)
        assert set(parsed) == {"request_id", "endpoint", "client_version", "http_status"}
    statuses = {json.loads(r[3])["http_status"] for r in rows}
    assert statuses == {503, 504}


def test_pool_errors_have_synthetic_stacktrace_and_one_log_line(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        f"SELECT id, stacktrace, request_context FROM errors WHERE {POOL_ERRORS}"
    ).fetchall()
    assert rows
    for error_id, stacktrace, context in rows[:20]:
        assert stacktrace.startswith("Traceback (most recent call last):")
        assert 'File "/app/catalog/' in stacktrace and "demo_app" not in stacktrace
        assert stacktrace.endswith(f"(pool_size={POOL_SIZE})")
        request_id = json.loads(context)["request_id"]
        lines = conn.execute(
            "SELECT level, error_id FROM logs WHERE request_id = ?", (request_id,)
        ).fetchall()
        assert lines == [("ERROR", error_id)]  # one line per request, no INFO duplicate
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_error_rate_rises_with_load_and_drops_to_zero(conn: sqlite3.Connection) -> None:
    def rate(start: str, end: str) -> float:
        total, errors = search_counts(conn, start, end)
        return errors / total

    assert rate("13:00", "14:45") == 0  # normal load: no timeouts
    assert 0 < rate("14:45", "15:15") < rate("15:15", "16:15")
    assert rate("15:15", "16:15") > 0.3
    assert rate("16:15", "16:45") < rate("15:15", "16:15")
    # No errors once the load is back to normal.
    assert search_counts(conn, "16:50", "23:59")[1] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'catalog' AND level = 'ERROR' "
        "AND timestamp >= ?",
        (DAY + "16:50:00Z",),
    ).fetchone() == (0,)


def test_errors_start_only_above_pool_capacity(conn: sqlite3.Connection) -> None:
    first = minutes(first_pool_error(conn))
    assert 14 * 60 + 55 <= first <= 15 * 60 + 10


def test_pool_warnings_precede_first_error(conn: sqlite3.Connection) -> None:
    warns = conn.execute(
        "SELECT timestamp, level, message, request_id FROM logs "
        "WHERE service = 'catalog' AND message LIKE 'connection pool%' ORDER BY timestamp"
    ).fetchall()
    assert len(warns) > 30
    assert {w[1] for w in warns} == {"WARN"}
    assert all(w[3] is None for w in warns)
    assert warns[0][0] < first_pool_error(conn)
    assert any(f"in_use={POOL_SIZE}/{POOL_SIZE}" in w[2] and "waiting=" in w[2] for w in warns)
    before = [w for w in warns if w[0] < first_pool_error(conn)]
    assert len(before) >= 3
    assert any("usage high" in w[2] for w in before)


def test_auth_deployment_is_a_red_herring(conn: sqlite3.Connection) -> None:
    deployment = conn.execute(
        "SELECT timestamp, commit_message FROM deployments "
        "WHERE service = 'auth' AND version = 'v3.2.0'"
    ).fetchone()
    assert deployment is not None
    gap = minutes(first_pool_error(conn)) - minutes(deployment[0])
    assert 5 <= gap <= 15
    assert "token" in deployment[1].lower()
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'auth' AND level = 'ERROR'"
    ).fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM errors WHERE service = 'auth'").fetchone() == (0,)
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'auth' "
        "AND (message LIKE '%pool%' OR message LIKE '%timeout%')"
    ).fetchone() == (0,)


def test_auth_traffic_is_unchanged_by_the_incident(conn: sqlite3.Connection) -> None:
    """Auth traffic follows the daily curve only: afternoon hours look like their neighbours."""

    def requests(hour: int) -> int:
        sql = (
            "SELECT COUNT(*) FROM logs WHERE service = 'auth' AND level = 'INFO' "
            "AND timestamp LIKE ?"
        )
        return conn.execute(sql, (f"{DAY}{hour:02d}:%",)).fetchone()[0]

    baseline = (requests(12) + requests(13) + requests(17)) / 3
    for hour in (14, 15, 16):
        assert abs(requests(hour) - baseline) < 0.25 * baseline, hour
    warn_counts = [
        conn.execute(
            "SELECT COUNT(*) FROM logs WHERE service = 'auth' AND level = 'WARN' "
            "AND timestamp LIKE ?",
            (f"{DAY}{hour:02d}:%",),
        ).fetchone()[0]
        for hour in (13, 15, 17)
    ]
    assert max(warn_counts) - min(warn_counts) <= 2  # token refresh retries every ~15 minutes


def test_deployments_are_consistent(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT timestamp, service, version FROM deployments ORDER BY id"
    ).fetchall()
    assert rows == [
        (DAY + "09:30:00Z", "catalog", "v2.8.0"),
        (DAY + "11:15:00Z", "auth", "v3.1.1"),
        (DAY + "14:50:00Z", "auth", "v3.2.0"),
    ]


def test_checkout_and_payment_are_not_affected(conn: sqlite3.Connection) -> None:
    errors = conn.execute(
        "SELECT service, timestamp FROM errors WHERE service IN ('checkout', 'payment') ORDER BY id"
    ).fetchall()
    assert all(timestamp < DAY + "14:00:00Z" for _, timestamp in errors)  # only the old one-offs
    assert conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE '%KeyError%'").fetchone() == (
        0,
    )
