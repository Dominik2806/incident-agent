"""Command line entry point: `run-eval` (local only, calls a model, costs money)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from incident_agent.evaluation import runplan
from incident_agent.evaluation.expected import EXPECTED
from incident_agent.evaluation.runner import (
    ClaudeNotFound,
    ExecFn,
    RunConfig,
    dry_run_text,
    find_claude,
    run_eval,
    subprocess_exec,
    timestamp_now,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def main(
    argv: Sequence[str] | None = None,
    exec_fn: ExecFn = subprocess_exec,
    ask: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="run-eval",
        description="Run the incident-runbook flow headlessly with `claude -p` N times per "
        "scenario "
        "against a fresh database, score each report and print pass rates and a verdict. "
        "Local only: it calls a model and costs money. "
        "Exit 0 if every scenario passes, 1 otherwise (FAIL, INCOMPLETE, CONTAMINATED). "
        "A Claude usage limit stops the plan and is INCOMPLETE, not FAIL.",
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--scenario", choices=sorted(EXPECTED))
    which.add_argument("--all", action="store_true", help="every scenario")
    parser.add_argument("--runs", type=int, default=runplan.DEFAULT_RUNS, help="runs per scenario")
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=runplan.DEFAULT_MAX_BUDGET_USD,
        help="hard cap per run (claude --max-budget-usd)",
    )
    parser.add_argument(
        "--total-budget-usd",
        type=float,
        default=runplan.DEFAULT_TOTAL_BUDGET_USD,
        help="stop starting runs when the next one could exceed this total",
    )
    parser.add_argument("--timeout-s", type=float, default=runplan.DEFAULT_TIMEOUT_S)
    parser.add_argument("--model", help="passed to claude --model (default: the CLI default)")
    parser.add_argument("--claude-path", help="claude executable (default: $CLAUDE_BIN, then PATH)")
    parser.add_argument("--results-dir", type=Path, default=PROJECT_ROOT / "evals" / "results")
    parser.add_argument("--dry-run", action="store_true", help="print the commands, call nothing")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.max_budget_usd <= 0 or args.total_budget_usd <= 0:
        parser.error("budgets must be positive")
    scenarios = sorted(EXPECTED) if args.all else [args.scenario]

    try:
        claude = find_claude(args.claude_path)
    except ClaudeNotFound as error:
        if not args.dry_run:
            parser.error(str(error))
        claude = ["claude"]
        print(f"note: {error}; showing 'claude' in the commands", file=sys.stderr)
    config = RunConfig(
        project_root=PROJECT_ROOT,
        claude=claude,
        max_budget_usd=args.max_budget_usd,
        timeout_s=args.timeout_s,
        model=args.model,
    )
    line = runplan.plan_line(args.runs, scenarios, args.max_budget_usd, args.total_budget_usd)

    if args.dry_run:
        print(line)
        print(dry_run_text(scenarios, args.runs, config, args.results_dir, timestamp_now()))
        return 0

    print(line)
    if not args.yes:
        tty = sys.stdin.isatty() if interactive is None else interactive
        if not tty:
            print("Not a terminal: pass --yes to run without a confirmation.", file=sys.stderr)
            return 1
        if ask("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Aborted, nothing was run.")
            return 1

    verdicts, records, session = run_eval(
        scenarios, args.runs, config, args.results_dir, args.total_budget_usd, exec_fn
    )
    print()
    print(runplan.format_summary(verdicts, records, args.model))
    print(f"Results: {session}")
    return 0 if all(v.passed for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
