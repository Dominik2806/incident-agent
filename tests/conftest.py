import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from incident_agent.data.database import build_database
from incident_agent.queries import open_readonly


@pytest.fixture(scope="session")
def incident_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database built by the Phase 1 generator (checkout-500, seed 42)."""
    return build_database("checkout-500", 42, tmp_path_factory.mktemp("incident") / "incident.db")


@pytest.fixture
def conn(incident_db: Path) -> Iterator[sqlite3.Connection]:
    connection = open_readonly(incident_db)
    yield connection
    connection.close()
