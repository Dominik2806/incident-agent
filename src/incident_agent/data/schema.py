"""SQLite schema for the sample incident database."""

import sqlite3

SCHEMA = """
CREATE TABLE errors (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    exception_type TEXT NOT NULL,
    message TEXT NOT NULL,
    stacktrace TEXT NOT NULL,
    request_context TEXT NOT NULL  -- JSON: request_id, endpoint, client_version, http_status
);

CREATE TABLE logs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    request_id TEXT,
    error_id INTEGER REFERENCES errors(id)
);

CREATE TABLE deployments (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    version TEXT NOT NULL,
    author TEXT NOT NULL,
    commit_message TEXT NOT NULL
);

CREATE INDEX idx_logs_timestamp ON logs(timestamp);
CREATE INDEX idx_logs_service_level ON logs(service, level);
CREATE INDEX idx_errors_timestamp ON errors(timestamp);
CREATE INDEX idx_deployments_service ON deployments(service, timestamp);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
