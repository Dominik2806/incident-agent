"""Command line entry point: `score-report`."""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path

from incident_agent.data.constants import DEFAULT_SEED
from incident_agent.data.database import build_database
from incident_agent.evaluation.expected import EXPECTED, ground_truth
from incident_agent.evaluation.report import default_template
from incident_agent.evaluation.scoring import format_score, score_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="score-report",
        description="Score an incident report against the expected result of a scenario "
        "(no model is called; exit 0 if all gating checks pass, 1 otherwise).",
    )
    parser.add_argument("report", type=Path, help="the incident report (Markdown)")
    parser.add_argument("--scenario", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed of the data")
    parser.add_argument("--db", type=Path, help="database to read the ground truth from")
    parser.add_argument("--template", type=Path, default=default_template())
    args = parser.parse_args(argv)

    try:
        text = args.report.read_text(encoding="utf-8")
        template = args.template.read_text(encoding="utf-8") if args.template.exists() else None
    except OSError as error:
        parser.error(str(error))
    with tempfile.TemporaryDirectory() as tmp:
        path = args.db or build_database(args.scenario, args.seed, Path(tmp) / "incident.db")
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            truth = ground_truth(conn, args.scenario)
        finally:
            conn.close()
    score = score_report(text, args.scenario, truth, template)
    print(format_score(score))
    return 0 if score.passed else 1
