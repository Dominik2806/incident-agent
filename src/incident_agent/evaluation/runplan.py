"""Pure logic of the live runner: prompts, the `claude` command, results and the verdict.

Nothing here starts a process or touches the file system (a test enforces it); `runner.py` does
that.
Every flag was checked against `claude --help` of Claude Code 2.1.302. There is no `--max-turns` in
that version: a run is capped by `--max-budget-usd` and by the wall-clock timeout of the runner.
"""

from __future__ import annotations

import json
import posixpath
import re
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
    "cert-expired": "Since about 3:20 pm UTC almost every checkout attempt is failing for our "
    "customers. What is going on?",
    "api-outage": "Earlier today, around 4 pm UTC, some customers could not pay at checkout. "
    "What happened?",
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
# Reads are scoped to the working copy (the cwd): `Read(./**)` matches only below the cwd, and Grep
# and Glob are read-only tools that need no rule inside it. A bare Read, Grep or Glob would reach
# the whole disk (probed with Claude Code 2.1.302, see docs/evaluation.md).
ALLOWED_TOOLS = (
    "Skill",
    "Agent",
    "Read(./**)",
    "Edit(reports/**)",
    f"mcp__{MCP_SERVER_NAME}__*",
)

# Answer-key locations, relative to the working copy. A Read, Grep or Glob aimed at one of them
# contaminates a run even though the copy does not hold them.
DENYLIST = ("docs", "src", "tests/fixtures", "evals")
SCAN_TOOLS = ("Read", "Grep", "Glob")

# Checks that must pass in every run; every other gating check needs 4 of 5 (80%).
STRICT_CHECKS = ("root-cause-named", "severity")
SOFT_NUMERATOR, SOFT_DENOMINATOR = 4, 5

SECRET_ENV = ("SLACK_WEBHOOK_URL",)

NOT_RUN_LIMIT = "not run (usage limit)"


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


TRUNCATED = re.compile(r"\.\.\.\[\+\d+ chars\]$")
DRIVE_PREFIX = re.compile(r"^/([a-z])(?=/|$)")
DRIVE_ABSOLUTE = re.compile(r"^[a-z]:/")


def normalize_path(raw: str) -> str:
    """Forward slashes, `/c/..` as `c:/..`, `.` and `..` collapsed, case folded (no file system)."""
    path = raw.replace("\\", "/")
    path = DRIVE_PREFIX.sub(r"\1:", path.casefold(), count=1)
    return posixpath.normpath(path) if path else ""


def is_absolute(path: str) -> bool:
    return path.startswith("/") or DRIVE_ABSOLUTE.match(path) is not None


def classify_target(raw: str, workdir: str) -> str | None:
    """Why a path targets something forbidden: 'outside the working copy', 'answer key (docs/)'
    or None. A relative path is resolved against the working copy, which is the cwd of the run."""
    work = normalize_path(workdir)
    text = TRUNCATED.sub("", raw).replace("\\", "/")
    path = normalize_path(text if is_absolute(text.casefold()) else f"{work}/{text}")
    if path != work and not path.startswith(work + "/"):
        return "outside the working copy"
    relative = path[len(work) + 1 :]
    for entry in DENYLIST:
        if relative == entry or relative.startswith(entry + "/"):
            return f"answer-key location ({entry}/)"
    return None


def escapes(pattern: str) -> bool:
    """A glob that is absolute or climbs out with `..` points beyond the working copy."""
    text = pattern.replace("\\", "/")
    return is_absolute(text.casefold()) or ".." in text.split("/")


def audit_targets(record: dict) -> list[tuple[str, str]]:
    """(field, value) pairs of a Read, Grep or Glob audit record that name a place.

    The audit hook keeps strings up to 300 characters whole and marks longer ones, so the start of a
    path is always visible. A pattern or glob only counts when it is absolute or uses `..`: a
    relative pattern stays below the cwd by construction (and `!{docs,tests}/**` is an exclusion).
    """
    tool, data = record.get("tool_name"), record.get("input")
    if tool not in SCAN_TOOLS or not isinstance(data, dict):
        return []
    fields = {"Read": ("file_path",), "Grep": ("path",), "Glob": ("path",)}[tool]
    found = [(f, data[f]) for f in fields if isinstance(data.get(f), str) and data[f]]
    pattern_field = {"Grep": "glob", "Glob": "pattern"}.get(tool)
    value = data.get(pattern_field) if pattern_field else None
    if isinstance(value, str) and escapes(TRUNCATED.sub("", value)):
        found.append((pattern_field, value))
    return found


