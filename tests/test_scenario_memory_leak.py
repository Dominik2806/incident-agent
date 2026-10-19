import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from incident_agent.data.cli import main
from incident_agent.data.constants import DAY_START
from incident_agent.data.database import build_database
from incident_agent.data.scenarios import (
    LEAK_DEPLOY_TIME,
    MEMORY_LIMIT_MIB,
    RESTART_SECONDS,
    SCENARIOS,
    SECONDS_TO_LIMIT,
    restart_schedule,
)
from incident_agent.data.util import CLIENT_VERSIONS

SEEDS = (42, 7)
DAY = "2026-03-12T"
LEAK_START = DAY + "10:20:00Z"
RESTARTING = "app container of catalog is restarting"
REFUSED_ERRORS = f"service = 'catalog' AND message LIKE '%{RESTARTING}%'"
# (kill, ready) of the four restarts, as quoted in docs/scenarios.md.
RESTARTS = (
    ("13:16:00", "13:18:00"),
    ("16:14:00", "16:16:00"),
    ("19:12:00", "19:14:00"),
    ("22:10:00", "22:12:00"),
)
# Seed 42, as quoted in docs/scenarios.md.
TOTAL_FAILED = 102
FAILED_PER_RESTART = (28, 36, 25, 13)
CATALOG_REQUESTS_SINCE_LEAK = 10076
SLOPE_MIB_PER_MIN = 1.75
MEMORY_LINE = re.compile(r"rss=(\d+)MiB limit=(\d+)MiB \((\d+)%\)")


@pytest.fixture(scope="module", params=SEEDS)
def seed(request: pytest.FixtureRequest) -> int:
    return request.param


