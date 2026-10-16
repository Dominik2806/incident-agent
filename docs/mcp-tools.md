# MCP tools reference

The MCP server `incident-logs` (`src/incident_agent/mcp_server.py`) gives Claude read-only access to the sample
database from [Phase 1](scenarios.md). The tool logic lives in `src/incident_agent/queries.py`; the server only
validates input, calls a query function and returns the result.

## General rules

- **Data:** one day, **2026-03-12 (UTC)**, for the services `checkout`, `payment`, `catalog` and `auth`.
- **Timestamps:** ISO 8601 in UTC, e.g. `2026-03-12T14:03:00Z`. A plain date (`2026-03-12`) is accepted and means
  00:00 UTC. Other time zone offsets are rejected. `start` is inclusive, `end` is exclusive. All time parameters
  are optional and default to the whole dataset.
- **Levels:** `DEBUG`, `INFO`, `WARN`, `ERROR`. **Services:** `checkout`, `payment`, `catalog`, `auth`.
- **Limits:** at most 200 rows per call. `search_logs` defaults to 50 rows and reports `total_matches` and
  `truncated`, so Claude knows when to narrow the search.
- **Read-only:** the database is opened with SQLite `mode=ro`; SQL uses parameters only.
- **Errors:** invalid input, an unknown `error_id` or a missing database file produce a tool error with a short,
  actionable message (not a stack trace). If the database does not exist, the message says to run
  `uv run generate-data --scenario checkout-500`.
- **Database path:** `data/incident.db`, or the file named in the environment variable `INCIDENT_DB_PATH`.

Typical workflow: `get_error_stats` to find when errors began, `get_deployments` to see what changed around that
time, `search_logs` for details, `get_stacktrace` with an `error_id` from the logs.

## `get_error_stats`

Error counts and error rate per service and time window. Start here to find when errors began and which services
are affected.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `start` | string | start of data | Earliest time (inclusive) |
| `end` | string | end of data | Latest time (exclusive) |
| `interval_minutes` | integer | `5` | Window size: one of `1`, `5`, `15`, `30`, `60` |

A request is a distinct `request_id` seen in that service's logs. A failed request is one with at least one
`ERROR` line in that service. Only windows that contain data are returned, ordered by window start and service.
At most 200 windows are returned (`truncated: true` if there are more; use a larger interval or a narrower range).

```json
{
  "interval_minutes": 5,
  "windows": [
    {"window_start": "2026-03-12T14:05:00Z", "service": "checkout", "error_lines": 4,
     "requests": 19, "failed_requests": 4, "error_rate": 0.2105}
  ],
  "returned": 1,
  "truncated": false
}
```

`error_rate` is `failed_requests / requests` (`null` if the window has no requests, e.g. only request-less lines).

## `get_deployments`

Deployments ordered by time. Use it to see what changed shortly before errors began.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `service` | string | all | Only this service |
| `start` | string | start of data | Earliest time (inclusive) |
| `end` | string | end of data | Latest time (exclusive) |

Returns `deployments` (each with `timestamp`, `service`, `version`, `author`, `commit_message`) and `returned`.

## `search_logs`

Log lines ordered by time (oldest first).

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `service` | string | all | Only this service |
| `level` | string | all | Only this log level |
| `start` | string | start of data | Earliest time (inclusive) |
| `end` | string | end of data | Latest time (exclusive) |
| `query` | string | none | Case-insensitive substring of the message. `%`, `_` and `\` are matched literally |
| `limit` | integer | `50` | Maximum lines to return, at most `200` |

```json
{
  "logs": [
    {"timestamp": "2026-03-12T14:03:13Z", "service": "payment", "level": "ERROR",
     "message": "Unhandled exception in charge: KeyError: 'currency'",
     "request_id": "req-4a7979fda4", "error_id": 6}
  ],
  "returned": 1,
  "total_matches": 435,
  "truncated": true,
  "limit": 1
}
```

`total_matches` counts all lines matching the filters; `truncated` is `true` if more lines matched than were returned.
Lines of failed requests carry an `error_id` for `get_stacktrace`.

## `get_stacktrace`

Full stack trace and request context of one error.

| Parameter | Type | Meaning |
|---|---|---|
| `error_id` | integer | Id from the `error_id` field of `search_logs` results |

Returns `id`, `timestamp`, `service`, `exception_type`, `message`, `stacktrace` and `request_context`, an object with
`request_id`, `endpoint`, `client_version` and `http_status`. An unknown id returns the error
`No error with id <n>. Error ids appear in the error_id field of search_logs results.`