def scan_audit(audit_text: str, workdir: str) -> tuple[str, ...]:
    """Evidence lines for every successful Read, Grep or Glob that targeted a place outside the
    working copy or an answer-key location. Calls that were denied never reach the audit log (a
    PostToolUse hook does not run for them), so a hit here is a real leak."""
    hits: list[str] = []
    for line in audit_text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        for name, value in audit_targets(record):
            reason = classify_target(value, workdir)
            if reason:
                hits.append(f"{record['tool_name']} {name}={value} ({reason})")
    return tuple(hits)


def describe_denial(denial: object) -> str:
    """One line for an entry of `permission_denials` of the claude result."""
    if not isinstance(denial, dict):
        return str(denial)
    data = denial.get("tool_input")
    data = data if isinstance(data, dict) else {}
    target = next(
        (str(data[k]) for k in ("file_path", "path", "pattern", "command", "url") if data.get(k)),
        "",
    )
    return f"{denial.get('tool_name', '?')} {target}".strip()


@dataclass(frozen=True)
class ClaudeResult:
    cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None
    is_error: bool = False
    subtype: str = ""
    parsed: bool = False
    denials: tuple[str, ...] = ()
    api_error: str = ""
    api_error_status: int | None = None
    usage_limit: bool = False
    reset_hint: str = ""


USAGE_LIMIT_CODE = "usage_limit_reached"
RESET_HINT = re.compile(r"resets\s+(.+)$")
RESET_HINT_MAX = 80


def extract_reset_hint(text: object) -> str:
    """The reset time from the human text of a usage-limit result ('resets 3:30pm (...)').

    Display only: it is called after the limit was detected from structured fields and an
    unexpected text gives an empty hint, never an error.
    """
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        match = RESET_HINT.search(line.strip())
        if match:
            return match.group(1).strip()[:RESET_HINT_MAX]
    return ""


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

    denials = data.get("permission_denials")
    denials = denials if isinstance(denials, list) else []
    is_error = data.get("is_error") is True
    api_error = data.get("api_error")
    api_error = api_error if isinstance(api_error, str) else ""
    status = data.get("api_error_status")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    # Structured fields only: never the text of the answer or of a report.
    usage_limit = is_error and api_error == USAGE_LIMIT_CODE
    return ClaudeResult(
        cost_usd=number("total_cost_usd", float),
        turns=number("num_turns", int),
        duration_ms=number("duration_ms", int),
        is_error=bool(data.get("is_error")),
        subtype=str(data.get("subtype") or ""),
        parsed=True,
        denials=tuple(describe_denial(d) for d in denials),
        api_error=api_error,
        api_error_status=status,
        usage_limit=usage_limit,
        reset_hint=extract_reset_hint(data.get("result")) if usage_limit else "",
    )


@dataclass(frozen=True)
class RunRecord:
    scenario: str
    run: int
    # "scored", "failed", "usage_limit" (cut by the subscription limit: executed, never scored,
    # not a failure) or "skipped" (not started: total budget or usage limit)
    status: str
    reason: str = ""
    cost_usd: float | None = None
    turns: int | None = None
    duration_s: float = 0.0
    checks: tuple[CheckResult, ...] = ()
    # Successful reads outside the working copy or of an answer-key location: the run is not valid.
    contamination: tuple[str, ...] = ()
    # Calls the permission rules denied. Nothing leaked, so they never change the verdict.
    blocked: tuple[str, ...] = ()
    reset_hint: str = ""

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
    contaminated_runs: tuple[RunRecord, ...] = ()
    limit_runs: tuple[RunRecord, ...] = ()
    not_run_for_limit: int = 0

    @property
    def label(self) -> str:
        if self.contaminated_runs:
            return "CONTAMINATED"
        if self.incomplete:
            return "INCOMPLETE"
        return "PASS" if self.passed else "FAIL"