@pytest.fixture(scope="module")
def db_path(seed: int, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("memory-leak", seed, tmp_path_factory.mktemp("leak") / "incident.db")


@pytest.fixture(scope="module")
def db42(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database("memory-leak", 42, tmp_path_factory.mktemp("leak42") / "incident.db")


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


def minutes(timestamp: str) -> float:
    hour, minute, second = (int(x) for x in timestamp[11:19].split(":"))
    return hour * 60 + minute + second / 60


def memory(conn: sqlite3.Connection, service: str, start: str, end: str) -> list[tuple[str, int]]:
    """(timestamp, rss) of the memory lines of a service in [start, end), times as 'HH:MM'."""
    rows = conn.execute(
        "SELECT timestamp, message FROM logs WHERE service = ? AND message LIKE 'memory usage%' "
        "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (service, DAY + start + ":00Z", DAY + end + ":00Z"),
    ).fetchall()
    return [(t, int(MEMORY_LINE.search(m).group(1))) for t, m in rows]


def slope(points: list[tuple[str, int]]) -> float:
    """Least squares slope in MiB per minute."""
    xs = [minutes(t) for t, _ in points]
    ys = [rss for _, rss in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    top = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return top / sum((x - mean_x) ** 2 for x in xs)


def catalog_requests(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    """(request lines, failed lines) of catalog in [start, end)."""
    return conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(level = 'ERROR'), 0) FROM logs "
        "WHERE service = 'catalog' AND request_id IS NOT NULL AND timestamp >= ? AND timestamp < ?",
        (start, end),
    ).fetchone()


def test_registered_and_cli_accepts_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "memory-leak" in SCENARIOS
    assert main(["--scenario", "memory-leak", "--output", str(tmp_path / "m.db")]) == 0
    assert "Generated scenario 'memory-leak'" in capsys.readouterr().out


def test_is_deterministic(seed: int, db_path: Path, tmp_path: Path) -> None:
    other = build_database("memory-leak", seed, tmp_path / "again.db")
    assert ordered_hash(db_path) == ordered_hash(other)
    different = build_database("memory-leak", seed + 1, tmp_path / "other.db")
    assert ordered_hash(db_path) != ordered_hash(different)


def test_restart_schedule_is_the_documented_one() -> None:
    schedule = restart_schedule()
    assert [(k.strftime("%H:%M:%S"), r.strftime("%H:%M:%S")) for k, r in schedule] == list(RESTARTS)
    assert LEAK_DEPLOY_TIME.strftime("%Y-%m-%dT%H:%M:%SZ") == LEAK_START
    assert SECONDS_TO_LIMIT == 176 * 60 and RESTART_SECONDS == 120
    # the next kill would be after midnight: the incident is still going on at the end of the data
    assert schedule[-1][1] + timedelta(seconds=SECONDS_TO_LIMIT) > DAY_START + timedelta(days=1)


def test_memory_is_flat_before_the_leaking_deployment(conn: sqlite3.Connection) -> None:
    points = memory(conn, "catalog", "00:00", "10:20")
    assert len(points) >= 120  # every 5 minutes
    values = [rss for _, rss in points]
    assert max(values) - min(values) <= 8  # jitter only, also over the v2.8.0 deployment at 09:30
    assert abs(slope(points)) < 0.02


def test_memory_rises_with_the_same_slope_in_every_cycle(conn: sqlite3.Connection) -> None:
    cycles = [("10:20", "13:16")] + [(RESTARTS[i][1][:5], RESTARTS[i + 1][0][:5]) for i in range(3)]
    for start, end in cycles:
        value = slope(memory(conn, "catalog", start, end))
        assert abs(value - SLOPE_MIB_PER_MIN) < 0.1, (start, value)


def test_slope_changes_at_the_catalog_deployment_and_not_at_the_others(
    conn: sqlite3.Connection,
) -> None:
    def rate(start: str, end: str, service: str = "catalog") -> float:
        return slope(memory(conn, service, start, end))

    assert abs(rate("09:00", "10:20")) < 0.05  # includes the cache deployment at 09:30
    assert rate("10:20", "11:20") > 1.5  # starts at v2.9.0
    assert abs(rate("09:30", "10:20")) < 0.05  # flat after v2.8.0, so it is not the cause
    for service in ("auth", "payment"):
        assert abs(rate("00:00", "13:16", service)) < 0.02, service  # incl. auth v3.4.0 at 13:04


def test_every_kill_is_at_the_limit_and_followed_by_a_restart_120s_later(
    conn: sqlite3.Connection,
) -> None:
    kills = conn.execute(
        "SELECT timestamp, level, request_id, error_id, message FROM logs "
        "WHERE message LIKE '%OOMKilled (%' ORDER BY timestamp"
    ).fetchall()
    assert [(t[11:19], lvl, rid, eid) for t, lvl, rid, eid, _ in kills] == [
        (k, "ERROR", None, None) for k, _ in RESTARTS
    ]
    for _, _, _, _, message in kills:
        assert f"rss={MEMORY_LIMIT_MIB}MiB limit={MEMORY_LIMIT_MIB}MiB exit_code=137" in message
    started = conn.execute(
        "SELECT timestamp, message FROM logs WHERE message LIKE 'catalog v2.9.0 started (restart%' "
        "ORDER BY timestamp"
    ).fetchall()
    assert [(t[11:19], m[m.index("(restart") :]) for t, m in started] == [
        (r, f"(restart {i} after OOMKilled), ready after 120s")
        for i, (_, r) in enumerate(RESTARTS, start=1)
    ]
    deployed = conn.execute(
        "SELECT timestamp, message FROM logs WHERE message IN "
        "('catalog v2.8.0 started', 'catalog v2.9.0 started') ORDER BY timestamp"
    ).fetchall()
    assert deployed == [
        (DAY + "09:30:00Z", "catalog v2.8.0 started"),
        (LEAK_START, "catalog v2.9.0 started"),
    ]


def test_memory_resets_after_each_restart(conn: sqlite3.Connection) -> None:
    for kill, ready in RESTARTS:
        before = memory(conn, "catalog", f"{kill[:2]}:{int(kill[3:5]) - 5:02d}", kill[:5])
        after = memory(conn, "catalog", ready[:5], f"{ready[:2]}:{int(ready[3:5]) + 10:02d}")
        assert before and before[-1][1] >= 495, kill
        assert after and after[0][1] <= 215, ready
        assert after[-1][1] > after[0][1]  # and climbs again
        down = memory(conn, "catalog", kill[:5], ready[:5])
        assert all(
            not (DAY + kill <= t < DAY + ready) for t, _ in down
        )  # nothing logged while down


def test_cadence_dates_the_leak_to_the_deployment() -> None:
    """kill 2 - ready 1 is the time to fill; kill 1 minus that is the deployment (10:20:00)."""
    (kill1, ready1), (kill2, _) = restart_schedule()[:2]
    fill = kill2 - ready1
    assert fill == timedelta(seconds=SECONDS_TO_LIMIT) == timedelta(minutes=176)
    assert kill1 - fill == LEAK_DEPLOY_TIME


def test_memory_warnings_start_at_85_percent_and_only_for_catalog(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT service, level, message FROM logs WHERE message LIKE 'memory usage%'"
    ).fetchall()
    assert len(rows) > 850  # catalog, auth and payment every 5 minutes
    for service, level, message in rows:
        percent = int(MEMORY_LINE.search(message).group(3))
        assert (level == "WARN") == (percent >= 85), message
        assert (level == "WARN") == message.startswith("memory usage high"), message
        if level == "WARN":
            assert service == "catalog"
    first_warn = conn.execute(
        "SELECT MIN(timestamp) FROM logs WHERE level = 'WARN' AND message LIKE 'memory usage high%'"
    ).fetchone()[0]
    assert DAY + "12:30:00Z" <= first_warn < DAY + "12:45:00Z"


def test_failures_exist_only_inside_restart_windows(conn: sqlite3.Connection) -> None:
    windows = [(DAY + k + "Z", DAY + r + "Z") for k, r in RESTARTS]
    rows = conn.execute(f"SELECT timestamp FROM errors WHERE {REFUSED_ERRORS}").fetchall()
    assert len(rows) > 60
    for (timestamp,) in rows:
        assert any(start <= timestamp < end for start, end in windows), timestamp
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service = 'catalog' AND timestamp < ?", (windows[0][0],)
    ).fetchone() == (2,)  # only the shared one-offs (03:42 and 12:26)


def test_every_catalog_request_in_a_window_fails(conn: sqlite3.Connection) -> None:
    for kill, ready in RESTARTS:
        total, failed = catalog_requests(conn, DAY + kill + "Z", DAY + ready + "Z")
        assert total > 0 and failed == total, kill
    total, failed = catalog_requests(conn, DAY + "10:20:00Z", DAY + "13:16:00Z")
    assert total > 1500 and failed == 1  # the shared 12:26 one-off only
    for start, end in (
        ("13:18:00", "16:14:00"),
        ("16:16:00", "19:12:00"),
        ("19:14:00", "22:10:00"),
    ):
        total, failed = catalog_requests(conn, DAY + start + "Z", DAY + end + "Z")
        assert failed == 0, start


def test_failed_requests_have_one_log_line_and_one_error_row(conn: sqlite3.Connection) -> None:
    rows = conn.execute(f"SELECT id, request_context FROM errors WHERE {REFUSED_ERRORS}").fetchall()
    for error_id, context in rows[:30] + rows[-30:]:
        request_id = json.loads(context)["request_id"]
        lines = conn.execute(
            "SELECT service, level, error_id, message FROM logs WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        assert [(s, lvl, e) for s, lvl, e, _ in lines] == [("catalog", "ERROR", error_id)]
        assert f"503 ConnectionRefusedError: {RESTARTING}" in lines[0][3]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_errors_say_connection_refused_and_not_oom(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT exception_type, message, stacktrace, request_context FROM errors "
        f"WHERE {REFUSED_ERRORS}"
    ).fetchall()
    assert len(rows) > 60
    for exception_type, message, stacktrace, context in rows:
        parsed = json.loads(context)
        assert exception_type == "ConnectionRefusedError"
        assert message == f"[Errno 111] Connection refused: {RESTARTING}"
        assert "oom" not in (message + stacktrace).lower() and "memory" not in message.lower()
        assert stacktrace.startswith("Traceback (most recent call last):")
        assert "/app/catalog/ingress.py" in stacktrace and "demo_app" not in stacktrace
        assert stacktrace.endswith(f"{exception_type}: {message}")
        assert set(parsed) == {"request_id", "endpoint", "client_version", "http_status"}
        assert parsed["http_status"] == 503 and parsed["client_version"] in CLIENT_VERSIONS
        assert parsed["endpoint"].startswith("GET /products/")


def test_other_services_memory_is_flat_also_after_the_auth_deployment(
    conn: sqlite3.Connection,
) -> None:
    for service, base in (("auth", 118), ("payment", 260)):
        points = memory(conn, service, "00:00", "23:59")
        assert len(points) >= 285
        assert all(abs(rss - base) <= 3 for _, rss in points), service
        after = memory(conn, service, "13:04", "23:59")
        assert abs(slope(after)) < 0.01, service


def test_checkout_payment_and_auth_have_no_new_errors(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT timestamp, service FROM errors WHERE service != 'catalog' ORDER BY timestamp"
    ).fetchall()
    assert rows == [  # only the shared one-offs
        (DAY + "08:17:40Z", "auth"),
        (DAY + "10:48:05Z", "checkout"),
        (DAY + "13:12:03Z", "payment"),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'ERROR' AND service != 'catalog'"
    ).fetchone() == (3,)


def test_ground_truth_of_seed_42(conn42: sqlite3.Connection) -> None:
    assert conn42.execute(f"SELECT COUNT(*) FROM errors WHERE {REFUSED_ERRORS}").fetchone() == (
        TOTAL_FAILED,
    )
    per_restart = tuple(
        conn42.execute(
            f"SELECT COUNT(*) FROM errors WHERE {REFUSED_ERRORS} "
            "AND timestamp >= ? AND timestamp < ?",
            (DAY + k + "Z", DAY + r + "Z"),
        ).fetchone()[0]
        for k, r in RESTARTS
    )
    assert per_restart == FAILED_PER_RESTART and sum(per_restart) == TOTAL_FAILED
    # all catalog ERROR lines are 102 + 4 kills + 2 shared one-offs: the counting trap
    assert conn42.execute(
        "SELECT COUNT(*) FROM logs WHERE service = 'catalog' AND level = 'ERROR'"
    ).fetchone() == (TOTAL_FAILED + 4 + 2,)
    first_id, first = conn42.execute(
        f"SELECT MIN(id), MIN(timestamp) FROM errors WHERE {REFUSED_ERRORS}"
    ).fetchone()
    assert (first_id, first[:16]) == (6, DAY + "13:16")
    assert conn42.execute("SELECT COUNT(*), COUNT(DISTINCT id) FROM errors").fetchone() == (
        107,
        107,
    )
    assert conn42.execute("SELECT COUNT(*) FROM deployments").fetchone() == (4,)
    # the numbers docs/scenarios.md quotes
    assert conn42.execute("SELECT COUNT(*) FROM logs").fetchone() == (28977,)
    assert conn42.execute(
        "SELECT service, COUNT(*) FROM logs WHERE message LIKE 'memory usage%' "
        "GROUP BY service ORDER BY service"
    ).fetchall() == [("auth", 288), ("catalog", 286), ("payment", 288)]
    assert conn42.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'WARN' AND message LIKE 'memory usage%'"
    ).fetchone() == (34,)
    assert catalog_requests(conn42, DAY + "13:15:00Z", DAY + "13:20:00Z") == (73, 28)


def test_severity_inputs_match_sev_3(conn42: sqlite3.Connection) -> None:
    """severity.md: a non-core service is degraded, no core flow is touched, a workaround exists"""
    total, failed = catalog_requests(conn42, LEAK_START, DAY + "23:59:59Z")
    assert total == CATALOG_REQUESTS_SINCE_LEAK and failed == TOTAL_FAILED + 1  # + shared one-off
    assert 0.005 < failed / total < 0.02  # about 1%, far from the 10-50% band of a core flow
    assert sum(1 for _ in RESTARTS) * RESTART_SECONDS == 8 * 60  # four short outages
    for service in ("checkout", "payment"):
        assert conn42.execute(
            "SELECT COUNT(*) FROM logs WHERE service = ? AND level = 'ERROR' AND timestamp >= ?",
            (service, DAY + "13:15:00Z"),
        ).fetchone() == (0,)


def test_deployments_are_consistent(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT timestamp, service, version, author, commit_message FROM deployments ORDER BY id"
    ).fetchall()
    assert rows == [
        (
            DAY + "09:30:00Z",
            "catalog",
            "v2.8.0",
            "j.novak",
            "perf(catalog): add category index cache",
        ),
        (
            LEAK_START,
            "catalog",
            "v2.9.0",
            "m.keller",
            "feat(catalog): add product comparison endpoint",
        ),
        (
            DAY + "11:15:00Z",
            "auth",
            "v3.1.1",
            "a.rossi",
            "fix(auth): shorten session refresh interval",
        ),
        (
            DAY + "13:04:00Z",
            "auth",
            "v3.4.0",
            "a.rossi",
            "perf(auth): cache validated sessions in memory",
        ),
    ]
    # the message of the real cause says nothing about memory
    assert "memory" not in rows[1][4] and "leak" not in rows[1][4]


def test_auth_deployment_is_a_red_herring(conn: sqlite3.Connection) -> None:
    deployed = conn.execute(
        "SELECT timestamp FROM deployments WHERE service = 'auth' AND version = 'v3.4.0'"
    ).fetchone()[0]
    first_kill = DAY + RESTARTS[0][0] + "Z"
    assert (
        10 <= minutes(first_kill) - minutes(deployed) <= 15
    )  # the last deployment before the kill
    # nothing changes in auth at the deployment: no error, flat memory, same traffic
    assert conn.execute(
        "SELECT COUNT(*) FROM errors WHERE service = 'auth' AND timestamp >= '" + DAY + "10:00:00Z'"
    ).fetchone() == (0,)
    before, after = memory(conn, "auth", "12:00", "13:04"), memory(conn, "auth", "13:04", "14:04")
    assert abs(sum(r for _, r in before) / len(before) - sum(r for _, r in after) / len(after)) < 2
    # and kills 2 to 4 follow hours after the last deployment of the day
    assert minutes(DAY + RESTARTS[1][0] + "Z") - minutes(deployed) > 180


def test_older_cache_deployment_is_not_the_cause(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'WARN' AND message LIKE 'memory usage high%'"
        " AND timestamp < ?",
        (DAY + "12:00:00Z",),
    ).fetchone() == (0,)
    after_cache = [rss for _, rss in memory(conn, "catalog", "09:30", "10:20")]
    assert len(after_cache) == 10 and max(after_cache) < 212  # flat for 50 minutes after v2.8.0


def test_incident_is_ongoing_at_the_end(conn: sqlite3.Connection) -> None:
    last = memory(conn, "catalog", "23:30", "23:59")
    assert len(last) >= 5 and last[-1][1] > last[0][1] > 320  # still climbing, about 75% full
    assert last[-1][1] < MEMORY_LIMIT_MIB - 80  # no kill is due before the end of the data
    assert conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'ERROR' AND timestamp >= ?", (DAY + "22:12:00Z",)
    ).fetchone() == (0,)


