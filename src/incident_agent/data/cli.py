"""Command line entry point: `generate-data`."""

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from incident_agent.data.constants import DEFAULT_OUTPUT, DEFAULT_SCENARIO, DEFAULT_SEED
from incident_agent.data.database import build_database
from incident_agent.data.scenarios import SCENARIOS
from incident_agent.data.summary import summarize


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate-data", description="Generate the sample incident database."
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=DEFAULT_SCENARIO)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    path = build_database(args.scenario, args.seed, args.output)
    print(f"Generated scenario '{args.scenario}' (seed {args.seed}) at {path}\n")
    conn = sqlite3.connect(path)
    try:
        print(summarize(conn))
    finally:
        conn.close()
    return 0