def verdict(scenario: str, records: Sequence[RunRecord]) -> ScenarioVerdict:
    """Pass rates over all executed runs; a failed run (no report, error, timeout) fails all.

    A scenario passes if `root-cause-named` and `severity` pass in 100% of the runs and every other
    gating check in at least 80% (exact integer arithmetic: N=1 and N=3 need every run, N=5 allows
    one miss). Warnings are counted but never change the verdict. Skipped runs make it INCOMPLETE.
    A run cut by the usage limit is not counted at all and also makes it INCOMPLETE.

    A contaminated run (it read outside the working copy or an answer-key location) counts as
    failing every check and makes the scenario CONTAMINATED: never a pass, whatever its report says.
    """
    started = [r for r in records if r.executed]
    skipped = len(records) - len(started)
    limit_runs = tuple(r for r in started if r.status == "usage_limit")
    not_run_for_limit = sum(1 for r in records if not r.executed and r.reason == NOT_RUN_LIMIT)
    # A run cut by the usage limit measured nothing: it is not in the pass counts.
    executed = [r for r in started if r.status != "usage_limit"]
    scored = [r for r in executed if r.status == "scored"]
    contaminated = tuple(r for r in started if r.contamination)
    valid = [r for r in scored if not r.contamination]
    order: dict[str, bool] = {}
    for record in scored:
        for result in record.checks:
            order.setdefault(result.name, result.gating)
    stats = tuple(
        CheckStat(
            name=name,
            gating=gating,
            passes=sum(1 for r in valid for c in r.checks if c.name == name and c.passed),
            total=len(executed),
        )
        for name, gating in order.items()
    )
    passed = (
        bool(scored)
        and all(s.ok for s in stats)
        and skipped == 0
        and not limit_runs
        and not contaminated
    )
    # A strict check that is missing entirely (no run was scored) cannot pass either.
    return ScenarioVerdict(
        scenario=scenario,
        executed=len(executed),
        skipped=skipped,
        failed_runs=tuple(r for r in executed if r.status == "failed"),
        stats=stats,
        passed=passed,
        incomplete=skipped > 0 or bool(limit_runs),
        contaminated_runs=contaminated,
        limit_runs=limit_runs,
        not_run_for_limit=not_run_for_limit,
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
        cut = f", {len(v.limit_runs)} cut by the usage limit" if v.limit_runs else ""
        lines.append(
            f"Scenario: {v.scenario}  ({v.executed} run(s) executed{cut}, {v.skipped} skipped)"
        )
        for run in (r for r in records if r.scenario == v.scenario and r.executed):
            cost = "?" if run.cost_usd is None else f"${run.cost_usd:.2f}"
            detail = f"  ({run.reason})" if run.reason else ""
            label = "LIMIT" if run.status == "usage_limit" else run.status.upper()
            lines.append(
                f"  run-{run.run}: {label:<6} cost {cost}, "
                f"turns {'?' if run.turns is None else run.turns}, {run.duration_s:.0f}s{detail}"
            )
            lines += [f"    CONTAMINATED: {hit}" for hit in run.contamination]
            if run.blocked:
                lines.append(
                    f"    {len(run.blocked)} call(s) denied by the permission rules "
                    f"(no leak, not counted): {'; '.join(run.blocked)}"
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
            lines.append(
                "  not run (usage limit): nothing was measured"
                if v.limit_runs or v.not_run_for_limit
                else "  no run produced a scorable report"
            )
        if v.contaminated_runs:
            lines.append(
                f"  {len(v.contaminated_runs)} run(s) read outside the working copy or an "
                "answer-key location: not valid, never a pass"
            )
        if v.limit_runs or v.not_run_for_limit:
            hint = next((r.reset_hint for r in v.limit_runs if r.reset_hint), "")
            lines.append(
                "  USAGE LIMIT: the subscription limit stopped the plan"
                + (f", resets {hint}" if hint else "")
                + f". {len(v.limit_runs)} run(s) cut and {v.not_run_for_limit} not run are not "
                "counted. Wait for the reset, then rerun this scenario."
            )
        if v.skipped > v.not_run_for_limit:
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
    limit_stop = any(v.limit_runs or v.not_run_for_limit for v in verdicts)
    only_incomplete = all(v.passed or v.label == "INCOMPLETE" for v in verdicts)
    if overall:
        result = "PASS"
    elif limit_stop and only_incomplete:
        result = "INCOMPLETE (usage limit)"
    else:
        result = "FAIL"
    lines.append(f"RESULT: {result}")
    return "\n".join(lines)