def test_every_checkout_line_still_ends_in_a_client(conn: sqlite3.Connection) -> None:
    lines = conn.execute("SELECT message FROM logs WHERE service = 'checkout'").fetchall()
    assert lines
    for (message,) in lines:
        assert message.rsplit("client=", 1)[-1] in CLIENT_VERSIONS, message


def test_logs_are_sorted_and_have_no_duplicates(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, timestamp FROM logs ORDER BY id").fetchall()
    assert [t for _, t in rows] == sorted(t for _, t in rows)
    assert [i for i, _ in rows] == list(range(1, len(rows) + 1))
    errors = conn.execute("SELECT id, timestamp FROM errors ORDER BY id").fetchall()
    assert [t for _, t in errors] == sorted(t for _, t in errors)
    duplicates = conn.execute(
        "SELECT timestamp, service, message FROM logs WHERE request_id IS NULL "
        "GROUP BY timestamp, service, message HAVING COUNT(*) > 1"
    ).fetchall()
    assert duplicates == []
    assert conn.execute("SELECT COUNT(*) FROM logs WHERE timestamp NOT LIKE '%Z'").fetchone() == (
        0,
    )


def test_no_forbidden_versions_of_other_scenarios(conn: sqlite3.Connection) -> None:
    versions = {v for (v,) in conn.execute("SELECT version FROM deployments")}
    assert versions.isdisjoint({"v1.4.2", "v3.2.0", "v3.3.0", "v2.7.1"})
