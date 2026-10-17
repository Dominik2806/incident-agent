"""Pure logic of the live runner: prompts, the `claude` command, results and the verdict.

Nothing here starts a process or touches the file system (a test enforces it); `runner.py` does
that.
Every flag was checked against `claude --help` of Claude Code 2.1.302. There is no `--max-turns` in
that version: a run is capped by `--max-budget-usd` and by the wall-clock timeout of the runner.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from incident_agent.evaluation.checks import CheckResult

# The demo prompts of docs/scenarios.md (a test compares them). They do not name the cause.
PROMPTS: dict[str, str] = {
    "checkout-500": "Since about 2 pm customers are getting 500 errors at checkout. "
    "What is going on?",
    "db-pool": "Since mid-afternoon (around 3 pm UTC) product searches have been slow or failing "
    "for some customers. What is going on?",
}

# Conservative defaults, to be lowered after the first measured run.
DEFAULT_RUNS = 1
DEFAULT_MAX_BUDGET_USD = 1.00
DEFAULT_TOTAL_BUDGET_USD = 3.00
DEFAULT_TIMEOUT_S = 900

MCP_SERVER_NAME = "incident-logs"
# Built-in tools that exist at all in a run: no Bash, no Edit. Subagents use their own `tools`.
TOOLS = ("Skill", "Agent", "Read", "Grep", "Glob", "Write")
# Pre-allowed; with `--permission-mode dontAsk` everything else is denied. File writes are
# checked against Edit(path) rules only (a Write(path) rule is accepted but never consulted).
ALLOWED_TOOLS = (
    "Skill",
    "Agent",
    "Read",
    "Grep",
    "Glob",
    "Edit(reports/**)",
    f"mcp__{MCP_SERVER_NAME}__*",
)

# Checks that must pass in every run; every other gating check needs 4 of 5 (80%).
STRICT_CHECKS = ("root-cause-named", "severity")
SOFT_NUMERATOR, SOFT_DENOMINATOR = 4, 5

SECRET_ENV = ("SLACK_WEBHOOK_URL",)


def build_command(
    claude: Sequence[str], mcp_config: Path, max_budget_usd: float, model: str | None = None
) -> list[str]:
    """The `claude -p` argv. The prompt is not part of it: it goes to stdin, because
    `--allowedTools`, `--tools` and `--mcp-config` are variadic and would swallow a positional."""
    command = [
        *claude,
        "-p",
        "--output-format",
        "json",
        "--max-budget-usd",
        f"{max_budget_usd:.2f}",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--setting-sources",
        "project",
        "--tools",
        ",".join(TOOLS),
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
    ]
    if model:
        command += ["--model", model]
    return command


def build_mcp_config(server_command: Sequence[str], db_path: Path) -> dict:
    """The per-run MCP config. The server keeps the name the agents' tool names are built from."""
    command, *args = server_command
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": command,
                "args": list(args),
                "env": {"INCIDENT_DB_PATH": str(db_path)},
            }
        }
    }


def env_overrides(db_path: Path, audit_log: Path) -> dict[str, str]:
    return {
        "INCIDENT_DB_PATH": str(db_path),
        "INCIDENT_AUDIT_LOG": str(audit_log),
        "INCIDENT_NOTIFY_DESKTOP": "0",
    }


def run_env(base: Mapping[str, str], db_path: Path, audit_log: Path) -> dict[str, str]:
    """Environment of the claude process: notifications off, no webhook, own DB and audit log."""
    env = {k: v for k, v in base.items() if k not in SECRET_ENV}
    env.update(env_overrides(db_path, audit_log))
    return env


@dataclass(frozen=True)
class ClaudeResult:
    cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None
    is_error: bool = False
    subtype: str = ""
    parsed: bool = False


