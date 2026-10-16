"""Fixed values shared by the generator. Nothing here depends on the current time."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

SERVICES = ("checkout", "payment", "catalog", "auth")

DAY_START = datetime(2026, 3, 12, 0, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)

DEFAULT_SEED = 42
DEFAULT_SCENARIO = "checkout-500"
DEFAULT_OUTPUT = Path("data/incident.db")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A UTC timestamp on the reference date."""
    return DAY_START.replace(hour=hour, minute=minute, second=second)


def iso(moment: datetime) -> str:
    """Format as ISO 8601 UTC with second precision, e.g. 2026-03-12T14:03:07Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
