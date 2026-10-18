"""Expected result per scenario, and the ground truth read from the generated database.

Every scenario in `SCENARIOS` needs one entry in `EXPECTED` (a test enforces it). The values come
from `docs/scenarios.md`; patterns are case-insensitive regular expressions.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

DAY = "2026-03-12T"


@dataclass(frozen=True)
class ExpectedResult:
    scenario: str
    severity: str
    # Every pattern must match in the "Root cause" section.
    cause_patterns: tuple[str, ...]
    # None of these may match in the "Root cause" section (they may appear in "Ruled out").
    forbidden_cause_patterns: tuple[str, ...]
    # Every pattern must match in the "Ruled out" section.
    red_herring_patterns: tuple[str, ...]
    # checkout-500: per-client ok/failed counts are compared with the database.
    # db-pool, cert-expired, api-outage: no clients; the total number of failed requests must
    # appear in the report.
    clients: tuple[str, ...] = ()
    # False: counts-reconciled is reported as a warning and does not fail the report.
    reconcile_gating: bool = True


EXPECTED: dict[str, ExpectedResult] = {
    "checkout-500": ExpectedResult(
        scenario="checkout-500",
        severity="SEV-2",
        # KeyError, or the same idea in words: the key is read "with no default".
        cause_patterns=(
            r"payment",
            r"v1\.4\.2",
            r"KeyError|\b(?:no|without)\s+(?:a\s+|any\s+)?default",
            r"currency",
        ),
        forbidden_cause_patterns=(r"catalog\s+v?2\.8\.0", r"auth\s+v?3\.1\.1"),
        red_herring_patterns=(r"v2\.8\.0|v3\.1\.1",),
        clients=("web-2.31.0", "ios-5.8.1", "web-2.30.4", "android-5.7.9"),
        reconcile_gating=True,
    ),
    "db-pool": ExpectedResult(
        scenario="db-pool",
        severity="SEV-3",
        cause_patterns=(r"catalog", r"pool", r"exhaust", r"search"),
        forbidden_cause_patterns=(r"v?3\.2\.0",),
        red_herring_patterns=(r"v?3\.2\.0",),
        reconcile_gating=False,
    ),
    "cert-expired": ExpectedResult(
        scenario="cert-expired",
        severity="SEV-1",
        cause_patterns=(r"certificate", r"expire", r"payment", r"TLS|x509|SSL"),
        forbidden_cause_patterns=(r"v?3\.3\.0",),
        red_herring_patterns=(r"v?3\.3\.0",),
        reconcile_gating=False,
    ),
    "api-outage": ExpectedResult(
        scenario="api-outage",
        severity="SEV-2",
        # The provider, its outage in plain words (503, unavailable, timeout), that it is external.
        cause_patterns=(
            r"Payvia",
            r"outage|unavailable|503|time[ds]?[ -]?out",
            r"provider|third-party|upstream|external|processor",
        ),
        forbidden_cause_patterns=(r"v?2\.7\.1",),
        red_herring_patterns=(r"v?2\.7\.1",),
        reconcile_gating=False,
    ),
}

# Scenarios without a per-client split: the failed requests are counted in the errors table.
TOTAL_FAILED_SQL: dict[str, str] = {
    "db-pool": (
        "SELECT COUNT(*) FROM errors WHERE service = 'catalog' "
        "AND message LIKE '%waiting for a connection from the pool%'"
    ),
    "cert-expired": (
        "SELECT COUNT(*) FROM errors WHERE service = 'checkout' "
        "AND message LIKE '%certificate has expired%'"
    ),
    "api-outage": (
        "SELECT COUNT(*) FROM errors WHERE service = 'payment' AND message LIKE '%Payvia%'"
    ),
}

# Where counting starts. The agent may use the deployment time, a round time or the first error.
FIXED_STARTS = ("14:00:00", "14:01:00", "14:03:00")
CLIENT = re.compile(r"client=(\S+)\s*$")


@dataclass(frozen=True)
class GroundTruth:
    """Numbers computed from the generated database (seed 42 unless told otherwise)."""

    # start time "HH:MM:SS" -> client -> (ok, failed); checkout-500 only.
    windows: dict[str, dict[str, tuple[int, int]]]
    # Failed requests of the incident: KeyError lines (checkout-500), pool timeouts (db-pool),
    # expired-certificate handshakes (cert-expired) or provider failures (api-outage).
    total_failed: int


def ground_truth(conn: sqlite3.Connection, scenario: str) -> GroundTruth:
    if scenario in TOTAL_FAILED_SQL:
        total = conn.execute(TOTAL_FAILED_SQL[scenario]).fetchone()[0]
        return GroundTruth(windows={}, total_failed=total)
    rows = conn.execute(
        "SELECT timestamp, level, message FROM logs WHERE service = 'checkout' "
        "AND timestamp >= ? AND message LIKE '%client=%' ORDER BY timestamp",
        (DAY + "14:00:00Z",),
    ).fetchall()
    first_error = next(
        (t[11:19] for t, lvl, msg in rows if lvl == "ERROR" and "KeyError" in msg), ""
    )
    starts = tuple(dict.fromkeys((*FIXED_STARTS, first_error) if first_error else FIXED_STARTS))
    windows: dict[str, dict[str, tuple[int, int]]] = {}
    for start in starts:
        counts: dict[str, list[int]] = {}
        for timestamp, level, message in rows:
            match = CLIENT.search(message)
            if timestamp[11:19] < start or not match:
                continue
            entry = counts.setdefault(match.group(1), [0, 0])
            if level == "INFO":
                entry[0] += 1
            elif level == "ERROR" and "KeyError" in message:
                entry[1] += 1
        windows[start] = {client: (ok, failed) for client, (ok, failed) in counts.items()}
    failed = sum(f for _, f in windows[FIXED_STARTS[0]].values())
    return GroundTruth(windows=windows, total_failed=failed)