def parse_result(text: str) -> ClaudeResult:
    """Read the `--output-format json` result. Unknown or missing fields become None."""
    try:
        data = json.loads(text)
    except ValueError:
        return ClaudeResult()
    if isinstance(data, list):  # some versions emit a list of events; the result is the last one
        data = next(
            (e for e in reversed(data) if isinstance(e, dict) and e.get("type") == "result"), None
        )
    if not isinstance(data, dict):
        return ClaudeResult()

    def number(name: str, kind: type):
        value = data.get(name)
        return (
            kind(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        )

    return ClaudeResult(
        cost_usd=number("total_cost_usd", float),
        turns=number("num_turns", int),
        duration_ms=number("duration_ms", int),
        is_error=bool(data.get("is_error")),
        subtype=str(data.get("subtype") or ""),
        parsed=True,
    )


@dataclass(frozen=True)
class RunRecord:
    scenario: str
    run: int
    status: str  # "scored", "failed" or "skipped"
    reason: str = ""
    cost_usd: float | None = None
    turns: int | None = None
    duration_s: float = 0.0
    checks: tuple[CheckResult, ...] = ()

    @property
    def executed(self) -> bool:
        return self.status != "skipped"


@dataclass(frozen=True)
class CheckStat:
    name: str
    gating: bool
    passes: int
    total: int

    @property
    def required(self) -> str:
        if not self.gating:
            return "warning"
        return "100%" if self.name in STRICT_CHECKS else "80%"

    @property
    def ok(self) -> bool:
        if not self.gating:
            return True
        if self.name in STRICT_CHECKS:
            return self.passes == self.total
        return self.passes * SOFT_DENOMINATOR >= self.total * SOFT_NUMERATOR


@dataclass(frozen=True)
class ScenarioVerdict:
    scenario: str
    executed: int
    skipped: int
    failed_runs: tuple[RunRecord, ...]
    stats: tuple[CheckStat, ...]
    passed: bool
    incomplete: bool

    @property
    def label(self) -> str:
        if self.incomplete:
            return "INCOMPLETE"
        return "PASS" if self.passed else "FAIL"


def verdict(scenario: str, records: Sequence[RunRecord]) -> ScenarioVerdict:
    """Pass rates over all executed runs; a failed run (no report, error, timeout) fails all.

    A scenario passes if `root-cause-named` and `severity` pass in 100% of the runs and every other
    gating check in at least 80% (exact integer arithmetic: N=1 and N=3 need every run, N=5 allows
    one miss). Warnings are counted but never change the verdict. Skipped runs make it INCOMPLETE.
    """
    executed = [r for r in records if r.executed]
    skipped = len(records) - len(executed)
    scored = [r for r in executed if r.status == "scored"]
    order: dict[str, bool] = {}
    for record in scored:
        for result in record.checks:
            order.setdefault(result.name, result.gating)
    stats = tuple(
        CheckStat(
            name=name,
            gating=gating,
            passes=sum(1 for r in scored for c in r.checks if c.name == name and c.passed),
            total=len(executed),
        )
        for name, gating in order.items()
    )
    passed = bool(scored) and all(s.ok for s in stats) and skipped == 0
    # A strict check that is missing entirely (no run was scored) cannot pass either.
    return ScenarioVerdict(
        scenario=scenario,
        executed=len(executed),
        skipped=skipped,
        failed_runs=tuple(r for r in executed if r.status == "failed"),
        stats=stats,
        passed=passed,
        incomplete=skipped > 0,
    )


def planned_max_cost(runs: int, scenarios: int, max_budget_usd: float, total_budget_usd: float):
    return min(runs * scenarios * max_budget_usd, total_budget_usd)


def plan_line(
    runs: int, scenarios: Sequence[str], max_budget_usd: float, total_budget_usd: float
) -> str:
    maximum = planned_max_cost(runs, len(scenarios), max_budget_usd, total_budget_usd)
    return (
        f"Planned: {runs} run(s) x {len(scenarios)} scenario(s) ({', '.join(scenarios)}), "
        f"up to ${max_budget_usd:.2f} per run, maximum ${maximum:.2f} "
        f"(total cap ${total_budget_usd:.2f})"
    )


@dataclass
class Totals:
    cost_usd: float = 0.0
    unknown_cost_runs: int = 0
    turns: int = 0
    duration_s: float = 0.0
    runs: int = 0
    extra: dict = field(default_factory=dict)


def totals(records: Sequence[RunRecord]) -> Totals:
    total = Totals()
    for r in records:
        if not r.executed:
            continue
        total.runs += 1
        total.duration_s += r.duration_s
        total.turns += r.turns or 0
        if r.cost_usd is None:
            total.unknown_cost_runs += 1
        else:
            total.cost_usd += r.cost_usd
    return total


def format_summary(
    verdicts: Sequence[ScenarioVerdict], records: Sequence[RunRecord], model: str | None = None
) -> str:
    lines: list[str] = []
    for v in verdicts:
        lines.append(f"Scenario: {v.scenario}  ({v.executed} run(s) executed, {v.skipped} skipped)")
        for run in (r for r in records if r.scenario == v.scenario and r.executed):
            cost = "?" if run.cost_usd is None else f"${run.cost_usd:.2f}"
            detail = f"  ({run.reason})" if run.reason else ""
            lines.append(
                f"  run-{run.run}: {run.status.upper():<6} cost {cost}, "
                f"turns {'?' if run.turns is None else run.turns}, {run.duration_s:.0f}s{detail}"
            )
        gating = [s for s in v.stats if s.gating]
        for s in gating:
            status = "ok  " if s.ok else "FAIL"
            lines.append(
                f"  {status}  {s.name:<24} {s.passes}/{s.total} passed (required {s.required})"
            )
        warnings = [s for s in v.stats if not s.gating]
        if warnings:
            lines.append("  Warnings (do not affect the verdict):")
            for s in warnings:
                lines.append(f"    {s.name:<24} {s.passes}/{s.total} passed")
        if not v.stats:
            lines.append("  no run produced a scorable report")
        if v.incomplete:
            lines.append("  stopped early by the total budget; skipped runs are not scored")
        lines.append(f"  VERDICT {v.scenario}: {v.label}")
    total = totals(records)
    unknown = (
        f" (+{total.unknown_cost_runs} run(s) with unknown cost)" if total.unknown_cost_runs else ""
    )
    lines.append(
        f"Totals: {total.runs} run(s), cost ${total.cost_usd:.2f}{unknown}, "
        f"turns {total.turns}, duration {total.duration_s:.0f}s"
        + (f", model {model}" if model else "")
    )
    overall = all(v.passed for v in verdicts)
    lines.append("RESULT: " + ("PASS" if overall else "FAIL"))
    return "\n".join(lines)
