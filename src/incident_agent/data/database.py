"""Build the SQLite database from generated data."""

import json
import random
import sqlite3
from pathlib import Path

from incident_agent.data.constants import DEFAULT_OUTPUT, DEFAULT_SCENARIO, DEFAULT_SEED, iso
from incident_agent.data.models import GeneratedData
from incident_agent.data.noise import add_noise
from incident_agent.data.normal import generate_normal_operation
from incident_agent.data.scenarios import NOISE_SKIP_ONE_OFFS, SCENARIOS
from incident_agent.data.schema import create_schema


def generate(scenario: str, seed: int) -> GeneratedData:
    """Run normal operation, noise and the scenario with one seeded random generator."""
    rng = random.Random(seed)
    data = generate_normal_operation(rng)
    add_noise(rng, data, NOISE_SKIP_ONE_OFFS.get(scenario, frozenset()))
    SCENARIOS[scenario](rng, data)
    return data


def write_database(data: GeneratedData, conn: sqlite3.Connection) -> None:
    """Assign chronological ids and insert all rows."""
    errors = sorted(enumerate(data.errors, start=1), key=lambda p: (p[1].timestamp, p[0]))
    new_error_id = {old: new for new, (old, _) in enumerate(errors, start=1)}
    deployments = sorted(data.deployments, key=lambda d: d.timestamp)
    logs = sorted(data.logs, key=lambda log: log.timestamp)  # stable: keeps emission order

    conn.executemany(
        "INSERT INTO errors VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                new_error_id[old],
                iso(e.timestamp),
                e.service,
                e.exception_type,
                e.message,
                e.stacktrace,
                json.dumps(e.request_context),
            )
            for old, e in errors
        ],
    )
    conn.executemany(
        "INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?)",
        [
            (i, iso(d.timestamp), d.service, d.version, d.author, d.commit_message)
            for i, d in enumerate(deployments, start=1)
        ],
    )
    conn.executemany(
        "INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i,
                iso(log.timestamp),
                log.service,
                log.level,
                log.message,
                log.request_id,
                None if log.error_id is None else new_error_id[log.error_id],
            )
            for i, log in enumerate(logs, start=1)
        ],
    )


def build_database(
    scenario: str = DEFAULT_SCENARIO,
    seed: int = DEFAULT_SEED,
    output: Path = DEFAULT_OUTPUT,
) -> Path:
    """Create (or overwrite) the database file and return its path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    data = generate(scenario, seed)
    conn = sqlite3.connect(output)
    try:
        create_schema(conn)
        write_database(data, conn)
        conn.commit()
    finally:
        conn.close()
    return output
