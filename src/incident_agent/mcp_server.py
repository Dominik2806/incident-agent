"""MCP server "incident-logs": a thin, read-only layer over `incident_agent.queries`.

The server talks over stdio, so stdout carries the protocol. Never print to stdout here;
logging goes to stderr.
"""

import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from incident_agent import queries

DEFAULT_DB_PATH = "data/incident.db"
DB_PATH_ENV = "INCIDENT_DB_PATH"

Service = Literal["checkout", "payment", "catalog", "auth"]
Level = Literal["DEBUG", "INFO", "WARN", "ERROR"]

mcp = FastMCP(
    "incident-logs",
    instructions=(
        "Read-only access to the production logs, deployments and error stack traces of the "
        "services checkout, payment, catalog and auth. The data covers one day, 2026-03-12 "
        "(UTC). To investigate an incident: get_error_stats to find when errors began, "
        "get_deployments to see what changed around then, search_logs for details, and "
        "get_stacktrace with an error_id from the logs."
    ),
)


def db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH))


def _run(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
    """Run a query on a fresh read-only connection; turn failures into short error messages."""
    try:
        conn = queries.open_readonly(db_path())
        try:
            return fn(conn)
        finally:
            conn.close()
    except queries.QueryError as exc:
        raise ValueError(str(exc)) from None
    except sqlite3.Error as exc:
        logging.exception("database error")
        raise ValueError(f"Database error: {exc}") from None


@mcp.tool()
def search_logs(
    service: Service | None = None,
    level: Level | None = None,
    start: str | None = None,
    end: str | None = None,
    query: str | None = None,
    limit: int = queries.DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Search log lines, ordered by time (oldest first).

    Use it for details once get_error_stats has shown when and where errors began, e.g. level
    ERROR for one service in a short window. Log lines of failed requests carry an error_id
    that can be passed to get_stacktrace.

    The data covers one day, 2026-03-12 (all times UTC). All filters are optional and combined
    with AND; without filters the whole day matches.

    Args:
        service: Only this service: checkout, payment, catalog or auth.
        level: Only this log level: DEBUG, INFO, WARN or ERROR.
        start: Earliest time, inclusive, ISO 8601 UTC, e.g. 2026-03-12T14:00:00Z
            (a date like 2026-03-12 also works).
        end: Latest time, exclusive, same format as start.
        query: Case-insensitive text that must appear in the message (plain substring, no
            wildcards), e.g. "KeyError" or "timeout".
        limit: Maximum lines to return, default 50, hard maximum 200.

    Returns:
        logs (timestamp, service, level, message, request_id, error_id), returned,
        total_matches (all lines matching the filters) and truncated (true if more lines
        matched than were returned; narrow the filters or time range to see them).
    """
    return _run(lambda c: queries.search_logs(c, service, level, start, end, query, limit))


@mcp.tool()
def get_error_stats(
    start: str | None = None,
    end: str | None = None,
    interval_minutes: int = 5,
) -> dict[str, Any]:
    """Error counts and error rate per service and time window. Start here to find when
    errors began and which services are affected.

    The data covers one day, 2026-03-12 (all times UTC). Windows are aligned to the interval
    (e.g. 14:00, 14:05, ...). Only windows that contain log lines are returned, ordered by
    window start and then service. At most 200 windows are returned; use a larger interval or
    a narrower time range for a long period.

    A request is a distinct request_id seen in that service's logs; a failed request is one
    with at least one ERROR line in that service.

    Args:
        start: Earliest time, inclusive, ISO 8601 UTC, e.g. 2026-03-12T13:00:00Z (a date like
            2026-03-12 also works). Default: start of the data.
        end: Latest time, exclusive, same format as start. Default: end of the data.
        interval_minutes: Window size, one of 1, 5, 15, 30, 60. Default 5.

    Returns:
        windows, each with window_start, service, error_lines (number of ERROR lines),
        requests, failed_requests and error_rate (failed_requests / requests, null if the
        window has no requests), plus returned and truncated.
    """
    return _run(lambda c: queries.get_error_stats(c, start, end, interval_minutes))


@mcp.tool()
def get_deployments(
    service: Service | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """List deployments, ordered by time (oldest first). Use it to see what changed shortly
    before errors began.

    The data covers one day, 2026-03-12 (all times UTC). All filters are optional.

    Args:
        service: Only this service: checkout, payment, catalog or auth.
        start: Earliest time, inclusive, ISO 8601 UTC, e.g. 2026-03-12T12:00:00Z (a date like
            2026-03-12 also works).
        end: Latest time, exclusive, same format as start.

    Returns:
        deployments (timestamp, service, version, author, commit_message) and returned.
    """
    return _run(lambda c: queries.get_deployments(c, service, start, end))


@mcp.tool()
def get_stacktrace(error_id: int) -> dict[str, Any]:
    """Get the full stack trace and request context of one error.

    Error ids come from the error_id field of search_logs results (ERROR lines). Errors of the
    same kind share their exception type and stack trace, so one example is usually enough.

    Args:
        error_id: Id of the error, a positive integer.

    Returns:
        id, timestamp (UTC), service, exception_type, message, stacktrace and request_context
        (an object with request_id, endpoint, client_version and http_status). An unknown id
        returns an error message.
    """
    return _run(lambda c: queries.get_stacktrace(c, error_id))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
