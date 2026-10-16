"""Human-readable summary of a generated database."""

import sqlite3

from incident_agent.data.constants import SERVICES


def summarize(conn: sqlite3.Connection) -> str:
    lines = ["Row counts:"]
    for table in ("logs", "deployments", "errors"):
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        lines.append(f"  {table:<12}{count:>7}")

    rows = conn.execute(
        "SELECT substr(timestamp, 12, 2), service, COUNT(*) FROM logs "
        "WHERE level = 'ERROR' GROUP BY 1, 2"
    ).fetchall()
    counts = {(hour, service): n for hour, service, n in rows}

    lines += ["", "ERROR logs per hour (UTC):"]
    lines.append("  hour  " + "".join(f"{s:>10}" for s in SERVICES) + f"{'total':>10}")
    for hour in range(24):
        key = f"{hour:02d}"
        per_service = [counts.get((key, s), 0) for s in SERVICES]
        lines.append(
            f"  {key}:00 " + "".join(f"{n:>10}" for n in per_service) + f"{sum(per_service):>10}"
        )
    return "\n".join(lines)
