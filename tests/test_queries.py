import sqlite3
from pathlib import Path

import pytest

from incident_agent import queries
from incident_agent.queries import QueryError

INCIDENT = "2026-03-12T14:03:00Z"


# --- search_logs ------------------------------------------------------------------------------


def test_search_logs_filters_and_orders(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(
        conn, service="payment", level="ERROR", start="2026-03-12T14:00:00Z", limit=20
    )
    logs = result["logs"]
    assert logs and result["returned"] == len(logs) <= 20
    assert all(r["service"] == "payment" and r["level"] == "ERROR" for r in logs)
    assert all(r["timestamp"] >= INCIDENT for r in logs)
    assert [r["timestamp"] for r in logs] == sorted(r["timestamp"] for r in logs)
    assert set(logs[0]) == {"timestamp", "service", "level", "message", "request_id", "error_id"}
    assert logs[0]["error_id"] is not None


def test_search_logs_end_is_exclusive_and_dates_work(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, start="2026-03-12", end="2026-03-12T01:00:00Z", limit=200)
    assert result["logs"]
    assert all(r["timestamp"] < "2026-03-12T01:00:00Z" for r in result["logs"])


def test_search_logs_query_is_case_insensitive_substring(conn: sqlite3.Connection) -> None:
    lower = queries.search_logs(conn, query="keyerror")["total_matches"]
    upper = queries.search_logs(conn, query="KEYERROR")["total_matches"]
    assert lower == upper > 0


@pytest.mark.parametrize("text", ["100%", "%", "a_b", "\\"])
def test_search_logs_wildcards_are_literal(conn: sqlite3.Connection, text: str) -> None:
    assert queries.search_logs(conn, query=text)["total_matches"] == 0


def test_search_logs_underscore_matches_only_a_real_underscore(conn: sqlite3.Connection) -> None:
    assert queries.search_logs(conn, query="category_id")["total_matches"] > 0
    assert queries.search_logs(conn, query="category.id")["total_matches"] == 0
    assert queries.search_logs(conn, query="categoryXid")["total_matches"] == 0


def test_search_logs_injection_attempt_is_harmless(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, query="x'; DROP TABLE logs; --")
    assert result["total_matches"] == 0
    assert queries.search_logs(conn)["total_matches"] > 0


def test_search_logs_empty_result(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, service="auth", level="ERROR", start="2026-03-12T14:00:00Z")
    assert result == {
        "logs": [],
        "returned": 0,
        "total_matches": 0,
        "truncated": False,
        "limit": 50,
    }


def test_search_logs_limit_and_truncated(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, limit=5)
    assert result["returned"] == 5 and result["truncated"] is True
    assert result["total_matches"] > 5
    default = queries.search_logs(conn)
    assert default["returned"] == 50 and default["limit"] == 50


def test_search_logs_limit_is_capped_at_200(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, limit=100000)
    assert result["limit"] == queries.MAX_LIMIT == 200
    assert result["returned"] == 200 and result["truncated"] is True


def test_search_logs_not_truncated_when_everything_fits(conn: sqlite3.Connection) -> None:
    result = queries.search_logs(conn, level="ERROR", end="2026-03-12T14:00:00Z")
    assert result["total_matches"] == result["returned"] > 0
    assert result["truncated"] is False


def test_search_logs_level_is_case_insensitive(conn: sqlite3.Connection) -> None:
    assert queries.search_logs(conn, level="warn")["logs"][0]["level"] == "WARN"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"level": "FATAL"}, "Valid levels"),
        ({"service": "billing"}, "Valid services"),
        ({"start": "yesterday"}, "ISO 8601"),
        ({"end": "2026-13-45"}, "ISO 8601"),
        ({"start": "2026-03-12T14:00:00+02:00"}, "UTC"),
        ({"start": "2026-03-12T15:00:00Z", "end": "2026-03-12T14:00:00Z"}, "before"),
        ({"limit": 0}, "limit"),
        ({"limit": -3}, "limit"),
    ],
)
def test_search_logs_invalid_input(
    conn: sqlite3.Connection, kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(QueryError, match=message):
        queries.search_logs(conn, **kwargs)  # type: ignore[arg-type]


# --- get_error_stats --------------------------------------------------------------------------


def test_error_stats_shows_the_incident(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, start="2026-03-12T12:00:00Z", end="2026-03-12T16:00:00Z")
    checkout = [w for w in result["windows"] if w["service"] == "checkout"]
    before = [w for w in checkout if w["window_start"] < "2026-03-12T14:00:00Z"]
    after = [w for w in checkout if w["window_start"] >= "2026-03-12T14:05:00Z"]
    assert before and all(w["error_rate"] == 0 for w in before)
    average = sum(w["failed_requests"] for w in after) / sum(w["requests"] for w in after)
    assert 0.15 <= average <= 0.21
    assert set(checkout[0]) == {
        "window_start",
        "service",
        "error_lines",
        "requests",
        "failed_requests",
        "error_rate",
    }


def test_error_stats_windows_are_ordered_aligned_and_non_empty(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, interval_minutes=60)
    windows = result["windows"]
    keys = [(w["window_start"], w["service"]) for w in windows]
    assert keys == sorted(keys)
    assert all(w["window_start"].endswith(":00:00Z") for w in windows)
    assert result["truncated"] is False


@pytest.mark.parametrize("interval", queries.INTERVALS)
def test_error_stats_accepts_allowed_intervals(conn: sqlite3.Connection, interval: int) -> None:
    result = queries.get_error_stats(
        conn,
        start="2026-03-12T14:00:00Z",
        end="2026-03-12T15:00:00Z",
        interval_minutes=interval,
    )
    assert result["interval_minutes"] == interval
    assert result["windows"]


def test_error_stats_rejects_other_intervals(conn: sqlite3.Connection) -> None:
    with pytest.raises(QueryError, match="interval_minutes"):
        queries.get_error_stats(conn, interval_minutes=7)


def test_error_stats_empty_range(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, start="2026-03-13", end="2026-03-14")
    assert result["windows"] == [] and result["returned"] == 0 and result["truncated"] is False


def test_error_stats_truncates_long_results(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, interval_minutes=1)
    assert result["returned"] == queries.MAX_LIMIT
    assert result["truncated"] is True and "hint" in result


def test_error_stats_old_style_call_keeps_all_old_keys(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, "2026-03-12T14:00:00Z", "2026-03-12T15:00:00Z", 15)
    assert {"interval_minutes", "windows", "returned", "truncated"} <= set(result)
    assert result["interval_minutes"] == 15 and result["truncated"] is False
    assert "hint" not in result and result["next_offset"] is None


def test_error_stats_full_day_default_reports_truncation(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn)
    assert result["returned"] == queries.MAX_LIMIT == len(result["windows"])
    assert result["truncated"] is True
    assert result["total_windows"] == 1145
    assert result["omitted_windows"] == 945
    assert result["offset"] == 0 and result["next_offset"] == 200
    hint = result["hint"]
    assert "200 of 1145" in hint and "945" in hint and "offset=200" in hint
    assert result["windows"][-1]["window_start"] in hint
    assert "errors_only" in hint and "service" in hint and "interval_minutes" in hint


def test_error_stats_paging_returns_everything_exactly_once(conn: sqlite3.Connection) -> None:
    keys: list[tuple[str, str]] = []
    offset: int | None = 0
    pages = 0
    while offset is not None:
        page = queries.get_error_stats(conn, offset=offset)
        keys += [(w["window_start"], w["service"]) for w in page["windows"]]
        assert page["offset"] == offset
        offset = page["next_offset"]
        pages += 1
        assert pages <= 10
    assert pages == 6 and page["truncated"] is False and page["omitted_windows"] == 0
    assert len(keys) == len(set(keys)) == 1145
    assert keys == sorted(keys)


def test_error_stats_offset_boundaries(conn: sqlite3.Connection) -> None:
    exact = queries.get_error_stats(conn, offset=945)  # exactly 200 rows left
    assert exact["returned"] == 200 and exact["truncated"] is False
    assert exact["omitted_windows"] == 0 and exact["next_offset"] is None and "hint" not in exact
    one_more = queries.get_error_stats(conn, offset=944)  # 201 rows left
    assert one_more["returned"] == 200 and one_more["truncated"] is True
    assert one_more["omitted_windows"] == 1 and one_more["next_offset"] == 1144
    for offset in (1145, 5000):
        beyond = queries.get_error_stats(conn, offset=offset)
        assert beyond["windows"] == [] and beyond["returned"] == 0
        assert beyond["truncated"] is False and beyond["omitted_windows"] == 0
        assert beyond["total_windows"] == 1145 and beyond["next_offset"] is None


@pytest.mark.parametrize("offset", [-1, 1.5, "0", None, True])
def test_error_stats_invalid_offset(conn: sqlite3.Connection, offset: object) -> None:
    with pytest.raises(QueryError, match="offset"):
        queries.get_error_stats(conn, offset=offset)  # type: ignore[arg-type]


def test_error_stats_service_filter(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, service="payment", interval_minutes=60)
    assert result["windows"] and {w["service"] for w in result["windows"]} == {"payment"}
    assert result["total_windows"] == len(result["windows"]) <= 24
    with pytest.raises(QueryError, match="Valid services"):
        queries.get_error_stats(conn, service="billing")


def test_error_stats_service_filter_does_not_change_window_values(conn: sqlite3.Connection) -> None:
    kwargs = {"start": "2026-03-12T14:00:00Z", "end": "2026-03-12T15:00:00Z"}
    everything = queries.get_error_stats(conn, **kwargs)["windows"]  # type: ignore[arg-type]
    only = queries.get_error_stats(conn, service="checkout", **kwargs)["windows"]  # type: ignore[arg-type]
    assert only == [w for w in everything if w["service"] == "checkout"]


def test_error_stats_errors_only(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, errors_only=True)
    assert result["windows"] and all(w["error_lines"] > 0 for w in result["windows"])
    assert result["total_windows"] == 235 and result["truncated"] is True
    assert result["omitted_windows"] == 35 and "offset=200" in result["hint"]
    everything = {
        (w["window_start"], w["service"]): w
        for w in queries.get_error_stats(conn, offset=0)["windows"]
    }
    shared = [w for w in result["windows"] if (w["window_start"], w["service"]) in everything]
    assert shared and all(w == everything[(w["window_start"], w["service"])] for w in shared)


def test_error_stats_errors_only_with_service_fits_and_shows_the_onset(
    conn: sqlite3.Connection,
) -> None:
    result = queries.get_error_stats(conn, service="checkout", errors_only=True)
    assert result["truncated"] is False and result["total_windows"] == result["returned"] == 116
    quiet = [w for w in result["windows"] if w["window_start"] < "2026-03-12T14:00:00Z"]
    onset = [w for w in result["windows"] if w["window_start"] >= "2026-03-12T14:00:00Z"]
    assert onset and all(w["error_rate"] > 0 for w in onset)
    assert len(onset) > len(quiet)


def test_error_stats_errors_only_empty(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(
        conn, service="auth", errors_only=True, start="2026-03-12T14:00:00Z"
    )
    assert result["windows"] == [] and result["total_windows"] == 0
    assert result["truncated"] is False and result["next_offset"] is None


@pytest.mark.parametrize("errors_only", ["yes", 1, None])
def test_error_stats_invalid_errors_only(conn: sqlite3.Connection, errors_only: object) -> None:
    with pytest.raises(QueryError, match="errors_only"):
        queries.get_error_stats(conn, errors_only=errors_only)  # type: ignore[arg-type]


def test_error_stats_empty_range_has_consistent_metadata(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(conn, start="2026-03-13", end="2026-03-14")
    assert result["total_windows"] == 0 and result["omitted_windows"] == 0
    assert result["next_offset"] is None and result["offset"] == 0 and "hint" not in result


def test_error_stats_start_inclusive_end_exclusive(conn: sqlite3.Connection) -> None:
    result = queries.get_error_stats(
        conn, start="2026-03-12T14:05:00Z", end="2026-03-12T14:10:00Z", service="checkout"
    )
    assert [w["window_start"] for w in result["windows"]] == ["2026-03-12T14:05:00Z"]
    before = queries.get_error_stats(
        conn, start="2026-03-12T14:00:00Z", end="2026-03-12T14:05:00Z", service="checkout"
    )
    assert [w["window_start"] for w in before["windows"]] == ["2026-03-12T14:00:00Z"]


def test_error_stats_start_not_before_end_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(QueryError, match="before"):
        queries.get_error_stats(conn, start="2026-03-12T15:00:00Z", end="2026-03-12T15:00:00Z")


def test_error_stats_invalid_time(conn: sqlite3.Connection) -> None:
    with pytest.raises(QueryError, match="ISO 8601"):
        queries.get_error_stats(conn, start="noon")


# --- get_deployments --------------------------------------------------------------------------


def test_deployments_all_ordered(conn: sqlite3.Connection) -> None:
    result = queries.get_deployments(conn)
    assert [(d["timestamp"], d["service"], d["version"]) for d in result["deployments"]] == [
        ("2026-03-12T09:30:00Z", "catalog", "v2.8.0"),
        ("2026-03-12T11:15:00Z", "auth", "v3.1.1"),
        ("2026-03-12T14:01:00Z", "payment", "v1.4.2"),
    ]
    assert set(result["deployments"][0]) == {
        "timestamp",
        "service",
        "version",
        "author",
        "commit_message",
    }
    assert result["returned"] == 3


def test_deployments_filters(conn: sqlite3.Connection) -> None:
    payment = queries.get_deployments(conn, service="payment")["deployments"]
    assert [d["version"] for d in payment] == ["v1.4.2"]
    window = queries.get_deployments(conn, start="2026-03-12T10:00:00Z", end="2026-03-12T12:00:00Z")
    assert [d["version"] for d in window["deployments"]] == ["v3.1.1"]


def test_deployments_empty_and_invalid(conn: sqlite3.Connection) -> None:
    assert queries.get_deployments(conn, service="checkout")["deployments"] == []
    with pytest.raises(QueryError, match="Valid services"):
        queries.get_deployments(conn, service="db")


# --- get_stacktrace ---------------------------------------------------------------------------


def test_stacktrace_known_error(conn: sqlite3.Connection) -> None:
    logs = queries.search_logs(conn, query="KeyError", level="ERROR", limit=1)["logs"]
    error_id = logs[0]["error_id"]
    result = queries.get_stacktrace(conn, error_id)
    assert result["id"] == error_id
    assert result["exception_type"] == "KeyError"
    assert result["message"] == "'currency'"
    assert "Traceback" in result["stacktrace"]
    context = result["request_context"]
    assert isinstance(context, dict)
    assert set(context) == {"request_id", "endpoint", "client_version", "http_status"}
    assert context["http_status"] == 500


@pytest.mark.parametrize("error_id", [999999, 0, -1])
def test_stacktrace_unknown_or_invalid_id(conn: sqlite3.Connection, error_id: int) -> None:
    with pytest.raises(QueryError, match="error"):
        queries.get_stacktrace(conn, error_id)


# --- read-only --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO deployments VALUES (99, 'x', 'auth', 'v0', 'me', 'x')",
        "DELETE FROM logs",
        "DROP TABLE errors",
    ],
)
def test_connection_is_read_only(conn: sqlite3.Connection, statement: str) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(statement)


def test_missing_database_points_to_generator(tmp_path: Path) -> None:
    with pytest.raises(QueryError, match=r"generate-data --scenario checkout-500"):
        queries.open_readonly(tmp_path / "missing.db")
