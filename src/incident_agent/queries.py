"""Read-only query functions for the incident database.

All logic of the MCP tools lives here as plain functions that take a `sqlite3.Connection` and
return dicts/lists, so it can be tested without MCP. Values always go through `?` placeholders.
Timestamps are UTC; `start` is inclusive and `end` is exclusive.
"""

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_agent.data.constants import SERVICES

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
INTERVALS = (1, 5, 15, 30, 60)
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

GENERATE_HINT = "Run `uv run generate-data --scenario checkout-500` first to create it."
TIMESTAMP_EXAMPLE = "2026-03-12T14:03:00Z"
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


class QueryError(ValueError):
    """Invalid input or missing data. The message is meant to be shown to the user."""


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open the database read-only (SQLite URI mode=ro)."""
    if not path.is_file():
        raise QueryError(f"Database not found at {path}. {GENERATE_HINT}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")  # second guard besides mode=ro
    return conn


# --- validation -------------------------------------------------------------------------------


def _check_service(service: str | None) -> str | None:
    if service is None:
        return None
    if service not in SERVICES:
        raise QueryError(f"Unknown service {service!r}. Valid services: {', '.join(SERVICES)}.")
    return service


def _check_level(level: str | None) -> str | None:
    if level is None:
        return None
    if level.upper() not in LEVELS:
        raise QueryError(f"Unknown level {level!r}. Valid levels: {', '.join(LEVELS)}.")
    return level.upper()


def _check_timestamp(value: str | None, name: str) -> str | None:
    """Normalize to the stored format YYYY-MM-DDTHH:MM:SSZ so string comparison works."""
    if value is None:
        return None
    text = value.strip()
    problem = f"Invalid {name} {value!r}. Use ISO 8601 UTC, e.g. {TIMESTAMP_EXAMPLE} or 2026-03-12."
    try:
        if _DATE_ONLY.fullmatch(text):
            parsed = datetime.fromisoformat(text).replace(tzinfo=UTC)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            elif parsed.utcoffset():
                raise QueryError(f"{name} must be in UTC (suffix Z), got {value!r}.")
    except ValueError:
        raise QueryError(problem) from None
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    start, end = _check_timestamp(start, "start"), _check_timestamp(end, "end")
    if start and end and start >= end:
        raise QueryError(f"start ({start}) must be before end ({end}).")
    return start, end


def _check_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise QueryError(f"limit must be an integer between 1 and {MAX_LIMIT}, got {limit!r}.")
    return min(limit, MAX_LIMIT)


def _range_clauses(start: str | None, end: str | None) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start:
        clauses.append("timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("timestamp < ?")
        params.append(end)
    return clauses, params


def _where(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- queries ----------------------------------------------------------------------------------


def search_logs(
    conn: sqlite3.Connection,
    service: str | None = None,
    level: str | None = None,
    start: str | None = None,
    end: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    service, level = _check_service(service), _check_level(level)
    start, end = _check_range(start, end)
    limit = _check_limit(limit)

    clauses, params = _range_clauses(start, end)
    if service:
        clauses.append("service = ?")
        params.append(service)
    if level:
        clauses.append("level = ?")
        params.append(level)
    if query:
        clauses.append("message LIKE ? ESCAPE '\\'")  # case-insensitive for ASCII
        params.append(f"%{_escape_like(query)}%")
    where = _where(clauses)

    total = conn.execute(f"SELECT COUNT(*) FROM logs {where}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT timestamp, service, level, message, request_id, error_id FROM logs "
        f"{where} ORDER BY timestamp, id LIMIT ?",
        [*params, limit],
    ).fetchall()
    return {
        "logs": [dict(row) for row in rows],
        "returned": len(rows),
        "total_matches": total,
        "truncated": total > len(rows),
        "limit": limit,
    }


def get_error_stats(
    conn: sqlite3.Connection,
    start: str | None = None,
    end: str | None = None,
    interval_minutes: int = 5,
) -> dict[str, Any]:
    if interval_minutes not in INTERVALS:
        raise QueryError(
            f"interval_minutes must be one of {', '.join(map(str, INTERVALS))}, "
            f"got {interval_minutes!r}."
        )
    start, end = _check_range(start, end)
    clauses, params = _range_clauses(start, end)

    seconds = interval_minutes * 60
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', (strftime('%s', timestamp) / ?) * ?, 'unixepoch')"
        "         AS window_start,"
        "       service,"
        "       COALESCE(SUM(level = 'ERROR'), 0) AS error_lines,"
        "       COUNT(DISTINCT request_id) AS requests,"
        "       COUNT(DISTINCT CASE WHEN level = 'ERROR' THEN request_id END) AS failed_requests "
        f"FROM logs {_where(clauses)} "
        "GROUP BY window_start, service ORDER BY window_start, service LIMIT ?",
        [seconds, seconds, *params, MAX_LIMIT + 1],
    ).fetchall()

    truncated = len(rows) > MAX_LIMIT
    windows = []
    for row in rows[:MAX_LIMIT]:
        window = dict(row)
        requests = window["requests"]
        window["error_rate"] = round(window["failed_requests"] / requests, 4) if requests else None
        windows.append(window)
    result: dict[str, Any] = {
        "interval_minutes": interval_minutes,
        "windows": windows,
        "returned": len(windows),
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (
            f"Only the first {MAX_LIMIT} windows are shown. Narrow start/end or use a larger "
            "interval_minutes."
        )
    return result


def get_deployments(
    conn: sqlite3.Connection,
    service: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    service = _check_service(service)
    start, end = _check_range(start, end)
    clauses, params = _range_clauses(start, end)
    if service:
        clauses.append("service = ?")
        params.append(service)
    rows = conn.execute(
        "SELECT timestamp, service, version, author, commit_message FROM deployments "
        f"{_where(clauses)} ORDER BY timestamp, id LIMIT ?",
        [*params, MAX_LIMIT],
    ).fetchall()
    return {"deployments": [dict(row) for row in rows], "returned": len(rows)}


def get_stacktrace(conn: sqlite3.Connection, error_id: int) -> dict[str, Any]:
    if isinstance(error_id, bool) or not isinstance(error_id, int) or error_id < 1:
        raise QueryError(f"error_id must be a positive integer, got {error_id!r}.")
    row = conn.execute(
        "SELECT id, timestamp, service, exception_type, message, stacktrace, request_context "
        "FROM errors WHERE id = ?",
        (error_id,),
    ).fetchone()
    if row is None:
        raise QueryError(
            f"No error with id {error_id}. Error ids appear in the error_id field of "
            "search_logs results."
        )
    result = dict(row)
    try:
        result["request_context"] = json.loads(result["request_context"])
    except json.JSONDecodeError:
        pass  # keep the raw text
    return result
